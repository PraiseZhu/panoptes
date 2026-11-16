import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import { ROOT, REQUIRED_LANES, validateFleet, validateStandard, preflight } from './qa-fleet-preflight.mjs'
import { configHash, verify } from './qa-fleet-verify.mjs'
import { compareShadow } from './qa-fleet-shadow-compare.mjs'
export const fleet = JSON.parse(readFileSync(resolve(ROOT, 'pi/conf/qa-fleet.json')))
export const standard = JSON.parse(readFileSync(resolve(ROOT, 'pi/conf/qa-standard.json')))
export const sha = 'a'.repeat(40), hash = configHash(resolve(ROOT, 'pi/conf/qa-fleet.json'), resolve(ROOT, 'pi/conf/qa-standard.json'))
export function shadow() {
  const rows = Array.from({ length: 7 }, (_, i) => ({ date: `2026-11-${10 + i}`, source_sha: sha, standard_version: standard.version, mode: 'shadow', run_id: `real-${i}`, decisions: [{ fingerprint: 'fp', severity: 'P1', decision: { status: 'confirmed' } }] }))
  return { legacy: structuredClone(rows), current: structuredClone(rows) }
}
const inventory = () => ({ dependencies: { git: true, node24: true, python3: true, bubblewrap: true }, tasks: Object.values(fleet.lanes).flatMap(l => l.entries), permissions: Object.fromEntries(REQUIRED_LANES.map(l => [l, Object.fromEntries(fleet.lanes[l].permissions.map(p => [p, true]))])) })
test('schema and actual cron wiring pass; secrets and missing lane/entry fail', () => {
  assert.deepEqual(validateFleet(fleet), []); assert.deepEqual(validateStandard(standard), [])
  const x = structuredClone(fleet); delete x.lanes.qa
  assert.ok(validateFleet(x).some(e => e.code === 'lane_missing'))
  x.lanes.qa = { ...fleet.lanes.qa, entry: '../wrong' }; assert.ok(validateFleet(x).some(e => e.code === 'lane_entry_missing'))
  x.lanes.qa.token = 'value'; assert.ok(validateFleet(x).some(e => e.code === 'sensitive_field'))
  x.lanes.qa.token = undefined; x.lanes.qa.entry = '/home/person/private'; assert.ok(validateFleet(x).some(e => e.code === 'sensitive_field'))
})
test('preflight requires host inventory, full SHA, permissions and dependencies, rejects old tasks', () => {
  const args = { fleet, standard, sourceSha: sha, runtimeSha: sha, inventory: inventory() }
  assert.equal(preflight(args).ok, true)
  for (const change of [{ sourceSha: undefined }, { runtimeSha: 'b'.repeat(40) }, { inventory: undefined }, { standard: { ...standard, version: 'other' } }]) assert.equal(preflight({ ...args, ...change }).ok, false)
  args.inventory.tasks.push({ job: 'legacy-runner', path: 'old.py' }); assert.ok(preflight(args).errors.some(e => e.code === 'old_task_residual'))
  args.inventory = inventory(); args.inventory.permissions.qa['write-evidence'] = false; assert.ok(preflight(args).errors.some(e => e.code === 'permission_missing'))
  args.inventory = inventory(); args.inventory.dependencies.node24 = false; assert.ok(preflight(args).errors.some(e => e.code === 'dependency_missing'))
})
test('verify binds full expected SHA, config bytes, version and active mode', () => {
  const args = { fleet, standard, expectedSourceSha: sha, expectedRuntimeSha: sha, expectedManifestHash: hash, manifest: { schema: 'qa.fleet.run/v1', run_id: 'x', source_sha: sha, runtime_sha: sha, manifest_hash: hash, mode: standard.mode, standard_version: standard.version } }
  assert.equal(verify(args).ok, true)
  for (const k of ['source_sha', 'runtime_sha', 'manifest_hash', 'standard_version', 'mode']) assert.equal(verify({ ...args, manifest: { ...args.manifest, [k]: 'drift' } }).ok, false)
  assert.equal(verify({ fleet, standard, manifest: args.manifest }).ok, false)
  const r = spawnSync('python3', ['-c', 'import sys; sys.path.insert(0,"pi/bin"); import qa_standard; print(qa_standard.binding()["manifest_hash"])'], { cwd: ROOT, encoding: 'utf8' })
  assert.equal(r.status, 0); assert.equal(r.stdout.trim(), hash)
})
test('seven-day comparison normalizes decisions, retains severe legacy changes and explanations', () => {
  const s = shadow(); assert.equal(compareShadow(s).ok, true)
  s.current[0].decisions[0] = { fingerprint: 'fp', severity: 'P3', decision: { status: 'pending' } }
  let r = compareShadow(s); assert.equal(r.ok, false); assert.equal(r.diffs[0].severity, 'P1')
  assert.equal(compareShadow({ ...s, explanations: [{ date: s.current[0].date, fingerprint: 'fp', reason: ' ' }] }).ok, false)
  r = compareShadow({ ...s, explanations: [{ date: s.current[0].date, fingerprint: 'fp', reason: 'v2 evidence missing' }] }); assert.equal(r.ok, true); assert.equal(r.diffs.length, 1)
})
test('gaps, duplicates, invalid input, drift and CLI day-count bypass fail closed', () => {
  assert.equal(compareShadow().ok, false)
  for (const mutate of [s => s.current.pop(), s => s.legacy[0].date = s.legacy[1].date, s => s.current[0].source_sha = 'b'.repeat(40), s => s.legacy[0].date = '2026-11-01']) {
    const s = shadow(); mutate(s); assert.equal(compareShadow(s).ok, false)
  }
  const r = spawnSync(process.execPath, [resolve(ROOT, 'scripts/ops/qa-fleet-shadow-compare.mjs'), '--days', '7'], { encoding: 'utf8' }); assert.equal(r.status, 1)
})
