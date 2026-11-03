// 2026-11-04 事故签名（3 次 / 7 天，PR #1686 #1713 #1720）：bench「nodeIdsInRect #1360 SC4 框选耗时(1 万节点全在组) >
// 每组 100 个节点:整屏框选 pointermove p95 ≤ 2ms(含第一次建索引)」被判成 pre-existing（pre_existing=true、severity P3），
// 报告写「在基线版本上也失败」。pi/bin/infrafix.py:262 的 check 规则只读 pre_existing + flaky，于是每 7 天把它当成
// 「测试工具（探针 / 用例）有问题，或主线本身坏了」再报一次（消息在 :264 生成）；日报 bot 也据此把它标成「老问题」。
//
// 证据（/workspace/mivo-qa/reports/catch-up/PR-1720.json 等三份）：bench 是 trialOnly 检查（配置 noteZh「用仓里已有的
// 性能测试和它们自带的阈值…共享机器上耗时有波动，试运行期只记拿不准」），三轮（h1 / h2 / b）都在空闲机器上
// （base_idle_wait.idle=true）跑同一条断言，p50 0.8–1.4ms、p95 2.4–4.9ms，2ms 阈值在这台 8 核共享机上根本达不到。
// 也就是说 base 与 PR 一起失败说明的是「这台机器 / 这套阈值测不出任何东西」，不是这个 PR 的问题，也不是主线坏了：
// compareKey 只能证明「同名用例、同类型断言两边都失败」（decide.mjs:70），无法证明产品状态。
//
// 约定：试运行检查（trialOnly）在合并前版本上也失败的，只记「拿不准」（pi/bin/control.mjs:111 toTrialDecision「试运行的
// 检查确认的问题只记「拿不准」，不自动提 issue」、argus/config/profiles/mivo-canvas-plugin.json 的 bench noteZh），
// 由 QA 自查（pending_verification check-qa，owner=qa），不进 findings、不置 pre_existing、不给级别；
// 与 2026-10-29 同一类签名的修法（tests/check-base-run-not-comparable.test.mjs）一致：报告不得替 QA 断言「基线也失败」。
//
// 反例钉住边界：普通产品检查（非 trialOnly）在基线版本上也失败时，仍然按 docs/report-schema.md 记 pre-existing P3 提醒，
// 不能被这条规则吞掉。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = mkdtempSync(join(tmpdir(), 'argus-bench-base-'))
process.env.ARGUS_ROOT = root
process.env.ARGUS_GH_BIN = join(here, 'fixtures/argus-fake-gh.mjs')
process.env.ARGUS_GH_RETRY_MS = '1'
process.env.ARGUS_E2E_GH_STATE = join(root, 'github-fixture.json')
after(() => rmSync(root, { recursive: true, force: true }))

const N = await import('../src/nightly.mjs')
const { MIRRORS_DIR } = await import('../src/paths.mjs')

const health = () => ({ status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], notes: [] })

// PR #1720 报告里的真实用例名（failed_checks[0].test）
const BENCH_TEST = 'src/canvas/nodeIdsInRect.bench.test.ts > #1360 SC4 框选耗时(1 万节点全在组) > 每组 100 个节点:整屏框选 pointermove p95 ≤ 2ms(含第一次建索引)'

// 三轮（h1 首跑 / h2 同提交重跑 / b 合并前版本）都写同一份 vitest JSON 报告并失败：就是 #1686 #1713 #1720 的形态。
// 报告与 stdout 必须互相吻合（runner.mjs:404 readTestReport），名字也与 vitest 的 `FAIL 文件 > 套件 > 用例` 一致。
const benchCmd = ['sh', '-c', [
  'run="$1"',
  'mkdir -p "$run/reports"',
  'cat > "$run/reports/bench.json" <<\'JSON\'',
  JSON.stringify({
    numTotalTestSuites: 2, numPassedTestSuites: 1, numFailedTestSuites: 1,
    numTotalTests: 3, numPassedTests: 2, numFailedTests: 1, numPendingTests: 0, numTodoTests: 0,
    success: false, unhandledErrors: [], numRuntimeErrorTestSuites: 0,
    testResults: [
      {
        name: 'src/canvas/nodeIdsInRect.bench.test.ts', status: 'failed',
        assertionResults: [{ title: '每组 100 个节点:整屏框选 pointermove p95 ≤ 2ms(含第一次建索引)', ancestorTitles: ['#1360 SC4 框选耗时(1 万节点全在组)'], status: 'failed' }],
      },
      {
        name: 'src/canvas/other.bench.test.ts', status: 'passed',
        assertionResults: [
          { title: '每组 2 个节点:整屏框选 pointermove p95 ≤ 2ms(含第一次建索引)', ancestorTitles: ['#1360 SC4 框选耗时(1 万节点全在组)'], status: 'passed' },
          { title: '每组 10000 个节点:整屏框选 pointermove p95 ≤ 2ms(含第一次建索引)', ancestorTitles: ['#1360 SC4 框选耗时(1 万节点全在组)'], status: 'passed' },
        ],
      },
    ],
  }, null, 2),
  'JSON',
  'cat <<\'OUT\'',
  '',
  ' RUN  v2.1.0 /tmp/fixture',
  '',
  ` FAIL  ${BENCH_TEST}`,
  'AssertionError: expected 4.9012 to be less than or equal to 2',
  '',
  ' Test Files  1 failed | 1 passed (2)',
  '      Tests  1 failed | 2 passed (3)',
  'OUT',
  'exit 1',
].join('\n'), 'sh', '${RUN}']

const installStep = { id: 'install', kind: 'setup', nameZh: '安装依赖', cmd: ['true'] }

// 与 argus/config/profiles/mivo-canvas-plugin.json 的 bench 步骤同形（optional + trialOnly + cache:false + vitest JSON 报告）
const benchStep = {
  id: 'bench', nameZh: '性能基准', kind: 'product', optional: true, trialOnly: true,
  covers: ['performance'], cache: false, cmd: benchCmd,
  testReport: { format: 'vitest-json', path: '${RUN}/reports/bench.json' }, timeoutMin: 15,
}

// 对照用：同一个失败，但这一步不是试运行检查（unit 单测）
const unitStep = { id: 'unit', nameZh: '单元测试', kind: 'product', optional: true, covers: ['unit', 'regression'], cache: false, cmd: benchCmd, timeoutMin: 15 }

const profile = (steps) => ({ sandbox: false, steps: [installStep, ...steps] })

function fixture(number) {
  const repo = `fixture/pr${number}`
  const src = join(root, `source-${number}`)
  mkdirSync(src)
  const git = (...args) => execFileSync('git', ['-C', src, '-c', 'user.name=F', '-c', 'user.email=f@example.invalid', ...args], { encoding: 'utf8' }).trim()
  git('init', '-q', '-b', 'main')
  mkdirSync(join(src, 'src/canvas'), { recursive: true })
  writeFileSync(join(src, 'src/canvas/nodeIdsInRect.ts'), 'export const v = 1\n')
  git('add', '.'); git('commit', '-qm', 'base')
  const baseSha = git('rev-parse', 'HEAD')
  writeFileSync(join(src, 'src/canvas/nodeIdsInRect.ts'), 'export const v = 2\n'); git('commit', '-qam', `code (#${number})`)
  const mergeSha = git('rev-parse', 'HEAD')
  mkdirSync(MIRRORS_DIR, { recursive: true })
  execFileSync('git', ['clone', '-q', '--bare', src, join(MIRRORS_DIR, `fixture__pr${number}.git`)])
  const pr = { number, title: 'perf(canvas): fixture', html_url: 'https://example.invalid', user: { login: 'f' }, base: { ref: 'main', sha: baseSha }, head: { sha: 'f'.repeat(40) }, merged_at: new Date().toISOString(), merge_commit_sha: mergeSha, state: 'closed' }
  writeFileSync(process.env.ARGUS_E2E_GH_STATE, JSON.stringify({ pr, baseSha, files: [{ filename: 'src/canvas/nodeIdsInRect.ts', additions: 1, deletions: 1 }], calls: [] }))
  return { repo }
}

async function runPr({ number, steps }) {
  const { repo } = fixture(number)
  const h = health()
  const out = await N.processPr({
    repo, project: { repo, profileData: profile(steps) }, number, discovery: 'merged',
    secrets: {}, db: null, health: h, evidenceRoot: join(root, `ev-${number}`),
    opts: { noReview: true, noAiTests: true, reuseMerged: false },
  })
  return { out, h }
}

// 断言消息里只放定位用的字段：整条 entry 带 command / log_tail，打出来读不了。
const brief = (e) => JSON.stringify({ check_id: e?.check_id, test: e?.test, status: e?.status, severity: e?.severity, pre_existing: e?.pre_existing, flaky: e?.flaky, failure_type: e?.failure_type, attribution: e?.attribution, first_run: e?.evidence?.first_run, rerun: e?.evidence?.rerun, base_run: e?.evidence?.base_run })

test('试运行检查（bench）在基线版本上也失败：只记「拿不准」交 QA 自查，不报成 pre-existing 的 PR 发现（PR #1686 #1713 #1720）', async () => {
  const run = await runPr({ number: 4101, steps: [benchStep] })
  const entries = (run.out.failed_checks ?? []).filter((c) => c.check_id === 'bench')
  assert.equal(entries.length, 1, `先决条件：bench 的失败要留在 failed_checks 里，不能悄悄变成通过：${JSON.stringify((run.out.failed_checks ?? []).map(brief))}`)
  const [entry] = entries
  const dump = brief(entry)
  // 三轮（首跑 / 重跑 / 基线）都失败，和 #1686 #1713 #1720 的报告一致
  assert.equal(entry.evidence?.first_run, 'fail', `先决条件：首跑失败 ${dump}`)
  assert.equal(entry.evidence?.rerun, 'fail', `先决条件：重跑仍失败 ${dump}`)
  assert.equal(entry.evidence?.base_run, 'fail', `先决条件：基线轮也失败 ${dump}`)
  assert.equal(entry.failure_type, 'assertion', `先决条件：断言失败（不是超时）${dump}`)

  assert.ok(entry.status !== 'pre-existing', `bench 是试运行检查：基线也失败说明这台机器 / 这套阈值测不出东西，不能定成 pre-existing：${dump}`)
  assert.ok(entry.pre_existing !== true, `不能置 pre_existing=true——infrafix 的 check 规则（pi/bin/infrafix.py:262）据此每 7 天开一张「在基线版本上也失败」的工单：${dump}`)
  assert.ok(!/also fails on the base revision/.test(String(entry.attribution ?? '')), `不能替 QA 断言基线版本上也失败（试运行检查只记拿不准）：${dump}`)
  assert.equal(entry.severity, null, `拿不准的试运行失败不定级（docs/report-schema.md：unverified → 无级别，待核实）：${dump}`)

  const graded = (run.out.findings ?? []).filter((f) => f.check_id === 'bench')
  assert.deepEqual(graded, [], `试运行检查的基线失败不进 findings（不发作者提醒）：${JSON.stringify(graded.map(brief))}`)

  const pending = (run.out.pending_verification ?? []).filter((p) => p.check_id === 'bench')
  assert.equal(pending.length, 1, `这种情况是 QA 自己要先查清的（待核实），实际 pending_verification=${JSON.stringify((run.out.pending_verification ?? []).map((p) => ({ check_id: p.check_id, pending_kind: p.pending_kind, severity_if_confirmed: p.severity_if_confirmed })))}`)
  assert.equal(pending[0].pending_kind, 'check-qa', `待核实种类应为 check-qa：${pending[0].pending_kind}`)
  assert.equal(pending[0].owner, 'qa', `待核实归 QA 自查，不算作者的问题：${pending[0].owner}`)
})

test('对照：普通产品检查（非 trialOnly）在基线版本上也失败时仍是 pre-existing P3 提醒，不能被这条规则吞掉', async () => {
  const run = await runPr({ number: 4102, steps: [unitStep] })
  const [entry] = (run.out.failed_checks ?? []).filter((c) => c.check_id === 'unit')
  const dump = brief(entry)
  assert.equal(entry?.evidence?.base_run, 'fail', `先决条件：基线轮也失败 ${dump}`)
  assert.equal(entry?.status, 'pre-existing', `普通产品检查的基线失败仍是 pre-existing（docs/report-schema.md）：${dump}`)
  assert.equal(entry?.severity, 'P3', `pre-existing 记 P3，只提醒：${dump}`)
  const graded = (run.out.findings ?? []).filter((f) => f.check_id === 'unit')
  assert.equal(graded.length, 1, `普通检查的 pre-existing P3 仍要进 findings：${JSON.stringify((run.out.findings ?? []).map(brief))}`)
})
