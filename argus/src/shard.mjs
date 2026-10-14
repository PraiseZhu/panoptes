// 单测分片（burst 容量）：一个 vitest 步骤拆成 N 个 `--shard=i/N` 进程并行跑（Mivo 的 vitest.config 是
// maxWorkers: 1 / fileParallelism: false，单进程串行；分片是独立进程，互不共享内存状态）。
// 分片数受内存护栏约束：同一进程里所有正在跑的分片步骤（首轮 h1 与覆盖率轮次 cov 可能同时在跑）共用一个预算
//   预算 = min(ARGUS_SHARD_BUDGET_GB（默认 10）, MemAvailable − ARGUS_SHARD_RESERVE_GB（默认 1.5）) − 已被其他分片步骤占用的
//   分片数 = clamp(floor(预算 / 每片内存), 1, min(step.shard.max, CPU 核数))，ARGUS_UNIT_SHARDS 可强制指定（1 = 不分片）。
// 跑完把各片的 vitest JSON 报告合成一份（判定、flaky 对比照旧读这一份），覆盖率 coverage-final.json 按语句位置把命中数相加，
// 再重新算 coverage-summary.json 的总数。
import { readFileSync } from 'node:fs'
import { cpus } from 'node:os'
import { memAvailableGb as memGbFrom } from './memory.mjs'

let reservedGb = 0
export const shardReservedGb = () => reservedGb
export function reserveShards(gb) { reservedGb += gb; let done = false; return () => { if (!done) { done = true; reservedGb = Math.max(0, reservedGb - gb) } } }

export function shardPlan(step, { freeGb = memGbFrom(), reserved = reservedGb, cores = cpus().length, env = process.env } = {}) {
  const cfg = step?.shard
  if (!cfg) return { shards: 1, reason: 'step not shardable' }
  // Hard cap (default 2, ARGUS_SHARD_MAX overrides): the box is shared, and MemAvailable at plan time ignores other bots' growth
  const max = Math.max(1, Math.min(Number(cfg.max ?? 8), cores, Number(env.ARGUS_SHARD_MAX ?? 2) || 2))
  const per = Number(env.ARGUS_SHARD_GB ?? cfg.perShardGb ?? 1.2)
  const forced = env.ARGUS_UNIT_SHARDS != null && env.ARGUS_UNIT_SHARDS !== '' ? Math.max(1, Math.floor(Number(env.ARGUS_UNIT_SHARDS)) || 1) : null
  const cap = Number(env.ARGUS_SHARD_BUDGET_GB ?? 10)
  const keep = Number(env.ARGUS_SHARD_RESERVE_GB ?? 1.5)
  const budget = Math.max(0, Math.min(cap, (freeGb ?? 0) - keep) - reserved)
  if (forced != null) return { shards: Math.min(forced, max), reason: `ARGUS_UNIT_SHARDS=${forced}`, per_shard_gb: per, budget_gb: round1(budget), mem_available_gb: round1(freeGb), reserved_gb: round1(reserved) }
  const byMem = Math.floor(budget / per)
  const shards = Math.max(1, Math.min(max, byMem))
  const reason = byMem >= max ? `${shards} shards (max ${cfg.max ?? 8}, ${cores} cores)` : `${shards} shard(s): memory budget ${round1(budget)} GB / ${per} GB per shard (MemAvailable ${round1(freeGb)} GB, other shard steps ${round1(reserved)} GB)`
  return { shards, reason, per_shard_gb: per, budget_gb: round1(budget), mem_available_gb: round1(freeGb), reserved_gb: round1(reserved) }
}
const round1 = (x) => (x == null || !Number.isFinite(x) ? null : Math.round(x * 10) / 10)

// 分片命令：在 vitest 参数后追加 --shard=i/N；${TEST_REPORT} 换成每片自己的报告文件
export function shardArgs(cmd, i, n) { return [...cmd, `--shard=${i}/${n}`] }

// vitest JSON 报告合并：计数相加，testResults 拼接，success 全部成功才为真
const SUM_KEYS = ['numTotalTestSuites', 'numPassedTestSuites', 'numFailedTestSuites', 'numPendingTestSuites', 'numTotalTests', 'numPassedTests', 'numFailedTests', 'numPendingTests', 'numTodoTests']
export function mergeVitestJson(reports) {
  const list = reports.filter(Boolean)
  const out = { ...(list[0] ?? {}) }
  for (const k of SUM_KEYS) out[k] = list.reduce((s, r) => s + (Number(r[k]) || 0), 0)
  out.startTime = Math.min(...list.map((r) => Number(r.startTime) || Infinity))
  if (!Number.isFinite(out.startTime)) delete out.startTime
  out.success = list.length > 0 && list.every((r) => r.success !== false)
  out.testResults = list.flatMap((r) => r.testResults ?? [])
  out.snapshot = list[0]?.snapshot
  out.shards = list.length
  return out
}

// istanbul coverage-final.json 合并：同一文件按语句 / 函数 / 分支的位置对齐后命中数相加（各片加载的源文件可能重叠）
const locKey = (l) => `${l?.start?.line}:${l?.start?.column}-${l?.end?.line}:${l?.end?.column}`
function mergeCounters(target, tMap, src, sMap, mapName, cntName, isBranch = false) {
  const index = new Map(Object.entries(tMap).map(([id, loc]) => [locKey(isBranch ? loc.loc ?? loc.locations?.[0] : loc), id]))
  let next = Object.keys(tMap).length
  for (const [id, loc] of Object.entries(sMap)) {
    const key = locKey(isBranch ? loc.loc ?? loc.locations?.[0] : loc)
    let tid = index.get(key)
    if (tid == null) { tid = String(next++); target[mapName][tid] = loc; index.set(key, tid); target[cntName][tid] = isBranch ? (src[cntName][id] ?? []).map(() => 0) : 0 }
    if (isBranch) {
      const a = target[cntName][tid] ?? [], b = src[cntName][id] ?? []
      target[cntName][tid] = Array.from({ length: Math.max(a.length, b.length) }, (_, i) => (a[i] ?? 0) + (b[i] ?? 0))
    } else target[cntName][tid] = (target[cntName][tid] ?? 0) + (src[cntName][id] ?? 0)
  }
}
export function mergeCoverageFinal(finals) {
  const out = {}
  for (const f of finals.filter(Boolean)) {
    for (const [file, fc] of Object.entries(f)) {
      if (!out[file]) { out[file] = structuredClone(fc); continue }
      const t = out[file]
      t.statementMap ??= {}; t.s ??= {}; t.fnMap ??= {}; t.f ??= {}; t.branchMap ??= {}; t.b ??= {}
      mergeCounters(t, t.statementMap, fc, fc.statementMap ?? {}, 'statementMap', 's')
      mergeCounters(t, t.fnMap, fc, fc.fnMap ?? {}, 'fnMap', 'f')
      mergeCounters(t, t.branchMap, fc, fc.branchMap ?? {}, 'branchMap', 'b', true)
    }
  }
  return out
}

// coverage-summary.json 的 total（istanbul 口径：行 = 语句起始行，同一行取最大命中数）
const metric = (total, covered) => ({ total, covered, skipped: 0, pct: total ? Math.round((covered / total) * 10000) / 100 : 100 })
export function coverageSummaryOf(final) {
  let st = 0, sc = 0, ft = 0, fc = 0, bt = 0, bc = 0, lt = 0, lc = 0
  for (const c of Object.values(final ?? {})) {
    const s = Object.values(c.s ?? {}); st += s.length; sc += s.filter((x) => x > 0).length
    const f = Object.values(c.f ?? {}); ft += f.length; fc += f.filter((x) => x > 0).length
    for (const arr of Object.values(c.b ?? {})) { bt += arr.length; bc += arr.filter((x) => x > 0).length }
    const lines = new Map()
    for (const [id, loc] of Object.entries(c.statementMap ?? {})) { const l = loc?.start?.line; if (l == null) continue; lines.set(l, Math.max(lines.get(l) ?? 0, c.s?.[id] ?? 0)) }
    lt += lines.size; lc += [...lines.values()].filter((x) => x > 0).length
  }
  return { total: { lines: metric(lt, lc), statements: metric(st, sc), functions: metric(ft, fc), branches: metric(bt, bc) } }
}

export function readJsonOrNull(file) { try { return JSON.parse(readFileSync(file, 'utf8')) } catch { return null } }
