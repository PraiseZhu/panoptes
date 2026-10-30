// 沙箱里「步骤临时目录的祖先链」也必须通过被测仓的权限断言。
//
// mivo 的 scripts/ops/review-capacity-check/workspace.mjs（assertControllerDirectory）在打开工作目录前逐层核对祖先：
// uid 必须是 root 或运行用户；组/其他可写位只有「root 且 sticky」才放行。
// panoptes 用 bwrap 跑单测，而 bwrap 的用户命名空间只映射运行用户，宿主的 root 在沙箱里是溢出 uid 65534：
// 祖先链的第一层「/」就不合格，于是 mivo 的用例抛「父目录必须由控制器独占管理」，
// PR #1449 / #1424 / #1447 的单测都记了一条「在基线版本上也失败」的假失败（PR-1449.json 的 log_tail 是这条错误）。
//
// 这个测试把同一条判据搬进 panoptes 的沙箱（用真实的 bwrapArgs），要求整条祖先链可接受：
// 沙箱不能把宿主的 root 变成谁也认不出的 uid，否则所有「父目录由控制器独占管理」的断言都会假失败。
// 要让它变绿：沙箱的根目录必须对「root 或运行用户」可辨认（沙箱自己造根、改归属映射），
// 或者这类步骤根本不进「只映射运行用户」的用户命名空间沙箱——现状是 --ro-bind / / 直接借宿主的根。
// 同一根因还打掉了 mivo 的 cleanup-identity 用例（同为「父目录必须由控制器独占管理」，已保留目录在 step-unit/ 下）。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-sbx-own-'))
const { bwrapArgs, sandboxMode } = await import('../src/runner.mjs')
const PROFILE = JSON.parse(readFileSync(new URL('../config/profiles/mivo-canvas-plugin.json', import.meta.url), 'utf8'))
const BWRAP = process.env.ARGUS_BWRAP_BIN ?? '/usr/bin/bwrap'

// 与 mivo workspace.mjs 的 assertControllerDirectory 同一判据（root 或运行用户独占管理；root+sticky 例外）
function acceptedByControllerCheck({ uid, mode }, owner = process.getuid()) {
  if (![0, owner].includes(uid)) return false
  if (!(mode & 0o022)) return true
  return uid === 0 && (mode & 0o1000) !== 0
}

// 夹具与 mivo 的隔离用例同形：<TMPDIR>/mivo-sealed-workspace-*/spill/capacity-sealed-private，
// 打开目录前逐层验证祖先（第一层就是 /）。只打印每层的 uid/mode，判据留在测试里。
const PROBE = `
import { constants, closeSync, fstatSync, mkdirSync, mkdtempSync, openSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'

const FLAGS = constants.O_RDONLY | constants.O_DIRECTORY
const root = mkdtempSync(join(tmpdir(), 'mivo-sealed-workspace-'))
mkdirSync(join(root, 'spill'))
const parent = dirname(join(root, 'spill', 'capacity-sealed-private'))
const levels = []
let current = '/'
const rootFd = openSync(current, FLAGS)
levels.push([current, fstatSync(rootFd)])
closeSync(rootFd)
for (const part of parent.split('/').filter(Boolean)) {
  current = join(current, part)
  const fd = openSync(current, FLAGS)
  levels.push([current, fstatSync(fd)])
  closeSync(fd)
}
console.log(JSON.stringify({ tmpdir: tmpdir(), uid: process.getuid(), levels: levels.map(([path, info]) => ({ path, uid: info.uid, mode: info.mode & 0o7777 })) }))
`.trim()

test('控制器独占判据与 mivo 一致：root+sticky 放行，其余组/其他可写与外部 uid 一律拒绝', () => {
  assert.equal(acceptedByControllerCheck({ uid: 0, mode: 0o41777 }, 1000), true)   // 开发机/GitHub CI 的 /tmp
  assert.equal(acceptedByControllerCheck({ uid: 0, mode: 0o755 }, 1000), true)     // 开发机/GitHub CI 的 /
  assert.equal(acceptedByControllerCheck({ uid: 1000, mode: 0o755 }, 1000), true)  // 自己的目录
  assert.equal(acceptedByControllerCheck({ uid: 1000, mode: 0o775 }, 1000), false) // 组可写（umask 002 的旧假失败）
  assert.equal(acceptedByControllerCheck({ uid: 1000, mode: 0o1777 }, 1000), false) // 非 root 持有的 sticky 世界可写
  assert.equal(acceptedByControllerCheck({ uid: 65534, mode: 0o755 }, 1000), false) // bwrap 里的 /
})

test('bwrap 沙箱里 TMPDIR 祖先链必须通过被测仓的「控制器独占」检查', { skip: process.platform !== 'linux' || sandboxMode(PROFILE) !== 'bwrap' ? 'not a bwrap sandbox on this platform' : false }, (t) => {
  const runDir = mkdtempSync(join(tmpdir(), 'argus-sbx-own-'))
  t.after(() => rmSync(runDir, { recursive: true, force: true }))
  const stepTmp = join(runDir, 'step-unit')
  mkdirSync(stepTmp, { recursive: true })
  mkdirSync(join(runDir, 'home'), { recursive: true })
  const probe = join(runDir, 'ancestor-probe.mjs')
  writeFileSync(probe, PROBE)

  const res = spawnSync(BWRAP, [...bwrapArgs(runDir), '--', process.execPath, probe], {
    encoding: 'utf8',
    env: { ...process.env, TMPDIR: stepTmp, HOME: join(runDir, 'home') },
  })
  assert.equal(res.status, 0, `沙箱里的探针没跑成：${String(res.stderr).slice(-500)}`)
  const seen = JSON.parse(res.stdout.trim().split('\n').pop())
  assert.equal(seen.tmpdir, stepTmp, '探针应把 TMPDIR 指到这一步的临时目录（runner 的 stepEnv 就是这么设的）')

  const rejected = seen.levels.filter((level) => !acceptedByControllerCheck(level, seen.uid))
  assert.deepEqual(rejected, [], `沙箱把 TMPDIR 祖先链上的目录变成了 root 和运行用户（uid ${seen.uid}）之外的归属。`
    + '被测仓的 assertControllerDirectory 会在第一层就抛「父目录必须由控制器独占管理」，把整条用例记成「基线也失败」的假失败。')
})
