// 2026-10-30 夜间（PR-1424 / 1447 / 1449 / 1450 / 1454 的 h1/logs/unit.log，5 次运行）：mivo 仓
// scripts/ops/review-capacity-check/*.native.test.mjs 整族（30 例）在每次运行的被测版本和合并前版本上都失败，
// 报告把它们记成 pre_existing 的「单元测试「…workspace-creation.native.test.mjs > 内部临时目录准备失败仍交回
// 工作目录以便槽位清理：tmp-mkdir」在基线版本上也失败」，infrafix 因此反复成单（inf1-98572bf8）。
//
// 那些用例的判据是「清理路径的父链必须由控制器独占管理」：mivo workspace.mjs 的 assertControllerDirectory 从 /
// 起逐层要求属主是 root 或运行用户（root 还得带 sticky 才允许 group/other 可写），run.sh 的启动路径信任同样要求
// owner==0 || owner==EUID。宿主上这些层都满足，GitHub CI 也绿。
//
// 但 bwrap 的用户命名空间只映射运行用户（/proc/self/uid_map 只有 "1000 1000 1" 一行），宿主 root 在沙箱里读成
// 溢出 uid 65534：`--ro-bind / /` 把宿主根搬进来以后，链的第一层 / 就是 uid 65534，既不是 0 也不是运行用户，
// assertControllerDirectory 立刻抛「父目录必须由控制器独占管理」。openWorkspace 抛错 → created=false，
// 于是「内部临时目录准备失败仍交回工作目录以便槽位清理：tmp-mkdir」里的 expect(result.created).toBe(true) 得到
// false。同一原因也解释了 launcher.native.test.mjs 的七例：run.sh 在模块检查之前就以「控制器启动路径不可信，
// 拒绝加载模块」退出，而用例期望的是「控制器模块不可信」。
//
// 这里用真实的 bwrap 参数（bwrapArgs）在沙箱里跑一段探针：沙箱的根和整条工作区父链必须能被 mivo 的判据接住。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'

// paths.mjs 在 import 时定下 RUNS_DIR / CACHE_DIR：先指到临时根，别碰本机真实缓存
process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-sandbox-root-'))
const { bwrapArgs, sandboxMode, stepEnv } = await import('../src/runner.mjs')

const BWRAP = process.env.ARGUS_BWRAP_BIN ?? '/usr/bin/bwrap'
const linuxBwrap = process.platform === 'linux' && sandboxMode() === 'bwrap'
const skip = linuxBwrap ? false : 'needs bwrap on Linux'

// 探针：按 mivo 的判据从 / 逐层核对工作区父链，然后照 openPrivateDirectory 的做法建 0700 工作目录。
// 等价于 workspace-creation.native.test.mjs 里 openWorkspace(unit, root) 的前半段。
const PROBE = String.raw`
import { closeSync, fstatSync, mkdirSync, mkdtempSync, openSync, rmdirSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { tmpdir } from 'node:os'

const uid = process.getuid()
// mivo scripts/ops/review-capacity-check/workspace.mjs › assertControllerDirectory（逐字同判据）
const managed = (info) => [0, uid].includes(info.uid) && (!(info.mode & 0o022) || (info.uid === 0 && (info.mode & 0o1000) !== 0))
const root = mkdtempSync(join(tmpdir(), 'mivo-workspace-creation-'))
const leaf = join(root, 'spill', 'capacity-prepare', 'gha-mivo-review-1')
mkdirSync(dirname(leaf), { recursive: true })
const chain = []
for (let path = dirname(leaf); ; path = dirname(path)) {
  const info = statSync(path)
  chain.unshift({ path, uid: info.uid, mode: (info.mode & 0o7777).toString(8), managed: managed(info) })
  if (path === '/') break
}
// openPrivateDirectory：在父句柄下建 0700 目录并核对属主与权限位，然后放开
let created = false, mode = null
try {
  mkdirSync(leaf, { mode: 0o700 })
  const descriptor = openSync(leaf, 'r')
  const info = fstatSync(descriptor)
  closeSync(descriptor)
  created = info.uid === uid && (info.mode & 0o777) === 0o700
  mode = (info.mode & 0o777).toString(8)
  rmdirSync(leaf)
} catch { created = false }
const rootInfo = statSync('/')
console.log(JSON.stringify({ uid, tmpdir: tmpdir(), sandboxRoot: { uid: rootInfo.uid, mode: (rootInfo.mode & 0o7777).toString(8) }, chain, created, mode }))
`

function probeInSandbox() {
  const runDir = mkdtempSync(join(tmpdir(), 'argus-sbxroot-'))
  mkdirSync(join(runDir, 'home'), { recursive: true })
  const probeFile = join(runDir, 'probe.mjs')
  writeFileSync(probeFile, PROBE)
  // 和真实步骤同一份环境：HOME / TMPDIR 都在本次 run 里，bwrapArgs 已把这两个目录挂回来
  const env = { ...stepEnv(runDir, {}), PATH: process.env.PATH }
  const res = spawnSync(BWRAP, [...bwrapArgs(runDir), '--', process.execPath, probeFile], { encoding: 'utf8', env, timeout: 60_000 })
  assert.equal(res.error, undefined, String(res.error))
  assert.equal(res.status, 0, res.stderr)
  return JSON.parse(res.stdout.trim().split('\n').pop())
}

test('步骤沙箱的根归运行用户，宿主 root 不能读成溢出 uid 65534', { skip }, () => {
  const r = probeInSandbox()
  // bwrap 只映射运行用户：宿主 root 在沙箱里就是 nobody(65534)，被测仓按属主判断父链是否可信时第一层就崩
  assert.equal(r.sandboxRoot.uid, r.uid,
    `沙箱里的 / 属主是 ${r.sandboxRoot.uid}（运行用户 ${r.uid}）：宿主 root 被读成溢出 uid，mivo 的 assertControllerDirectory 会拒绝整条父链`)
  assert.notEqual(r.sandboxRoot.uid, 65534)
})

test('工作区父链全部由控制器独占管理：tmp-mkdir 一族不再在沙箱里假失败', { skip }, () => {
  const r = probeInSandbox()
  const rejected = r.chain.filter((layer) => !layer.managed)
  assert.deepEqual(rejected, [],
    `这些父层在沙箱里不是 root/运行用户的（workspace-creation.native.test.mjs：内部临时目录准备失败仍交回工作目录以便槽位清理）：${JSON.stringify(rejected)}`)
  // openWorkspace 成功才会把 unit.created 置 true，也就是用例里 expect(result.created).toBe(true)
  assert.equal(r.created, true, `工作目录没能按 0700 建好（mode=${r.mode}）`)
  assert.equal(r.mode, '700')
})
