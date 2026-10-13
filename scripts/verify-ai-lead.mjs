#!/usr/bin/env node
// 用法: node scripts/verify-ai-lead.mjs --pr 918 (--fingerprint <fp|pr_fingerprint> | --check-id ai-test-2) [--by daily-review] [--note "..."] [--unset] [--file path] [--dry-run]
// 默认文件: $MIVO_QA_REPORTS (默认 /workspace/mivo-qa/reports)/merged/PR-<N>.json。只改 verified* 字段，原子写回。
import { readFileSync, writeFileSync, renameSync } from 'node:fs'
import { join } from 'node:path'
import { parseArgs } from 'node:util'
import { verifyLeads } from '../argus/src/verify-lead.mjs'

const { values: v } = parseArgs({ options: { pr: { type: 'string' }, fingerprint: { type: 'string' }, 'check-id': { type: 'string' }, by: { type: 'string' }, note: { type: 'string' }, unset: { type: 'boolean' }, file: { type: 'string' }, 'dry-run': { type: 'boolean' }, at: { type: 'string' } } })
if (!v.pr && !v.file) { console.error('need --pr <N> or --file'); process.exit(2) }
const file = v.file ?? join(process.env.MIVO_QA_REPORTS ?? '/workspace/mivo-qa/reports', 'merged', `PR-${v.pr}.json`)
const report = JSON.parse(readFileSync(file, 'utf8'))
let changed
try { changed = verifyLeads(report, { pr: v.pr, fingerprint: v.fingerprint, checkId: v['check-id'], by: v.by ?? null, note: v.note ?? null, unset: v.unset, ...(v.at ? { at: v.at } : {}) }) }
catch (e) { console.error(e.message); process.exit(2) }
if (!changed.length) { console.error(`no ai-test lead matched in ${file}`); process.exit(1) }
if (!v['dry-run']) { const tmp = `${file}.tmp-${process.pid}`; writeFileSync(tmp, JSON.stringify(report, null, 2) + '\n'); renameSync(tmp, file) }
console.log(JSON.stringify({ file, changed, dry_run: Boolean(v['dry-run']) }))
