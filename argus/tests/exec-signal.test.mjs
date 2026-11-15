import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, writeFileSync, existsSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { run } from '../src/exec.mjs'

for (const signal of ['SIGTERM', 'SIGINT']) {
  test(`nightly-style ${signal} shutdown preserves TERM grace before immediate process.exit`, async () => {
    const root = mkdtempSync(join(tmpdir(), 'argus-stop-grace-'))
    const marker = join(root, 'cleaned')
    const pidfile = join(root, 'child.pid')
    const owner = join(root, 'owner.mjs')
    const execModule = new URL('../src/exec.mjs', import.meta.url).href
    const childCode = `const fs = require('node:fs');process.on('SIGTERM',()=>{setTimeout(()=>{fs.writeFileSync(${JSON.stringify(marker)},'done');process.exit(0)},200)});fs.writeFileSync(${JSON.stringify(pidfile)},String(process.pid));setInterval(()=>{},1000)`
    writeFileSync(owner, `import {run,killAllActive} from ${JSON.stringify(execModule)};
      import {existsSync} from 'node:fs';
      process.once(${JSON.stringify(signal)},()=>{killAllActive();process.exit(143)});
      const pending=run(process.execPath,['-e',${JSON.stringify(childCode)}],{timeoutMs:5000,killGraceMs:700});
      while(!existsSync(${JSON.stringify(pidfile)})) await new Promise(r=>setTimeout(r,10));
      process.kill(process.pid,${JSON.stringify(signal)});await pending;`)
    try {
      const result = await run(process.execPath, [owner], { timeoutMs: 7000 })
      assert.equal(result.exitCode, 143, result.tail)
      assert.equal(existsSync(marker), true, 'TERM cleanup must get its 200ms before owner exits')
      assert.equal(readFileSync(marker, 'utf8'), 'done')
      const pid = Number(readFileSync(pidfile, 'utf8'))
      // A reparented zombie on a container cannot execute or hold resources.
      let zombie = false
      try { zombie = /^\d+ \(.*\) Z /.test(readFileSync(`/proc/${pid}/stat`, 'utf8')) } catch {}
      if (!zombie) assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' })
    } finally { rmSync(root, { recursive: true, force: true }) }
  })
}

test('run: normal exit records signal null and ISO ended_at', async () => {
  const logFile = join(mkdtempSync(join(tmpdir(), 'argus-sig-')), 'ok.log')
  const r = await run(process.execPath, ['-e', 'process.exit(3)'], { logFile, timeoutMs: 30_000 })
  assert.equal(r.exitCode, 3)
  assert.equal(r.signal, null)
  assert.ok(!Number.isNaN(Date.parse(r.endedAt)) && r.endedAt.endsWith('Z'))
  await new Promise((res) => setTimeout(res, 50))
  assert.match(readFileSync(logFile, 'utf8'), /\[exit 3 \d+ms\]/)
})

test('run: child killed by external SIGTERM records signal', async () => {
  const logFile = join(mkdtempSync(join(tmpdir(), 'argus-sig-')), 'term.log')
  const r = await run(process.execPath, ['-e', 'setTimeout(() => process.kill(process.pid, "SIGTERM"), 50); setInterval(() => {}, 1000)'], { logFile, timeoutMs: 30_000 })
  assert.equal(r.signal, 'SIGTERM')
  assert.equal(r.timedOut, false)
  assert.equal(r.ok, false)
  assert.equal(r.status, 'runner_error')
  assert.ok(r.endedAt)
  await new Promise((res) => setTimeout(res, 50))
  assert.match(readFileSync(logFile, 'utf8'), /\[exit null SIGTERM \d+ms\]/)
})

test('run: spawn failure returns runner_error and complete result fields', async () => {
  const r = await run('/nonexistent/argus-command', [])
  assert.equal(r.status, 'runner_error')
  assert.equal(r.ok, false)
  assert.equal(r.exitCode, null)
  assert.equal(r.signal, null)
  assert.equal(r.failureParseStatus, 'not_detected')
})
