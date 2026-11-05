// 11-05（retest-1104 分析 §1.2 补充）：#1742（QA: #1490）的运行里分诊跳过了 e2e-browser，而 #1490 失败的正是 e2e-browser，
// 修复 PR 因此没有原用例证据。修复 PR 的原报告里失败过的步骤必须跑，不受分诊影响。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-forced-'))
const { toStepPlan } = await import('../src/matrix.mjs')
const { originalFailedSteps, qaFixRefs } = await import('../src/nightly.mjs')

const unrelated = { status: 'ok', rows: [{ key: 'regression', verdict: 'looks_unrelated' }, { key: 'performance', verdict: 'looks_unrelated' }] }
const profile = { steps: [{ id: 'unit', kind: 'product' }, { id: 'e2e-browser', kind: 'product', optional: true, covers: ['regression'] },
  { id: 'bench', kind: 'product', optional: true, covers: ['performance'] }] }

test('toStepPlan：forceStepIds 里的可选步骤不被分诊跳过，其余照旧', () => {
  const p = toStepPlan({ profile, riskClass: 'B', triage: unrelated, files: [{ filename: 'src/a.ts' }], forceStepIds: ['e2e-browser'] })
  assert.deepEqual(p.stepIds, ['unit', 'e2e-browser']); assert.deepEqual(p.skipped.map((x) => x.id), ['bench'])
  assert.deepEqual(toStepPlan({ profile, riskClass: 'B', triage: unrelated, files: [{ filename: 'src/a.ts' }] }).stepIds, ['unit'], '不传 forceStepIds 行为不变')
})

test('qaFixRefs：与 pm_extra.fix_refs 同规则', () => {
  assert.deepEqual(qaFixRefs({ number: 1742, title: 'fix', body: 'QA: #1490\nQA 来源：#12 QA #1490 fixes #9' }), [1490, 12])
  assert.deepEqual(qaFixRefs({ number: 1, title: 'chore: 更新日志补扫', body: 'QA #5' }), [])
})

test('originalFailedSteps：读原报告 failed_checks[].check_id；原报告缺失 → 空', () => {
  const dir = mkdtempSync(join(tmpdir(), 'argus-forced-merged-'))
  try {
    writeFileSync(join(dir, 'PR-1490.json'), JSON.stringify({ prs: [{ number: 1490, findings: [], failed_checks: [{ check_id: 'e2e-browser' }, { check_id: 'e2e-browser' }] }] }))
    const r = originalFailedSteps({ number: 1742, title: 'x', body: 'QA: #1490 QA: #7' }, { mergedDir: dir })
    assert.deepEqual(r.stepIds, ['e2e-browser']); assert.deepEqual(r.from, [{ pr: 1490, step_ids: ['e2e-browser'] }]); assert.match(r.reasonZh, /#1490/)
    assert.deepEqual(originalFailedSteps({ number: 3, body: 'no refs' }, { mergedDir: dir }).stepIds, [])
  } finally { rmSync(dir, { recursive: true, force: true }) }
})
