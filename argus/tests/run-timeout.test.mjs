import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { run, setBudget } from '../src/exec.mjs'

const root = mkdtempSync(join(tmpdir(), 'argus-deadline-'))
process.env.ARGUS_ROOT = root
after(() => { setBudget(null); rmSync(root, { recursive: true, force: true }) })
const live = (pid) => {
  // Linux containers can retain zombies until init reaps them; they cannot run.
  try { if (/^\d+ \(.*\) Z /.test(readFileSync(`/proc/${pid}/stat`, 'utf8'))) return false } catch {}
  try { process.kill(pid, 0); return true } catch { return false }
}
const waitGone = async (pid) => {
  const end = Date.now() + 1500
  while (live(pid) && Date.now() < end) await new Promise((r) => setTimeout(r, 20))
  assert.equal(live(pid), false, `process ${pid} survived cleanup`)
}
for (const ignoreTerm of [false, true]) {
  test(`never-ending child: TERM${ignoreTerm ? '/KILL escalation' : ''} ends within budget + 5s`, async () => {
    const code = `${ignoreTerm ? 'process.on("SIGTERM", () => {});' : ''}console.log(process.pid); setInterval(() => {}, 1000)`
    const result = await run(process.execPath, ['-e', code], { timeoutMs: 350, killGraceMs: 100 })
    assert.equal(result.status, 'timeout')
    assert.equal(result.ok, false)
    assert.equal(result.timedOut, true)
    assert.ok(result.durationMs < 5350)
    assert.equal(result.signal, ignoreTerm ? 'SIGKILL' : 'SIGTERM')
    await waitGone(Number(result.tail.trim()))
  })
}
test('timeout reaps grandchildren even when server parent handles TERM by exiting', async () => {
  const code = `const { spawn } = require('node:child_process'); const c = spawn(process.execPath, ['-e', 'process.on("SIGTERM",()=>{});setInterval(()=>{},1000)'], {stdio:'ignore'}); console.log(c.pid); setInterval(()=>{},1000)`
  const result = await run(process.execPath, ['-e', code], { timeoutMs: 400, killGraceMs: 100 })
  assert.equal(result.status, 'timeout')
  await waitGone(Number(result.tail.trim()))
})
test('leader exiting successfully still reaps descendants holding output pipes', async () => {
  const code = `const { spawn } = require('node:child_process'); const c = spawn(process.execPath, ['-e', 'setInterval(()=>{},1000)'], {stdio:['ignore',1,2]}); console.log(c.pid); c.unref()`
  const result = await run(process.execPath, ['-e', code], { timeoutMs: 1000 })
  assert.equal(result.status, 'pass')
  await waitGone(Number(result.tail.trim()))
})
test('expired run budget does not spawn a child; remaining budget bounds child timeout', async () => {
  const sentinel = join(root, 'should-not-exist')
  try {
    setBudget(Date.now() - 1)
    const result = await run(process.execPath, ['-e', `require('fs').writeFileSync(${JSON.stringify(sentinel)},'bad')`])
    assert.equal(result.status, 'timeout'); assert.equal(result.budgetExceeded, true)
    assert.equal(existsSync(sentinel), false)
    setBudget(Date.now() + 100)
    const bounded = await run(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { timeoutMs: 10_000, killGraceMs: 100 })
    assert.equal(bounded.status, 'timeout'); assert.equal(bounded.budgetExceeded, true)
    assert.ok(bounded.durationMs < 5100)
  } finally { setBudget(null) }
})
test('runSteps bounds beforeStep, execution, total run and environment preconditions', async () => {
  const { newRunDir, runSteps, cleanupRun } = await import('../src/runner.mjs')
  const dir = newRunDir(287, 'deadlines')
  const src = join(dir, 'src'); mkdirSync(src)
  const step = { id: 'never', kind: 'product', cmd: [process.execPath, '-e', 'setInterval(()=>{},1000)'], timeoutMs: 100 }
  try {
    const [blocked] = await runSteps({ sandbox: false, steps: [step] }, src, dir, { beforeStep: () => new Promise(() => {}) })
    assert.equal(blocked.status, 'timeout')
    const [timed] = await runSteps({ sandbox: false, steps: [step] }, src, dir)
    assert.equal(timed.status, 'timeout')
    const [suite] = await runSteps({ sandbox: false, steps: [{ ...step, timeoutMs: 10_000 }] }, src, dir, { timeoutMs: 100 })
    assert.equal(suite.status, 'timeout'); assert.ok(suite.durationMs < 5100)
    const [missing] = await runSteps({ sandbox: false, steps: [{ ...step, binary: { names: ['argus-missing-287'], var: 'MISSING' } }] }, src, dir)
    assert.equal(missing.status, 'environment_unavailable'); assert.equal(missing.ok, false)
  } finally { await cleanupRun(null, dir) }
})
test('single Node test budget reports timeout before the suite budget expires', async () => {
  const file = join(root, 'single-hang.test.mjs')
  writeFileSync(file, `import { test } from 'node:test'; test('never', async () => new Promise(()=>{setInterval(()=>{},1000)}))`)
  const env = { ...process.env, ARGUS_TEST_SUITE_TIMEOUT_MS: '4000', ARGUS_TEST_TIMEOUT_MS: '150' }
  delete env.NODE_TEST_CONTEXT
  const result = await run('bash', [resolve('run-tests.sh'), file], { env, timeoutMs: 7000 })
  assert.equal(result.exitCode, 124)
  assert.match(result.tail, /"status":"timeout","budget_ms":150/)
  assert.ok(result.durationMs < 5150)
})
test('executor owner termination cleans its detached child group', async () => {
  const module = new URL('../src/exec.mjs', import.meta.url).href
  const file = join(root, 'owner.mjs')
  writeFileSync(file, `import {run} from ${JSON.stringify(module)};
    const p = run(process.execPath, ['-e','console.log(process.pid);process.on("SIGTERM",()=>{});setInterval(()=>{},1000)'], {inheritOutput:true,timeoutMs:10000,killGraceMs:100});
    setTimeout(()=>process.kill(process.pid,'SIGTERM'),350);await p;`)
  const result = await run(process.execPath, [file], { timeoutMs: 3000 })
  assert.equal(result.exitCode, 143)
  await waitGone(Number(result.tail.trim()))
})
test('shell entry preserves test arguments and bounds an entire hanging suite', async () => {
  const file = join(root, 'hanging.test.mjs')
  writeFileSync(file, `import { test } from 'node:test'; test('never', async () => new Promise(()=>{setInterval(()=>{},1000)}))`)
  const env = { ...process.env, ARGUS_TEST_SUITE_TIMEOUT_MS: '800', ARGUS_TEST_TIMEOUT_MS: '10000' }
  delete env.NODE_TEST_CONTEXT
  const result = await run('bash', [resolve('run-tests.sh'), file], { cwd: resolve('.'), env, timeoutMs: 7000 })
  assert.equal(result.exitCode, 124)
  assert.match(result.tail, /"status":"timeout"/)
  assert.ok(result.durationMs < 5800)
})
test('probe timeout cleans server and reports timeout even when server ignores TERM', async () => {
  const spec = join(root, 'probe.json')
  const pidfile = join(root, 'server.pid')
  writeFileSync(spec, JSON.stringify({ timeoutMs: 600, portEnv: 'PORT', ready: { timeoutSec: 30 }, requests: [],
    start: [process.execPath, '-e', `require('fs').writeFileSync(${JSON.stringify(pidfile)},String(process.pid));process.on('SIGTERM',()=>{});setInterval(()=>{},1000)`] }))
  const result = await run(process.execPath, [resolve('src/probe-run.mjs'), spec], { timeoutMs: 6000 })
  assert.equal(result.exitCode, 124)
  assert.equal(JSON.parse(readFileSync(join(root, 'probe.report.json'), 'utf8')).status, 'timeout')
  await waitGone(Number(readFileSync(pidfile, 'utf8')))
})
