// 性能门预热 + 失败只重跑一次（2026-11-05，阈值不改）：第一次读数保留，重跑单独记，pass-after-retry 单独标出，两次都失败仍失败。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, rmSync, readFileSync, existsSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const root = mkdtempSync(join(tmpdir(), 'argus-perfretry-'))
process.env.ARGUS_ROOT = root
const { newRunDir, runSteps, cleanupRun } = await import('../src/runner.mjs')
const { loadProfile } = await import('../src/config.mjs')
const dirs = []
after(async () => { for (const d of dirs) await cleanupRun(null, d); rmSync(root, { recursive: true, force: true }) })

// 假 bench：每次调用记一行到 calls 文件；第 N 次调用（含预热）打印 p95=<readings[N]>，>2 就 exit 1
const script = `const fs=require('fs');const [calls,...r]=process.argv.slice(1);const n=fs.existsSync(calls)?fs.readFileSync(calls,'utf8').split('\\n').filter(Boolean).length:0;
fs.appendFileSync(calls,'call\\n');const p=Number(r[n]);console.log('[SC4] groupSize=2 p50=1.00ms p95='+p.toFixed(2)+'ms');process.exit(p>2?1:0)`
async function bench(readings, perfRetry = { warmup: true, retries: 1 }) {
  const runDir = newRunDir(1, 'perf'); dirs.push(runDir)
  const src = join(runDir, 'src'); mkdirSync(src)
  const calls = join(runDir, 'calls.txt')
  const profile = { steps: [{ id: 'bench', kind: 'product', trialOnly: true, cache: false, perfRetry, cmd: [process.execPath, '-e', script, calls, ...readings.map(String)] }] }
  const [r] = await runSteps(profile, src, runDir)
  return { r, n: existsSync(calls) ? readFileSync(calls, 'utf8').split('\n').filter(Boolean).length : 0, runDir }
}

test('mivo profile：bench 开了预热 + 重跑一次，阈值命令不变', () => {
  assert.deepEqual(loadProfile('mivo-canvas-plugin').steps.find((s) => s.id === 'bench').perfRetry, { warmup: true, retries: 1 })
})

test('预热先跑且不计入：预热超线、正式过 → pass，不重跑', async () => {
  const { r, n } = await bench([9.9, 1.5])
  assert.equal(n, 2); assert.equal(r.ok, true)
  assert.equal(r.perf.verdict, 'pass'); assert.equal(r.perf.warmup.ok, false); assert.equal(r.perf.retry, null)
  assert.equal(r.perf.first.p95[0].p95Ms, 1.5)
})

test('第一次失败 → 正好重跑一次；过了标 pass-after-retry，第一次读数保留不被覆盖', async () => {
  const { r, n, runDir } = await bench([1.0, 2.56, 1.4])
  assert.equal(n, 3, '预热 1 + 正式 1 + 重跑 1')
  assert.equal(r.ok, true); assert.equal(r.perf.verdict, 'pass-after-retry')
  assert.equal(r.perf.first.ok, false); assert.equal(r.perf.first.p95[0].p95Ms, 2.56)
  assert.equal(r.perf.retry.ok, true); assert.equal(r.perf.retry.p95[0].p95Ms, 1.4)
  assert.match(readFileSync(join(runDir, 'logs', 'bench.log'), 'utf8'), /p95=2\.56ms/, '正式日志是第一次读数')
  assert.match(readFileSync(join(runDir, 'logs', 'bench.retry.log'), 'utf8'), /p95=1\.40ms/)
})

test('两次都失败 → 仍失败，只重跑一次，两次读数都在', async () => {
  const { r, n } = await bench([1.0, 3.34, 2.9, 1.0])
  assert.equal(n, 3, '不会有第二次重跑')
  assert.equal(r.ok, false); assert.equal(r.perf.verdict, 'fail')
  assert.equal(r.perf.first.p95[0].p95Ms, 3.34); assert.equal(r.perf.retry.p95[0].p95Ms, 2.9)
})

test('没配 perfRetry 的步骤行为不变：不预热、不重跑', async () => {
  const runDir = newRunDir(1, 'plain'); dirs.push(runDir)
  const src = join(runDir, 'src'); mkdirSync(src); const calls = join(runDir, 'c.txt')
  const [r] = await runSteps({ steps: [{ id: 'bench', kind: 'product', cache: false, cmd: [process.execPath, '-e', script, calls, '3'] }] }, src, runDir)
  assert.equal(r.ok, false); assert.equal(r.perf, undefined); assert.equal(readFileSync(calls, 'utf8').trim().split('\n').length, 1)
})

// 直接 mock runOnce：断言报告文件行为（ops verify-252/first-json）——bench.first.json 保留第一次读数，bench.json 可被重跑覆盖
const { runPerfWithRetry } = await import('../src/perf-retry.mjs')

test('bench.first.json 保留第一次读数；预热报告被删；只重跑一次', async () => {
  const d = mkdtempSync(join(tmpdir(), 'argus-fj-'))
  try {
    const rf = join(d, 'bench.json')
    const seq = [['warmup', 9.9, true], [null, 2.56, false], ['retry', 1.4, true]]
    const tags = []
    const { res, perf } = await runPerfWithRetry({
      warmup: true, retries: 1, reportFile: rf,
      runOnce: async (tag) => {
        tags.push(tag)
        const [, p, ok] = seq[tags.length - 1]
        writeFileSync(rf, JSON.stringify({ tag, p95: p }))
        return { ok, exitCode: ok ? 0 : 1, tail: `p95=${p}ms`, durationMs: 1 }
      },
    })
    assert.deepEqual(tags, ['warmup', null, 'retry'])
    assert.equal(JSON.parse(readFileSync(join(d, 'bench.first.json'), 'utf8')).p95, 2.56)
    assert.equal(JSON.parse(readFileSync(rf, 'utf8')).tag, 'retry')
    assert.equal(perf.first.reportFile, join(d, 'bench.first.json'))
    assert.equal(perf.verdict, 'pass-after-retry')
    assert.equal(res.ok, true)
  } finally { rmSync(d, { recursive: true, force: true }) }
})

test('整步超时不重跑', async () => {
  let n = 0
  const { perf } = await runPerfWithRetry({
    warmup: false,
    runOnce: async () => { n++; return { ok: false, timedOut: true, tail: '' } },
  })
  assert.equal(n, 1)
  assert.equal(perf.verdict, 'fail')
})
