import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { run } from '../src/exec.mjs'

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
