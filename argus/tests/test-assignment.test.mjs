// 测试分配（审计 2026-10-18 S1/S2/S7）：非 vitest 的测试要有步骤跑、改到的测试文件必须被执行、
// 步骤只在改动碰到它实际测的路径时才算覆盖、scripts/ci 归 CI、ghost.json 归配置
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-assign-'))
const { toStepPlan, toCoverage, stepExercises } = await import('../src/matrix.mjs')
const { withDimensionStatus, ownTestsNotRun } = await import('../src/nightly.mjs')
const { toAiGaps, fileKind, aiTestTier } = await import('../src/aitests.mjs')
const { normalizeTestOutcomes } = await import('../src/test-outcomes.mjs')
const { loadProfile } = await import('../src/config.mjs')

const F = (...names) => names.map((filename) => ({ filename }))
const mivo = loadProfile('mivo-canvas-plugin')
const step = (id) => mivo.steps.find((s) => s.id === id)
const unrelated = { status: 'ok', rows: [{ key: 'unit', verdict: 'looks_unrelated' }, { key: 'regression', verdict: 'looks_unrelated' }, { key: 'performance', verdict: 'looks_unrelated' }] }

test('mivo profile: node-test / pytest 步骤存在，审计里漏跑的测试文件都落在某一步的 runsFiles 里', () => {
  assert.equal(step('node-test').testReport.format, 'node-events')
  assert.equal(step('pytest').testReport.format, 'junit-xml')
  const plan = toStepPlan({ profile: mivo, riskClass: 'B', triage: unrelated, files: F('.github/scripts/tests/review_pr_workflow.node-test.mjs', '.github/scripts/tests/test_review_models.py', 'src/kernel/shadowRoundTrip.bench.test.ts') })
  for (const id of ['node-test', 'pytest', 'bench']) assert.ok(plan.stepIds.includes(id), id)
})

test('toStepPlan：改动含可选步骤自己执行的测试文件 → 必须跑；不含 → 照旧按分诊跳过', () => {
  const profile = { steps: [{ id: 'unit', kind: 'product' }, { id: 'bench', kind: 'product', optional: true, covers: ['performance'], runsFiles: ['**/*.bench.test.ts'] }] }
  assert.deepEqual(toStepPlan({ profile, riskClass: 'B', triage: unrelated, files: F('src/a.bench.test.ts') }).stepIds, ['unit', 'bench'])
  const p = toStepPlan({ profile, riskClass: 'B', triage: unrelated, files: F('src/a.ts') })
  assert.deepEqual(p.stepIds, ['unit']); assert.equal(p.skipped[0].id, 'bench')
  assert.deepEqual(toStepPlan({ profile, riskClass: 'B', triage: unrelated }).stepIds, ['unit'], '不传 files 行为不变')
})

test('ownTestsNotRun：改过的测试文件不在任何测试报告里 → 列出；报告里有 / 已删除 / 报告不完整说不清 → 不列', () => {
  const profile = { steps: [{ id: 'unit', testReport: { format: 'vitest-json' } }, { id: 'node-test', testReport: { format: 'node-events' }, runsFiles: ['**/*.node-test.mjs'] }, { id: 'lint' }] }
  const files = [{ filename: 'src/a.test.ts' }, { filename: 'x/b.node-test.mjs' }, { filename: 'x/test_c.py' }, { filename: 'src/gone.test.ts', status: 'removed' }, { filename: 'src/a.ts' }]
  const results = [{ id: 'unit', testOutcomeStatus: 'complete', testOutcomes: [{ caseId: 'src/a.test.ts > t' }] }, { id: 'lint', ok: true }]
  assert.deepEqual(ownTestsNotRun({ files, results, profile }), ['x/b.node-test.mjs', 'x/test_c.py'])
  const withNode = [...results, { id: 'node-test', testOutcomeStatus: 'incomplete', testOutcomes: [] }]
  assert.deepEqual(ownTestsNotRun({ files, results: withNode, profile }), ['x/test_c.py'], 'node-test 报告不完整：说不清，不报')
  const unitBroken = [{ id: 'unit', testOutcomeStatus: 'incomplete', testOutcomes: [] }]
  assert.deepEqual(ownTestsNotRun({ files, results: unitBroken, profile }), [], '没写 runsFiles 的报告步骤不完整：所有文件都说不清')
  assert.deepEqual(ownTestsNotRun({ files: F('src/a.ts'), results, profile }), [])
})

test('junit-xml（pytest xunit1）报告 → 用例结果，路径带文件', () => {
  const root = mkdtempSync(join(tmpdir(), 'argus-junit-'))
  const raw = `<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="3">
<testcase classname="tests.test_m.T" name="test_ok" file=".github/scripts/tests/test_m.py" line="3" time="0.1" />
<testcase classname="tests.test_m.T" name="test_bad" file=".github/scripts/tests/test_m.py" line="9"><failure message="x">boom</failure></testcase>
<testcase classname="tests.test_m.T" name="test_skip" file=".github/scripts/tests/test_m.py"><skipped message="s"/></testcase>
</testsuite></testsuites>`
  const o = normalizeTestOutcomes({ format: 'junit-xml', raw, sourceRoot: root })
  assert.equal(o.testOutcomeStatus, 'complete')
  assert.deepEqual(o.testOutcomes.map((r) => r.status), ['passed', 'failed', 'skipped'])
  assert.equal(o.testOutcomes[0].caseId, '.github/scripts/tests/test_m.py > tests.test_m.T > test_ok')
  assert.equal(normalizeTestOutcomes({ format: 'junit-xml', raw: 'garbage', sourceRoot: root }).testOutcomeStatus, 'incomplete')
})

test('覆盖只认改动碰到步骤 exercises 的步骤：withDimensionStatus / toAiGaps / toCoverage 一致', () => {
  const profile = { steps: [{ id: 'unit', covers: ['unit'] }, { id: 'api', covers: ['api', 'state-transition'], exercises: ['server/**'] }] }
  const results = [{ id: 'unit', ok: true }, { id: 'api', ok: true }]
  const dims = { unit: { applicability: 'applicable' }, api: { applicability: 'applicable' }, 'state-transition': { applicability: 'applicable' } }
  const triage = { status: 'ok', dimensions: dims }
  const docs = withDimensionStatus({}, triage, null, profile, results, F('scripts/ops/gh.mjs'))
  assert.equal(docs.dimension_status.unit.status, 'covered', '没写 exercises = 整个仓')
  assert.equal(docs.dimension_status.api.status, 'gap')
  assert.deepEqual(docs.dimension_status.api.checks, [])
  assert.equal(withDimensionStatus({}, triage, null, profile, results, F('server/x.ts')).dimension_status.api.status, 'covered')
  assert.equal(withDimensionStatus({}, triage, null, profile, results).dimension_status.api.status, 'covered', '拿不到文件列表：按碰到算')
  const applicability = { applicable: ['unit', 'api', 'state-transition'] }
  assert.deepEqual(toAiGaps({ applicability, profile, results, files: F('scripts/ops/gh.mjs') }), ['state-transition'])
  assert.deepEqual(toAiGaps({ applicability, profile, results, files: F('server/x.ts') }), [])
  const rows = [{ key: 'api', verdict: 'applicable' }]
  assert.deepEqual(toCoverage({ profile, triage: { rows }, results, files: F('docs/a.md') }).missed, ['api'])
  assert.equal(stepExercises(step('api-inproc'), ['AGENTS.md']), false)
  assert.equal(stepExercises(step('api-inproc'), ['shared/x.ts']), true)
})

test('fileKind：scripts/ci/** 归 CI，ghost.json 归配置；版本号 PR 不再按产品代码给 AI 补测', () => {
  assert.equal(fileKind('scripts/ci/vitest-headroom-reporter.mjs'), 'ci')
  assert.equal(fileKind('cindyplugin/ghost.json'), 'config')
  assert.equal(fileKind('scripts/run-vitest.mjs'), 'product')
  const t = aiTestTier({ files: [{ filename: 'cindyplugin/ghost.json', additions: 1, deletions: 1 }, { filename: 'public/changelog.json', additions: 5, deletions: 0 }], riskClass: 'C', env: {} })
  assert.equal(t.tier.type, 'docs'); assert.equal(t.tier.tier, 'none')
  assert.equal(aiTestTier({ files: [{ filename: 'scripts/ci/structure-guard.mjs', additions: 80, deletions: 4 }], riskClass: 'B', env: {} }).tier.type, 'ci')
})
