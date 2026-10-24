// 单 PR 时间预算（--pr-timeout）被某个检查吃光时的收尾。事故形态（10-25 PR #1166，nightly 01:32，
// report/catch-up/PR-1166.json：not_tested_code=pr-timeout、partial_result.checks=[]、耗时 2792 s = 45 min + 92 s）：
//   1) unit 卡住 → 进程在 45 min 预算处被 SIGTERM。processPr 在「覆盖率轮次之后再查一次预算」那里
//      （nightly.mjs:355 `if (overBudget()) return out`）直接返回，out.checks 还是空的，
//      于是已经跑完的检查（typecheck / e2e-browser …）和「其实是 unit 超时」这条线索全丢，
//      报告只剩一条笼统的 qa_tool_failure「per-PR time budget of 45 min exceeded」。
//      report-schema.md 的约定是：partial_result = 被打断那一轮的 { verdict, checks, evidence_dir }。
//   2) 预算用完之后，ensureMirror（runner.mjs:63-71）把「一开就被预算挡住」的 fetch（run() 立刻返回
//      budgetExceeded，不启动进程）当成网络抖动，照旧睡着重试 15 + 30 + 45 = 90 s —— 正好是事故里
//      超出预算的那 92 s。util.mjs 的 waitWithinBudget 注释写明：每次等待都不越过 PR 时间预算。
// 两条都在同一个 run 上断言，所以这个文件今天会先跑满 ~96 s（缺陷 2 本身）再报失败。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = mkdtempSync(join(tmpdir(), 'argus-budget-partial-'))
process.env.ARGUS_ROOT = root
process.env.ARGUS_GH_BIN = join(here, 'fixtures/argus-fake-gh.mjs')
process.env.ARGUS_GH_RETRY_MS = '1'
process.env.ARGUS_E2E_GH_STATE = join(root, 'github-fixture.json')
after(() => rmSync(root, { recursive: true, force: true }))

const N = await import('../src/nightly.mjs')
const { setBudget } = await import('../src/exec.mjs')
const { MIRRORS_DIR } = await import('../src/paths.mjs')

const BUDGET_MIN = 0.05 // 3 s
const health = () => ({ status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], notes: [] })

// 首轮按 profile 顺序跑：install → typecheck（通过）→ unit（睡下去，被预算剪掉）
const PROFILE = { sandbox: false, steps: [
  { id: 'install', kind: 'setup', nameZh: '安装依赖', cmd: ['true'] },
  { id: 'typecheck', kind: 'product', nameZh: '类型检查', covers: ['types'], cmd: ['true'] },
  { id: 'unit', kind: 'product', nameZh: '单元测试', covers: ['unit'], cmd: ['sh', '-c', 'sleep 30'] },
] }

function fixture(number) {
  const repo = `fixture/pr${number}`
  const src = join(root, `source-${number}`)
  mkdirSync(src)
  const git = (...args) => execFileSync('git', ['-C', src, '-c', 'user.name=F', '-c', 'user.email=f@example.invalid', ...args], { encoding: 'utf8' }).trim()
  git('init', '-q', '-b', 'main')
  writeFileSync(join(src, 'value.mjs'), 'export const v = 1\n')
  git('add', '.'); git('commit', '-qm', 'base')
  const baseSha = git('rev-parse', 'HEAD')
  writeFileSync(join(src, 'value.mjs'), 'export const v = 2\n'); git('commit', '-qam', `code (#${number})`)
  const mergeSha = git('rev-parse', 'HEAD')
  mkdirSync(MIRRORS_DIR, { recursive: true })
  execFileSync('git', ['clone', '-q', '--bare', src, join(MIRRORS_DIR, `fixture__pr${number}.git`)])
  const pr = { number, title: 'code', html_url: 'https://example.invalid', user: { login: 'f' }, base: { ref: 'main', sha: baseSha }, head: { sha: 'f'.repeat(40) }, merged_at: new Date().toISOString(), merge_commit_sha: mergeSha, state: 'closed' }
  writeFileSync(process.env.ARGUS_E2E_GH_STATE, JSON.stringify({ pr, baseSha, files: [{ filename: 'value.mjs', additions: 1, deletions: 1 }], calls: [] }))
  return { repo, number }
}

// 预算 3 s：install / typecheck 用掉几十毫秒，剩下的全给 unit；unit 的 sleep 30 在预算处被 SIGTERM → 预算用完，
// processPr 走「预算用完就不再做重跑 / 基线」的 early return。两个测试共用这一次运行。
let sharedRun = null
function runWithTightBudget() {
  sharedRun ??= (async () => {
    const number = 2101
    const { repo } = fixture(number)
    const h = health()
    const ev = join(root, 'ev-budget')
    const processOne = (t) => N.processPr({ repo, project: { repo, profileData: PROFILE }, number: t.number, discovery: t.reason, secrets: {}, db: null, health: h, evidenceRoot: ev, opts: { noReview: true } })
    try {
      const { prs, heartbeat } = await N.processTargets([{ number, reason: 'merged' }], { health: h, opts: { prTimeoutMin: BUDGET_MIN }, processOne })
      return { pr: prs[0], heartbeat, h }
    } finally { setBudget(null) }
  })()
  return sharedRun
}

test('预算被检查吃光：被打断那一轮的结果留在 partial_result（unit 记 timeout、已通过的检查保留）', async () => {
  const { pr } = await runWithTightBudget()
  // 外层结论不变：预算用完 → not-tested / pr-timeout（这条行为本身是对的，不该被这次修改动到）
  assert.equal(pr.verdict, 'not-tested')
  assert.equal(pr.not_tested_code, 'pr-timeout')
  assert.equal(pr.severity, 'untested')
  assert.ok(pr.partial_result, 'partial_result 必须存在')
  assert.ok(pr.partial_result.checks.length > 0,
    '被打断那一轮跑完的检查要留在 partial_result.checks 里（report-schema 的约定），不能是空数组')
  const byId = Object.fromEntries(pr.partial_result.checks.map((c) => [c.id, c]))
  assert.equal(byId.unit?.status, 'timeout', '吃掉预算的 unit 步骤在部分结果里是 timeout')
  assert.equal(byId.typecheck?.status, 'pass', '预算用完前已经通过的检查仍然留着')
})

test('吃掉 PR 预算的那一步要在 qa_tool_failures 里署名（stage=unit），不能只报 per-PR budget exceeded', async () => {
  const { h } = await runWithTightBudget()
  const stages = h.qa_tool_failures.map((f) => f.stage)
  assert.ok(stages.includes('unit'),
    `超时的检查要以自己的 step id 记录，否则报告看不出是谁卡掉了整段预算：${JSON.stringify(h.qa_tool_failures)}`)
  assert.ok(stages.includes('pr-timeout'), '整段预算用完这件事本身仍要记录')
})

test('预算用完后不再空等：per-PR 墙钟不得超出预算约 90 s（mirror fetch 在预算外重试 15/30/45 s）', async () => {
  const { heartbeat } = await runWithTightBudget()
  const budgetS = BUDGET_MIN * 60
  const took = heartbeat.prs[0].duration_s
  assert.equal(typeof took, 'number')
  assert.ok(took <= budgetS + 6,
    `预算用完（${budgetS}s）后不该再为不可能成功的 fetch 睡 15+30+45 s：这个 PR 用了 ${took}s（事故里 45 min 预算跑成 46 min 32 s）`)
})
