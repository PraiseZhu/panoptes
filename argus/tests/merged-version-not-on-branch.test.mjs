// 「镜像里缺这个版本：d875a57829437dbc78849a16ac406461186f30c6（本地镜像 …/mirrors/xindong__mivo-canvas-plugin.git
// 没有这个 commit，可能合并后 main 被回退或合并分支已删除）」—— 10-30 01:53（北京）那轮 catch-up 里 PR #1436 以此收场：
// reports/catch-up/PR-1436.json 的 prs[0] = { verdict: "qa-error", severity: "untested", tested: false, error: 这条 }、
// system_health.status = "failed"、system_health.qa_tool_failures[0] = { pr: 1436, stage: "pipeline", message: 这条 }。
//
// 现场：#1436 是探针 PR（PR 正文：「#1410 反向测试探针 PR（只对 probe-1410/* 自己的基线分支，用完即关）」），
// 00:29:58 北京时间合并进那个一次性基线分支后分支就删了。本地镜像里确实没有这个版本
// （git -C mirrors/xindong__mivo-canvas-plugin.git cat-file -e d875a57…^{commit} → Not a valid object name；
// refs/heads/main 的 tip 是 #1420 的 60571c93，不含它），但远端的 pull refs 仍按版本留着它：
// GitHub 保留 refs/pull/N/head、refs/pull/N/merge，并允许 git fetch origin <sha> 取到可达的版本。
//
// 缺陷在 runner.mjs 的取码链路：ensureMirror（65 行起）只按 `+refs/heads/*`（开放 PR 另加 `+refs/pull/N/head`）取，
// 从不按**需要的那个版本本身**向远端要一次；checkout（160 行起、166 行抛出）随后把「本地镜像没有」直接当成结论。
// 于是只要合并版本不挂在任何分支上（探针 PR 的一次性基线分支被删、合进集成分支后被回退），
// 整个 PR 就记成 qa-error + stage="pipeline" 的自测系统故障：报告说不出「远端其实还能取到，只是没去取」，
// catch-up 还会（#109 之后对 exit 3 不计数、不放弃地）一直重试这个缺口。
//
// 因此这里的断言是：版本不在镜像的任何分支上、但远端还能按版本取到时，取码链路必须把它取回来并**真的检出**
// （HEAD == 该版本），不能直接判「镜像里缺这个版本」。远端也确实取不到时仍照旧报错，见
// checkout-missing-version.test.mjs（消息要点名「镜像里缺/没有这个版本」并带上 sha）。
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const root = mkdtempSync(join(tmpdir(), 'argus-merged-offbranch-'))
process.env.ARGUS_ROOT = root
process.env.ARGUS_SECRETS_FILE = join(root, 'intentionally-no-secrets.env')

const { ensureMirror, checkout, cleanupRun, verifyCheckoutSha, mirrorPath, newRunDir } = await import('../src/runner.mjs')
const { MIRRORS_DIR } = await import('../src/paths.mjs')

const REPO = 'fixture/merged-offbranch'
const git = (cwd, ...args) => execFileSync('git', ['-C', cwd, '-c', 'user.name=F', '-c', 'user.email=f@example.invalid', '-c', 'advice.detachedHead=false', ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()

// 远端（= GitHub）：main 只有 base；探针 PR 的合并版本只挂在 refs/pull/1436/{head,merge} 上，
// 那个一次性基线分支（probe-1410/base）合并完就删了 —— 这正是 #1436 的真实形状。
let cached = null
function fixture () {
  if (cached) return cached
  const work = join(root, 'work')
  mkdirSync(work, { recursive: true })
  git(work, 'init', '-q', '-b', 'main')
  writeFileSync(join(work, 'value.mjs'), 'export const v = 1\n')
  git(work, 'add', '.')
  git(work, 'commit', '-qm', 'base')
  const base = git(work, 'rev-parse', 'HEAD')

  // 镜像的老祖宗：只含 base 的一份裸仓（clone 只能从它开始，否则会把 pull refs 一起搬进镜像）
  const stale = join(root, 'stale.git')
  execFileSync('git', ['clone', '-q', '--bare', work, stale], { stdio: 'ignore' })

  // 探针 PR：基线分支上的提交 + 合并提交（GitHub 建的 merge commit）
  git(work, 'checkout', '-q', '-b', 'probe-1410/base')
  writeFileSync(join(work, 'value.mjs'), 'export const v = 2\n')
  git(work, 'commit', '-qam', 'probe: control group')
  const head = git(work, 'rev-parse', 'HEAD')
  git(work, 'checkout', '-q', '-b', 'probe-1410/merge-1436', base)
  git(work, 'merge', '-q', '--no-ff', '-m', 'Merge pull request #1436 from PraiseZhu/probe-1410', 'probe-1410/base')
  const merge = git(work, 'rev-parse', 'HEAD')

  const remote = join(root, 'remote.git')
  execFileSync('git', ['clone', '-q', '--bare', work, remote], { stdio: 'ignore' })
  // 分支都删掉，只留 pull refs；并像 GitHub 一样允许按版本取可达对象
  git(remote, 'update-ref', '-d', 'refs/heads/probe-1410/base')
  git(remote, 'update-ref', '-d', 'refs/heads/probe-1410/merge-1436')
  git(remote, 'update-ref', 'refs/pull/1436/head', head)
  git(remote, 'update-ref', 'refs/pull/1436/merge', merge)
  git(remote, 'config', 'uploadpack.allowReachableSHA1InWant', 'true')
  assert.equal(git(remote, 'rev-parse', 'refs/heads/main'), base, '远端 main 不含这个版本')
  assert.equal(git(remote, 'rev-parse', 'refs/pull/1436/merge'), merge, '远端仍按 pull ref 留着合并版本')

  // 本地镜像：老镜像 + origin 指回远端（生产里 ensureMirror 就是从镜像的 origin fetch）
  mkdirSync(MIRRORS_DIR, { recursive: true })
  execFileSync('git', ['clone', '-q', '--bare', stale, mirrorPath(REPO)], { stdio: 'ignore' })
  git(mirrorPath(REPO), 'remote', 'set-url', 'origin', remote)
  // 前提自检：镜像在任何分支上都看不到这个版本，否则断言的就不是本缺陷
  assert.throws(() => git(mirrorPath(REPO), 'cat-file', '-e', `${merge}^{commit}`), `fixture 前提不成立：镜像里居然有这个版本 ${merge}`)
  cached = { base, head, merge }
  return cached
}

// 生产里「测这个版本」就是这两步：取码（ensureMirror）→ 检出（checkout，内部先确认版本在镜像里）。
// 断言只看结果：不许抛「镜像里缺这个版本」，且检出的 HEAD 必须是被请求的那个版本。
async function testVersion (mode) {
  const { merge } = fixture()
  const runDir = newRunDir('n1436', mode)
  try {
    await ensureMirror(REPO, merge)
    let src
    try {
      src = await checkout(mirrorPath(REPO), merge, runDir, { repo: REPO, mode })
    } catch (err) {
      assert.fail(`${mode}：远端仍按版本留着 ${merge.slice(0, 12)}，取码链路却直接判「镜像里缺这个版本」：${err.message}`)
    }
    assert.equal(await verifyCheckoutSha(src, merge), merge, `${mode}：检出的 HEAD 必须就是被请求的版本`)
  } finally {
    await cleanupRun(mirrorPath(REPO), runDir)
  }
}

test('已合并 PR 的版本不在任何分支上（探针 PR 的一次性基线分支已删）、远端仍按 pull ref 留着：取到并检出，不判「镜像里缺这个版本」', async () => {
  await testVersion('clone') // mivo-canvas-plugin profile 用的是 clone 模式
})

test('worktree 模式：同一件事（版本不在分支上、远端还留着）也要取到并检出', async () => {
  await testVersion('worktree')
})

test.after(() => rmSync(root, { recursive: true, force: true }))
