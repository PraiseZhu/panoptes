// 单元测试「scripts/ops/review-capacity-check/cleanup-identity.test.mjs > 受保护父句柄删除原空目录，替代对象保留并拒绝：false」
// 在基线版本上也失败（inf1-b21cbcc7ff4e49478830，7 天 5 次运行）。
//
// 现场（reports/catch-up/PR-1454.json 的 failed_checks[unit]）：
//   Error: 父目录必须由控制器独占管理；已保留目录：/tmp/argus-run-…/step-unit/mivo-cleanup-controller-…/spill/…
//   ❯ assertControllerDirectory scripts/ops/review-capacity-check/workspace.mjs:30
//   ❯ cleanupUnits scripts/ops/review-capacity-check/run.mjs:173
// 仓库那道闸（scripts/ops/review-capacity-check/workspace.mjs assertControllerDirectory）从 '/' 开始逐级核对：
// 每级属主必须是 root(0) 或当前用户，且不能有组/他人可写位（root + sticky 的 /tmp 例外）。GitHub CI 里 / 属 root，
// 所以绿。panoptes 的 bwrapArgs 用 `--ro-bind / /`：宿主 root（uid 0）不在沙箱 user namespace 的 uid 映射里，
// 沙箱里的 '/' 显示成溢出 uid 65534(nobody)，闸的第一级就拒绝 —— 首跑、重跑、基线版本一起失败，于是 nightly 只能
// 记成「在基线版本上也失败」P3（pre_existing），与 PR 改动无关，infrafix 按「基线也失败」一直把同一张票据顶回来。
//
// 本测试钉住真正需要的性质（与修法无关：tmpfs 根 + 逐项只读挂回，或让 bwrap 带权限跑，都行）：
//   1. 沙箱里 '/' 的属主必须是 root(0) 或沙箱用户自己，不能是 65534；
//   2. 步骤 TMPDIR 的每一级祖先都要通过「父目录必须由控制器独占管理」这条属主校验。
// 两条现在都红：修复后沙箱里的 / 归沙箱用户（0755 tmpfs 根）或 root，被测仓库的 cleanup-identity 用例即可通过。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-stepowner-'))
const { bwrapArgs, sandboxMode, stepTmpDir } = await import('../src/runner.mjs')

const BWRAP = process.env.ARGUS_BWRAP_BIN ?? '/usr/bin/bwrap'
const canSandbox = process.platform === 'linux' && sandboxMode() === 'bwrap'

// 被测仓库的规则（mivo scripts/ops/review-capacity-check/workspace.mjs assertControllerDirectory）
const controllerOwned = (uid, mode, me) => [0, me].includes(uid) && (!(mode & 0o022) || (uid === 0 && (mode & 0o1000) !== 0))

// 沙箱里从 '/' 走到步骤 TMPDIR，逐级取属主与权限位（判据只要这份数据，不关心修法）
const WALK = `const { statSync, readFileSync } = require('node:fs')
const target = process.env.TMPDIR
const me = process.getuid()
const rows = []
let cur = '/'
for (const part of [null, ...target.split('/').filter(Boolean)]) {
  if (part) cur = cur === '/' ? '/' + part : cur + '/' + part
  const s = statSync(cur)
  rows.push({ path: cur, mode: (s.mode & 0o7777).toString(8), uid: s.uid })
}
process.stdout.write(JSON.stringify({ me, map: readFileSync('/proc/self/uid_map', 'utf8').trim().replace(/\\s+/g, ' '), rows }))`

function walkInSandbox() {
  const runDir = mkdtempSync(join(tmpdir(), 'argus-stepowner-'))
  try {
    const stepTmp = stepTmpDir(runDir, 'unit')
    const res = spawnSync(BWRAP, [...bwrapArgs(runDir, { chdir: runDir }), '--', process.execPath, '-e', WALK], {
      encoding: 'utf8', env: { ...process.env, TMPDIR: stepTmp }, timeout: 120_000,
    })
    assert.equal(res.status, 0, `沙箱里跑不通（bwrap 退出 ${res.status}）：${(res.stderr || '').slice(-800)}`)
    const out = JSON.parse(res.stdout)
    assert.equal(out.rows.at(-1)?.path, stepTmp, '沙箱里看到的最后一级必须是步骤 TMPDIR（否则下面的断言在空转）')
    return out
  } finally { rmSync(runDir, { recursive: true, force: true }) }
}

test('bwrap 沙箱里 / 的属主是 root 或沙箱用户，不能是 nobody(65534)', { skip: !canSandbox }, () => {
  const { me, map, rows } = walkInSandbox()
  const root = rows[0]
  assert.ok([0, me].includes(root.uid),
    `沙箱里 / 的属主是 ${root.uid}（沙箱用户 uid=${me}，uid_map="${map}"）：宿主 root 没进映射，` +
    '被测仓库的 assertControllerDirectory 从第一级就判「父目录必须由控制器独占管理」，cleanup-identity 一族用例在 head 和 base 上一起假失败')
})

test('步骤 TMPDIR 的每级祖先都通过「父目录必须由控制器独占管理」校验（cleanup-identity 的路径链）', { skip: !canSandbox }, () => {
  const { me, rows } = walkInSandbox()
  const bad = rows.filter((r) => !controllerOwned(r.uid, parseInt(r.mode, 8), me))
  assert.deepEqual(bad, [],
    `这些祖先目录过不了 mivo 的属主校验（沙箱用户 uid=${me}），cleanup-identity 会保留目录并报错：\n` +
    bad.map((r) => `  ${r.path} mode=${r.mode} uid=${r.uid}`).join('\n'))
})
