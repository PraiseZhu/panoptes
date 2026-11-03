// Praise PR step 9（2026-11-03）：QA 侧把失败归到错误 PR（kiro-p1-1102/VERDICT.md：#1384 #1433 #1456 三条 code-review P1 + #1433 AI 补测 P2）。
// 每项一条：旧代码失败、新代码通过；回滚开关 $QA_HOME/qa-misattrib-off 存在时回到旧行为。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = mkdtempSync(join(tmpdir(), 'argus-misattrib-'))
process.env.ARGUS_ROOT = root
process.env.QA_HOME = join(root, 'qa-home')
delete process.env.PANOPTES_QA_MISATTRIB_OFF
process.env.ARGUS_GH_BIN = join(here, 'fixtures/argus-fake-gh.mjs')
process.env.ARGUS_GH_RETRY_MS = '1'
process.env.ARGUS_E2E_GH_STATE = join(root, 'github-fixture.json')
mkdirSync(process.env.QA_HOME)
after(() => rmSync(root, { recursive: true, force: true }))
const OFF = join(process.env.QA_HOME, 'qa-misattrib-off')
const withOff = async (fn) => { writeFileSync(OFF, ''); try { return await fn() } finally { rmSync(OFF, { force: true }) } }

const N = await import('../src/nightly.mjs')
const M = await import('../src/mingqa.mjs')
const R = await import('../src/runner.mjs')
const { memoryQuota } = await import('../src/quota.mjs')

// #1384 h1 的 report.browser.json（原样）
const CHECKS_1384 = [
  { id: 'browser-host-probe', status: 'unavailable', reason: 'Error: real MivoCanvas pan target\n    at waitFor (http://127.0.0.1:33697/scripts/e2e/bug-doctor-images/browser-harness.ts:42:8)' },
  { id: 'browser-product-roundtrip', status: 'fail', reason: 'product persist/export roundtrip did not complete' },
  { id: 'browser-not-cindy', status: 'pass', reason: 'used a worktree-local Chrome user-data-dir' },
  { id: 'candidate-snapshot-stable', status: 'pass', reason: 'candidate files unchanged during layer run' },
]
const browserStep = { id: 'e2e-browser', nameZh: '浏览器端到端', kind: 'product', cmd: ['x'], failureReport: { format: 'checks', path: '${RUN}/report.browser.json' } }
function browserResult() {
  const runDir = mkdtempSync(join(root, 'run-'))
  writeFileSync(join(runDir, 'report.browser.json'), JSON.stringify({ status: 'fail', checks: CHECKS_1384 }))
  return R.toStepOutcome(browserStep, { id: 'e2e-browser', nameZh: '浏览器端到端', kind: 'product', ok: false, exitCode: 1, tail: '' }, runDir)
}
const check1384 = { check_id: 'e2e-browser', check_name: '浏览器端到端', test: 'browser-product-roundtrip', failure_type: 'runner_error', status: 'unverified', pre_existing: false, test_script_failure: true, severity: null,
  fingerprint: 'mq1-bb183524fddd60dc2ded', evidence: { tested_sha: 'd'.repeat(40), first_run: 'fail', rerun: 'fail', base_run: 'pass' } }

// ── 1. 送审查的内容带失败原因和基线结果（mingqa.mjs checkSummary）──
test('1. review prompt: FAIL line carries the failing reason, rerun/base result and the test-script flag', () => {
  const r = browserResult()
  assert.equal(r.checkReasons?.[0]?.reason, 'Error: real MivoCanvas pan target', 'runner keeps the first line of each non-pass check reason')
  const s = M.checkSummary([r], [check1384])
  assert.match(s, /FAIL — browser-product-roundtrip/)
  assert.match(s, /reason: browser-host-probe unavailable: Error: real MivoCanvas pan target/)
  assert.match(s, /QA attribution \(browser-product-roundtrip\): unverified; rerun=fail, base=pass; test script error: the product steps never ran/)
  assert.match(s, /not evidence against this diff/)
  const old = M.checkSummary([r], [check1384], { off: true })
  assert.equal(old, '- e2e-browser (浏览器端到端): FAIL — browser-product-roundtrip', 'off = the old single line')
})

test('1. runCodeReview hands the failed checks to codeReview', async () => {
  let seen = null
  const out = { quota_capped: [], findings: [], pending_verification: [], checks: [], failed_checks: [check1384] }
  await N.runCodeReview({ out, repo: 'o/r', pr: { number: 1384, body: '' }, files: [], prDiff: { files: [] }, sha: 'a'.repeat(40), baseSha: 'b'.repeat(40), results: [], profile: {}, evDir: mkdtempSync(join(root, 'ev-')), secrets: {},
    health: { notes: [], llm_errors: [], qa_tool_failures: [], env_problems: [] }, number: 1384, quota: memoryQuota({ pool: 'test' }), opts: { freshReview: true },
    deps: { codeReview: async (a) => { seen = a; return { status: 'error', error: { kind: 'x' } } } } })
  assert.deepEqual(seen.failedChecks, [check1384])
})

// ── 2. review 要补证据的项并进对应检查，不单开「P1 待核实」──
const needsEvidence = (title, file = null) => ({ kind: 'needs-evidence', title, file, line: file ? 74 : null, severity_if_confirmed: 'P1', reason: '只有失败用例名，缺断言和日志' })
async function review(unverified, failedChecks) {
  const out = { quota_capped: [], findings: [], pending_verification: [], checks: [], failed_checks: structuredClone(failedChecks) }
  const doc = { status: 'passed', summary: 's', findings: [], unverified, residualRisks: [] }
  await N.runCodeReview({ out, repo: 'o/r', pr: { number: 1, body: '' }, files: [], prDiff: { files: [] }, sha: 'a'.repeat(40), baseSha: 'b'.repeat(40), results: [], profile: {}, evDir: mkdtempSync(join(root, 'ev-')), secrets: {},
    health: { notes: [], llm_errors: [], qa_tool_failures: [], env_problems: [] }, number: 1, quota: memoryQuota({ pool: 'test' }), opts: { freshReview: true },
    deps: { codeReview: async () => ({ status: 'ok', model: 'm', tokens: 1, doc, gate: null, reviewPath: '/x' }) } })
  return out
}
test('2. needs-evidence items naming a failed check fold into that check (#1384 / #1433 / #1456 titles)', async () => {
  const items = [needsEvidence('浏览器端 browser-product-roundtrip 失败原因尚无法归因'), needsEvidence('browser-product-roundtrip 端到端检查失败是否由本 PR 引起'), needsEvidence('浏览器端到端 roundtrip 失败是否与移除根 Vite 插件有关', 'vite.config.ts'), needsEvidence('unrelated: cache key misses locale', 'src/a.ts')]
  const out = await review(items, [check1384])
  const pending = out.pending_verification.filter((p) => p.source === 'code-review')
  assert.deepEqual(pending.map((p) => p.title), ['unrelated: cache key misses locale'], 'only the item that does not name a failed check stays a code-review pending entry')
  assert.equal(out.failed_checks[0].review_requests.length, 3)
  assert.equal(out.failed_checks[0].review_requests[2].file, 'vite.config.ts')
  assert.equal(out.code_review.folded_into_checks, 3)
  assert.equal(out.code_review.pending_count, 1)
  const old = await withOff(() => review(items, [check1384]))
  assert.equal(old.pending_verification.filter((p) => p.source === 'code-review').length, 4, 'off = every item is its own pending entry')
})
test('2. matchFailedCheck: only needs-evidence, and short check ids (unit) do not match plain words', () => {
  assert.equal(N.matchFailedCheck({ kind: 'design-question', title: 'browser-product-roundtrip' }, [check1384]), null)
  assert.equal(N.matchFailedCheck({ kind: 'needs-evidence', title: 'unit of work is unclear' }, [{ check_id: 'unit', check_name: '单元测试', test: null }]), null)
  assert.equal(N.matchFailedCheck({ kind: 'needs-evidence', title: 'e2e-browser failed' }, [{ check_id: 'e2e-browser', check_name: '浏览器端到端', test: null }])?.check_id, 'e2e-browser')
})

// ── 3. 测试脚本自身出错单独标记、按最早出现的提交归组；runner_error 写明产品步骤没跑 ──
test('3. runner_error + unverified: flagged test_script_failure, attribution says the product steps never ran (processPr, #1384 shape)', async () => {
  const { MIRRORS_DIR } = await import('../src/paths.mjs')
  const run = async (number) => {
    const repo = `fixture/pr${number}`
    const src = join(root, `source-${number}`)
    mkdirSync(src)
    const git = (...args) => spawnSync('git', ['-C', src, '-c', 'user.name=F', '-c', 'user.email=f@example.invalid', ...args], { encoding: 'utf8' }).stdout.trim()
    git('init', '-q', '-b', 'main'); writeFileSync(join(src, 'v.mjs'), '1\n'); git('add', '.'); git('commit', '-qm', 'base')
    const baseSha = git('rev-parse', 'HEAD')
    writeFileSync(join(src, 'v.mjs'), '2\n'); git('commit', '-qam', 'code')
    const mergeSha = git('rev-parse', 'HEAD')
    mkdirSync(MIRRORS_DIR, { recursive: true })
    spawnSync('git', ['clone', '-q', '--bare', src, join(MIRRORS_DIR, `fixture__pr${number}.git`)])
    const pr = { number, title: 'code', html_url: 'https://example.invalid', user: { login: 'f' }, base: { ref: 'main', sha: baseSha }, head: { sha: 'f'.repeat(40) }, merged_at: new Date().toISOString(), merge_commit_sha: mergeSha, state: 'closed' }
    writeFileSync(process.env.ARGUS_E2E_GH_STATE, JSON.stringify({ pr, baseSha, files: [{ filename: 'v.mjs', additions: 1, deletions: 1 }], calls: [] }))
    // 首跑 / 重跑写 #1384 的失败报告，合并前版本（b）通过
    const report = JSON.stringify({ status: 'fail', checks: CHECKS_1384 })
    const pass = JSON.stringify({ status: 'pass', checks: CHECKS_1384.map((c) => ({ ...c, status: 'pass' })) })
    const cmd = ['sh', '-c', `mkdir -p "$1/e2e"; case "$(basename "$1")" in *-b-*) printf '%s' '${pass}' > "$1/e2e/report.browser.json"; exit 0;; *) printf '%s' '${report}' > "$1/e2e/report.browser.json"; exit 1;; esac`, 'sh', '${RUN}']
    const profile = { sandbox: false, steps: [{ id: 'install', kind: 'setup', nameZh: '安装依赖', cmd: ['true'] },
      { id: 'e2e-browser', nameZh: '浏览器端到端', kind: 'product', optional: true, covers: ['e2e'], cmd, failureReport: { format: 'checks', path: '${RUN}/e2e/report.browser.json' }, timeoutMin: 5 }] }
    return N.processPr({ repo, project: { repo, profileData: profile }, number, discovery: 'merged', secrets: {}, db: null, health: { status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], notes: [] },
      evidenceRoot: join(root, `ev-${number}`), opts: { noReview: true, reuseMerged: false } })
  }
  const out = await run(4101)
  const c = out.failed_checks.find((x) => x.check_id === 'e2e-browser')
  assert.equal(c.status, 'unverified')
  assert.equal(c.evidence.base_run, 'pass')
  assert.equal(c.test_script_failure, true)
  assert.match(c.attribution, /product steps never executed/)
  assert.equal(c.evidence.check_reasons[0].id, 'browser-host-probe')
  const p = out.pending_verification.find((x) => x.check_id === 'e2e-browser')
  assert.equal(p.pending_kind, 'check-qa')
  assert.match(p.pending_reason_zh, /测试脚本自身出错，产品步骤没跑/)
  const old = await withOff(() => run(4102))
  const oc = old.failed_checks.find((x) => x.check_id === 'e2e-browser')
  assert.equal(oc.test_script_failure, undefined)
  assert.equal(oc.attribution, 'rerun result incomplete or failure type not comparable')
})

test('3. groupTestScriptFailures: one group per fingerprint, first_seen = the ancestor commit (old reports without the flag count too)', async () => {
  const { groupTestScriptFailures } = await import('../src/test-script-failures.mjs')
  const legacy = { ...check1384, test_script_failure: undefined }
  const pr = (number, sha, tested_at, c = legacy) => ({ number, tested_at, failed_checks: [{ ...c, evidence: { ...c.evidence, tested_sha: sha } }] })
  const order = ['a1384', 'b1433', 'c1456']   // a1384 是另外两个的祖先
  const isAncestor = (x, y) => order.indexOf(x) <= order.indexOf(y)
  const groups = groupTestScriptFailures([{ prs: [pr(1456, 'c1456', '2026-10-30T10:00'), pr(1433, 'b1433', '2026-10-30T09:00')] }, { prs: [pr(1384, 'a1384', '2026-10-31T00:00'), pr(1500, 'x', '2026-10-31', { ...legacy, status: 'introduced' })] }], { isAncestor })
  assert.equal(groups.length, 1)
  assert.deepEqual(groups[0].first_seen, { pr: 1384, sha: 'a1384' }, 'ancestry wins over test time (#1384 was re-tested later than #1433)')
  assert.deepEqual(groups[0].prs.sort(), [1384, 1433, 1456])
})

// ── 4. 沙箱浏览器强制 reduced-motion ──
test('4. sandbox steps get CHROMIUM_FLAGS --force-prefers-reduced-motion (kept with a profile value); off = no flag', async () => {
  const dir = mkdtempSync(join(root, 'env-'))
  assert.equal(R.stepEnv(dir, {}).CHROMIUM_FLAGS, '--force-prefers-reduced-motion')
  assert.equal(R.stepEnv(dir, { env: { CHROMIUM_FLAGS: '--foo' } }).CHROMIUM_FLAGS, '--foo --force-prefers-reduced-motion')
  assert.equal(N.envSnapshot().browser_reduced_motion, true)
  await withOff(() => {
    assert.equal(R.stepEnv(dir, {}).CHROMIUM_FLAGS, undefined)
    assert.equal(N.envSnapshot().browser_reduced_motion, false)
  })
})

// ── 5. AI 补测：先核对植入的错误代码在源码里，再跑测试（#1433 mq1-73738e6f）──
const RUNNER = join(here, '..', 'src', 'ai-test-run.mjs')
function aiRun(spec) {
  const dir = mkdtempSync(join(root, 'aiproj-'))
  writeFileSync(join(dir, 'package.json'), JSON.stringify({ type: 'module' }))
  writeFileSync(join(dir, 'Section.mjs'), "export const attrs = (selected) => ({ 'aria-pressed': selected })\n")
  const specPath = join(dir, '..', `spec-${Math.random()}.json`)
  // 补测断言 aria-checked（死组件的属性），活组件只有 aria-pressed：当前代码上必然失败，植入点也对不上
  const content = "import test from 'node:test'\nimport assert from 'node:assert/strict'\nimport { attrs } from './Section.mjs'\ntest('checked', () => assert.equal(attrs(true)['aria-checked'], true))\n"
  writeFileSync(specPath, JSON.stringify({ runCmd: ['node', '--test', '{file}'], purposeZh: '选中态', filePath: 'section.argus.test.mjs', content, mutation: { file: 'Section.mjs', find: 'aria-checked={selected}', replace: 'aria-checked={!selected}' }, ...spec }))
  const { NODE_TEST_CONTEXT, ...env } = process.env
  return spawnSync(process.execPath, [RUNNER, specPath], { cwd: dir, env, encoding: 'utf8', timeout: 60_000 })
}
test('5. mutation that does not match the source → exit 4 (discarded) before the test runs, not exit 1 "fails on current code"', () => {
  const r = aiRun({})
  assert.equal(r.status, 4, r.stdout)
  assert.match(r.stdout, /植入错误对不上源码/)
  assert.doesNotMatch(r.stdout, /✖/)
  const old = aiRun({ legacyOrder: true })
  assert.equal(old.status, 1, 'legacyOrder (rollback) = old order: runs first, exit 1')
})
test('5. runner writes legacyOrder into the AI test spec only when the rollback flag is on', async () => {
  const step = { id: 'ai-test-1', kind: 'product', cmd: ['node'], aiGenerated: true, aiTest: { purposeZh: 'x', mutation: { file: 'a', find: 'b', replace: 'c' } } }
  const { readFileSync } = await import('node:fs')
  const specOf = (runDir) => JSON.parse(readFileSync(join(runDir, 'ai-test-1.json'), 'utf8'))
  const a = mkdtempSync(join(root, 'prep-')); R.prepareTool(step, a, {})
  assert.equal(specOf(a).legacyOrder, undefined)
  const b = mkdtempSync(join(root, 'prep-')); await withOff(() => R.prepareTool(step, b, {}))
  assert.equal(specOf(b).legacyOrder, true)
})
