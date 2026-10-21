// 分诊与覆盖：29 维齐全；不适用只来自 profile；可选检查按分诊跑；覆盖只认真实通过的步骤；Jev 失败不改变判定
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, mkdirSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

process.env.ARGUS_ROOT = mkdtempSync(join(tmpdir(), 'argus-matrix-'))
const { DIMENSIONS, toMatrixQuestions, toMatrixState, toTriage, toStepPlan, toCoverage } = await import('../src/matrix.mjs')
const { validateDimensions } = await import('../src/config.mjs')
const { toStepOutcome } = await import('../src/runner.mjs')
const { decideOutcome, failedIds, failureKeys } = await import('../src/decide.mjs')
const { evaluateJev } = await import('../src/jev.mjs')

const EXPECTED = ['unit', 'integration', 'api', 'e2e', 'functional', 'regression', 'boundary', 'exception', 'security', 'permission', 'compatibility', 'performance', 'data', 'visual',
  'permission-boundary', 'money-reward-settlement', 'state-transition', 'async-callback-retry', 'data-consistency', 'negative-path', 'concurrency', 'provably-fair', 'business-rule', 'privacy',
  'ui', 'api-oracle', 'db', 'sideEffects', 'negativeAssertions']

const profile = {
  intentZh: '画布插件',
  notApplicable: { 'money-reward-settlement': '不是游戏或结算业务', 'provably-fair': '不是游戏业务' },
  pathHints: { 'server/**': ['api'] },
  steps: [
    { id: 'install', kind: 'setup', nameZh: '安装依赖', cmd: ['x'] },
    { id: 'unit', kind: 'product', nameZh: '单元测试', cmd: ['x'], covers: ['unit', 'functional'] },
    { id: 'probe', kind: 'product', nameZh: '接口测试（真起服务）', cmd: ['x'], optional: true, covers: ['api'] },
    { id: 'e2e', kind: 'product', nameZh: '浏览器端到端', cmd: ['x'], optional: true, covers: ['e2e', 'ui'] },
  ],
}
const jevAll = (p) => ({ ok: true, model: 'jev-1.13.0', answers: Object.fromEntries(DIMENSIONS.map((d) => [d.key, { type: 'noul', noul: p[d.key] ?? 0.1 }])) })

test('29 个维度与 ming-qa 目录逐项一致；profile 声明不适用的不送 Jev', () => {
  assert.deepEqual(DIMENSIONS.map((d) => d.key).sort(), [...EXPECTED].sort())
  const qs = toMatrixQuestions(profile)
  assert.equal(Object.keys(qs).length, 27)
  assert.ok(!('provably-fair' in qs))
  assert.ok(Object.values(qs).every((x) => x.type === 'noul' && x.instructions.startsWith('Could this code change')))
})

test('发给 Jev 的内容只有标题、说明摘要、文件名和行数，不带改动正文', () => {
  const files = Array.from({ length: 250 }, (_, i) => ({ filename: `f${i}.ts`, changes: i, additions: 1, deletions: 0, patch: 'SECRET DIFF' }))
  const state = toMatrixState({ pr: { title: 'feat(x): 加导出', body: 'b'.repeat(5000) }, files, profile })
  assert.equal(state.changedFiles.length, 200)
  assert.equal(state.changedFiles[0], 'f249.ts')
  assert.equal(state.prDescription.length, 1500)
  assert.ok(!JSON.stringify(state).includes('SECRET DIFF'))
})

test('分诊阈值：≥0.7 涉及、≤0.3 看起来无关、中间拿不准；路径规则直接判涉及；不适用来自 profile', () => {
  const t = toTriage({ profile, files: [{ filename: 'server/app.ts' }], jev: jevAll({ unit: 0.7, e2e: 0.3, ui: 0.5, api: 0.05 }) })
  const v = Object.fromEntries(t.rows.map((r) => [r.key, r]))
  assert.equal(v.unit.verdict, 'applicable')
  assert.equal(v.e2e.verdict, 'looks_unrelated')
  assert.equal(v.ui.verdict, 'undetermined')
  assert.equal(v.api.verdict, 'applicable')
  assert.equal(v.api.source, 'path')
  assert.equal(v['provably-fair'].verdict, 'not_applicable')
  assert.equal(v['provably-fair'].source, 'profile')
})

test('选步骤：基线每次都跑；可选步骤只在涉及或拿不准时跑，跳过写原因；C 级和分诊失败时全跑', () => {
  const t = toTriage({ profile, files: [], jev: jevAll({ api: 0.9, e2e: 0.1, ui: 0.1 }) })
  const plan = toStepPlan({ profile, riskClass: 'B', triage: t })
  assert.deepEqual(plan.stepIds, ['install', 'unit', 'probe'])
  assert.equal(plan.skipped[0].id, 'e2e')
  assert.match(plan.skipped[0].reasonZh, /看起来不涉及真实使用流程（端到端）、界面上的结果/)
  assert.deepEqual(toStepPlan({ profile, riskClass: 'C', triage: t }).stepIds, ['install', 'unit', 'probe', 'e2e'])
  const failed = toTriage({ profile, files: [], jev: { ok: false, reasonZh: '限流' } })
  assert.equal(failed.status, 'skipped')
  assert.deepEqual(toStepPlan({ profile, riskClass: 'B', triage: failed }).stepIds, ['install', 'unit', 'probe', 'e2e'])
  const unsure = toTriage({ profile, files: [], jev: jevAll({ ui: 0.5 }) })
  assert.ok(toStepPlan({ profile, riskClass: 'B', triage: unsure }).stepIds.includes('e2e'), '拿不准就跑')
})

test('覆盖只认真实通过的步骤；没跑成和失败的不算；两个比例分开算', () => {
  const t = toTriage({ profile, files: [], jev: jevAll({ unit: 0.9, functional: 0.9, api: 0.9, e2e: 0.9 }) })
  const results = [
    { id: 'install', kind: 'setup', ok: true },
    { id: 'unit', kind: 'product', ok: true },
    { id: 'probe', kind: 'product', ok: false },
    { id: 'e2e', kind: 'product', ok: false, unavailable: true },
  ]
  const c = toCoverage({ profile, triage: t, results })
  assert.deepEqual(c.covered.sort(), ['functional', 'unit'])
  assert.deepEqual(c.missed.sort(), ['api', 'e2e'])
  assert.equal(c.coverage, 0.5)
  assert.equal(c.passRate, 0.5, '没跑成的不算进通过率分母')
  assert.equal(toCoverage({ profile, triage: toTriage({ profile, files: [], jev: jevAll({}) }), results }).coverage, null)
})

test('只含老失败的步骤仍算测到；拿不准时不算', async () => {
  const { toKnownOnlySteps } = await import('../src/control.mjs')
  const t = toTriage({ profile, files: [], jev: jevAll({ unit: 0.9, functional: 0.9 }) })
  const results = [{ id: 'install', kind: 'setup', ok: true }, { id: 'unit', kind: 'product', ok: false }]
  const known = toKnownOnlySteps(results, { outcome: 'passed_known_failures', failedSteps: ['unit'], knownFailureCount: 3 })
  assert.deepEqual(known, ['unit'])
  assert.equal(toCoverage({ profile, triage: t, results, knownOnlyStepIds: known }).coverage, 1)
  assert.deepEqual(toKnownOnlySteps(results, { outcome: 'needs_review', failedSteps: ['unit'] }), [])
  assert.deepEqual(toKnownOnlySteps(results, { outcome: 'defect', failedSteps: ['unit'] }), [], '有新失败的步骤不算')
})

test('AI 补测失败即使属于老失败也不能算覆盖，必须通过有效性验证', () => {
  const p = { ...profile, steps: [...profile.steps, { id: 'ai-test-1', kind: 'product', aiGenerated: true, covers: ['security'] }] }
  const triage = toTriage({ profile: p, files: [], jev: jevAll({ security: 0.9, unit: 0.9 }) })
  const results = [
    { id: 'unit', kind: 'product', ok: false },
    { id: 'ai-test-1', kind: 'product', ok: false },
  ]
  const covered = toCoverage({ profile: p, triage, results, knownOnlyStepIds: ['unit', 'ai-test-1'] })
  assert.deepEqual(covered.covered, ['unit'])
  assert.deepEqual(covered.missed, ['security'])
  results[1].ok = true
  assert.deepEqual(toCoverage({ profile: p, triage, results, knownOnlyStepIds: ['unit'] }).covered, ['unit', 'security'])
})

test('未判定维度只把实际未覆盖项列入缺口，超时不能靠老失败算覆盖', () => {
  const triage = toTriage({ profile, files: [], jev: jevAll({ unit: 0.5, api: 0.5, security: 0.5 }) })
  const coverage = toCoverage({ profile, triage, results: [
    { id: 'unit', kind: 'product', ok: true },
    { id: 'probe', kind: 'product', ok: false, timedOut: true },
  ], knownOnlyStepIds: ['probe'] })
  assert.deepEqual(coverage.undetermined, ['unit', 'api', 'security'])
  assert.deepEqual(coverage.undeterminedMissed, ['api', 'security'])
})

test('Cindy 配置声明不做宿主真实使用，且没有步骤覆盖这些方面', () => {
  const cindy = JSON.parse(readFileSync(new URL('../config/profiles/cindy.json', import.meta.url)))
  validateDimensions('cindy', cindy)
  assert.ok(cindy.notApplicable.e2e && cindy.notApplicable.visual && cindy.notApplicable.ui)
  assert.ok(!cindy.steps.some((s) => (s.covers ?? []).some((k) => ['e2e', 'visual', 'ui'].includes(k))))
})

test('profile 校验：不认识的维度、既不适用又被覆盖、可选步骤不写 covers 都报错', () => {
  assert.throws(() => validateDimensions('p', { steps: [{ id: 'a', covers: ['nope'] }] }), /不认识的维度：nope/)
  assert.throws(() => validateDimensions('p', { notApplicable: { ui: 'x' }, steps: [{ id: 'a', covers: ['ui'] }] }), /既声明不适用/)
  assert.throws(() => validateDimensions('p', { steps: [{ id: 'a', optional: true }] }), /必须写 covers/)
  assert.throws(() => validateDimensions('p', { pathHints: { 'x/**': ['zzz'] }, steps: [] }), /pathHints/)
})

test('约定的退出码记为没跑成：不进失败对比，结论仍按其余步骤判定', () => {
  const step = { id: 'e2e', unavailableExitCodes: [2] }
  const r = toStepOutcome(step, { id: 'e2e', kind: 'product', ok: false, exitCode: 2, tail: 'x\n浏览器没能启动\n' }, '/tmp/none')
  assert.equal(r.unavailable, true)
  assert.equal(r.unavailableReasonZh, '浏览器没能启动')
  const first = [{ id: 'install', kind: 'setup', ok: true }, { id: 'unit', kind: 'product', ok: true }, r]
  assert.deepEqual(failedIds(first), [])
  assert.equal(decideOutcome({ first, rerun: null, base: null, latest: null }).outcome, 'passed')
})

test('报告缺原因仍保留具体用例为 unknown；报告缺失的 whole key 只作诊断', () => {
  const runDir = mkdtempSync(join(tmpdir(), 'argus-rep-'))
  try {
  mkdirSync(join(runDir, 'out'))
  writeFileSync(join(runDir, 'out', 'report.json'), JSON.stringify({ checks: [{ id: 'a', status: 'fail' }, { id: 'b', status: 'pass' }, { id: 'c', status: 'fail' }] }))
  const step = { id: 'e2e', failureReport: { format: 'checks', path: '${RUN}/out/report.json' } }
  const r = toStepOutcome(step, { id: 'e2e', kind: 'product', ok: false, exitCode: 1, tail: '' }, runDir)
  assert.deepEqual(r.failures, ['a', 'c'])
  assert.deepEqual(r.failureDetails, [{ name: 'a', type: 'unknown' }, { name: 'c', type: 'unknown' }])
  assert.equal(decideOutcome({ first: [r], rerun: [r], base: [{ ...r, ok: true, exitCode: 0 }], latestIsSame: true }).outcome, 'needs_review')
  const missing = toStepOutcome({ ...step, failureReport: { format: 'checks', path: '${RUN}/nope.json' } }, { id: 'e2e', kind: 'product', ok: false, exitCode: 1, tail: '' }, runDir)
  assert.deepEqual(missing.failures, [])
  assert.deepEqual(failureKeys([missing]), ['e2e::*'], '认不出用例时整步作为一个失败')
  assert.equal(missing.failureParseStatus, 'incomplete')
  assert.equal(decideOutcome({ first: [missing], rerun: [missing], base: [{ ...missing, ok: true, exitCode: 0 }], latestIsSame: true }).outcome, 'needs_review')
  } finally { rmSync(runDir, { recursive: true, force: true }) }
})

test('checks 报告只把明确产品 oracle 不符当断言；超时和未完成不能确认', () => {
  const runDir = mkdtempSync(join(tmpdir(), 'argus-check-types-'))
  try {
    const step = { id: 'browser', failureReport: { format: 'checks', path: '${RUN}/report.json' } }
    for (const [id, reason, type, outcome] of [
      ['browser-product-roundtrip', 'product journey failed persisted={"ok":false}', 'assertion', 'defect'],
      ['browser-host-probe', 'CDP timeout Page.navigate', 'test_timeout', 'needs_review'],
      ['browser-product-roundtrip', 'product persist/export roundtrip did not complete', 'runner_error', 'needs_review'],
      ['candidate-snapshot-stable', 'candidate files changed during layer run', 'runner_error', 'needs_review'],
      ['browser-product-roundtrip', 'unexpected new producer reason', 'unknown', 'needs_review'],
    ]) {
      writeFileSync(join(runDir, 'report.json'), JSON.stringify({ status: 'fail', checks: [{ id, status: 'fail', reason }] }))
      const r = toStepOutcome(step, { id: 'browser', kind: 'product', ok: false, exitCode: 1 }, runDir)
      assert.equal(r.failureDetails[0].type, type)
      const d = decideOutcome({ first: [r], rerun: [r], base: [{ id: 'browser', ok: true, exitCode: 0, testOutcomeStatus: 'complete', testOutcomes: [{ caseId: id, failureName: id, status: 'passed' }] }], latestIsSame: true })
      assert.equal(d.outcome, outcome)
    }
    writeFileSync(join(runDir, 'report.json'), JSON.stringify({ checks: [
      { id: 'browser-product-roundtrip', status: 'fail', reason: 'product journey failed persisted={}' },
      { id: 'candidate-snapshot-stable', status: 'fail', reason: 'candidate files changed during layer run' },
    ] }))
    const unstable = toStepOutcome(step, { id: 'browser', ok: false, exitCode: 1 }, runDir)
    assert.equal(unstable.failureParseStatus, 'incomplete')
    assert.equal(decideOutcome({ first: [unstable], rerun: [unstable], base: [{ id: 'browser', ok: true, exitCode: 0 }], latestIsSame: true }).outcome, 'needs_review')
  } finally { rmSync(runDir, { recursive: true, force: true }) }
})

test('坏报告、重复 id、零失败、不可用、矛盾状态及超限报告均不得 whole 确认', () => {
  const runDir = mkdtempSync(join(tmpdir(), 'argus-bad-report-'))
  try {
    const step = { id: 'e2e', failureReport: { format: 'checks', path: '${RUN}/report.json' } }
    for (const content of [
      '{', JSON.stringify({ checks: [] }), JSON.stringify({ checks: [{ id: 'a', status: 'pass' }] }),
      JSON.stringify({ checks: [{ id: 'a', status: 'fail' }, { id: 'a', status: 'fail' }] }),
      JSON.stringify({ checks: [{ id: 'a', status: 'unavailable' }] }),
      JSON.stringify({ checks: [{ id: '', status: 'fail' }] }),
      JSON.stringify({ status: 'pass', checks: [{ id: 'a', status: 'fail' }] }),
      JSON.stringify({ checks: Array.from({ length: 1001 }, (_, i) => ({ id: `a${i}`, status: 'fail' })) }),
      ' '.repeat(1024 * 1024 + 1),
    ]) {
      writeFileSync(join(runDir, 'report.json'), content)
      const r = toStepOutcome(step, { id: 'e2e', ok: false, exitCode: 1 }, runDir)
      assert.equal(r.failureParseStatus, 'incomplete')
      assert.equal(decideOutcome({ first: [r], rerun: [r], base: [{ id: 'e2e', ok: true, exitCode: 0 }], latestIsSame: true }).outcome, 'needs_review')
    }
  } finally { rmSync(runDir, { recursive: true, force: true }) }
})

test('测试命令缺 reporter 待核实，普通 build/quality 命令保留完整退出的整步对照', () => {
  const testCommands = [
    ['npm', 'run', 'test:unit'], ['node', 'scripts/run-vitest.mjs', 'run', 'server'],
    ['.venv/bin/python', '-m', 'pytest', '-q'], ['pnpm', 'run', 'test:workspaces', '--tier', 'unit'],
    ['pnpm', 'test:runner'], ['pnpm', 'test:git-integration'], ['npm', 'test'],
    [process.execPath, '--test', '--test-reporter=spec', 'behavior.test.mjs'], ['pnpm', 'exec', 'vitest', 'run'],
  ]
  for (const cmd of testCommands) {
    const r = toStepOutcome({ id: 'check', cmd }, { id: 'check', ok: false, exitCode: 1, failures: [], failureParseStatus: 'not_detected' }, '/tmp/unused')
    assert.equal(r.failureParseStatus, 'incomplete', cmd.join(' '))
    assert.equal(decideOutcome({ first: [r], rerun: [r], base: [{ id: 'check', ok: true, exitCode: 0 }], latestIsSame: true }).outcome, 'needs_review')
  }
  for (const cmd of [['npx', 'tsc', '-b'], ['bash', 'cindyplugin/build.sh'], ['npm', 'run', 'lint']]) {
    const r = toStepOutcome({ id: 'build', cmd }, { id: 'build', ok: false, exitCode: 1, failures: [], failureParseStatus: 'not_detected' }, '/tmp/unused')
    assert.equal(r.failureParseStatus, 'not_detected')
    assert.equal(decideOutcome({ first: [r], rerun: [r], base: [{ id: 'build', ok: true, exitCode: 0 }], latestIsSame: true }).outcome, 'defect')
  }
})

// jev client

function stubFetch(responses) {
  const calls = []
  globalThis.fetch = async (url, init) => {
    calls.push({ url, auth: init.headers.authorization })
    const r = responses.shift()
    if (r instanceof Error) throw r
    return { status: r.status, headers: new Map(Object.entries(r.headers ?? {})), json: async () => r.body }
  }
  return calls
}
const secrets = { ARGUS_TYPESAFE_API_KEY: 'k-test-123' }
const qs = { a: { type: 'noul', instructions: 'x' } }
const good = { status: 200, body: { model: 'jev-1.13.0', answers: { a: { type: 'noul', noul: 0.8 } }, usage: { input_tokens: 10 } } }

test('Jev：固定版本号；429 退避后成功；401 不重试；格式不对、没 key 都返回失败且不含 key', async () => {
  let calls = stubFetch([good])
  let r = await evaluateJev({ state: 's', questions: qs, secrets, retryDelays: [1, 1, 1] })
  assert.equal(r.ok, true)
  assert.equal(r.model, 'jev-1.13.0')
  calls = stubFetch([{ status: 429 }, { status: 529 }, good])
  r = await evaluateJev({ state: 's', questions: qs, secrets, retryDelays: [1, 1, 1] })
  assert.equal(r.ok, true)
  assert.equal(calls.length, 3)
  calls = stubFetch([{ status: 401 }, good])
  r = await evaluateJev({ state: 's', questions: qs, secrets, retryDelays: [1, 1, 1] })
  assert.equal(r.ok, false)
  assert.equal(calls.length, 1)
  assert.ok(!r.reasonZh.includes('k-test-123'))
  stubFetch([{ status: 200, body: { answers: {} } }])
  assert.match((await evaluateJev({ state: 's', questions: qs, secrets, retryDelays: [] })).reasonZh, /格式不对/)
  stubFetch([new Error('ECONNRESET k-test-123'), new Error('ECONNRESET')])
  r = await evaluateJev({ state: 's', questions: qs, secrets, retryDelays: [1] })
  assert.equal(r.ok, false)
  assert.equal((await evaluateJev({ state: 's', questions: qs, secrets: {} })).ok, false)
})

test('改了脚本且自带测试（#1116）：unit / regression 适用，即使 Jev 判不涉及', () => {
  const files = [{ filename: 'scripts/ci/pr-handoff-gate.cjs' }, { filename: 'scripts/ci/pr-handoff-gate.test.mjs' }]
  const t = toTriage({ profile, files, jev: jevAll({ unit: 0.01, regression: 0.01 }) })
  const by = Object.fromEntries(t.rows.map((r) => [r.key, r]))
  if (by.unit) { assert.equal(by.unit.verdict, 'applicable'); assert.equal(by.unit.source, 'ships-tests') }
  if (by.regression) assert.equal(by.regression.verdict, 'applicable')
  assert.ok(by.unit || by.regression)
})

test('只改文档 + 测试 不算改代码；只改代码不带测试 不强制', () => {
  const docs = toTriage({ profile, files: [{ filename: 'docs/a.md' }, { filename: 'x.test.mjs' }, { filename: 'b.mjs' }].slice(0, 1), jev: jevAll({ unit: 0.01 }) })
  assert.notEqual(docs.rows.find((r) => r.key === 'unit')?.source, 'ships-tests')
  const noTests = toTriage({ profile, files: [{ filename: 'scripts/a.mjs' }], jev: jevAll({ unit: 0.01 }) })
  assert.notEqual(noTests.rows.find((r) => r.key === 'unit')?.source, 'ships-tests')
  const docAndTest = toTriage({ profile, files: [{ filename: 'docs/a.md' }, { filename: 'x.test.mjs' }], jev: jevAll({ unit: 0.01 }) })
  assert.notEqual(docAndTest.rows.find((r) => r.key === 'unit')?.source, 'ships-tests')
})
