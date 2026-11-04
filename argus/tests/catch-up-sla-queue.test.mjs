// 11-04 (pi-work/late-1609-1618 FINDINGS #3, #4): catch-up queue by SLA (today's merges before yesterday's backlog, backlog capped
// per round) and catch-up children keep their place in line for the run lock (queue tickets in scripts/qa-slot.sh).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, existsSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { catchUp } from '../src/catchup.mjs'

// 11-03 12:07 BJ tick: four 11-02 backlog gaps + #1618 (merged 11-03 02:19 BJ)
const now = new Date('2026-11-03T04:07:00Z')
const g = (number, day, at, base_ref = 'integration/canvas-s2') => ({ number, merged_at: at, merge_sha: String(number % 10).repeat(40), day, base_ref })
const PRS = [g(1597, '2026-11-02', '2026-11-02T03:00:00Z', 'main'), g(1601, '2026-11-02', '2026-11-02T04:00:00Z'), g(1379, '2026-11-02', '2026-11-02T05:00:00Z', 'feat/video-in-canvas'),
  g(1610, '2026-11-02', '2026-11-02T06:00:00Z'), g(1618, '2026-11-03', '2026-11-02T18:19:09Z')]
const run = (opts = {}) => {
  const ran = []
  const r = catchUp({ repo: 'o/r', now, reportsDir: mkdtempSync(join(tmpdir(), 'argus-cu-sla-')), list: () => PRS, active: () => new Set(), skipBaseBranches: ['probe-*'], mainOnly: false,
    runOne: (p) => { ran.push(p.number); return 1 }, ...opts })
  return { r, ran }
}

test('catch-up SLA order: today\'s merge (#1618) runs before yesterday\'s backlog, not 6th in line', () => {
  const { ran } = run({ maxBacklog: 10 })
  assert.equal(ran[0], 1618)
  assert.deepEqual(ran, [1618, 1597, 1601, 1379, 1610])   // backlog: main first, then oldest first
})

test('catch-up caps backlog drain per round (default 2); deferred backlog is reported, not counted as an attempt', () => {
  const { r, ran } = run()
  assert.deepEqual(ran, [1618, 1597, 1601])
  assert.deepEqual(r.deferred_backlog, [1379, 1610])
  const { ran: r0 } = run({ maxBacklog: 0 })
  assert.deepEqual(r0, [1618])
})

const SLOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'scripts', 'qa-slot.sh')
const sh = (root, script, env = {}) => spawnSync('bash', ['-c', `set -u; source ${SLOT}; ${script}`], { encoding: 'utf8', env: { ...process.env, ARGUS_ROOT: root, PANOPTES_SLOT_POLL_S: '0.1', ...env } })
const sleeper = () => spawn('sleep', ['30'], { stdio: 'ignore' })

test('run lock: a newer waiter does not jump an older waiting ticket (catch-up child keeps its place)', async () => {
  const root = mkdtempSync(join(tmpdir(), 'argus-slot-q-')); mkdirSync(join(root, 'priority'))
  const older = sleeper()
  try {
    writeFileSync(join(root, 'priority', `q-${String(Math.floor(Date.now() / 1000) - 60).padStart(12, '0')}-${older.pid}`), '')
    const r = sh(root, `exec 9>$ARGUS_ROOT/nightly.lock; slot_acquire_normal 9 1 && echo got || echo gave-up`)
    assert.match(r.stdout, /gave-up/, r.stderr)
    assert.equal(readdirSync(join(root, 'priority')).filter((f) => f.startsWith('q-')).length, 1, 'own ticket removed on give-up')
  } finally { older.kill() }
  await new Promise((res) => older.exitCode !== null || older.signalCode ? res() : older.once('exit', res))
  const r2 = sh(root, `exec 9>$ARGUS_ROOT/nightly.lock; slot_acquire_normal 9 1 && echo got`)
  assert.match(r2.stdout, /got/, 'dead ticket swept, slot taken')
})

test('run lock: a catch-up child that has waited PANOPTES_SLOT_STARVE_S goes ahead of a later on-merge request; on-merge does not take the free slot then', () => {
  const root = mkdtempSync(join(tmpdir(), 'argus-slot-q-')); mkdirSync(join(root, 'priority'))
  const req = sleeper(), waiter = sleeper()
  try {
    writeFileSync(join(root, 'priority', `req-${req.pid}`), '')
    let r = sh(root, `exec 9>$ARGUS_ROOT/nightly.lock; slot_acquire_normal 9 2 && echo got || echo gave-up`, { PANOPTES_SLOT_STARVE_S: '1' })
    assert.match(r.stdout, /got/, 'starving waiter took the slot despite a live request: ' + r.stderr)
    writeFileSync(join(root, 'priority', `q-${String(Math.floor(Date.now() / 1000) - 3600).padStart(12, '0')}-${waiter.pid}`), '')
    r = sh(root, `exec 9>$ARGUS_ROOT/nightly.lock; exec 7>$ARGUS_ROOT/priority.lock; slot_acquire_priority 9 7 1 && echo got-$SLOT_MODE || echo gave-up`)
    assert.match(r.stdout, /gave-up/, 'priority run left the free slot to the starving ticket')
    assert.ok(existsSync(join(root, 'priority', `req-${req.pid}`)))
  } finally { req.kill(); waiter.kill() }
})
