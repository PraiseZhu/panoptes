// 「gh api -H Accept: application/vnd.github+json repos/xindong/mivo-canvas-plugin/branches/integration%2Fcanvas-s2
// 失败：gh: Bad credentials (HTTP 401)」—— 11-05 02:26（北京）那轮补测里 PR #1740 以此收场：
// /workspace/mivo-qa/reports/catch-up/PR-1740.json 的 prs[0] = { verdict: "qa-error", severity: "untested", tested: false,
// error: 这条 }、system_health.status = "failed"、system_health.qa_tool_failures[0] = { pr: 1740, stage: "pipeline", message: 这条 }、
// summary.prs_by_severity.untested = 1（这个 PR 一个结论都没有），整轮耗时 1926 s。
//
// 现场：#1740 是已合并 PR（merged_at 01:53:22 +08:00，merge_sha 87a87859…），这轮先跑完首轮 / 重跑 / 合并前对照
// （32 分钟），判定进入「合并后新出现问题」分支后才去问「最新主干（PR 的 base 分支 integration/canvas-s2）现在还是不是这个提交」——
// 这一步是 argus/src/nightly.mjs:423 的 getBranchHead（control.mjs:189 同形）。gh 这一步返回 401，于是 git 之外唯一的
// 「顺手看一眼主干」的辅助读失败，把整条流水线连同已经跑完的检查一起扔掉，PR 记成 qa-error + stage="pipeline"。
//
// 401 不是一次性事故，而是这条链路上的常见事件：App 安装令牌 1 小时过期，scripts/gh-app-env.sh:37-44 在 run-nightly.sh 开跑时
// 才铸一次（pi/bin/ghtoken.py 只在「剩余不足 10 分钟」时重铸，见 ghtoken.py:MARGIN_S），所以开跑时这个 token 只剩 10–60 分钟，
// 而夜间 / 补测单 PR 预算就是 90 分钟（PR-1740.json run.args.pr_timeout_min = 90）。跑超过令牌剩余寿命时，之后任何一次 gh 调用
// 都会拿到 401，而 argus 里没有任何重铸路径（仓库里只有 argus/src/github.mjs 的 ghEnvFor 读 PRAISE_QA_GH_TOKEN，没有 ghtoken 调用）。
// 同一个 token，立刻重试还是同一个 401，所以「重试」不是修法；这里的正确行为是 panoptes 自己早就写下的降级约定：
//   · nightly.mjs:207-214 p0NoteFor：「latest main not re-checked (latest main head unknown)」；
//   · decide.mjs:172-173：!latestIsSame 且 latest 跑不起来 → needs_review「最新主干没能跑起来，没法确认问题现在还在不在」；
//   · control.mjs:191-192：「拿不到最新主干时不能当成「就是合并版本」，交给判定记待核实」。
// 也就是：主干 head 取不到 → 记「主干没复查」，已经跑完的检查与结论照样出报告。
//
// 因此这里的断言是：取主干 head 的 gh 调用 401 时，这个 PR 不能变成 qa-error、不能让整轮 failed / incomplete，
// 已跑完的检查与失败必须留在报告里（结论隔在 needs_review 一类，不得伪称「最新主干仍然失败」）；
// 而 gh 正常返回时照旧按最新主干判定（本文件最后一个用例守着这条路，别把修复写成「永远当作不知道」）。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const root = mkdtempSync(join(tmpdir(), 'argus-branch-401-'))
const GH_BIN = join(root, 'fake-gh.mjs')

// 进程级 fake gh：只读数、绝不联网。state.branches 为真值时才回答 branches 接口，否则像过期安装令牌一样回 401
writeFileSync(GH_BIN, `#!/usr/bin/env node
import { readFileSync, writeFileSync } from 'node:fs'
const file = process.env.ARGUS_E2E_GH_STATE
const state = JSON.parse(readFileSync(file, 'utf8'))
const args = process.argv.slice(2)
const route = args.find((a) => a.startsWith('repos/')) ?? null
state.calls.push(route)
writeFileSync(file, JSON.stringify(state))
if (!route || args.includes('-X')) { process.stderr.write('fake gh: unexpected write call ' + args.join(' ')); process.exit(2) }
const say = (obj) => { process.stdout.write(JSON.stringify(obj)); process.exit(0) }
const die = (msg) => { process.stderr.write(msg); process.exit(1) }
if (/\\/branches\\//.test(route)) {
  if (state.branches) say(state.branches)
  die('gh: Bad credentials (HTTP 401)')
}
if (/\\/pulls\\/\\d+\\/files\\?/.test(route)) say(state.files)
else if (/\\/pulls\\/\\d+$/.test(route)) say(state.pr)
else if (/\\/commits\\//.test(route)) say({ parents: [{ sha: state.baseSha }], commit: { message: 'fixture' } })
else if (/\\/contents\\//.test(route) || /\\/compare\\//.test(route)) die('gh: Not Found (HTTP 404)')
else if (/\\/issues\\?/.test(route)) say([])
else die('fake gh: undeclared route ' + route)
`, { mode: 0o755 })

process.env.ARGUS_ROOT = root
process.env.ARGUS_GH_BIN = GH_BIN
process.env.ARGUS_GH_RETRY_MS = '1'
process.env.ARGUS_E2E_GH_STATE = join(root, 'gh-state.json')
process.env.ARGUS_SECRETS_FILE = join(root, 'intentionally-no-secrets.env')
process.env.ARGUS_COVERAGE = '0'
process.env.PANOPTES_YIELD = '0'

const N = await import('../src/nightly.mjs')
const { setBudget } = await import('../src/exec.mjs')
const { MIRRORS_DIR } = await import('../src/paths.mjs')

after(() => rmSync(root, { recursive: true, force: true }))

const health = () => ({ status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], notes: [] })

// 只跑一步真检查：合并版本失败、合并前通过 → 判定进入「合并后新出现问题」分支，才会去问最新主干 head
const PROFILE = { sandbox: false, steps: [
  { id: 'install', kind: 'setup', nameZh: '准备', cmd: [process.execPath, '-e', ''] },
  { id: 'unit', kind: 'product', nameZh: '单元测试', covers: ['unit'],
    cmd: [process.execPath, '--test', '--test-reporter=spec', '--test-reporter=${NODE_TEST_REPORTER}', '--test-reporter-destination=stdout', '--test-reporter-destination=${TEST_REPORT}', 'behavior.test.mjs'],
    testReport: { format: 'node-events', path: '${RUN}/reports/unit.jsonl' } },
] }

// 已合并 PR：base 上 value = 1（用例通过），merge commit 上 value = 0（用例失败）。branchHead 决定 branches 接口怎么答
function fixture(number, { branchHead }) {
  const repo = `fixture/pr${number}`
  const src = join(root, `source-${number}`)
  mkdirSync(src)
  const git = (...args) => execFileSync('git', ['-C', src, '-c', 'user.name=F', '-c', 'user.email=f@example.invalid', ...args], { encoding: 'utf8' }).trim()
  git('init', '-q', '-b', 'main')
  writeFileSync(join(src, 'value.mjs'), 'export const value = 1\n')
  writeFileSync(join(src, 'behavior.test.mjs'), "import { test } from 'node:test'\nimport assert from 'node:assert/strict'\nimport { value } from './value.mjs'\ntest('value remains positive', () => assert.ok(value > 0))\n")
  git('add', '.'); git('commit', '-qm', 'base')
  const baseSha = git('rev-parse', 'HEAD')
  writeFileSync(join(src, 'value.mjs'), 'export const value = 0\n')
  git('commit', '-qam', `change (#${number})`)
  const mergeSha = git('rev-parse', 'HEAD')
  mkdirSync(MIRRORS_DIR, { recursive: true })
  execFileSync('git', ['clone', '-q', '--bare', src, join(MIRRORS_DIR, `fixture__pr${number}.git`)])
  writeFileSync(process.env.ARGUS_E2E_GH_STATE, JSON.stringify({
    pr: { number, title: `perf(render): 夹具 (#${number})`, html_url: 'https://example.invalid', body: '', user: { login: 'PraiseZhu' },
      base: { ref: 'main', sha: baseSha }, head: { sha: mergeSha }, merged_at: new Date().toISOString(), merge_commit_sha: mergeSha, state: 'closed' },
    baseSha, files: [{ filename: 'value.mjs', status: 'modified', additions: 1, deletions: 1, patch: '-export const value = 1\n+export const value = 0' }],
    branches: branchHead ? { commit: { sha: branchHead === 'SELF' ? mergeSha : branchHead } } : null,
    calls: [],
  }))
  return { repo, baseSha, mergeSha }
}

// 走真实的 processTargets → processPr（含真实 git 检出 / 真跑 node --test），只有 GitHub 是进程级 fake
async function runPipeline(number, { branchHead }) {
  const f = fixture(number, { branchHead })
  const h = health()
  const processOne = (t) => N.processPr({ repo: f.repo, project: { repo: f.repo, profileData: PROFILE }, number: t.number, discovery: t.reason, secrets: {}, db: null, health: h, evidenceRoot: join(root, 'ev'), opts: { noReview: true, noAiTests: true } })
  try {
    const { prs } = await N.processTargets([{ number, reason: 'merged' }], { health: h, opts: { prTimeoutMin: 5, yieldToPriority: false }, processOne })
    const state = JSON.parse(readFileSync(process.env.ARGUS_E2E_GH_STATE, 'utf8'))
    return { pr: prs[0], h, finalized: N.finalizeHealth(h, prs), calls: state.calls, ...f }
  } finally { setBudget(null) }
}

let cached401 = null
const run401 = () => { cached401 ??= runPipeline(2401, { branchHead: null }); return cached401 }

test('最新主干 head 取不到（gh 401）不能让这个 PR 变成 qa-error：整轮不算失败，也不留 stage=pipeline 的工具故障', async () => {
  const { pr, h, finalized, calls } = await run401()
  assert.ok(calls.some((c) => /\/branches\//.test(String(c))), `前提不成立：这轮根本没去问主干 head，就无从复现这个签名（calls=${JSON.stringify(calls)}）`)
  assert.notEqual(pr.verdict, 'qa-error',
    `「顺手看一眼主干」的 gh 401 不是 QA 崩溃：首轮 / 重跑 / 合并前对照都跑完了，PR 不该一条结论都没有。实际：${JSON.stringify({ verdict: pr.verdict, tested: pr.tested, error: pr.error })}`)
  assert.ok(!h.qa_tool_failures.some((f) => f.stage === 'pipeline'),
    `不能把这条记成 stage=pipeline 的工具故障（整轮 failed + incomplete 的来源）：${JSON.stringify(h.qa_tool_failures)}`)
  assert.notEqual(finalized.status, 'failed', `整轮不该因为一次 401 变成 failed：${JSON.stringify({ status: finalized.status, qaf: h.qa_tool_failures })}`)
  assert.equal(finalized.complete, true,
    `这个 PR 有最终结论（主干没复查也是结论），整轮不该 incomplete（docs/report-schema.md）：complete=${finalized.complete}`)
})

test('最新主干 head 取不到（gh 401）时，已经跑完的检查与失败必须留在报告里：tested、失败步骤、结论都在', async () => {
  const { pr } = await run401()
  assert.equal(pr.tested, true, `测试已经跑完，报告必须记 tested=true；实际 ${JSON.stringify({ verdict: pr.verdict, tested: pr.tested })}`)
  assert.equal(pr.checks.find((c) => c.id === 'unit')?.status, 'fail', `单元测试确实在合并版本上失败，这条事实不能丢：${JSON.stringify(pr.checks)}`)
  assert.ok(pr.failed_checks.some((c) => c.check_id === 'unit'), `失败检查要进 failed_checks：${JSON.stringify(pr.failed_checks)}`)
  // 主干 head 不知道 ≠ 缺陷已确认（decide.mjs:172-173）：不得伪称「最新主干仍然失败」，也不得当成通过
  assert.equal(pr.outcome, 'needs_review',
    `主干没复查时结论只能是待核实，不能自称 defect（那是「最新主干仍然失败」），也不能当通过。实际 outcome=${pr.outcome} reason=${pr.outcome_reason}`)
  assert.equal(pr.latest_main_sha ?? null, null, `没取到主干 head 时不能编一个 sha：${pr.latest_main_sha}`)
  const said = [pr.outcome_reason, pr.error, ...(pr.findings ?? []).map((x) => [x.p0_note, x.attribution].filter(Boolean).join(' ')), ...(pr.pending_verification ?? []).map((x) => x.title)].filter(Boolean).join(' | ')
  assert.match(said, /最新主干|latest main/i, `报告要说清「主干没复查」，而不是悄悄当成已确认：${said}`)
})

test('主干 head 正常返回时照旧按最新主干判定（修复不能写成「永远当作不知道」）', async () => {
  const { pr, mergeSha, calls } = await runPipeline(2402, { branchHead: 'SELF' })
  assert.ok(calls.some((c) => /\/branches\//.test(String(c))), '这轮必须真的问过主干 head')
  // 主干 head 就是被测提交本身 → 判定为确认缺陷（最新主干上同样失败），不需要再跑一轮
  assert.equal(pr.latest_main_sha, mergeSha, `主干 head 取到了就要记下来：${pr.latest_main_sha}`)
  assert.equal(pr.outcome, 'defect', `主干 head = 被测提交时判定是 defect：${JSON.stringify({ outcome: pr.outcome, reason: pr.outcome_reason })}`)
})
