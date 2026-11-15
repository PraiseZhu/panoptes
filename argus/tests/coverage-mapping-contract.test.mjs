import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { compactCoverageEvidence, coverageMapping, coverageRunnerState, coverageGate, readCoverageDir, newCodeCoverage, isCoverageSource } from '../src/coverage.mjs'
import { mergeCoverageFinal, mergeCoverageShards, coverageSummaryOf } from '../src/shard.mjs'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'coverage-mapping-runner-'))
const { withV8Coverage } = await import('../src/runner.mjs')
const { toCoverage } = await import('../src/matrix.mjs')

const added = { 'src/a.ts': [{ line: 2, text: 'return x ? yes() : no()' }] }
const loc = (start, end = start) => ({ start: { line: start, column: 0 }, end: { line: end, column: 10 } })
const final = (hits = 1, all = false) => ({ '/repo/src/a.ts': {
  path: '/repo/src/a.ts', all, statementMap: { 0: loc(2) }, s: { 0: hits },
  fnMap: { 0: { name: 'a', loc: loc(1, 3) } }, f: { 0: hits },
  branchMap: { 0: { loc: loc(2), locations: [loc(2), { ...loc(2), start: { line: 2, column: 6 } }] } }, b: { 0: [hits, 0] },
} })
const result = (id, hits = 1) => ({ id, ok: true, coverage: { status: 'ok', total: {}, evidence: compactCoverageEvidence(final(hits), '/repo') } })

test('loaded changed line maps to unit, node-test and browser independently of declared covers', () => {
  const out = coverageMapping({ added, results: [result('unit', 0), result('node-test'), result('browser')] })
  const row = out.files[0]
  assert.equal(out.schema, 'coverage.mapping/v1')
  assert.equal(row.load_status, 'loaded')
  assert.deepEqual(row.loaded_by, ['unit', 'node-test', 'browser'])
  assert.deepEqual(row.lines.changed[0], { line: 2, status: 'pass', measured_by: ['unit', 'node-test', 'browser'], covered_by: ['node-test', 'browser'] })
  assert.equal(row.branches.total, 2)
  assert.equal(row.branches.covered, 1)
  assert.equal(row.branches.status, 'fail')
  assert.equal(row.functions.status, 'pass')
  assert.equal(out.finding_eligible, false)
  assert.equal(out.blocking, false)
})

test('loaded but unexecuted source is measurable; Istanbul all placeholders are unloaded', () => {
  const unloaded = result('unit', 0)
  unloaded.coverage.evidence = compactCoverageEvidence(final(0, true), '/repo')
  const a = coverageMapping({ added, results: [result('unit', 0)] }).files[0]
  const b = coverageMapping({ added, results: [unloaded] }).files[0]
  assert.equal(a.load_status, 'loaded')
  assert.equal(a.lines.status, 'fail')
  assert.equal(b.load_status, 'unloaded')
  assert.deepEqual(b.loaded_by, [])
  assert.equal(b.lines.status, 'fail')
})

test('plain JS/subprocess without reports is not_measurable, not a 0% product failure', () => {
  const js = { 'scripts/child.mjs': [{ line: 2, text: 'work()' }] }
  const out = coverageMapping({ added: js, results: [{ id: 'unit', coverage: { status: 'ok', files: { 'src/other.ts': { lines: [1], covered: [1] } } } }] })
  assert.equal(out.files[0].lines.status, 'not_measurable')
  assert.equal(out.files[0].lines.pct, null)
  assert.equal(out.finding_eligible, false)
  assert.equal(newCodeCoverage(js, {}).status, 'not_measurable')
  assert.deepEqual(newCodeCoverage(js, {}).not_measurable_files, ['scripts/child.mjs'])
})

test('runner failure with stale files is unavailable and never coverage fail', () => {
  for (const flags of [{ timedOut: true }, { unavailable: true }, { runner_error: true }, { status: 'environment_unavailable' },
    { coverage: { status: 'unavailable', reason: 'runner failed', files: { 'src/a.ts': { lines: [2], covered: [] } } } }]) {
    const r = { ...result('unit'), ...flags }
    assert.equal(coverageRunnerState(r).status, 'unavailable')
    const out = coverageMapping({ added, results: [r] })
    assert.equal(out.files[0].lines.status, 'unavailable')
    assert.deepEqual(out.files[0].loaded_by, [])
    assert.equal(out.finding_eligible, false)
  }
  const gate = coverageGate({ added, coverage: { status: 'unavailable', reason: 'runner failure' } })
  assert.equal(gate.status, 'unavailable')
  assert.equal(gate.pass, null)
  assert.equal(gate.values, null)
})

test('real withV8Coverage empty or absent directory is unavailable, not 0% changed TS coverage', () => {
  for (const exists of [false, true]) {
    const root = mkdtempSync(join(tmpdir(), 'coverage-empty-v8-'))
    const dir = join(root, 'v8')
    if (exists) mkdirSync(dir)
    const coverage = withV8Coverage(null, dir, root)
    assert.deepEqual(coverage, { status: 'ok', kind: 'v8', total: null, files: {} })
    const state = coverageRunnerState({ id: 'node-test', coverage })
    assert.deepEqual(state, { status: 'unavailable', reason: 'coverage-not-produced' })
    const mapping = coverageMapping({ added, results: [{ id: 'node-test', coverage }] })
    assert.equal(mapping.files[0].load_status, 'unknown')
    assert.equal(mapping.files[0].lines.status, 'unavailable')
    assert.equal(mapping.files[0].lines.reason, 'coverage-not-produced')
    assert.equal(mapping.files[0].lines.pct, null)
    assert.equal(mapping.files[0].lines.covered, null)
  }
  assert.equal(coverageRunnerState({ coverage: { status: 'ok', evidence: {} } }).status, 'unavailable')
  assert.equal(coverageRunnerState({ coverage: { status: 'ok', files: {}, evidence: compactCoverageEvidence(final(), '/repo') } }).status, 'ok')
})

test('only assigned steps determine whether absent source coverage is a gap or measured zero', () => {
  const changed = { 'src/Panel.tsx': [{ line: 2, text: 'return <Panel />' }] }
  const other = { id: 'other', ok: true, coverage: { status: 'ok', files: { 'other/a.ts': { lines: [1], covered: [1] } } } }
  const browser = { id: 'browser', kind: 'product', exercises: ['src/**'], applicability: { changeKinds: ['browser'] }, coveragePolicy: { status: 'not_measurable' } }
  const otherStep = { id: 'other', kind: 'product', exercises: ['other/**'], applicability: { changeKinds: ['browser'] }, coveragePolicy: { status: 'collect' } }
  for (const [flags, expected, reason] of [
    [{ ok: true }, 'not_measurable', 'step-coverage-not-measurable'],
    [{ unavailable: true }, 'unavailable', 'coverage-runner-unavailable'],
    [{ status: 'skipped' }, 'not_evaluated', 'coverage-step-not-run'],
  ]) {
    const row = coverageMapping({ added: changed, profile: { steps: [browser, otherStep] }, results: [other, { id: 'browser', ...flags }] }).files[0]
    assert.equal(row.lines.status, expected)
    assert.equal(row.lines.reason, reason)
    assert.equal(row.lines.pct, null)
    assert.equal(row.lines.covered, null)
    assert.equal(row.load_status, 'unknown')
    assert.deepEqual(row.measurement_gaps.map((s) => s.step), ['browser'])
  }
  const notRun = coverageMapping({ added: changed, profile: { steps: [browser, otherStep] }, results: [other] }).files[0]
  assert.equal(notRun.lines.status, 'not_measurable', 'even an absent browser result retains the assigned step policy')
  const measurable = { ...browser, coveragePolicy: { status: 'collect' } }
  const row = coverageMapping({ added: changed, profile: { steps: [measurable, otherStep] }, results: [other, { ...other, id: 'browser' }] }).files[0]
  assert.equal(row.load_status, 'unloaded')
  assert.equal(row.lines.status, 'fail')
  assert.equal(row.lines.pct, 0)
  assert.equal(row.lines.reason, 'assigned-steps-did-not-load-source')
  assert.deepEqual(row.measurement_gaps, [])
  const throughMatrix = toCoverage({ profile: { steps: [browser, otherStep] }, triage: { rows: [] },
    files: [{ filename: 'src/Panel.tsx', patch: '@@ -1 +2 @@\n-old()\n+return <Panel />' }],
    results: [other, { id: 'browser', ok: true }] }).changed_source.files[0]
  assert.equal(throughMatrix.lines.status, 'not_measurable', 'matrix must pass profile assignment into the mapping')
})

test('an assigned unmeasurable layer prevents unrelated measured layers from implying 0% source coverage', () => {
  const profile = { steps: [
    { id: 'unit', kind: 'product', exercises: ['src/**'], applicability: { changeKinds: ['browser'] }, coveragePolicy: { status: 'collect' } },
    { id: 'browser', kind: 'product', exercises: ['src/**'], applicability: { changeKinds: ['browser'] }, coveragePolicy: { status: 'not_measurable' } },
  ] }
  const mapping = coverageMapping({ added: { 'src/Panel.tsx': [{ line: 2, text: 'return <Panel />' }] }, profile,
    results: [{ id: 'unit', coverage: { status: 'ok', files: { 'src/other.ts': { lines: [1], covered: [1] } } } }, { id: 'browser', ok: true }] })
  assert.equal(mapping.files[0].lines.status, 'not_measurable')
  assert.equal(mapping.files[0].lines.pct, null)
})

test('runner failure on one layer does not erase valid line evidence from another layer', () => {
  const out = coverageMapping({ added, results: [{ ...result('unit'), timedOut: true }, result('node-test')] })
  assert.equal(out.sources[0].status, 'unavailable')
  assert.equal(out.files[0].lines.status, 'pass')
  assert.deepEqual(out.files[0].loaded_by, ['node-test'])
})

test('skipped steps and explicit not-measured states cannot credit stale coverage files', () => {
  const r = { ...result('browser'), status: 'skipped' }
  const mapping = coverageMapping({ added, results: [r] })
  assert.equal(mapping.sources[0].status, 'not_evaluated')
  assert.equal(mapping.files[0].lines.status, 'not_evaluated')
  assert.deepEqual(mapping.files[0].loaded_by, [])
  const notMeasurable = coverageMapping({ added, results: [{ id: 'browser', coverage: { status: 'not_measurable' } }] })
  assert.equal(notMeasurable.files[0].lines.status, 'not_measurable')
})

test('no run, docs-only, comment-only and line-only reports do not invent measured branches/functions', () => {
  assert.equal(coverageMapping({ added }).files[0].lines.status, 'not_evaluated')
  assert.deepEqual(coverageMapping({ added: { 'docs/a.md': [{ line: 1, text: 'text' }] } }).files, [])
  const comment = coverageMapping({ added: { 'src/a.ts': [{ line: 2, text: '// note' }] } })
  assert.equal(comment.files[0].lines.status, 'not_evaluated')
  const v8 = coverageMapping({ added, results: [{ id: 'node-test', coverage: { status: 'ok', kind: 'v8', files: { 'src/a.ts': { lines: [2], covered: [2] } } } }] })
  assert.equal(v8.files[0].lines.status, 'pass')
  for (const key of ['branches', 'functions']) assert.equal(v8.files[0][key].status, 'not_evaluated')
  for (const f of ['src/a.node-test.mjs', 'fixtures/helper.js', 'src/a-fixture.ts']) assert.equal(isCoverageSource(f), false, f)
})

test('multiline changed lines use innermost uncovered branch rather than outer statement hit', () => {
  const evidence = { 'src/a.ts': { lines: [1], covered: [1], spans: [[1, 8, true], [2, 4, false]] } }
  const out = coverageMapping({ added: { 'src/a.ts': [{ line: 3, text: 'no()' }, { line: 6, text: 'yes()' }] }, results: [{ id: 'unit', coverage: { status: 'ok', evidence } }] })
  assert.deepEqual(out.files[0].lines.changed.map((r) => r.status), ['fail', 'pass'])
})

test('shard merge preserves loaded state, sources, missing shard reasons and changed metric ranges', () => {
  const empty = final(0, true), loaded = final(1)
  const merged = mergeCoverageFinal([empty, loaded], { sources: [{ step: 'unit', shard: 1 }, { step: 'unit', shard: 2 }] })
  const fc = compactCoverageEvidence(merged, '/repo')['src/a.ts']
  assert.equal(fc.load_status, 'loaded')
  assert.deepEqual(fc.sources, [{ step: 'unit', shard: 1 }, { step: 'unit', shard: 2 }])
  assert.equal(fc.functions[0].covered, true)
  assert.equal(merged['/repo/src/a.ts'].s[0], 1)
  assert.equal(empty['/repo/src/a.ts'].all, true, 'does not mutate input')
  const partial = mergeCoverageShards([{ shard: 1, step: 'unit', final: loaded }, { shard: 2, step: 'unit', status: 'unavailable', reason: 'runner-timeout' }])
  assert.equal(partial.status, 'unavailable')
  assert.equal(partial.failures[0].reason, 'runner-timeout')
  assert.equal(partial.files['/repo/src/a.ts'].coverage_failures[0].shard, 2)
  assert.equal(mergeCoverageShards([]).status, 'unavailable')
})

test('readCoverageDir carries compact range and shard evidence without changing legacy files', () => {
  const root = mkdtempSync(join(tmpdir(), 'coverage-contract-'))
  const dir = join(root, 'coverage'); mkdirSync(dir)
  const merged = mergeCoverageFinal([final(1), final(0)])
  writeFileSync(join(dir, 'coverage-final.json'), JSON.stringify(merged))
  writeFileSync(join(dir, 'coverage-summary.json'), JSON.stringify(coverageSummaryOf(merged)))
  const report = readCoverageDir(dir, '/repo')
  assert.equal(report.status, 'ok')
  assert.deepEqual(report.files['src/a.ts'], { lines: [2], covered: [2] })
  assert.equal(report.evidence['src/a.ts'].sources.length, 2)
  assert.equal(coverageMapping({ added, results: [{ id: 'unit', coverage: report }] }).files[0].functions.status, 'pass')
})

test('readCoverageDir preserves incomplete shard failure as unavailable even with a readable summary', () => {
  const root = mkdtempSync(join(tmpdir(), 'coverage-partial-contract-'))
  const partial = mergeCoverageShards([{ final: final(1), step: 'unit' }, { step: 'unit', reason: 'runner-timeout' }])
  writeFileSync(join(root, 'coverage-final.json'), JSON.stringify(partial.files))
  writeFileSync(join(root, 'coverage-summary.json'), JSON.stringify(coverageSummaryOf(partial.files)))
  const report = readCoverageDir(root, '/repo')
  assert.equal(report.status, 'unavailable')
  assert.equal(coverageGate({ added, coverage: report }).status, 'unavailable')
})
