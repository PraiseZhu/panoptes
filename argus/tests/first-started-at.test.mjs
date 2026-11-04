// 11-05 stdcheck qa_start 口径：首跑失败后重跑覆盖 reports/merged/PR-N.json，run.first_started_at 必须保留首跑开始时间（PR-1740 / PR-1788 模型）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-first-start-'))
const N = await import('../src/nightly.mjs')
const run = (iso) => ({ run: { started_at: iso }, prs: [{ number: 1740 }] })
const read = (f) => JSON.parse(readFileSync(f, 'utf8'))

test('1740: first run wrote a report, the rerun overwrites it but keeps first_started_at', () => {
  const f = join(mkdtempSync(join(tmpdir(), 'merged-')), 'PR-1740.json')
  N.recordAttemptStart(f, new Date('2026-11-04T17:54:06Z'))
  N.writeReport(N.stampFirstStart(run('2026-11-04T17:54:06.000Z'), f), f)
  N.recordAttemptStart(f, new Date('2026-11-04T18:27:03Z'))
  N.writeReport(N.stampFirstStart(run('2026-11-04T18:27:03.000Z'), f), f)
  const d = read(f)
  assert.equal(d.run.started_at, '2026-11-04T18:27:03.000Z')
  assert.equal(d.run.first_started_at, '2026-11-04T17:54:06.000Z')
  assert.equal(d.run.attempt, 2)
  assert.equal(read(join(f, '..', 'history/PR-1740.v1.json')).run.first_started_at, '2026-11-04T17:54:06.000Z')
})

test('1788: first run died before writing any report (ENOSPC, exit 3); the rerun still gets the first start', () => {
  const f = join(mkdtempSync(join(tmpdir(), 'merged-')), 'PR-1788.json')
  N.recordAttemptStart(f, new Date('2026-11-04T18:32:06Z'))   // first run: no report written
  assert.equal(existsSync(f), false)
  N.recordAttemptStart(f, new Date('2026-11-04T18:44:03Z'))
  N.writeReport(N.stampFirstStart(run('2026-11-04T18:44:03.000Z'), f), f)
  const d = read(f)
  assert.equal(d.run.first_started_at, '2026-11-04T18:32:06.000Z')
  assert.equal(d.run.attempt, 2)
  assert.deepEqual(read(N.attemptsPath(f)), { first_started_at: '2026-11-04T18:32:06.000Z', attempts: 2, last_started_at: '2026-11-04T18:44:03.000Z' })
})

test('a clean first run: first_started_at = started_at, attempt 1; an old report without the field counts as the first', () => {
  const dir = mkdtempSync(join(tmpdir(), 'merged-'))
  const f = join(dir, 'PR-1718.json')
  N.recordAttemptStart(f, new Date('2026-11-04T05:47:54Z'))
  N.writeReport(N.stampFirstStart(run('2026-11-04T05:47:54.000Z'), f), f)
  assert.deepEqual([read(f).run.first_started_at, read(f).run.attempt], ['2026-11-04T05:47:54.000Z', 1])
  const g = join(dir, 'PR-1600.json')
  N.writeReport(run('2026-11-01T00:00:00.000Z'), g)   // pre-feature report
  N.recordAttemptStart(g, new Date('2026-11-02T00:00:00Z'))
  N.writeReport(N.stampFirstStart(run('2026-11-02T00:00:00.000Z'), g), g)
  assert.deepEqual([read(g).run.first_started_at, read(g).run.attempt], ['2026-11-01T00:00:00.000Z', 2])
})

test('stampFirstStart leaves non per-PR files (daily reports) alone', () => {
  const f = join(mkdtempSync(join(tmpdir(), 'reports-')), '2026-11-04.json')
  assert.equal(N.stampFirstStart(run('2026-11-04T00:00:00.000Z'), f).run.first_started_at, undefined)
})
