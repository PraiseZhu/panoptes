// 11-03 integration-branch QA: catch-up tests every base except registry.skipBaseBranches (probe-*), the same rule as
// nightly / mergewatch / watchdog; old skipped/non-main-base reports of a now-tested base are gaps (stale-skip); main first.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { catchUp } from '../src/catchup.mjs'

const now = new Date('2026-11-02T10:10:00Z')
const pr = (number, base_ref, m = '09:00') => ({ number, merged_at: `2026-11-02T${m}:00Z`, merge_sha: String(number % 10).repeat(40), day: '2026-11-02', base_ref })
const run = (prs, opts = {}) => {
  const reportsDir = opts.reportsDir ?? mkdtempSync(join(tmpdir(), 'argus-cu-int-'))
  const ran = []
  const r = catchUp({ repo: 'o/r', now, reportsDir, list: () => prs, active: () => new Set(), skipBaseBranches: ['probe-*'], mainOnly: false, runOne: (p) => { ran.push(p.number); return 1 }, ...opts })
  return { r, ran }
}

test('catch-up: integration/* and feat/* merges are gaps; probe-* stays skipped', () => {
  const { r, ran } = run([pr(1601, 'integration/canvas-s2'), pr(1379, 'feat/video-in-canvas', '09:01'), pr(1410, 'probe-1410/status/base', '09:02')])
  assert.deepEqual(ran, [1601, 1379])
  assert.deepEqual(r.skipped, [{ number: 1410, base_ref: 'probe-1410/status/base', reason: 'non-main-base' }])
})

test('catch-up: a skipped/non-main-base report for a base that is now tested is a gap (stale-skip)', () => {
  const reportsDir = mkdtempSync(join(tmpdir(), 'argus-cu-int-'))
  mkdirSync(join(reportsDir, 'merged'), { recursive: true })
  for (const [n, b] of [[1402, 'integration/canvas-s1'], [1411, 'probe-x']]) writeFileSync(join(reportsDir, 'merged', `PR-${n}.json`), JSON.stringify({ prs: [{ number: n, verdict: 'skipped', skip_reason: 'non-main-base', base_ref: b }] }))
  const { r, ran } = run([pr(1402, 'integration/canvas-s1'), pr(1411, 'probe-x', '09:01')], { reportsDir })
  assert.deepEqual(ran, [1402])
  assert.equal(r.gaps[0].reason, 'stale-skip')
})

test('catch-up: main gaps run before branch gaps (queue: main first)', () => {
  const { ran } = run([pr(1601, 'integration/canvas-s2', '08:00'), pr(1379, 'feat/video-in-canvas', '08:30'), pr(1700, 'main', '09:00')])
  assert.deepEqual(ran, [1700, 1601, 1379])
})

test('catch-up: mainOnly (PANOPTES_MAIN_ONLY rollback) skips every non-main base again', () => {
  const { r, ran } = run([pr(1601, 'integration/canvas-s2'), pr(1700, 'main', '09:01')], { mainOnly: true })
  assert.deepEqual(ran, [1700])
  assert.deepEqual(r.skipped.map((s) => s.number), [1601])
})
