// bench 环境超时（审计 2026-10-18 S3）：bench 排在单测之后并等覆盖率轮次；试运行检查里的超时不算 PR 发现。等 Praise 同意后再部署。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const root = mkdtempSync(join(tmpdir(), 'argus-bench-'))
process.env.ARGUS_ROOT = root
const { newRunDir, runSteps, cleanupRun } = await import('../src/runner.mjs')
const { isTrialTimeout, severityFor, waitForIdle } = await import('../src/nightly.mjs')
const { isIdle } = await import('../src/memory.mjs')
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

test('isTrialTimeout：只有试运行检查的 test_timeout 用例、且空闲重跑通过才降级；重跑仍超时 / introduced / 整步超时保持原级别', () => {
  const bench = { id: 'bench', trialOnly: true, kind: 'product' }
  const name = 'src/kernel/shadowRoundTrip.bench.test.ts > S6d kernel=new shadow round-trip'
  const r = { failureDetails: [{ name, type: 'test_timeout' }] }
  assert.equal(isTrialTimeout(bench, r, name, { status: 'flaky', rerun: 'pass', idle: true }), true, '空闲重跑通过 → 降级')
  assert.equal(isTrialTimeout(bench, r, name, { status: 'flaky', rerun: 'not-failing', idle: true }), false, 'not-failing 不是 pass，不降级')
  assert.equal(isTrialTimeout(bench, r, name, { status: 'flaky', rerun: 'pass', idle: false }), false, '等满 10 分钟机器没空下来：重跑通过也不降级')
  assert.equal(isTrialTimeout(bench, r, name, { status: 'flaky', rerun: 'pass' }), false, '没等空闲不降级')
  assert.equal(isTrialTimeout(bench, r, name, { status: 'unverified', rerun: 'fail', idle: true }), false, '重跑还超时 → 保持原级别')
  assert.equal(isTrialTimeout(bench, r, name, { status: 'unverified', rerun: 'timeout', idle: true }), false)
  assert.equal(isTrialTimeout(bench, r, name, { status: 'unverified', rerun: 'not-run', idle: true }), false, '没重跑不降级')
  assert.equal(isTrialTimeout(bench, r, name, { status: 'introduced', rerun: 'pass', idle: true }), false, 'introduced 不降级')
  assert.equal(isTrialTimeout(bench, { timedOut: true, failureDetails: r.failureDetails }, name, { status: 'timeout', rerun: 'pass', idle: true }), false, '整步超时不降级')
  assert.equal(isTrialTimeout(bench, { timedOut: true }, '*', { status: 'timeout', rerun: 'pass', idle: true }), false)
  assert.equal(isTrialTimeout(bench, { failureDetails: [{ name, type: 'assertion' }] }, name, { status: 'flaky', rerun: 'pass', idle: true }), false, '非超时失败照旧')
  assert.equal(isTrialTimeout({ id: 'unit', kind: 'product' }, r, name, { status: 'flaky', rerun: 'pass', idle: true }), false, '非试运行检查照旧')
  assert.equal(severityFor({ status: 'flaky', step: bench, merged: true }), 'P2', '非降级的试运行失败仍最高 P2')
})

test('waitForIdle：负载高就等，空闲后返回负载快照（loadavg、内存、等了多久）；等满上限仍忙 → idle=false', async () => {
  const busy = { loadavg: [9, 9, 9], cores: 8, mem_available_gb: 12 }, lowMem = { loadavg: [1, 1, 1], cores: 8, mem_available_gb: 2 }, idle = { loadavg: [1, 2, 3], cores: 8, mem_available_gb: 12 }
  const seq = [busy, lowMem, idle]
  let waits = 0
  const m = await waitForIdle({ sample: () => seq.shift(), wait: async () => { waits++ }, maxWaitMs: 60_000 })
  assert.equal(waits, 2); assert.equal(m.idle, true); assert.deepEqual(m.loadavg, [1, 2, 3]); assert.equal(m.mem_available_gb, 12); assert.equal(typeof m.waited_ms, 'number')
  const stuck = await waitForIdle({ sample: () => busy, wait: async () => {}, maxWaitMs: 0 })
  assert.equal(stuck.idle, false)
  assert.equal(isIdle(lowMem), false, '内存不到 3G 不算空闲')
})

test('runSteps：试运行检查（trialOnly）的结果带机器负载 machine（loadavg、内存），写进报告 evidence.machine_load', async () => {
  const runDir = newRunDir(1, 'load'); dirs.push(runDir)
  const src = join(runDir, 'src'); mkdirSync(src)
  const profile = { steps: [{ id: 'unit', kind: 'product', cmd: [process.execPath, '-e', '0'] }, { id: 'bench', kind: 'product', trialOnly: true, cmd: [process.execPath, '-e', '0'] }] }
  const [unit, bench] = await runSteps(profile, src, runDir, {})
  assert.equal(unit.machine, undefined)
  assert.equal(bench.machine.loadavg.length, 3); assert.ok(bench.machine.cores > 0); assert.equal(typeof bench.machine.mem_available_gb, 'number')
})
