#!/usr/bin/env node
// A shadow window consists of paired, dated receipts, never a caller-supplied day count.
import { readFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'
const SHA = /^[a-f0-9]{40}$/
const severity = { P0: 0, P1: 1, P2: 2, P3: 3 }
const status = x => typeof x?.decision === 'object' ? x.decision.status : x?.decision ?? x?.status

export function compareShadow({ legacy, current, explanations = [] } = {}) {
  const errors = [], diffs = []
  if (!Array.isArray(legacy) || !Array.isArray(current) || !Array.isArray(explanations)) return { ok: false, errors: ['missing_or_invalid_input'], days: 0, diffs }
  const read = (rows, side) => {
    const map = new Map()
    for (const r of rows) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(r?.date) || new Date(r.date).toISOString().slice(0, 10) !== r.date || map.has(r.date) ||
          !SHA.test(r.source_sha) || !r.standard_version || !Array.isArray(r.decisions) || !r.run_id || r.mode !== 'shadow') { errors.push(`${side}:invalid_receipt`); continue }
      const keys = new Set()
      for (const d of r.decisions) {
        if (!d.fingerprint || keys.has(d.fingerprint) || !Object.hasOwn(severity, d.severity) || typeof status(d) !== 'string') errors.push(`${side}:invalid_decision`)
        keys.add(d.fingerprint)
      }
      map.set(r.date, r)
    }
    return map
  }
  let a, b
  try { a = read(legacy, 'legacy'); b = read(current, 'current') } catch { return { ok: false, errors: ['invalid_receipt'], days: 0, diffs } }
  const dates = [...a.keys()].sort()
  if (dates.length < 7 || dates.length !== b.size || dates.some(d => !b.has(d))) errors.push('seven_paired_days_required')
  for (let i = 1; i < dates.length; i++) if (Date.parse(dates[i]) - Date.parse(dates[i - 1]) !== 86400000) errors.push('non_consecutive_days')
  const binding = dates.length ? { source_sha: a.get(dates[0]).source_sha, standard_version: a.get(dates[0]).standard_version } : {}
  for (const date of dates) {
    const old = a.get(date), next = b.get(date)
    if (!next) continue
    for (const r of [old, next]) if (r.source_sha !== binding.source_sha || r.standard_version !== binding.standard_version) errors.push('source_policy_drift')
    const om = new Map(old.decisions.map(d => [d.fingerprint, d])), nm = new Map(next.decisions.map(d => [d.fingerprint, d]))
    for (const key of new Set([...om.keys(), ...nm.keys()])) {
      const x = om.get(key), y = nm.get(key)
      if (status(x) === status(y)) continue
      const sev = Math.min(severity[x?.severity] ?? 3, severity[y?.severity] ?? 3)
      const explanation = explanations.find(e => e?.date === date && e.fingerprint === key && typeof e.reason === 'string' && e.reason.trim())
      diffs.push({ date, fingerprint: key, legacy: status(x) ?? null, current: status(y) ?? null, severity: `P${sev}`, explained: !!explanation, reason: explanation?.reason ?? null })
    }
  }
  const critical = diffs.filter(d => !d.explained && ['P0', 'P1'].includes(d.severity))
  return { ok: errors.length === 0 && critical.length === 0, errors, ...binding, dates, days: dates.length, diffs, unexplained_critical: critical }
}
export function main(argv = process.argv.slice(2)) {
  const arg = name => argv[argv.indexOf(name) + 1]
  try {
    const read = name => { if (!argv.includes(name)) throw Error('missing_input'); return JSON.parse(readFileSync(arg(name), 'utf8')) }
    const result = compareShadow({ legacy: read('--legacy'), current: read('--current'), explanations: argv.includes('--explanations') ? read('--explanations') : [] })
    process.stdout.write(JSON.stringify(result, null, 2) + '\n'); return result.ok ? 0 : 1
  } catch { process.stdout.write('{"ok":false,"errors":["missing_or_invalid_input"]}\n'); return 1 }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) process.exitCode = main()
