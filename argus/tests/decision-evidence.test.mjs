import { test } from 'node:test'
import assert from 'node:assert/strict'
import { decideOutcome, compareKey } from '../src/decide.mjs'
import { toCoverage } from '../src/matrix.mjs'

const step = (statuses, extra = {}) => {
  const failures = Object.keys(statuses).filter((name) => statuses[name] === 'failed')
  return [{ id: 'unit', kind: 'product', ok: !failures.length, exitCode: failures.length ? 1 : 0,
    timedOut: false, failures, failureDetails: failures.map((name) => ({ name, type: 'assertion' })),
    failureParseStatus: 'complete', testOutcomeStatus: 'complete',
    testOutcomes: Object.entries(statuses).map(([failureName, status]) => ({ caseId: failureName, failureName, status })), ...extra }]
}

test('重跑新增失败不会被老失败掩盖，也不贡献完整覆盖', () => {
  const d = decideOutcome({ first: step({ old: 'failed', fresh: 'passed' }), rerun: step({ old: 'failed', fresh: 'failed' }), base: step({ old: 'failed', fresh: 'passed' }) })
  assert.equal(d.outcome, 'needs_review')
  assert.deepEqual(d.knownOnlyStepIds, [])
  assert.ok(d.unverifiedFailures.some((f) => f.key === 'unit::fresh' && f.phase === 'rerun'))
})

test('原失败消失但重跑另有失败，不声称重跑通过', () => {
  const d = decideOutcome({ first: step({ A: 'failed', B: 'passed' }), rerun: step({ A: 'passed', B: 'failed' }) })
  assert.equal(d.outcome, 'needs_review')
  assert.equal(d.transientFailureCount, 1)
})

test('最新主干仅剩新失败，不声称最新主干已经通过', () => {
  const d = decideOutcome({ first: step({ A: 'failed', B: 'passed' }), rerun: step({ A: 'failed', B: 'passed' }), base: step({ A: 'passed', B: 'passed' }), latest: step({ A: 'passed', B: 'failed' }) })
  assert.equal(d.outcome, 'needs_review')
  assert.ok(d.unverifiedFailures.some((f) => f.key === 'unit::B' && f.phase === 'latest'))
})

test('目标跳过、待办、缺失、旧数据或不完整报告不能作为基线通过', () => {
  const first = step({ target: 'failed' })
  const legacy = step({ target: 'passed' })
  delete legacy[0].testOutcomes; delete legacy[0].testOutcomeStatus
  for (const base of [step({ target: 'skipped' }), step({ target: 'todo' }), step({}), legacy, step({ target: 'passed' }, { testOutcomeStatus: 'incomplete' })]) {
    assert.equal(compareKey('unit::target', base, first), 'unknown')
    assert.equal(decideOutcome({ first, rerun: first, base, latestIsSame: true }).outcome, 'needs_review')
  }
})

test('已确认缺陷仍能发布，附带新失败单独待核实', () => {
  const first = step({ A: 'failed', B: 'passed' })
  const d = decideOutcome({ first, rerun: step({ A: 'failed', B: 'failed' }), base: step({ A: 'passed', B: 'passed' }), latest: first })
  assert.equal(d.outcome, 'defect')
  assert.deepEqual(d.failureKeys, ['unit::A'])
  assert.ok(d.unverifiedFailures.some((f) => f.key === 'unit::B'))
})

test('明确通过与稳定老失败保持原有判定', () => {
  const fail = step({ A: 'failed' }), pass = step({ A: 'passed' })
  assert.equal(decideOutcome({ first: fail, rerun: pass }).outcome, 'flaky')
  assert.equal(decideOutcome({ first: fail, rerun: fail, base: pass, latest: pass }).outcome, 'fixed_later')
  assert.equal(decideOutcome({ first: fail, rerun: fail, base: fail }).outcome, 'passed_known_failures')
})

test('首跑不稳定用例在最新主干又失败，也必须保留为待核实', () => {
  const d = decideOutcome({ first: step({ A: 'failed', B: 'failed' }), rerun: step({ A: 'failed', B: 'passed' }),
    base: step({ A: 'passed', B: 'passed' }), latest: step({ A: 'passed', B: 'failed' }) })
  assert.equal(d.outcome, 'needs_review')
  assert.ok(d.unverifiedFailures.some((f) => f.key === 'unit::B' && f.phase === 'latest'))
})

test('基线出现与当前目标无关的旧失败，不阻止有正证据的归因', () => {
  const fail = step({ A: 'failed' })
  const d = decideOutcome({ first: fail, rerun: fail, base: step({ A: 'passed', old: 'failed' }), latest: fail })
  assert.equal(d.outcome, 'defect')
  assert.deepEqual(d.failureKeys, ['unit::A'])
})

test('退出0但配置的报告缺失时不能当成功或贡献覆盖', () => {
  const first = step({ A: 'passed' }, { testOutcomeStatus: 'incomplete' })
  assert.equal(decideOutcome({ first, rerun: first }).outcome, 'needs_review')
  const coverage = toCoverage({ profile: { steps: [{ id: 'unit', covers: ['unit'] }] }, triage: { rows: [{ key: 'unit', verdict: 'applicable' }] }, results: first })
  assert.deepEqual(coverage.missed, ['unit'])
})
