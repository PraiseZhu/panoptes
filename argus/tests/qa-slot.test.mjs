// 合并即测插队 + 单槽位 + 内存门（scripts/qa-slot.sh、src/priority.mjs、processTargets 让位）
import test from 'node:test'
import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, existsSync, readdirSync, readFileSync } from 'node:fs'
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

test('slot_mem_ok (on-merge admission) = slot_shards_for_mem >= 1: >= 6144 MiB admits (any swap), < 6144 rejects; thresholds configurable', () => {
  const root = mkdtempSync(join(tmpdir(), 'argus-slot-'))
  const mi = (avail, swap) => { const f = join(root, `mi-${avail}-${swap}`); writeFileSync(f, `MemTotal: 16000000 kB\nMemAvailable: ${avail} kB\nSwapTotal: 8388604 kB\nSwapFree: ${swap} kB\n`); return f }
  const ok = (f, env = {}) => sh(root, 'slot_mem_ok && echo y || echo n', { PANOPTES_MEMINFO: f, ...env }).stdout.trim()
  assert.equal(ok(mi(9 * 1048576, 5 * 1048576)), 'y')
  assert.equal(ok(mi(7 * 1048576, 5 * 1048576)), 'y')
  assert.equal(ok(mi(9 * 1048576, 2 * 1048576)), 'y')       // low swap: still 1 shard
  assert.equal(ok(mi(6144 * 1024, 0)), 'y')
  assert.equal(ok(mi(6143 * 1024, 5 * 1048576)), 'n')
  assert.equal(ok(mi(5 * 1048576, 2 * 1048576)), 'n')
  assert.equal(ok(mi(7 * 1048576, 5 * 1048576), { PANOPTES_DEGRADED_MEM_GB: '8' }), 'n')
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

// 10-27 内存门（电脑运维规则）：≥8G 正常；≥6G 且 swap ≥4G 降级；否则跳过；单个纯文档 PR 低内存不等
const fakeMem = (root, swapGb = 5) => { const f = join(root, 'meminfo'); writeFileSync(f, `MemTotal: 16386368 kB\nMemAvailable: 1 kB\nSwapFree: ${swapGb * 1048576} kB\n`); return f }
const fakeVmstat = (root) => { const f = join(root, 'vmstat'); writeFileSync(f, 'balloon_inflate 64896512\nballoon_deflate 63323648\n'); return f }
test('slot_mem_gate: PI_TEST_MEM_AVAIL_MB 9000 / 6500 / 4000 → normal / degraded / skip; docs-only skips the wait', () => {
  const root = mkdtempSync(join(tmpdir(), 'argus-gate-'))
  // 10-30: degraded/skip go through GATE_WAIT_S=$(( SECONDS - start )) (qa-slot.sh:148), 0 or 1 s across a whole second
  const gate = (mb, check = '', swap = 5) => sh(root, `slot_mem_gate ${check}; echo "$GATE_MODE|$GATE_WAIT_S|$GATE_SHARDS"`, { PANOPTES_MEMINFO: fakeMem(root, swap), PANOPTES_TEST: '1', PI_TEST_MEM_AVAIL_MB: String(mb), PANOPTES_MEM_POLL_S: '1', PANOPTES_MEM_WAIT_S: '1' }).stdout.trim()
  assert.equal(gate(9000, 'false'), 'normal|0|2')                       // 内存够：不问文档检查
  assert.match(gate(6500), /^degraded\|[01]\|1$/)                            // 10-28：<8G、≥6G：不等 30 分钟，马上用 1 片跑
  assert.match(gate(4000), /^skip\|[1-9]\d*\|0$/)                     // <6G（不够一片）：照旧等满再跳过
  assert.match(gate(9000, '', 3), /^degraded\|[01]\|1$/)                      // 10-28 PM：swap <4G：告警，按内存最多 1 片
  assert.equal(gate(4000, 'true'), 'docs-only|0|2')                     // 纯文档：不等
  // PI_TEST_MEM_AVAIL_MB 只在 PANOPTES_TEST=1 时生效：真机上设了它也不改读数（这里真读数是假 meminfo 的 1 kB → 跳过，而不是 9000 的正常）
  assert.match(sh(root, 'slot_mem_gate; echo $GATE_MODE', { PANOPTES_MEMINFO: fakeMem(root), PI_TEST_MEM_AVAIL_MB: '9000', PANOPTES_TEST: '', PANOPTES_MEM_POLL_S: '1', PANOPTES_MEM_WAIT_S: '1' }).stdout.trim(), /^skip$/)
  // 10-28 分片↔内存表：2 片 8192 MiB、1 片 6144 MiB；取装得下的最大片数，swap 不够一律 0
  const fit = (mb, swap = 5) => sh(root, 'slot_shards_for_mem', { PANOPTES_MEMINFO: fakeMem(root, swap), PANOPTES_TEST: '1', PI_TEST_MEM_AVAIL_MB: String(mb) }).stdout.trim()
  assert.deepEqual([16000, 8192, 8191, 6144, 6143, 5500, 1000].map((mb) => fit(mb)), ['2', '2', '1', '1', '0', '0', '0'])
  assert.equal(fit(9000, 3), '1'); assert.equal(fit(16000, 0), '1'); assert.equal(fit(6143, 2), '0')
  assert.equal(sh(root, 'slot_mem_gate; echo $GATE_SHARDS', { PANOPTES_MEMINFO: fakeMem(root), PANOPTES_TEST: '1', PI_TEST_MEM_AVAIL_MB: '7200', PANOPTES_MIN_MEM_GB: '7' }).stdout.trim(), '2') // 阈值仍跟 PANOPTES_MIN_MEM_GB
  // 等待期间内存回到够一片：不等满，立刻降级跑
  const grow = sh(root, 'slot_mem_avail_kb() { if (( SECONDS - T0 >= 1 )); then echo $((6500*1024)); else echo $((3000*1024)); fi; }; T0=$SECONDS; slot_mem_gate; echo "$GATE_MODE|$GATE_SHARDS"', { PANOPTES_MEMINFO: fakeMem(root), PANOPTES_MEM_POLL_S: '1', PANOPTES_MEM_WAIT_S: '30' }).stdout.trim()
  assert.equal(grow, 'degraded|1')
  assert.equal(sh(root, 'slot_balloon_mb', { PANOPTES_VMSTAT: fakeVmstat(root) }).stdout.trim(), '6144')
})

// 10-28 PM：SwapTotal 0（机器没有 swap）→ 不等、不退出：告警，只按内存规则跑，最多 1 片；< 6144 照旧等
test('no swap (SwapTotal 0): >= 8192 and 6144-8191 -> 1 shard + alert + memory_gate swap=0; < 6144 waits as before', async () => {
  const root = mkdtempSync(join(tmpdir(), 'argus-noswap-'))
  const f = join(root, 'mi-noswap'); writeFileSync(f, 'MemTotal: 16386368 kB\nMemAvailable: 1 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n')
  const gate = (mb) => sh(root, 'slot_mem_gate; echo "$GATE_MODE|$GATE_SHARDS|$GATE_LOW_SWAP|$GATE_WAIT_S"', { PANOPTES_MEMINFO: f, PANOPTES_TEST: '1', PI_TEST_MEM_AVAIL_MB: String(mb), PANOPTES_MEM_POLL_S: '1', PANOPTES_MEM_WAIT_S: '1' }).stdout.trim()
  assert.match(gate(9000), /^degraded\|1\|1\|[01]$/)
  assert.match(gate(8192), /^degraded\|1\|1\|[01]$/)
  assert.match(gate(7000), /^degraded\|1\|1\|[01]$/)
  assert.match(gate(6144), /^degraded\|1\|1\|[01]$/)
  assert.match(gate(6143), /^skip\|0\|1\|[1-9]\d*$/)
  // end to end: alert on stderr, 1 shard, no wait
  const nvm = join(root, 'nvm'); mkdirSync(nvm); writeFileSync(join(nvm, 'nvm.sh'), 'nvm() { :; }\n')
  const secrets = join(root, 'secrets.env'); writeFileSync(secrets, 'GITHUB_TOKEN=x\nQA_LLM_BASE_URL=http://127.0.0.1:9\nQA_LLM_API_KEY=x\n')
  const r = spawnSync('bash', [join(dirname(SLOT), '..', 'run-nightly.sh'), '--date', '2026-10-27'], { encoding: 'utf8', timeout: 60_000, env: { ...process.env, QA_HOME: join(root, 'qa'),
    PANOPTES_NVM_DIR: nvm, QA_SECRETS_FILE: secrets, PANOPTES_BOOTSTRAP: '0', PANOPTES_TMP_SWEEP: '0', PANOPTES_TEST: '1', PANOPTES_GATE_DRYRUN: '1', PI_TEST_MEM_AVAIL_MB: '9000',
    PANOPTES_MEMINFO: f, PANOPTES_VMSTAT: fakeVmstat(root), PANOPTES_MEM_POLL_S: '1', PANOPTES_MEM_WAIT_S: '1', ARGUS_UNIT_SHARDS: '', ARGUS_MAX_PARALLEL: '' } })
  assert.equal(r.status, 0, r.stderr)
  assert.match(r.stderr, /ALERT SwapFree 0 MiB < 4 GiB: running by memory rules only, max 1 shard \(needs >= 6144 MiB available\)/)
  assert.match(r.stdout, /gate: mode=degraded wait=[01]s shards=1 max_parallel=1/)
  const { memoryGate } = await import('../src/nightly.mjs')
  const mg = memoryGate({ PANOPTES_GATE_MODE: 'degraded', PANOPTES_GATE_SHARDS: '1', PANOPTES_GATE_NORMAL_SHARDS: '2', PANOPTES_GATE_MEM_MB: '9000', PANOPTES_GATE_LOW_SWAP: '1', PANOPTES_GATE_SWAP_FREE_MB: '0' })
  assert.equal(mg.low_swap, true); assert.equal(mg.swap_free_mb, 0); assert.equal(mg.shards, 1); assert.match(mg.swap_alert, /SwapFree < 4 GiB/)
})

// 10-28 PM：swap free 1-3G（这里 2G）+ 9000 MiB → 告警 + 1 片（以前是等满再 exit 76）
test('low swap (SwapFree 2 GiB) with 9000 MiB -> 1 shard + alert, no wait', () => {
  const root = mkdtempSync(join(tmpdir(), 'argus-lowswap-'))
  const f = join(root, 'mi'); writeFileSync(f, `MemTotal: 16386368 kB\nMemAvailable: 1 kB\nSwapTotal: 8388604 kB\nSwapFree: ${2 * 1048576} kB\n`)
  assert.match(sh(root, 'slot_mem_gate; echo "$GATE_MODE|$GATE_SHARDS|$GATE_LOW_SWAP|$GATE_WAIT_S"', { PANOPTES_MEMINFO: f, PANOPTES_TEST: '1', PI_TEST_MEM_AVAIL_MB: '9000', PANOPTES_MEM_POLL_S: '1', PANOPTES_MEM_WAIT_S: '1' }).stdout.trim(), /^degraded\|1\|1\|[01]$/)
  const w = sh(root, 'slot_mem_gate; echo "$GATE_MODE"', { PANOPTES_MEMINFO: f, PANOPTES_TEST: '1', PI_TEST_MEM_AVAIL_MB: '5000', PANOPTES_MEM_POLL_S: '1', PANOPTES_MEM_WAIT_S: '2' })  // 10-30: WAIT_S 2, a 1 s SECONDS gain on the first poll must not skip before the waiting line
  assert.equal(w.stdout.trim(), 'skip'); assert.match(w.stderr, /\(>= 6144 MiB available\), waiting/); assert.doesNotMatch(w.stderr, /swap free/)
  const nvm = join(root, 'nvm'); mkdirSync(nvm); writeFileSync(join(nvm, 'nvm.sh'), 'nvm() { :; }\n')
  const secrets = join(root, 'secrets.env'); writeFileSync(secrets, 'GITHUB_TOKEN=x\nQA_LLM_BASE_URL=http://127.0.0.1:9\nQA_LLM_API_KEY=x\n')
  const qa = join(root, 'qa')
  const r = spawnSync('bash', [join(dirname(SLOT), '..', 'run-nightly.sh'), '--date', '2026-10-27'], { encoding: 'utf8', timeout: 60_000, env: { ...process.env, QA_HOME: qa,
    PANOPTES_NVM_DIR: nvm, QA_SECRETS_FILE: secrets, PANOPTES_BOOTSTRAP: '0', PANOPTES_TMP_SWEEP: '0', PANOPTES_TEST: '1', PANOPTES_GATE_DRYRUN: '1', PI_TEST_MEM_AVAIL_MB: '9000',
    PANOPTES_MEMINFO: f, PANOPTES_VMSTAT: fakeVmstat(root), PANOPTES_MEM_POLL_S: '1', PANOPTES_MEM_WAIT_S: '1', ARGUS_UNIT_SHARDS: '', ARGUS_MAX_PARALLEL: '' } })
  assert.equal(r.status, 0, r.stderr)
  assert.match(r.stderr, /ALERT SwapFree 2048 MiB < 4 GiB/)
  assert.match(r.stderr, /low swap free: SwapFree 2048 MiB < 4096 MiB: degraded run/)
  assert.match(r.stdout, /gate: mode=degraded wait=[01]s shards=1 max_parallel=1/)
  const alert = readFileSync(join(qa, 'argus-root', 'logs', 'control.log'), 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l)).find((e) => e.msg === 'run-nightly low swap alert')
  assert.equal(alert.level, 'warn')
})

// 同样三档走真的 run-nightly.sh（假 nvm / 假密钥 / 临时 QA_HOME，PANOPTES_GATE_DRYRUN=1 在门后停下，不跑测试）
test('run-nightly.sh memory gate end to end: 9000 runs normally, 6500 degraded (1 shard, no parallel), 4000 skipped with exit 76 and the slot released', () => {
  const root = mkdtempSync(join(tmpdir(), 'argus-gate-e2e-'))
  const nvm = join(root, 'nvm'); mkdirSync(nvm); writeFileSync(join(nvm, 'nvm.sh'), 'nvm() { :; }\n')
  const secrets = join(root, 'secrets.env'); writeFileSync(secrets, 'GITHUB_TOKEN=x\nQA_LLM_BASE_URL=http://127.0.0.1:9\nQA_LLM_API_KEY=x\n')
  const script = join(dirname(SLOT), '..', 'run-nightly.sh')
  const run = (mb) => spawnSync('bash', [script, '--date', '2026-10-27'], { encoding: 'utf8', timeout: 60_000, env: { ...process.env, QA_HOME: join(root, `qa-${mb}`),
    PANOPTES_NVM_DIR: nvm, QA_SECRETS_FILE: secrets, PANOPTES_BOOTSTRAP: '0', PANOPTES_TMP_SWEEP: '0', PANOPTES_TEST: '1', PANOPTES_GATE_DRYRUN: '1', PI_TEST_MEM_AVAIL_MB: String(mb),
    PANOPTES_MEMINFO: fakeMem(root), PANOPTES_VMSTAT: fakeVmstat(root), PANOPTES_MEM_POLL_S: '1', PANOPTES_MEM_WAIT_S: '1', ARGUS_UNIT_SHARDS: '', ARGUS_MAX_PARALLEL: '' } })
  let r = run(9000); assert.equal(r.status, 0, r.stderr); assert.match(r.stdout, /gate: mode=normal wait=0s shards=auto max_parallel=auto/)
  r = run(6500); assert.equal(r.status, 0, r.stderr); assert.match(r.stdout, /gate: mode=degraded wait=[01]s shards=1 max_parallel=1/)
  assert.match(r.stderr, /low memory: MemAvailable 6500 MiB < 8192 MiB: degraded run/)
  r = run(4000); assert.equal(r.status, 76, r.stdout + r.stderr); assert.doesNotMatch(r.stdout, /gate: mode=/)
  assert.match(r.stderr, /SKIP MemAvailable 4000 MiB, SwapFree 5120 MiB, balloon 6144 MiB after [1-9]\d*s: below one shard/)
  // 退出后槽位已释放：马上能拿到 nightly.lock
  assert.equal(spawnSync('bash', ['-c', `exec 9>${join(root, 'qa-4000', 'argus-root', 'nightly.lock')}; flock -n 9 && echo free`], { encoding: 'utf8' }).stdout.trim(), 'free')
})

// 10-29 gate_wait_s 累计每次尝试：单 PR 运行把内存门等待加到 argus-root/gate-wait/PR-N 上；有 merged 报告后从 0 开始
test('run-nightly.sh --pr N: gate wait accumulates across attempts, restarts once the merged report exists', () => {
  const root = mkdtempSync(join(tmpdir(), 'argus-gate-acc-'))
  const nvm = join(root, 'nvm'); mkdirSync(nvm); writeFileSync(join(nvm, 'nvm.sh'), 'nvm() { :; }\n')
  const secrets = join(root, 'secrets.env'); writeFileSync(secrets, 'GITHUB_TOKEN=x\nQA_LLM_BASE_URL=http://127.0.0.1:9\nQA_LLM_API_KEY=x\n')
  const qa = join(root, 'qa'), gw = join(qa, 'argus-root', 'gate-wait')   // not pre-created
  const script = join(dirname(SLOT), '..', 'run-nightly.sh')
  const run = () => spawnSync('bash', [script, '--pr', '7', '--date', '2026-10-27'], { encoding: 'utf8', timeout: 60_000, env: { ...process.env, QA_HOME: qa,
    PANOPTES_NVM_DIR: nvm, QA_SECRETS_FILE: secrets, PANOPTES_BOOTSTRAP: '0', PANOPTES_TMP_SWEEP: '0', PANOPTES_TEST: '1', PANOPTES_GATE_DRYRUN: '1', PI_TEST_MEM_AVAIL_MB: '9000',
    PANOPTES_MEMINFO: fakeMem(root), PANOPTES_VMSTAT: fakeVmstat(root), PANOPTES_MEM_POLL_S: '1', PANOPTES_MEM_WAIT_S: '1', ARGUS_UNIT_SHARDS: '', ARGUS_MAX_PARALLEL: '' } })
  // 10-30: normal mode returns before the SECONDS - start timing (qa-slot.sh:144), so wait here is exact, no 1801/1802 tolerance needed
  // first attempt of a new PR: no gate-wait file yet must not trip set -e (#92 acceptance blocker)
  let r = run(); assert.equal(r.status, 0, r.stdout + r.stderr); assert.match(r.stdout, /gate: mode=normal wait=0s/)
  assert.equal(readFileSync(join(gw, 'PR-7'), 'utf8').trim(), '0')
  writeFileSync(join(gw, 'PR-7'), '1801\n')   // an earlier skipped attempt waited 1801 s
  r = run(); assert.equal(r.status, 0, r.stderr); assert.match(r.stdout, /gate: mode=normal wait=1801s/)
  assert.equal(readFileSync(join(gw, 'PR-7'), 'utf8').trim(), '1801')
  mkdirSync(join(qa, 'reports', 'merged'), { recursive: true }); writeFileSync(join(qa, 'reports', 'merged', 'PR-7.json'), '{}')
  r = run(); assert.equal(r.status, 0, r.stderr); assert.match(r.stdout, /gate: mode=normal wait=0s/)
})

// merge of #92 + #93: low-swap degraded run and the accumulated gate wait both show up
test('run-nightly.sh --pr N: low swap degraded run keeps the accumulated gate wait (alert + reason + title)', () => {
  const root = mkdtempSync(join(tmpdir(), 'argus-gate-acc-swap-'))
  const nvm = join(root, 'nvm'); mkdirSync(nvm); writeFileSync(join(nvm, 'nvm.sh'), 'nvm() { :; }\n')
  const secrets = join(root, 'secrets.env'); writeFileSync(secrets, 'GITHUB_TOKEN=x\nQA_LLM_BASE_URL=http://127.0.0.1:9\nQA_LLM_API_KEY=x\n')
  const qa = join(root, 'qa'), gw = join(qa, 'argus-root', 'gate-wait'); mkdirSync(gw, { recursive: true }); writeFileSync(join(gw, 'PR-8'), '1801\n')
  const r = spawnSync('bash', [join(dirname(SLOT), '..', 'run-nightly.sh'), '--pr', '8', '--date', '2026-10-27'], { encoding: 'utf8', timeout: 60_000, env: { ...process.env, QA_HOME: qa,
    PANOPTES_NVM_DIR: nvm, QA_SECRETS_FILE: secrets, PANOPTES_BOOTSTRAP: '0', PANOPTES_TMP_SWEEP: '0', PANOPTES_TEST: '1', PANOPTES_GATE_DRYRUN: '1', PI_TEST_MEM_AVAIL_MB: '9000',
    PANOPTES_MEMINFO: fakeMem(root, 2), PANOPTES_VMSTAT: fakeVmstat(root), PANOPTES_MEM_POLL_S: '1', PANOPTES_MEM_WAIT_S: '1', ARGUS_UNIT_SHARDS: '', ARGUS_MAX_PARALLEL: '' } })
  assert.equal(r.status, 0, r.stdout + r.stderr)
  // 10-30: this attempt's own gate wait is SECONDS - start, 0 or 1 s if the clock crosses a whole second -> 1801 or 1802
  const w = r.stdout.match(/gate: mode=degraded wait=(180[12])s shards=1 max_parallel=1/)?.[1]
  assert.ok(w, r.stdout)
  assert.match(r.stderr, /ALERT SwapFree 2048 MiB < 4 GiB/)
  assert.match(r.stderr, new RegExp(`after ${w}s: low swap free: SwapFree 2048 MiB < 4096 MiB: degraded run with 1 of 2 shards`))
  assert.equal(readFileSync(join(gw, 'PR-8'), 'utf8').trim(), w)
  const log = readFileSync(join(qa, 'argus-root', 'logs', 'control.log'), 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l))
  assert.equal(log.find((e) => e.msg === 'run-nightly low swap alert').level, 'warn')
  assert.ok(log.some((e) => e.msg === 'run-nightly low swap free, degraded'))
  assert.ok(!log.some((e) => e.msg === 'run-nightly low memory, degraded'))
})

// 10-28 合并即测 SLA：按 PR 加锁，内存够（slot_shards_for_mem ≥ 1 片）就和占槽的运行并行，不够就照旧排队
test('on-merge: slot busy + memory ok -> concurrent; memory low -> keeps waiting (gives up); same PR twice -> refused', async () => {
  const root = mkdtempSync(join(tmpdir(), 'argus-slot-')); mkdirSync(join(root, 'priority'))
  const mi = (avail, swapKb = 5 * 1048576) => { const f = join(root, `mi-${avail}-${swapKb}`); writeFileSync(f, `MemTotal: 16000000 kB\nMemAvailable: ${avail} kB\nSwapTotal: 8388604 kB\nSwapFree: ${swapKb} kB\n`); return f }
  const vm = (mb) => { const f = join(root, `vm-${mb}`); writeFileSync(f, `balloon_inflate ${mb * 256 + 1000}\nballoon_deflate 1000\n`); return f }
  const h = holder(root); await sleepMs(300)
  try {
    let r = sh(root, `exec 9>$ARGUS_ROOT/nightly.lock; slot_acquire_onmerge 9 101 5 && echo $SLOT_MODE; ls $ARGUS_ROOT/priority | grep -c conc; slot_release_priority; ls $ARGUS_ROOT/priority | grep -c 'conc\\|req'`, { PANOPTES_MEMINFO: mi(9 * 1048576), PANOPTES_VMSTAT: vm(0) })
    assert.deepEqual(r.stdout.trim().split('\n'), ['concurrent', '1', '0'])
    // 10-28: balloon > 2G -> no second concurrent run even with 9G available (keeps waiting, gives up)
    r = sh(root, `exec 9>$ARGUS_ROOT/nightly.lock; slot_acquire_onmerge 9 105 1 && echo $SLOT_MODE || echo gave-up`, { PANOPTES_MEMINFO: mi(9 * 1048576), PANOPTES_VMSTAT: vm(3072) })
    assert.equal(r.stdout.trim(), 'gave-up')
    assert.match(r.stderr, /balloon 3072 MiB > 2048 MiB: no second concurrent run/)
    r = sh(root, `exec 9>$ARGUS_ROOT/nightly.lock; slot_acquire_onmerge 9 106 5 && echo $SLOT_MODE; slot_release_priority`, { PANOPTES_MEMINFO: mi(9 * 1048576), PANOPTES_VMSTAT: vm(2048) })
    assert.equal(r.stdout.trim(), 'concurrent')
    // 10-28 PM：swap free 2G + 9000 MiB → 仍放行（1 片）
    r = sh(root, `exec 9>$ARGUS_ROOT/nightly.lock; slot_acquire_onmerge 9 107 5 && echo $SLOT_MODE; slot_release_priority`, { PANOPTES_MEMINFO: mi(9000 * 1024, 2 * 1048576), PANOPTES_VMSTAT: vm(0) })
    assert.equal(r.stdout.trim(), 'concurrent')
    r = sh(root, `exec 9>$ARGUS_ROOT/nightly.lock; slot_acquire_onmerge 9 102 1 && echo $SLOT_MODE || echo gave-up`, { PANOPTES_MEMINFO: mi(6143 * 1024) })
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

// 10-28：降级记进报告（run-nightly.sh 导出 PANOPTES_GATE_* → 每个 PR 的 memory_gate）
test('memoryGate: report records degraded run (shards used vs normal, MemAvailable MB); null outside run-nightly.sh', async () => {
  const { memoryGate } = await import('../src/nightly.mjs')
  assert.deepEqual(memoryGate({ PANOPTES_GATE_MODE: 'degraded', PANOPTES_GATE_SHARDS: '1', PANOPTES_GATE_NORMAL_SHARDS: '2', PANOPTES_GATE_MEM_MB: '5500' }),
    { mode: 'degraded', degraded: true, shards: 1, normal_shards: 2, mem_available_mb: 5500 })
  assert.equal(memoryGate({ PANOPTES_GATE_MODE: 'normal', PANOPTES_GATE_SHARDS: '2', PANOPTES_GATE_NORMAL_SHARDS: '2', PANOPTES_GATE_MEM_MB: '9000' }).degraded, false)
  assert.equal(memoryGate({}), null)
})

// 10-28：降级时 lint（首轮）和单测 vitest（覆盖率轮次）不并行；正常内存照旧并行
test('parallelDecision: memory gate degraded -> lint and unit tests serialized even with ARGUS_MAX_PARALLEL=2 and plenty of memory', async () => {
  const { parallelDecision } = await import('../src/nightly.mjs')
  assert.deepEqual(parallelDecision({ freeGb: 12, minGb: 8, maxParallel: 2, gateMode: 'degraded' }), { parallel: false, reason: 'memory gate degraded: lint and unit tests serialized' })
  assert.equal(parallelDecision({ freeGb: 12, minGb: 8, maxParallel: 2, gateMode: 'normal' }).parallel, true)
  assert.equal(parallelDecision({ freeGb: 12, minGb: 8, maxParallel: 2, gateMode: undefined }).parallel, true)
})

// 10-28：运行中内存低水位 < 1536 MiB → 失败记环境问题（infra_failed），不算测试失败
test('low-memory low-water mark: sampler keeps the minimum; < 1536 MiB turns a failure into infra_failed, passes untouched', async () => {
  const { startMemLowWater, lowMemoryHit } = await import('../src/memory.mjs')
  const { lowMemoryEnvProblem, verdictOf } = await import('../src/nightly.mjs')
  const seq = [4000, 1200, 3000]; let i = 0
  const lw = startMemLowWater({ intervalMs: 5, read: () => seq[Math.min(i++, seq.length - 1)] })
  await new Promise((r) => setTimeout(r, 40))
  assert.equal(lw.stop(), 1200)
  assert.equal(lowMemoryHit(1535, {}), true); assert.equal(lowMemoryHit(1536, {}), false); assert.equal(lowMemoryHit(null, {}), false)
  const lm = lowMemoryEnvProblem('defect', 1200, {})
  assert.equal(lm.outcome, 'infra_failed'); assert.match(lm.message, /1200 MiB \(< 1536 MiB\).*environment problem/)
  assert.equal(verdictOf({ outcome: lm.outcome, findings: [], checks: [] }), 'qa-env-problem')
  assert.equal(lowMemoryEnvProblem('needs_review', 1500, {}).outcome, 'infra_failed')
  assert.equal(lowMemoryEnvProblem('passed', 1000, {}), null)     // 通过就是通过
  assert.equal(lowMemoryEnvProblem('defect', 2000, {}), null)     // 内存够：失败照旧算
  assert.equal(lowMemoryEnvProblem('defect', 1900, { ARGUS_LOW_MEM_MB: '2048' }).outcome, 'infra_failed')
})
