// 试运行检查（trialOnly）判 introduced：PR 与 base 须各跑 3 次，且 PR 3 次全失败、base 3 次全通过；否则 unverified。
// 依据：/workspace/pi-work/fp-679cbc-1110/（11-02 以来 persistFlush.bench 的 introduced 都只有 2 次 PR 失败 + 1 次 base 通过）。
import { test, describe, after } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = mkdtempSync(join(tmpdir(), 'argus-trial-intro-'))
process.env.ARGUS_ROOT = root
process.env.ARGUS_GH_BIN = join(here, 'fixtures/argus-fake-gh.mjs')
process.env.ARGUS_GH_RETRY_MS = '1'
after(() => rmSync(root, { recursive: true, force: true }))

const N = await import('../src/nightly.mjs')
const { MIRRORS_DIR } = await import('../src/paths.mjs')

const NAME = 'src/persist/persistFlush.bench.test.ts > persistDeltaFlush p95 stays within 50ms budget'
const KEY = `bench::${NAME}`

const fail = (id = 'bench') => ({
  id, kind: 'product', ok: false, exitCode: 1, timedOut: false, unavailable: false,
  failures: [NAME], failureDetails: [{ name: NAME, type: 'assertion' }],
  failureParseStatus: 'complete', testOutcomeStatus: 'complete',
  testOutcomes: [{ caseId: NAME, failureName: NAME, status: 'failed' }],
})
const pass = (id = 'bench') => ({
  id, kind: 'product', ok: true, exitCode: 0, timedOut: false, unavailable: false,
  failures: [], failureDetails: [], failureParseStatus: 'complete', testOutcomeStatus: 'complete',
  testOutcomes: [{ caseId: NAME, failureName: NAME, status: 'passed' }],
})
const setupOk = { id: 'install', kind: 'setup', ok: true, exitCode: 0 }
const packRun = (ok) => [setupOk, ok ? pass() : fail()]
const pack = (statuses) => statuses.map((s) => packRun(s === 'pass'))

function classifyArgs({ pr, base, trialOnly = true, latestIsSame = true } = {}) {
  const prRuns = pack(pr)
  const baseRuns = pack(base)
  return { first: prRuns[0], rerun: prRuns[1], base: baseRuns[0], prRuns, baseRuns, latest: null, latestIsSame, trialOnly }
}

test('试运行检查：PR 3 次全失败且 base 3 次全通过 → introduced', () => {
  const got = N.classifyKey(KEY, classifyArgs({ pr: ['fail', 'fail', 'fail'], base: ['pass', 'pass', 'pass'] }))
  assert.equal(got.status, 'introduced', got.why)
})

test('试运行检查：PR 3 次里有一次通过 → unverified（不能判 introduced）', () => {
  const got = N.classifyKey(KEY, classifyArgs({ pr: ['fail', 'fail', 'pass'], base: ['pass', 'pass', 'pass'] }))
  assert.equal(got.status, 'unverified', got.why)
  assert.match(String(got.why), /3 failing PR runs and 3 passing base runs/)
})

test('试运行检查：base 3 次里有一次失败 → unverified（不能判 introduced）', () => {
  const got = N.classifyKey(KEY, classifyArgs({ pr: ['fail', 'fail', 'fail'], base: ['pass', 'pass', 'fail'] }))
  assert.equal(got.status, 'unverified', got.why)
  assert.match(String(got.why), /3 failing PR runs and 3 passing base runs/)
})

test('对照：普通产品检查（非 trialOnly）仍按 2 次 PR 失败 + 1 次 base 通过判 introduced', () => {
  const got = N.classifyKey(KEY, classifyArgs({ pr: ['fail', 'fail'], base: ['pass'], trialOnly: false }))
  assert.equal(got.status, 'introduced', got.why)
})

test('试运行检查：首跑失败、重跑通过仍是 flaky，不要求凑满 3 次', () => {
  const got = N.classifyKey(KEY, { first: packRun(false), rerun: packRun(true), base: packRun(true), latest: null, latestIsSame: true, trialOnly: true })
  assert.equal(got.status, 'flaky', got.why)
})

const failJson = JSON.stringify({
  numTotalTestSuites: 1, numPassedTestSuites: 0, numFailedTestSuites: 1,
  numTotalTests: 1, numPassedTests: 0, numFailedTests: 1, numPendingTests: 0, numTodoTests: 0,
  success: false, unhandledErrors: [], numRuntimeErrorTestSuites: 0,
  testResults: [{
    name: 'src/persist/persistFlush.bench.test.ts', status: 'failed',
    assertionResults: [{ title: 'persistDeltaFlush p95 stays within 50ms budget', ancestorTitles: [], status: 'failed' }],
  }],
}, null, 2)

const passJson = JSON.stringify({
  numTotalTestSuites: 1, numPassedTestSuites: 1, numFailedTestSuites: 0,
  numTotalTests: 1, numPassedTests: 1, numFailedTests: 0, numPendingTests: 0, numTodoTests: 0,
  success: true, unhandledErrors: [], numRuntimeErrorTestSuites: 0,
  testResults: [{
    name: 'src/persist/persistFlush.bench.test.ts', status: 'passed',
    assertionResults: [{ title: 'persistDeltaFlush p95 stays within 50ms budget', ancestorTitles: [], status: 'passed' }],
  }],
}, null, 2)

const failOut = [
  '',
  ' RUN  v2.1.0 /tmp/fixture',
  '',
  ` FAIL  ${NAME}`,
  'AssertionError: expected 61.2 to be less than or equal to 50',
  '',
  ' Test Files  1 failed (1)',
  '      Tests  1 failed (1)',
].join('\n')

const passOut = [
  '',
  ' RUN  v2.1.0 /tmp/fixture',
  '',
  ` ✓  ${NAME}`,
  '',
  ' Test Files  1 passed (1)',
  '      Tests  1 passed (1)',
].join('\n')

// failLabels: labels whose bench step should fail (h1/h2/h3/b/b2/b3). Others pass.
function benchCmd(failLabels) {
  const fails = failLabels.join('|')
  const script = [
    'run="$1"',
    'label=$(basename "$run" | sed -n "s/^n[0-9]*-\\([a-z0-9]*\\)-[a-z0-9]*$/\\1/p")',
    `case "$label" in ${fails}) fail=1 ;; *) fail=0 ;; esac`,
    'mkdir -p "$run/reports"',
    'if [ "$fail" = 1 ]; then',
    "cat > \"$run/reports/bench.json\" <<'JSON'",
    failJson,
    'JSON',
    "cat <<'OUT'",
    failOut,
    'OUT',
    'exit 1',
    'else',
    "cat > \"$run/reports/bench.json\" <<'JSON'",
    passJson,
    'JSON',
    "cat <<'OUT'",
    passOut,
    'OUT',
    'exit 0',
    'fi',
  ].join('\n')
  return ['sh', '-c', script, 'sh', '${RUN}']
}

const installStep = { id: 'install', kind: 'setup', nameZh: '安装依赖', cmd: ['true'] }
const benchStep = (failLabels) => ({
  id: 'bench', nameZh: '性能基准', kind: 'product', optional: true, trialOnly: true,
  covers: ['performance'], cache: false, cmd: benchCmd(failLabels),
  testReport: { format: 'vitest-json', path: '${RUN}/reports/bench.json' }, timeoutMin: 15,
})
const profileOf = (failLabels) => ({ sandbox: false, steps: [installStep, benchStep(failLabels)] })

function fixture(number) {
  const sourceFile = [5204, 5205, 5206, 5207].includes(number) ? 'src/persist/value.mjs' : 'src/persist/persistFlush.ts'
  const repo = `fixture/pr${number}`
  const src = join(root, `source-${number}`)
  mkdirSync(src)
  const git = (...a) => execFileSync('git', ['-C', src, '-c', 'user.name=F', '-c', 'user.email=f@example.invalid', ...a], { encoding: 'utf8' }).trim()
  git('init', '-q', '-b', 'main')
  mkdirSync(join(src, 'src/persist'), { recursive: true })
  writeFileSync(join(src, sourceFile), 'export const v = 1\n')
  if (number === 5207) writeFileSync(join(src, 'src/persist/other.mjs'), 'export const other = 1\n')
  if (number === 5206) writeFileSync(join(src, 'regression.test.mjs'), "import { test } from 'node:test'; import assert from 'node:assert/strict'; import { v } from './src/persist/value.mjs'; test('regression', () => assert.equal(v, 1)); test('old failure', () => assert.fail('pre-existing'));\n")
  git('add', '.'); git('commit', '-qm', 'base')
  const baseSha = git('rev-parse', 'HEAD')
  writeFileSync(join(src, sourceFile), 'export const v = 2\n')
  if (number === 5207) writeFileSync(join(src, 'src/persist/other.mjs'), 'export const other = 2\n')
  git('commit', '-qam', `code (#${number})`)
  const mergeSha = git('rev-parse', 'HEAD')
  mkdirSync(MIRRORS_DIR, { recursive: true })
  execFileSync('git', ['clone', '-q', '--bare', src, join(MIRRORS_DIR, `fixture__pr${number}.git`)])
  const pr = {
    number, title: 'perf(persist): fixture', html_url: 'https://example.invalid', user: { login: 'f' },
    base: { ref: 'main', sha: baseSha }, head: { sha: 'f'.repeat(40) },
    merged_at: new Date().toISOString(), merge_commit_sha: mergeSha, state: 'closed',
  }
  const stateFile = join(root, `github-fixture-${number}.json`)
  writeFileSync(stateFile, JSON.stringify({ pr, baseSha, files: [{ filename: sourceFile, additions: 1, deletions: 1 }, ...(number === 5207 ? [{ filename: 'src/persist/other.mjs', additions: 1, deletions: 1 }] : [])], calls: [] }))
  return { repo, stateFile }
}

const health = () => ({ status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], notes: [] })
const brief = (e) => JSON.stringify({
  check_id: e?.check_id, test: e?.test, status: e?.status, severity: e?.severity,
  first_run: e?.evidence?.first_run, rerun: e?.evidence?.rerun, base_run: e?.evidence?.base_run,
  pr_runs: e?.evidence?.pr_runs, base_runs: e?.evidence?.base_runs, attribution: e?.attribution,
})
const evLabels = (evDir) => {
  const top = readdirSync(evDir).filter((n) => !n.startsWith('.'))
  const nested = top.length === 1 ? join(evDir, top[0]) : evDir
  try { return readdirSync(nested).filter((n) => !n.startsWith('.')).sort() }
  catch { return top.sort() }
}

const origIdle = { sample: N.idleHooks.sample, wait: N.idleHooks.wait }
N.idleHooks.sample = () => ({ loadavg: [0.1, 0.1, 0.1], cores: 8, mem_available_gb: 10, at: new Date().toISOString() })
N.idleHooks.wait = async () => {}
after(() => { N.idleHooks.sample = origIdle.sample; N.idleHooks.wait = origIdle.wait })

async function runPr({ number, failLabels, profile }) {
  const { repo, stateFile } = fixture(number)
  process.env.ARGUS_E2E_GH_STATE = stateFile
  const evDir = join(root, `ev-${number}`)
  const out = await N.processPr({
    repo, project: { repo, profileData: profile ?? profileOf(failLabels) }, number, discovery: 'merged',
    secrets: {}, db: null, health: health(), evidenceRoot: evDir,
    opts: { noReview: true, noAiTests: true, reuseMerged: false },
  })
  return { out, evDir }
}

describe('processPr：试运行检查的 3+3 引入门槛', { concurrency: 1 }, () => {
  test('PR 3 次全失败且 base 3 次全通过：报告为 introduced，并实际跑了 h3/b2/b3', async () => {
    const { out, evDir } = await runPr({ number: 5201, failLabels: ['h1', 'h2', 'h3'] })
    const entry = (out.failed_checks ?? []).find((c) => c.check_id === 'bench')
    const dump = brief(entry)
    assert.equal(entry?.status, 'introduced', dump)
    assert.equal(entry?.severity, 'P2', dump)
    assert.deepEqual(entry?.evidence?.pr_runs, ['fail', 'fail', 'fail'], dump)
    assert.deepEqual(entry?.evidence?.base_runs, ['pass', 'pass', 'pass'], dump)
    assert.equal(out.outcome, 'defect', `outcome=${out.outcome} reason=${out.outcome_reason}`)
    const labels = evLabels(evDir)
    for (const need of ['h1', 'h2', 'h3', 'b', 'b2', 'b3']) {
      assert.ok(labels.includes(need), `应留下 ${need} 轮证据，实际=${labels.join(',')}`)
    }
    const graded = (out.findings ?? []).filter((f) => f.check_id === 'bench')
    assert.equal(graded.length, 1, `introduced 应进 findings：${JSON.stringify(graded.map(brief))}`)
    assert.equal(graded[0].decision_status, 'confirmed')
    assert.equal(graded[0].fix_eligible, true)
    const findingFile = join(graded[0].evidence_root, 'fixture-finding.json')
    writeFileSync(findingFile, JSON.stringify(graded[0]))
    // Consumer contract: the real nightly artifacts must satisfy Pi's admission.
    const consumer = fileURLToPath(new URL('../../pi/bin', import.meta.url))
    execFileSync('python3', ['-c', 'import sys,json;sys.path.insert(0,sys.argv[1]);import lib;lib.read_finding_evidence(json.load(open(sys.argv[2])))', consumer, findingFile])
  })

  test('PR 第 3 次通过：报告为 unverified，不再跑多余的 base 轮', async () => {
    const { out, evDir } = await runPr({ number: 5202, failLabels: ['h1', 'h2'] })
    const entry = (out.failed_checks ?? []).find((c) => c.check_id === 'bench')
    const dump = brief(entry)
    assert.equal(entry?.status, 'unverified', dump)
    assert.equal(entry?.severity, null, dump)
    assert.deepEqual(entry?.evidence?.pr_runs, ['fail', 'fail', 'pass'], dump)
    assert.equal(out.outcome, 'needs_review', `outcome=${out.outcome} reason=${out.outcome_reason}`)
    const labels = evLabels(evDir)
    assert.ok(labels.includes('h3'), `第 3 次 PR 轮应发生，实际=${labels.join(',')}`)
    assert.ok(!labels.includes('b2') && !labels.includes('b3'), `第 3 次 PR 已通过就不应再跑 b2/b3，实际=${labels.join(',')}`)
    assert.deepEqual((out.findings ?? []).filter((f) => f.check_id === 'bench'), [], 'unverified 不进 findings')
    const pending = (out.pending_verification ?? []).filter((p) => p.check_id === 'bench')
    assert.equal(pending.length, 1, `应进待核实：${JSON.stringify((out.pending_verification ?? []).map((p) => p.check_id))}`)
  })

  test('base 第 3 次失败：报告为 unverified', async () => {
    const { out, evDir } = await runPr({ number: 5203, failLabels: ['h1', 'h2', 'h3', 'b3'] })
    const entry = (out.failed_checks ?? []).find((c) => c.check_id === 'bench')
    const dump = brief(entry)
    assert.equal(entry?.status, 'unverified', dump)
    assert.equal(entry?.severity, null, dump)
    assert.deepEqual(entry?.evidence?.pr_runs, ['fail', 'fail', 'fail'], dump)
    assert.deepEqual(entry?.evidence?.base_runs, ['pass', 'pass', 'fail'], dump)
    assert.equal(out.outcome, 'needs_review', `outcome=${out.outcome} reason=${out.outcome_reason}`)
    const labels = evLabels(evDir)
    for (const need of ['h1', 'h2', 'h3', 'b', 'b2', 'b3']) {
      assert.ok(labels.includes(need), `应留下 ${need} 轮证据，实际=${labels.join(',')}`)
    }
  })
})

test('processPr: independent cov round contributes actual V8 changed-line evidence', async () => {
  const profile = { sandbox: false, steps: [installStep,
    { id: 'cov-trigger', nameZh: 'coverage trigger', kind: 'product', cmd: ['true'], coverage: { kind: 'istanbul' }, exercises: ['other/**'] },
    { id: 'node-test', nameZh: 'real source import', kind: 'product', cmd: [process.execPath, '-e', 'await import("./src/persist/value.mjs")'],
      exercises: ['src/**'], covers: ['unit'], coverage: { kind: 'v8' } }] }
  const { out } = await runPr({ number: 5204, profile })
  const file = out.coverage.changed_source.files.find((f) => f.file === 'src/persist/value.mjs')
  assert.ok(file, JSON.stringify(out.coverage.changed_source))
  assert.deepEqual(file.loaded_by, ['node-test'])
  assert.equal(file.lines.changed[0].status, 'pass')
  assert.deepEqual(file.lines.changed[0].covered_by, ['node-test'])
  assert.equal(out.findings.length, 0, 'coverage run is never a product failure')
})


test('processPr: named regression remains admitted when base has an unrelated old failure', async () => {
  const profile = { sandbox: false, steps: [installStep,
    { id: 'unit', nameZh: 'named checks', kind: 'product', covers: ['unit'], cache: false,
      cmd: [process.execPath, '--test', '--test-reporter=spec', '--test-reporter=${NODE_TEST_REPORTER}',
        '--test-reporter-destination=stdout', '--test-reporter-destination=${TEST_REPORT}', 'regression.test.mjs'],
      testReport: { format: 'node-events', path: '${RUN}/reports/unit.jsonl' } }] }
  const { out } = await runPr({ number: 5206, profile })
  const finding = out.findings.find((f) => f.test === 'regression')
  assert.ok(finding, JSON.stringify({ checks: out.failed_checks, pending: out.pending_verification }))
  assert.equal(finding.decision.status, 'confirmed')
  assert.equal(finding.evidence.base_run, 'pass')
  assert.deepEqual([finding.finding_eligible, finding.issue_eligible, finding.fix_eligible], [true, true, true])
  assert.ok(!out.findings.some((f) => f.test === 'old failure'))
  assert.ok(!out.pending_verification.some((f) => f.test === 'regression'))
  const file = join(finding.evidence_root, 'mixed-base-finding.json')
  writeFileSync(file, JSON.stringify(finding))
  const consumer = fileURLToPath(new URL('../../pi/bin', import.meta.url))
  execFileSync('python3', ['-c', `import sys,json,pathlib
sys.path.insert(0,sys.argv[1]);import lib
f=json.load(open(sys.argv[2]));m=lib.read_finding_evidence(f)
results=[json.load(open(pathlib.Path(f['evidence_root'])/a['path'])) for a in m['artifacts']]
base=next(r for r in results if r['phase']=='base' and r['check_id']=='unit')
assert base['status']=='fail' and base['passed_cases']
`, consumer, file])
})

test('processPr: unavailable cov round preserves valid head coverage for the same step', async () => {
  const profile = { sandbox: false, steps: [installStep,
    { id: 'cov-trigger', nameZh: 'coverage trigger', kind: 'product', cmd: ['true'], coverage: { kind: 'istanbul' }, exercises: ['other/**'] },
    { id: 'node-test', nameZh: 'head-only source import', kind: 'product',
      cmd: [process.execPath, '-e', 'if (!process.cwd().includes("-cov-")) await import("./src/persist/value.mjs")'],
      exercises: ['src/**'], covers: ['unit'], coverage: { kind: 'v8', labels: ['h1', 'cov'] } }] }
  const { out } = await runPr({ number: 5205, profile })
  const file = out.coverage.changed_source.files.find((f) => f.file === 'src/persist/value.mjs')
  assert.ok(file, JSON.stringify(out.coverage.changed_source))
  assert.deepEqual(file.loaded_by, ['node-test'])
  assert.equal(file.lines.changed[0].status, 'pass')
  assert.deepEqual(file.lines.changed[0].covered_by, ['node-test'])
  assert.equal(out.findings.length, 0)
})


test('processPr: cov measuring another file preserves head target evidence and both step origins', async () => {
  const profile = { sandbox: false, steps: [installStep,
    { id: 'cov-trigger', nameZh: 'coverage trigger', kind: 'product', cmd: ['true'], coverage: { kind: 'istanbul' }, exercises: ['other/**'] },
    { id: 'node-test', nameZh: 'different sources per round', kind: 'product',
      cmd: [process.execPath, '-e', 'await import(process.cwd().includes("-cov-") ? "./src/persist/other.mjs" : "./src/persist/value.mjs")'],
      exercises: ['src/**'], covers: ['unit'], coverage: { kind: 'v8', labels: ['h1', 'cov'] } }] }
  const { out } = await runPr({ number: 5207, profile })
  for (const path of ['src/persist/value.mjs', 'src/persist/other.mjs']) {
    const file = out.coverage.changed_source.files.find((f) => f.file === path)
    assert.ok(file, JSON.stringify(out.coverage.changed_source))
    assert.deepEqual(file.loaded_by, ['node-test'], path)
    assert.equal(file.lines.changed[0].status, 'pass', path)
    assert.deepEqual(file.lines.changed[0].covered_by, ['node-test'], path)
    assert.deepEqual(file.measurement_gaps, [], path)
    assert.ok(file.sources.every((s) => s.step === 'node-test'), path)
  }
  assert.equal(out.coverage.changed_source.sources.find((s) => s.step === 'node-test').status, 'ok')
  assert.equal(out.findings.length, 0)
})
