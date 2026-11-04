// 补测（catch-up）：纯脚本，不靠人盯。
//   1. 用 gh 列出昨天和今天（上海时区）合并的 PR（目标分支不在 registry.skipBaseBranches 里的都算，11-03 起不再只认 main），按合并时间从早到晚；
//   2. 与 reports/merged/PR-N.json 比对：缺文件、读不了、或测的不是这个 merge commit（tested_sha ≠ merge commit；
//      docs-only 看 merged_sha）、或上次没有结论（qa-error / not-tested）都算缺口；
//   3. 逐个缺口调用 run-nightly.sh --pr N --date <合并日> --write-merged --wait-lock S --no-issues|--create-issues --trigger catch-up
//      --out reports/catch-up/PR-N.json（绝不碰当天的日报文件）；
//   4. 等锁超时（exit 75）就停下，留到下一次调用再试；每个 PR 最多试 3 次，之后放弃，
//      把放弃记录写进本次结果文件的 system_health，下一次夜间日报的 system_health 也会带上（stage 'catch-up'）。
//   没有缺口时立即安静退出（不输出、不写文件）。
import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync, readdirSync, mkdirSync, writeFileSync, renameSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { REPORTS_DIR } from './paths.mjs'
import { toShanghaiDay, toShanghaiIso } from './util.mjs'
import { shanghaiDayWindow } from './github.mjs'
import { classifyChange } from './classify.mjs'
import { loadRegistry, shouldTestBase, mainOnly, DEFAULT_SKIP_BASE_BRANCHES } from './config.mjs'

export const MAX_ATTEMPTS = 3
export const LOCK_TIMEOUT_EXIT = 75
// run-nightly.sh 内存门跳过（10-27）：没开测，不算尝试次数、不放弃，下次再试
export const MEM_SKIP_EXIT = 76
// run-nightly.sh exit 3（pipeline 失败 / QA 环境坏，例如合并 commit 不在本地镜像里读不出 tree）也是「一个测试都没跑」：
// 10-29 22:07 / 23:22 / 23:40 三轮补测 #1411/#1413/#1419/#1422 在 66 分钟内把 3 次预算烧成永久 gave_up，工具修好也补不到。
// 所以和 76 一样只记录、不计数、不放弃。
export const PIPELINE_ERROR_EXIT = 3
export const catchUpDir = (reportsDir = REPORTS_DIR) => join(reportsDir, 'catch-up')
export const statePath = (reportsDir = REPORTS_DIR) => join(catchUpDir(reportsDir), 'state.json')

export function catchUpDays(now = new Date()) {
  const today = toShanghaiDay(now)
  const yesterday = toShanghaiDay(new Date(Date.parse(`${today}T00:00:00+08:00`) - 12 * 3600_000))
  return [yesterday, today]
}

// gh pr list（只读）。搜索条件按 UTC 日期放宽一天，再按上海时区窗口精确过滤
export function listMergedPrs(repo, days, { gh = process.env.ARGUS_GH_BIN ?? 'gh', run = spawnSync } = {}) {
  const windows = days.map(shanghaiDayWindow)
  const since = new Date(windows[0].startMs - 86400_000).toISOString().slice(0, 10)
  const r = run(gh, ['pr', 'list', '-R', repo, '--state', 'merged', '--search', `merged:>=${since}`, '--limit', '200', '--json', 'number,title,mergedAt,mergeCommit,baseRefName'], { encoding: 'utf8', timeout: 120_000 })
  let rows
  if (r.status === 0) rows = JSON.parse(r.stdout || '[]')
  else {
    // gh pr list 走 GraphQL；同一账号的 GraphQL 额度常被别的 bot 用光（10-16 22:28–03:24 连续失败，#971/#1017 晚补 5 小时）。
    // 退回 REST（额度单独计）：按更新时间倒序翻已关闭 PR，更新时间早于 since 就停（合并必然发生在最后一次更新之前）。
    const first = `gh pr list failed (exit ${r.status}): ${String(r.stderr ?? '').trim().slice(0, 300)}`
    try { rows = listMergedViaRest(repo, since, { gh, run }) } catch (e) { throw new Error(`${first}; REST fallback failed: ${e.message}`) }
    log(`[catch-up] ${first.split('\n')[0]} → listed ${rows.length} merged PR(s) via REST instead`)
  }
  return rows
    .map((p) => ({ number: p.number, title: p.title, merged_at: p.mergedAt, merge_sha: p.mergeCommit?.oid ?? null, base_ref: p.baseRefName ?? null, day: windows.find((w) => { const t = Date.parse(p.mergedAt); return t >= w.startMs && t < w.endMs })?.day ?? null }))
    .filter((p) => p.day && p.merge_sha)
    .sort((a, b) => Date.parse(a.merged_at) - Date.parse(b.merged_at) || a.number - b.number)
}

const log = (m) => process.stderr.write(`${m}\n`)
export function listMergedViaRest(repo, since, { gh = 'gh', run = spawnSync, maxPages = 5 } = {}) {
  const sinceMs = Date.parse(`${since}T00:00:00Z`)
  const rows = []
  for (let page = 1; page <= maxPages; page++) {
    const r = run(gh, ['api', `repos/${repo}/pulls?state=closed&sort=updated&direction=desc&per_page=100&page=${page}`, '--jq', '[.[] | {number, title, merged_at, merge_commit_sha, updated_at, base: {ref: .base.ref}}]'], { encoding: 'utf8', timeout: 120_000, maxBuffer: 64 * 1024 * 1024 })
    if (r.status !== 0) throw new Error(`gh api pulls exit ${r.status}${r.error ? ` (${r.error.code ?? r.error.message})` : ''}: ${String(r.stderr ?? '').trim().slice(0, 200)}`)
    const items = JSON.parse(r.stdout || '[]')
    for (const p of items) if (p.merged_at && Date.parse(p.merged_at) >= sinceMs) rows.push({ number: p.number, title: p.title, mergedAt: p.merged_at, mergeCommit: p.merge_commit_sha ? { oid: p.merge_commit_sha } : null, baseRefName: p.base?.ref ?? null })
    if (items.length < 100 || items.some((p) => Date.parse(p.updated_at) < sinceMs)) break
  }
  return rows
}

function registrySkip() { try { return loadRegistry().skipBaseBranches } catch { return DEFAULT_SKIP_BASE_BRANCHES } }

const UNFINISHED = new Set(['qa-error', 'not-tested'])
// 一个已合并 PR 是否有缺口：返回 null（没缺口）或 { reason }
export function gapOf(pr, mergedDir, { testBase = () => false } = {}) {
  const file = join(mergedDir, `PR-${pr.number}.json`)
  if (!existsSync(file)) return { reason: 'missing', file }
  let doc
  try { doc = JSON.parse(readFileSync(file, 'utf8')) } catch { return { reason: 'unreadable', file } }
  const entry = (doc.prs ?? []).find((x) => x.number === pr.number)
  if (!entry) return { reason: 'no-entry', file }
  // a skipped/non-main-base report is "no gap" only while its base is still out of scope; one written under the old main-only
  // rule (10-30 #139, e.g. integration/canvas-s1) is a gap again (11-03 stale-skip)
  if (entry.verdict === 'skipped' && entry.skip_reason === 'non-main-base') return testBase(pr.base_ref ?? entry.base_ref ?? null) ? { reason: 'stale-skip', file } : null
  if (UNFINISHED.has(entry.verdict)) return { reason: `unfinished (${entry.verdict})`, file }
  const tested = entry.verdict === 'docs-only' ? (doc.merged_sha ?? entry.merge_sha ?? null) : entry.tested_sha
  if (tested !== pr.merge_sha) return { reason: `stale (tested ${String(tested ?? 'none').slice(0, 12)} ≠ merge ${pr.merge_sha.slice(0, 12)})`, file }
  return null
}

// 正在跑或排队等 nightly.lock 的 run-nightly（on-merge 事件、手动）：它们的报告还没写出来，不算缺口（10-21 #1128 / #1161 被误补）。
// 排队中的 run-nightly.sh 已经是活进程（卡在 flock -w），所以扫 /proc 里 run-nightly.sh / cli.mjs nightly 的 --pr N 即可同时覆盖两种状态。
export function prsInArgv(argv) {
  const isRun = argv.some((a) => /(^|\/)run-nightly\.sh$/.test(a)) || (argv.some((a) => /(^|\/)cli\.mjs$/.test(a)) && argv.includes('nightly'))
  if (!isRun) return []
  return argv.flatMap((a, i) => {
    if (a === '--pr') return /^\d+$/.test(argv[i + 1] ?? '') ? [Number(argv[i + 1])] : []
    const m = /^--pr=(\d+)$/.exec(a)
    return m ? [Number(m[1])] : []
  })
}
export function activeRunPrs({ procDir = '/proc', selfPid = process.pid } = {}) {
  const prs = new Set()
  let pids = []
  try { pids = readdirSync(procDir).filter((d) => /^\d+$/.test(d) && Number(d) !== selfPid) } catch { return prs }
  for (const pid of pids) {
    let argv
    try { argv = readFileSync(join(procDir, pid, 'cmdline'), 'utf8').split('\0').filter(Boolean) } catch { continue }
    for (const n of prsInArgv(argv)) prs.add(n)
  }
  return prs
}

export function loadState(file) {
  try { return JSON.parse(readFileSync(file, 'utf8')) } catch { return { prs: {} } }
}
function saveState(file, state) {
  mkdirSync(dirname(file), { recursive: true })
  writeFileSync(`${file}.tmp-${process.pid}`, JSON.stringify(state, null, 2) + '\n')
  renameSync(`${file}.tmp-${process.pid}`, file)
}

// 主流程。runOne(pr) 返回子进程退出码；gh / 时钟 / 目录都可注入，便于测试。
// backlog (older-day, non-docs) gaps drained per catch-up round; PANOPTES_CATCHUP_MAX_BACKLOG overrides (0 = none, today's only)
export function backlogCap(env = process.env) {
  const v = Number.parseInt(env.PANOPTES_CATCHUP_MAX_BACKLOG ?? '', 10)
  return Number.isFinite(v) && v >= 0 ? v : 2
}

export function catchUp({ repo, skipBaseBranches = registrySkip(), mainOnly: mo = mainOnly(), now = new Date(), reportsDir = REPORTS_DIR, mergedDir = join(reportsDir, 'merged'), list = (r, d) => listMergedPrs(r, d), runOne, maxAttempts = MAX_ATTEMPTS, active = () => activeRunPrs(), docsOnly = () => false, maxBacklog = backlogCap() }) {
  const days = catchUpDays(now)
  const listed = list(repo, days)
  const sFile = statePath(reportsDir)
  const state = loadState(sFile)
  state.prs ??= {}
  // 11-04 (#203 follow-up): backlog gaps deferred by the per-round cap are kept in state.backlog, so one whose merge day leaves the
  // 2-day window at midnight is still caught up later instead of silently dropped. Dropped again once filled / run / given up.
  const seen = new Set(listed.map((p) => p.number))
  const merged = [...Object.values(state.backlog ?? {}).filter((p) => p && !seen.has(p.number)), ...listed]
  const hadBacklog = Object.keys(state.backlog ?? {}).length > 0
  const testBase = (ref) => shouldTestBase(ref, skipBaseBranches, { mainOnly: mo })
  const gaps = []
  const running = active()
  const inProgress = []
  const skipped = []
  for (const pr of merged) {
    // 11-03: every base is tested except registry.skipBaseBranches (probe-*), same rule as nightly.mjs / mergewatch / watchdog.
    // PANOPTES_MAIN_ONLY=1 (or the flag file) restores the 10-30 main-only rule. base_ref null counts as main.
    if (!testBase(pr.base_ref)) { skipped.push({ number: pr.number, base_ref: pr.base_ref, reason: 'non-main-base' }); delete state.prs[String(pr.number)]; continue }
    const gap = gapOf(pr, mergedDir, { testBase })
    const key = String(pr.number)
    const prev = state.prs[key]
    if (!gap) { if (prev && !prev.gave_up_at) delete state.prs[key]; continue }
    // 新的 merge commit（例如 revert 后重新合并）重新计数
    if (prev && prev.merge_sha !== pr.merge_sha) delete state.prs[key]
    if (state.prs[key]?.gave_up_at) continue
    // 有 on-merge（或手动）运行正在跑 / 排队：交给它，不算缺口、不计尝试次数
    if (running.has(pr.number)) { inProgress.push(pr.number); continue }
    gaps.push({ ...pr, gap })
  }
  // 先看 diff 再排队（10-26 #1298 纯文档等了 40 分钟）：纯文档的缺口插到最前面，几秒就出报告，不排在长测试后面
  for (const g of gaps) { try { g.docs_only = !!docsOnly(g) } catch { g.docs_only = false } }
  // 11-03: then main before integration / feature branches, so a burst of branch merges never delays main
  const isMain = (g) => Number(g.base_ref == null || g.base_ref === 'main')
  // 11-04 (late-1609-1618 #3): SLA order. A merge from today (Shanghai) can still make its merge-to-test SLA, yesterday's backlog
  // cannot: today's gaps run before yesterday's (#1618 waited ~4 h behind four 11-02 backlog PRs). Within a day: main first, oldest first.
  const today = days[days.length - 1]
  const isToday = (g) => Number(g.day === today)
  gaps.sort((a, b) => Number(b.docs_only) - Number(a.docs_only) || isToday(b) - isToday(a) || isMain(b) - isMain(a))
  // and at most maxBacklog non-docs backlog (older-day) gaps per round; the rest wait for the next tick, after that tick's fresh merges
  const deferred = []
  { let n = 0
    const kept = []
    state.backlog = {}
    for (const g of gaps) {
      if (!g.docs_only && g.day !== today && ++n > maxBacklog) {
        deferred.push(g.number)
        state.backlog[String(g.number)] = { number: g.number, merged_at: g.merged_at, merge_sha: g.merge_sha, day: g.day, base_ref: g.base_ref ?? null, deferred_at: toShanghaiIso(now) }
      } else kept.push(g)
    }
    gaps.splice(0, gaps.length, ...kept) }
  if (!Object.keys(state.backlog).length) delete state.backlog
  if (!gaps.length) { if (existsSync(sFile) || state.backlog || hadBacklog) saveState(sFile, state); return { status: 'nothing-to-do', days, gaps: [], deferred_backlog: deferred, in_progress: inProgress, skipped } }

  const result = { schema: 'mivo-qa-catch-up/v1', repo, days, started_at: toShanghaiIso(now), gaps: gaps.map((g) => ({ number: g.number, day: g.day, merge_sha: g.merge_sha, reason: g.gap.reason, docs_only: g.docs_only, base_ref: g.base_ref ?? null })), deferred_backlog: deferred, in_progress: inProgress, skipped, runs: [],
    system_health: { status: 'ok', gave_up: [], qa_tool_failures: [] } }
  for (const g of gaps) {
    const key = String(g.number)
    const st = state.prs[key] ??= { number: g.number, merge_sha: g.merge_sha, day: g.day, attempts: 0, history: [] }
    st.attempts++
    const t0 = Date.now()
    const code = runOne(g)
    if (code === MEM_SKIP_EXIT) {   // 内存不足没开测：撤回这次计数，不放弃；剩下的也会被跳过，停到下次
      st.attempts--; st.last_reason = 'memory-skip'; st.history.push({ at: toShanghaiIso(), exit_code: code, filled: false, memory_skip: true })
      result.runs.push({ number: g.number, attempt: st.attempts, exit_code: code, duration_s: Math.round((Date.now() - t0) / 1000), filled: false, remaining_gap: g.gap.reason, memory_skip: true })
      result.stopped = 'memory-skip'; break
    }
    const still = gapOf(g, mergedDir, { testBase })
    // pipeline 失败没开测（exit 3）：撤回这次计数、不放弃，接着试下一个缺口（工具修好后照样会补上）
    if (code === PIPELINE_ERROR_EXIT && still) {
      st.attempts--; st.last_reason = 'pipeline-error'
      st.history.push({ at: toShanghaiIso(), exit_code: code, filled: false, pipeline_error: true })
      result.runs.push({ number: g.number, attempt: st.attempts, exit_code: code, duration_s: Math.round((Date.now() - t0) / 1000), filled: false, remaining_gap: still.reason, pipeline_error: true })
      continue
    }
    const run = { number: g.number, attempt: st.attempts, exit_code: code, duration_s: Math.round((Date.now() - t0) / 1000), filled: !still, remaining_gap: still?.reason ?? null }
    result.runs.push(run)
    st.history.push({ at: toShanghaiIso(), exit_code: code, filled: !still })
    if (!still) { delete state.prs[key]; continue }
    st.last_reason = code === LOCK_TIMEOUT_EXIT ? 'lock-timeout' : `exit ${code}; ${still.reason}`
    if (st.attempts >= maxAttempts) {
      st.gave_up_at = toShanghaiIso()
      const entry = { pr: g.number, stage: 'catch-up', merge_sha: g.merge_sha, attempts: st.attempts, message: `catch-up gave up after ${st.attempts} attempts (last: ${st.last_reason})` }
      result.system_health.gave_up.push(entry)
      result.system_health.qa_tool_failures.push(entry)
    }
    // 等锁超时：别的运行还没完，剩下的缺口留到下一次调用
    if (code === LOCK_TIMEOUT_EXIT) { result.stopped = 'lock-timeout'; break }
  }
  result.finished_at = toShanghaiIso()
  result.system_health.status = result.system_health.gave_up.length ? 'degraded' : result.runs.every((r) => r.filled || r.memory_skip) ? 'ok' : 'degraded'
  saveState(sFile, state)
  return { status: 'ran', ...result }
}

// 夜间运行读取：最近 48 小时内放弃的补测，带进日报 system_health.qa_tool_failures（stage 'catch-up'）
export function recentGiveUps({ reportsDir = REPORTS_DIR, now = Date.now(), withinMs = 48 * 3600_000 } = {}) {
  const st = loadState(statePath(reportsDir))
  return Object.values(st.prs ?? {}).filter((p) => p.gave_up_at && now - Date.parse(p.gave_up_at) <= withinMs)
    .map((p) => ({ pr: p.number, stage: 'catch-up', message: `catch-up gave up after ${p.attempts} attempts (last: ${p.last_reason ?? 'unknown'}); merge ${String(p.merge_sha).slice(0, 12)} has no matching reports/merged/PR-${p.number}.json` }))
}

// 实际调用 run-nightly.sh（由 run-nightly.sh --catch-up 设置 PANOPTES_RUN_NIGHTLY）
export function catchUpChildArgs(pr, { script, waitLock = 14400, reportsDir = REPORTS_DIR, createIssues = false, trigger = 'catch-up' } = {}) {
  const out = join(catchUpDir(reportsDir), `PR-${pr.number}.json`)
  return [script, '--pr', String(pr.number), '--date', pr.day, '--write-merged', '--wait-lock', String(waitLock), createIssues ? '--create-issues' : '--no-issues', '--trigger', trigger, '--out', out]
}
export function runNightlyFor(pr, { script = process.env.PANOPTES_RUN_NIGHTLY, waitLock = 14400, reportsDir = REPORTS_DIR, log = null, createIssues = false, trigger = 'catch-up' } = {}) {
  if (!script) throw new Error('PANOPTES_RUN_NIGHTLY not set (use run-nightly.sh --catch-up)')
  const args = catchUpChildArgs(pr, { script, waitLock, reportsDir, createIssues, trigger })
  // 子进程只在开 issue 时才拿到 ARGUS_ALLOW_ISSUES=1
  const env = { ...process.env, PANOPTES_CATCH_UP_CHILD: '1' }
  if (!createIssues) delete env.ARGUS_ALLOW_ISSUES
  const r = spawnSync('bash', args, { stdio: ['ignore', log ?? 'inherit', log ?? 'inherit'], env })
  return r.status ?? 1
}

// 默认的「纯文档」判断：gh 读 PR 改动文件（只读），用和夜间测试同一个 classifyChange + 项目 profile；拿不到就当不是纯文档（照常排队）
export function docsOnlyOf(repo, { gh = process.env.ARGUS_GH_BIN ?? 'gh', run = spawnSync, profile = null } = {}) {
  let prof = profile
  return (pr) => {
    if (!prof) { try { const p = loadRegistry().projects.find((x) => x.repo === repo); prof = p?.profileData ?? {} } catch { prof = {} } }
    const r = run(gh, ['api', '--paginate', `repos/${repo}/pulls/${pr.number}/files`, '--jq', '.[] | {filename, status, additions, deletions, patch}'], { encoding: 'utf8', timeout: 30_000 })
    if (r.status !== 0 || !r.stdout) return false
    const files = r.stdout.trim().split('\n').filter(Boolean).map((l) => JSON.parse(l))
    return files.length > 0 && classifyChange(files, prof).riskClass === 'A'
  }
}
