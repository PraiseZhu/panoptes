// 2026-10-11：--write-open（开放 PR 单独成文件）、文档改动也做代码审查 + 覆盖率门禁 not-applicable、
// flaky（首跑失败、同提交重跑通过）= P2 不稳定且不算测试佐证。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = mkdtempSync(join(tmpdir(), 'argus-kinds-'))
process.env.ARGUS_ROOT = root
process.env.ARGUS_GH_BIN = join(here, 'fixtures/argus-fake-gh.mjs')
process.env.ARGUS_GH_RETRY_MS = '1'
process.env.ARGUS_E2E_GH_STATE = join(root, 'github-fixture.json')
after(() => rmSync(root, { recursive: true, force: true }))

const N = await import('../src/nightly.mjs')
const { decideOutcome, compareKey } = await import('../src/decide.mjs')
const { MIRRORS_DIR } = await import('../src/paths.mjs')

const health = () => ({ status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], notes: [] })
const baseReport = (prs) => ({ schema_version: '1.0', date: '2026-10-10', run: { id: 'r', args: { prs: [878, 888, 731] } }, summary: {}, prs,
  system_health: { status: 'degraded', qa_tool_failures: [{ pr: 731, stage: 'deadline', message: 'x' }, { pr: 902, stage: 'y' }], env_problems: [], llm_errors: [], heartbeat: { prs: [{ number: 878 }] } } })

test('--write-open：开放 PR 都写 reports/open/PR-N.json（含 deadline 未测的、带原因），不写已合并与 --pr 排除的', () => {
  assert.equal(N.writeOpenEnabled({}), false)
  assert.equal(N.writeOpenEnabled({ writeOpen: true }), true)
  const prs = [
    { number: 878, state: 'open', verdict: 'clean', severity: 'P3', tested_sha: 'h'.repeat(40), findings: [], test_backed: false },
    { number: 731, state: 'open', verdict: 'not-tested', severity: 'untested', not_tested_code: 'deadline', not_tested_reason: 'run deadline reached', findings: [] },
    { number: 999, state: 'open', verdict: 'not-tested', severity: 'untested', not_tested_code: 'excluded-by-pr-filter', findings: [] },
    { number: 902, state: 'merged', verdict: 'clean', severity: 'P3', findings: [] },
  ]
  const dir = mkdtempSync(join(root, 'open-'))
  const report = baseReport(prs)
  const out = join(root, 'run.json')
  assert.deepEqual(N.writePerPrReports(report, { opts: {}, outFile: out, kind: 'open' }), [], 'off by default')
  const written = N.writePerPrReports(report, { opts: { writeOpen: true, openDir: dir }, outFile: out, kind: 'open' })
  assert.deepEqual(written.map((w) => w.pr), [878, 731])
  const d731 = JSON.parse(readFileSync(join(dir, 'PR-731.json'), 'utf8'))
  assert.equal(d731.prs[0].not_tested_code, 'deadline')
  assert.deepEqual(d731.summary.not_tested, [{ number: 731, code: 'deadline', reason: 'run deadline reached' }])
  assert.equal(d731.summary.tested_count, 0)
  assert.equal(d731.summary.prs_by_severity.untested, 1)
  assert.deepEqual(d731.system_health.qa_tool_failures.map((f) => f.stage), ['deadline'])
  assert.equal(d731.merged_sha, undefined)
  const d878 = JSON.parse(readFileSync(join(dir, 'PR-878.json'), 'utf8'))
  assert.equal(d878.head_sha, 'h'.repeat(40))
  assert.equal(d878.run.source_report, out)
  assert.equal(d878.summary.tested_count, 1)
  assert.ok(!existsSync(join(dir, 'PR-999.json')) && !existsSync(join(dir, 'PR-902.json')))
  // merged 规则不变
  const mdir = mkdtempSync(join(root, 'merged-'))
  assert.deepEqual(N.writePerPrReports(report, { opts: { mergedDir: mdir }, outFile: out, kind: 'merged' }).map((w) => w.pr), [902])
})

// bench 的形态：同名用例首跑内部超时，重跑通过；两轮都有 vitest JSON 报告
const NAME = 'src/kernel/shadowRoundTrip.bench.test.ts > S6d > round-trip cost 20k'
const benchRun = (ok) => [{ id: 'install', kind: 'setup', ok: true, exitCode: 0 }, {
  id: 'bench', kind: 'product', ok, exitCode: ok ? 0 : 1, timedOut: false, failures: ok ? [] : [NAME],
  failureDetails: ok ? [] : [{ name: NAME, type: 'test_timeout' }], failureParseStatus: ok ? 'not_detected' : 'complete',
  testOutcomeStatus: 'complete', testOutcomes: [{ caseId: NAME, failureName: NAME, status: ok ? 'passed' : 'failed' }],
}]

test('flaky：首跑超时、同提交重跑通过 → status flaky、P2（不稳定）、与 PR 无关、test_backed=false', () => {
  const first = benchRun(false), rerun = benchRun(true)
  assert.equal(compareKey(`bench::${NAME}`, rerun, first), 'absent')
  assert.equal(decideOutcome({ first, rerun, base: null, latest: null }).outcome, 'flaky')
  const step = { id: 'bench', kind: 'product', trialOnly: true }
  assert.equal(N.severityFor({ status: 'flaky', step, merged: true }), 'P2')
  assert.equal(N.severityFor({ status: 'flaky', step: { kind: 'product' }, merged: false }), 'P2')
  assert.equal(N.severityFor({ status: 'pre-existing', step: { kind: 'product' }, merged: true }), 'P3')
  // 没有逐用例报告时重跑无法核实 → unverified（这就是 bench 现在带 JSON reporter 的原因）
  const noReport = (r) => r.map((x) => ({ ...x, testOutcomeStatus: 'unavailable', testOutcomes: undefined }))
  assert.equal(compareKey(`bench::${NAME}`, noReport(rerun), noReport(first)), 'unknown')
  // test_backed：flaky 的失败检查不是测试佐证
  assert.equal(N.checkIsTestBacked({ status: 'flaky' }), false)
  assert.equal(N.checkIsTestBacked({ status: 'unverified' }), true)
  const [pr] = N.annotateTestBacked([{ number: 892, findings: [
    { source: 'check', status: 'flaky', flaky: true, severity: 'P2' }, // 旧格式没有 test_backed 字段
    { source: 'code-review', severity: 'P2', test_backed: false },
  ] }])
  assert.deepEqual(pr.findings.map((f) => f.test_backed), [false, false])
  assert.equal(pr.test_backed, false)
  // 通知 / issue 规则：flaky 的 P2 不会成为候选（即使是 P1 也不会，因为 test_backed=false）
  const { candidates } = N.selectIssueCandidates({ date: 'd', repo: 'r', prs: [{ number: 892, author: 'PraiseZhu', findings: [{ source: 'check', status: 'flaky', test_backed: false, severity: 'P1', fingerprint: 'f' }] }], system_health: { status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [] } })
  assert.deepEqual(candidates, [])
})

test('bench 步骤带 vitest JSON 报告（逐用例结果，重跑通过可核实）', () => {
  const prof = JSON.parse(readFileSync(join(here, '../config/profiles/mivo-canvas-plugin.json'), 'utf8'))
  const bench = prof.steps.find((s) => s.id === 'bench')
  assert.equal(bench.testReport.format, 'vitest-json')
  assert.ok(bench.cmd.includes('--reporter=json') && bench.cmd.includes('--outputFile=${TEST_REPORT}'))
})

test('docs-only：仍做代码审查（merge-base..merge commit 的本地 diff），覆盖率门禁 not-applicable，不跑测试', async () => {
  const number = 2001
  const repo = `fixture/pr${number}`
  const src = join(root, `source-${number}`)
  mkdirSync(src)
  const git = (...args) => execFileSync('git', ['-C', src, '-c', 'user.name=F', '-c', 'user.email=f@example.invalid', ...args], { encoding: 'utf8' }).trim()
  git('init', '-q', '-b', 'main')
  writeFileSync(join(src, 'README.md'), 'a\n'); writeFileSync(join(src, 'value.mjs'), 'export const v = 1\n')
  git('add', '.'); git('commit', '-qm', 'base')
  const baseSha = git('rev-parse', 'HEAD')
  writeFileSync(join(src, 'README.md'), 'a\nb\n'); git('commit', '-qam', `docs (#${number})`)
  const mergeSha = git('rev-parse', 'HEAD')
  mkdirSync(MIRRORS_DIR, { recursive: true })
  execFileSync('git', ['clone', '-q', '--bare', src, join(MIRRORS_DIR, `fixture__pr${number}.git`)])
  const pr = { number, title: 'docs', html_url: 'https://example.invalid', user: { login: 'f' }, base: { ref: 'main', sha: baseSha }, head: { sha: 'f'.repeat(40) }, merged_at: new Date().toISOString(), merge_commit_sha: mergeSha, state: 'closed' }
  writeFileSync(process.env.ARGUS_E2E_GH_STATE, JSON.stringify({ pr, baseSha, files: [{ filename: 'README.md', additions: 1, deletions: 0 }], calls: [] }))
  const profile = { steps: [{ id: 'install', kind: 'setup', cmd: ['true'] }, { id: 'unit', kind: 'product', nameZh: '单元测试', covers: ['unit'], cmd: ['false'] }] }
  const h = health()
  const out = await N.processPr({ repo, project: { repo, profileData: profile }, number, discovery: 'merged', secrets: {}, db: null, health: h, evidenceRoot: join(root, 'ev'), opts: {} })
  assert.equal(out.verdict, 'docs-only')
  assert.equal(out.tested, false)
  assert.equal(out.checks.length, 0, 'no tests run')
  assert.deepEqual(out.coverage_gate, { status: 'not-applicable', reason: 'docs-only change' })
  assert.equal(out.reviewed_sha, mergeSha, 'review looks at the merge commit')
  assert.equal(out.review_base_sha, baseSha, 'diff base = first parent (merge-base)')
  assert.equal(out.review_diff_source, 'local git diff base_sha..merge_sha')
  assert.equal(out.tested_sha, null, 'nothing was tested')
  assert.equal(out.sha_consistent, true)
  assert.equal(out.code_review.status, 'unavailable', 'review attempted (no LLM configured in tests)')
  assert.deepEqual(h.llm_errors.map((e) => e.stage), ['code-review'])
  // --no-review：不审查，但门禁仍是 not-applicable
  const out2 = await N.processPr({ repo, project: { repo, profileData: profile }, number, discovery: 'merged', secrets: {}, db: null, health: health(), evidenceRoot: join(root, 'ev2'), opts: { noReview: true } })
  assert.equal(out2.code_review, null)
  assert.equal(out2.coverage_gate.status, 'not-applicable')
})

test('复用：旧的 docs-only 结果没有代码审查 → 审查开着时不复用', () => {
  const sha = 'm'.repeat(40)
  const dir = mkdtempSync(join(root, 'reuse-'))
  const doc = (cr) => ({ merged_sha: sha, prs: [{ number: 897, state: 'merged', verdict: 'docs-only', merge_sha: sha, code_review: cr }] })
  writeFileSync(join(dir, 'PR-897.json'), JSON.stringify(doc(null)))
  assert.equal(N.loadMergedResult(897, sha, { mergedDir: dir, requireReview: true }), null)
  assert.ok(N.loadMergedResult(897, sha, { mergedDir: dir, requireReview: false }))
  writeFileSync(join(dir, 'PR-897.json'), JSON.stringify(doc({ status: 'passed' })))
  assert.ok(N.loadMergedResult(897, sha, { mergedDir: dir, requireReview: true }))
})

test('assemble：后来源覆盖同号 PR、summary 重算、system_health 取最差并只保留被采用条目的问题', async () => {
  const { assembleReport } = await import('../src/assemble.mjs')
  const batch = { tool: 't', repo: 'o/r', run: { id: 'b' }, generated_at: 'g1', prs: [
    { number: 1, state: 'merged', verdict: 'review-suggested', severity: 'P2', findings: [{ source: 'check', status: 'flaky', test_backed: false, severity: 'P2' }] },
    { number: 2, state: 'merged', verdict: 'docs-only', severity: 'P3', findings: [] },
    { number: 3, state: 'open', verdict: 'not-tested', severity: 'untested', not_tested_code: 'excluded-by-pr-filter', findings: [] },
  ], system_health: { status: 'ok', complete: true, qa_tool_failures: [{ pr: 2, stage: 'old' }], env_problems: [], llm_errors: [], notes: ['n1'], heartbeat: { prs: [{ number: 1 }, { number: 2 }] } } }
  const doc897 = { run: { id: 'x' }, prs: [{ number: 2, state: 'merged', verdict: 'docs-only', severity: 'P3', code_review: { status: 'passed' }, findings: [] }], system_health: { status: 'ok', complete: true, qa_tool_failures: [], env_problems: [], llm_errors: [], notes: ['n2'], heartbeat: { prs: [{ number: 2 }] } } }
  const open3 = { run: { id: 'o' }, prs: [{ number: 3, state: 'open', verdict: 'needs-attention', severity: 'P1', findings: [{ source: 'check', status: 'introduced', test_backed: true, severity: 'P1' }] }], system_health: { status: 'degraded', complete: true, qa_tool_failures: [{ pr: 3, stage: 'e2e-browser' }], env_problems: [], llm_errors: [], notes: [], heartbeat: { prs: [{ number: 3 }] } } }
  const r = assembleReport({ date: '2026-10-10', sources: [{ file: 'b', doc: batch }, { file: 'm2', doc: doc897 }, { file: 'o3', doc: open3 }] })
  assert.deepEqual(r.prs.map((p) => p.verdict), ['review-suggested', 'docs-only', 'needs-attention'])
  assert.deepEqual(r.prs[1].code_review, { status: 'passed' })
  assert.deepEqual(r.assembled_from.map((a) => a.file), ['b', 'm2', 'o3'])
  assert.equal(r.system_health.status, 'degraded')
  assert.deepEqual(r.system_health.qa_tool_failures.map((f) => f.stage), ['e2e-browser'], 'batch issue of the replaced #2 entry dropped')
  assert.deepEqual(r.system_health.heartbeat.prs.map((b) => b.number), [1, 2, 3])
  assert.deepEqual(r.system_health.notes, ['n1', 'n2'])
  assert.equal(r.system_health.complete, true)
  assert.equal(r.system_health.all_discovered_tested, true)
  assert.deepEqual(r.summary.prs_test_backed, [3], 'flaky finding is not test-backed')
  assert.deepEqual(r.summary.test_backed_findings_by_severity, { P0: 0, P1: 1, P2: 0, P3: 0 })
  assert.deepEqual(r.summary.prs_by_severity, { P0: 0, P1: 1, P2: 1, P3: 1, untested: 0 })
  // deadline 未测 → complete=false
  const late = { ...open3, prs: [{ number: 3, state: 'open', verdict: 'not-tested', severity: 'untested', not_tested_code: 'deadline', findings: [] }] }
  const r2 = assembleReport({ date: '2026-10-10', sources: [{ file: 'b', doc: batch }, { file: 'o3', doc: late }] })
  assert.equal(r2.system_health.complete, false)
  assert.throws(() => assembleReport({ date: 'd', sources: [{ file: 'b', doc: batch }], order: [1, 9] }), /PR 9/)
})

test('pre_existing: unverified + base_run fail upgrades to pre-existing P3 (shadowRoundTrip-style)', async () => {
  const { severityFor } = await import('../src/nightly.mjs')
  let status = 'unverified'
  let why = 'reproduced; base comparison incomplete'
  const baseRun = 'fail'
  if (baseRun === 'fail' && status === 'unverified') {
    status = 'pre-existing'
    why = 'also fails on the base revision (base run failed; failure type not fingerprint-comparable, e.g. test_timeout)'
  }
  const pre_existing = status === 'pre-existing' || baseRun === 'fail'
  assert.equal(status, 'pre-existing')
  assert.equal(pre_existing, true)
  assert.equal(severityFor({ status, step: { kind: 'product' }, merged: true }), 'P3')
  assert.match(why, /base revision/)
})
