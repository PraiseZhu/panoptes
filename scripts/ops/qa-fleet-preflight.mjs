#!/usr/bin/env node
// Read-only: configuration, wiring and explicitly supplied host inventory. No deployment.
import { accessSync, constants, existsSync, readFileSync } from 'node:fs'
import { resolve, relative, isAbsolute } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { compareShadow } from './qa-fleet-shadow-compare.mjs'
export const ROOT = fileURLToPath(new URL('../../', import.meta.url))
export const REQUIRED_LANES = ['qa', 'repair', 'retest', 'ops', 'outbound']
export const MODES = ['legacy', 'shadow', 'canary', 'strict', 'rollback']
export const SHA = /^[a-f0-9]{40}$/
export const HASH = /^[a-f0-9]{64}$/
export function readJson(file) { try { return JSON.parse(readFileSync(file, 'utf8')) } catch { return null } }
const issue = (code, detail = code) => ({ code, detail })
const safePath = p => typeof p === 'string' && p.length && !isAbsolute(p) && !p.split(/[\\/]/).includes('..')
export function sensitiveErrors(value) {
  const errors = []
  const visit = x => {
    if (!x || typeof x !== 'object') return
    for (const [k, v] of Object.entries(x)) {
      if (/token|secret|password|credential|prompt/i.test(k) || (typeof v === 'string' && /(?:\/Users\/|\/home\/|sk-[A-Za-z0-9]{12}|Bearer\s|-----BEGIN.*PRIVATE KEY)/.test(v))) errors.push(issue('sensitive_field'))
      visit(v)
    }
  }
  visit(value); return errors
}
export function validateFleet(fleet, { root = ROOT } = {}) {
  const errors = sensitiveErrors(fleet)
  if (fleet?.schema !== 'qa.fleet/v1') errors.push(issue('fleet_schema'))
  if (typeof fleet?.standard_version !== 'string' || !fleet.standard_version) errors.push(issue('fleet_standard_version'))
  if (Object.keys(fleet?.lanes ?? {}).sort().join() !== [...REQUIRED_LANES].sort().join()) errors.push(issue('lanes_missing'))
  const jobs = new Set()
  for (const lane of REQUIRED_LANES) {
    const spec = fleet?.lanes?.[lane]
    if (!spec) { errors.push(issue('lane_missing', lane)); continue }
    for (const field of ['permissions', 'health_signals', 'jobs', 'entries']) if (!Array.isArray(spec[field]) || !spec[field].length) errors.push(issue(`${field}_missing`, lane))
    for (const p of [spec.entry, ...(spec.entries ?? []).map(e => e.path)]) {
      if (!safePath(p) || !existsSync(resolve(root, p)) || relative(root, resolve(root, p)).startsWith('..')) errors.push(issue('lane_entry_missing', lane))
      else { try { accessSync(resolve(root, p), constants.R_OK) } catch { errors.push(issue('entry_unreadable', lane)) } }
    }
    for (const job of spec.jobs ?? []) {
      if (jobs.has(job) || !spec.entries?.some(e => e.job === job && typeof e.scheduler === 'string' && e.scheduler)) errors.push(issue('job_wiring', job))
      jobs.add(job)
    }
    if ((spec.entries ?? []).some(e => !spec.jobs?.includes(e.job))) errors.push(issue('job_wiring', lane))
  }
  // Every checked-in cron job must be registered, including direct zero-model entrypoints.
  try {
    const cron = readFileSync(resolve(root, 'pi/cron/pi-line.cron'), 'utf8').split('\n').filter(l => !l.trim().startsWith('#')).join('\n')
    for (const m of cron.matchAll(/\$R (\w+)|pi\/bin\/(\w+)\.py/g)) if (!jobs.has(m[1] ?? m[2])) errors.push(issue('unregistered_cron_job', m[1] ?? m[2]))
  } catch { errors.push(issue('cron_inventory_missing')) }
  return errors
}
export function validateStandard(standard) {
  const errors = sensitiveErrors(standard)
  if (standard?.schema !== 'qa.standard/v1' || !standard?.version) errors.push(issue('standard_schema'))
  if (!MODES.includes(standard?.mode) || !MODES.every(m => standard?.allowed_modes?.includes(m))) errors.push(issue('mode_invalid'))
  const min = standard?.evidence_minimum
  if (min?.finding_status !== 'confirmed' || !['run_id', 'tested_sha', 'base_sha', 'artifact_refs'].every(k => min?.required?.includes(k)) ||
      !['pending', 'environment_unavailable', 'runner_error', 'invalid'].every(k => min?.forbidden_outbound_statuses?.includes(k))) errors.push(issue('evidence_minimum_invalid'))
  if (standard?.rollout?.shadow_days < 7 || standard?.rollout?.canary_runs_per_lane < 1) errors.push(issue('rollout_invalid'))
  return errors
}
export function canTransition(from, to, evidence = {}) {
  const fail = reason => ({ ok: false, reason })
  if (!MODES.includes(from) || !MODES.includes(to)) return fail('unknown mode')
  if (to === 'rollback') return { ok: true, reason: 'stop product actions immediately; deploy rollback separately' }
  if (from === 'rollback' && to === 'legacy') return SHA.test(evidence.restored_source_sha) ? { ok: true } : fail('restored source SHA required')
  if ({ legacy: 'shadow', shadow: 'canary', canary: 'strict' }[from] !== to) return fail('invalid transition')
  if (to === 'canary' || to === 'strict') {
    const shadow = compareShadow(evidence.shadow)
    if (!shadow.ok) return fail('seven-day shadow evidence required')
    if (!SHA.test(evidence.source_sha) || !evidence.standard_version || shadow.source_sha !== evidence.source_sha || shadow.standard_version !== evidence.standard_version) return fail('shadow source/policy drift')
  }
  if (to === 'strict') {
    if (!HASH.test(evidence.manifest_hash)) return fail('canary manifest hash required')
    for (const lane of REQUIRED_LANES) {
      const bundle = evidence.canary?.[lane]
      if (!bundle) return fail(`missing canary lane ${lane}`)
      const run = bundle.scheduler
      if (run?.schema !== 'qa.fleet.run/v1' || !run.run_id || run.lane !== lane || run.status !== 'pass' || run.exit_code !== 0 || !run.finished_at) return fail(`invalid scheduler ${lane}`)
      for (const key of ['scheduler', 'report', 'fleet_status', 'stdcheck', 'outbound']) {
        const r = bundle[key]
        if (!r || r.run_id !== run.run_id || r.source_sha !== evidence.source_sha || r.runtime_sha !== evidence.source_sha || r.standard_version !== evidence.standard_version || r.manifest_hash !== evidence.manifest_hash || r.mode !== 'canary' || r.ok !== true) return fail(`canary drift ${lane}/${key}`)
      }
      if (bundle.report.all_discovered_tested !== true || bundle.outbound.missing_evidence_outbound !== 0) return fail(`canary incomplete ${lane}`)
    }
    const rollback = evidence.rollback
    if (!rollback || rollback.ok !== true || !SHA.test(rollback.previous_source_sha) || rollback.source_sha !== evidence.source_sha || rollback.preserved_evidence !== true || !rollback.receipt_id) return fail('rollback drill receipt required')
  }
  return { ok: true, reason: 'evidence gate passed' }
}
export function transitionStandard(standard, to, evidence = {}) {
  const check = canTransition(standard?.mode, to, evidence)
  if (!check.ok) throw Error(check.reason)
  return { ...standard, mode: to, cutover: { from: standard.mode, to, evidence } }
}
export function rollbackPlan(previousSourceSha) {
  if (!SHA.test(previousSourceSha)) throw Error('full previous source SHA required')
  return { source_sha: previousSourceSha, mode: 'rollback', deploy_via: 'opsdeploy', preserve: ['v2 manifests', 'shadow receipts', 'cutover records'], delete: [] }
}
export function preflight({ fleet, standard, root = ROOT, sourceSha, runtimeSha, inventory } = {}) {
  const errors = [...validateFleet(fleet, { root }), ...validateStandard(standard)]
  if (!SHA.test(sourceSha) || !SHA.test(runtimeSha)) errors.push(issue('sha_missing_or_invalid'))
  else if (sourceSha !== runtimeSha) errors.push(issue('sha_drift'))
  if (fleet?.standard_version !== standard?.version) errors.push(issue('standard_drift'))
  if (!inventory || !Array.isArray(inventory.tasks) || !inventory.tasks.length || !inventory.dependencies || !inventory.permissions) errors.push(issue('host_inventory_missing'))
  else {
    for (const dep of ['git', 'node24', 'python3', 'bubblewrap']) if (inventory.dependencies[dep] !== true) errors.push(issue('dependency_missing', dep))
    const jobs = new Set(Object.values(fleet?.lanes ?? {}).flatMap(s => s.jobs ?? []))
    for (const task of inventory.tasks) if (!jobs.has(task.job) || !Object.values(fleet?.lanes ?? {}).some(s => s.entries?.some(e => e.job === task.job && e.path === task.path))) errors.push(issue('old_task_residual', task.job))
    for (const lane of REQUIRED_LANES) for (const permission of fleet?.lanes?.[lane]?.permissions ?? []) if (inventory.permissions[lane]?.[permission] !== true) errors.push(issue('permission_missing', `${lane}:${permission}`))
  }
  return { ok: errors.length === 0, errors, mode: standard?.mode ?? null, lanes: Object.keys(fleet?.lanes ?? {}) }
}
export function main(argv = process.argv.slice(2)) {
  const arg = (k, fallback) => argv.includes(k) ? argv[argv.indexOf(k) + 1] : fallback
  const root = resolve(arg('--root', ROOT))
  const result = preflight({ fleet: readJson(arg('--fleet', resolve(root, 'pi/conf/qa-fleet.json'))), standard: readJson(arg('--standard', resolve(root, 'pi/conf/qa-standard.json'))), root,
    sourceSha: arg('--source-sha'), runtimeSha: arg('--runtime-sha'), inventory: readJson(arg('--inventory', '')) })
  if (argv.includes('--transition')) {
    try {
      if (!result.ok) throw Error('preflight failed')
      result.proposed_standard = transitionStandard(readJson(arg('--standard', resolve(root, 'pi/conf/qa-standard.json'))), arg('--transition'), readJson(arg('--evidence', '')) ?? {})
    } catch (e) { result.ok = false; result.errors.push(issue('transition_rejected', e.message)) }
  }
  process.stdout.write(JSON.stringify(result, null, 2) + '\n'); return result.ok ? 0 : 1
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) process.exitCode = main()
