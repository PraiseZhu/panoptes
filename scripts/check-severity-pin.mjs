#!/usr/bin/env node
// 统一分级钉住值的每日比对（Praise 2026-10-23）：panoptes 的 argus/config/severity.md 是审查机 PraiseZhu/Review-PR
// mivo/config/severity.md 按 sha256 钉住的只读副本。这里每天和 Review-PR main 上的版本比一次：
//   same  → 记一行日志，退出 0
//   drift → 日志里写 ALERT，退出 4（不自动更新：等人核对后改 argus/config/severity.lock.json 的 ref / sha256 并换副本）
//   error → 读不到上游（权限 / 网络），退出 3
//   本地副本和锁文件对不上 → 退出 2（审查会拒绝运行，见 argus/src/severity.mjs）
// 用法: node scripts/check-severity-pin.mjs [--once-per-day] [--ref main] [--quiet]
// 日志: $ARGUS_ROOT/logs/severity-pin.log（每次一行 JSON）；最近一次结果: $ARGUS_ROOT/logs/severity-pin.json（nightly 读进 system_health）
// run-nightly.sh 每次启动都调用它（--once-per-day，同一天只真查一次），不改 cron。
import { readFileSync, writeFileSync, appendFileSync, mkdirSync, renameSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { execFileSync } from 'node:child_process'
import { parseArgs } from 'node:util'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const { readSeverityLock, loadSeverityStandard, compareUpstream } = await import(join(here, '..', 'argus', 'src', 'severity.mjs'))
const { values: v } = parseArgs({ options: { 'once-per-day': { type: 'boolean' }, ref: { type: 'string' }, quiet: { type: 'boolean' } } })
const logsDir = process.env.ARGUS_ROOT ? join(process.env.ARGUS_ROOT, 'logs') : join(here, '..', 'argus', 'logs')
const LOG = process.env.PANOPTES_SEVERITY_PIN_LOG ?? join(logsDir, 'severity-pin.log')
const STATE = process.env.PANOPTES_SEVERITY_PIN_STATE ?? join(logsDir, 'severity-pin.json')
const shanghaiDay = (d = new Date()) => new Date(d.getTime() + 8 * 3600e3).toISOString().slice(0, 10)
const now = new Date()
const readJson = (p) => { try { return JSON.parse(readFileSync(p, 'utf8')) } catch { return null } }

const prev = readJson(STATE)
const lock = readSeverityLock()
// 同一天只真查一次；但钉住值当天被人更新过（锁文件的 sha256 变了）就重查，不再沿用旧的 drift 结论。error 不缓存，下次运行重试
if (v['once-per-day'] && prev?.day === shanghaiDay(now) && ['same', 'drift'].includes(prev.status) && prev.pinned_sha256 === lock.sha256) process.exit(prev.status === 'drift' ? 4 : 0)

let entry
try { loadSeverityStandard({ fresh: true }) } catch (err) {
  entry = { status: 'local-mismatch', message: err.message }
}
if (!entry) {
  const ref = v.ref ?? 'main'
  let text = null, error = null
  try {
    const b64 = execFileSync(process.env.ARGUS_GH_BIN ?? 'gh', ['api', `repos/${lock.source_repo}/contents/${lock.path}?ref=${encodeURIComponent(ref)}`, '--jq', '.content'], { encoding: 'utf8', timeout: 60_000, stdio: ['ignore', 'pipe', 'pipe'] })
    text = Buffer.from(b64.replace(/\s+/g, ''), 'base64').toString('utf8')
  } catch (err) { error = String(err.stderr || err.message).trim().slice(0, 300) }
  entry = { ...compareUpstream({ lock, upstreamText: text }), upstream_ref: ref, ...(error ? { message: error } : {}) }
  if (entry.status === 'drift') entry.message = `ALERT: ${lock.source_repo} ${lock.path}@${ref} sha256 ${entry.upstream_sha256.slice(0, 12)} ≠ pinned ${lock.sha256.slice(0, 12)} (ref ${lock.ref.slice(0, 12)}). Review the upstream change, then update argus/config/severity.lock.json + argus/config/severity.md (a human decides; nothing is updated automatically).`
}
const out = { at: now.toISOString(), day: shanghaiDay(now), source_repo: lock.source_repo, path: lock.path, pinned_ref: lock.ref, pinned_sha256: lock.sha256, ...entry }
mkdirSync(dirname(LOG), { recursive: true })
appendFileSync(LOG, JSON.stringify(out) + '\n')
writeFileSync(`${STATE}.tmp-${process.pid}`, JSON.stringify(out, null, 2) + '\n'); renameSync(`${STATE}.tmp-${process.pid}`, STATE)
if (!v.quiet || out.status !== 'same') console.error(`[panoptes] severity pin: ${out.status}${out.message ? ` — ${out.message}` : ''}`)
process.exit({ same: 0, drift: 4, error: 3, 'local-mismatch': 2 }[out.status] ?? 3)
