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

// 10-27 内存门（#1315 在内存门等满 30 分钟）：只有单个纯文档 PR 低内存时不等；其他照旧等满并记 GATE_WAIT_S
test('slot_mem_gate: docs-only skips the wait only when memory is low; normal PRs still wait and record gate wait', () => {
  const root = mkdtempSync(join(tmpdir(), 'argus-gate-'))
  const mi = (avail, swap) => { const f = join(root, `mi-${avail}-${swap}`); writeFileSync(f, `MemTotal: 16386368 kB\nMemAvailable: ${avail * 1048576} kB\nSwapFree: ${swap * 1048576} kB\n`); return f }
  const gate = (f, check, wait = '2') => sh(root, `slot_mem_gate ${check}; echo "$GATE_WAIT_S|$GATE_SKIP|$GATE_LOW"`, { PANOPTES_MEMINFO: f, PANOPTES_MEM_POLL_S: '1', PANOPTES_MEM_WAIT_S: wait }).stdout.trim()
  assert.equal(gate(mi(9, 5), 'false'), '0||')                       // 内存够：不问文档检查，直接开
  assert.equal(gate(mi(6, 5), 'true'), '0|docs-only|')               // 低内存 + 纯文档：不等
  const [w, skip, low] = gate(mi(6, 5), 'false').split('|')          // 低内存 + 普通 PR：等满，记等待秒数
  assert.ok(Number(w) >= 2, w); assert.equal(skip, ''); assert.equal(low, '1')
  assert.equal(gate(mi(9, 3), '').split('|')[2], '1')                 // swap 不足、没有文档检查（多 PR）：照样等
})
