import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { isTestPath, isTestsOnly } from '../src/classify.mjs'
import { fileKind, aiTestTier } from '../src/aitests.mjs'
import { isCoverageSource } from '../src/coverage.mjs'
import { catchUp, prsInArgv, activeRunPrs, docsOnlyOf } from '../src/catchup.mjs'

const HELPERS = ['scripts/ops/company-cli-test-fixture.mjs', 'scripts/ops/company-validation-fixture.mjs', 'scripts/ci/design-ratchet-fixture.mjs', 'src/canvas/canvasHarness.ts', 'src/x/render-harness.tsx', 'src/x/nodeFixtures.ts', 'src/x/scene_fixture.js']
const PRODUCT = ['src/lib/fixtureLoader.ts', 'src/fixture.ts', 'src/model/FixtureNode.ts', 'src/harnessConfig.ts', 'src/lib/harness.ts', 'scripts/ops/company-cli.mjs']

test('test helpers (*-fixture.*, *Harness.*) count as tests for classify, tier and coverage', () => {
  for (const f of HELPERS) {
    assert.equal(isTestPath(f), true, f)
    assert.equal(fileKind(f), 'tests', f)
    assert.equal(isCoverageSource(f), false, f)
  }
  assert.equal(isTestsOnly([{ filename: 'src/a.test.ts' }, { filename: HELPERS[0] }]), true)
  const t = aiTestTier({ files: [{ filename: HELPERS[0], additions: 400, deletions: 0 }, { filename: 'src/a.ts', additions: 3, deletions: 3 }] })
  assert.equal(t.tier.product_lines, 6)
})

test('product files that merely mention fixture/harness stay product', () => {
  for (const f of PRODUCT) {
    assert.equal(isTestPath(f), false, f)
    if (f.startsWith('src/')) { assert.equal(fileKind(f), 'product', f); assert.equal(isCoverageSource(f), true, f) }
  }
})

test('prsInArgv: run-nightly.sh / cli.mjs nightly --pr N, nothing else', () => {
  assert.deepEqual(prsInArgv(['bash', '/p/run-nightly.sh', '--trigger', 'event', '--pr', '1161', '--wait-lock', '14400']), [1161])
  assert.deepEqual(prsInArgv(['node', '--disable-warning=ExperimentalWarning', 'src/cli.mjs', 'nightly', '--pr', '1128', '--pr', '1129']), [1128, 1129])
  assert.deepEqual(prsInArgv(['node', 'src/cli.mjs', 'catch-up', '--wait-lock', '14400']), [])
  assert.deepEqual(prsInArgv(['gh', 'pr', 'view', '--pr', '7']), [])
  assert.deepEqual(prsInArgv(['bash', '/p/run-nightly.sh', '--pr=1161', '--wait-lock', '14400']), [1161])
  assert.deepEqual(prsInArgv(['node', 'src/cli.mjs', 'nightly', '--pr=1128', '--pr', '1129']), [1128, 1129])
  assert.deepEqual(prsInArgv(['bash', 'run-nightly.sh', '--pr=', '--pr=abc', '--pr=12x']), [])
  assert.deepEqual(prsInArgv(['gh', 'pr', 'view', '--pr=7']), [])
})

test('activeRunPrs scans a proc dir (running + queued-on-lock processes)', () => {
  const proc = mkdtempSync(join(tmpdir(), 'argus-proc-'))
  const add = (pid, argv) => { mkdirSync(join(proc, String(pid))); writeFileSync(join(proc, String(pid), 'cmdline'), argv.join('\0') + '\0') }
  add(101, ['bash', 'run-nightly.sh', '--pr', '1128', '--wait-lock', '14400'])
  add(102, ['node', 'src/cli.mjs', 'nightly', '--pr', '1161'])
  add(103, ['sleep', '60'])
  mkdirSync(join(proc, 'self'))
  assert.deepEqual([...activeRunPrs({ procDir: proc, selfPid: 1 })].sort(), [1128, 1161])
})

test('catchUp skips PRs with an on-merge run in progress / queued (not missing, no attempt)', () => {
  const reportsDir = mkdtempSync(join(tmpdir(), 'argus-cu-'))
  const now = new Date('2026-10-21T10:10:00Z')
  const prs = [{ number: 1161, merged_at: '2026-10-21T09:55:26Z', merge_sha: 'a'.repeat(40), day: '2026-10-21' }, { number: 1162, merged_at: '2026-10-21T09:56:00Z', merge_sha: 'b'.repeat(40), day: '2026-10-21' }]
  const ran = []
  const r = catchUp({ repo: 'o/r', now, reportsDir, list: () => prs, active: () => new Set([1161]), runOne: (p) => { ran.push(p.number); return 1 } })
  assert.deepEqual(ran, [1162])
  assert.deepEqual(r.in_progress, [1161])
  assert.deepEqual(r.gaps.map((g) => g.number), [1162])
  const r2 = catchUp({ repo: 'o/r', now, reportsDir, list: () => prs.slice(0, 1), active: () => new Set([1161]), runOne: () => { throw new Error('must not run') } })
  assert.equal(r2.status, 'nothing-to-do')
  assert.deepEqual(r2.in_progress, [1161])
})

test('catchUp checks the diff first: docs-only gaps jump the queue (10-26 #1298 waited 40 min behind a long run)', () => {
  const reportsDir = mkdtempSync(join(tmpdir(), 'argus-cu-docs-'))
  const now = new Date('2026-10-26T10:10:00Z')
  const prs = [1296, 1297, 1298].map((n, i) => ({ number: n, merged_at: `2026-10-26T09:5${i}:00Z`, merge_sha: String(i).repeat(40), day: '2026-10-26' }))
  const ran = []
  const r = catchUp({ repo: 'o/r', now, reportsDir, list: () => prs, active: () => new Set(), docsOnly: (p) => p.number === 1298, runOne: (p) => { ran.push(p.number); return 1 } })
  assert.deepEqual(ran, [1298, 1296, 1297])
  assert.equal(r.gaps.find((g) => g.number === 1298).docs_only, true)
  // classifier errors never block: treated as not docs-only, merge order kept
  const ran2 = []
  catchUp({ repo: 'o/r', now, reportsDir: mkdtempSync(join(tmpdir(), 'argus-cu-docs2-')), list: () => prs, active: () => new Set(), docsOnly: () => { throw new Error('gh down') }, runOne: (p) => { ran2.push(p.number); return 1 } })
  assert.deepEqual(ran2, [1296, 1297, 1298])
})

test('docsOnlyOf: same classifyChange as nightly; gh failure → not docs-only', () => {
  const mk = (files, status = 0) => () => ({ status, stdout: files.map((f) => JSON.stringify({ filename: f, additions: 1, deletions: 0 })).join('\n') })
  assert.equal(docsOnlyOf('o/r', { run: mk(['docs/guide.md', 'README.md']), profile: {} })({ number: 1 }), true)
  assert.equal(docsOnlyOf('o/r', { run: mk(['docs/guide.md', 'src/a.ts']), profile: {} })({ number: 1 }), false)
  assert.equal(docsOnlyOf('o/r', { run: mk(['docs/guide.md'], 1), profile: {} })({ number: 1 }), false)
})
