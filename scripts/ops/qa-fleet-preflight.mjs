#!/usr/bin/env node
// Read-only deployment preflight for the Grokbot/Pi QA fleet.
import { existsSync, readFileSync, statSync } from 'node:fs'
import { resolve, relative } from 'node:path'

const DEFAULT_FLEET = new URL('../../pi/conf/qa-fleet.json', import.meta.url).pathname
const DEFAULT_STANDARD = new URL('../../pi/conf/qa-standard.json', import.meta.url).pathname

export function readJson(file) {
  try { return JSON.parse(readFileSync(file, 'utf8')) } catch { return null }
}

export const REQUIRED_LANES = ['qa', 'repair', 'retest', 'ops', 'outbound']
export const MODES = ['legacy', 'shadow', 'canary', 'strict', 'rollback']

function issue(code, detail) { return { code, detail } }

export function validateFleet(fleet, { root = process.cwd() } = {}) {
  const errors = []
  if (!fleet || fleet.schema !== 'qa.fleet/v1') errors.push(issue('fleet_schema', 'schema 必须是 qa.fleet/v1'))
  if (!fleet?.standard_version) errors.push(issue('fleet_standard_version', '缺 standard_version'))
  const lanes = fleet?.lanes
  if (!lanes || typeof lanes !== 'object') return [...errors, issue('lanes_missing', '缺 lanes')]
  for (const name of REQUIRED_LANES) {
    const lane = lanes[name]
    if (!lane) { errors.push(issue('lane_missing', `缺少 lane ${name}`)); continue }
    if (typeof lane.entry !== 'string' || !lane.entry) errors.push(issue('lane_entry_missing', `${name} 缺 entry`))
    else if (root && !existsSync(resolve(root, lane.entry))) errors.push(issue('lane_entry_missing', `${name} entry 不存在: ${lane.entry}`))
    if (!Array.isArray(lane.health_signals) || !lane.health_signals.length) errors.push(issue('health_signal_missing', `${name} 缺 health_signals`))
    if (!Array.isArray(lane.permissions) || !lane.permissions.length) errors.push(issue('permissions_missing', `${name} 缺 permissions`))
  }
  return errors
}

export function validateStandard(standard) {
  const errors = []
  if (!standard || standard.schema !== 'qa.standard/v1') errors.push(issue('standard_schema', 'schema 必须是 qa.standard/v1'))
  if (!standard?.version) errors.push(issue('standard_version_missing', '缺 version'))
  if (!MODES.includes(standard?.mode)) errors.push(issue('mode_invalid', `mode 必须是 ${MODES.join('|')}`))
  if (!Array.isArray(standard?.allowed_modes) || !MODES.every((m) => standard.allowed_modes.includes(m))) errors.push(issue('allowed_modes_invalid', 'allowed_modes 必须覆盖全部标准状态'))
  const min = standard?.evidence_minimum
  if (!min || min.finding_status !== 'confirmed' || !Array.isArray(min.required) || !min.required.includes('run_id') || !min.required.includes('artifact_refs')) errors.push(issue('evidence_minimum_invalid', 'evidence_minimum 必须要求 confirmed、run_id 和 artifact_refs'))
  if (!Number.isInteger(standard?.rollout?.shadow_days) || standard.rollout.shadow_days < 1) errors.push(issue('rollout_invalid', 'rollout.shadow_days 必须为正整数'))
  return errors
}

export function canTransition(from, to, evidence = {}) {
  if (!MODES.includes(from) || !MODES.includes(to)) return { ok: false, reason: 'unknown mode' }
  if (to === 'rollback') return { ok: true, reason: 'rollback is always available' }
  const next = { legacy: 'shadow', shadow: 'canary', canary: 'strict' }[from]
  if (next !== to) return { ok: false, reason: `${from} 不能直接切到 ${to}` }
  if (to === 'canary' && Number(evidence.shadow_days ?? 0) < 7) return { ok: false, reason: 'shadow 未满 7 天' }
  if (to === 'canary' && Number(evidence.unexplained_p0_p1 ?? 0) > 0) return { ok: false, reason: '仍有未解释 P0/P1 差异' }
  if (to === 'strict' && Number(evidence.canary_receipts ?? 0) < Number(evidence.required_canary_receipts ?? 5)) return { ok: false, reason: 'canary 每条 lane 的首轮 receipt 不完整' }
  if (to === 'strict' && evidence.rollback_verified !== true) return { ok: false, reason: 'rollback 演练未通过' }
  return { ok: true, reason: 'evidence gate passed' }
}

export function transitionStandard(standard, to, evidence = {}) {
  const check = canTransition(standard?.mode, to, evidence)
  if (!check.ok) throw new Error(check.reason)
  return { ...standard, mode: to, cutover: { from: standard.mode, to, evidence } }
}

function walkFiles(root) {
  if (!root || !existsSync(root)) return []
  try {
    const st = statSync(root)
    if (st.isFile()) return [root]
    return readFileSync(root, 'utf8').split(/\r?\n/).filter(Boolean).map((x) => resolve(root, '..', x))
  } catch { return [] }
}

export function preflight({ fleet, standard, root = process.cwd(), sourceSha = null, runtimeSha = null, oldTaskFiles = [] } = {}) {
  const errors = [...validateFleet(fleet, { root }), ...validateStandard(standard)]
  const patterns = fleet?.old_entry_patterns ?? []
  for (const file of oldTaskFiles) {
    const text = (() => { try { return readFileSync(file, 'utf8') } catch { return '' } })()
    if (patterns.some((p) => text.includes(p))) errors.push(issue('old_task_residual', `发现旧任务入口残留: ${file}`))
  }
  if (sourceSha && runtimeSha && sourceSha !== runtimeSha) errors.push(issue('sha_drift', `source/runtime SHA 不一致: ${sourceSha} != ${runtimeSha}`))
  if (standard?.version && fleet?.standard_version && standard.version !== fleet.standard_version) errors.push(issue('standard_drift', `fleet=${fleet.standard_version} standard=${standard.version}`))
  return { ok: errors.length === 0, errors, mode: standard?.mode ?? null, lanes: Object.keys(fleet?.lanes ?? {}) }
}

function arg(args, name, fallback) { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : fallback }
export function main(argv = process.argv.slice(2)) {
  const root = resolve(arg(argv, '--root', process.cwd()))
  const fleetFile = resolve(arg(argv, '--fleet', DEFAULT_FLEET)); const standardFile = resolve(arg(argv, '--standard', DEFAULT_STANDARD))
  const fleet = readJson(fleetFile); const standard = readJson(standardFile)
  const result = preflight({ fleet, standard, root, sourceSha: arg(argv, '--source-sha', null), runtimeSha: arg(argv, '--runtime-sha', null), oldTaskFiles: argv.filter((x) => x.startsWith('--old-task=' )).map((x) => x.slice(11)) })
  process.stdout.write(JSON.stringify({ ...result, fleet: fleetFile, standard: standardFile }, null, 2) + '\n')
  return result.ok ? 0 : 1
}

if (import.meta.url === `file://${process.argv[1]}`) process.exitCode = main()
