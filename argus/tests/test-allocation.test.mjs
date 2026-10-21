// 测试分配改进（#1101 / #1099 / 覆盖率模式单测 / run-nightly --out）：夹具取自真实 PR 的 diff 与报告，全部本地、不调模型
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFileSync } from 'node:child_process'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-alloc-'))
const { toTriage, toApplicability, toStepPlan } = await import('../src/matrix.mjs')
const { dimensionsOf, verdictOf, withDimensionStatus, splitDiffFiles, coverageMaybeLow } = await import('../src/nightly.mjs')
const { toAiGaps } = await import('../src/aitests.mjs')
const { prTestDimensions } = await import('../src/pr-tests.mjs')
const { vitestCoverageArgs } = await import('../src/coverage.mjs')
const { catchUpChildArgs } = await import('../src/catchup.mjs')
const { loadProfile } = await import('../src/config.mjs')

const HERE = dirname(fileURLToPath(import.meta.url))
const FX = join(HERE, 'fixtures/alloc')
const jevP = JSON.parse(readFileSync(join(FX, 'jev-p.json'), 'utf8'))
const jevOf = (n) => ({ ok: true, model: 'mock', answers: Object.fromEntries(Object.entries(jevP[n]).map(([k, p]) => [k, { noul: p }])) })
const diffFiles = (n) => splitDiffFiles(readFileSync(join(FX, `pr-${n}.diff`), 'utf8'))
const mivo = loadProfile('mivo-canvas-plugin')

// ── (a) #1101：只改测试 ──────────────────────────────
test('(a) #1101 只改测试的 PR：unit 和 regression 判为适用（Jev 全 < 0.3 也一样）', () => {
  const files = diffFiles(1101)
  assert.deepEqual(files.map((f) => f.filename), ['scripts/ops/gh-app-router/gh-app-router.test.mjs'])
  const triage = toTriage({ profile: mivo, files, jev: jevOf(1101) })
  const verdict = Object.fromEntries(triage.rows.map((r) => [r.key, r.verdict]))
  assert.equal(verdict.unit, 'applicable')
  assert.equal(verdict.regression, 'applicable')
  assert.equal(verdict.e2e, 'looks_unrelated', '其它维度仍听 Jev')
  const app = toApplicability({ ...triage, status: 'ok' })
  assert.ok(app.applicable.includes('unit') && app.applicable.includes('regression'))
  assert.equal(app.dimensions.unit.source, 'tests-only')
  // 混有非测试代码时不再算 tests-only，改走 ships-tests（改代码且自带测试）
  const mixed = toTriage({ profile: mivo, files: [...files, { filename: 'src/a.ts' }], jev: jevOf(1101) })
  const mixedUnit = mixed.rows.find((r) => r.key === 'unit')
  assert.equal(mixedUnit.verdict, 'applicable')
  assert.equal(mixedUnit.source, 'ships-tests')
  // 混的是文档时两条规则都不套用，听 Jev
  const withDoc = toTriage({ profile: mivo, files: [...files, { filename: 'docs/a.md' }], jev: jevOf(1101) })
  assert.equal(withDoc.rows.find((r) => r.key === 'unit').verdict, 'looks_unrelated')
})

test('(a) #1101 维度的步骤全因 triage 判不适用而跳过：不进 dimensions_missing，verdict 不降为 partially-verified', () => {
  const rep = JSON.parse(readFileSync(join(FX, 'pr-1101-report.json'), 'utf8'))
  const d = dimensionsOf(rep.checks, rep.skipped_steps)
  assert.deepEqual(d.dimensions_missing, [])
  assert.deepEqual(d.dimensions_not_applicable, ['black-box'])
  assert.equal(verdictOf({ ...rep, ...d }), 'clean')
  // 有一个黑盒步骤是「没跑到」（不是 triage）：仍算缺
  const stopped = [...rep.skipped_steps, { id: 'x', dimension: 'black-box', reason: 'not run: run stopped before this step (time budget / shutdown)' }]
  assert.deepEqual(dimensionsOf(rep.checks, stopped).dimensions_missing, ['black-box'])
  // 黑盒有检查但 unavailable：仍算缺
  assert.deepEqual(dimensionsOf([...rep.checks, { id: 'e2e-browser', kind: 'product', dimension: 'black-box', status: 'unavailable' }], rep.skipped_steps).dimensions_missing, ['black-box'])
  // 不传 skipped（旧调用）行为不变
  assert.deepEqual(dimensionsOf(rep.checks).dimensions_missing, ['black-box'])
})

test('(a) #1101 修正后的分诊让覆盖 unit/regression 的可选步骤进计划', () => {
  const files = diffFiles(1101)
  const plan = toStepPlan({ profile: mivo, riskClass: 'B', triage: toTriage({ profile: mivo, files, jev: jevOf(1101) }), files })
  // pytest（可选，covers unit/regression，exercises scripts/ops/**）：#1101 当时被 triage 跳过
  assert.ok(plan.stepIds.includes('pytest'))
  assert.ok(plan.skipped.every((s) => s.id !== 'pytest'))
})

// ── (b) #1099：PR 自带测试覆盖的维度 ─────────────────
const unitOk = [{ id: 'install', kind: 'setup', ok: true }, { id: 'unit', kind: 'product', ok: true }]
test('(b) #1099 PR 自己新增的测试覆盖的维度：exception / async-callback-retry / concurrency', () => {
  const files = diffFiles(1099)
  const dims = prTestDimensions({ files, results: unitOk, profile: mivo })
  for (const k of ['unit', 'regression', 'exception', 'async-callback-retry', 'concurrency']) assert.ok(dims[k]?.length, `${k} 应被 PR 测试覆盖`)
  assert.ok(dims.concurrency.includes('scripts/loops/automerge/state-lock.test.mjs'))
  // 跑这些测试的步骤没跑成：不算
  assert.deepEqual(prTestDimensions({ files, results: [{ id: 'unit', kind: 'product', ok: false, unavailable: true }], profile: mivo }), {})
})

test('(b) #1099 AI 缺口先扣掉 PR 自带测试覆盖的维度；dimension_status 记为 covered', () => {
  const files = diffFiles(1099)
  const triage = toApplicability({ ...toTriage({ profile: mivo, files, jev: jevOf(1099) }), status: 'ok' })
  assert.deepEqual(triage.applicable.filter((k) => ['exception', 'async-callback-retry', 'concurrency'].includes(k)).sort(), ['async-callback-retry', 'concurrency', 'exception'])
  assert.deepEqual(toAiGaps({ applicability: triage, profile: mivo, results: unitOk, files }), [])
  const cov = withDimensionStatus({}, triage, null, mivo, unitOk, files)
  for (const k of ['exception', 'async-callback-retry', 'concurrency']) {
    assert.equal(cov.dimension_status[k].status, 'covered', k)
    assert.ok(cov.dimension_status[k].pr_tests.length)
  }
  assert.ok(cov.covered.includes('concurrency'))
  // 改动里没有测试文件时照旧是缺口
  const src = files.filter((f) => !f.filename.endsWith('.test.mjs'))
  assert.ok(toAiGaps({ applicability: triage, profile: mivo, results: unitOk, files: src }).includes('concurrency'))
})

// ── (c) 覆盖率模式 ───────────────────────────────────
test('(c) 覆盖率模式调高 vitest testTimeout / hookTimeout', () => {
  const a = vitestCoverageArgs('/x')
  assert.ok(a.includes('--testTimeout=30000'))
  assert.ok(a.includes('--hookTimeout=30000'))
  process.env.ARGUS_COVERAGE_TEST_TIMEOUT_MS = '60000'
  try { assert.ok(vitestCoverageArgs('/x').includes('--testTimeout=60000')) } finally { delete process.env.ARGUS_COVERAGE_TEST_TIMEOUT_MS }
})

test('(c) 覆盖率模式下失败的测试文件导入了 PR 改动的文件 → 新代码覆盖率可能偏低', () => {
  const sources = {
    'cindyplugin/src/__tests__/library.test.ts': "import { x } from '../library'\nimport y from '../../shared/z.js'\n",
    'cindyplugin/src/__tests__/depositBindingLock.test.ts': "import { lock } from '../deposit/bindingLock.ts'\n",
  }
  const results = [{ id: 'unit', ok: false, testOutcomeStatus: 'complete', testOutcomes: [
    { caseId: 'cindyplugin/src/__tests__/library.test.ts > lib > loads', status: 'failed' },
    { caseId: 'cindyplugin/src/__tests__/depositBindingLock.test.ts > lock', status: 'failed' },
    { caseId: 'cindyplugin/src/__tests__/ok.test.ts > ok', status: 'passed' },
  ] }]
  const hit = coverageMaybeLow({ results, changedFiles: ['cindyplugin/src/library/index.ts'], readFile: (f) => sources[f] ?? null })
  assert.equal(hit.new_code_maybe_low, true)
  assert.deepEqual(hit.failed_tests_importing_changes, [{ test_file: 'cindyplugin/src/__tests__/library.test.ts', imports: ['cindyplugin/src/library/index.ts'] }])
  const miss = coverageMaybeLow({ results, changedFiles: ['server/other.ts'], readFile: (f) => sources[f] ?? null })
  assert.equal(miss.new_code_maybe_low, false)
  assert.deepEqual(miss.failed_tests_importing_changes, [])
  // 失败用例名只在 failures 里（没有测试报告）
  const fromFailures = coverageMaybeLow({ results: [{ id: 'unit', ok: false, failures: ['cindyplugin/src/__tests__/depositBindingLock.test.ts > lock > times out'] }], changedFiles: ['cindyplugin/src/deposit/bindingLock.ts'], readFile: (f) => sources[f] ?? null })
  assert.equal(fromFailures.new_code_maybe_low, true)
})

test('(c) report-schema 写了新字段', () => {
  const doc = readFileSync(join(HERE, '../../docs/report-schema.md'), 'utf8')
  for (const f of ['new_code_maybe_low', 'failed_tests_importing_changes', 'dimensions_not_applicable', 'pr_tests', 'tests-only']) assert.ok(doc.includes(f), f)
})

// ── (d) run-nightly.sh --out 相对路径 ────────────────
const SCRIPT = join(HERE, '../../run-nightly.sh')
const resolveOut = (out, { cwd, qaHome }) => execFileSync('bash', ['-c', `set -euo pipefail; eval "$(sed -n '/^resolve_out()/,/^}/p' "$0")"; resolve_out "$1"`, SCRIPT, out],
  { cwd, env: { ...process.env, QA_HOME: qaHome }, encoding: 'utf8' }).trim()
test('(d) run-nightly.sh：相对 --out 按调用目录解析，reports/ 开头按 QA_HOME；绝对路径不变', () => {
  const cwd = mkdtempSync(join(tmpdir(), 'argus-out-'))
  assert.equal(resolveOut('reports/merged/PR-1098.json', { cwd, qaHome: '/q' }), '/q/reports/merged/PR-1098.json')
  assert.equal(resolveOut('x/PR-1.json', { cwd, qaHome: '/q' }), join(cwd, 'x/PR-1.json'))
  assert.equal(resolveOut('./reports/a.json', { cwd, qaHome: '/q' }), join(cwd, 'reports/a.json'))
  assert.equal(resolveOut('/abs/a.json', { cwd, qaHome: '/q' }), '/abs/a.json')
  assert.ok(!resolveOut('reports/merged/PR-1098.json', { cwd: join(HERE, '..'), qaHome: '/q' }).includes('/argus/'), '不再落到 argus 目录')
})

test('(d) catch-up 子进程的 --out（reports/catch-up/PR-N.json）不受影响', () => {
  const args = catchUpChildArgs({ number: 1099, day: '2026-10-19' }, { script: SCRIPT, reportsDir: '/q/reports' })
  const out = args[args.indexOf('--out') + 1]
  assert.equal(out, '/q/reports/catch-up/PR-1099.json')
  assert.equal(resolveOut(out, { cwd: join(HERE, '..'), qaHome: '/q' }), out)
  // 即使有人写成相对的 reports/catch-up/…，也落在 QA_HOME
  assert.equal(resolveOut('reports/catch-up/PR-1099.json', { cwd: join(HERE, '..'), qaHome: '/q' }), '/q/reports/catch-up/PR-1099.json')
})

// ── prTestDimensions 关键词收紧：反例 ─────────────────
const fakeTest = (filename, lines) => ({ filename, status: 'added', patch: lines.map((l) => `+${l}`).join('\n') })
test('prTestDimensions 反例：只用 setTimeout（以及 callback / mockResolvedValue）不算覆盖 async-callback-retry', () => {
  const f = fakeTest('tests/timer.test.mjs', ["it('waits', async () => {", '  await new Promise((r) => setTimeout(r, 10))', '  const cb = vi.fn(); api.mockResolvedValue(1); run(callback)', '  expect(cb).toHaveBeenCalled()', '})'])
  const dims = prTestDimensions({ files: [f], results: unitOk, profile: mivo })
  assert.ok(dims.unit?.length, '测试本身应被执行')
  assert.equal(dims['async-callback-retry'], undefined)
})
test('prTestDimensions 反例：只有 block 不算覆盖 concurrency', () => {
  const f = fakeTest('tests/block.test.mjs', ["it('renders a code block', () => {", '  const block = render(blocks[0])', '  expect(block.blocked).toBe(false)', '})'])
  const dims = prTestDimensions({ files: [f], results: unitOk, profile: mivo })
  assert.ok(dims.unit?.length, '测试本身应被执行')
  assert.equal(dims.concurrency, undefined)
})
