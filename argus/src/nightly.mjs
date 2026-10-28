// 夜间 QA（Linux / Mivo QA）：发现前一天（上海时区）的 PR → 在隔离沙箱里跑项目检查 → 失败三重对照 →
// ming-qa 代码审查 → 写 reports/YYYY-MM-DD.json。默认绝不写 GitHub；提 issue 只在 --create-issues 且
// ARGUS_ALLOW_ISSUES=1 时才可能发生。
import { yieldToPriority } from './priority.mjs'
import { memAvailableGb, machineLoad, isIdle } from './memory.mjs'
import { mkdirSync, writeFileSync, renameSync, existsSync, readFileSync, statfsSync, readdirSync } from 'node:fs'
import { join, resolve, dirname, basename, posix } from 'node:path'
import { createHash, randomUUID } from 'node:crypto'
import { hostname } from 'node:os'
import { execFileSync } from 'node:child_process'
import { loadRegistry, loadSecrets } from './config.mjs'
import { openDb } from './db.mjs'
import { listNightlyPrs, getPull, getCommit, getBranchHead, listPullFiles, addFullText, getHandoff, shanghaiDayWindow, findIssuesByMarkerLabel, createIssue, commentIssue, reopenIssue, findCommentByMarker, listPullReviewComments, listPullCommits, listTeamMembers } from './github.mjs'
import { classifyChange, highRiskFiles, withoutHandoff, isTestPath } from './classify.mjs'
import { parseHandoff, splitHandoffCases, toHandoffReport, settleHandoff } from './handoff.mjs'
import { executeOnSha, ensureMirror, mergeBase, sandboxMode, janitor, mirrorPath, currentUmask, INHERITED_UMASK, PINNED_UMASK, fmtUmask } from './runner.mjs'
import { decideOutcome, failedIds, setupFailed, failureKeys, compareKey } from './decide.mjs'
import { analyzePr } from './ai.mjs'
import { evaluateJev } from './jev.mjs'
import { toMatrixQuestions, toMatrixState, toTriage, toStepPlan, toCoverage, toApplicability, stepExercises, stepRunsChangedFile } from './matrix.mjs'
import { codeReview, toBox, BOXES, LLM_MODEL, mingQaVersion, mingQaAvailable, reviewCacheKey, lookupReviewVerdict, chat } from './mingqa.mjs'
import { loadSeverityStandard, severityStandardRef, SEVERITY_LEVEL_NAMES } from './severity.mjs'
import { toPending, pendingSummary, PENDING_LABEL_ZH } from './pending.mjs'
import { threadsFromReviewComments, collectPriorRulings, withCodeState, headAtFrom, matchPriorRuling, rulingPromptSection } from './rulings.mjs'
import { repoReaderFor, verifyReviewFinding, verifyCacheKey, lookupVerification, storeVerification, VERIFY_MAX_PER_PR, VERIFY_STATUS_ZH } from './review-verify.mjs'
import { ALL_DIRS, CACHE_DIR, EVIDENCE_DIR, REPORTS_DIR, CODE_DIR, LOGS_DIR } from './paths.mjs'
import { log, redact, sleep, tail, toShanghaiDay, toShanghaiIso, waitWithinBudget } from './util.mjs'
import { setBudget, overBudget } from './exec.mjs'
import { coverageGate, parseAddedLines, mergeCoverageFiles, COVERAGE_THRESHOLDS, statedCoverageTarget, evaluateStatedTarget } from './coverage.mjs'
import { recentGiveUps } from './catchup.mjs'
import { prTestDimensions } from './pr-tests.mjs'
import { generateTargetedTests, toAiTestSteps, toAiGaps, toUncoveredTargets, toAiTestResult, aiTestCaps, aiTestTier, degradeForQuota, toSourceHead } from './aitests.mjs'
import { createQuota, memoryQuota, resolvePool, CAPPED_ZH } from './quota.mjs'

// 代码版本在进程启动（模块加载）时记下：运行中 checkout 前进不会让报告冒认新提交（#1086 曾在 dfbc980 之前启动，却记成 153e03e）
const LOADED_ARGUS_COMMIT = (() => { try { return execFileSync('git', ['-C', CODE_DIR, 'rev-parse', '--short', 'HEAD'], { encoding: 'utf8' }).trim() } catch { return null } })()

const SCHEMA_VERSION = '1.0'
const SEV_RANK = { P0: 0, P1: 1, P2: 2, P3: 3 }
const maxSev = (list) => list.reduce((m, s) => (SEV_RANK[s] < SEV_RANK[m] ? s : m), 'P3')
const sha256 = (s) => createHash('sha256').update(String(s)).digest('hex')

export function previousShanghaiDay(now = new Date()) {
  return toShanghaiDay(new Date(Date.parse(`${toShanghaiDay(now)}T00:00:00+08:00`) - 12 * 3600_000))
}

// ── 指纹 ─────────────────────────────────────────────
// fingerprint：与 PR 号、SHA、时间无关（同一问题跨 PR / 跨夜可去重）；pr_fingerprint：同一 PR 内去重。
const normalize = (s) => String(s ?? '').replace(/\x1b\[[0-9;]*m/g, '').replace(/\b[0-9a-f]{7,40}\b/g, '<sha>').replace(/\d+(\.\d+)?\s*(ms|s|m)\b/g, '<dur>').replace(/:\d+:\d+/g, ':<l>:<c>').replace(/\/tmp\/[^\s'"]+|\/workspace\/[^\s'"]+/g, '<path>').replace(/\s+/g, ' ').trim().toLowerCase()
export function fingerprintOf(repo, parts) { return `mq1-${sha256([repo, ...parts.map(normalize)].join('|')).slice(0, 20)}` }
const prFingerprint = (fp, n) => `${fp}-pr${n}`
// 代码审查标题归一化后的短哈希（12 位）：同一文件 / 符号 / 类别下的不同问题不再撞同一个指纹
export const normalizeTitle = (t) => normalize(t).replace(/[`'"“”‘’.,;:!?()[\]{}<>]/g, ' ').replace(/\b\d+\b/g, '<n>').replace(/\s+/g, ' ').trim()
export const titleHash = (t) => sha256(normalizeTitle(t)).slice(0, 12)
// 标题哈希不经 normalize（它会把 12 位十六进制当成 sha 抹掉），直接拼进指纹输入
export const codeReviewFingerprint = (repo, f) => `mq1-${sha256([repo, ...['code-review', f.file, f.symbol || '', f.category].map(normalize), `title=${titleHash(f.title)}`].join('|')).slice(0, 20)}`

function wholeStepSignature(r) {
  const lines = String(r?.tail ?? '').split('\n').filter((l) => /error|fail|✗|×|assert/i.test(l)).slice(-5)
  return lines.map(normalize).join('|') || `exit:${r?.exitCode}`
}

// ── 单个失败键的归因（逐项，不依赖整体判定）────────────────
function classifyKey(key, { first, rerun, base, latest, latestIsSame, skipLatest = false }) {
  if (!rerun) return { status: 'unverified', why: 'no rerun available' }
  const vsRerun = compareKey(key, rerun, first)
  if (vsRerun === 'absent') return { status: 'flaky', why: 'unstable: failed once, passed on a clean rerun of the same commit; not attributed to this PR' }
  if (vsRerun === 'unknown') return { status: 'unverified', why: 'rerun result incomplete or failure type not comparable' }
  if (!base) return { status: 'unverified', why: 'reproduced, but the base revision could not be run for comparison' }
  const vsBase = compareKey(key, base, first)
  if (vsBase === 'same') return { status: 'pre-existing', why: 'also fails on the base revision' }
  if (vsBase === 'unknown') return { status: 'unverified', why: 'reproduced; base comparison incomplete' }
  if (skipLatest) return { status: 'introduced', why: 'fails reproducibly on this PR and passes on the base revision (no latest-main recheck: a problem in the PR\'s own commits cannot be fixed by later commits)' }
  if (!latestIsSame && latest) {
    const vsLatest = compareKey(key, latest, first)
    if (vsLatest === 'absent') return { status: 'fixed-later', why: 'introduced by this PR but already passing on latest main' }
    if (vsLatest === 'unknown') return { status: 'introduced', why: 'fails here and passes on base; latest-main comparison incomplete' }
  }
  return { status: 'introduced', why: 'fails reproducibly on this PR and passes on the base revision' }
}

// 检查失败的定级（统一分级，Praise 2026-10-23；文档见 README-linux.md）：
//  introduced：产品类检查（构建/类型/单测/接口/E2E）→ P1（已合并、未合并一样）；质量守卫类 → P2；试运行检查（trialOnly）最高 P2。
//    P0 不再「已合并就自动 P0」：只在符合统一分级 P0 定义时由人工或裁决确认。已合并且最新主干仍失败的记 p0_candidate 供人看。
//  unverified / setup-failed / timeout / flaky 不是作者的问题：不定级（severity null），进待核实（QA 自查）。
//  pre-existing / fixed-later → P3，只提醒。
export { classifyKey }

// ── 盒子自检 / 自举（run-nightly.sh 写 PANOPTES_BOOTSTRAP_STATUS）→ system_health ─────────────
export function readBootstrapStatus(path = process.env.PANOPTES_BOOTSTRAP_STATUS) {
  if (!path) return null
  try { return JSON.parse(readFileSync(path, 'utf8')) } catch { return null }
}
export function recordBootstrap(health, st) {
  if (!st) return
  const before = st.missing_before ?? [], after = st.missing_after ?? []
  const item = (m) => m.replace(/^missing: /, '').split(' — ')[0]
  const left = new Set(after.map(item))
  // self_repaired：本次运行开始时缺、自举后已补齐的项（只记在报告里，不提 issue）
  const repaired = st.ran ? [...new Set(before.map(item))].filter((x) => !left.has(x)) : []
  health.bootstrap = { ran: Boolean(st.ran), missing_before: before, missing_after: after, self_repaired: repaired, log: st.log || null, checked_at: st.checked_at ?? null }
  if (st.ran && !after.length) health.notes.push(`box self-check found missing dependencies and bootstrap-box.sh installed them: ${before.map((m) => m.replace(/^missing: /, '').split(' — ')[0]).join(', ')}`)
  if (after.length) {
    health.qa_tool_failures.push({ stage: 'bootstrap', message: st.ran ? `bootstrap-box.sh could not install ${after.length} dependenc${after.length === 1 ? 'y' : 'ies'}` : `${after.length} dependenc${after.length === 1 ? 'y' : 'ies'} missing (auto-install disabled)`, detail: st.log || null })
    for (const m of after) health.env_problems.push({ message: m.replace(/^missing: /, '') })
  }
}

// 收尾复查：开跑时的自检快照可能早于后来的修复（如 apt 锁释放后手动 / 另一进程补装）。
// 报告写出前再跑一次 --check；开跑时缺、现在已齐的项记入 bootstrap.resolved_later，并从 env_problems / qa_tool_failures 撤掉。
export function bootstrapCheckNow() {
  try { execFileSync(join(CODE_DIR, '..', 'scripts', 'bootstrap-box.sh'), ['--check'], { encoding: 'utf8', timeout: 60_000, stdio: ['ignore', 'pipe', 'ignore'] }); return [] }
  catch (err) { return typeof err.stdout === 'string' ? err.stdout.split('\n').filter((l) => l.trim()) : null }
}
export function recheckBootstrap(health, check = bootstrapCheckNow, now = () => new Date()) {
  const b = health.bootstrap
  if (!b?.missing_after?.length) return
  const missing = check()
  if (!Array.isArray(missing)) return // 复查本身失败：保留开跑时的结论
  const item = (m) => m.replace(/^missing: /, '').split(' — ')[0]
  const still = new Set(missing.map(item))
  const resolved = [...new Set(b.missing_after.map(item))].filter((x) => !still.has(x))
  b.recheck = { checked_at: toShanghaiIso(now()), missing }
  b.resolved_later = resolved
  if (!resolved.length) return
  const gone = new Set(b.missing_after.filter((m) => !still.has(item(m))).map((m) => m.replace(/^missing: /, '')))
  health.env_problems = health.env_problems.filter((e) => !gone.has(e.message) && !(resolved.some((r) => r.includes('chromium')) && /chromium missing: browser E2E/.test(e.message)))
  health.qa_tool_failures = health.qa_tool_failures.filter((f) => f.stage !== 'bootstrap')
  if (missing.length) health.qa_tool_failures.push({ stage: 'bootstrap', message: `${missing.length} dependenc${missing.length === 1 ? 'y' : 'ies'} still missing at report time`, detail: b.log || null })
  for (const m of missing) if (!health.env_problems.some((e) => e.message === m.replace(/^missing: /, ''))) health.env_problems.push({ message: m.replace(/^missing: /, '') })
  health.notes.push(`bootstrap self-check at ${b.checked_at} still reported missing ${resolved.join(', ')}; re-check at report time found them present (installed during the run, not by this run's bootstrap)`)
  if (resolved.some((r) => r.includes('chromium'))) { const env = health.environment; if (env) { try { env.chromium = execFileSync(env.chromium_path, ['--version'], { encoding: 'utf8', timeout: 15_000 }).trim().split('\n')[0] } catch { /* keep */ } } }
}

// ── 统一分级钉住值（scripts/check-severity-pin.mjs 每天和 Review-PR main 比一次）→ system_health.severity_pin ─────────
export const SEVERITY_PIN_STATE = () => process.env.PANOPTES_SEVERITY_PIN_STATE ?? join(LOGS_DIR, 'severity-pin.json')
// 「读不到上游」的 note 同一天（上海日期）只写一次：记在状态文件旁边的 <状态文件>.noted.json（nightly 自己写，不碰比对脚本的状态文件）
export function recordSeverityPin(health, file = SEVERITY_PIN_STATE(), { now = new Date() } = {}) {
  let st = null
  try { st = JSON.parse(readFileSync(file, 'utf8')) } catch { return }
  if (!st || typeof st !== 'object') return
  health.severity_pin = { status: st.status ?? null, checked_at: st.at ?? null, pinned_sha256: st.pinned_sha256 ?? null, upstream_sha256: st.upstream_sha256 ?? null, upstream_ref: st.upstream_ref ?? null,
    ...(st.message ? { message: redact(String(st.message)).slice(0, 300) } : {}) }
  if (st.status === 'drift') health.notes.push(`统一分级上游已变：${st.source_repo} ${st.path}@${st.upstream_ref} 和钉住的 ${String(st.pinned_sha256).slice(0, 12)} 不一致；本次仍按钉住的版本分级，等人核对后更新 argus/config/severity.lock.json`)
  if (st.status === 'local-mismatch') health.notes.push(`统一分级副本和钉住的 sha256 对不上：${st.message ?? ''}`)
  if (st.status === 'error') {
    const day = toShanghaiDay(now)
    const markFile = `${file}.noted.json`
    let mark = null
    try { mark = JSON.parse(readFileSync(markFile, 'utf8')) } catch { /* 没写过 */ }
    if (mark?.day !== day) {
      health.notes.push(`统一分级每日比对没跑成：读不到 ${st.source_repo ?? '?'} ${st.path ?? '?'}@${st.upstream_ref ?? 'main'}（${redact(String(st.message ?? 'unknown error')).slice(0, 200)}）；本次仍按钉住的版本分级，比对会在下次运行重试`)
      try { writeFileSync(`${markFile}.tmp-${process.pid}`, JSON.stringify({ day, status: 'error', at: now.toISOString() }) + '\n'); renameSync(`${markFile}.tmp-${process.pid}`, markFile) } catch { /* 写不了标记就下次再提醒一次 */ }
    }
  }
}

// ── 内存护栏：可用内存不够时，覆盖率轮次不与首轮并行，改为排在首轮之后 ─────────────────
export const parallelMinFreeGb = () => Number(process.env.ARGUS_PARALLEL_MIN_FREE_GB ?? 8)
export { memAvailableGb }
export function parallelDecision({ freeGb = memAvailableGb(), minGb = parallelMinFreeGb(), maxParallel = Number(process.env.ARGUS_MAX_PARALLEL ?? 2) } = {}) {
  if (maxParallel <= 1) return { parallel: false, reason: 'ARGUS_MAX_PARALLEL=1' }
  if (freeGb == null) return { parallel: false, reason: 'MemAvailable unknown' }
  if (freeGb < minGb) return { parallel: false, reason: `MemAvailable ${freeGb} GB < ${minGb} GB` }
  return { parallel: true, reason: `MemAvailable ${freeGb} GB >= ${minGb} GB` }
}

// 不稳定（flaky）失败与合并前版本（base）对比：base 上单跑一次的结果只作基线记录，不改变 flaky 的状态和 P2 严重度。
// 没有对比结果时，文字必须写明「未与合并前版本对比」。
export const NOT_COMPARED_ZH = '未与合并前版本对比'
export function flakyBaseComparison({ baseSha, baseRun }) {
  const compared = ['pass', 'fail', 'not-failing'].includes(baseRun)
  if (!compared) {
    const reason = !baseSha ? 'base revision unknown' : baseRun === 'not-run' ? 'base run did not happen' : `base run ${baseRun}`
    return { compared: false, base_sha: baseSha ?? null, base_run: baseRun, result: 'not-compared', note: `${NOT_COMPARED_ZH} (not compared with the base revision: ${reason})`, note_zh: `${NOT_COMPARED_ZH}（${reason}）` }
  }
  if (baseRun === 'fail') return { compared: true, base_sha: baseSha, base_run: baseRun, result: 'also-fails-on-base', note: 'also failed on the base (pre-merge) revision in one run: instability predates this PR', note_zh: '合并前版本上同样失败：不稳定在这个 PR 之前就有' }
  return { compared: true, base_sha: baseSha, base_run: baseRun, result: 'passes-on-base', note: 'passed on the base (pre-merge) revision in one run; a single run cannot rule out the same instability on base', note_zh: '合并前版本上跑一次通过；单次运行不能排除合并前也偶发失败' }
}
// 试运行检查（bench）里单个用例 test_timeout，且在空闲机器上重跑通过 → 才算环境负载、降成 P3。
// 整步超时、introduced、重跑没有严格 pass（含 not-failing）、等满 10 分钟机器仍没空下来：保持原级别
export function isTrialTimeout(step, r, test, { status, rerun, idle } = {}) {
  if (!step?.trialOnly || r?.timedOut || status === 'introduced' || !test || test === '*') return false
  if (r?.failureDetails?.find((f) => f.name === test)?.type !== 'test_timeout') return false
  return idle === true && rerun === 'pass'
}
// 重跑前等机器空闲（最多 maxWaitMs、不超时间预算）；返回最后一次负载快照和等了多久。idleHooks 供测试替换
export const idleHooks = { sample: machineLoad, wait: sleep }
export const needsIdleBeforeBase = (stepIds, profile) => stepIds.some((id) => profile?.steps?.find((x) => x.id === id)?.trialOnly)
export async function waitForIdle({ maxWaitMs = 10 * 60_000, everyMs = 15_000, sample = idleHooks.sample, wait = idleHooks.wait } = {}) {
  const t0 = Date.now()
  let m = sample()
  while (!isIdle(m) && Date.now() - t0 < maxWaitMs && !overBudget()) { await wait(everyMs); m = sample() }
  return { ...m, idle: isIdle(m), waited_ms: Date.now() - t0 }
}
export const QA_SELF_CHECK_STATUSES = ['unverified', 'setup-failed', 'timeout', 'flaky']
// introduced 已经排除了「最新主干上已通过」（那是 fixed-later），所以已合并 + 产品类 + introduced = 主干可能还坏着
export const p0Candidate = ({ status, step, merged }) => Boolean(merged) && status === 'introduced' && step.kind !== 'quality' && !step.trialOnly
// p0_note 按实际有没有在最新主干上复查写：latestSha = 复查时取到的主干 head（取不到为 null）；latestIsSame = 被测提交就是主干 head；
// latestRun = 这一步在最新主干上的结果（compareOn(latest)：fail / not-failing / pass / timeout / unavailable / not-run）
export function p0NoteFor({ step, latestSha, latestIsSame, latestRun }) {
  const where = step.noLatestRecheck ? 'not re-checked on latest main by design (noLatestRecheck: e.g. a leaked secret stays in history)'
    : !latestSha ? 'latest main not re-checked (latest main head unknown)'
      : latestIsSame ? 'the tested commit is the latest main, so latest main fails too'
        : latestRun === 'fail' ? 'still failing on latest main (re-checked)'
          : latestRun && latestRun !== 'not-run' ? `latest-main re-check incomplete (${latestRun})`
            : 'latest main not re-checked'
  return `merged PR, product check introduced by this PR; ${where}. P0 only if it meets the severity.md P0 definition, confirmed by a human or a ruling`
}
export function severityFor({ status, step }) {
  if (QA_SELF_CHECK_STATUSES.includes(status)) return null
  let sev = status === 'introduced' ? (step.kind === 'quality' ? 'P2' : 'P1') : 'P3'
  if (step.trialOnly && SEV_RANK[sev] < SEV_RANK.P2) sev = 'P2'
  return sev
}

function evidenceTail(r, name) {
  const lines = String(r?.tail ?? '').replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '').split('\n')
  let idx = name ? lines.findIndex((l) => l.includes(String(name).split(' > ').pop())) : -1
  const slice = idx >= 0 ? lines.slice(Math.max(0, idx - 3), idx + 25) : lines.slice(-30)
  return redact(slice.join('\n')).slice(-3000)
}

// ── 单个 PR ───────────────────────────────────────────
export async function processPr({ repo, project, number, discovery, secrets, db, health, evidenceRoot, opts, quota = memoryQuota() }) {
  const startedAt = Date.now()
  const baseProfile = { ...project.profileData, displayName: project.displayName }
  const pr = await getPull(repo, number)
  // PR 交接文件 docs/pr/** 不参与分级、代码审查、覆盖率和被测文件列表（只算进 changed_files 总数）
  const allFiles = await addFullText(repo, pr, await listPullFiles(repo, number))
  const files = withoutHandoff(allFiles)
  const merged = Boolean(pr.merged_at)
  const out = {
    number, title: pr.title, author: pr.user?.login ?? null, url: pr.html_url,
    state: merged ? 'merged' : pr.state, draft: Boolean(pr.draft), discovery_reason: discovery,
    created_at: pr.created_at, updated_at: pr.updated_at, merged_at: pr.merged_at,
    base_ref: pr.base?.ref, head_sha: pr.head?.sha, merge_sha: pr.merge_commit_sha ?? null, tested_sha: null, base_sha: null,
    changed_files: allFiles.length, additions: pr.additions, deletions: pr.deletions,
    risk_class: null, severity: 'P3', verdict: null, outcome: null, outcome_reason: null,
    checks: [], failed_checks: [], code_review: null, findings: [], pending_verification: [], ai_summary: null, coverage: null, coverage_gate: null,
    evidence_dir: null, duration_s: null,
    // tested_at：这个 PR 得出结论的时间（上海时区 ISO 8601，带 +08:00）；test_started_at：开始处理的时间
    test_started_at: toShanghaiIso(new Date(startedAt)), tested_at: null,
    // gate_wait_s：开测前在 run-nightly.sh 内存门等了多少秒（10-27：SLA 把排队/等内存和流水线分开算）
    gate_wait_s: Number(process.env.PANOPTES_GATE_WAIT_S) || 0, reviewed_sha: null, review_base_sha: null,
    // 因每日模型额度用尽而没做的事（Jev 分诊 / AI 补测 / 代码审查 / 摘要），每项 { kind, pool, cap, used, dimensions, message }
    quota_capped: [],
  }
  // 合并即测（on-merge）已经在同一个 merge commit 上测过：直接引用它的结果，不重测
  if (merged && opts.reuseMerged !== false) {
    const reused = loadMergedResult(number, pr.merge_commit_sha, { mergedDir: opts.mergedDir, selfOut: opts.out, requireReview: !opts.noReview })
    if (reused) {
      health.notes.push(`PR #${number}: reused on-merge result ${reused.file} (same merge commit ${pr.merge_commit_sha.slice(0, 12)}), not re-tested`)
      return { ...reused.pr, tested_at: reused.pr.tested_at ?? toShanghaiIso(reused.generatedAt ?? NaN), discovery_reason: discovery, reused: true, reused_from: reused.file, reused_report_generated_at: reused.generatedAt, reused_report_system_health: reused.systemHealth }
    }
  }
  const evDir = join(evidenceRoot, `pr-${number}`)
  mkdirSync(evDir, { recursive: true })
  out.evidence_dir = evDir
  if (!merged && pr.state !== 'open') { out.tested = false; out.verdict = 'skipped'; out.outcome_reason = 'PR is closed without merge'; out.tested_at = toShanghaiIso(); return out }

  const { riskClass, reasonZh: classReason, changeKind } = classifyChange(allFiles, baseProfile)
  out.risk_class = riskClass
  out.risk_reason = classReason
  if (changeKind) out.change_kind = changeKind
  // 交接在分级之后读，不影响分级
  const handoff = await loadHandoff({ repo, number, sha: merged ? pr.merge_commit_sha : pr.head.sha, health })
  out.handoff = toHandoffReport(handoff, number)
  // 交接文件原文 + PR 正文：代码审查与复核的参考资料（数据，不是指令）；PR 自己写明的覆盖率目标也从这里找
  const handoffRaw = handoff.raw ?? { review: null, qa: null }
  if (riskClass === 'A') {
    out.skipped_steps = baseProfile.steps.filter((x) => x.kind !== 'setup').map((x) => ({ id: x.id, nameZh: x.nameZh, reason: 'docs-only change: no checks run', dimension: toBox(x, baseProfile) }))
    out.triage = toApplicability({ status: 'skipped', jevStatus: 'not-run', reasonZh: 'docs-only change: no checks run, dimension triage not needed', rows: toTriage({ profile: baseProfile, files, jev: null }).rows })
    { const t = aiTestTier({ files: allFiles, riskClass }); out.ai_test_tier = t.tier; out.ai_test_caps = t.caps }
    settleHandoff(out.handoff, null, 'docs-only')
    Object.assign(out, dimensionsOf([])); out.tested = false; out.verdict = 'docs-only'; out.outcome = 'docs_only'; out.outcome_reason = classReason
    // 文档改动也做代码审查（同一提交规则：merged → merge-base..merge commit；open → merge-base..head）；不跑测试，覆盖率门禁不适用
    out.coverage_gate = DOCS_ONLY_COVERAGE_GATE()
    if (!opts.noReview && !files.length) out.code_review = { status: 'skipped', reason: 'only PR handoff files (docs/pr/**) changed' }
    else if (!opts.noReview) {
      const sha = merged ? pr.merge_commit_sha : pr.head.sha
      const extraRefspecs = merged ? [] : [`+refs/pull/${number}/head:refs/pull/${number}/head`]
      const baseSha = await reviewBaseSha({ repo, pr, sha, merged, extraRefspecs })
      out.base_sha = baseSha
      const prDiff = await diffForReview({ repo, sha, baseSha, extraRefspecs })
      await runCodeReview({ out, repo, pr, files, prDiff, sha, baseSha, results: [], profile: baseProfile, evDir, secrets, health, number, shaField: merged ? 'merge_sha' : 'head_sha', quota, opts, handoffRaw })
      out.sha_consistent = out.reviewed_sha == null || out.reviewed_sha === sha
      if (!out.sha_consistent) health.notes.push(`PR #${number}: docs-only review looked at ${String(out.reviewed_sha).slice(0, 12)} instead of ${String(sha).slice(0, 12)} (${out.review_diff_source})`)
    }
    out.severity = out.findings.length ? maxSev(out.findings.map((f) => f.severity)) : 'P3'
    if (out.pending_verification.length) out.pending_summary = pendingSummary(out.pending_verification)
    out.duration_s = Math.round((Date.now() - startedAt) / 1000); out.tested_at = toShanghaiIso()
    return out
  }

  // 被测版本：已合并 → merge commit；开放 → PR head（从 refs/pull/N/head 取，支持 fork）
  const sha = merged ? pr.merge_commit_sha : pr.head.sha
  const extraRefspecs = merged ? [] : [`+refs/pull/${number}/head:refs/pull/${number}/head`]
  out.tested_sha = sha

  // 分诊（Jev，可选）；没有 key 时所有可选检查都跑
  const jevConfigured = Boolean(secrets.ARGUS_TYPESAFE_API_KEY)
  let jev = { ok: false, reasonZh: '没有配置 Jev 的 key（ARGUS_TYPESAFE_API_KEY），可选检查全部执行' }
  const jevT0 = Date.now()
  if (jevConfigured) {
    const q = quota.check('jev')
    if (!q.ok) {
      jev = { ok: false, capped: true, reasonZh: `${CAPPED_ZH}: jev daily cap reached (${q.pool} pool ${q.used}/${q.cap})` }
    } else {
      jev = await evaluateJev({ state: toMatrixState({ pr, files, profile: baseProfile }), questions: toMatrixQuestions(baseProfile), secrets })
      quota.consume('jev', (jev.usage?.input_tokens ?? 0) + (jev.usage?.output_tokens ?? 0))
    }
  }
  const triage = toTriage({ profile: baseProfile, files, jev, handoff })
  triage.jevStatus = jev.ok ? 'ok' : jev.capped ? 'capped' : jevConfigured ? 'failed' : 'not-configured'
  out.triage = { ...toApplicability(triage), duration_ms: jevConfigured ? Date.now() - jevT0 : null, usage: jev.usage ?? null }
  recordTriage(health, number, out.triage)
  const plan = toStepPlan({ profile: baseProfile, riskClass, triage, files })
  for (const sk of plan.skipped) sk.reason ??= 'triage: change does not touch what this optional step covers'
  if (opts.skipSteps?.length) {
    for (const id of plan.stepIds.filter((x) => opts.skipSteps.includes(x))) {
      const st = baseProfile.steps.find((x) => x.id === id)
      plan.skipped.push({ id, nameZh: st?.nameZh, reason: 'skipped by --skip-step', reasonZh: '运行参数 --skip-step 跳过' })
    }
    plan.stepIds = plan.stepIds.filter((id) => !opts.skipSteps.includes(id))
  }
  const profile = baseProfile
  // 对照版本：已合并 → merge commit 的第一个父提交；开放 → PR head 与目标分支的 merge-base。
  // 先算出来：按 diff 范围工作的检查（verify:design 的 MIVO_GUARD_BASE_SHA、gitleaks 的 base..HEAD）每一轮都用它（${DIFF_BASE_SHA}）
  const baseSha = await reviewBaseSha({ repo, pr, sha, merged, extraRefspecs })
  out.base_sha = baseSha
  const common = { repo, jobId: `n${number}`, profile, extraRefspecs, keepTo: evDir, diffBase: baseSha }

  // 覆盖率轮次：独立检出、只跑单测（带 --coverage），与首轮并行；它的通过 / 失败不参与任何判定
  const covStep = profile.steps.find((x) => x.coverage && x.coverage.kind !== 'v8' && plan.stepIds.includes(x.id))
  // 内存护栏：可用内存低于 ARGUS_PARALLEL_MIN_FREE_GB（默认 8）或 ARGUS_MAX_PARALLEL=1 时，覆盖率轮次排到首轮之后串行跑
  const wantCov = Boolean(covStep) && process.env.ARGUS_COVERAGE !== '0'
  const par = wantCov ? parallelDecision() : null
  // 覆盖率轮次跑所有计划内带 coverage 的步骤（单测 vitest + node --test 等 V8 步骤）
  const covIds = profile.steps.filter((x) => x.coverage && plan.stepIds.includes(x.id)).map((x) => x.id)
  // 覆盖率轮次是单独一轮（不参与判定），带 --coverage 比首轮慢、并行时还和首轮抢 CPU：超时放宽（10-25 #1166 单测覆盖率被 120 分钟超时杀掉 → 覆盖率「未测」）
  const covProfile = (parallel) => ({ ...profile, steps: profile.steps.map((x) => covIds.includes(x.id) ? { ...x, timeoutMin: coverageTimeoutMin(x, parallel) } : x) })
  const startCov = () => ensureMirror(repo, sha, { extraRefspecs }).then(() => executeOnSha({ ...common, profile: covProfile(Boolean(par?.parallel)), label: 'cov', sha, stepIds: covIds })).catch((err) => ({ error: err }))
  if (par) out.run_mode = { coverage_parallel: par.parallel, reason: par.reason }
  let covRun = wantCov && par.parallel ? startCov() : Promise.resolve(null)
  // afterCoverage 的步骤（bench：对机器负载敏感）等并行的覆盖率轮次（单测分片）跑完再开始
  const beforeStep = (st) => (st.afterCoverage && par?.parallel ? covRun : null)
  let first = await executeOnSha({ ...common, label: 'h1', sha, stepIds: plan.stepIds, beforeStep })
  if (setupFailed(first)) {
    health.qa_tool_failures.push({ pr: number, stage: 'setup', message: 'dependency install failed, retrying once', detail: tail(redact(first.find((r) => r.kind === 'setup')?.tail ?? ''), 600) })
    first = await executeOnSha({ ...common, label: 'h1r', sha, stepIds: plan.stepIds, beforeStep })
  }
  // 不适用于这个版本的步骤（appliesIf 不满足）：不进 checks、不参与判定，记进 skipped_steps
  for (const r of first.filter((x) => x.notApplicable)) plan.skipped.push({ id: r.id, nameZh: r.nameZh, reason: `not applicable to this revision: ${r.notApplicableReason}` })
  first = first.filter((x) => !x.notApplicable)
  // 同一份 diff（base..tested）同时给覆盖率门禁和代码审查用：审查的就是被测的那个提交
  const prDiff = await diffForReview({ repo, sha, baseSha, extraRefspecs })
  if (wantCov && !par.parallel) covRun = startCov()
  const cov = await covRun
  // 检查清单（每步带维度）；超时 / 不可用的步骤同时以自己的 step id 记进 qa_tool_failures
  const stepById = Object.fromEntries(profile.steps.map((s) => [s.id, s]))
  const checkRows = (results) => (results ?? []).map((r) => {
    const step = stepById[r.id] ?? { id: r.id, kind: r.kind }
    if (r.unavailable) health.qa_tool_failures.push({ pr: number, stage: r.id, dimension: toBox(step, profile), message: `check could not run: ${r.unavailableReasonZh}` })
    if (r.timedOut) health.qa_tool_failures.push({ pr: number, stage: r.id, dimension: toBox(step, profile), message: `check timed out after ${Math.round(r.durationMs / 60000)} min` })
    return {
      id: r.id, name: r.nameZh, kind: r.kind, dimension: toBox(step, profile), qa_dimensions: step.covers ?? [],
      status: r.unavailable ? 'unavailable' : r.timedOut ? 'timeout' : r.ok ? 'pass' : 'fail',
      exit_code: r.exitCode, signal: r.signal ?? null, ended_at: r.endedAt ?? null, duration_ms: r.durationMs, failure_count: r.failures?.length ?? 0,
      ...(r.unavailable ? { unavailable_reason: r.unavailableReasonZh } : {}),
      ...(r.probeReport ? { sub_checks: probeSummary(r.probeReport) } : {}),
      ...(r.shards ? { shards: r.shards } : {}),
      ...(r.reused ? { reused: true, reused_from: r.reusedFrom } : {}),
    }
  })
  // 时间预算用完的收尾：不再重跑 / 跑基线（外层按 pr-timeout 记未测完）。被打断那一轮跑完的检查要交给 partial_result
  // （report-schema：{ verdict, checks, evidence_dir }），否则报告只剩一条笼统的 budget exceeded，看不出是哪一步吃掉了预算
  const budgetPartial = () => {
    out.partial_result = { verdict: out.verdict, checks: checkRows(first), evidence_dir: out.evidence_dir ?? null }
    return out
  }
  // 覆盖率轮次可能很久：跑完再看一次时间预算
  if (overBudget()) return budgetPartial()
  const readAtSha = (file) => { try { return execFileSync('git', ['-C', mirrorPath(repo), 'show', `${sha}:${file}`], { encoding: 'utf8', maxBuffer: 8 << 20 }) } catch { return null } }
  out.coverage_gate = computeCoverageGate({ first: Array.isArray(cov) ? cov : [], prDiff, covError: cov?.error, changedFiles: files.map((f) => f.filename), readFile: readAtSha })

  let rerun = null, base = null, latest = null, latestIsSame = true
  const failed = failedIds(first)
  if (!setupFailed(first) && failed.length) {
    // 有试运行检查（bench）失败：重跑前先等机器空闲
    if (failed.some((id) => profile.steps.find((x) => x.id === id)?.trialOnly)) {
      out.rerun_idle_wait = await waitForIdle()
      // 等空闲可能很久：等完再看一次时间预算，超了就不重跑
      if (overBudget()) return budgetPartial()
    }
    rerun = await executeOnSha({ ...common, label: 'h2', sha, stepIds: failed })
    const still = failed.filter((id) => failedIds(rerun).includes(id))
    // 首跑失败、重跑通过（不稳定）的步骤也在合并前版本（base）上跑一次，记下基线结果；
    // 和真失败的步骤同一轮 'b' 执行，不影响 outcome 判定（判定只看重跑仍失败的项）
    // 2026-10-26：base 上单跑一次的失败里 10/48 是试运行检查（bench）在重跑刚结束、机器还忙时跑的 → base 前也先等机器空闲
    if (failed.length && baseSha && needsIdleBeforeBase(failed, profile)) { out.base_idle_wait = await waitForIdle(); if (overBudget()) return budgetPartial() }
    if (failed.length && baseSha) base = await executeOnSha({ ...common, label: 'b', sha: baseSha, stepIds: failed })
    if (merged) {
      const prelim = decideOutcome({ first, rerun, base, latest: null, baseKnown: Boolean(baseSha), latestIsSame: true })
      if (prelim.outcome === 'defect') {
        const head = await getBranchHead(repo, pr.base.ref)
        latestIsSame = Boolean(head) && head === sha
        // noLatestRecheck 的步骤（secret-scan）不在最新主干复查：泄露进了历史，后续提交修不掉
        const recheck = prelim.failedSteps.filter((id) => !profile.steps.find((x) => x.id === id)?.noLatestRecheck)
        if (head && !latestIsSame && recheck.length) latest = await executeOnSha({ ...common, label: 'l', sha: head, stepIds: recheck })
        out.latest_main_sha = head
      }
    }
  }
  const decision = decideOutcome({ first, rerun, base, latest, baseKnown: Boolean(baseSha), latestIsSame })
  out.outcome = decision.outcome
  out.outcome_reason = decision.reasonZh
  out.coverage = toCoverage({ profile, triage, results: first, files })
  // 计划要跑却没有结果的步骤（安装失败后中止、时间预算用完等）也列进 skipped_steps
  const ranIds = new Set(first.map((r) => r.id))
  for (const id of plan.stepIds) {
    // 已经以别的理由（不适用于这个版本、triage、--skip-step）记过的步骤不再记「没跑到」
    if (ranIds.has(id) || plan.skipped.some((sk) => sk.id === id)) continue
    const st = profile.steps.find((x) => x.id === id)
    if (st?.kind === 'setup') continue
    plan.skipped.push({ id, nameZh: st?.nameZh, reason: setupFailed(first) ? 'not run: dependency install failed' : 'not run: run stopped before this step (time budget / shutdown)', reasonZh: '没跑到' })
  }
  out.skipped_steps = dedupeSkippedSteps(plan.skipped).map((sk) => ({ ...sk, dimension: toBox(profile.steps.find((x) => x.id === sk.id) ?? { id: sk.id }, profile) }))
  // 接口探测里没跑的子用例（例如没有测试账号的登录态用例）逐条记进 skipped_steps，绝不算通过
  out.skipped_steps.push(...probeSkippedSteps(first, profile))

  // 检查清单（每步带维度）
  out.checks.push(...checkRows(first))

  // 失败检查 → findings
  if (setupFailed(first)) {
    const r = first.find((x) => x.kind === 'setup' && !x.ok)
    const fp = fingerprintOf(repo, ['setup', r.id, wholeStepSignature(r)])
    out.failed_checks.push({ check_id: r.id, check_name: r.nameZh, dimension: 'white-box', test: null, status: 'setup-failed', severity: null, attribution: 'dependency install failed twice; no checks could run', fingerprint: fp, pr_fingerprint: prFingerprint(fp, number), evidence: { tested_sha: sha, exit_code: r.exitCode, timed_out: r.timedOut, log_tail: evidenceTail(r), log_file: join(evDir, 'h1r/logs', `${r.id}.log`) } })
    health.env_problems.push({ pr: number, message: `setup step '${r.id}' failed twice (exit ${r.exitCode})` })
  }
  for (const key of failureKeys(first)) {
    const stepId = key.slice(0, key.indexOf('::'))
    const test = key.slice(key.indexOf('::') + 2)
    const r = first.find((x) => x.id === stepId)
    const step = stepById[stepId] ?? { id: stepId, kind: r?.kind }
    let { status, why } = classifyKey(key, { first, rerun, base, latest, latestIsSame, skipLatest: Boolean(step.noLatestRecheck) })
    if (r?.timedOut) { status = 'timeout'; why = 'check timed out; no comparable failure evidence' }
    const named = test !== '*'
    const fp = fingerprintOf(repo, ['check', stepId, named ? test : wholeStepSignature(r)])
    const type = r?.failureDetails?.find((f) => f.name === test)?.type ?? null
    const compareOn = (results) => { const x = results?.find((y) => y.id === stepId); return x ? (x.unavailable ? 'unavailable' : x.timedOut ? 'timeout' : x.ok ? 'pass' : (named ? (x.failures?.includes(test) ? 'fail' : 'not-failing') : 'fail')) : 'not-run' }
    let baseComparison
    if (status === 'flaky') {
      baseComparison = flakyBaseComparison({ baseSha, baseRun: compareOn(base) })
      why = `${why}; ${baseComparison.note}`
    }
    const baseRun = compareOn(base)
    // 合并前同检查也失败：显式 pre_existing 给日报读；test_timeout 等无法 fingerprint 对齐时仍标为 pre-existing（P3）
    if (baseRun === 'fail' && status === 'unverified') {
      status = 'pre-existing'
      why = 'also fails on the base revision (base run failed; failure type not fingerprint-comparable, e.g. test_timeout)'
    }
    const preExisting = status === 'pre-existing' || baseRun === 'fail'
    // 试运行检查（bench）的用例超时、空闲机器上重跑通过：机器负载（审计 2026-10-18 S3），不算这个 PR 的发现
    const envTimeout = isTrialTimeout(step, r, test, { status, rerun: compareOn(rerun), idle: out.rerun_idle_wait?.idle })
    out.failed_checks.push({
      check_id: stepId, check_name: r?.nameZh, dimension: toBox(step, profile), qa_dimensions: step.covers ?? [],
      test: named ? test : null, failure_type: type, status, flaky: status === 'flaky', related_to_pr: status === 'flaky' ? false : undefined,
      pre_existing: preExisting,
      severity: envTimeout ? 'P3' : severityFor({ status, step, merged }), attribution: envTimeout ? `${why}; test timed out in a trial-only check and passed on the idle-machine rerun: treated as environment load, not a PR finding` : why,
      ...(envTimeout ? { not_a_pr_finding: true, env_suspected: 'trial-timeout' } : {}),
      // 统一分级 P0 要人工或裁决确认：已合并、产品类、本 PR 引入且（复查过的）最新主干仍失败 → 只标 p0_candidate，等级仍是 P1
      ...(!envTimeout && p0Candidate({ status, step, merged }) ? { p0_candidate: true, p0_note: p0NoteFor({ step, latestSha: out.latest_main_sha ?? null, latestIsSame, latestRun: latest ? compareOn(latest) : null }) } : {}),
      fingerprint: fp, pr_fingerprint: prFingerprint(fp, number),
      evidence: {
        tested_sha: sha, base_sha: baseSha, exit_code: r?.exitCode, signal: r?.signal ?? null, ended_at: r?.endedAt ?? null, timed_out: Boolean(r?.timedOut),
        first_run: 'fail', rerun: compareOn(rerun), base_run: baseRun, ...(latest ? { latest_main_run: compareOn(latest) } : {}),
        command: (step.cmd ?? []).join(' '), log_tail: evidenceTail(r, named ? test : null), log_file: join(evDir, 'h1/logs', `${stepId}.log`),
        ...(step.trialOnly ? { machine_load: { first_run: r?.machine ?? null, rerun_wait: out.rerun_idle_wait ?? null, rerun: rerun?.find((y) => y.id === stepId)?.machine ?? null } } : {}),
        ...(baseComparison?.compared ? { base_log_file: join(evDir, 'b/logs', `${stepId}.log`) } : {}),
      },
      ...(baseComparison ? { base_comparison: baseComparison } : {}),
    })
  }

  // PR 自己改过的测试文件：所有检查的测试报告里都找不到 → P2（没有任何一步执行它）
  const notRun = setupFailed(first) ? [] : ownTestsNotRun({ files, results: first, profile })
  // 统一分级：没证实是作者的问题（多半是 QA 这边没配上对应的运行器）→ 待核实（QA 自查），不发作者
  if (notRun.length) {
    const fp = fingerprintOf(repo, ['own-tests-not-run', `pr=${number}`, notRun.join(',')])
    out.pending_verification.push(toPending('own-tests', { source: 'own-tests', check_id: 'own-tests-not-run', dimension: 'white-box', severity: 'P2',
      title: `changed test file(s) not executed by any check: ${notRun.slice(0, 5).join(', ')}${notRun.length > 5 ? ` (+${notRun.length - 5})` : ''}`,
      fingerprint: fp, pr_fingerprint: prFingerprint(fp, number), evidence: { tested_sha: sha, files: notRun } },
      { reason: 'no check executed the changed test files; first check whether the QA profile runs them', reasonZh: 'PR 改过的测试没被任何检查跑到，先查 QA 自己有没有配上对应的运行器' }))
  }

  // AI 补测：Jev 判为适用、但没有检查覆盖的维度 → 生成 vitest 用例，经「当前通过 + 植入错误失败」验证后才保留。
  // 独立的执行轮次（ai1、ai2…），结果不参与 outcome 判定；在当前代码上失败的只记 P2、test_backed=false。
  if (!opts.noAiTests) await runAiTests({ out, repo, pr, files, profile, riskClass, sha, common, db, secrets, health, number, results: first, quota, handoff })
  settleHandoff(out.handoff, out.ai_tests ?? null, 'ai-tests-disabled')
  out.coverage = withDimensionStatus(out.coverage, out.triage, out.ai_tests, profile, first, files)
  // Jev 因额度没跑：没判定的维度记 capped；AI 补测因额度没做完：剩下的缺口维度记 capped（不是普通 gap）
  if (out.triage?.status === 'capped') out.quota_capped.push(quota.markCapped('jev', { pr: number, dimensions: out.coverage?.capped ?? out.triage.undetermined }))
  if (out.ai_tests?.capped) out.quota_capped.push(quota.markCapped('ai-test', { pr: number, dimensions: out.ai_tests.capped_gaps }))

  // ming-qa 代码审查（白盒）
  if (!opts.noReview) await runCodeReview({ out, repo, pr, files, prDiff, sha, baseSha, results: first, profile, evDir, secrets, health, number, quota, opts, handoffRaw })
  // 人话摘要（Argus 原有 AI 解读，可选）
  if (!opts.noReview) {
    const ai = await analyzePr({ db, secrets, riskClass, pr, files, profile, results: first, outcome: decision.outcome, quota })
    if (ai.status === 'ok') out.ai_summary = { summary: ai.summaryZh, risks: ai.risksZh, suspect: ai.suspectZh || null, failure_explanation: ai.failureZh || null }
    else if (ai.status === 'capped') { out.ai_summary = { status: 'capped', reason: ai.reasonZh }; out.quota_capped.push(quota.markCapped('pr-summary', { pr: number })) }
    else if (ai.status === 'error') health.llm_errors.push({ pr: number, stage: 'pr-summary', model: LLM_MODEL, message: `${ai.reasonZh} ${ai.detail ?? ''}`.trim() })
  }

  // 覆盖率（统一分级，Praise 2026-10-23）：新代码覆盖率低于 90 本身不再记 P2，数字照常写在 coverage_gate 里当信息。
  // 只有 PR 正文或 docs/pr/<N>/qa.md 自己写明了覆盖率目标却没达到，才按「自己定的验收没达成」记 P2（source: stated-acceptance）
  const stated = statedAcceptanceFinding({ gate: out.coverage_gate, body: pr.body, qaMd: handoffRaw.qa, repo, number, sha, baseSha })
  if (stated) out.findings.push(stated)
  for (const c of out.failed_checks.filter((x) => x.not_a_pr_finding)) health.env_problems.push({ pr: number, message: `trial check '${c.check_id}' timed out${c.test ? ` (${c.test})` : ''} and passed on the idle-machine rerun: treated as environment load, not a PR finding` })
  // 失败检查：unverified / setup-failed / timeout / flaky 不是作者的问题 → 待核实（QA 自查）；其余按 severityFor 进 findings
  const asCheck = (c) => ({ source: 'check', test_backed: checkIsTestBacked(c), ...c })
  for (const c of out.failed_checks.filter((x) => !x.not_a_pr_finding && QA_SELF_CHECK_STATUSES.includes(x.status))) {
    out.pending_verification.push(toPending('check-qa', asCheck(c), { reason: `check ${c.status}: not attributable to the PR author; QA re-runs / fixes the environment first`, reasonZh: `检查结果是 ${c.status}，不是作者的问题，QA 先自己查清（重跑、修环境）` }))
  }
  out.findings = [...out.failed_checks.filter((c) => !c.not_a_pr_finding && !QA_SELF_CHECK_STATUSES.includes(c.status)).map(asCheck), ...out.findings]
  out.severity = out.findings.length ? maxSev(out.findings.map((f) => f.severity)) : 'P3'
  if (out.pending_verification.length) out.pending_summary = pendingSummary(out.pending_verification)
  Object.assign(out, dimensionsOf(out.checks, out.skipped_steps))
  out.tested = true
  out.verdict = verdictOf(out)
  out.duration_s = Math.round((Date.now() - startedAt) / 1000)
  out.tested_at = toShanghaiIso()
  // 测试与审查必须是同一个提交
  out.sha_consistent = out.reviewed_sha == null || out.reviewed_sha === out.tested_sha
  if (!out.sha_consistent) health.notes.push(`PR #${number}: tests ran on ${String(out.tested_sha).slice(0, 12)} but code review looked at ${String(out.reviewed_sha).slice(0, 12)} (${out.review_diff_source})`)
  return out
}

// run.args 里的 ai_test_caps / ai_test_tier：单 PR 运行就是这个 PR 的生效值；多 PR 取各 PR 中最大的一档并附 per_pr
const TIER_ORDER = ['none', 'S', 'M', 'L', 'XL']
function pickTop(prs) {
  const t = prs.filter((p) => p.ai_test_tier && !p.reused)
  if (!t.length) return null
  return t.reduce((a, b) => (TIER_ORDER.indexOf(b.ai_test_tier.tier) > TIER_ORDER.indexOf(a.ai_test_tier.tier) ? b : a))
}
export function runAiTestCaps(prs) {
  const top = pickTop(prs)
  return top ? { ...top.ai_test_caps } : aiTestCaps()
}
export function runAiTestTier(prs) {
  const t = prs.filter((p) => p.ai_test_tier && !p.reused)
  const top = pickTop(prs)
  if (!top) return null
  return { ...top.ai_test_tier, pr: top.number, per_pr: t.map((p) => ({ pr: p.number, tier: p.ai_test_tier.tier, type: p.ai_test_tier.type, product_lines: p.ai_test_tier.product_lines, files: p.ai_test_tier.files, degraded: Boolean(p.ai_test_tier.degraded), caps: p.ai_test_caps })) }
}

// ── AI 补测（夜间）────────────────────────────────────────
// llm：测试注入（fetchImpl / netRetryDelayMs / sleep），生产不传
// handoff：parseHandoff 的结果；层次 unit/contract/browser-fixture 的 Q 和 qa.md 第 5 节条目排在覆盖缺口之前（不加条数、不加调用次数）
export async function runAiTests({ out, repo, pr, files, profile, riskClass, sha, common, db, secrets, health, number, results, quota = memoryQuota(), llm = {}, handoff = null }) {
  const tiered = degradeForQuota(aiTestTier({ files, riskClass }), quota.check('ai-test'))
  const caps = tiered.caps
  out.ai_test_tier = tiered.tier
  out.ai_test_caps = { ...caps }
  const t0 = Date.now()
  const deadline = t0 + caps.maxWallMin * 60_000
  const ai = out.ai_tests = { status: 'not-run', reason: null, model: null, caps: { max_model_calls: caps.maxCalls, max_wall_min: caps.maxWallMin, max_tests_per_call: caps.maxTestsPerCall },
    model_calls: 0, wall_s: 0, gaps: [], generated: 0, kept: 0, discarded: 0, invalid: 0, fails_on_current: 0, newly_covered: [], tests: [], rounds: [] }
  const finish = (status, reason = null) => { ai.status = status; ai.reason = reason; ai.wall_s = Math.round((Date.now() - t0) / 1000) }
  if (!caps.enabled) return finish('disabled', 'ARGUS_AITESTS=0')
  if (!profile.aiTests) return finish('skipped', 'profile has no aiTests config')
  if (setupFailed(results)) return finish('skipped', 'dependency install failed')
  if (out.triage?.status !== 'ok') return finish('skipped', `dimension applicability not decided (triage ${out.triage?.status ?? 'missing'})`)
  ai.gaps = toAiGaps({ applicability: out.triage, profile, results, files })
  // 首要目标：覆盖率门禁算出的「改动里没被执行到的行」（门禁在补测之前算好）
  ai.uncovered_targets = toUncoveredTargets(out.coverage_gate?.new_code)
  const handoffCases = handoff?.present ? splitHandoffCases(handoff).auto : []
  if (!ai.gaps.length && !ai.uncovered_targets.length && !handoffCases.length) return finish('skipped', 'no applicable gap dimension and no uncovered changed lines')
  if (caps.maxCalls < 1) return finish(tiered.tier.tier === 'none' ? 'skipped' : tiered.tier.degraded ? 'capped' : 'skipped', tiered.tier.tier === 'none' ? `ai-test tier none: ${tiered.tier.reason}` : tiered.tier.degraded ? `${CAPPED_ZH}: ${tiered.tier.reason}` : 'ARGUS_AITEST_MAX_CALLS=0')
  const cHits = highRiskFiles(files, profile)
  const setupIds = profile.steps.filter((s) => s.kind === 'setup').map((s) => s.id)
  // 被测源文件（镜像里该提交的版本）：开头几十行给模型看函数签名 / 配置字段；全文用来判「测试传入的对象缺字段」（#1013）
  const sourceAt = (file) => { try { return execFileSync('git', ['-C', mirrorPath(repo), 'show', `${sha}:${file}`], { encoding: 'utf8', maxBuffer: 8 << 20 }) } catch { return null } }
  const sources = files.filter((f) => f.status !== 'removed' && /\.[cm]?[jt]sx?$/.test(f.filename) && !TEST_FILE_RE.test(f.filename)).slice(0, 6)
    .map((f) => ({ file: f.filename, head: toSourceHead(sourceAt(f.filename)) })).filter((x) => x.head)
  const withSource = (t) => t && { ...t, source: sourceAt(t.mutation.file) }
  let remaining = [...ai.gaps]
  let targets = [...ai.uncovered_targets]
  let pendingHandoff = [...handoffCases]
  const feedback = []
  let offset = 0
  const stepDefs = new Map()
  const pendingLlmErrors = []
  // 网关断连（原地重试后仍失败）不计入 maxCalls：等约 30 s 后最多补 1 轮（S 档 maxCalls=1 断一次也还有一次机会）
  let netExtra = 0
  for (let round = 1; round <= caps.maxCalls + netExtra && (remaining.length || targets.length || pendingHandoff.length); round++) {
    const left = deadline - Date.now()
    if (left < 3 * 60_000) { ai.rounds.push({ round, skipped: 'wall-time cap reached' }); break }
    const gen = await generateTargetedTests({ db, secrets, riskClass, pr, files, profile, gaps: remaining, uncovered: targets, handoffCases: pendingHandoff, handoffContext: handoff?.present ? { risks: handoff.risks, boundaries: handoff.boundaries } : null, maxTests: caps.maxTestsPerCall, highRiskFiles: cHits, feedback, sources, timeoutMs: Math.max(1, Math.floor(Math.min(8 * 60_000, left / 2))), quota, deadline, ...llm })
    if (gen.status === 'capped') {
      // 每日 ai-test 额度用尽：这一轮及之后都不调模型；剩下的缺口维度记 capped（显示「额度用尽未补测」）
      ai.capped = true; ai.capped_gaps = [...remaining]; ai.quota = gen.quota
      ai.rounds.push({ round, gaps: [...remaining], status: 'capped', reason: gen.reasonZh, offered: 0, rejected_by_validation: 0, tokens: null, tests: [] })
      break
    }
    const called = !['skipped'].includes(gen.status) && !gen.network
    if (called) ai.model_calls++
    if (gen.model) ai.model = gen.model
    const rr = { round, gaps: [...remaining], status: gen.status, reason: gen.reasonZh ?? null, offered: gen.offered ?? 0, rejected_by_validation: gen.rejected_by_validation ?? 0, tokens: gen.usage?.total_tokens ?? null, tests: [] }
    ai.rounds.push(rr)
    // 可重试的模型错误（空内容 / 半截 JSON）：还有调用次数就再试一轮；之后有一轮写出了测试就不算 QA 故障（rounds 里仍留记录）
    if (gen.status === 'error' && gen.retryable) pendingLlmErrors.push({ pr: number, stage: 'ai-tests', model: gen.model ?? null, message: gen.reasonZh })
    else if (gen.status === 'error') health.llm_errors.push({ pr: number, stage: 'ai-tests', model: gen.model ?? null, message: gen.reasonZh })
    else if (gen.status === 'ok') pendingLlmErrors.length = 0
    if (gen.network && !netExtra) {
      netExtra = 1
      // 等完还要留够一轮的 3 分钟，否则不补
      if (!await waitWithinBudget(30_000, { deadline: deadline - 3 * 60_000, sleep: llm.sleep })) { ai.rounds.push({ round: round + 1, skipped: 'wall-time cap reached' }); break }
    }
    if (!gen.tests?.length) { if (gen.status === 'skipped' || (gen.status === 'error' && !gen.retryable)) break; continue }
    const steps = toAiTestSteps(gen.tests, profile, { offset }).map((st) => ({ ...st, timeoutMin: Math.max(1, Math.min(10, Math.floor((deadline - Date.now()) / 60_000))) }))
    offset += steps.length
    for (const st of steps) stepDefs.set(st.id, st)
    ai.generated += steps.length
    const aiProfile = { ...profile, steps: [...profile.steps, ...steps] }
    const run = await executeOnSha({ ...common, profile: aiProfile, label: `ai${round}`, sha, stepIds: [...setupIds, ...steps.map((s) => s.id)] })
    for (const st of steps) {
      const r = run.find((x) => x.id === st.id)
      const verdict = toAiTestResult(setupFailed(run) ? null : r, withSource(st.aiTest))
      const t = { id: st.id, round, kind: st.aiKind, purpose: st.aiTest.purposeZh, covers: st.covers, file: st.aiTest.filePath, mutation_file: st.aiTest.mutation.file, ...(st.aiTest.targetsUncovered ? { targets_uncovered: st.aiTest.targetsUncovered } : {}), ...(st.aiTest.handoff ? { handoff: st.aiTest.handoff } : {}), ...verdict, exit_code: r?.exitCode ?? null, duration_ms: r?.durationMs ?? null, log_tail: r ? tail(redact(String(r.tail ?? '')), 1500) : null }
      ai.tests.push(t); rr.tests.push(st.id)
      if (verdict.result !== 'kept') feedback.push(`${st.aiTest.purposeZh}（${st.aiTest.filePath}）：${verdict.reason}`)
    }
    const keptCovers = new Set(ai.tests.filter((t) => t.result === 'kept').flatMap((t) => t.covers))
    remaining = ai.gaps.filter((k) => !keptCovers.has(k))
    const keptFiles = new Set(ai.tests.filter((t) => t.result === 'kept').map((t) => t.mutation_file))
    targets = targets.filter((u) => !keptFiles.has(u.file))
    const keptHandoff = new Set(ai.tests.filter((t) => t.result === 'kept' && t.handoff).map((t) => t.handoff))
    pendingHandoff = pendingHandoff.filter((c) => !keptHandoff.has(c.id))
  }
  health.llm_errors.push(...pendingLlmErrors)
  // 在当前代码上失败的补测：再单独跑一次确认是否复现（未确认、不算测试佐证；进待核实，人确认后才定级，见 verify-ai-lead）
  const failing = ai.tests.filter((t) => t.result === 'fails-on-current')
  if (failing.length && deadline - Date.now() > 2 * 60_000) {
    const steps = failing.map((t) => stepDefs.get(t.id)).filter(Boolean)
    const again = await executeOnSha({ ...common, profile: { ...profile, steps: [...profile.steps, ...steps] }, label: 'air', sha, stepIds: [...setupIds, ...steps.map((s) => s.id)] })
    for (const t of failing) {
      const r = again.find((x) => x.id === t.id)
      t.reproduced = r ? toAiTestResult(r, withSource(stepDefs.get(t.id)?.aiTest)).result === 'fails-on-current' : null
    }
  } else for (const t of failing) t.reproduced = null
  ai.kept = ai.tests.filter((t) => t.result === 'kept').length
  ai.fails_on_current = failing.length
  ai.discarded = ai.tests.filter((t) => t.result === 'discarded').length
  ai.invalid = ai.tests.filter((t) => t.result === 'discarded' && t.invalid).length // 测试无效：已含在 discarded 里
  ai.newly_covered = ai.gaps.filter((k) => ai.tests.some((t) => t.result === 'kept' && t.covers.includes(k)))
  for (const t of ai.tests) {
    out.checks.push({ id: t.id, name: `AI test: ${t.purpose}`, kind: 'product', source: 'ai-test', dimension: 'white-box', qa_dimensions: t.covers,
      status: t.result === 'kept' ? 'pass' : t.result === 'fails-on-current' ? 'fail' : 'discarded', exit_code: t.exit_code, duration_ms: t.duration_ms, failure_count: t.result === 'fails-on-current' ? 1 : 0, ai_result: t.result, ai_reason: t.reason, ...(t.invalid ? { ai_invalid: true, display_zh: t.display_zh } : {}) })
    if (t.result === 'fails-on-current') {
      const fp = fingerprintOf(repo, ['ai-test', t.file, t.purpose])
      out.pending_verification.push(toPending('ai-test', { source: 'ai-test', check_id: t.id, check_name: `AI test: ${t.purpose}`, dimension: 'white-box', qa_dimensions: t.covers, severity: 'P2',
        title: `AI-generated test fails on the tested commit: ${t.purpose}`, status: 'unconfirmed', reproduced: t.reproduced, verified: false, verified_at: null, verified_by: null, verified_note: null, upgrade: 'only after the failure is reproduced and confirmed by a human',
        fingerprint: fp, pr_fingerprint: prFingerprint(fp, number), evidence: { tested_sha: sha, file: t.file, covers: t.covers, log_tail: t.log_tail } },
        { reason: 'AI-generated test fails on the tested commit: unconfirmed, not test-backed', reasonZh: 'AI 补测在当前代码上失败：未确认、不算测试佐证，QA 先复现并确认' }))
    }
  }
  if (ai.capped) ai.capped_gaps = ai.capped_gaps.filter((k) => !ai.newly_covered.includes(k))
  // 一次模型调用都没成就被额度挡住 → status capped；做了一部分 → status 按结果，capped: true + capped_gaps
  if (ai.capped && !ai.generated) return finish('capped', `${CAPPED_ZH}: ${ai.rounds.at(-1)?.reason ?? 'ai-test daily cap reached'}`)
  finish(ai.generated ? 'ok' : ai.rounds.some((r) => r.status === 'error') ? 'error' : 'empty', ai.generated ? (ai.capped ? `${CAPPED_ZH} (partly): ${ai.rounds.at(-1)?.reason}` : null) : (ai.rounds.at(-1)?.reason ?? ai.rounds.at(-1)?.skipped ?? null))
}

// 覆盖率 → finding（统一分级）：低于参考线不出 finding；PR 正文或 docs/pr/<N>/qa.md 写明的覆盖率目标没达到 → 一条 P2（stated-acceptance）。
// 会在 gate 上记 finding_policy 和 stated_target（找到的目标 + 实测 + met / missed / not-evaluated）
const metricZh = (m) => ({ new_code: '新代码', lines: '全仓行', branches: '全仓分支', functions: '全仓函数', new_code_branches: '新代码分支', new_code_functions: '新代码函数' }[m] ?? m)
export function statedAcceptanceFinding({ gate, body, qaMd, repo, number, sha, baseSha }) {
  if (!gate) return null
  gate.finding_policy = 'informational: no finding for being below the reference line; a P2 only when the PR body or docs/pr/<N>/qa.md states a coverage target that is not met'
  const stated = evaluateStatedTarget(statedCoverageTarget([{ source: 'pr-body', text: body }, { source: `docs/pr/${number}/qa.md`, text: qaMd }]), gate)
  gate.stated_target = stated
  if (stated?.status !== 'missed') return null
  const fp = fingerprintOf(repo, ['stated-acceptance', 'coverage', `pr=${number}`, stated.metric, String(stated.pct)])
  return {
    source: 'stated-acceptance', test_backed: false, check_id: 'coverage-target', dimension: 'white-box', severity: 'P2', category: 'acceptance',
    title: `PR 自己写明的覆盖率目标没达到：写明${metricZh(stated.metric)}覆盖率 ≥ ${stated.pct}%，实测 ${stated.value}%`,
    fingerprint: fp, pr_fingerprint: prFingerprint(fp, number),
    evidence: { tested_sha: sha, base_sha: baseSha, stated_target: stated, values: gate.values, baseline: gate.baseline, new_code_files: (gate.new_code?.files ?? []).slice(0, 20),
      trigger: `${stated.source} 写明：「${stated.quote}」`, behavior: `实测${metricZh(stated.metric)}覆盖率 ${stated.value}% < ${stated.pct}%`, impact: '自己定的验收标准没达成（统一分级 P2 必修第 2 类）',
      recommendation: `补测试把${metricZh(stated.metric)}覆盖率提到 ${stated.pct}% 以上，或在 PR / 交接文件里改写验收标准并说明原因` },
  }
}

// 改动（非删除）的测试文件里，哪些不在任何一步的测试报告里。可能执行它的步骤（runsFiles 命中，或有报告且没写 runsFiles）
// 报告不完整（失败时报告与日志对不上等）→ 说不清，不报
export const TEST_FILE_RE = /\.(test|spec)\.[cm]?[jt]sx?$|\.node-test\.[cm]?js$|(^|\/)test_[^/]+\.py$|_test\.py$/
export function ownTestsNotRun({ files, results, profile }) {
  const changed = (files ?? []).filter((f) => f.status !== 'removed' && TEST_FILE_RE.test(f.filename)).map((f) => f.filename)
  if (!changed.length) return []
  const stepOf = Object.fromEntries(profile.steps.map((s) => [s.id, s]))
  const reported = (results ?? []).filter((r) => stepOf[r.id]?.testReport && !r.unavailable)
  const seen = new Set(reported.flatMap((r) => (r.testOutcomes ?? []).map((t) => String(t.caseId).split(' > ')[0])))
  const unclear = (file) => reported.some((r) => r.testOutcomeStatus !== 'complete' && (!stepOf[r.id].runsFiles || stepRunsChangedFile(stepOf[r.id], [file])))
  return changed.filter((f) => !seen.has(f) && !unclear(f))
}

// coverage（QA 维度覆盖，不是代码覆盖率）补上：applicable_total / covered_total 与逐维度状态
// 只有改动碰到步骤实际测的路径（step.exercises）时，这一步才算覆盖它声明的维度
export function withDimensionStatus(cov, triage, ai, profile, results, files = []) {
  if (!cov || !triage?.dimensions) return cov
  const ran = new Set((results ?? []).filter((r) => r.kind !== 'setup' && !r.unavailable && !r.timedOut).map((r) => r.id))
  const paths = files.map((f) => f.filename)
  const byChecks = (k) => profile.steps.filter((s) => ran.has(s.id) && (s.covers ?? []).includes(k) && stepExercises(s, paths)).map((s) => s.id)
  const byAi = (k) => (ai?.tests ?? []).filter((t) => t.result === 'kept' && t.covers.includes(k)).map((t) => t.id)
  const prTests = prTestDimensions({ files, results, profile })
  const dims = {}
  // capped（额度用尽未补测）：Jev 因额度没跑时没判定的维度；AI 补测因额度没做的缺口维度。和普通 gap 分开计
  const jevCapped = triage.status === 'capped'
  const aiCapped = new Set(ai?.capped ? ai.capped_gaps ?? [] : [])
  for (const [k, d] of Object.entries(triage.dimensions)) {
    const checks = byChecks(k), aiIds = byAi(k)
    const status = d.applicability === 'not-applicable' ? 'not-applicable' : d.applicability === 'undetermined' ? (jevCapped ? 'capped' : 'undetermined')
      : checks.length || prTests[k]?.length ? 'covered' : aiIds.length ? 'covered-by-ai-test' : aiCapped.has(k) ? 'capped' : 'gap'
    dims[k] = { applicability: d.applicability, status, checks, ai_tests: aiIds, ...(prTests[k]?.length ? { pr_tests: prTests[k] } : {}), ...(status === 'capped' ? { display_zh: CAPPED_ZH, capped_by: jevCapped && d.applicability === 'undetermined' ? 'jev' : 'ai-test' } : {}) }
  }
  return { ...cov, ...coverageTotalsFrom(dims), dimension_status: dims, basis: 'dimension_status (from prs[].triage, Jev)' }
}

// 所有 coverage 汇总字段都只从 dimension_status 推出来，保证和 Jev 的结论（triage.dimensions）不矛盾：
// applicable = applicability applicable；covered = 其中 status covered / covered-by-ai-test；missed = gaps = 其中 status gap；
// undetermined = applicability undetermined；coverage = covered / applicable。
export function coverageTotalsFrom(dims) {
  const keys = Object.keys(dims ?? {})
  const applicable = keys.filter((k) => dims[k].applicability === 'applicable')
  const covered = applicable.filter((k) => ['covered', 'covered-by-ai-test'].includes(dims[k].status))
  const gaps = applicable.filter((k) => dims[k].status === 'gap')
  const undetermined = keys.filter((k) => dims[k].applicability === 'undetermined')
  return {
    applicable, covered, missed: gaps, undetermined,
    undeterminedMissed: undetermined.filter((k) => !(dims[k].checks?.length || dims[k].ai_tests?.length)),
    coverage: applicable.length ? covered.length / applicable.length : null,
    applicable_total: applicable.length, covered_total: covered.length, covered_dimensions: covered, gaps,
    ai_covered: applicable.filter((k) => dims[k].status === 'covered-by-ai-test'),
    not_applicable_total: keys.filter((k) => dims[k].applicability === 'not-applicable').length,
    undetermined_total: undetermined.length,
    // 额度用尽未补测（status capped）：不算 gap，也不算覆盖
    capped: keys.filter((k) => dims[k].status === 'capped'),
    capped_total: keys.filter((k) => dims[k].status === 'capped').length,
  }
}

// skipped_steps：每个步骤只出现一次。第一个理由最具体（not applicable / triage / --skip-step 先于「没跑到」记录），后面的同 id 条目丢弃
export function dedupeSkippedSteps(list) {
  const seen = new Set()
  return (list ?? []).filter((sk) => (seen.has(sk.id) ? false : (seen.add(sk.id), true)))
}

// 文档改动（risk A）的覆盖率门禁：没有单测运行，不是失败也不是 unavailable
export const DOCS_ONLY_COVERAGE_GATE = () => ({ status: 'not-applicable', reason: 'docs-only change' })

// 接口探测报告 → skipped_steps 条目（id: api-probe/<子用例>，parent_step 指回探测步骤）
export function probeSkippedSteps(results, profile) {
  const out = []
  for (const r of results ?? []) {
    for (const s of r.probeReport?.skipped ?? []) {
      const step = profile.steps.find((x) => x.id === r.id) ?? { id: r.id }
      out.push({ id: `${r.id}/${s.id}`, nameZh: s.name, reason: s.reason, parent_step: r.id, dimension: toBox(step, profile) })
    }
  }
  return out
}
export function probeSummary(rep) {
  const checks = rep.checks ?? []
  return { total: checks.length + (rep.skipped?.length ?? 0), passed: checks.filter((c) => c.status === 'pass').length, failed: checks.filter((c) => c.status !== 'pass').length, skipped: rep.skipped?.length ?? 0, auth: rep.auth ?? null }
}

// 额度：system_health.quota = { pool, day, caps, caps_source, used_by_run, used_today, capped[] }；
// 每个 capped 另加一条 notes（「额度用尽未补测：…」）。capped 让 status 变成 degraded，但不是 qa_tool_failures（不开 issue）
export function recordQuota(health, summary) {
  health.quota = summary
  for (const c of summary.capped ?? []) health.notes.push(`${CAPPED_ZH}：#${c.pr ?? '?'} ${c.kind} (${c.pool} pool ${c.used}/${c.cap}, ${c.day})${c.dimensions?.length ? ` — ${c.dimensions.join(', ')}` : ''}`)
}

// 分诊结果进 system_health.triage；Jev 失败另记 llm_errors（失败 / 没配置时由 runNightly 加说明）
export const TRIAGE_NOTE_NOT_CONFIGURED = '维度适用性未判定（Jev 未配置）'
export const TRIAGE_NOTE_FAILED = '维度适用性未判定（Jev 分诊失败）'
export function recordTriage(health, number, t) {
  const h = health.triage ??= { model: null, prs: [], failed: [] }
  if (t.model) h.model = t.model
  h.prs.push({ pr: number, status: t.status, applicable: t.applicable.length, not_applicable: t.not_applicable.length, undetermined: t.undetermined.length, duration_ms: t.duration_ms ?? null })
  if (t.status === 'failed') {
    h.failed.push({ pr: number, reason: t.reason })
    health.llm_errors.push({ pr: number, stage: 'triage(jev)', message: t.reason })
  }
}

// 对照版本：已合并 → merge commit 的第一个父提交；开放 → PR head 与目标分支的 merge-base
async function reviewBaseSha({ repo, pr, sha, merged, extraRefspecs }) {
  if (merged) return (await getCommit(repo, sha))?.parents?.[0]?.sha ?? null
  const mirror = await ensureMirror(repo, sha, { extraRefspecs })
  return mergeBase(mirror, sha, pr.base.sha)
}

// ── 合并前已裁定的问题（PR 讨论）──────────────────────────
// 盯梢正式裁决 / 盯梢核实回帖 / 维护者裁定不改（解析见 rulings.mjs）。只有盯梢正式裁决、且裁定之后相关文件没改的，
// 才会确定性地把同一问题移出 findings（matchPriorRuling，还要内容相近）；盯梢核实回帖（只收审查账号或 PR 作者 author 发的）
// 和维护者裁定只写进审查 / 复核提示词，由模型判断（mode: prompt-only）。
// 读不到 PR 讨论不挡审查：照常审，system_health.notes 记一句。deps 供测试替换 GitHub / git
export async function loadPriorRulings({ repo, number, sha, profile, health, author = null, deps = {} }) {
  const cfg = profile?.reviewRulings ?? {}
  const empty = (extra = {}) => ({ rulings: [], summary: { used: 0, rejected: 0, items: [], ...extra } })
  // profile 里没开（reviewRulings.enabled）就不读 PR 讨论
  if (cfg.enabled !== true) return empty({ disabled: true })
  try {
    const comments = await (deps.listPullReviewComments ?? listPullReviewComments)(repo, number)
    const threads = threadsFromReviewComments(comments)
    if (!threads.length) return empty()
    let maintainers = cfg.maintainers ?? []
    if (cfg.maintainersTeam) {
      const [org, team] = String(cfg.maintainersTeam).split('/')
      try { maintainers = await (deps.listTeamMembers ?? listTeamMembers)(org, team) } catch (err) { health?.notes.push(`PR #${number}: maintainers team ${cfg.maintainersTeam} not readable (${redact(err.message).slice(0, 120)}); using the profile list for 维护者裁定不改`) }
    }
    const { rulings, rejected } = collectPriorRulings({ threads, reviewer: cfg.reviewer ?? 'PraiseZhu', author, maintainers, standardVersion: loadSeverityStandard().version })
    const rejectedItems = rejected.slice(0, 10).map(({ finding_text, ...r }) => r)
    if (!rulings.length) return { rulings: [], summary: { used: 0, rejected: rejected.length, items: [], rejected_items: rejectedItems } }
    const headAt = headAtFrom(await (deps.listPullCommits ?? listPullCommits)(repo, number))
    const heads = rulings.map((r) => r.ruled_head ?? headAt(r.at)).filter(Boolean)
    const codeState = deps.codeState ?? await gitCodeState({ repo, number, sha, heads })
    const withState = withCodeState(rulings, { codeState, headAt })
    return { rulings: withState, summary: { used: withState.length, rejected: rejected.length,
      items: withState.map((r) => ({ kind: r.kind, mode: r.kind === 'adjudication' ? 'auto-match' : 'prompt-only', verdict: r.verdict, path: r.path, line: r.line, severity: r.severity, key: r.key ?? null,
        code_state: r.code_state, ruled_head: r.ruled_head, url: r.url, by: r.by, at: r.at, finding: r.finding })),
      rejected_items: rejectedItems } }
  } catch (err) {
    health?.notes.push(`PR #${number}: prior rulings not read (${redact(err.message).slice(0, 160)}); code review ran without them`)
    return empty({ error: redact(err.message).slice(0, 200) })
  }
}
// 裁定时的 head 到被测提交之间，这个文件改没改（镜像里没有裁定时的提交 → 先按镜像的凭证补拉一次 PR head 引用；拉不到就是 unknown）
async function gitCodeState({ repo, number, sha, heads = [] }) {
  const mirror = mirrorPath(repo)
  const has = (c) => { try { execFileSync('git', ['-C', mirror, 'cat-file', '-e', `${c}^{commit}`], { stdio: 'ignore' }); return true } catch { return false } }
  if (heads.some((h) => !has(h))) { try { await ensureMirror(repo, null, { extraRefspecs: [`+refs/pull/${number}/head:refs/pull/${number}/head`] }) } catch { /* unknown */ } }
  return (head, path) => {
    if (!has(head) || !sha) return 'unknown'
    try { execFileSync('git', ['-C', mirror, 'diff', '--quiet', head, sha, '--', path], { stdio: 'ignore' }); return 'unchanged' } catch (err) { return err.status === 1 ? 'changed' : 'unknown' }
  }
}

// AI 代码审查单独发现的 P1：发给作者之前先用整仓复核会话核实（review-verify.mjs）。每个 PR 最多核 VERIFY_MAX_PER_PR 条，
// 额度（ai-verify）、模型、git 任何一环不行都是「没核实」→ 待核实，不发作者。结果按提交 + 问题缓存，重跑结论一致
// 2026-10-26（Praise）：AI 单独报的 P1 只有两种情况才发作者 —— ① 复现测试在被测 head 上失败（本次运行里重跑仍失败的 AI 补测，
// 测的正是这条问题所在的文件），或 ② Opus 整仓复核判证实。两样都没有 → 待核实（pending_verification），不发作者。
// 复核模型默认 Opus（VERIFY_MODEL；ARGUS_VERIFY_MODEL 覆盖），不再沿用审查模型自己复核自己
export const VERIFY_MODEL = 'anthropic/claude-opus-5-5'
export function reproOnHead(out, finding, sha) {
  const t = (out?.ai_tests?.tests ?? []).find((x) => x.result === 'fails-on-current' && x.reproduced === true && finding?.file && (x.mutation_file === finding.file || x.file === finding.file))
  return t ? { status: 'failed-on-head', test_id: t.id, test_file: t.file, purpose: t.purpose ?? null, tested_sha: sha ?? null, log_tail: t.log_tail ?? null } : null
}
export async function verifyReviewP1s({ entries, out, repo, pr, sha, profile, secrets, health, number, quota, opts = {}, prDiff, handoffRaw, rulingsText, deps = {} }) {
  if (!entries.length) return
  let std
  try { std = loadSeverityStandard() } catch (err) { for (const e of entries) out.pending_verification.push(toPending('review-p1-unconfirmed', e, { reason: `not verified: ${err.message}`, reasonZh: '没核实：统一分级读不到' })); return }
  const reader = deps.reader ?? repoReaderFor(mirrorPath(repo), sha)
  const model = process.env.ARGUS_VERIFY_MODEL ?? VERIFY_MODEL
  const context = { pr: { number, title: pr.title, body: pr.body, merged_at: pr.merged_at, state: pr.state }, intent: profile.intentZh, handoff: handoffRaw, rulingsText }
  const ctxHash = createHash('sha256').update(JSON.stringify({ body: pr.body ?? '', handoff: handoffRaw ?? null, rulingsText })).digest('hex')
  let n = 0
  for (const e of entries) {
    const filePatch = (prDiff?.files ?? []).find((f) => f.filename === e.file)?.patch ?? null
    const key = verifyCacheKey({ sha, finding: e, model, std, contextHash: ctxHash })
    let result = opts.freshReview || process.env.ARGUS_REVIEW_CACHE === '0' ? null : lookupVerification(key)
    if (result) result = { ...result, reused: true }
    else if (n >= VERIFY_MAX_PER_PR) result = { status: 'not-run', status_reason: `more than ${VERIFY_MAX_PER_PR} AI-only P0/P1 in this PR; the rest wait for a human` }
    else if (!quota.check('ai-verify').ok) { result = { status: 'not-run', status_reason: 'ai-verify daily cap reached', capped: true }; out.quota_capped.push(quota.markCapped('ai-verify', { pr: number })) }
    else {
      n++
      result = await verifyReviewFinding({ secrets, finding: e, reader, context: { ...context, filePatch }, std, chat: deps.chat ?? chat, model })
      quota.consume('ai-verify', result.tokens ?? 0)
      if (result.status === 'not-run') health.llm_errors.push({ pr: number, stage: 'review-verify', model, message: result.status_reason })
      else storeVerification(key, result)
    }
    const verification = { status: result.status, method: 'repo-review', verdict: result.verdict ?? null, reason: result.status_reason ?? null, reasoning: result.reasoning ?? null, call_path: result.call_path ?? [], design_reference: result.design_reference ?? null,
      supported_scenario: result.supported_scenario ?? null, steps: result.steps?.length ?? 0, model: result.model ?? null, tokens: result.tokens ?? null, reused: Boolean(result.reused), status_zh: VERIFY_STATUS_ZH[result.status] ?? null }
    const repro = (deps.repro ?? reproOnHead)(out, e, sha)
    if (repro) verification.repro = repro
    verification.opus_status = result.status
    if (e.severity === 'P0') {
      const ok = result.status === 'confirmed' && repro
      if (ok) out.findings.push({ ...e, test_backed: true, verification: { ...verification, method: 'opus+repro-test', confirmed_by: ['opus', 'repro-test'] } })
      else out.pending_verification.push(toPending('review-p0-unconfirmed', { ...e, verification }, { extra: { p0_unconfirmed: true },
        reason: `AI P0 needs both the Opus repo-wide review (${result.status}) and a repro test failing on head (${repro ? 'present' : 'missing'}); kept at P0, not downgraded`,
        reasonZh: `AI 审查报的 P0：需要 Opus 整仓复核证实（${VERIFY_STATUS_ZH[result.status] ?? result.status}）+ 复现测试在 head 上失败（${repro ? '有' : '没有'}），两样齐了才定 P0；不降成 P1，先待核实` }))
      continue
    }
    if (result.status === 'confirmed') out.findings.push({ ...e, verification: { ...verification, confirmed_by: repro ? ['opus', 'repro-test'] : ['opus'] } })
    else if (repro) out.findings.push({ ...e, test_backed: true, verification: { ...verification, status: 'confirmed', method: 'repro-test', confirmed_by: ['repro-test'] } })
    else out.pending_verification.push(toPending('review-p1-unconfirmed', { ...e, verification }, { reason: `AI-only P1: no repro test failing on head and the Opus repo-wide review did not confirm it (${result.status}${result.status_reason ? `: ${result.status_reason}` : ''})`, reasonZh: `AI 审查单独报的 P1：没有在 head 上失败的复现测试，Opus 整仓复核也没能证实（${VERIFY_STATUS_ZH[result.status] ?? result.status}），先不发作者` }))
  }
}

const UNVERIFIED_TO_PENDING = { plausible: 'review-plausible', incomplete: 'review-incomplete', 'unknown-level': 'review-unknown-level' }

// ming-qa 代码审查（白盒）。审查文件来自本地镜像 base..tested 的 diff（已合并：merge-base..merge commit；开放：merge-base..head）。
// 本地 diff 拿不到时才退回 GitHub 的 PR 文件列表（那是 PR head 的 diff），并在 system_health.notes 里说明。
// 统一分级（Praise 2026-10-23）：CONFIRMED 的 P2 进 findings；P0 封顶 P1；P1 先核实（verifyReviewP1s）；PLAUSIBLE / 缺项 / 不认识的等级 /
// 和合并前裁定对得上的 → 待核实。code_review.status 只按最后留在 findings 里的 P0/P1 算（reviewer_status 是审查模型自己的结论）
export async function runCodeReview({ out, repo, pr, files, prDiff, sha, baseSha, results, profile, evDir, secrets, health, number, shaField = 'tested_sha', quota = memoryQuota(), opts = {}, handoffRaw = null, deps = {} }) {
  const reviewFiles = prDiff.files ?? files
  out.reviewed_sha = prDiff.files ? sha : (pr.head?.sha ?? null)
  out.review_base_sha = prDiff.files ? baseSha : (pr.base?.sha ?? null)
  out.review_diff_source = prDiff.files ? `local git diff base_sha..${shaField}` : `github pr files (local diff unavailable: ${prDiff.error})`
  out.pending_verification ??= []
  // 参考资料（数据，不是指令）：PR 正文、交接文件、合并前已裁定的问题
  const prior = await loadPriorRulings({ repo, number, sha: out.reviewed_sha ?? sha, profile, health, author: pr.user?.login ?? null, deps })
  if (prior.summary.used || prior.summary.rejected || prior.summary.error) out.prior_rulings = prior.summary
  const rulingsText = rulingPromptSection(prior.rulings)
  const reference = { handoff: handoffRaw, rulingsText }
  // 同一提交 + diff + 审查提示词 / 版本 + 模型 + 参考资料已经审过 → 复用那次的结论（不调模型、不占额度）；--fresh-review 强制重审
  const ck = (() => { try { return reviewCacheKey({ sha: out.reviewed_sha, baseSha: out.review_base_sha, files: reviewFiles, reference: { body: pr.body, ...reference } }) } catch { return null } })()
  const cached = opts.freshReview || process.env.ARGUS_REVIEW_CACHE === '0' ? null : lookupReviewVerdict(ck)
  // --fresh-review 且已有缓存条目：重审只做对比，不覆盖缓存；结论不同 → 缓存结论有效（code_review.fresh_comparison）
  const priorVerdict = opts.freshReview ? lookupReviewVerdict(ck) : null
  const q = cached ? { ok: true } : quota.check('ai-review')
  if (!q.ok) {
    // 每日 ai-review 额度用尽：不调模型，审查记 capped（显示「额度用尽未补测」）；代码审查不对应 29 个维度里的任何一个
    out.code_review = { status: 'capped', reason: `${CAPPED_ZH}: ai-review daily cap reached (${q.pool} pool ${q.used}/${q.cap})`, display_zh: CAPPED_ZH }
    out.quota_capped.push(quota.markCapped('ai-review', { pr: number }))
    return
  }
  const cr = await (deps.codeReview ?? codeReview)({ secrets, pr, files: reviewFiles, results, profile, outDir: join(evDir, 'code-review'), cacheKey: ck, cached, prior: priorVerdict, verdictOf: reviewVerdict, reference,
    source: { pr: number, run_id: opts.runId ?? null, reviewed_sha: out.reviewed_sha, reviewed_at: new Date().toISOString() } })
  if (cr.status !== 'unavailable' && (!cr.reused || cr.fresh_comparison)) quota.consume('ai-review', cr.tokens ?? 0)
  if (cr.status !== 'ok') {
    if (cr.error?.kind === 'severity-standard') health.qa_tool_failures.push({ pr: number, stage: 'code-review', message: cr.error.message })
    else health.llm_errors.push({ pr: number, stage: 'code-review', model: LLM_MODEL, ...cr.error })
    // ran: false = the review itself did not complete (model/gateway error, or the severity pin failed); status carries the failure kind
    out.code_review = { status: cr.status, ran: false, error: cr.error }
    return
  }
  if (cr.gateError) health.qa_tool_failures.push({ pr: number, stage: 'ming-qa assert-code-review', message: cr.gateError.message })
  const unverified = cr.doc.unverified ?? []
  // status 'failed' here is the review VERDICT (a P0/P1 that stays in findings after verification / rulings, set below), not a
  // run failure; ran: true lets consumers tell "reviewed, found P1" from "review did not run" (QA daily-review misread it on 10-22)
  out.code_review = { status: cr.doc.status, ran: true, reviewer_status: cr.doc.status, summary: cr.doc.summary, gate: cr.gate, model: cr.model, tokens: cr.tokens, findings_count: cr.doc.findings.length, unverified_count: unverified.length, dropped: cr.doc.dropped ?? null,
    severity_standard: cr.doc.severityStandard ?? null, residual_risks: cr.doc.residualRisks, file: cr.reviewPath,
    // 可复现：reused = 这次复用了缓存的结论（reused_from 指向第一次审查）；cache_key 的组成见 docs/report-schema.md › Code review reuse
    reused: Boolean(cr.reused), reused_from: cr.reused_from ?? null, fresh_requested: Boolean(opts.freshReview), fresh_comparison: cr.fresh_comparison ?? null, sampling: cr.sampling ?? null,
    // 模型回复不是单个严格 JSON（多个对象连写 / 代码围栏），取的是最后一个带 findings 数组的对象（mingqa.pickReviewJson）
    ...(cr.json_loose ? { json_loose: true } : {}),
    cache_key: ck ? { key: ck.key.slice(0, 16), reviewed_sha: ck.reviewed_sha, review_base_sha: ck.review_base_sha, diff_hash: ck.diff_hash.slice(0, 16), prompt_hash: ck.prompt_hash.slice(0, 16), prompt_version: ck.prompt_version, model: ck.model, context_hash: ck.context_hash?.slice(0, 16) ?? null } : null,
    verdict: reviewVerdict(cr.doc) }
  const toVerify = []
  for (const f of cr.doc.findings) {
    if (f.verdict === 'REJECTED') continue
    // 2026-10-26（Praise）：AI 报的 P0 不再静默降成 P1：P0 要 Opus 整仓复核证实 + 复现测试在 head 上失败，两样都有才是 P0；
    // 缺一样 → 待核实 review-p0-unconfirmed（severity_if_confirmed 仍是 P0），见 verifyReviewP1s
    const severity = f.severity
    const fp = codeReviewFingerprint(repo, f)
    const entry = {
      source: 'code-review', test_backed: false, check_id: 'code-review', dimension: 'white-box', severity, llm_severity: f.severity, verdict: f.verdict,
      category: f.category, title: f.title, file: f.file, line: f.line, symbol: f.symbol,
      fingerprint: fp, pr_fingerprint: prFingerprint(fp, number),
      evidence: { evidence: f.evidence, trigger: f.trigger ?? null, behavior: f.behavior ?? null, impact: f.impact ?? null, failure_scenario: f.failureScenario, recommendation: f.recommendation, reviewed_sha: out.reviewed_sha },
    }
    // 和合并前盯梢正式裁决的同一问题对得上（同文件、行号差 ≤ 3、裁决之后文件没改、内容相近）→ 不再报
    const ruled = matchPriorRuling(prior.rulings, entry)
    if (ruled) {
      out.pending_verification.push(toPending('review-ruled', entry, { reason: `matches a pre-merge watcher adjudication (mivo-ruling key ${ruled.key}, ${ruled.verdict}) on the same file and lines with similar content (score ${ruled.similarity.score}); code unchanged since`,
        reasonZh: '和合并前盯梢正式裁决的同一问题对得上（不成立 / 属于 P3），位置和内容都相近，裁决之后文件没改',
        extra: { ruling: { kind: ruled.kind, verdict: ruled.verdict, key: ruled.key ?? null, url: ruled.url, by: ruled.by, at: ruled.at, reason: ruled.reason, similarity: ruled.similarity } } }))
      continue
    }
    if (severity === 'P1' || severity === 'P0') toVerify.push(entry)
    else out.findings.push(entry)
  }
  for (const u of unverified) {
    const fp = codeReviewFingerprint(repo, { file: u.file ?? '', symbol: u.symbol ?? '', category: u.category ?? 'unverified', title: u.title })
    const kind = UNVERIFIED_TO_PENDING[u.kind] ?? (u.kind === 'same-as-ruling' ? 'review-ruled' : 'review-unverified')
    out.pending_verification.push(toPending(kind, { source: 'code-review', check_id: 'code-review', dimension: 'white-box', severity: u.severity_if_confirmed ?? null, llm_severity: u.raw_severity ?? u.severity_if_confirmed ?? null, verdict: u.verdict ?? null,
      category: u.category ?? null, title: u.title, file: u.file, line: u.line, symbol: u.symbol ?? null, fingerprint: fp, pr_fingerprint: prFingerprint(fp, number),
      evidence: { evidence: u.evidence ?? null, trigger: u.trigger ?? null, behavior: u.behavior ?? null, impact: u.impact ?? null, recommendation: u.recommendation ?? null, reviewed_sha: out.reviewed_sha, unverified_kind: u.kind, missing: u.missing ?? null } },
      { reason: u.reason ?? null, reasonZh: UNVERIFIED_REASON_ZH[u.kind] ?? null }))
  }
  toVerify.sort((x, y) => (x.severity === 'P0' ? 0 : 1) - (y.severity === 'P0' ? 0 : 1))   // P0 先核（每 PR 有上限）
  await verifyReviewP1s({ entries: toVerify, out, repo, pr, sha: out.reviewed_sha ?? sha, profile, secrets, health, number, quota, opts, prDiff, handoffRaw, rulingsText, deps })
  settleCodeReviewStatus(out)
}

const UNVERIFIED_REASON_ZH = {
  'design-question': '审查认为写明的设计有疑问：不报 P1，进待核实',
  plausible: '审查怀疑但排除不了（PLAUSIBLE），不是等级',
  'same-as-ruling': '审查认为可能和合并前的裁定是同一问题，QA 核对',
}

// 复核 / 裁定改了结论之后，code_review 的结论字段要和最后留在 findings 里的代码审查问题一致：
// status、gate.status / gate.blockingFindings、verdict.status / verdict.max_severity 都按最终的 findings 重算；
// 审查模型自己的结论原样留在 reviewer_status、reviewer_gate、reviewer_verdict
export function settleCodeReviewStatus(out) {
  const cr = out.code_review
  if (!cr || cr.ran !== true) return
  const final = (out.findings ?? []).filter((f) => f.source === 'code-review')
  const blocking = final.filter((f) => ['P0', 'P1'].includes(f.severity))
  cr.status = blocking.length ? 'failed' : 'passed'
  cr.pending_count = (out.pending_verification ?? []).filter((p) => p.source === 'code-review').length
  if (!('reviewer_gate' in cr)) cr.reviewer_gate = cr.gate ? structuredClone(cr.gate) : null
  if (!('reviewer_verdict' in cr)) cr.reviewer_verdict = cr.verdict ? structuredClone(cr.verdict) : null
  const g = cr.reviewer_gate
  if (g) {
    // ming-qa 门禁的 code-review-status-failed 来自审查结论本身：最终没有阻断问题就不再算；其他问题（缺 file / evidence 等）照旧
    const problems = blocking.length ? [...(g.problems ?? [])] : (g.problems ?? []).filter((p) => p !== 'code-review-status-failed')
    cr.gate = { ...g, status: blocking.length || problems.length ? 'failed' : 'passed', blockingFindings: blocking.length, problems }
  }
  if (cr.reviewer_verdict) cr.verdict = { ...cr.reviewer_verdict, status: cr.status, max_severity: final.length ? maxSev(final.map((f) => f.severity)) : null }
}

// 审查结论摘要：状态 + 每条发现的（等级 / 结论 / 文件 / 标题哈希）+ 待核实条目，用来比较两次审查是否一致
export function reviewVerdict(doc) {
  const f = (doc?.findings ?? []).filter((x) => x.verdict !== 'REJECTED').map((x) => ({ severity: x.severity, verdict: x.verdict, file: x.file, title_hash: titleHash(x.title) }))
  const sev = f.map((x) => (x.severity === 'P0' ? 'P1' : x.severity))
  const unverified = (doc?.unverified ?? []).map((x) => ({ kind: x.kind, file: x.file ?? null, title_hash: titleHash(x.title) }))
  return { status: doc?.status ?? null, max_severity: sev.length ? maxSev(sev) : null, findings: f, ...(unverified.length ? { unverified } : {}) }
}

// base..tested 的完整 diff：一份文本，解析出审查用的文件列表（GitHub files 形状）和覆盖率用的新增行
// 交接文件 docs/pr/** 不进代码审查和新代码覆盖率：从 diff 文本和文件列表里去掉
export const stripHandoffDiff = (text) => String(text).split(/(?=^diff --git )/m).filter((ch) => !/^diff --git a\/docs\/pr\//.test(ch)).join('')
// raw：交接文件原文（解析不了也保留），给代码审查和复核当参考资料
async function loadHandoff({ repo, number, sha, health }) {
  try { const raw = await getHandoff(repo, number, sha, { health }); return { ...parseHandoff(raw), raw } } catch (err) {
    health.env_problems.push({ pr: number, message: `handoff read failed: ${String(err.message).slice(0, 200)}` })
    return { present: false, reason: 'handoff read failed', raw: { review: null, qa: null } }
  }
}
async function diffForReview({ repo, sha, baseSha, extraRefspecs }) {
  if (!baseSha) return { text: null, files: null, error: 'base revision unknown' }
  try {
    const mirror = await ensureMirror(repo, sha, { extraRefspecs })
    // run() 只保留输出尾部，这里用同步调用拿全量
    const text = execFileSync('git', ['-C', mirror, 'diff', '--no-color', '--no-ext-diff', '--no-renames', '-U3', baseSha, sha], { encoding: 'utf8', maxBuffer: 256 * 1024 * 1024, timeout: 120_000 })
    const kept = stripHandoffDiff(text)
    return { text: kept, files: splitDiffFiles(kept), error: null }
  } catch (err) { return { text: null, files: null, error: String(err.message).slice(0, 200) } }
}

// `git diff` 文本 → [{ filename, status, additions, deletions, patch }]（与 GitHub PR files 接口同形）
export function splitDiffFiles(text) {
  const files = []
  let cur = null
  for (const line of String(text ?? '').split('\n')) {
    const m = line.match(/^diff --git a\/(.+) b\/(.+)$/)
    if (m) { cur = { filename: m[2], status: 'modified', additions: 0, deletions: 0, patch: null, _hunks: [] }; files.push(cur); continue }
    if (!cur) continue
    if (!cur._hunks.length) {
      if (line.startsWith('new file mode')) cur.status = 'added'
      else if (line.startsWith('deleted file mode')) cur.status = 'removed'
      else if (line.startsWith('+++ /dev/null')) cur.status = 'removed'
      else if (line.startsWith('+++ b/')) cur.filename = line.slice(6)
      if (!line.startsWith('@@')) continue
    }
    cur._hunks.push(line)
    if (line.startsWith('+') && !line.startsWith('+++')) cur.additions++
    else if (line.startsWith('-') && !line.startsWith('---')) cur.deletions++
  }
  for (const f of files) { f.patch = f._hunks.length ? f._hunks.join('\n') : null; delete f._hunks }
  return files
}

// 覆盖率轮次里失败的测试文件（测试报告完整时取失败用例；否则取 failures 里「文件 > …」的文件部分）
const failedTestFiles = (results) => [...new Set((results ?? []).flatMap((r) => [
  ...(r.testOutcomes ?? []).filter((t) => t.status === 'failed').map((t) => t.caseId),
  ...(r.failures ?? []),
].map((x) => String(x).split(' > ')[0].trim()).filter(isTestPath)))]
// 测试文件里相对导入（import … from / import() / require()）指向的仓内路径，去掉扩展名
const IMPORT_RE = /(?:\bfrom\s*|\bimport\s*\(\s*|\bimport\s+|\brequire\s*\(\s*)['"](\.{1,2}\/[^'"]+)['"]/g
const stripExt = (p) => p.replace(/\.[cm]?[jt]sx?$/, '')
function relativeImports(testFile, text) {
  const out = []
  for (const m of String(text ?? '').matchAll(IMPORT_RE)) out.push(stripExt(posix.normalize(posix.join(posix.dirname(testFile), m[1]))))
  return out
}
// 覆盖率轮次里失败的测试导入了 PR 改动的（非测试）文件：这些文件的新代码覆盖率可能偏低（失败的用例没走完）
// readFile(path) → 被测版本里的文件内容或 null
export function coverageMaybeLow({ results, changedFiles, readFile }) {
  const changed = (changedFiles ?? []).filter((f) => !isTestPath(f))
  const hits = []
  for (const tf of failedTestFiles(results)) {
    const imps = relativeImports(tf, readFile(tf))
    const imports = changed.filter((c) => imps.some((i) => stripExt(c) === i || stripExt(c) === `${i}/index`))
    if (imports.length) hits.push({ test_file: tf, imports })
  }
  return { new_code_maybe_low: hits.length > 0, failed_tests_importing_changes: hits }
}

// 覆盖率门禁：取首轮里带 coverage 的步骤（单测），与 base..tested 的 diff 新增行求交
function computeCoverageGate({ first, prDiff, covError = null, changedFiles = [], readFile = () => null }) {
  const main = first.find((x) => x.coverage && x.coverage.kind !== 'v8')
  // node --test 等 V8 步骤的逐文件数据并进主报告（主报告已有的文件不动）
  const v8 = first.filter((x) => x.coverage?.kind === 'v8').map((x) => x.coverage.files)
  const r = main && main.coverage.status === 'ok' && main.coverage.files && v8.length ? { ...main, coverage: { ...main.coverage, files: mergeCoverageFiles(main.coverage.files, ...v8) } } : main
  if (!r) {
    const setup = first.find((x) => x.kind === 'setup' && !x.ok)
    return coverageGate({ coverage: { status: 'unavailable', reason: covError ? `coverage run failed: ${redact(String(covError.message)).slice(0, 200)}` : setup ? 'coverage run: dependency install failed' : 'unit step not planned or coverage disabled' } })
  }
  // 覆盖率轮次里单测本身的结果只作说明（插桩可能让个别用例失败），不影响判定
  const underCoverage = { exit_code: r.exitCode, signal: r.signal ?? null, ended_at: r.endedAt ?? null, timed_out: Boolean(r.timedOut), failed_tests: (r.failures ?? []).length, note: 'unit run under v8 coverage; its pass/fail is informational only and never used for the verdict', ...(r.shards ? { shards: r.shards } : {}), ...(r.reused ? { reused: true, reused_from: r.reusedFrom } : {}), ...coverageMaybeLow({ results: first, changedFiles, readFile }) }
  if (r.coverage.status !== 'ok') return { ...coverageGate({ coverage: r.coverage, stepId: r.id }), unit_run_under_coverage: underCoverage }
  const diffError = prDiff.text == null ? `new-code coverage not evaluated: ${prDiff.error}` : null
  return { ...coverageGate({ coverage: r.coverage, added: diffError ? null : parseAddedLines(prDiff.text), stepId: r.id, diffError }), unit_run_under_coverage: underCoverage }
}

// 三个维度各自是否真的跑了检查（pass / fail 才算跑了；unavailable / timeout 不算）
// skipped（skipped_steps，可选）：一个维度没有任何检查、它的步骤又全是因 triage 判不涉及才跳过的 → 记进
// dimensions_not_applicable，不算缺（#1101：只改测试，黑盒步骤全被 triage 跳过）
export function dimensionsOf(checks, skipped = []) {
  const run = Object.fromEntries(Object.keys(BOXES).map((b) => [b, []]))
  for (const c of checks ?? []) if (['pass', 'fail'].includes(c.status) && c.kind !== 'setup' && run[c.dimension]) run[c.dimension].push(c.id)
  const triagedOut = (b) => {
    if ((checks ?? []).some((c) => c.kind !== 'setup' && c.dimension === b)) return false
    const sk = (skipped ?? []).filter((x) => x.dimension === b)
    return sk.length > 0 && sk.every((x) => String(x.reason ?? '').startsWith('triage:'))
  }
  const empty = Object.keys(run).filter((b) => !run[b].length)
  return { dimensions_run: run, dimensions_missing: empty.filter((b) => !triagedOut(b)), dimensions_not_applicable: empty.filter(triagedOut) }
}

// clean 只能在：没有发现、没有待核实、所有检查都跑成、三个维度都真的跑过时给出。
// 待核实（pending_verification）不让 PR 变 needs-attention / review-suggested，但 QA 还没查清，也不能说 clean
export function verdictOf(pr) {
  if (pr.outcome === 'infra_failed') return 'qa-env-problem'
  if (pr.findings.some((f) => ['P0', 'P1'].includes(f.severity))) return 'needs-attention'
  if (pr.findings.some((f) => f.severity === 'P2')) return 'review-suggested'
  if ((pr.pending_verification ?? []).length) return 'partially-verified'
  if (pr.checks.some((c) => c.status === 'unavailable' || c.status === 'timeout')) return 'partially-verified'
  if ((pr.dimensions_missing ?? []).length) return 'partially-verified'
  // 代码审查因每日额度用尽没做：不能说 clean
  if (pr.code_review?.status === 'capped') return 'partially-verified'
  return 'clean'
}

// ── 系统健康 ──────────────────────────────────────────
// Linux browser for Mivo's journey: the local wrapper (scripts/chromium-linux.sh → /usr/local/bin/chromium) when installed,
// otherwise Debian's launcher. Must match the profile's linux BD2_CHROME.
const LINUX_CHROMIUM = existsSync('/usr/local/bin/chromium') ? '/usr/local/bin/chromium' : '/usr/bin/chromium'

function envSnapshot() {
  const ver = (cmd, args) => { try { return execFileSync(cmd, args, { encoding: 'utf8', timeout: 15_000 }).trim().split('\n')[0] } catch { return null } }
  let free = null
  try { const s = statfsSync(CACHE_DIR); free = Math.round((s.bavail * s.bsize) / 1024 ** 3) } catch { /* ignore */ }
  return {
    host: hostname(), platform: process.platform, node: process.version,
    sandbox: sandboxMode({}), bwrap: ver('bwrap', ['--version']), chromium: ver(LINUX_CHROMIUM, ['--version']), chromium_path: LINUX_CHROMIUM,
    playwright: (() => { try { return JSON.parse(readFileSync(join(CODE_DIR, 'node_modules/playwright/package.json'), 'utf8')).version } catch { return null } })(),
    python: ver(process.env.ARGUS_PYTHON ?? 'python3', ['--version']), disk_free_gb: free,
    umask: currentUmask(), umask_inherited: process.env.PANOPTES_INHERITED_UMASK || INHERITED_UMASK,
  }
}

// 实际 umask 不是 0022 → env_problem（测试夹具权限会变，权限相关断言可能假失败）
export function checkUmask(health) {
  const want = fmtUmask(PINNED_UMASK)
  const u = health.environment?.umask
  if (u !== want) health.env_problems.push({ message: `umask is ${u ?? 'unknown'} (expected ${want}): test fixture permissions will differ, permission checks such as RUNNER_OWNER_MISMATCH may fail falsely` })
}


// ── 合并即测结果的复用 / 输出路径 ─────────────────────────
export const mergedReportsDir = () => process.env.ARGUS_MERGED_REPORTS_DIR ?? join(REPORTS_DIR, 'merged')
export const mergedReportPath = (n, dir = mergedReportsDir()) => join(dir, `PR-${n}.json`)
const REUSABLE_VERDICTS = new Set(['clean', 'review-suggested', 'needs-attention', 'partially-verified', 'docs-only'])

// 只复用「同一个 merge commit、真实跑完」的结果；PR 号对不上、测的不是这个提交、或上次自身出错的都不复用
export function loadMergedResult(number, mergeSha, { mergedDir, selfOut, requireReview = false } = {}) {
  const file = mergedReportPath(number, mergedDir)
  if (selfOut && resolve(selfOut) === resolve(file)) return null
  if (!mergeSha || !existsSync(file)) return null
  let doc
  try { doc = JSON.parse(readFileSync(file, 'utf8')) } catch { return null }
  const pr = (doc.prs ?? []).find((p) => p.number === number)
  if (!pr || pr.state !== 'merged' || !REUSABLE_VERDICTS.has(pr.verdict)) return null
  // 旧的 docs-only 结果没有代码审查：审查开着时重做（文档改动也要审查同一个 merge commit）
  if (requireReview && pr.verdict === 'docs-only' && !pr.code_review) return null
  const tested = pr.verdict === 'docs-only' ? (doc.merged_sha ?? pr.merge_sha ?? null) : pr.tested_sha
  if (!tested || tested !== mergeSha) return null
  return { file, pr, generatedAt: doc.generated_at ?? null, systemHealth: doc.system_health?.status ?? null }
}

// --out：显式输出路径。为了不覆盖每日报告，拒绝写到 REPORTS_DIR/YYYY-MM-DD.json 形式的文件
export function resolveOutPath(out, day) {
  if (!out) return join(REPORTS_DIR, `${day}.json`)
  const abs = resolve(out)
  if (resolve(dirname(abs)) === resolve(REPORTS_DIR) && /^\d{4}-\d{2}-\d{2}\.json$/.test(basename(abs))) throw new Error(`--out 不能指向每日报告文件：${abs}`)
  return abs
}

// ── 报告 ──────────────────────────────────────────────
// 已产出的报告绝不就地改写：再次写同一路径时，旧文件先原样改名为 <名字>.vN.json（N 取下一个空号），再写新的。
// 重新分级 = 重跑；原件保留（例如 PR-902.v1.json）。
// 版本放在报告同目录的 history/ 子目录：reports/merged/PR-902.json 的旧版 → reports/merged/history/PR-902.vN.json。
// N 取下一个空号（history/ 和 2026-10-11 之前平铺在同目录的旧版都算）。previous_version 记相对于当前报告所在目录的路径
// （"history/PR-902.v1.json"），不论引用它的文件本身在哪。读报告的程序只读 PR-<数字>.json。
export const HISTORY_DIR = 'history'
export function nextVersionPath(file) {
  const dir = dirname(file)
  const stem = basename(file).replace(/\.json$/, '')
  let n = 1
  while (existsSync(join(dir, HISTORY_DIR, `${stem}.v${n}.json`)) || existsSync(join(dir, `${stem}.v${n}.json`))) n++
  return join(dir, HISTORY_DIR, `${stem}.v${n}.json`)
}
export const versionRef = (kept) => `${HISTORY_DIR}/${basename(kept)}`
export function preserveExisting(file) {
  if (!existsSync(file)) return null
  const kept = nextVersionPath(file)
  mkdirSync(dirname(kept), { recursive: true })
  renameSync(file, kept)
  return kept
}

// 一次性迁移：把平铺在 dir 里的旧版 <name>.vN.json 挪进 dir/history/，并把指向它们的路径字段改成新位置：
// previous_version（裸文件名 → "history/<name>.vN.json"）和 run.source_report（绝对路径）。只改这两个路径字段，内容不动。
export function migrateHistory(dir) {
  const moved = new Map() // 旧绝对路径 → 新绝对路径
  const conflicts = []
  mkdirSync(join(dir, HISTORY_DIR), { recursive: true })
  for (const name of readdirSync(dir)) {
    if (!/\.v\d+\.json$/.test(name)) continue
    const from = join(dir, name), to = join(dir, HISTORY_DIR, name)
    if (existsSync(to)) { conflicts.push(name); continue }
    renameSync(from, to)
    moved.set(from, to)
  }
  const rewritten = []
  const files = [...readdirSync(dir).filter((n) => /\.json$/.test(n)).map((n) => join(dir, n)), ...readdirSync(join(dir, HISTORY_DIR)).filter((n) => /\.json$/.test(n)).map((n) => join(dir, HISTORY_DIR, n))]
  for (const f of files) {
    let doc
    try { doc = JSON.parse(readFileSync(f, 'utf8')) } catch { continue }
    const changes = []
    const pv = doc.previous_version
    if (typeof pv === 'string' && /\.v\d+\.json$/.test(pv) && !pv.includes('/')) { doc.previous_version = `${HISTORY_DIR}/${pv}`; changes.push({ field: 'previous_version', from: pv, to: doc.previous_version }) }
    const src = doc.run?.source_report
    if (typeof src === 'string' && moved.has(resolve(src))) { doc.run.source_report = moved.get(resolve(src)); changes.push({ field: 'run.source_report', from: src, to: doc.run.source_report }) }
    if (!changes.length) continue
    const tmp = `${f}.tmp-${process.pid}`
    writeFileSync(tmp, JSON.stringify(doc, null, 2) + '\n')
    renameSync(tmp, f)
    rewritten.push({ file: f, changes })
  }
  return { dir, moved: [...moved.entries()].map(([from, to]) => ({ from, to })), conflicts, rewritten }
}

// 规范化（只在重新存报告时做，原件按版本机制保留）：2026-10-11 之前的 flaky 条目没有 base_comparison →
// 补成 not-compared，reason "report predates feature"，文字「未与合并前版本对比（该报告早于此功能生成）」。
export const PREDATES_FEATURE_ZH = `${NOT_COMPARED_ZH}（该报告早于此功能生成）`
export function normalizePreFeature(report) {
  const changes = []
  for (const pr of report?.prs ?? []) {
    for (const field of ['failed_checks', 'findings']) {
      for (const c of pr[field] ?? []) {
        if (c?.status !== 'flaky' || c.base_comparison) continue
        c.base_comparison = { compared: false, base_sha: c.evidence?.base_sha ?? pr.base_sha ?? null, base_run: c.evidence?.base_run ?? 'not-recorded', result: 'not-compared', reason: 'report predates feature', note: `${PREDATES_FEATURE_ZH} (not compared with the base revision: report predates feature)`, note_zh: PREDATES_FEATURE_ZH }
        changes.push({ pr: pr.number, field, check_id: c.check_id, test: c.test ?? null, change: 'base_comparison: not-compared (report predates feature)' })
      }
    }
  }
  if (changes.length) report.normalized = [...(report.normalized ?? []), { at: new Date().toISOString(), by: 'panoptes normalizePreFeature', changes }]
  return changes
}

export function writeReport(report, file) {
  mkdirSync(dirname(file), { recursive: true })
  const kept = preserveExisting(file)
  if (kept && report && typeof report === 'object') report.previous_version = versionRef(kept)
  const tmp = `${file}.tmp-${process.pid}`
  writeFileSync(tmp, JSON.stringify(report, null, 2) + '\n')
  renameSync(tmp, file)
  return file
}

// ── 提 issue（默认关闭；本次部署不启用）─────────────────
// 只有 --create-issues 且环境变量 ARGUS_ALLOW_ISSUES=1 同时满足才会执行。
// 目标固定为私有仓 PraiseZhu/pr-qa-agent-deploy（绝不往被测仓 xindong/mivo-canvas-plugin 提），
// labels: qa-auto + cindy-dispatch；按 fingerprint 去重（含已关闭的 issue）；复发处理见 recurrenceDecision。
// 正文带 `<!-- mivo-qa-fingerprint: … -->` 标记和一个 ```json mivo-qa-finding``` 证据块，供下游 lifeline/doctor.mjs 解析。
export const ISSUE_REPO = 'PraiseZhu/pr-qa-agent-deploy'
export const ISSUE_LABELS = ['qa-auto', 'cindy-dispatch']
export const fingerprintMarker = (fp) => `<!-- mivo-qa-fingerprint: ${fp} -->`
export const observationMarker = (id) => `<!-- mivo-qa-observation: ${id} -->`

export function buildIssuePayload(report, pr, f) {
  const finding = {
    schema: 'mivo-qa-finding/v1', fingerprint: f.fingerprint, pr_fingerprint: f.pr_fingerprint,
    merge_sha: pr.merge_sha ?? null,
    report_date: report.date, report_file: `reports/${report.date}.json`, run_id: report.run?.id,
    repo: report.repo, pr: { number: pr.number, title: pr.title, author: pr.author, url: pr.url, state: pr.state, tested_sha: pr.tested_sha, base_sha: pr.base_sha },
    source: f.source, check_id: f.check_id, check_name: f.check_name ?? null, dimension: f.dimension, qa_dimensions: f.qa_dimensions ?? [],
    severity: f.severity, status: f.status ?? null, attribution: f.attribution ?? null, test: f.test ?? null, failure_type: f.failure_type ?? null,
    file: f.file ?? null, line: f.line ?? null, symbol: f.symbol ?? null, category: f.category ?? null, verdict: f.verdict ?? null,
    evidence: { ...(f.evidence ?? {}), log_tail: undefined }, evidence_dir: pr.evidence_dir,
  }
  const what = f.source === 'code-review' ? `code review: ${f.title}` : `${f.check_name ?? f.check_id}${f.test ? ` › ${f.test}` : ''}`
  const title = `[Mivo QA][${f.severity}] ${report.repo}#${pr.number} ${what}`.slice(0, 240)
  const body = [
    `Mivo QA nightly (${report.date}, Asia/Shanghai) found a **${f.severity}** problem in ${report.repo}#${pr.number} — ${pr.title} (@${pr.author}).`,
    '',
    `- PR: ${pr.url} (${pr.state}), tested ${pr.tested_sha ?? '?'} vs base ${pr.base_sha ?? '?'}`,
    `- Check: ${f.check_name ?? f.check_id} (${f.dimension})`,
    f.test ? `- Test: \`${f.test}\`` : null,
    f.status ? `- Status: ${f.status} — ${f.attribution}` : null,
    f.file ? `- Location: \`${f.file}${f.line ? `:${f.line}` : ''}\`${f.symbol ? ` (${f.symbol})` : ''}` : null,
    f.evidence?.rerun ? `- Runs: first=fail, rerun=${f.evidence.rerun}, base=${f.evidence.base_run}${f.evidence.latest_main_run ? `, latest main=${f.evidence.latest_main_run}` : ''}` : null,
    f.evidence?.evidence ? `\n**Evidence**\n\n${f.evidence.evidence}` : null,
    f.evidence?.failure_scenario ? `\n**Failure scenario**\n\n${f.evidence.failure_scenario}` : null,
    f.evidence?.recommendation ? `\n**Recommendation**\n\n${f.evidence.recommendation}` : null,
    f.evidence?.log_tail ? `\n<details><summary>Log tail</summary>\n\n\`\`\`\n${String(f.evidence.log_tail).slice(-3000)}\n\`\`\`\n</details>` : null,
    '',
    '```json mivo-qa-finding',
    JSON.stringify(finding, null, 2),
    '```',
    '',
    fingerprintMarker(f.fingerprint),
  ].filter((x) => x !== null).join('\n')
  return { title, body: redact(body), labels: ISSUE_LABELS }
}

// 哪些东西可以变成 issue（纯函数，便于本地核对）：
//  ① PR finding：必须有测试证据（source === 'check'，即一个失败的检查），P0/P1，且 PR 作者在白名单（PraiseZhu）。
//     只来自 LLM 代码审查（source === 'code-review'）的 finding 无论等级都不提 issue，只留在报告里。
//  ② system_health：QA 自身的故障（qa_tool_failures / env_problems / llm_errors）照常提，不受作者限制。
export const ISSUE_AUTHORS = ['PraiseZhu']
const HEALTH_KINDS = { qa_tool_failures: 'QA tool failure', env_problems: 'environment problem', llm_errors: 'LLM error' }

export function selectIssueCandidates(report, { authors = ISSUE_AUTHORS } = {}) {
  const out = []
  const skipped = []
  for (const pr of report.prs ?? []) {
    for (const f of (pr.findings ?? []).filter((x) => ['P0', 'P1'].includes(x.severity))) {
      // 规则按 test_backed（有失败检查佐证）判断，不只看严重度；旧报告没有该字段时按 source === 'check'
      const why = !isTestBacked(f) ? `not test-backed (source ${f.source ?? 'unknown'}; e.g. llm-review-only)`
        : !authors.includes(pr.author) ? `author ${pr.author} not in ${authors.join(',')}`
          : null
      if (why) skipped.push({ pr: pr.number, fingerprint: f.fingerprint, severity: f.severity, source: f.source, reason: why })
      else out.push({ kind: 'finding', pr, finding: f, fingerprint: f.fingerprint })
    }
  }
  const hc = healthIssueCandidate(report)
  if (hc) out.push(hc)
  return { candidates: out, skipped }
}

// system_health → 至多一个 issue（每次运行一个，带清单）。规则：
//  · 只收本次运行自修复（bootstrap 重试）之后仍然存在的问题：transient / self_repaired 的条目只留在报告里；
//  · 指纹固定（repo + 'system-health'），所以已有开放的 system_health issue 时只评论，不再新开；
//  · 每个条目自己的指纹（entry_fingerprint）放在清单里，便于对照。
export const HEALTH_ISSUE_KEY = ['system-health', 'aggregate-v1']
export const healthIssueFingerprint = (repo) => fingerprintOf(repo, HEALTH_ISSUE_KEY)
export function persistentHealthEntries(report) {
  const h = report.system_health ?? {}
  const items = []
  for (const [key, label] of Object.entries(HEALTH_KINDS)) {
    for (const e of h[key] ?? []) {
      if (e?.transient === true || e?.self_repaired === true) continue
      items.push({ category: key, label, entry: e, entry_fingerprint: fingerprintOf(report.repo, ['system-health', key, e.stage ?? '', e.kind ?? '', e.message ?? '']) })
    }
  }
  return items
}
export function healthIssueCandidate(report) {
  const items = persistentHealthEntries(report)
  if (!items.length) return null
  return { kind: 'health', category: 'system_health', label: 'system_health', items, entry: items[0].entry, fingerprint: healthIssueFingerprint(report.repo) }
}

const healthLine = (it) => `- **${it.label}**${it.entry.pr ? ` (PR #${it.entry.pr})` : ''}${it.entry.stage ? ` [${it.entry.stage}]` : ''}: ${String(it.entry.message ?? '').slice(0, 300)}`
export function buildHealthIssuePayload(report, c) {
  const h = report.system_health ?? {}
  const repaired = h.bootstrap?.self_repaired ?? []
  const block = { schema: 'mivo-qa-health/v2', fingerprint: c.fingerprint, report_date: report.date, report_file: `reports/${report.date}.json`, run_id: report.run?.id, repo: report.repo,
    items: c.items.map((it) => ({ category: it.category, entry_fingerprint: it.entry_fingerprint, entry: it.entry })), self_repaired: repaired, system_health_status: h.status, environment: h.environment }
  const details = c.items.filter((it) => it.entry.detail).map((it) => `\n<details><summary>${it.entry.stage ?? it.label}</summary>\n\n\`\`\`\n${String(it.entry.detail).slice(-2000)}\n\`\`\`\n</details>`)
  const body = [
    `Mivo QA (${report.date}, Asia/Shanghai, run ${report.run?.id ?? '?'}) found ${c.items.length} QA system problem${c.items.length === 1 ? '' : 's'} that persisted after the run's self-repair attempt:`,
    '',
    ...c.items.map(healthLine),
    repaired.length ? `\nSelf-repaired during this run (not part of this issue): ${repaired.join(', ')}` : null,
    ...details,
    '',
    '```json mivo-qa-health',
    JSON.stringify(block, null, 2),
    '```',
    '',
    fingerprintMarker(c.fingerprint),
  ].filter((x) => x !== null).join('\n')
  const first = c.items[0]
  const head = `${first.label}: ${String(first.entry.stage ?? first.entry.message ?? '').slice(0, 80)}`
  return { title: `[Mivo QA][system_health] ${c.items.length} problem${c.items.length === 1 ? '' : 's'} (${head}${c.items.length > 1 ? ', …' : ''})`.slice(0, 240), body: redact(body), labels: ISSUE_LABELS }
}

export function buildIssuePayloadFor(report, c) {
  return c.kind === 'health' ? buildHealthIssuePayload(report, c) : buildIssuePayload(report, c.pr, c.finding)
}

// 复发处理（沿用 argus publish.mjs 的做法），去重只按指纹：
//   同指纹有开放 issue → 在它下面评论新证据（每次运行 × PR 只评论一次，按观察标记回查）；
//   已关闭且是「不修」（state_reason not_planned，或带 wontfix / won't fix 标签）→ 保持沉默；
//   已关闭（修好了）又出现 → 重开并评论；
//   没有 → 新开。
const WONTFIX_LABELS = new Set(['wontfix', "won't fix", 'wont-fix', 'won\'t-fix'])
const labelNames = (i) => (i.labels ?? []).map((l) => String(typeof l === 'string' ? l : l?.name ?? '').toLowerCase())
export const isWontFix = (i) => i.state === 'closed' && (i.state_reason === 'not_planned' || labelNames(i).some((l) => WONTFIX_LABELS.has(l)))
export const HEALTH_BODY_MARK = '```json mivo-qa-health'
export const isHealthIssue = (i) => String(i?.body ?? '').includes(HEALTH_BODY_MARK) || String(i?.title ?? '').startsWith('[Mivo QA][system_health]')
export function recurrenceDecision(issues, c) {
  const fpMark = fingerprintMarker(c.fingerprint)
  const mine = (issues ?? []).filter((i) => String(i.body ?? '').includes(fpMark)).sort((a, b) => b.number - a.number)
  const open = mine.find((i) => i.state === 'open')
  if (open) return { action: 'comment', issue: open }
  // system_health：任何仍开着的 system_health issue（含旧版逐条 issue）都算同一件事 → 评论，不新开
  if (c.kind === 'health') {
    const anyOpen = (issues ?? []).filter((i) => i.state === 'open' && isHealthIssue(i)).sort((a, b) => b.number - a.number)[0]
    if (anyOpen) return { action: 'comment', issue: anyOpen }
  }
  const latest = mine[0]
  if (!latest) return { action: 'create' }
  if (isWontFix(latest)) return { action: 'suppressed', issue: latest, reason: latest.state_reason === 'not_planned' ? 'closed as not planned' : 'closed with a wontfix label' }
  return { action: 'reopen', issue: latest }
}

// 新证据评论：哪天、哪个 PR、测的哪个提交、状态与归因、日志尾巴
export function buildEvidenceComment(report, c, { reopened = false } = {}) {
  const obsId = `${report.run?.id ?? report.date}/${c.kind === 'finding' ? `pr${c.pr.number}` : c.category}`
  const lines = c.kind === 'finding'
    ? [
        reopened ? `**Recurred** after this issue was closed — reopened by Mivo QA (${report.date}, Asia/Shanghai).` : `Observed again by Mivo QA (${report.date}, Asia/Shanghai).`,
        '',
        `- PR: ${c.pr.url ?? `${report.repo}#${c.pr.number}`} (${c.pr.state}) — ${c.pr.title ?? ''}`,
        `- Tested ${c.pr.tested_sha ?? '?'} vs base ${c.pr.base_sha ?? '?'}`,
        `- ${c.finding.check_name ?? c.finding.check_id}${c.finding.test ? ` › \`${c.finding.test}\`` : ''}: **${c.finding.severity}**${c.finding.status ? `, ${c.finding.status} — ${c.finding.attribution ?? ''}` : ''}`,
        c.finding.evidence?.rerun ? `- Runs: first=fail, rerun=${c.finding.evidence.rerun}, base=${c.finding.evidence.base_run}${c.finding.evidence.latest_main_run ? `, latest main=${c.finding.evidence.latest_main_run}` : ''}` : null,
        c.finding.evidence?.log_tail ? `\n<details><summary>Log tail</summary>\n\n\`\`\`\n${String(c.finding.evidence.log_tail).slice(-2000)}\n\`\`\`\n</details>` : null,
      ]
    : [
        reopened ? `**Recurred** after this issue was closed — reopened by Mivo QA (${report.date}).` : `Observed again by Mivo QA (${report.date}).`,
        '',
        `Run ${report.run?.id ?? '?'}: ${c.items.length} problem${c.items.length === 1 ? '' : 's'} still present after self-repair:`,
        ...c.items.map(healthLine),
      ]
  return { id: obsId, body: redact([...lines.filter((x) => x !== null), '', fingerprintMarker(c.fingerprint), observationMarker(obsId)].join('\n')) }
}

export async function createIssues(report, { repo = ISSUE_REPO } = {}) {
  const { candidates, skipped } = selectIssueCandidates(report)
  const results = skipped.map((x) => ({ ...x, action: 'not-eligible' }))
  for (const c of candidates) {
    const found = await findIssuesByMarkerLabel(repo, ISSUE_LABELS[0], c.kind === 'health' ? HEALTH_BODY_MARK : fingerprintMarker(c.fingerprint))
    const d = recurrenceDecision(found, c)
    const base = { kind: c.kind, pr: c.pr?.number ?? null, fingerprint: c.fingerprint }
    if (d.action === 'create') {
      const issue = await createIssue(repo, buildIssuePayloadFor(report, c))
      results.push({ ...base, action: 'created', issue: issue?.html_url })
      continue
    }
    if (d.action === 'suppressed') { results.push({ ...base, action: 'suppressed', reason: d.reason, issue: d.issue.html_url }); continue }
    if (d.action === 'reopen') await reopenIssue(repo, d.issue.number)
    const comment = buildEvidenceComment(report, c, { reopened: d.action === 'reopen' })
    // 同一次运行 × 同一 PR 只评论一次（重跑 / 补发时按观察标记回查）
    const already = await findCommentByMarker(repo, d.issue.number, observationMarker(comment.id))
    if (!already) await commentIssue(repo, d.issue.number, comment.body)
    results.push({ ...base, action: d.action === 'reopen' ? 'reopened' : 'commented', issue: d.issue.html_url, ...(already ? { note: 'evidence comment already present' } : {}) })
  }
  return results
}

// 发现阶段就拿得到的 PR 基本信息：没测的 / 出错的条目也带 merged_at 等字段（日报按 merged_at 换算上海时区筛选）
export function prMeta(pr) {
  if (!pr) return {}
  return { title: pr.title ?? null, author: pr.user?.login ?? null, url: pr.html_url ?? null, state: pr.merged_at ? 'merged' : (pr.state ?? null), merged_at: pr.merged_at ?? null, merge_sha: pr.merge_commit_sha ?? null }
}

// test_backed：只有失败检查（source: check）佐证的发现才是 true；LLM 审查、覆盖率门禁、QA 自身出错都是 false。
// 下游通知 / 提 issue 规则以它为准，而不是只看严重度。
// coverage-gate / qa-error / ai-test / own-tests 只出现在旧报告里：统一分级之后它们进 pending_verification（或不出 finding）
export const FINDING_SOURCES = ['check', 'code-review', 'stated-acceptance', 'coverage-gate', 'qa-error', 'ai-test', 'own-tests']
// 只有真复现了的失败才算测试佐证：introduced（本 PR 引入）、pre-existing / fixed-later（真失败，只是不归这个 PR）。
// unverified（没核实）、flaky（重跑通过）、timeout、setup-failed 都不算
export const TEST_BACKED_STATUSES = ['introduced', 'pre-existing', 'fixed-later']
// （很早的旧报告里失败检查没有 status：沿用当时的口径，算测试佐证）
export const checkIsTestBacked = (c) => c?.flaky !== true && (c?.status == null || TEST_BACKED_STATUSES.includes(c.status))
// 失败检查（source check）每次按状态重算，不信存下来的布尔值：10-26 实测 6 条 status=unverified 的检查发现带着 test_backed=true
// （先按初始状态标 true，之后状态改成 unverified 没回写）。其他来源沿用存下来的值（没有就是 false）。
export const isTestBacked = (f) => ((f?.source ?? 'check') === 'check' ? checkIsTestBacked(f) && f?.test_backed !== false : f?.test_backed === true)
export function annotateTestBacked(prs) {
  for (const pr of prs) {
    pr.findings = (pr.findings ?? []).map((f) => { const g = { ...f, source: f.source ?? 'check' }; return { ...g, test_backed: isTestBacked(g) } })
    const backed = pr.findings.filter((f) => f.test_backed)
    pr.test_backed = backed.length > 0
    pr.test_backed_severity = backed.length ? maxSev(backed.map((f) => f.severity)) : null
    pr.finding_sources = [...new Set(pr.findings.map((f) => f.source))]
    if (!('merged_at' in pr)) pr.merged_at = null
    pr.merged_at_shanghai = pr.merged_at ? toShanghaiIso(pr.merged_at) : null
  }
  return prs
}

// 发现了但这次没测的 PR：仍进 prs[]，verdict=not-tested 并写明原因。
// severity 固定为 "untested"（不是 P 级，绝不显示成 P3）；tested: false。超时 / 截止时间另记 qa_tool_failures。
export function notTestedEntry(t, code, reason) {
  return { number: t.number, ...(t.meta ?? {}), discovery_reason: t.reason, verdict: 'not-tested', severity: 'untested', tested: false, not_tested_code: code, not_tested_reason: reason, tested_at: null, findings: [], pending_verification: [], failed_checks: [], checks: [], coverage_gate: null }
}

// --deadline：HH:MM（上海时间，指开始之后最近的那个时刻）或完整 ISO 时间
export function parseDeadline(v, now = new Date()) {
  const m = String(v).match(/^(\d{1,2}):(\d{2})$/)
  if (m) {
    const day = toShanghaiDay(now)
    let t = Date.parse(`${day}T${m[1].padStart(2, '0')}:${m[2]}:00+08:00`)
    if (t <= now.getTime()) t += 24 * 3600_000
    return t
  }
  const t = Date.parse(v)
  if (!Number.isFinite(t)) throw new Error(`--deadline 需要 HH:MM 或 ISO 时间：${v}`)
  return t
}

export const BURST_CUTOFF_DEFAULT = '08:30'
export function burstReason(ms, when) {
  return `burst cutoff ${toShanghaiIso(new Date(ms)).slice(11, 16)} Asia/Shanghai reached ${when}: not tested before the daily report; the catch-up timer (every 2 h) tests it later`
}

// 发现结果 → 本次要测的目标 + 发现了但不测的 PR（not-tested，带原因）
export function planTargets(discovered, opts = {}) {
  let targets = discovered
  if (opts.prs?.length) {
    const byNum = Object.fromEntries(discovered.map((t) => [t.number, t]))
    targets = opts.prs.map((n) => ({ number: n, reason: byNum[n]?.reason ?? 'manual (--pr)', meta: byNum[n]?.meta }))
  }
  const notTested = []
  // --pr 运行（单 PR / 指定 PR）只报告被点名的 PR：当天发现的其他 PR 不再以 not-tested（excluded-by-pr-filter）进 prs[]，
  // 只记在 system_health.discovery.excluded_by_pr_filter（2026-10-11 之前的 --pr 报告里还有这些条目）
  const excluded = opts.prs?.length ? discovered.filter((t) => !opts.prs.includes(t.number)).map((t) => t.number) : []
  if (opts.limit && targets.length > opts.limit) {
    for (const t of targets.slice(opts.limit)) notTested.push(notTestedEntry(t, 'over-limit', `beyond --limit ${opts.limit}`))
    targets = targets.slice(0, opts.limit)
  }
  return { targets, notTested, excluded }
}

// --pr 且没给 --date 时报告的日期：PR 的合并时间换算成上海日期（开放 / 关闭未合并的 PR 用 updated_at）。
// 多个 PR 时取最晚的那天。返回 { day, source, prs: [{ number, day, basis }] }；拿不到时间时 day 为 null。
export function prReportDay(pulls) {
  const per = (pulls ?? []).filter(Boolean).map((p) => {
    const basis = p.merged_at ? 'merged_at' : 'updated_at'
    const t = Date.parse(p[basis] ?? '')
    return { number: p.number, day: Number.isFinite(t) ? toShanghaiDay(new Date(t)) : null, basis }
  })
  const days = per.map((x) => x.day).filter(Boolean).sort()
  if (!days.length) return { day: null, source: null, prs: per }
  const bases = [...new Set(per.filter((x) => x.day).map((x) => x.basis))]
  return { day: days.at(-1), source: `pr ${bases.join(' / ')} (Asia/Shanghai)`, prs: per }
}

// 逐个 PR 执行：每个 PR 有心跳（开始/结束/耗时）；qa-error 记 P2 并登记 qa_tool_failures；
// 超出单 PR 时间预算或整晚截止时间 → not-tested（部分结果放 partial_result）
export async function processTargets(targets, { opts = {}, health, startedAt = new Date(), processOne, now = () => Date.now(), repo = null }) {
  const prTimeoutMs = opts.prTimeoutMin ? opts.prTimeoutMin * 60_000 : null
  const deadlineMs = opts.deadline ? parseDeadline(opts.deadline, startedAt) : null
  // --burst-cutoff：和 --deadline 一样截止，但 not_tested_code 是 burst-cutoff（之后由补测定时任务接着测）
  const burst = opts.deadlineKind === 'burst-cutoff'
  const cutCode = burst ? 'burst-cutoff' : 'deadline'
  const heartbeat = { run_started_at: toShanghaiIso(startedAt), run_finished_at: null, duration_s: null, last_beat_at: toShanghaiIso(startedAt), pr_timeout_min: opts.prTimeoutMin ?? null, deadline: deadlineMs ? new Date(deadlineMs).toISOString() : null, prs: [] }
  health.heartbeat = heartbeat
  const prs = []
  for (const t of targets) {
    if (deadlineMs && now() >= deadlineMs) {
      const e = notTestedEntry(t, cutCode, burst ? burstReason(deadlineMs, 'before this PR started') : `run deadline ${new Date(deadlineMs).toISOString()} reached before this PR started`)
      prs.push(e)
      health.qa_tool_failures.push({ pr: t.number, stage: cutCode, message: e.not_tested_reason })
      continue
    }
    if (opts.yieldToPriority !== false) {
      const y = await (opts.yieldFn ?? yieldToPriority)({ onYield: () => process.stderr.write(`[panoptes] parked before PR #${t.number}: a fresh merge has the QA slot\n`) })
      if (y.yielded) (health.notes ??= []).push(`parked ${Math.round(y.waitedMs / 1000)}s before #${t.number} for a fresh-merge run`)
    }
    log('info', 'nightly pr start', { pr: t.number })
    process.stderr.write(`[panoptes] PR #${t.number} (${t.reason}) …\n`)
    const beat = { number: t.number, started_at: toShanghaiIso(new Date(now())), finished_at: null, duration_s: null, verdict: null }
    heartbeat.prs.push(beat)
    const t0 = now()
    const byTimeout = prTimeoutMs != null && (deadlineMs == null || t0 + prTimeoutMs <= deadlineMs)
    const budget = Math.min(prTimeoutMs ? t0 + prTimeoutMs : Infinity, deadlineMs ?? Infinity)
    setBudget(Number.isFinite(budget) ? budget : null)
    let result = null
    try {
      result = await processOne(t)
    } catch (err) {
      if (!overBudget()) {
        health.qa_tool_failures.push({ pr: t.number, stage: 'pipeline', message: redact(err.message).slice(0, 800) })
        const msg = redact(err.message).slice(0, 800)
        const fp = fingerprintOf(repo ?? '', ['qa-error', 'pipeline', msg])
        // 测试系统自己出错：PR 本身没测到，不是作者的问题 → 不定级（untested），待核实（QA 自查）
        result = { number: t.number, ...(t.meta ?? {}), discovery_reason: t.reason, verdict: 'qa-error', severity: 'untested', tested: false, error: msg, failed_checks: [], checks: [], coverage_gate: null, test_started_at: beat.started_at, tested_at: null,
          findings: [], pending_verification: [toPending('qa-error', { source: 'qa-error', check_id: 'pipeline', dimension: null, severity: null, title: 'QA pipeline error (the PR itself was not verified)', fingerprint: fp, pr_fingerprint: prFingerprint(fp, t.number), evidence: { error: msg } }, { reason: 'QA pipeline error', reasonZh: '测试系统自己出错，PR 本身没测到' })] }
      }
    }
    if (overBudget()) {
      const code = byTimeout ? 'pr-timeout' : cutCode
      const why = byTimeout ? `per-PR time budget of ${opts.prTimeoutMin} min exceeded` : burst ? burstReason(deadlineMs, 'while this PR was being tested') : `run deadline ${new Date(deadlineMs).toISOString()} reached`
      const e = notTestedEntry(t, code, why)
      if (result) e.partial_result = result.partial_result ?? { verdict: result.verdict, checks: result.checks, evidence_dir: result.evidence_dir ?? null }
      health.qa_tool_failures.push({ pr: t.number, stage: code, message: why })
      result = e
    }
    setBudget(null)
    prs.push(result)
    beat.finished_at = toShanghaiIso(new Date(now()))
    beat.duration_s = Math.round((now() - t0) / 1000)
    beat.verdict = result.verdict
    heartbeat.last_beat_at = beat.finished_at
    process.stderr.write(`[panoptes] PR #${t.number} done: ${result.verdict} ${result.severity ?? ''} (${beat.duration_s}s)\n`)
  }
  return { prs, heartbeat }
}

// status / complete / all_discovered_tested / 心跳收尾
export function finalizeHealth(health, prs, startedAt = new Date(), now = () => Date.now()) {
  health.status = health.qa_tool_failures.some((f) => ['discovery', 'pipeline'].includes(f.stage)) || prs.some((p) => p.verdict === 'qa-error') ? 'failed'
    : (health.qa_tool_failures.length || health.env_problems.length || health.llm_errors.length || health.quota?.capped?.length) ? 'degraded' : 'ok'
  // 复用合并即测结果的 PR：结论来自那次运行，它的 system_health 不是 ok 时本次至少记 degraded（否则单 PR 副本与原报告不一致）
  if (health.status === 'ok' && prs.some((p) => p.reused && p.reused_report_system_health && p.reused_report_system_health !== 'ok')) {
    health.status = 'degraded'
    health.notes ??= []
    health.notes.push(`reused on-merge results came from runs whose system_health was not ok: ${prs.filter((p) => p.reused && p.reused_report_system_health && p.reused_report_system_health !== 'ok').map((p) => `#${p.number} (${p.reused_report_system_health})`).join(', ')}`)
  }
  // complete：发现成功，且本次要测的每个 PR 都有最终结论（没有 qa-error，没有因超时 / 截止时间而没测）。
  // all_discovered_tested：当天发现的每个 PR 都有最终结论（--pr / --limit 排除的也算没测）。
  const unfinished = (p) => p.verdict === 'qa-error' || (p.verdict === 'not-tested' && ['pr-timeout', 'deadline'].includes(p.not_tested_code))
  const discoveryOk = !health.qa_tool_failures.some((f) => f.stage === 'discovery')
  health.complete = discoveryOk && !prs.some(unfinished)
  health.all_discovered_tested = discoveryOk && !prs.some((p) => unfinished(p) || p.verdict === 'not-tested')
  const hb = health.heartbeat ??= { run_started_at: toShanghaiIso(startedAt), prs: [] }
  hb.run_finished_at = toShanghaiIso(new Date(now()))
  hb.duration_s = Math.round((now() - startedAt.getTime()) / 1000)
  hb.last_beat_at = hb.run_finished_at
  return health
}

// ── 入口 ──────────────────────────────────────────────
export async function runNightly(opts = {}) {
  for (const d of ALL_DIRS) mkdirSync(d, { recursive: true })
  const startedAt = new Date()
  const repo = opts.repo ?? 'xindong/mivo-canvas-plugin'
  const health = { status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], environment: envSnapshot(), discovery: null, notes: [] }
  // 日期：--date 优先；--pr 没给 --date → PR 合并时间（开放 PR：updated_at）的上海日期；否则上海昨天
  let day = opts.date ?? null
  let dateSource = day ? '--date' : null
  if (!day && opts.prs?.length) {
    try {
      const d = prReportDay(await Promise.all(opts.prs.map((n) => (opts.getPull ?? getPull)(repo, n))))
      if (d.day) { day = d.day; dateSource = d.source }
    } catch (err) { health.notes.push(`report date: could not read the PR's merge time (${redact(err.message).slice(0, 200)}); fell back to yesterday (Asia/Shanghai)`) }
  }
  if (!day) { day = previousShanghaiDay(startedAt); dateSource = 'yesterday (Asia/Shanghai)' }
  shanghaiDayWindow(day)
  const outFile = resolveOutPath(opts.out, day)
  if (opts.deadline) parseDeadline(opts.deadline, startedAt) // 参数错误在任何工作开始前报出
  const reg = loadRegistry()
  const project = reg.projects.find((p) => p.repo === repo)
  if (!project?.profileData) throw new Error(`${repo} 没有在 registry 里配置 profile`)
  let secrets = {}
  try { secrets = loadSecrets() } catch (err) { health.env_problems.push({ message: `secrets file unreadable: ${err.message}` }) }
  for (const k of ['ARGUS_LLM_BASE_URL', 'ARGUS_LLM_API_KEY']) if (!secrets[k]) health.env_problems.push({ message: `missing secret ${k === 'ARGUS_LLM_BASE_URL' ? 'QA_LLM_BASE_URL' : 'QA_LLM_API_KEY'}` })
  if (!secrets.ARGUS_TYPESAFE_API_KEY) health.notes.push(TRIAGE_NOTE_NOT_CONFIGURED, 'Jev triage not configured (no ARGUS_TYPESAFE_API_KEY): every optional check runs for every PR')
  // 补测放弃的 PR（最近 48 小时）进日报 system_health；补测自己起的子运行不重复带
  if (process.env.PANOPTES_CATCH_UP_CHILD !== '1') { try { health.qa_tool_failures.push(...recentGiveUps()) } catch { /* 没有状态文件 */ } }
  if (!mingQaAvailable()) health.env_problems.push({ message: 'ming-qa qa_agent.py not found; code-review gate unavailable' })
  const env = health.environment
  if (env.sandbox !== 'bwrap') health.env_problems.push({ message: `sandbox mode is '${env.sandbox}' (expected bwrap)` })
  if (!env.chromium) health.env_problems.push({ message: `${LINUX_CHROMIUM} missing: browser E2E will be unavailable` })
  if (env.disk_free_gb != null && env.disk_free_gb < 12) health.env_problems.push({ message: `low disk: ${env.disk_free_gb} GB free` })
  checkUmask(health)
  recordBootstrap(health, readBootstrapStatus())
  recordSeverityPin(health)
  const db = openDb()
  // 模型额度：manual（手动 / 演练 / 验收重跑）默认走 test 池，event / catch-up 走 production 池；--quota-pool 显式指定
  const quota = createQuota({ db, pool: resolvePool({ quotaPool: opts.quotaPool, trigger: normalizeTrigger(opts.trigger) }) })
  const runId = randomUUID()
  const evidenceRoot = join(EVIDENCE_DIR, 'nightly', day, runId.slice(0, 8))
  mkdirSync(evidenceRoot, { recursive: true })
  await janitor().catch((err) => health.qa_tool_failures.push({ stage: 'janitor', message: err.message }))

  // 发现
  let targets = []
  try {
    const disc = await listNightlyPrs(repo, day)
    health.discovery = { window_start: disc.window.startIso, window_end: disc.window.endIso, found: disc.prs.map((x) => ({ number: x.pr.number, reason: x.reason })) }
    targets = disc.prs.map((x) => ({ number: x.pr.number, reason: x.reason, meta: prMeta(x.pr) }))
  } catch (err) {
    health.qa_tool_failures.push({ stage: 'discovery', message: redact(err.message).slice(0, 500) })
  }
  const plan = planTargets(targets, opts)
  if (opts.prs?.length && health.discovery) health.discovery.excluded_by_pr_filter = plan.excluded
  const { prs } = await processTargets(plan.targets, {
    opts, health, startedAt, repo,
    processOne: (t) => processPr({ repo, project, number: t.number, discovery: t.reason, secrets, db, health, evidenceRoot, opts: { ...opts, runId }, quota }),
  })
  recordQuota(health, quota.summary())
  prs.push(...plan.notTested)
  annotateTestBacked(prs)
  if (health.triage?.failed?.length) health.notes.push(`${TRIAGE_NOTE_FAILED}：${health.triage.failed.map((f) => `#${f.pr} ${f.reason}`).join('；')}`)
  if (opts.catchUp) health.catch_up = opts.catchUp

  const allFindings = prs.flatMap((p) => p.findings ?? [])
  recheckBootstrap(health)
  finalizeHealth(health, prs, startedAt)
  const report = {
    schema_version: SCHEMA_VERSION,
    tool: 'panoptes (argus + ming-qa)',
    date: day,
    timezone: 'Asia/Shanghai',
    repo,
    generated_at: new Date().toISOString(),
    run: {
      id: runId, started_at: startedAt.toISOString(), finished_at: new Date().toISOString(),
      started_at_shanghai: toShanghaiIso(startedAt), finished_at_shanghai: toShanghaiIso(),
      duration_s: Math.round((Date.now() - startedAt.getTime()) / 1000),
      trigger: normalizeTrigger(opts.trigger),
      args: { date: opts.date ?? null, date_source: dateSource, prs: opts.prs ?? null, out: opts.out ?? null, reuse_merged: opts.reuseMerged !== false, create_issues: Boolean(opts.createIssues), limit: opts.limit ?? null, pr_timeout_min: opts.prTimeoutMin ?? null, deadline: opts.deadline ?? null, burst_cutoff: opts.deadlineKind === 'burst-cutoff' ? opts.deadline : null, step_cache: process.env.ARGUS_STEP_CACHE !== '0', write_merged: writeMergedEnabled(opts), write_open: writeOpenEnabled(opts), ai_tests: !opts.noAiTests && aiTestCaps().enabled, ai_test_caps: runAiTestCaps(prs), ai_test_tier: runAiTestTier(prs), quota_pool: quota.pool, quota_caps: quota.caps, fresh_review: Boolean(opts.freshReview) },
      argus_commit: LOADED_ARGUS_COMMIT,
      ming_qa_commit: await mingQaVersion(), llm_model: LLM_MODEL, evidence_root: evidenceRoot,
    },
    dimensions: BOXES,
    // 等级定义不在这里手写：以 severity_standard 指向的审查机统一分级原文为准；severity_scale 只是等级名称
    severity_standard: severityStandardRef(),
    severity_scale: { ...SEVERITY_LEVEL_NAMES, pending: PENDING_LABEL_ZH },
    summary: {
      pr_count: prs.length,
      prs_by_severity: Object.fromEntries(['P0', 'P1', 'P2', 'P3', 'untested'].map((s) => [s, prs.filter((p) => p.severity === s).length])),
      findings_by_severity: Object.fromEntries(['P0', 'P1', 'P2', 'P3'].map((s) => [s, allFindings.filter((f) => f.severity === s).length])),
      findings_total: allFindings.length,
      reused_from_on_merge: prs.filter((p) => p.reused).map((p) => p.number),
      tested_count: prs.filter((p) => !['not-tested', 'qa-error'].includes(p.verdict)).length,
      not_tested: prs.filter((p) => p.verdict === 'not-tested').map((p) => ({ number: p.number, code: p.not_tested_code, reason: p.not_tested_reason })),
      qa_errors: prs.filter((p) => p.verdict === 'qa-error').map((p) => p.number),
      test_backed_findings_by_severity: Object.fromEntries(['P0', 'P1', 'P2', 'P3'].map((s) => [s, allFindings.filter((f) => f.test_backed && f.severity === s).length])),
      prs_test_backed: prs.filter((p) => p.test_backed).map((p) => p.number),
      pending_verification: pendingSummary(prs.flatMap((p) => p.pending_verification ?? [])),
    },
    prs,
    issues: { enabled: false, repo: ISSUE_REPO, labels: ISSUE_LABELS, dedup: 'fingerprint marker in body (label qa-auto): open issue → comment with new evidence; closed as fixed → reopen + comment; closed as not planned / wontfix label → silent', policy: 'P0/P1 findings backed by a failing check on PRs authored by PraiseZhu; system_health failures always; LLM-review-only findings never', results: [] },
    system_health: health,
  }
  if (opts.createIssues) {
    if (process.env.ARGUS_ALLOW_ISSUES !== '1') report.issues = { enabled: false, repo: ISSUE_REPO, labels: ISSUE_LABELS, reason: '--create-issues given but ARGUS_ALLOW_ISSUES!=1; nothing created' }
    else {
      try { report.issues = { enabled: true, repo: ISSUE_REPO, labels: ISSUE_LABELS, results: await createIssues(report) } }
      catch (err) { report.issues = { enabled: true, error: redact(err.message).slice(0, 500) }; health.qa_tool_failures.push({ stage: 'issues', message: redact(err.message).slice(0, 500) }) }
    }
  }
  report.merged_reports = writePerPrReports(report, { opts, outFile, kind: 'merged' })
  if (writeOpenEnabled(opts)) report.open_reports = writePerPrReports(report, { opts, outFile, kind: 'open' })
  const file = writeReport(report, outFile)
  return { file, report }
}

// 夜间全量（没有 --pr）默认把每个真实测完的已合并 PR 也写成 reports/merged/PR-N.json（与合并即测同格式），
// 供日报与之后的复用读取。--no-write-merged 关闭；--write-merged 在 --pr 运行里也打开。
// 运行来源：event（合并事件触发的 on-merge）· catch-up（补测，cron 每 2 小时）· manual（人手动跑，默认）
export const TRIGGERS = ['event', 'catch-up', 'manual']
export function normalizeTrigger(t) {
  if (t == null || t === '') return 'manual'
  if (!TRIGGERS.includes(t)) throw new Error(`--trigger must be one of ${TRIGGERS.join('|')}: ${t}`)
  return t
}

export const writeMergedEnabled = (opts) => opts.writeMerged ?? !opts.prs?.length
// --write-open：同样的单 PR 格式写 reports/open/PR-N.json。开放 PR 无论结果都写（含 not-tested / qa-error，
// 带具体原因），这样 deadline 截断的 PR 也有文件。默认关。
export const writeOpenEnabled = (opts) => Boolean(opts.writeOpen)
export const openReportsDir = () => process.env.ARGUS_OPEN_REPORTS_DIR ?? join(REPORTS_DIR, 'open')
export const openReportPath = (n, dir = openReportsDir()) => join(dir, `PR-${n}.json`)

const UNTESTED_VERDICTS = new Set(['not-tested', 'qa-error'])
export function mergedReportFor(report, pr, { kind = 'merged' } = {}) {
  const own = (list) => (list ?? []).filter((e) => e.pr == null || e.pr === pr.number)
  const h = report.system_health
  const findings = pr.findings ?? []
  const untested = UNTESTED_VERDICTS.has(pr.verdict)
  return {
    ...report,
    // 单 PR 文件的 date：已合并 → 合并时间的上海日期（与日报按 merged_at 选 PR 的规则一致）
    date: kind === 'merged' && pr.merged_at ? toShanghaiDay(new Date(pr.merged_at)) : report.date,
    ...(kind === 'merged' ? { merged_sha: pr.merge_sha ?? pr.tested_sha ?? null } : { head_sha: pr.tested_sha ?? pr.head_sha ?? null }),
    written_by: 'nightly',
    run: { ...report.run, args: { ...report.run.args, prs: [pr.number] }, source_report: null },
    summary: {
      pr_count: 1,
      prs_by_severity: Object.fromEntries(['P0', 'P1', 'P2', 'P3', 'untested'].map((s) => [s, pr.severity === s ? 1 : 0])),
      findings_by_severity: Object.fromEntries(['P0', 'P1', 'P2', 'P3'].map((s) => [s, findings.filter((f) => f.severity === s).length])),
      findings_total: findings.length, reused_from_on_merge: pr.reused ? [pr.number] : [], tested_count: untested ? 0 : 1,
      not_tested: pr.verdict === 'not-tested' ? [{ number: pr.number, code: pr.not_tested_code, reason: pr.not_tested_reason }] : [],
      qa_errors: pr.verdict === 'qa-error' ? [pr.number] : [],
      test_backed_findings_by_severity: Object.fromEntries(['P0', 'P1', 'P2', 'P3'].map((s) => [s, findings.filter((f) => f.test_backed && f.severity === s).length])),
      prs_test_backed: pr.test_backed ? [pr.number] : [],
      pending_verification: pendingSummary(pr.pending_verification ?? []),
    },
    prs: [pr],
    issues: { enabled: false, reason: 'written from a nightly run; issues are only handled by the run itself' },
    system_health: { ...h, qa_tool_failures: own(h.qa_tool_failures), env_problems: own(h.env_problems), llm_errors: own(h.llm_errors),
      ...(h.quota ? { quota: { ...h.quota, capped: own(h.quota.capped) } } : {}),
      heartbeat: h.heartbeat ? { ...h.heartbeat, prs: (h.heartbeat.prs ?? []).filter((b) => b.number === pr.number) } : null },
    merged_reports: undefined,
    open_reports: undefined,
  }
}

// 单 PR 文件的选择规则：merged → 真实测完且非复用；open → 所有开放 PR（未测的也写，带原因）
const PER_PR_KINDS = {
  merged: { enabled: writeMergedEnabled, path: (n, opts) => mergedReportPath(n, opts.mergedDir), select: (pr) => pr.state === 'merged' && !pr.reused && REUSABLE_VERDICTS.has(pr.verdict) },
  open: { enabled: writeOpenEnabled, path: (n, opts) => openReportPath(n, opts.openDir), select: (pr) => pr.state === 'open' && pr.not_tested_code !== 'excluded-by-pr-filter' },
}

export function writePerPrReports(report, { opts, outFile, kind }) {
  const k = PER_PR_KINDS[kind]
  if (!k.enabled(opts)) return []
  const written = []
  for (const pr of report.prs) {
    if (!k.select(pr)) continue
    const file = k.path(pr.number, opts)
    const self = resolve(file) === resolve(outFile)
    if (self && !opts.allowSelf) continue
    const doc = mergedReportFor(report, pr, { kind })
    // store-merged 从当前的 PR-N.json 自己重新存（例如修正 date / 去掉别的 PR）：原件先按版本机制改名，来源指向原件
    doc.run.source_report = self ? nextVersionPath(file) : outFile
    try { writeReport(doc, file); written.push({ pr: pr.number, file }) } catch (err) { report.system_health.qa_tool_failures.push({ pr: pr.number, stage: `write-${kind}-report`, message: redact(err.message).slice(0, 300) }) }
  }
  return written
}

// 覆盖率轮次的超时：步骤的 timeoutMin × 系数（并行 2、串行 1.5；ARGUS_COVERAGE_TIMEOUT_FACTOR 可覆盖），上限 ARGUS_COVERAGE_TIMEOUT_MAX_MIN（默认 300）
export function coverageTimeoutMin(step, parallel, env = process.env) {
  const base = step.timeoutMin ?? 20
  const f = Number(env.ARGUS_COVERAGE_TIMEOUT_FACTOR) > 0 ? Number(env.ARGUS_COVERAGE_TIMEOUT_FACTOR) : (parallel ? 2 : 1.5)
  const cap = Number(env.ARGUS_COVERAGE_TIMEOUT_MAX_MIN) > 0 ? Number(env.ARGUS_COVERAGE_TIMEOUT_MAX_MIN) : 300
  return Math.max(base, Math.min(cap, Math.round(base * f)))
}
