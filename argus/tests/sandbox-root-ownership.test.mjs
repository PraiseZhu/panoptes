// 盒子把 / 归给非 0 且非控制器自己的 uid（容器 root 压缩 → 65534 nobody）：
// 目标仓里「父目录必须由控制器独占管理」「控制器启动路径不可信」这类信任断言从 / 开始逐级检查，
// 于是所有路径都被拒，测试在 PR 版本和基线版本上一起失败 → panoptes 记 pre_existing/P3，
// infrafix 又按「检查在基线版本上也失败」成单（PR #1449/#1450/#1454 各 30 条，2026-10-30）。
// 这类失败是盒子条件，不是代码，也不是 panoptes 的检查写错；env 探测必须说出来（同类先例：checkUmask）。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, statSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { basename, join } from 'node:path'
import { tmpdir } from 'node:os'

const NIGHTLY = new URL('../src/nightly.mjs', import.meta.url).href
const RUNNER = new URL('../src/runner.mjs', import.meta.url).href

const fmtMode = (mode) => (mode & 0o7777).toString(8).padStart(4, '0')
const isTrusted = (uid, mode, owner) => [0, owner].includes(uid) && (!(mode & 0o022) || (uid === 0 && Boolean(mode & 0o1000)))

test('envSnapshot 记录根目录的所有者和权限位：容器把 root 压缩成 nobody 时，权限类断言从 / 就开始失败', async () => {
  const { envSnapshot } = await import(NIGHTLY)
  const env = envSnapshot()
  const root = statSync('/')
  assert.equal(env.root_uid, root.uid, 'system_health.environment.root_uid 必须是被测进程看到的 / 所有者 uid')
  assert.equal(env.root_gid, root.gid)
  assert.equal(env.root_mode, fmtMode(root.mode))
  assert.equal(env.uid, process.getuid(), '还要记下跑检查的 uid：/ 由 0 或它自己持有才算可信')
})

test('checkRootOwnership：/ 由 root 或控制器自己持有且组/他人不可写时无 env_problem，被压缩或可写时必须报一条', async () => {
  const { checkRootOwnership } = await import(NIGHTLY)
  const ok = { environment: { uid: 1000, root_uid: 0, root_gid: 0, root_mode: '0755' }, env_problems: [] }
  checkRootOwnership(ok)
  assert.deepEqual(ok.env_problems, [])
  const selfOwned = { environment: { uid: 1000, root_uid: 1000, root_gid: 1000, root_mode: '0755' }, env_problems: [] }
  checkRootOwnership(selfOwned)
  assert.deepEqual(selfOwned.env_problems, [])
  // root 压缩：/ 归 nobody(65534)，既不是 0 也不是控制器自己的 uid
  const squashed = { environment: { uid: 1000, root_uid: 65534, root_gid: 65534, root_mode: '0755' }, env_problems: [] }
  checkRootOwnership(squashed)
  assert.equal(squashed.env_problems.length, 1)
  const msg = squashed.env_problems[0].message
  assert.match(msg, /65534/, 'env_problem 要点名实际所有者，运维才知道该改什么')
  assert.match(msg, /uid 0/)
  assert.match(msg, /permission|trust/i, '要说明这类失败是盒子条件造成的假失败，不是被测代码的问题')
  // 组/他人可写的 / 同样让信任断言失败
  const writable = { environment: { uid: 1000, root_uid: 0, root_gid: 0, root_mode: '0777' }, env_problems: [] }
  checkRootOwnership(writable)
  assert.equal(writable.env_problems.length, 1)
  assert.match(writable.env_problems[0].message, /0777|writable/i)
})

test('沙箱里被测进程看到的 / 所有者与 envSnapshot 一致；不可信时「父目录必须由控制器独占管理」在所有路径上都失败', async () => {
  const { envSnapshot, checkRootOwnership } = await import(NIGHTLY)
  const { sandboxMode, bwrapArgs } = await import(RUNNER)
  const env = envSnapshot()
  const host = statSync('/')
  // mivo scripts/ops/review-capacity-check/workspace.mjs:28-42（assertControllerDirectory）与 run.sh trusted_path：
  // 从 / 开始逐级要求 owner ∈ {0, EUID} 且组/他人不可写（root 持有时允许 sticky）
  const probe = `
const fs = require('node:fs'), os = require('node:os'), path = require('node:path')
const owner = process.getuid()
const trusted = (p) => { const i = fs.statSync(p); return ([0, owner].includes(i.uid) && (!(i.mode & 0o022) || (i.uid === 0 && Boolean(i.mode & 0o1000)))) }
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mivo-capacity-workspace-'))
const work = path.join(root, 'spill/capacity-test/gha-mivo-review-16')
fs.mkdirSync(path.dirname(work), { recursive: true })
let rejected = false
try { for (const p of new Set(['/', root, path.dirname(work)])) if (!trusted(p)) throw new Error('untrusted ' + p) }
catch { rejected = true }
const s = fs.statSync('/')
console.log(JSON.stringify({ rootUid: s.uid, rootGid: s.gid, rootMode: (s.mode & 0o7777).toString(8).padStart(4, '0'), rejected }))
`
  // 默认用本机视图；Linux + bwrap（单元测试真跑的沙箱）时取被测进程看到的那份
  let seen = { rootUid: host.uid, rootGid: host.gid, rootMode: fmtMode(host.mode), rejected: null }
  const runDir = mkdtempSync(join(tmpdir(), 'argus-root-owner-'))
  try {
    if (process.platform === 'linux' && sandboxMode({}) === 'bwrap') {
      const argv = [process.env.ARGUS_BWRAP_BIN ?? '/usr/bin/bwrap', ...bwrapArgs(runDir, { chdir: runDir }), '--', process.execPath, '-e', probe]
      seen = JSON.parse(execFileSync(argv[0], argv.slice(1), { encoding: 'utf8', timeout: 60_000 }).trim().split('\n').pop())
    }
  } finally {
    rmSync(runDir, { recursive: true, force: true })
    rmSync(join(tmpdir(), `argus-${basename(runDir)}`), { recursive: true, force: true })
  }
  // env 探测必须和被测进程看到的一致，不然 env_problem 指不到失败的真实原因
  assert.equal(seen.rootUid, env.root_uid, 'envSnapshot.root_uid 必须等于沙箱内进程看到的 / 所有者')
  assert.equal(seen.rootGid, env.root_gid)
  assert.equal(seen.rootMode, env.root_mode)
  if (seen.rejected !== null) {
    assert.equal(seen.rejected, !isTrusted(seen.rootUid, Number.parseInt(seen.rootMode, 8), process.getuid()),
      '信任断言只应因 / 的所有者或权限位不可信而整体失败')
  }
  // 本机（容器 root 压缩）就在这个状态：/ 归 65534 → 这类测试在基线上也必然失败，必须进 env_problems
  const health = { environment: env, env_problems: [] }
  checkRootOwnership(health)
  assert.equal(health.env_problems.length, isTrusted(host.uid, host.mode, process.getuid()) ? 0 : 1)
})
