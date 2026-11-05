// 11-05 #1788 / #1729：手动补跑 --trigger manual 覆盖 reports/merged/PR-N.json，首次 event 触发信息不能丢
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-first-trigger-'))
const N = await import('../src/nightly.mjs')
const run = (iso, trigger) => ({ run: { started_at: iso, trigger }, prs: [{ number: 1788 }] })
const read = (f) => JSON.parse(readFileSync(f, 'utf8'))

test('event then manual rerun: first_trigger stays event, trigger_history appends', () => {
  const f = join(mkdtempSync(join(tmpdir(), 'merged-')), 'PR-1788.json')
  N.writeReport(N.stampFirstStart(run('2026-11-04T18:44:03.000Z', 'event'), f), f)
  N.writeReport(N.stampFirstStart(run('2026-11-04T21:47:54.000Z', 'manual'), f), f)
  const d = read(f)
  assert.equal(d.run.trigger, 'manual')
  assert.equal(d.run.first_trigger, 'event')
  assert.deepEqual(d.run.trigger_history, ['event', 'manual'])
})

test('old report without the fields: its run.trigger counts as the first', () => {
  const f = join(mkdtempSync(join(tmpdir(), 'merged-')), 'PR-1729.json')
  N.writeReport(run('2026-11-04T20:12:28.000Z', 'event'), f)
  N.writeReport(N.stampFirstStart(run('2026-11-04T22:32:55.000Z', 'manual'), f), f)
  N.writeReport(N.stampFirstStart(run('2026-11-04T23:03:00.000Z', 'manual'), f), f)
  const d = read(f)
  assert.equal(d.run.first_trigger, 'event')
  assert.deepEqual(d.run.trigger_history, ['event', 'manual', 'manual'])
})
