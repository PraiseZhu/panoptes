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

// 10-27 内存门（电脑运维规则）：≥8G 正常；等满后 ≥5G 且 swap ≥4G 降级；否则跳过；单个纯文档 PR 低内存不等
const fakeMem = (root, swapGb = 5) => { const f = join(root, 'meminfo'); writeFileSync(f, `MemTotal: 16386368 kB\nMemAvailable: 1 kB\nSwapFree: ${swapGb * 1048576} kB\n`); return f }
const fakeVmstat = (root) => { const f = join(root, 'vmstat'); writeFileSync(f, 'balloon_inflate 64896512\nballoon_deflate 63323648\n'); return f }
test('slot_mem_gate: PI_TEST_MEM_AVAIL_MB 9000 / 5500 / 4000 → normal / degraded / skip; docs-only skips the wait', () => {
  const root = mkdtempSync(join(tmpdir(), 'argus-gate-'))
  const gate = (mb, check = '', swap = 5) => sh(root, `slot_mem_gate ${check}; echo "$GATE_MODE|$GATE_WAIT_S"`, { PANOPTES_MEMINFO: fakeMem(root, swap), PANOPTES_TEST: '1', PI_TEST_MEM_AVAIL_MB: String(mb), PANOPTES_MEM_POLL_S: '1', PANOPTES_MEM_WAIT_S: '1' }).stdout.trim()
  assert.equal(gate(9000, 'false'), 'normal|0')                       // 内存够：不问文档检查
  assert.match(gate(5500), /^degraded\|[1-9]/)                        // 等满仍 <8G、≥5G：降级，记等待秒数
  assert.match(gate(4000), /^skip\|[1-9]/)                            // <5G：跳过
  assert.match(gate(9000, '', 3), /^skip\|/)                          // swap <4G：就算 9G 也不跑
  assert.equal(gate(4000, 'true'), 'docs-only|0')                     // 纯文档：不等
  // PI_TEST_MEM_AVAIL_MB 只在 PANOPTES_TEST=1 时生效：真机上设了它也不改读数（这里真读数是假 meminfo 的 1 kB → 跳过，而不是 9000 的正常）
  assert.match(sh(root, 'slot_mem_gate; echo $GATE_MODE', { PANOPTES_MEMINFO: fakeMem(root), PI_TEST_MEM_AVAIL_MB: '9000', PANOPTES_TEST: '', PANOPTES_MEM_POLL_S: '1', PANOPTES_MEM_WAIT_S: '1' }).stdout.trim(), /^skip$/)
  assert.equal(sh(root, 'slot_balloon_mb', { PANOPTES_VMSTAT: fakeVmstat(root) }).stdout.trim(), '6144')
})

// 同样三档走真的 run-nightly.sh（假 nvm / 假密钥 / 临时 QA_HOME，PANOPTES_GATE_DRYRUN=1 在门后停下，不跑测试）
test('run-nightly.sh memory gate end to end: 9000 runs normally, 5500 degraded (1 shard, no parallel), 4000 skipped with exit 76 and the slot released', () => {
  const root = mkdtempSync(join(tmpdir(), 'argus-gate-e2e-'))
  const nvm = join(root, 'nvm'); mkdirSync(nvm); writeFileSync(join(nvm, 'nvm.sh'), 'nvm() { :; }\n')
  const secrets = join(root, 'secrets.env'); writeFileSync(secrets, 'GITHUB_TOKEN=x\nQA_LLM_BASE_URL=http://127.0.0.1:9\nQA_LLM_API_KEY=x\n')
  const script = join(dirname(SLOT), '..', 'run-nightly.sh')
  const run = (mb) => spawnSync('bash', [script, '--date', '2026-10-27'], { encoding: 'utf8', timeout: 60_000, env: { ...process.env, QA_HOME: join(root, `qa-${mb}`),
    PANOPTES_NVM_DIR: nvm, QA_SECRETS_FILE: secrets, PANOPTES_BOOTSTRAP: '0', PANOPTES_TMP_SWEEP: '0', PANOPTES_TEST: '1', PANOPTES_GATE_DRYRUN: '1', PI_TEST_MEM_AVAIL_MB: String(mb),
    PANOPTES_MEMINFO: fakeMem(root), PANOPTES_VMSTAT: fakeVmstat(root), PANOPTES_MEM_POLL_S: '1', PANOPTES_MEM_WAIT_S: '1', ARGUS_UNIT_SHARDS: '', ARGUS_MAX_PARALLEL: '' } })
  let r = run(9000); assert.equal(r.status, 0, r.stderr); assert.match(r.stdout, /gate: mode=normal wait=0s shards=auto max_parallel=auto/)
  r = run(5500); assert.equal(r.status, 0, r.stderr); assert.match(r.stdout, /gate: mode=degraded wait=\d+s shards=1 max_parallel=1/)
  r = run(4000); assert.equal(r.status, 76, r.stdout + r.stderr); assert.doesNotMatch(r.stdout, /gate: mode=/)
  assert.match(r.stderr, /SKIP MemAvailable 4000 MiB, SwapFree 5120 MiB, balloon 6144 MiB after \d+s: below the degraded floor/)
  // 退出后槽位已释放：马上能拿到 nightly.lock
  assert.equal(spawnSync('bash', ['-c', `exec 9>${join(root, 'qa-4000', 'argus-root', 'nightly.lock')}; flock -n 9 && echo free`], { encoding: 'utf8' }).stdout.trim(), 'free')
})

// 10-28 合并即测 SLA：按 PR 加锁，内存够（8G+4G 交换）就和占槽的运行并行，不够就照旧排队
test('on-merge: slot busy + memory ok -> concurrent; memory low -> keeps waiting (gives up); same PR twice -> refused', async () => {
  const root = mkdtempSync(join(tmpdir(), 'argus-slot-')); mkdirSync(join(root, 'priority'))
  const mi = (avail) => { const f = join(root, `mi-${avail}`); writeFileSync(f, `MemTotal: 16000000 kB\nMemAvailable: ${avail} kB\nSwapTotal: 8388604 kB\nSwapFree: ${5 * 1048576} kB\n`); return f }
  const h = holder(root); await sleepMs(300)
  try {
    let r = sh(root, `exec 9>$ARGUS_ROOT/nightly.lock; slot_acquire_onmerge 9 101 5 && echo $SLOT_MODE; ls $ARGUS_ROOT/priority | grep -c conc; slot_release_priority; ls $ARGUS_ROOT/priority | grep -c 'conc\\|req'`, { PANOPTES_MEMINFO: mi(9 * 1048576) })
    assert.deepEqual(r.stdout.trim().split('\n'), ['concurrent', '1', '0'])
    r = sh(root, `exec 9>$ARGUS_ROOT/nightly.lock; slot_acquire_onmerge 9 102 1 && echo $SLOT_MODE || echo gave-up`, { PANOPTES_MEMINFO: mi(6 * 1048576) })
    assert.equal(r.stdout.trim(), 'gave-up')
    const same = spawn('bash', ['-c', `source ${SLOT}; exec {x}>${root}/priority/pr-103.lock; flock $x; sleep 30`], { stdio: 'ignore' }); await sleepMs(300)
    try {
      r = sh(root, `exec 9>$ARGUS_ROOT/nightly.lock; slot_acquire_onmerge 9 103 1 && echo $SLOT_MODE || echo refused`, { PANOPTES_MEMINFO: mi(9 * 1048576) })
      assert.equal(r.stdout.trim(), 'refused')
    } finally { same.kill() }
    r = sh(root, `: >$ARGUS_ROOT/priority/conc-${h.pid}; exec 9>$ARGUS_ROOT/nightly.lock; slot_acquire_onmerge 9 104 1 && echo $SLOT_MODE || echo capped`, { PANOPTES_MEMINFO: mi(9 * 1048576), PANOPTES_ONMERGE_MAX_CONC: '1' })
    assert.equal(r.stdout.trim(), 'capped')
  } finally { h.kill() }
})

test('on-merge: free slot -> lock mode (unchanged)', () => {
  const root = mkdtempSync(join(tmpdir(), 'argus-slot-'))
  const r = sh(root, `exec 9>$ARGUS_ROOT/nightly.lock; slot_acquire_onmerge 9 7 5 && echo $SLOT_MODE`)
  assert.equal(r.stdout.trim(), 'lock')
})
