// bench 环境超时（审计 2026-10-18 S3）：bench 排在单测之后并等覆盖率轮次；试运行检查里的超时不算 PR 发现。等 Praise 同意后再部署。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const root = mkdtempSync(join(tmpdir(), 'argus-bench-'))
process.env.ARGUS_ROOT = root
const { newRunDir, runSteps, cleanupRun } = await import('../src/runner.mjs')
const { isTrialTimeout, severityFor } = await import('../src/nightly.mjs')
const { loadProfile } = await import('../src/config.mjs')
const dirs = []
after(async () => { for (const d of dirs) await cleanupRun(null, d); rmSync(root, { recursive: true, force: true }) })

test('mivo profile：bench 排在 unit 之后且 afterCoverage', () => {
  const ids = loadProfile('mivo-canvas-plugin').steps.map((s) => s.id)
  assert.ok(ids.indexOf('bench') > ids.indexOf('unit'))
  assert.equal(loadProfile('mivo-canvas-plugin').steps.find((s) => s.id === 'bench').afterCoverage, true)
})

test('runSteps beforeStep：afterCoverage 的步骤等 promise 结束后才开跑', async () => {
  const runDir = newRunDir(1, 'wait'); dirs.push(runDir)
  const src = join(runDir, 'src'); mkdirSync(src)
  const order = []
  let release
  const gate = new Promise((r) => { release = r })
  const profile = { steps: [{ id: 'unit', kind: 'product', cmd: [process.execPath, '-e', '0'] }, { id: 'bench', kind: 'product', afterCoverage: true, cmd: [process.execPath, '-e', '0'] }] }
  setTimeout(() => { order.push('cov-done'); release() }, 200)
  const res = await runSteps(profile, src, runDir, { beforeStep: (s) => { order.push(`before:${s.id}`); return s.afterCoverage ? gate.then(() => order.push('bench-start')) : null } })
  assert.deepEqual(res.map((r) => r.ok), [true, true])
  assert.deepEqual(order, ['before:unit', 'before:bench', 'cov-done', 'bench-start'])
})

test('isTrialTimeout：只认试运行检查的整步超时或 test_timeout 用例；其他失败照旧', () => {
  const bench = { id: 'bench', trialOnly: true, kind: 'product' }
  const name = 'src/kernel/shadowRoundTrip.bench.test.ts > S6d kernel=new shadow round-trip'
  assert.equal(isTrialTimeout(bench, { failureDetails: [{ name, type: 'test_timeout' }] }, name), true)
  assert.equal(isTrialTimeout(bench, { timedOut: true }, '*'), true)
  assert.equal(isTrialTimeout(bench, { failureDetails: [{ name, type: 'assertion' }] }, name), false)
  assert.equal(isTrialTimeout({ id: 'unit', kind: 'product' }, { failureDetails: [{ name, type: 'test_timeout' }] }, name), false)
  assert.equal(severityFor({ status: 'flaky', step: bench, merged: true }), 'P2', '非超时的试运行失败仍最高 P2')
})
