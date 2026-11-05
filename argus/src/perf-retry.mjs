// 性能门（bench）减噪（2026-11-05，阈值不改，Jasper 裁定 nodeIdsInRect p95 仍是 2ms）：
// 先跑一遍预热（结果丢弃），再正式测；正式测失败（非整步超时）就原样重跑一次，只重跑一次。
// 第一次读数（first）永远保留、不被重跑覆盖：报告文件挪到 <name>.first.json，重跑单独记在 retry。
// verdict：pass（第一次就过）/ pass-after-retry（第一次没过、重跑过了，单独标出）/ fail（两次都没过）。
import { existsSync, renameSync, rmSync } from 'node:fs'

const P95_RE = /([^\n]*?)p95=([\d.]+)ms/g
export function p95Readings(tail) {
  return [...String(tail ?? '').matchAll(P95_RE)].map((m) => ({ line: m[0].trim().slice(0, 200), p95Ms: Number(m[2]) }))
}

const summary = (r) => ({ ok: r.ok, exitCode: r.exitCode, timedOut: Boolean(r.timedOut), durationMs: r.durationMs,
  failures: r.ok ? [] : [...(r.failures ?? [])], p95: p95Readings(r.tail) })

export const sideFile = (file, tag) => file.replace(/(\.[^./]+)?$/, (ext) => `.${tag}${ext || ''}`)

// runOnce(logTag) → run() 的结果；logTag 为 'warmup' / null（正式）/ 'retry'
export async function runPerfWithRetry({ runOnce, reportFile = null, warmup = true, retries = 1 }) {
  let warm = null
  if (warmup) {
    const w = await runOnce('warmup')
    warm = { ok: w.ok, durationMs: w.durationMs }
    if (reportFile) rmSync(reportFile, { force: true })   // 预热的报告不算数
  }
  const first = await runOnce(null)
  const perf = { warmup: warm, first: summary(first), retry: null, verdict: first.ok ? 'pass' : 'fail' }
  if (first.ok || first.timedOut || retries < 1) return { res: first, perf }
  if (reportFile && existsSync(reportFile)) { const f = sideFile(reportFile, 'first'); renameSync(reportFile, f); perf.first.reportFile = f }
  const retry = await runOnce('retry')
  perf.retry = summary(retry)
  perf.verdict = retry.ok ? 'pass-after-retry' : 'fail'
  return { res: retry, perf }
}
