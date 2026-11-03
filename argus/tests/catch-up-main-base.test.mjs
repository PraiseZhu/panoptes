// catch-up 目标分支规则（11-03 起）：只有 registry.skipBaseBranches（probe-*）记 skipped，不补测、不计尝试；release/*、integration/* 等照常是缺口
// （10-30 D 曾只认 main；PANOPTES_MAIN_ONLY=1 可退回，见 catch-up-integration-base.test.mjs）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { catchUp, listMergedPrs } from '../src/catchup.mjs'

test('listMergedPrs keeps baseRefName as base_ref (gh pr list rows)', () => {
  const rows = [{ number: 7, title: 't', mergedAt: '2026-10-30T02:00:00Z', mergeCommit: { oid: 'a'.repeat(40) }, baseRefName: 'probe-x' }]
  const out = listMergedPrs('o/r', ['2026-10-30'], { run: () => ({ status: 0, stdout: JSON.stringify(rows) }) })
  assert.equal(out.length, 1)
  assert.equal(out[0].base_ref, 'probe-x')
})

test('catchUp: probe-* bases are skipped, never run; main and release/* are gaps', () => {
  const reportsDir = mkdtempSync(join(tmpdir(), 'argus-cu-base-'))
  const now = new Date('2026-10-30T10:10:00Z')
  const prs = [
    { number: 1, merged_at: '2026-10-30T09:00:00Z', merge_sha: 'a'.repeat(40), day: '2026-10-30', base_ref: 'main' },
    { number: 2, merged_at: '2026-10-30T09:01:00Z', merge_sha: 'b'.repeat(40), day: '2026-10-30', base_ref: 'probe-base' },
    { number: 3, merged_at: '2026-10-30T09:02:00Z', merge_sha: 'c'.repeat(40), day: '2026-10-30', base_ref: 'release/1.2' },
  ]
  const ran = []
  const r = catchUp({ repo: 'o/r', now, reportsDir, mainOnly: false, list: () => prs, active: () => new Set(), runOne: (p) => { ran.push(p.number); return 1 } })
  assert.deepEqual(ran, [1, 3])
  assert.deepEqual(r.gaps.map((g) => g.number), [1, 3])
  assert.deepEqual(r.skipped, [{ number: 2, base_ref: 'probe-base', reason: 'non-main-base' }])
  const only = catchUp({ repo: 'o/r', now, reportsDir, mainOnly: false, list: () => prs.slice(1, 2), active: () => new Set(), runOne: () => { throw new Error('must not run') } })
  assert.equal(only.status, 'nothing-to-do')
  assert.equal(only.skipped.length, 1)
})
