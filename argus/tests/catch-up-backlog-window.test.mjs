// 11-04 (#203 follow-up): backlog gaps deferred by the per-round cap stay in catch-up state, so when the date rolls over and their
// merge day leaves the 2-day window they are still caught up (before: silently dropped, no report ever).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { catchUp } from '../src/catchup.mjs'

const g = (number, day, at) => ({ number, merged_at: at, merge_sha: String(number % 10).repeat(40), day, base_ref: 'integration/canvas-s2' })
const D2 = [g(1597, '2026-11-02', '2026-11-02T03:00:00Z'), g(1601, '2026-11-02', '2026-11-02T04:00:00Z'), g(1379, '2026-11-02', '2026-11-02T05:00:00Z'), g(1610, '2026-11-02', '2026-11-02T06:00:00Z')]

test('catch-up: capped backlog survives the date rollover (kept in state, run next round)', () => {
  const reportsDir = mkdtempSync(join(tmpdir(), 'argus-cu-bw-'))
  const ran = []
  const opt = { repo: 'o/r', reportsDir, active: () => new Set(), skipBaseBranches: ['probe-*'], mainOnly: false, maxBacklog: 2, runOne: (p) => { ran.push(p.number); return 1 } }
  const r1 = catchUp({ ...opt, now: new Date('2026-11-03T04:07:00Z'), list: () => D2 })   // 11-03 12:07 BJ: window 11-02..11-03
  assert.deepEqual(r1.deferred_backlog, [1379, 1610])
  ran.length = 0
  // 11-04 02:07 BJ: window is 11-03..11-04, the list no longer has any 11-02 merge
  catchUp({ ...opt, now: new Date('2026-11-03T18:07:00Z'), list: () => [] })
  assert.ok(ran.includes(1379) && ran.includes(1610), `deferred backlog must still run after the rollover, ran ${JSON.stringify(ran)}`)
})
