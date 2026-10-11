// 版本放 history/；一次性迁移只改路径字段；早于功能的 flaky 条目规范化为 not-compared（report predates feature）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, readFileSync, readdirSync, existsSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-history-'))
const N = await import('../src/nightly.mjs')

test('migrateHistory moves flat *.vN.json into history/ and rewrites only the path fields', () => {
  const dir = mkdtempSync(join(tmpdir(), 'merged-mig-'))
  const w = (n, d) => writeFileSync(join(dir, n), JSON.stringify(d))
  w('PR-1.v1.json', { n: 'v1', prs: [] })
  w('PR-1.json', { n: 'cur', previous_version: 'PR-1.v1.json', run: { source_report: join(dir, 'PR-1.v1.json') }, prs: [] })
  w('PR-2.json', { n: 'untouched', prs: [] })
  w('PR-3.v1.json', { n: '3v1' }); w('PR-3.v2.json', { n: '3v2', previous_version: 'PR-3.v1.json' }); w('PR-3.json', { previous_version: 'PR-3.v2.json' })
  const r = N.migrateHistory(dir)
  assert.deepEqual(readdirSync(dir).sort(), ['PR-1.json', 'PR-2.json', 'PR-3.json', 'history'])
  assert.deepEqual(readdirSync(join(dir, 'history')).sort(), ['PR-1.v1.json', 'PR-3.v1.json', 'PR-3.v2.json'])
  const cur = JSON.parse(readFileSync(join(dir, 'PR-1.json'), 'utf8'))
  assert.deepEqual(cur, { n: 'cur', previous_version: 'history/PR-1.v1.json', run: { source_report: join(dir, 'history/PR-1.v1.json') }, prs: [] })
  assert.equal(JSON.parse(readFileSync(join(dir, 'history/PR-3.v2.json'), 'utf8')).previous_version, 'history/PR-3.v1.json')
  assert.deepEqual(JSON.parse(readFileSync(join(dir, 'PR-2.json'), 'utf8')), { n: 'untouched', prs: [] })
  assert.equal(r.moved.length, 3)
  assert.deepEqual(r.rewritten.flatMap((x) => x.changes.map((c) => c.field)).sort(), ['previous_version', 'previous_version', 'previous_version', 'run.source_report'])
  // idempotent
  assert.deepEqual(N.migrateHistory(dir).rewritten, [])
  // the next version continues after the migrated ones
  N.writeReport({ n: 'new' }, join(dir, 'PR-3.json'))
  assert.ok(existsSync(join(dir, 'history/PR-3.v3.json')))
  assert.equal(JSON.parse(readFileSync(join(dir, 'PR-3.json'), 'utf8')).previous_version, 'history/PR-3.v3.json')
})

test('nextVersionPath also counts legacy flat versions (no number reused)', () => {
  const dir = mkdtempSync(join(tmpdir(), 'merged-legacy-'))
  writeFileSync(join(dir, 'PR-9.v1.json'), '{}')
  assert.equal(N.nextVersionPath(join(dir, 'PR-9.json')), join(dir, 'history/PR-9.v2.json'))
})

test('normalizePreFeature: flaky entries without base_comparison become not-compared (report predates feature); others untouched', () => {
  const flaky = () => ({ check_id: 'bench', test: 't', status: 'flaky', flaky: true, evidence: { base_sha: 'b1', base_run: 'not-run' } })
  const withBc = { ...flaky(), base_comparison: { compared: true, result: 'passes-on-base' } }
  const report = { prs: [{ number: 892, base_sha: 'b1', failed_checks: [flaky(), withBc, { check_id: 'x', status: 'introduced' }], findings: [{ source: 'check', ...flaky() }] }] }
  const changes = N.normalizePreFeature(report)
  assert.equal(changes.length, 2)
  const bc = report.prs[0].failed_checks[0].base_comparison
  assert.deepEqual([bc.compared, bc.result, bc.reason, bc.note_zh, bc.base_sha, bc.base_run], [false, 'not-compared', 'report predates feature', '未与合并前版本对比（该报告早于此功能生成）', 'b1', 'not-run'])
  assert.equal(report.prs[0].findings[0].base_comparison.reason, 'report predates feature')
  assert.deepEqual(report.prs[0].failed_checks[1].base_comparison, { compared: true, result: 'passes-on-base' })
  assert.equal(report.prs[0].failed_checks[2].base_comparison, undefined)
  assert.equal(report.normalized.length, 1)
  assert.equal(N.normalizePreFeature(report).length, 0, 'idempotent')
})
