// 镜像里没有要测的提交（10-29 的 8 次 nightly：run-nightly.sh 退出码 3）
//
// 现场（control.log 23:21/23:33、reports/catch-up/PR-1413.json）：gh 说 #1413/#1419/#1422 已合并，
// merge_commit_sha = aafb8305…/86df970e…/5965c64e…，但镜像里没有这些提交 —— 它们不是任何分支尖
// （合并分支删掉 / 主干被重写后只剩 GitHub 的 refs/pull/N/*）。ensureMirror 的 `git fetch` 照样返回 0：
// 分支引用没变，fetch 什么都不下载，`cat-file -e` 只在 fetch 失败那条分支上做，于是函数带着缺提交的镜像正常返回。
// mivo profile 用 checkoutMode: "clone"（config/profiles/mivo-canvas-plugin.json），checkout 走
// `git checkout --detach <sha>`，不存在的提交被当成路径，git 只吐一句 `fatal: unable to read tree (<sha>)`，
// nightly 记成 qa-error（PR 没测到）、system_health failed、run-nightly.sh exit 3；catch-up 每轮重试，8 次一模一样。
//
// 两个测试都跑在注入了假 GitHub + 本地裸仓 origin 的真实 git 上（不联网、不碰生产路径）：
//   1) 提交在 origin 的 refs/pull/N/* 或按提交号可取 → 镜像补拉后按 merge commit 测，不是 qa-error；
//   2) origin 也没有这个提交 → ensureMirror 必须报错说出「镜像里没有这个提交」，不能返回缺提交的镜像。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = mkdtempSync(join(tmpdir(), 'argus-mirror-missing-'))
process.env.ARGUS_ROOT = root
process.env.ARGUS_GH_BIN = join(here, 'fixtures/argus-fake-gh.mjs')
process.env.ARGUS_GH_RETRY_MS = '1'
process.env.ARGUS_E2E_GH_STATE = join(root, 'github-fixture.json')
after(() => rmSync(root, { recursive: true, force: true }))

const N = await import('../src/nightly.mjs')
const { ensureMirror, mirrorPath } = await import('../src/runner.mjs')
const { MIRRORS_DIR } = await import('../src/paths.mjs')

const health = () => ({ status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], notes: [] })
const gitIn = (dir, ...args) => execFileSync('git', ['-C', dir, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()
const hasObject = (dir, sha) => { try { execFileSync('git', ['-C', dir, 'cat-file', '-e', `${sha}^{commit}`], { stdio: 'ignore' }); return true } catch { return false } }

// checkoutMode: "clone" 是 mivo profile 的写法，10-29 那句 `unable to read tree` 正是它吐出来的
const PROFILE = {
  sandbox: false,
  checkoutMode: 'clone',
  steps: [
    { id: 'install', kind: 'setup', nameZh: '安装依赖', cmd: [process.execPath, '-e', ''] },
    { id: 'unit', kind: 'product', nameZh: '单元测试', covers: ['unit'], cmd: [process.execPath, '-e', ''] },
  ],
}

// base(main) → feat(head) → merge(main 上的合并提交，随后 main 退回 base)
// 镜像先克隆（此时 origin 只有 main），PR 提交之后再出现：镜像里就没有 head/merge，和 10-29 现场一样。
// origin 只把 main 当分支；PR 的两个引用只在 withPrRefs 时推送，模拟「合并分支已删、GitHub 只剩 refs/pull/N/*」
function fixture(number, { withPrRefs }) {
  const repo = `fixture/pr${number}`
  const work = join(root, `work-${number}`)
  mkdirSync(work)
  gitIn(work, 'init', '-q', '-b', 'main')
  writeFileSync(join(work, 'value.mjs'), 'export const v = 1\n')
  gitIn(work, 'add', '.')
  gitIn(work, 'commit', '-qm', 'base')
  const baseSha = gitIn(work, 'rev-parse', 'HEAD')

  const origin = join(root, `origin-${number}.git`)
  execFileSync('git', ['init', '-q', '--bare', origin])
  gitIn(work, 'push', '-q', '--force', origin, 'main:refs/heads/main')
  mkdirSync(MIRRORS_DIR, { recursive: true })
  execFileSync('git', ['clone', '-q', '--bare', origin, mirrorPath(repo)], { stdio: 'pipe' })

  gitIn(work, 'checkout', '-qb', 'feat')
  writeFileSync(join(work, 'value.mjs'), 'export const v = 2\n')
  gitIn(work, 'commit', '-qam', `change (#${number})`)
  const headSha = gitIn(work, 'rev-parse', 'HEAD')
  gitIn(work, 'checkout', '-q', 'main')
  gitIn(work, 'merge', '-q', '--no-ff', '-m', `Merge (#${number})`, 'feat')
  const mergeSha = gitIn(work, 'rev-parse', 'HEAD')
  gitIn(work, 'reset', '-q', '--hard', baseSha)
  gitIn(work, 'update-ref', `refs/pull/${number}/head`, headSha)
  gitIn(work, 'update-ref', `refs/pull/${number}/merge`, mergeSha)
  if (withPrRefs) {
    gitIn(work, 'push', '-q', '--force', origin, `refs/pull/${number}/head:refs/pull/${number}/head`, `refs/pull/${number}/merge:refs/pull/${number}/merge`)
  }
  // GitHub 允许按提交号取对象（refs/pull/* 不在分支尖上时也拉得到）
  execFileSync('git', ['-C', origin, 'config', 'uploadpack.allowAnySHA1InWant', 'true'])

  const pr = {
    number, node_id: `fixture-${number}`, title: `probe(#${number}): merge commit 不在分支尖上`,
    html_url: `https://example.invalid/pull/${number}`, user: { id: 99, login: 'fixture' },
    state: 'closed', base: { ref: 'main', sha: baseSha }, head: { sha: headSha },
    merged_at: new Date().toISOString(), merge_commit_sha: mergeSha, commits: 1,
  }
  writeFileSync(process.env.ARGUS_E2E_GH_STATE, JSON.stringify({ pr, baseSha, files: [{ filename: 'value.mjs', additions: 1, deletions: 1 }], calls: [] }))
  return { repo, number, baseSha, headSha, mergeSha, mirror: mirrorPath(repo) }
}

test('合并提交不在任何分支尖上：镜像补拉后按 merge commit 测这个 PR，不是 qa-error', async () => {
  const f = fixture(2101, { withPrRefs: true })
  // 前置：镜像里确实没有这个提交 —— 现场就是「fetch 成功但对象缺失」
  assert.equal(hasObject(f.mirror, f.mergeSha), false, '夹具镜像不应该已经含有合并提交')

  const h = health()
  // nightly 的调用方会把 checkout 抛出的错误收成 qa-error（report/catch-up/PR-1413.json 就是这样来的）
  const pr = await N.processPr({ repo: f.repo, project: { repo: f.repo, displayName: '夹具', profileData: PROFILE }, number: f.number, discovery: 'merged', secrets: {}, db: null, health: h, evidenceRoot: join(root, 'ev-1'), opts: { noReview: true } })
    .catch((err) => ({ verdict: 'qa-error', error: err.message, tested: false, tested_sha: null, merge_sha: f.mergeSha, checks: [] }))

  assert.equal(hasObject(f.mirror, f.mergeSha), true, '镜像要先把这个提交补回来，才能在它上面检出')
  assert.notEqual(pr.verdict, 'qa-error', `不该把「本地镜像缺提交」判成 QA 自己出错：${pr.error ?? ''}`)
  assert.ok(!/unable to read tree/.test(pr.error ?? ''), 'git 的临时 checkout 报错不能当成结论')
  assert.equal(pr.tested, true, '提交在 origin 上取得到，这个 PR 就应该被真测')
  assert.equal(pr.tested_sha, f.mergeSha, '已合并的 PR 要按 merge commit 测，不能悄悄换成别的提交')
  assert.equal(pr.merge_sha, f.mergeSha)
  assert.ok(pr.checks.length > 0, '要真的跑过检查，而不是跳过这个 PR')
})

test('合并提交在 origin 也拿不到：ensureMirror 必须报错，不能返回缺提交的镜像', async () => {
  const f = fixture(2102, { withPrRefs: false })
  assert.equal(hasObject(f.mirror, f.mergeSha), false)

  // 现在返回得很正常，缺的提交一路带到 checkout 才炸成 `unable to read tree (<sha>)`
  await assert.rejects(ensureMirror(f.repo, f.mergeSha), (err) => {
    assert.match(err.message, new RegExp(f.mergeSha.slice(0, 12)), '报错要说清是哪个提交拿不到')
    return true
  })
})
