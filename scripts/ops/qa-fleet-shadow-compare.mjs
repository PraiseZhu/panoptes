#!/usr/bin/env node
// Compare legacy and new decisions without treating expected, explained changes as drift.
import { readFileSync } from 'node:fs'

const SEV = { P0: 0, P1: 1, P2: 2, P3: 3 }
const keyOf = (x) => String(x?.fingerprint ?? x?.id ?? x?.key ?? `${x?.pr ?? ''}:${x?.check_id ?? ''}:${x?.test ?? ''}`)

export function compareShadow({ legacy = [], current = [], explanations = [], days = 0, requiredDays = 7 } = {}) {
  const allowed = new Set((explanations ?? []).map((x) => typeof x === 'string' ? x : keyOf(x)))
  const old = new Map(legacy.map((x) => [keyOf(x), x])); const next = new Map(current.map((x) => [keyOf(x), x]))
  const diffs = []
  for (const key of new Set([...old.keys(), ...next.keys()])) {
    const a = old.get(key)?.decision ?? old.get(key)?.status ?? null; const b = next.get(key)?.decision ?? next.get(key)?.status ?? null
    if (a !== b && !allowed.has(key)) diffs.push({ key, legacy: a, current: b, severity: next.get(key)?.severity ?? old.get(key)?.severity ?? null, explained: false })
  }
  const unexplainedCritical = diffs.filter((x) => SEV[x.severity] <= 1)
  return { ok: days >= requiredDays && unexplainedCritical.length === 0, days, required_days: requiredDays, diffs, unexplained_critical: unexplainedCritical }
}

function json(file, fallback) { try { return JSON.parse(readFileSync(file, 'utf8')) } catch { return fallback } }
function arg(args, name, fallback) { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : fallback }
export function main(argv = process.argv.slice(2)) {
  const legacy = json(arg(argv, '--legacy', ''), []); const current = json(arg(argv, '--current', ''), []); const explanations = json(arg(argv, '--explanations', ''), [])
  const result = compareShadow({ legacy: Array.isArray(legacy) ? legacy : legacy.decisions, current: Array.isArray(current) ? current : current.decisions, explanations, days: Number(arg(argv, '--days', 0)), requiredDays: Number(arg(argv, '--required-days', 7)) })
  process.stdout.write(JSON.stringify(result, null, 2) + '\n'); return result.ok ? 0 : 1
}
if (import.meta.url === `file://${process.argv[1]}`) process.exitCode = main()
