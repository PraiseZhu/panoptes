// 「检出版本失败：fatal: unable to read tree (<sha>)」—— 10-29 23:21（北京）那一轮 catch-up 里，#1411 / #1413 / #1419 /
// #1422 四条已合并 PR 全部以此收场（reports/catch-up/PR-1411.json … PR-1422.json：verdict=qa-error、severity=untested、
// system_health.status=failed、qa_tool_failures[0] = { stage: 'pipeline', message: 这条 }）。
//
// 现场：这四条是「已合并 → 测 merge commit」的补测，但它们的 merge commit 拉取后仍然不在镜像里 —— 本地
// mirrors/xindong__mivo-canvas-plugin.git 的对象库里整个没有这四个 sha（`git cat-file -t` 报 could not get object info，
// refs/heads/main 的 tip 是 #1409），也就是镜像本身没坏，是「这个版本在远端不再挂在任何分支上」。
//
// 缺陷在 runner.mjs:160 checkout()：它不先确认请求的版本真在镜像里，直接把 git 的 stderr 当结论抛出来，而同一件事在两种
// checkout 模式下措辞还不一样、且都不说「镜像里没有这个版本」：
//   - clone 模式（mivo-canvas-plugin profile：config/profiles/mivo-canvas-plugin.json:2053 "checkoutMode": "clone"）
//     `git clone --shared` 之后 `git checkout --detach <不存在的 sha>` → "fatal: unable to read tree (<这个 commit sha>)"，
//     看着像镜像丢了 tree（仓库损坏），实际丢的是整个 commit；
//   - worktree 模式 `git worktree add --force --detach <路径> <sha>` → "fatal: invalid reference: <sha>"。
// 这条消息会原样进 system_health.qa_tool_failures[].message 和 pr.error（nightly.mjs:1611），运维只能猜镜像是不是坏了，
// 也看不出该去查「合并后 main 被回退 / 合并分支已被删」。
//
// 因此这里的断言是：请求的版本不在镜像里时，checkout 必须报错（不能假装检出成功），并且错误消息要点名
// 「镜像里没有这个版本/镜像里缺这个版本」并带上 sha —— 不管走哪种 checkout 模式。
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const root = mkdtempSync(join(tmpdir(), 'argus-checkout-missing-'))
process.env.ARGUS_ROOT = root

const { checkout, mirrorPath, newRunDir } = await import('../src/runner.mjs')
const { MIRRORS_DIR } = await import('../src/paths.mjs')

const REPO = 'fixture/mivo-probe'

// 镜像只有 main 一个分支；「已合并 PR 的 merge commit」在 source 里提交过、但没推到任何分支，
// 等价于镜像 fetch 完仍然没有这个版本（真实原因：合并后 main 被回退，或合并分支合并后就删了）。
let cached = null
function fixture () {
  if (cached) return cached
  const src = join(root, 'source')
  mkdirSync(src, { recursive: true })
  const git = (...args) => execFileSync('git', ['-C', src, '-c', 'user.name=F', '-c', 'user.email=f@example.invalid', ...args], { encoding: 'utf8' }).trim()
  git('init', '-q', '-b', 'main')
  writeFileSync(join(src, 'value.mjs'), 'export const v = 1\n')
  git('add', '.')
  git('commit', '-qm', 'base')
  mkdirSync(MIRRORS_DIR, { recursive: true })
  execFileSync('git', ['clone', '-q', '--bare', src, mirrorPath(REPO)])
  writeFileSync(join(src, 'value.mjs'), 'export const v = 2\n')
  git('commit', '-qam', 'merged, then main was rewound')
  const gone = git('rev-parse', 'HEAD')
  // 前提自检：镜像里确实没有这个版本，否则这两个用例断言的就不是本缺陷
  assert.throws(
    () => execFileSync('git', ['-C', mirrorPath(REPO), 'cat-file', '-e', `${gone}^{commit}`], { stdio: 'ignore' }),
    `fixture 前提不成立：镜像里居然有这个版本 ${gone}`,
  )
  cached = { gone }
  return cached
}

const failureOf = (promise) => promise.then(() => null, (err) => err)

// 这条消息就是报告里的原文（nightly.mjs:1611 redact(err.message)），所以「版本不在镜像里」必须写在消息里
function assertNamesMissingVersion (err, sha, mode) {
  assert.ok(err instanceof Error, `${mode}：镜像里没有这个版本，checkout 必须报错（不能假装检出成功）`)
  const msg = String(err.message ?? '')
  assert.ok(msg.includes(sha), `${mode}：消息要带上被请求的版本号，实际：${msg}`)
  assert.match(msg, /镜像|mirror/i, `${mode}：消息要指出是（本地）镜像里没有这个版本，实际：${msg}`)
  assert.match(msg, /不在|没有|缺|不完整|missing|absent|not found|not in|no such/i, `${mode}：消息要点明找不到这个版本，实际：${msg}`)
}

test('clone 模式（mivo-canvas-plugin profile）：版本不在镜像里时点名这件事，不是 git 的 unable to read tree', async () => {
  const { gone } = fixture()
  const runDir = newRunDir('checkout-missing', 'clone')
  const err = await failureOf(checkout(mirrorPath(REPO), gone, runDir, { repo: REPO, mode: 'clone' }))
  assertNamesMissingVersion(err, gone, 'clone')
})

test('worktree 模式：同一件事的说法和 clone 模式一致（不能一个说 invalid reference、一个说 unable to read tree）', async () => {
  const { gone } = fixture()
  const runDir = newRunDir('checkout-missing', 'worktree')
  const err = await failureOf(checkout(mirrorPath(REPO), gone, runDir, { repo: REPO, mode: 'worktree' }))
  assertNamesMissingVersion(err, gone, 'worktree')
})

test.after(() => rmSync(root, { recursive: true, force: true }))
