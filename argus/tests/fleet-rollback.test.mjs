import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { resolve, join } from 'node:path'
import { spawnSync } from 'node:child_process'
import { canTransition, transitionStandard, rollbackPlan, REQUIRED_LANES, ROOT } from '../../scripts/ops/qa-fleet-preflight.mjs'
import { configHash } from '../../scripts/ops/qa-fleet-verify.mjs'
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
  assert.equal(canTransition('rollback', 'legacy', { restored_source_sha: plan.source_sha }).ok, false)
})
test('rollback exit binds the saved plan to qa-fleet-verify runtime and retains evidence', t => {
  mkdirSync(resolve(ROOT, '_tmp'), { recursive: true })
  const dir = mkdtempSync(resolve(ROOT, '_tmp/fleet-rollback-'))
  t.after(() => rmSync(dir, { recursive: true, force: true }))
  const cli = (file, args) => spawnSync(process.execPath, [resolve(ROOT, 'scripts/ops', file), ...args], { encoding: 'utf8' })
  const previous = 'b'.repeat(40), other = 'd'.repeat(40)
  const planned = cli('qa-fleet-preflight.mjs', ['--rollback-plan', previous])
  assert.equal(planned.status, 0, planned.stderr)
  const plan = JSON.parse(planned.stdout)
  assert.equal(plan.previous_source_sha, previous)
  const fleet = JSON.parse(readFileSync(resolve(ROOT, 'pi/conf/qa-fleet.json')))
  const policy = { ...standard, mode: 'rollback', cutover: { from: 'strict', to: 'rollback', evidence: { receipt_id: 'original' } } }
  const fleetFile = join(dir, 'fleet.json'), policyFile = join(dir, 'standard.json'), manifestFile = join(dir, 'fleet-manifest.json')
  const save = (file, value) => writeFileSync(file, JSON.stringify(value))
  save(fleetFile, fleet); save(policyFile, policy)
  save(manifestFile, { schema: 'qa.fleet.run/v1', run_id: 'restored-run', source_sha: previous, runtime_sha: previous,
    manifest_hash: configHash(fleetFile, policyFile), mode: 'rollback', standard_version: policy.version })
  const shadowFile = join(dir, 'shadow.json'), cutoverFile = join(dir, 'cutover.json'), v2File = join(dir, 'v2-manifest.json')
  save(v2File, { schema: 'qa.evidence/v2', run_id: 'retained-previous-run' })
  save(shadowFile, evidence().shadow); save(cutoverFile, policy.cutover)
  const retained = [manifestFile, v2File, shadowFile, cutoverFile].map(file => [file, readFileSync(file)])
  const checked = cli('qa-fleet-verify.mjs', ['--fleet', fleetFile, '--standard', policyFile, '--manifest', manifestFile,
    '--source-sha', previous, '--runtime-sha', previous])
  assert.equal(checked.status, 0, checked.stdout + checked.stderr)
  const verified = JSON.parse(checked.stdout)
  const proof = { rollback_plan: plan, restored_source_sha: previous, runtime_verification: verified }
  for (const bad of [
    { ...proof, restored_source_sha: other },
    { ...proof, rollback_plan: rollbackPlan(other) },
    { ...proof, runtime_verification: { ...verified, runtime_sha: other } },
    { ...proof, runtime_verification: { ...verified, source_sha: other } },
    { ...proof, runtime_verification: { ...verified, ok: false } },
    { ...proof, runtime_verification: { ...verified, errors: [{ code: 'runtime_sha_drift' }] } },
    { ...proof, runtime_verification: undefined }
  ]) assert.equal(canTransition('rollback', 'legacy', bad).ok, false)
  assert.equal(canTransition('rollback', 'legacy', proof).ok, true)
  const next = transitionStandard(policy, 'legacy', proof)
  assert.deepEqual(next.cutover_history, [policy.cutover])
  assert.equal(next.cutover.evidence.rollback_plan.previous_source_sha, previous)
  const inventoryFile = join(dir, 'inventory.json'), proofFile = join(dir, 'proof.json')
  save(inventoryFile, { dependencies: { git: true, node24: true, python3: true, bubblewrap: true }, tasks: Object.values(fleet.lanes).flatMap(l => l.entries),
    permissions: Object.fromEntries(REQUIRED_LANES.map(l => [l, Object.fromEntries(fleet.lanes[l].permissions.map(p => [p, true]))])) })
  save(proofFile, proof)
  const args = ['--fleet', fleetFile, '--standard', policyFile, '--inventory', inventoryFile, '--evidence', proofFile, '--transition', 'legacy']
  assert.equal(cli('qa-fleet-preflight.mjs', [...args, '--source-sha', other, '--runtime-sha', other]).status, 1)
  const good = cli('qa-fleet-preflight.mjs', [...args, '--source-sha', previous, '--runtime-sha', previous])
  assert.equal(good.status, 0, good.stdout + good.stderr)
  assert.deepEqual(JSON.parse(good.stdout).proposed_standard.cutover_history, [policy.cutover])
  for (const [file, bytes] of retained) assert.deepEqual(readFileSync(file), bytes)
})
