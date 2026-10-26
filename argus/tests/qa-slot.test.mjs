// 合并即测插队 + 单槽位 + 内存门（scripts/qa-slot.sh、src/priority.mjs、processTargets 让位）
import test from 'node:test'
import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, existsSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { yieldToPriority, liveRequests } from '../src/priority.mjs'
import { processTargets } from '../src/nightly.mjs'

const SLOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'scripts', 'qa-slot.sh')
const sh = (root, script, env = {}) => spawnSync('bash', ['-c', `set -u; source ${SLOT}; ${script}`], { encoding: 'utf8', env: { ...process.env, ARGUS_ROOT: root, PANOPTES_SLOT_POLL_S: '0.1', ...env } })
const holder = (root) => spawn('bash', ['-c', `exec 9>${root}/nightly.lock; flock 9; sleep 30`], { stdio: 'ignore' })
const sleepMs = (ms) => new Promise((r) => setTimeout(r, ms))

test('normal run does not take a free slot while a live priority request exists; dead requests are swept', () => {
  const root = mkdtempSync(join(tmpdir(), 'argus-slot-')); mkdirSync(join(root, 'priority'))
  writeFileSync(join(root, 'priority', `req-${process.pid}`), '')
  let r = sh(root, `exec 9>$ARGUS_ROOT/nightly.lock; slot_acquire_normal 9 1 && echo got || echo gave-up`)
  assert.equal(r.stdout.trim(), 'gave-up')
  writeFileSync(join(root, 'priority', 'req-999999'), '')
  r = sh(root, `rm -f $ARGUS_ROOT/priority/req-${process.pid}; exec 9>$ARGUS_ROOT/nightly.lock; slot_acquire_normal 9 1 && echo got`)
  assert.equal(r.stdout.trim(), 'got')
  assert.ok(!existsSync(join(root, 'priority', 'req-999999')))
})

test('priority run takes the free slot (mode lock) and cleans its request', () => {
  const root = mkdtempSync(join(tmpdir(), 'argus-slot-'))
  const r = sh(root, `exec 9>$ARGUS_ROOT/nightly.lock 7>$ARGUS_ROOT/priority.lock; slot_acquire_priority 9 7 5 && echo $SLOT_MODE; ls $ARGUS_ROOT/priority; slot_release_priority; ls $ARGUS_ROOT/priority | wc -l`)
  const [mode, req, left] = r.stdout.trim().split('\n')
  assert.equal(mode, 'lock'); assert.match(req, /^req-\d+$/); assert.equal(left, '0')
})

test('priority run borrows a held slot only while the holder is parked (yield file), never runs alongside it otherwise', async () => {
  const root = mkdtempSync(join(tmpdir(), 'argus-slot-')); mkdirSync(join(root, 'priority'))
  const h = holder(root); await sleepMs(300)
  try {
    let r = sh(root, `exec 9>$ARGUS_ROOT/nightly.lock 7>$ARGUS_ROOT/priority.lock; slot_acquire_priority 9 7 1 && echo $SLOT_MODE || echo gave-up`)
    assert.equal(r.stdout.trim(), 'gave-up')                     // 槽位被占、对方没让 → 不并行
    assert.equal(readdirSync(join(root, 'priority')).length, 0)  // 放弃时请求也撤掉
    writeFileSync(join(root, 'priority', `yield-${h.pid}`), '')
    r = sh(root, `exec 9>$ARGUS_ROOT/nightly.lock 7>$ARGUS_ROOT/priority.lock; slot_acquire_priority 9 7 5 && echo $SLOT_MODE`)
    assert.equal(r.stdout.trim(), 'borrowed')
  } finally { h.kill() }
})

test('cap other than 1 is refused (电脑运维: max 1 concurrent QA)', () => {
  const root = mkdtempSync(join(tmpdir(), 'argus-slot-'))
  const r = sh(root, `exec 9>$ARGUS_ROOT/nightly.lock; slot_acquire_normal 9 "" && echo got || echo refused`, { PANOPTES_QA_SLOTS: '2' })
  assert.equal(r.stdout.trim(), 'refused')
})

test('memory gate: MemAvailable >= 8 GB AND SwapFree >= 4 GB by default, both configurable', () => {
  const root = mkdtempSync(join(tmpdir(), 'argus-slot-'))
  const mi = (avail, swap) => { const f = join(root, `mi-${avail}-${swap}`); writeFileSync(f, `MemTotal: 16000000 kB\nMemAvailable: ${avail} kB\nSwapTotal: 8388604 kB\nSwapFree: ${swap} kB\n`); return f }
  const ok = (f, env = {}) => sh(root, 'slot_mem_ok && echo y || echo n', { PANOPTES_MEMINFO: f, ...env }).stdout.trim()
  assert.equal(ok(mi(9 * 1048576, 5 * 1048576)), 'y')
  assert.equal(ok(mi(7 * 1048576, 5 * 1048576)), 'n')
  assert.equal(ok(mi(9 * 1048576, 3 * 1048576)), 'n')
  assert.equal(ok(mi(7 * 1048576, 3 * 1048576), { PANOPTES_MIN_MEM_GB: '6', PANOPTES_MIN_SWAP_GB: '2' }), 'y')
})

test('yieldToPriority: parks while a live request exists, writes and removes the yield ack', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'argus-prio-'))
  assert.deepEqual(await yieldToPriority({ dir, env: {} }), { yielded: false, waitedMs: 0 })
  const req = spawn('sleep', ['30']); writeFileSync(join(dir, `req-${req.pid}`), '')
  let sawAck = false
  const p = yieldToPriority({ dir, env: {}, pollMs: 20, onYield: () => { sawAck = existsSync(join(dir, `yield-${process.pid}`)) } })
  await sleepMs(150); req.kill(); const r = await p
  assert.ok(r.yielded && sawAck)
  assert.ok(!existsSync(join(dir, `yield-${process.pid}`)))
  assert.equal(liveRequests(dir), 0)
  // the priority run itself never parks
  const req2 = spawn('sleep', ['30']); writeFileSync(join(dir, `req-${req2.pid}`), '')
  try { assert.equal((await yieldToPriority({ dir, env: { PANOPTES_PRIORITY: '1' } })).yielded, false) } finally { req2.kill() }
})

test('processTargets parks between PRs (not mid-PR) and notes it', async () => {
  const order = []
  const health = { qa_tool_failures: [], notes: [] }
  await processTargets([{ number: 1, reason: 'merged' }, { number: 2, reason: 'merged' }], {
    health, opts: { yieldFn: async () => { order.push('yield'); return order.length > 2 ? { yielded: true, waitedMs: 3000 } : { yielded: false, waitedMs: 0 } } },
    processOne: async (t) => { order.push(t.number); return { number: t.number, verdict: 'pass' } },
  })
  assert.deepEqual(order, ['yield', 1, 'yield', 2])
  assert.ok(health.notes.some((n) => /parked 3s before #2/.test(n)))
})
