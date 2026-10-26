#!/usr/bin/env node
// 用法：node scripts/base-run-stats.mjs <reports 目录>…（只读）。例：node scripts/base-run-stats.mjs /workspace/mivo-qa/reports/merged
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { baseRunStats } from '../src/base-run-stats.mjs'
const reports = []
for (const dir of process.argv.slice(2)) for (const f of readdirSync(dir)) if (/^PR-\d+\.json$/.test(f)) { try { reports.push(JSON.parse(readFileSync(join(dir, f), 'utf8'))) } catch { /* skip */ } }
const s = baseRunStats(reports)
console.log(JSON.stringify({ total: s.total, ok: s.ok, fail: s.fail, not_run: s.not_run, rate: s.rate && Math.round(s.rate * 1000) / 10, by_cause: s.by_cause,
  by_day: Object.fromEntries([...new Set(s.rows.map((r) => r.day))].sort().map((d) => [d, s.rows.filter((r) => r.day === d).reduce((m, r) => ({ ...m, [r.base_run]: (m[r.base_run] ?? 0) + 1 }), {})])) }, null, 1))
