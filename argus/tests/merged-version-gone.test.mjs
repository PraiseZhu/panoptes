// 已合并 PR 的「被测版本」已经不在仓库里（上游回退 / 强制推送 / 合并分支被删）时的收场。
//
// 事故（10-29 23:21 起那一轮补测，reports/catch-up/PR-1411.json、PR-1413.json、PR-1419.json、PR-1422.json 四条全中）：
//   #1411 / #1413 / #1419 / #1422 是 #1410 的探针 PR（标题 probe(1410) / test(1410) …「镜像成功后再被取代」），
//   GitHub 上 state=merged，但它们的 merge commit 已经不在仓库里：本地镜像 xindong__mivo-canvas-plugin.git 里
//   `git cat-file -t 3b416d02…` / `aafb8305…` / `86df970e…` / `5965c64e…` 四个全部 could not get object info，
//   而 refs/heads/main 上已经有编号更大的 #1425 和 #1409 —— 也就是 main 被强推改写过，这四个提交不再挂在任何分支上
//   （fetch +refs/heads/* 永远取不回来；squash 合并时 refs/pull/N/merge 也不含这个 merge commit）。
//   每轮 2-4 s、四条全 same：「检出版本失败：fatal: unable to read tree (<sha>)」→ verdict=qa-error、
//   system_health.status=failed、complete=false，于是 infrafix 又按 run incomplete 记一次（这个签名已经进第二轮；
//   上一轮的 #106 只把这条消息改成「镜像里缺这个版本」，判定没动，所以签名照旧复发）。
//
// 缺陷：版本被上游弄没了，是输入事实（这个 PR 永远没法按 merge commit 测），不是 QA 机器坏掉。
//   qa-error 的定义是「测试系统自己出错，PR 本身没测到」（nightly.mjs:1614），也就是 report-schema 的
//   「QA pipeline error」（docs/report-schema.md:165），而 status=failed / complete=false 的判定里 qa-error 是来源之一
//   （nightly.mjs:1649-1651）。把它继续当 qa-error，等于让一条永久事实每轮都把整轮打成 failed + incomplete，
//   签名收敛不了（第三轮就要人看）。应该按「这个版本没法测」记：PR 记 not-tested（带自己的 not_tested_code + 原因，
//   写明版本 sha 与「镜像/仓库里没有这个版本」），并把它记成 note / 非 pipeline 的 qa_tool_failure，而不是 QA 崩溃。
// 证据（本地只读核对）：/workspace/mivo-qa/argus-root/cache/mirrors/xindong__mivo-canvas-plugin.git
//   `git log --oneline refs/heads/main` 的头是 af1ef07a(#1425)、2353e868(#1409)，没有 #1411–#1422；
//   `git cat-file -t <四个 merge sha>` 全部 fatal: could not get object info。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = mkdtempSync(join(tmpdir(), 'argus-version-gone-'))
process.env.ARGUS_ROOT = root
process.env.ARGUS_GH_BIN = join(here, 'fixtures/argus-fake-gh.mjs')
process.env.ARGUS_GH_RETRY_MS = '1'
process.env.ARGUS_E2E_GH_STATE = join(root, 'github-fixture.json')
process.env.ARGUS_COVERAGE = '0'
after(() => rmSync(root, { recursive: true, force: true }))

const N = await import('../src/nightly.mjs')
const { setBudget } = await import('../src/exec.mjs')
const { MIRRORS_DIR } = await import('../src/paths.mjs')

const health = () => ({ status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], notes: [] })
const PROFILE = { sandbox: false, steps: [
  { id: 'install', kind: 'setup', nameZh: '安装依赖', cmd: ['true'] },
  { id: 'unit', kind: 'product', nameZh: '单元测试', covers: ['unit'], cmd: ['true'] },
] }

// 已合并 PR，但 merge commit 不在镜像里：mirror 克隆时 main 还在 base，随后 main 被回退，merge commit 变成不可达
function goneVersionFixture(number) {
  const repo = `fixture/pr${number}`
  const src = join(root, `source-${number}`)
  mkdirSync(src)
  const git = (...args) => execFileSync('git', ['-C', src, '-c', 'user.name=F', '-c', 'user.email=f@example.invalid', ...args], { encoding: 'utf8' }).trim()
  git('init', '-q', '-b', 'main')
  writeFileSync(join(src, 'value.mjs'), 'export const v = 1\n')
  git('add', '.'); git('commit', '-qm', 'base')
  const baseSha = git('rev-parse', 'HEAD')
  mkdirSync(MIRRORS_DIR, { recursive: true })
  execFileSync('git', ['clone', '-q', '--bare', src, join(MIRRORS_DIR, `fixture__pr${number}.git`)])
  // 「已合并」的提交：只在 source 里存在过，随后 main 被回退到 base（上游强推 / 合并分支被删）
  writeFileSync(join(src, 'value.mjs'), 'export const v = 2\n'); git('commit', '-qam', `probe (#${number})`)
  const mergeSha = git('rev-parse', 'HEAD')
  git('update-ref', 'refs/heads/main', baseSha)
  // 前提自检：镜像里确实没有这个版本（否则断言的不是本缺陷）
  assert.throws(
    () => execFileSync('git', ['-C', join(MIRRORS_DIR, `fixture__pr${number}.git`), 'cat-file', '-e', `${mergeSha}^{commit}`], { stdio: 'ignore' }),
    `fixture 前提不成立：镜像里居然有 ${mergeSha}`,
  )
  writeFileSync(process.env.ARGUS_E2E_GH_STATE, JSON.stringify({
    pr: { number, title: `probe(1410) (#${number})`, html_url: 'https://example.invalid', user: { login: 'PraiseZhu' }, base: { ref: 'main', sha: baseSha }, head: { sha: mergeSha }, merged_at: new Date().toISOString(), merge_commit_sha: mergeSha, state: 'closed' },
    baseSha, files: [{ filename: 'value.mjs', additions: 1, deletions: 1, patch: '+value' }], calls: [],
  }))
  return { repo, baseSha, mergeSha }
}

let sharedRun = null
function runGoneVersion() {
  sharedRun ??= (async () => {
    const number = 2301
    const { repo, mergeSha } = goneVersionFixture(number)
    const h = health()
    const processOne = (t) => N.processPr({ repo, project: { repo, profileData: PROFILE }, number: t.number, discovery: t.reason, secrets: {}, db: null, health: h, evidenceRoot: join(root, 'ev'), opts: { noReview: true } })
    try {
      const { prs } = await N.processTargets([{ number, reason: 'merged' }], { health: h, opts: { prTimeoutMin: 5 }, processOne })
      const finalized = N.finalizeHealth(h, prs)
      return { pr: prs[0], h, finalized, mergeSha }
    } finally { setBudget(null) }
  })()
  return sharedRun
}

test('已合并 PR 的版本已不在仓库里：这不是 QA 崩溃（不记 qa-error，system_health 不是 failed）', async () => {
  const { pr, h, finalized } = await runGoneVersion()
  assert.notEqual(pr.verdict, 'qa-error',
    `版本被上游弄没了是输入事实，不是「测试系统自己出错」：记成 qa-error 会让 status=failed、complete=false，签名每轮复发。实际：${JSON.stringify({ verdict: pr.verdict, error: pr.error })}`)
  assert.equal(pr.tested, false)
  assert.ok(!h.qa_tool_failures.some((f) => f.stage === 'pipeline'), `不能再记一条 stage=pipeline 的工具故障：${JSON.stringify(h.qa_tool_failures)}`)
  assert.notEqual(finalized.status, 'failed', `整轮不该因为这条永久事实变成 failed：${JSON.stringify({ status: finalized.status, qaf: h.qa_tool_failures })}`)
})

test('已合并 PR 的版本已不在仓库里：整轮不算 incomplete，但报告要把这件事说清楚（带版本 sha）', async () => {
  const { pr, h, finalized, mergeSha } = await runGoneVersion()
  // 这个 PR 永远没法按 merge commit 测：记「没测 / 跳过」，不是「这轮跑坏了」——否则 run incomplete 签名收敛不了
  assert.equal(finalized.complete, true,
    `一条「版本已不存在」的永久事实不该让整轮 incomplete（docs/report-schema.md:694）：complete=${finalized.complete}`)
  const said = [pr.not_tested_reason, pr.outcome_reason, pr.error, ...(h.notes ?? [])].filter(Boolean).join(' | ')
  assert.ok(said.includes(mergeSha), `报告要带上取不到的版本号，实际：${said}`)
  assert.match(said, /镜像|仓库|版本|缺少|不在|取不到|missing|not in/i, `报告要说清是「仓库里没有这个版本」，实际：${said}`)
})

test('已合并 PR 的版本已不在仓库里：这个 PR 仍明确列进 not_tested / 跳过列表，不能悄悄消失', async () => {
  const { pr } = await runGoneVersion()
  assert.ok(['not-tested', 'skipped'].includes(pr.verdict), `既没测也没有明确记号：verdict=${pr.verdict}`)
  assert.ok(pr.not_tested_code || pr.outcome_reason, `要给出原因码或原因文字：${JSON.stringify({ code: pr.not_tested_code, reason: pr.outcome_reason })}`)
})
