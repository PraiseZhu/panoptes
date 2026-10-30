// bwrap 沙箱里的「路径属主」必须和真 CI 同解。
//
// mivo 的 scripts/ops/review-capacity-check/{launcher.native,workspace-creation.native,isolation,...}.test.mjs 要求
// 「启动路径 / 控制器父目录」从 / 开始的每一层 stat uid ∈ {0, 当前用户} 且不带组/其他写位（review-capacity-check/run.sh
// 的 trusted_path、workspace.mjs 的 assertControllerDirectory）。真 CI（GitHub runner，非 root 用户）成立：/ 属主是 0。
// 但 bwrap 默认建一个非特权 user namespace，宿主 root 不映射进来，于是沙箱里 / 读成 65534(nobody)，整条链在 / 处被拒：
// h1 与基线轮次都报 `控制器启动路径不可信，拒绝加载模块` / `父目录必须由控制器独占管理`（reports/catch-up PR-1449/1450/1454，
// 每次 30 个用例，合并前后都失败，于是被记成 pre-existing「在基线版本上也失败」，连续 5 次运行）。这类假失败是 QA 沙箱与
// 真 CI 的属主视图不一致造成的，不是被测版本的问题。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const root = mkdtempSync(join(tmpdir(), 'argus-bwrap-owner-'))
process.env.ARGUS_ROOT ??= root
process.env.ARGUS_STEP_CACHE = '0' // 复用的旧结果里没有这次的沙箱视图
const { newRunDir, runSteps, cleanupRun, sandboxMode } = await import('../src/runner.mjs')
const dirs = []
after(async () => { for (const d of dirs) await cleanupRun(null, d); rmSync(root, { recursive: true, force: true }) })

// 与项目用例同形的检查：从 / 走到目标路径，逐层看属主与权限位
const CHAIN = `cur=/
printf 'CHAIN %s %s %s\\n' / "$(/usr/bin/stat -c %u /)" "$(/usr/bin/stat -c %a /)"
for part in $(printf '%s' "\${1#/}" | tr / ' '); do
  cur="\${cur%/}/$part"
  printf 'CHAIN %s %s %s\\n' "$cur" "$(/usr/bin/stat -c %u "$cur")" "$(/usr/bin/stat -c %a "$cur")"
done`

test('沙箱里从 / 到 HOME 里夹具的每一层，属主都是 root 或当前用户（真 CI 成立，沙箱里 / 读成 65534）', { skip: process.platform !== 'linux' || sandboxMode() !== 'bwrap' }, async () => {
  const runDir = newRunDir(1, 'bwrap-owner'); dirs.push(runDir)
  mkdirSync(join(runDir, 'src'), { recursive: true })
  // 项目用例把夹具建在 HOME（0700 的 mkdtemp）里；stepEnv 把 HOME 指到 run 目录的 home
  const fixture = join(runDir, 'home', '.review-capacity-launcher-fixture')
  mkdirSync(fixture, { recursive: true, mode: 0o700 })
  const profile = { steps: [{ id: 'trust-chain', kind: 'product', nameZh: '路径信任链', cmd: ['sh', '-c', CHAIN, '--', fixture] }] }
  const [res] = await runSteps(profile, join(runDir, 'src'), runDir)
  assert.equal(res.exitCode, 0, res.tail)
  const rows = res.tail.split('\n').filter((l) => l.startsWith('CHAIN ')).map((l) => {
    const [, path, uid, mode] = l.split(' ')
    return { path, uid: Number(uid), mode: parseInt(mode, 8) }
  })
  assert.ok(rows.length >= 4, `链路过短，沙箱没跑起来：${res.tail}`)
  const uid = process.getuid()
  const rejected = rows.filter((r) => ![0, uid].includes(r.uid) || (r.mode & 0o022) !== 0)
  assert.deepEqual(rejected, [], `沙箱里这些路径的属主/权限和真 CI 不一致，项目的信任链用例会一律失败（合并前版本也一样）：${
    rejected.map((r) => `${r.path} uid=${r.uid}（期望 0 或 ${uid}） mode=${r.mode.toString(8)}`).join(', ')}`)
})
