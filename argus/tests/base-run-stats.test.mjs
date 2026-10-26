import { test } from 'node:test'
import assert from 'node:assert/strict'
const { baseRunStats } = await import('../src/base-run-stats.mjs')
const N = await import('../src/nightly.mjs')

const fc = (check_id, test, base_run) => ({ check_id, test, evidence: { base_run } })
test('base 运行成功率：按（PR,检查,用例）去重，最新报告为准；pass/not-failing 算成功；失败按原因分', () => {
  const s = baseRunStats([
    { prs: [{ number: 1, tested_at: '2026-10-20T01:00:00+08:00', failed_checks: [fc('unit', 'a', 'fail')] }] },
    { prs: [{ number: 1, tested_at: '2026-10-21T01:00:00+08:00', failed_checks: [fc('unit', 'a', 'pass')] }] },
    { prs: [{ number: 2, failed_checks: [fc('api-probe', 'x', 'fail'), fc('bench', 'y', 'fail'), fc('unit', 'z', 'not-failing'), fc('unit', 'w', 'not-run'), { check_id: 'unit', evidence: {} }] }] },
  ])
  assert.deepEqual([s.total, s.ok, s.fail, s.not_run], [5, 2, 2, 1])
  assert.equal(s.rate, 0.4)
  assert.deepEqual(s.by_cause, { 'probe-or-env': 1, 'machine-load': 1 })
})

test('base 前等空闲：只在失败步骤里有试运行检查（bench）时', () => {
  const profile = { steps: [{ id: 'unit' }, { id: 'bench', trialOnly: true }] }
  assert.equal(N.needsIdleBeforeBase(['unit'], profile), false)
  assert.equal(N.needsIdleBeforeBase(['unit', 'bench'], profile), true)
})
