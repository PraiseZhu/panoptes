// 单 PR 报告（--pr N 且没有 --date）：日期 = PR 合并时间的上海日期（开放 PR 用 updated_at），prs[] 只有这个 PR
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-single-pr-'))
const { prReportDay, planTargets, mergedReportFor, writePerPrReports } = await import('../src/nightly.mjs')

test('prReportDay: merged → merged_at in Asia/Shanghai, incl. the day boundary', () => {
  // #907: merged 2026-10-11T10:37:30Z = 18:37 Shanghai on 10-11 (the box clock still says 10-11 03:37 PT)
  assert.deepEqual(prReportDay([{ number: 907, merged_at: '2026-10-11T10:37:30Z', updated_at: '2026-10-11T10:40:00Z' }]).day, '2026-10-11')
  // 2026-10-10T16:30Z is 00:30 on 10-11 in Shanghai, still 10-10 in UTC
  const r = prReportDay([{ number: 1, merged_at: '2026-10-10T16:30:00Z' }])
  assert.equal(r.day, '2026-10-11')
  assert.match(r.source, /merged_at/)
  // 15:59:59Z = 23:59:59 Shanghai → same day
  assert.equal(prReportDay([{ number: 1, merged_at: '2026-10-10T15:59:59Z' }]).day, '2026-10-10')
})

test('prReportDay: open PRs use updated_at; several PRs → the latest day; nothing usable → null', () => {
  const open = prReportDay([{ number: 878, merged_at: null, state: 'open', updated_at: '2026-10-09T17:00:00Z' }])
  assert.equal(open.day, '2026-10-10')
  assert.deepEqual(open.prs, [{ number: 878, day: '2026-10-10', basis: 'updated_at' }])
  assert.match(open.source, /updated_at/)
  const many = prReportDay([{ number: 1, merged_at: '2026-10-09T01:00:00Z' }, { number: 2, merged_at: '2026-10-11T01:00:00Z' }])
  assert.equal(many.day, '2026-10-11')
  assert.equal(prReportDay([{ number: 1 }]).day, null)
  assert.equal(prReportDay([]).day, null)
})

test('--pr run: prs[] holds only the requested PR(s), other discovered PRs go to excluded only', () => {
  const disc = [{ number: 888, reason: 'open-updated' }, { number: 893, reason: 'merged' }, { number: 902, reason: 'merged' }]
  const p = planTargets(disc, { prs: [893] })
  assert.deepEqual(p.targets.map((t) => t.number), [893])
  assert.deepEqual(p.notTested, [])
  assert.deepEqual(p.excluded, [888, 902])
  // a PR that discovery did not see (merged on another day) is still tested, reason manual
  const q = planTargets(disc, { prs: [907] })
  assert.deepEqual(q.targets.map((t) => [t.number, t.reason]), [[907, 'manual (--pr)']])
})

const health = () => ({ status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], notes: [], heartbeat: { prs: [] } })
const prEntry = (number, merged_at) => ({ number, state: 'merged', merged_at, merge_sha: `sha${number}`, tested_sha: `sha${number}`, verdict: 'clean', severity: 'P3', findings: [] })

test('per-PR merged file: date = merge day in Shanghai, prs = [that PR]', () => {
  const report = { date: '2026-10-10', run: { args: {} }, system_health: health(), prs: [prEntry(907, '2026-10-11T10:37:30Z'), { number: 888, state: 'open', verdict: 'not-tested', severity: 'untested', not_tested_code: 'excluded-by-pr-filter', findings: [] }] }
  const doc = mergedReportFor(report, report.prs[0])
  assert.equal(doc.date, '2026-10-11')
  assert.deepEqual(doc.prs.map((p) => p.number), [907])
  assert.equal(doc.summary.pr_count, 1)
  assert.deepEqual(doc.summary.not_tested, [])
})

test('store-merged from the current PR-N.json itself: original kept as PR-N.v1.json, new file has previous_version + source_report', () => {
  const dir = mkdtempSync(join(tmpdir(), 'merged-self-'))
  const file = join(dir, 'PR-905.json')
  const original = { date: '2026-10-10', run: { args: { prs: [905] } }, system_health: health(), prs: [prEntry(905, '2026-10-11T06:42:40Z'), { number: 892, state: 'merged', merged_at: '2026-10-10T06:23:41Z', verdict: 'not-tested', severity: 'untested', not_tested_code: 'excluded-by-pr-filter', findings: [] }] }
  writeFileSync(file, JSON.stringify(original))
  const report = JSON.parse(readFileSync(file, 'utf8'))
  report.prs = report.prs.filter((p) => p.number === 905)
  // without allowSelf the writer refuses to overwrite its own source
  assert.deepEqual(writePerPrReports(report, { opts: { writeMerged: true, mergedDir: dir }, outFile: file, kind: 'merged' }), [])
  const written = writePerPrReports(report, { opts: { writeMerged: true, allowSelf: true, mergedDir: dir }, outFile: file, kind: 'merged' })
  assert.deepEqual(written.map((w) => w.pr), [905])
  assert.ok(existsSync(join(dir, 'PR-905.v1.json')))
  assert.deepEqual(JSON.parse(readFileSync(join(dir, 'PR-905.v1.json'), 'utf8')), original, 'original unchanged')
  const now = JSON.parse(readFileSync(file, 'utf8'))
  assert.equal(now.previous_version, 'PR-905.v1.json')
  assert.equal(now.run.source_report, join(dir, 'PR-905.v1.json'))
  assert.equal(now.date, '2026-10-11')
  assert.deepEqual(now.prs.map((p) => p.number), [905])
  assert.equal(now.written_by, 'nightly')
})
