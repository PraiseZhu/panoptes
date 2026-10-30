// 沙箱的根目录不能显示成陌生人（nobody / 65534）。
//
// 现场（2026-10-30，inf1-4beabcdb5afccde7fcdf）：mivo 的 scripts/ops/review-capacity-check/workspace.mjs
// 用 assertControllerDirectory 从 '/' 逐级检查「父目录必须由控制器独占管理」——uid 必须是 0 或自己，
// 而且不能有组/其他可写位（root + sticky 的 /tmp 例外）。argus 的 bwrapArgs 是 `--ro-bind / /`：
// 宿主 root(uid 0) 不在沙箱 user namespace 的 uid 映射里，于是沙箱里的 '/' 是 uid=65534(nobody)，
// 检查在第一级就抛错。整批用例在 head 和 base 上同样失败，报告只能写成「在基线版本上也失败」P3，
// 7 天 5 次运行都把同一张票据顶回来，与 PR 的改动无关。
//
// 下面两条钉住真正需要的性质（与具体修法无关）：沙箱里 '/' 的 uid 必须是 root 或沙箱用户自己，
// 并且步骤 TMPDIR 的每一级祖先都要能通过控制器归属检查。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-sbxroot-'))
const { bwrapArgs, sandboxMode, stepTmpDir } = await import('../src/runner.mjs')

const BWRAP = '/usr/bin/bwrap'
const canSandbox = process.platform === 'linux' && sandboxMode() === 'bwrap'

// 被测仓库的规则（mivo scripts/ops/review-capacity-check/workspace.mjs assertControllerDirectory）
const controllerOwned = (uid, mode, me) => [0, me].includes(uid) && (!(mode & 0o022) || (uid === 0 && (mode & 0o1000) !== 0))

// 在沙箱里从 '/' 走到 TMPDIR，逐级报告 uid / mode（同一份数据供两条用例断言）
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

function walkInSandbox(stepTmp, runDir) {
  const res = spawnSync(BWRAP, [...bwrapArgs(runDir, { chdir: runDir }), '--', process.execPath, '-e', WALK], {
    encoding: 'utf8', env: { ...process.env, TMPDIR: stepTmp }, timeout: 120_000,
  })
  assert.equal(res.status, 0, `沙箱里跑不通（bwrap 退出 ${res.status}）：${(res.stderr || '').slice(-800)}`)
  const out = JSON.parse(res.stdout)
  assert.equal(out.rows.at(-1).path, stepTmp, '沙箱里看到的最后一级必须是步骤 TMPDIR（否则断言会空转）')
  return out
}

test('bwrap 沙箱的根目录属于 root 或沙箱用户：/ 不能显示成 nobody(65534)', { skip: !canSandbox }, () => {
  const runDir = mkdtempSync(join(tmpdir(), 'argus-sbxroot-'))
  try {
    const stepTmp = stepTmpDir(runDir, 'unit')
    const { me, map, rows } = walkInSandbox(stepTmp, runDir)
    const root = rows[0]
    assert.ok([0, me].includes(root.uid),
      `沙箱里 / 的 uid 是 ${root.uid}（沙箱用户 uid=${me}，uid_map="${map}"）：宿主 root 没被映射，` +
      '被测仓库从第一级就判定「父目录必须由控制器独占管理」')
  } finally { rmSync(runDir, { recursive: true, force: true }) }
})

test('步骤 TMPDIR 的每一级祖先都能通过控制器归属检查（mivo review-capacity-check 的父目录断言）', { skip: !canSandbox }, () => {
  const runDir = mkdtempSync(join(tmpdir(), 'argus-sbxroot-'))
  try {
    const stepTmp = stepTmpDir(runDir, 'unit')
    const { me, rows } = walkInSandbox(stepTmp, runDir)
    const bad = rows.filter((r) => !controllerOwned(r.uid, parseInt(r.mode, 8), me))
    assert.deepEqual(bad, [],
      `沙箱用户 uid=${me} 认领不了这些祖先目录，被测仓库会拒绝继续：\n` +
      bad.map((r) => `  ${r.path} mode=${r.mode} uid=${r.uid}`).join('\n'))
  } finally { rmSync(runDir, { recursive: true, force: true }) }
})
