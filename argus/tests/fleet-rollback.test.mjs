import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { canTransition, transitionStandard, rollbackPlan, REQUIRED_LANES } from '../../scripts/ops/qa-fleet-preflight.mjs'
const standard = JSON.parse(readFileSync(new URL('../../pi/conf/qa-standard.json', import.meta.url)))
const sha = 'a'.repeat(40), hash = 'c'.repeat(64)
function evidence() {
  const rows = Array.from({ length: 7 }, (_, i) => ({ date: `2026-11-${10 + i}`, run_id: `shadow-${i}`, source_sha: sha, standard_version: standard.version, mode: 'shadow', decisions: [] }))
  return { source_sha: sha, standard_version: standard.version, manifest_hash: hash, shadow: { legacy: rows, current: structuredClone(rows) },
    canary: Object.fromEntries(REQUIRED_LANES.map(lane => {
      const meta = { source_sha: sha, runtime_sha: sha, standard_version: standard.version, manifest_hash: hash, mode: 'canary', run_id: `${lane}-run`, ok: true }
      return [lane, { scheduler: { ...meta, schema: 'qa.fleet.run/v1', lane, status: 'pass', exit_code: 0, finished_at: '2026-11-16T00:00:00Z' }, report: { ...meta, all_discovered_tested: true }, fleet_status: { ...meta }, stdcheck: { ...meta }, outbound: { ...meta, missing_evidence_outbound: 0 } }]
    })), rollback: { ok: true, source_sha: sha, previous_source_sha: 'b'.repeat(40), preserved_evidence: true, receipt_id: 'drill-1' } }
}
test('legacy/shadow/canary/strict/rollback require the actual evidence bundle', () => {
  const e = evidence()
  assert.equal(canTransition('legacy', 'shadow').ok, true)
  assert.equal(canTransition('shadow', 'canary', e).ok, true)
  assert.equal(canTransition('canary', 'strict', e).ok, true)
  assert.equal(canTransition('legacy', 'strict', e).ok, false)
  assert.equal(canTransition('shadow', 'canary', { shadow_days: 7 }).ok, false)
  assert.equal(canTransition('canary', 'strict', { canary_receipts: 5, rollback_verified: true }).ok, false)
  for (const lane of REQUIRED_LANES) {
    for (const key of ['scheduler', 'report', 'fleet_status', 'stdcheck', 'outbound']) {
      const bad = evidence(); bad.canary[lane][key].source_sha = 'd'.repeat(40)
      assert.equal(canTransition('canary', 'strict', bad).ok, false, `${lane}/${key}`)
    }
  }
  e.canary.qa.report.all_discovered_tested = false; assert.equal(canTransition('canary', 'strict', e).ok, false)
})
test('rollback is available from every mode and plans preserve evidence without writes', () => {
  for (const mode of standard.allowed_modes) assert.equal(canTransition(mode, 'rollback').ok, true)
  const before = JSON.stringify(standard), next = transitionStandard(standard, 'rollback')
  assert.equal(next.mode, 'rollback'); assert.equal(JSON.stringify(standard), before)
  const plan = rollbackPlan('b'.repeat(40)); assert.equal(plan.source_sha, 'b'.repeat(40)); assert.deepEqual(plan.delete, []); assert.equal(plan.preserve.length, 3)
  assert.throws(() => rollbackPlan('abc')); assert.equal(canTransition('rollback', 'legacy').ok, false)
  assert.equal(canTransition('rollback', 'legacy', { restored_source_sha: plan.source_sha }).ok, true)
})
