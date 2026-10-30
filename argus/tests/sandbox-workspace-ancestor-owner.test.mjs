// 沙箱里「这一步的临时目录 → 工作区目录」的祖先链必须通过被测仓的「控制器独占」判据。
//
// 出问题的用例：mivo scripts/ops/review-capacity-check/workspace-creation.native.test.mjs 的
// 「内部临时目录准备失败仍交回工作目录以便槽位清理：tmp-open」（同族还有 :tmp-mkdir / :tmp-chown）。
// 它在子进程里建 <TMPDIR>/mivo-workspace-creation-*/spill/capacity-prepare/gha-mivo-review-1，再调
// workspace.mjs 的 openWorkspace → openPrivateDirectory → openDirectory(dirname(work), assertControllerDirectory)：
// 打开目录之前从 / 开始逐层核对祖先，uid 必须是 root(0) 或运行用户，组/其他可写位只有「root 且 sticky」才放行。
// panoptes 用 bwrap 跑单元测试，而 bwrap 的用户命名空间只映射运行用户：宿主的 root 在沙箱里是溢出 uid 65534，
// 于是祖先链第一层「/」就不合格 → 抛「父目录必须由控制器独占管理」，unit.created 没被置上，
// 用例断言 expect(result.created).toBe(true) 失败（证据 PR-1454.json failed_checks[17].log_tail）。
// 首跑、重跑和基线版本一起失败，报告只能记成「单元测试「…：tmp-open」在基线版本上也失败」（PR #1454 / #1450 /
// #1449 / #1424 同一条），infrafix 每 7 天又按检查项级故障重开工单。
//
// 这里把同一条判据搬进 panoptes 的沙箱：沙箱参数用生产同一份 bwrapArgs，逐层只打印 / 到父目录的 uid 与 mode，
// 判据留在测试里。要让它变绿：沙箱的根由本次 run 自己造（归运行用户），再把宿主 / 的顶层逐项只读挂回
// （--tmpfs / + --ro-bind/--symlink 各条目 + --remount-ro /），或者这类步骤不要进「只映射运行用户」的
// 用户命名空间沙箱——现状是 --ro-bind / / 直接借了宿主的根。
//
// 注意：这不是把被测仓的判据放松。__init__ 那条「判据本身」的用例把 mivo/GitHub CI 的真实值固定住：
// 开发机的 / 是 root 的 0755、/tmp 是 root 的 1777（sticky）都放行，uid 65534 与组可写一律拒绝。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, join } from 'node:path'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-sbx-ws-'))
const { bwrapArgs, sandboxMode } = await import('../src/runner.mjs')
const BWRAP = process.env.ARGUS_BWRAP_BIN ?? '/usr/bin/bwrap'

// 与 mivo workspace.mjs:41-43 的 assertControllerDirectory 同一判据：
// 属主必须是 root 或运行用户；组/其他可写位只有「root 且带 sticky 位」才放行（例如开发机的 /tmp 1777）。
function acceptedByControllerCheck({ uid, mode }, owner = process.getuid()) {
  if (![0, owner].includes(uid)) return false
  if (!(mode & 0o022)) return true
  return uid === 0 && (mode & 0o1000) !== 0
}

const show = (level) => `${level.path} uid=${level.uid} mode=0${level.mode.toString(8)}`

// 夹具与 mivo 的 workspace-creation 用例同形（TMPDIR 由 stepEnv 指到这一步的临时目录）；
// 只打印从 / 到 dirname(work) 每一层的 uid/mode，判据不搬进子进程。
const PROBE = `
import { constants, closeSync, fstatSync, mkdirSync, mkdtempSync, openSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'

const FLAGS = constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW
const root = mkdtempSync(join(tmpdir(), 'mivo-workspace-creation-'))
mkdirSync(join(root, 'spill'))
mkdirSync(join(root, 'spill', 'capacity-prepare'))
const work = join(root, 'spill', 'capacity-prepare', 'gha-mivo-review-1')
const parent = dirname(work)
const levels = []
let current = '/'
const first = openSync(current, FLAGS)
levels.push([current, fstatSync(first)])
closeSync(first)
for (const part of parent.split('/').filter(Boolean)) {
  current = join(current, part)
  const fd = openSync(current, FLAGS)
  levels.push([current, fstatSync(fd)])
  closeSync(fd)
}
console.log(JSON.stringify({ tmpdir: tmpdir(), work, uid: process.getuid(),
  levels: levels.map(([path, info]) => ({ path, uid: info.uid, mode: info.mode & 0o7777 })) }))
`.trim()

test('控制器独占判据不改口径：root 或本人拥有且不可组写才放行，沙箱里的 65534 一律拒绝', () => {
  assert.equal(acceptedByControllerCheck({ uid: 0, mode: 0o755 }, 1000), true, '开发机 / 或 GitHub CI 的 root 0755')
  assert.equal(acceptedByControllerCheck({ uid: 0, mode: 0o41777 }, 1000), true, '开发机的 /tmp：root + sticky 世界可写')
  assert.equal(acceptedByControllerCheck({ uid: 1000, mode: 0o700 }, 1000), true, '自己建的 0700 工作区父目录')
  assert.equal(acceptedByControllerCheck({ uid: 65534, mode: 0o755 }, 1000), false, 'bwrap 里 root 属主变成的溢出 uid')
  assert.equal(acceptedByControllerCheck({ uid: 1000, mode: 0o775 }, 1000), false, '组可写（umask 002 那次假失败）')
})

test('bwrap 沙箱里 TMPDIR→工作目录的祖先链必须通过「控制器独占」判据', {
  skip: process.platform !== 'linux' || sandboxMode({}) !== 'bwrap' ? `这台机器没有 bwrap 沙箱（${sandboxMode({})}）` : false,
}, (t) => {
  const runDir = mkdtempSync(join(tmpdir(), 'argus-sbx-ws-'))
  t.after(() => rmSync(runDir, { recursive: true, force: true }))
  // 生产里这一步的 TMPDIR 是 <tmpdir>/argus-<run>/step-unit（runner 的 stepTmpDir(shortTmpOf(runDir), 'unit')），
  // 与证据日志里的 /tmp/argus-run-*/argus-n1454-h1-*/step-unit 同形。
  const stepTmp = join(tmpdir(), `argus-${basename(runDir)}`, 'step-unit')
  mkdirSync(stepTmp, { recursive: true })
  mkdirSync(join(runDir, 'home'), { recursive: true })
  const probe = join(runDir, 'workspace-ancestor-probe.mjs')
  writeFileSync(probe, PROBE)

  const res = spawnSync(BWRAP, [...bwrapArgs(runDir), '--', process.execPath, probe], {
    encoding: 'utf8',
    env: { ...process.env, TMPDIR: stepTmp, HOME: join(runDir, 'home') },
    timeout: 60_000,
  })
  assert.equal(res.status, 0, `沙箱里的探针没跑成：${String(res.stderr).slice(-600)}`)
  const seen = JSON.parse(res.stdout.trim().split('\n').pop())
  assert.equal(seen.tmpdir, stepTmp, '探针的工作目录应落在这一步的临时目录里（runner 的 stepEnv 就是这么设 TMPDIR 的）')

  const rejected = seen.levels.filter((level) => !acceptedByControllerCheck(level, seen.uid))
  assert.deepEqual(rejected.map(show), [],
    '沙箱把工作目录祖先链上的目录变成了 root 与运行用户之外的归属，被测仓的 assertControllerDirectory 会在第一层'
    + `就抛「父目录必须由控制器独占管理」（${seen.work} 建不出来，tmp-open/tmp-mkdir/tmp-chown 三条用例全部假失败，`
    + '报告记成「在基线版本上也失败」）。沙箱的根应由本次 run 自己造并归运行用户，宿主 / 的顶层再逐项只读挂回。')
})
