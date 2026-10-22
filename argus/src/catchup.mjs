// 补测（catch-up）：纯脚本，不靠人盯。
//   1. 用 gh 列出昨天和今天（上海时区）合并进主干的 PR，按合并时间从早到晚；
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

export const MAX_ATTEMPTS = 3
export const LOCK_TIMEOUT_EXIT = 75
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
    .map((p) => ({ number: p.number, title: p.title, merged_at: p.mergedAt, merge_sha: p.mergeCommit?.oid ?? null, day: windows.find((w) => { const t = Date.parse(p.mergedAt); return t >= w.startMs && t < w.endMs })?.day ?? null }))
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

const UNFINISHED = new Set(['qa-error', 'not-tested'])
// 一个已合并 PR 是否有缺口：返回 null（没缺口）或 { reason }
export function gapOf(pr, mergedDir) {
  const file = join(mergedDir, `PR-${pr.number}.json`)
  if (!existsSync(file)) return { reason: 'missing', file }
  let doc
  try { doc = JSON.parse(readFileSync(file, 'utf8')) } catch { return { reason: 'unreadable', file } }
  const entry = (doc.prs ?? []).find((x) => x.number === pr.number)
  if (!entry) return { reason: 'no-entry', file }
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
  return argv.flatMap((a, i) => (a === '--pr' && /^\d+$/.test(argv[i + 1] ?? '') ? [Number(argv[i + 1])] : []))
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
export function catchUp({ repo, now = new Date(), reportsDir = REPORTS_DIR, mergedDir = join(reportsDir, 'merged'), list = (r, d) => listMergedPrs(r, d), runOne, maxAttempts = MAX_ATTEMPTS, active = () => activeRunPrs() }) {
  const days = catchUpDays(now)
  const merged = list(repo, days)
  const sFile = statePath(reportsDir)
  const state = loadState(sFile)
  state.prs ??= {}
  const gaps = []
  const running = active()
  const inProgress = []
  for (const pr of merged) {
    const gap = gapOf(pr, mergedDir)
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
  if (!gaps.length) { if (existsSync(sFile)) saveState(sFile, state); return { status: 'nothing-to-do', days, gaps: [], in_progress: inProgress } }

  const result = { schema: 'mivo-qa-catch-up/v1', repo, days, started_at: toShanghaiIso(now), gaps: gaps.map((g) => ({ number: g.number, day: g.day, merge_sha: g.merge_sha, reason: g.gap.reason })), in_progress: inProgress, runs: [],
    system_health: { status: 'ok', gave_up: [], qa_tool_failures: [] } }
  for (const g of gaps) {
    const key = String(g.number)
    const st = state.prs[key] ??= { number: g.number, merge_sha: g.merge_sha, day: g.day, attempts: 0, history: [] }
    st.attempts++
    const t0 = Date.now()
    const code = runOne(g)
    const still = gapOf(g, mergedDir)
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
  result.system_health.status = result.system_health.gave_up.length ? 'degraded' : result.runs.every((r) => r.filled) ? 'ok' : 'degraded'
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
