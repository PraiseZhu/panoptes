// 常驻 control：轮询 → 入账 → 串行执行 → 判定 → 发布 → 心跳 / 清理 / 日报兜底
import { statfsSync, openSync, writeSync, closeSync, readFileSync, rmSync, mkdirSync, writeFileSync, readdirSync, statSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import { createHash, randomUUID } from 'node:crypto'
import { loadRegistry, loadSecrets } from './config.mjs'
import { openDb, insertJob, nextQueuedJob, markRunning, finishJob, requeueJob, recoverRunning, setMeta, getMeta, getOutbox, aiUsageToday, addAiUsage } from './db.mjs'
import { listMergedByAuthor, getPull, getCommit, getBranchHead, listPullFiles } from './github.mjs'
import { classifyChange } from './classify.mjs'
import { executeOnSha, janitor } from './runner.mjs'
import { decideOutcome, failedIds, setupFailed } from './decide.mjs'
import { analyzePr } from './ai.mjs'
import { evaluateJev } from './jev.mjs'
import { toMatrixQuestions, toMatrixState, toTriage, toStepPlan, toCoverage } from './matrix.mjs'
import { generateTargetedTests, toGapDimensions, toAiTestSteps } from './aitests.mjs'
import { publishDefect, retryPendingIssues } from './publish.mjs'
import { sendDailyReport } from './daily.mjs'
import { killAllActive, run } from './exec.mjs'
import { ALL_DIRS, LOCK_PATH, CACHE_DIR, NPM_CACHE_DIR, UV_CACHE_DIR, EVIDENCE_DIR } from './paths.mjs'
import { log, redact, tail, sleep, toShanghaiDay } from './util.mjs'

const TICK_MS = 60_000
const POLL_EVERY_MS = 5 * 60_000
const HOUSEKEEP_EVERY_MS = 60 * 60_000
const MIN_FREE_GB = Number(process.env.ARGUS_MIN_FREE_GB ?? 12)
const CACHE_CAP_GB = Number(process.env.ARGUS_CACHE_CAP_GB ?? 12)
const CATCHUP_DAYS = 30
const SCAN_OVERLAP_MS = 2 * 3600_000
const EVIDENCE_DAYS = 30
const JEV_DAILY_CAP = Number(process.env.ARGUS_JEV_DAILY_CAP ?? 100)

const state = { stopping: false }
class StopRequested extends Error {}

export function diskFreeGb(path = CACHE_DIR) {
  const s = statfsSync(path)
  return Math.round((s.bavail * s.bsize) / 1024 ** 3)
}

// single instance lock

function acquireLock() {
  mkdirSync(join(LOCK_PATH, '..'), { recursive: true })
  if (existsSync(LOCK_PATH)) {
    const pid = Number(readFileSync(LOCK_PATH, 'utf8'))
    let alive = false
    try { process.kill(pid, 0); alive = pid !== process.pid } catch { alive = false }
    if (alive) throw new Error(`已有 control 在运行（pid ${pid}），本进程退出`)
    rmSync(LOCK_PATH, { force: true })
  }
  const fd = openSync(LOCK_PATH, 'wx')
  writeSync(fd, String(process.pid))
  closeSync(fd)
}

// poll

// 每个仓有自己的水位：上次完整扫完的时刻。下次从水位前 2 小时扫起，停机多久都能补回来
export async function pollOnce(db, reg) {
  let added = 0
  const errors = []
  for (const p of reg.projects) {
    const scanStarted = new Date().toISOString()
    const mark = getMeta(db, `scan_ok_at:${p.repo}`)
    const since = new Date(Math.max(Date.parse(reg.enabledSince), mark ? Date.parse(mark) - SCAN_OVERLAP_MS : 0)).toISOString()
    let prs
    try {
      prs = await listMergedByAuthor(p.repo, reg.owner.id, since)
    } catch (err) {
      errors.push(`${p.displayName ?? p.repo}：${err.message}`)
      continue
    }
    for (const pr of prs) {
      const enrolled = Boolean(p.profileData)
      const inserted = insertJob(db, {
        repo: p.repo, prNumber: pr.number, prNodeId: pr.node_id, prTitle: pr.title, prUrl: pr.html_url,
        authorLogin: pr.user.login, baseRef: pr.base.ref, mergedAt: pr.merged_at, mergeSha: pr.merge_commit_sha,
        status: enrolled ? 'queued' : 'done', outcome: enrolled ? null : 'not_enrolled',
        detail: enrolled ? null : { reasonZh: p.notEnrolledReasonZh ?? '这个项目还没接入自动测试' },
      })
      if (inserted) { added++; log('info', 'job enqueued', { repo: p.repo, pr: pr.number, enrolled }) }
    }
    setMeta(db, `scan_ok_at:${p.repo}`, scanStarted)
  }
  // 超过追测窗口仍在排队的任务给终态，不静默消失
  const cutoff = new Date(Date.now() - CATCHUP_DAYS * 86400_000).toISOString()
  for (const j of db.prepare("SELECT id FROM jobs WHERE status = 'queued' AND merged_at < ?").all(cutoff)) {
    finishJob(db, j.id, { outcome: 'expired', detail: { reasonZh: `排队超过 ${CATCHUP_DAYS} 天没测上` } })
  }
  if (errors.length) throw new Error(`部分项目读取失败：${errors.join('；')}`)
  return added
}

// pipeline

async function execOrStop(args) {
  const results = await executeOnSha(args)
  if (state.stopping) throw new StopRequested()
  return results
}

async function resolveBase(repo, job, pr) {
  const commit = await getCommit(repo, job.merge_sha)
  const parents = commit?.parents ?? []
  if (parents.length === 2) return { baseSha: parents[0].sha, baseKnown: true, how: 'merge' }
  const msg = String(commit?.commit?.message ?? '')
  if (parents.length === 1 && (msg.includes(`(#${job.pr_number})`) || pr.commits === 1)) return { baseSha: parents[0].sha, baseKnown: true, how: 'squash' }
  return { baseSha: null, baseKnown: false, how: 'unknown' }
}

// 试运行的检查（AI 补测、性能基准）确认的问题只记「拿不准」，不自动提 issue；和其它检查一起确认的缺陷，只按其它检查提
export function toTrialDecision(decision, trialSteps) {
  if (decision.outcome !== 'defect' || trialSteps.length === 0) return decision
  const aiIds = new Set(trialSteps.map((s) => s.id))
  const isAi = (k) => aiIds.has(k.slice(0, k.indexOf('::')))
  const keys = decision.failureKeys ?? []
  if (keys.length && keys.every(isAi)) {
    const hit = trialSteps.filter((s) => keys.some((k) => k.startsWith(`${s.id}::`)))
    const aiFound = hit.some((s) => s.aiGenerated)
    const names = hit.filter((s) => !s.aiGenerated).map((s) => s.nameZh)
    const what = [aiFound ? 'AI 补写的测试' : null, ...names].filter(Boolean).join('、')
    return { ...decision, outcome: 'needs_review', aiFound, trialFound: true, reasonZh: `${what}在合并后失败、合并前通过，最新主干仍失败。这类检查还在试运行，没有自动提 issue` }
  }
  const kept = keys.filter((k) => !isAi(k))
  const stepOf = (k) => k.slice(0, k.indexOf('::'))
  return { ...decision, failureKeys: kept, failedSteps: (decision.failedSteps ?? []).filter((id) => !aiIds.has(id)), failedTests: kept.filter((k) => !k.endsWith('::*')).map((k) => k.slice(k.indexOf('::') + 2)), aiAlsoFailed: keys.some(isAi) && kept.map(stepOf).length > 0 }
}

// 失败了、但判定确认只含合并前就有的老失败的步骤；拿不准或环境问题时一律不算
export function toKnownOnlySteps(results, decision) {
  if (Array.isArray(decision.knownOnlyStepIds)) return decision.knownOnlyStepIds
  if (!['passed_known_failures', 'defect', 'fixed_later'].includes(decision.outcome)) return []
  // passed_known_failures 的 failedSteps 列的正是「只有老失败」的步骤；另两种列的是这个 PR 带来的失败
  if (decision.outcome === 'passed_known_failures') return failedIds(results)
  const newFailed = new Set(decision.failedSteps ?? [])
  return failedIds(results).filter((id) => !newFailed.has(id))
}

// 分诊：Jev 判断这次改动涉及哪些测试方面；超额度、没 key 或调用失败时跳过，可选检查就全跑
export async function triagePr({ db, secrets, pr, files, profile, now = new Date() }) {
  const day = `jev:${toShanghaiDay(now)}`
  let jev
  if (aiUsageToday(db, day).calls >= JEV_DAILY_CAP) jev = { ok: false, reasonZh: `今天的 Jev 额度（${JEV_DAILY_CAP} 次）用完了` }
  else {
    jev = await evaluateJev({ state: toMatrixState({ pr, files, profile }), questions: toMatrixQuestions(profile), secrets })
    if (jev.ok) addAiUsage(db, day, jev.usage?.input_tokens ?? 0)
    else log('error', 'jev triage failed', { reason: jev.reasonZh })
  }
  return toTriage({ profile, files, jev })
}

export async function processJob({ db, reg, secrets, job }) {
  const project = reg.projects.find((p) => p.repo === job.repo)
  const baseProfile = { ...project.profileData, displayName: project.displayName }
  const pr = await getPull(job.repo, job.pr_number)
  if (!pr?.merged_at || pr.merge_commit_sha !== job.merge_sha || pr.user?.id !== reg.owner.id) {
    return { outcome: 'blocked', detail: { reasonZh: '复核 PR 时发现合并状态或作者对不上，没有测试' } }
  }
  const files = await listPullFiles(job.repo, job.pr_number)
  const { riskClass, reasonZh: classReason } = classifyChange(files, baseProfile)
  if (riskClass === 'A') return { outcome: 'docs_only', riskClass, detail: { classReason } }

  const triage = await triagePr({ db, secrets, pr, files, profile: baseProfile })
  const plan = toStepPlan({ profile: baseProfile, riskClass, triage })
  // AI 补测：分诊说涉及、但要跑的检查覆盖不到的方面；补测步骤放在最后
  const gaps = toGapDimensions({ profile: baseProfile, triage, stepIds: plan.stepIds })
  const gen = await generateTargetedTests({ db, secrets, riskClass, pr, files, profile: baseProfile, gaps })
  const aiSteps = toAiTestSteps(gen.tests, baseProfile)
  const profile = { ...baseProfile, steps: [...baseProfile.steps, ...aiSteps] }
  plan.stepIds.push(...aiSteps.map((s) => s.id))
  const { baseSha, baseKnown, how } = await resolveBase(job.repo, job, pr)
  const common = { repo: job.repo, jobId: job.id, profile }
  let first = await execOrStop({ ...common, label: 'm1', sha: job.merge_sha, stepIds: plan.stepIds })
  if (setupFailed(first)) {
    log('info', 'setup failed, retrying once', { job: job.id })
    first = await execOrStop({ ...common, label: 'm1r', sha: job.merge_sha, stepIds: plan.stepIds })
  }
  let rerun = null
  let base = null
  let latest = null
  let latestIsSame = true
  let latestHeadSha = null
  const failed = failedIds(first)
  if (!setupFailed(first) && failed.length > 0) {
    rerun = await execOrStop({ ...common, label: 'm2', sha: job.merge_sha, stepIds: failed })
    const still = failed.filter((id) => failedIds(rerun).includes(id))
    if (still.length > 0 && baseKnown) base = await execOrStop({ ...common, label: 'b', sha: baseSha, stepIds: still })
    const prelim = decideOutcome({ first, rerun, base, latest: null, baseKnown, latestIsSame: true })
    if (prelim.outcome === 'defect') {
      const head = await getBranchHead(job.repo, job.base_ref)
      latestHeadSha = head ?? null
      // 拿不到最新主干时不能当成「就是合并版本」，交给判定记待核实
      latestIsSame = Boolean(head) && head === job.merge_sha
      if (head && !latestIsSame) latest = await execOrStop({ ...common, label: 'l', sha: head, stepIds: prelim.failedSteps })
    }
  }
  const decision = toTrialDecision(decideOutcome({ first, rerun, base, latest, baseKnown, latestIsSame }), profile.steps.filter((s) => s.aiGenerated || s.trialOnly))
  const coverage = toCoverage({ profile, triage, results: first, knownOnlyStepIds: toKnownOnlySteps(first, decision) })
  // 先保存执行事实，再请求 AI 解读和外部发布；外部故障不能抹掉已经完成的测试。
  const evidenceFile = writeEvidence(job, { decision, first, rerun, base, latest, riskClass, profile, plan, triage, coverage, baseSha, latestHeadSha, latestIsSame })
  const ai = await analyzePr({ db, secrets, riskClass, pr, files, profile, results: first, outcome: decision.outcome })

  const detail = {
    classReason, baseHow: how, reasonZh: decision.reasonZh, failedSteps: decision.failedSteps ?? [],
    aiFound: Boolean(decision.aiFound), failedTests: decision.failedTests ?? [], knownFailureCount: decision.knownFailureCount ?? 0, knownTests: decision.knownTests ?? [],
    results: evidenceSafe(first.map((r) => ({ id: r.id, nameZh: r.nameZh, kind: r.kind, ok: r.ok, unavailable: Boolean(r.unavailable), exitCode: r.exitCode, timedOut: r.timedOut, durationMs: r.durationMs, failureDetails: r.failureDetails ?? null, failureParseStatus: r.failureParseStatus ?? null }))),
    unverifiedFailures: evidenceSafe(decision.unverifiedFailures ?? []),
    transientFailureCount: decision.transientFailureCount ?? 0,
    transientFailures: evidenceSafe(decision.transientFailures ?? []),
    testOutcomeStatus: first.filter((r) => r.kind !== 'setup').map((r) => ({ id: r.id, status: r.testOutcomeStatus ?? 'unavailable' })),
    ai,
    triage: { status: triage.status, reasonZh: triage.reasonZh, model: triage.model, rows: triage.rows },
    coverage, evidenceFile,
    skippedSteps: plan.skipped,
    unavailableSteps: first.filter((r) => r.unavailable).map((r) => ({ id: r.id, nameZh: r.nameZh, reasonZh: r.unavailableReasonZh })),
    aiTests: { status: gen.status, reasonZh: gen.reasonZh, gaps, tests: aiSteps.map((s) => { const r = first.find((x) => x.id === s.id); return { id: s.id, kind: s.aiKind, purposeZh: s.aiTest.purposeZh, result: !r ? 'not_run' : r.unavailable ? 'invalid' : r.ok ? 'pass' : 'fail', reasonZh: r?.unavailableReasonZh ?? null } }) },
    disturbedSteps: [first, rerun, base, latest].flat().filter((r) => r?.disturbedDesktop).map((r) => r.nameZh),
  }
  if (decision.outcome === 'defect') {
    const pub = await publishDefect({ db, reg, job, decision, results: first, ai, displayName: project.displayName ?? job.repo })
    Object.assign(detail, { issueUrl: pub.issueUrl ?? null, issueNumber: pub.issueNumber ?? null, issueAction: pub.action, issuePending: pub.action === 'pending', fingerprint: pub.fingerprint })
  }
  return { outcome: decision.outcome, riskClass, detail }
}

const evidenceSafe = (value) => JSON.parse(JSON.stringify(value, (_key, item) => typeof item === 'string' ? redact(item) : item))

function safeCommand(command) {
  let hideNext = false
  return (command ?? []).map((arg) => {
    if (hideNext) { hideNext = false; return '<redacted>' }
    hideNext = /^--?(?:token|api[-_]?key|password|secret|authorization)$/i.test(arg)
    return redact(arg).replace(/^(--?(?:token|api[-_]?key|password|secret|authorization)=).*/i, '$1<redacted>')
  })
}

// 只保留报告器的汇总和信号计数，成功时也留证；不把整份成功输出写入永久证据。
export function summarizeStep(result) {
  const lines = String(result.tail ?? '').replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '').split('\n').map((s) => s.trim())
  const summaryLines = lines.filter((s) => /^(?:#|ℹ)\s*(?:tests|suites|pass|fail|cancelled|skipped|todo|duration_ms)\b/i.test(s)
    || /^(?:Test Files|Tests|Suites)\s+\d/i.test(s)
    || /^=+.*\b\d+\s+(?:passed|failed|skipped|error)/i.test(s)).slice(-10)
  return {
    source: 'captured_output_tail',
    lines: summaryLines.map((s) => redact(s).slice(0, 200)),
    assertionSignals: {
      passed: lines.filter((s) => /^(?:✔|✓|ok\s+\d+\b)/.test(s)).length,
      failed: lines.filter((s) => /^(?:✖|×|not ok\s+\d+\b)/.test(s)).length,
    },
    mutationVerified: result.aiGenerated ? Boolean(result.ok && lines.some((s) => s.includes('植入错误后能发现问题'))) : null,
  }
}

export function writeEvidence(job, { decision, first, rerun, base, latest, riskClass, profile, plan, triage, coverage, baseSha = null, latestHeadSha = null, latestIsSame = false }) {
  mkdirSync(EVIDENCE_DIR, { recursive: true })
  const slim = (rs) => rs?.map((r) => ({
    id: r.id, kind: r.kind, ok: r.ok, exitCode: r.exitCode, timedOut: r.timedOut, durationMs: r.durationMs,
    requestedSha: r.requestedSha ?? null, checkedOutSha: r.checkedOutSha ?? null, runLabel: r.runLabel ?? null,
    commandTemplate: safeCommand(r.commandTemplate), covers: r.covers ?? [], aiGenerated: Boolean(r.aiGenerated),
    unavailable: Boolean(r.unavailable), unavailableReasonZh: r.unavailableReasonZh ?? null,
    failures: r.failures ?? [], failureDetails: r.failureDetails ?? null, failureParseStatus: r.failureParseStatus ?? null,
    testOutcomes: r.testOutcomes ?? [], testOutcomeStatus: r.testOutcomeStatus ?? 'unavailable', testOutcomeSource: r.testOutcomeSource ?? null,
    testOutcomeReason: r.testOutcomeReason ?? null,
    summary: summarizeStep(r), tail: r.ok ? '' : redact(tail(r.tail, 6000)),
  })) ?? null
  const phases = { first, rerun, base, latest }
  const artifact = evidenceSafe({
    schemaVersion: 2, jobId: job.id, repo: job.repo, pr: job.pr_number, mergeSha: job.merge_sha, riskClass, decision,
    profileHash: createHash('sha256').update(JSON.stringify(profile)).digest('hex'),
    profileSteps: profile.steps.map((s) => ({ id: s.id, kind: s.kind, covers: s.covers ?? [], optional: Boolean(s.optional), trialOnly: Boolean(s.trialOnly), aiGenerated: Boolean(s.aiGenerated), commandTemplate: safeCommand(s.cmd), testReport: s.testReport ?? null })),
    plannedStepIds: plan.stepIds, skippedSteps: plan.skipped, triage, coverage,
    versions: { mergeSha: job.merge_sha, baseSha, latestHeadSha, latestIsSame: latestHeadSha ? latestIsSame : null },
    executions: Object.fromEntries(Object.entries(phases).map(([phase, rs]) => [phase, rs ? { requestedSha: rs[0]?.requestedSha ?? null, checkedOutSha: rs[0]?.checkedOutSha ?? null, runLabel: rs[0]?.runLabel ?? null } : null])),
    runs: Object.fromEntries(Object.entries(phases).map(([phase, rs]) => [phase, slim(rs)])), at: new Date().toISOString(),
  })
  // 重试产生新文件，不覆盖前一次执行事实；账本 detail 指向这次的精确文件。
  let file = `job-${job.id}.json`
  if (existsSync(join(EVIDENCE_DIR, file))) file = `job-${job.id}-${randomUUID()}.json`
  writeFileSync(join(EVIDENCE_DIR, file), JSON.stringify(artifact, null, 2), { flag: 'wx', mode: 0o600 })
  return file
}

// housekeeping

async function dirSizeGb(path) {
  if (!existsSync(path)) return 0
  const res = await run('du', ['-sk', path], { timeoutMs: 120_000 })
  return Math.round((Number(res.tail.split(/\s+/)[0]) || 0) / 1024 / 1024)
}

async function housekeeping(db) {
  const j = await janitor()
  const cacheGb = await dirSizeGb(CACHE_DIR)
  if (cacheGb > CACHE_CAP_GB) {
    // 依赖缓存可以重建；代码镜像保留
    for (const dir of [NPM_CACHE_DIR, UV_CACHE_DIR, join(CACHE_DIR, 'pnpm-store')]) {
      rmSync(dir, { recursive: true, force: true })
      mkdirSync(dir, { recursive: true })
    }
    log('info', 'cache pruned', { cacheGb })
  }
  const linked = new Set(db.prepare('SELECT first_job_id FROM defects').all().map((r) => r.first_job_id))
  if (existsSync(EVIDENCE_DIR)) {
    for (const f of readdirSync(EVIDENCE_DIR)) {
      const p = join(EVIDENCE_DIR, f)
      const jobId = Number(/^job-(\d+)(?:-|\.json$)/.exec(f)?.[1])
      if (!linked.has(jobId) && Date.now() - statSync(p).mtimeMs > EVIDENCE_DAYS * 86400_000) rmSync(p, { force: true })
    }
  }
  log('info', 'housekeeping', { runsRemoved: j.removed, cacheGb })
}

// daily report fallback：launchd 的 09:00 任务没发出去时，control 在 09:05 之后补发

function dailyDue(now = new Date()) {
  const hour = Number(new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Shanghai', hour: '2-digit', hour12: false }).format(now))
  const minute = Number(new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Shanghai', minute: '2-digit' }).format(now))
  return hour > 9 || (hour === 9 && minute >= 5)
}

// 手动验收用：取下一个排队任务跑完即退出（与 serve 共用锁，不能同时运行）
export async function processNextOnce() {
  for (const d of ALL_DIRS) mkdirSync(d, { recursive: true })
  acquireLock()
  const onSig = () => { state.stopping = true; killAllActive() }
  process.once('SIGTERM', onSig)
  process.once('SIGINT', onSig)
  try {
    const reg = loadRegistry()
    const db = openDb()
    recoverRunning(db)
    await janitor()
    const job = nextQueuedJob(db)
    if (!job) return null
    markRunning(db, job.id)
    try {
      const res = await processJob({ db, reg, secrets: loadSecrets(), job })
      finishJob(db, job.id, res)
      return { job: job.id, repo: job.repo, pr: job.pr_number, ...res }
    } catch (err) {
      requeueJob(db, job.id)
      throw err
    }
  } finally {
    rmSync(LOCK_PATH, { force: true })
  }
}

export async function serve() {
  for (const d of ALL_DIRS) mkdirSync(d, { recursive: true })
  acquireLock()
  const reg = loadRegistry()
  const db = openDb()
  const recovered = recoverRunning(db)
  log('info', 'control started', { pid: process.pid, recovered, projects: reg.projects.length })

  const stop = (sig) => {
    if (state.stopping) return
    state.stopping = true
    log('info', 'stopping', { sig })
    killAllActive()
  }
  process.on('SIGTERM', () => stop('SIGTERM'))
  process.on('SIGINT', () => stop('SIGINT'))
  // 心跳独立于主循环：长测试跑 40 分钟时也要保持新鲜，否则日报会误报「服务停了」
  const beat = setInterval(() => {
    try { setMeta(db, 'heartbeat_at', new Date().toISOString()) } catch { /* 下一拍再写 */ }
  }, TICK_MS)
  beat.unref()

  await housekeeping(db).catch((err) => log('error', 'housekeeping failed', { err: err.message }))
  let lastPoll = 0
  let lastHousekeep = Date.now()
  while (!state.stopping) {
    const secrets = loadSecrets()
    setMeta(db, 'heartbeat_at', new Date().toISOString())
    const free = diskFreeGb()
    setMeta(db, 'disk_free_gb', free)

    if (Date.now() - lastPoll >= POLL_EVERY_MS) {
      lastPoll = Date.now()
      try {
        const added = await pollOnce(db, reg)
        setMeta(db, 'last_poll_ok_at', new Date().toISOString())
        setMeta(db, 'last_poll_error', '')
        if (added) log('info', 'poll', { added })
      } catch (err) {
        setMeta(db, 'last_poll_error', redact(err.message).slice(0, 300))
        log('error', 'poll failed', { err: err.message })
      }
      await retryPendingIssues(db, reg).catch((err) => log('error', 'retry issues failed', { err: err.message }))
    }

    if (dailyDue() && !['sent', 'given_up'].includes(getOutbox(db, `daily:${toShanghaiDay()}`)?.status)) {
      await sendDailyReport({ db, reg, secrets }).catch((err) => log('error', 'daily fallback failed', { err: err.message }))
    }

    if (Date.now() - lastHousekeep >= HOUSEKEEP_EVERY_MS) {
      lastHousekeep = Date.now()
      await housekeeping(db).catch((err) => log('error', 'housekeeping failed', { err: err.message }))
    }

    const job = free >= MIN_FREE_GB ? nextQueuedJob(db) : null
    if (free < MIN_FREE_GB) setMeta(db, 'paused_reason', `磁盘只剩 ${free} GB`)
    else setMeta(db, 'paused_reason', '')
    if (!job) { await sleep(TICK_MS); continue }

    markRunning(db, job.id)
    log('info', 'job start', { job: job.id, repo: job.repo, pr: job.pr_number })
    try {
      const res = await processJob({ db, reg, secrets, job })
      finishJob(db, job.id, res)
      log('info', 'job done', { job: job.id, outcome: res.outcome })
    } catch (err) {
      if (err instanceof StopRequested) { requeueJob(db, job.id, { refundAttempt: true }); break }
      log('error', 'job crashed', { job: job.id, err: err.message })
      const attempts = db.prepare('SELECT attempts FROM jobs WHERE id = ?').get(job.id).attempts
      // 多半是网络抖动：放回队列，等 5 分钟再试，最多 5 次
      if (attempts >= 5) finishJob(db, job.id, { outcome: 'infra_failed', detail: { reasonZh: '测试流程连续 5 次出错（多半是网络），已停止重试', error: redact(err.message).slice(0, 300) } })
      else { requeueJob(db, job.id); await sleep(5 * TICK_MS) }
    }
    await janitor().catch(() => {})
  }
  clearInterval(beat)
  rmSync(LOCK_PATH, { force: true })
  log('info', 'control stopped')
  db.close()
}
