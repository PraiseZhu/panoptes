// 2026-10-29 事故签名（3 次 / 7 天，PR #1391 #1392 #1398）：e2e-browser「browser-product-roundtrip」被判成
// pre-existing（severity P3、pre_existing=true、flaky=false），报告写「在基线版本上也失败」。
// pi/bin/infrafix.py:251 的 check 规则只读 pre_existing + flaky（消息在该文件 :253 生成），于是每 7 天把它当成
// 「测试工具（探针 / 用例）有问题，或主线本身坏了」再报一次。
//
// 证据（/workspace/mivo-qa/reports/catch-up/PR-1398.json / PR-1392.json / PR-1391.json）：三份报告的 h1 / h2 / b
// 三轮 report.browser.json 都是
//   browser-host-probe: unavailable（waitFor 等真实画布目标超时）→ failureParseStatus=incomplete
//   browser-product-roundtrip: fail, reason="product persist/export roundtrip did not complete" → runner_error
// compareKey 只认 assertion / test_error（decide.mjs:30 confirmable、:70 同一失败才返回 'same'），runner_error /
// 不完整的报告对照永远是 unknown，classifyKey 因此给 unverified（「对照结果无法比较」）；nightly.mjs:466-470
// 随后用基线轮的粗结果 'fail' 把它改写成 pre-existing P3 并置 pre_existing=true。PR #1398 更直接：同一提交的 h2
// 真的通过了（evidence.rerun="pass"），报告仍写 pre-existing、flaky:false、attribution「also fails on the base revision」。
//
// 约定：对照无法比较的失败不是「老问题」——decide.mjs 开头「超时、不可用、缺失的对照不构成失败重现证据」，
// docs/report-schema.md「unverified → 无级别，待核实（QA 自查）」、findings 只放核实过的 P0–P3。
// 所以这里断言：不得宣称基线也失败（pre_existing / status pre-existing / 「also fails on the base revision」）、
// 不得进 findings 定级，应作为 check-qa 待核实交给 QA 自己查。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = mkdtempSync(join(tmpdir(), 'argus-base-comparable-'))
process.env.ARGUS_ROOT = root
process.env.ARGUS_GH_BIN = join(here, 'fixtures/argus-fake-gh.mjs')
process.env.ARGUS_GH_RETRY_MS = '1'
process.env.ARGUS_E2E_GH_STATE = join(root, 'github-fixture.json')
after(() => rmSync(root, { recursive: true, force: true }))

const N = await import('../src/nightly.mjs')
const { MIRRORS_DIR } = await import('../src/paths.mjs')

const health = () => ({ status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], notes: [] })

// 真实 report.browser.json 的四个 check，原样照抄（PR-1398 h1）。
const PASS_CHECKS = [
  { id: 'browser-host-probe', status: 'pass', reason: 'real MivoCanvas zoom target found' },
  { id: 'browser-product-roundtrip', status: 'pass', reason: 'product persisted canvas survived reopen and export' },
  { id: 'browser-not-cindy', status: 'pass', reason: 'used a worktree-local Chrome user-data-dir, not a Cindy profile' },
  { id: 'candidate-snapshot-stable', status: 'pass', reason: 'candidate files unchanged during layer run' },
]
const FAIL_CHECKS = [
  { id: 'browser-host-probe', status: 'unavailable', reason: 'Error: real MivoCanvas zoom target\n    at waitFor (http://127.0.0.1:46179/scripts/e2e/bug-doctor-images/browser-harness.ts:42:8)' },
  { id: 'browser-product-roundtrip', status: 'fail', reason: 'product persist/export roundtrip did not complete' },
  { id: 'browser-not-cindy', status: 'pass', reason: 'used a worktree-local Chrome user-data-dir, not a Cindy profile' },
  { id: 'candidate-snapshot-stable', status: 'pass', reason: 'candidate files unchanged during layer run' },
]
const browserReport = (checks) => JSON.stringify({ schemaVersion: 1, layer: 'browser', status: checks.some((c) => c.status === 'fail') ? 'fail' : 'pass', checks }, null, 2)

const writeReport = (checks) => ['    cat > "$run/e2e-browser/report.browser.json" <<\'JSON\'', browserReport(checks), 'JSON']

// 这一步按 run 目录名里的 label 决定这一轮的结果：h1 首跑、h2 同一提交重跑、b 合并前版本（runner.mjs newRunDir）。
// h2Passing=false 时三轮都复现同一份失败报告（PR #1391 #1392 的形态）；true 时 h2 通过（PR #1398 的形态）。
function e2eBrowserCmd(h2Passing) {
  const h2 = h2Passing ? PASS_CHECKS : FAIL_CHECKS
  const lines = [
    'run="$1"',
    'mkdir -p "$run/e2e-browser"',
    'case "$(basename "$run")" in',
    '  *-h2-*)',
    ...writeReport(h2),
    h2Passing ? '    echo \'{"status":"pass"}\'' : '    echo \'{"status":"fail"}\'',
    h2Passing ? '    exit 0 ;;' : '    exit 1 ;;',
    '  *)',
    ...writeReport(FAIL_CHECKS),
    '    echo \'{"status":"fail"}\'',
    '    exit 1 ;;',
    'esac',
  ]
  return ['sh', '-c', lines.join('\n'), 'sh', '${RUN}']
}

// 与 argus/config/profiles/mivo-canvas-plugin.json 的 e2e-browser 步骤同形：把 report.browser.json 当失败报告读。
const profile = (h2Passing) => ({
  sandbox: false,
  steps: [
    { id: 'install', kind: 'setup', nameZh: '安装依赖', cmd: ['true'] },
    {
      id: 'e2e-browser', nameZh: '浏览器端到端', kind: 'product', optional: true,
      covers: ['e2e', 'ui', 'data', 'sideEffects', 'visual'],
      cmd: e2eBrowserCmd(h2Passing),
      failureReport: { format: 'checks', path: '${RUN}/e2e-browser/report.browser.json' },
      timeoutMin: 10,
    },
  ],
})

function fixture(number) {
  const repo = `fixture/pr${number}`
  const src = join(root, `source-${number}`)
  mkdirSync(src)
  const git = (...args) => execFileSync('git', ['-C', src, '-c', 'user.name=F', '-c', 'user.email=f@example.invalid', ...args], { encoding: 'utf8' }).trim()
  git('init', '-q', '-b', 'main')
  writeFileSync(join(src, 'value.mjs'), 'export const v = 1\n')
  git('add', '.'); git('commit', '-qm', 'base')
  const baseSha = git('rev-parse', 'HEAD')
  writeFileSync(join(src, 'value.mjs'), 'export const v = 2\n'); git('commit', '-qam', `code (#${number})`)
  const mergeSha = git('rev-parse', 'HEAD')
  mkdirSync(MIRRORS_DIR, { recursive: true })
  execFileSync('git', ['clone', '-q', '--bare', src, join(MIRRORS_DIR, `fixture__pr${number}.git`)])
  const pr = { number, title: 'code', html_url: 'https://example.invalid', user: { login: 'f' }, base: { ref: 'main', sha: baseSha }, head: { sha: 'f'.repeat(40) }, merged_at: new Date().toISOString(), merge_commit_sha: mergeSha, state: 'closed' }
  writeFileSync(process.env.ARGUS_E2E_GH_STATE, JSON.stringify({ pr, baseSha, files: [{ filename: 'value.mjs', additions: 1, deletions: 1 }], calls: [] }))
  return { repo }
}

async function runPr({ number, h2Passing }) {
  const { repo } = fixture(number)
  const h = health()
  const out = await N.processPr({
    repo, project: { repo, profileData: profile(h2Passing) }, number, discovery: 'merged',
    secrets: {}, db: null, health: h, evidenceRoot: join(root, `ev-${number}`), opts: { noReview: true, reuseMerged: false },
  })
  return { out, h }
}

// 断言消息里只放定位用的字段：整条 entry 带 command / log_tail，打出来读不了。
const brief = (e) => JSON.stringify({ check_id: e?.check_id, test: e?.test, status: e?.status, severity: e?.severity, pre_existing: e?.pre_existing, flaky: e?.flaky, failure_type: e?.failure_type, attribution: e?.attribution, first_run: e?.evidence?.first_run, rerun: e?.evidence?.rerun, base_run: e?.evidence?.base_run })

// 基线轮的失败类型与首跑对不上（runner_error / report 不完整 → compareKey=unknown）时，panoptes 不能把首跑的失败
// 说成「基线也失败」：那是一个它自己写着无法比较、却让下游（日报 bot、infrafix 健康规则）当成已核实老问题的断言。
function assertNotCalledPreExisting({ out }, shape) {
  const entries = (out.failed_checks ?? []).filter((c) => c.check_id === 'e2e-browser')
  assert.equal(entries.length, 1, `${shape}：e2e-browser 的失败要留在 failed_checks 里，不能悄悄变成通过：${JSON.stringify((out.failed_checks ?? []).map(brief))}`)
  const [entry] = entries
  const dump = brief(entry)
  assert.ok(entry.status !== 'pre-existing', `${shape}：基线对照无法比较（runner_error）时不能定成 pre-existing：${dump}`)
  assert.ok(entry.pre_existing !== true, `${shape}：不能置 pre_existing=true——下游据此把失败当成「合并前就有」的老问题：${dump}`)
  assert.ok(!/also fails on the base revision/.test(String(entry.attribution ?? '')), `${shape}：不能宣称基线版本上也失败，它自己同时写着 failure type not fingerprint-comparable：${dump}`)
  assert.equal(entry.severity, null, `${shape}：没核实的失败不定级（docs/report-schema.md：unverified → 无级别，待核实）：${dump}`)
  const graded = (out.findings ?? []).filter((f) => f.check_id === 'e2e-browser')
  assert.deepEqual(graded, [], `${shape}：没核实的失败不进 findings（findings 只放核实过的 P0–P3）：${JSON.stringify(graded)}`)
  const pending = (out.pending_verification ?? []).filter((p) => p.check_id === 'e2e-browser')
  assert.equal(pending.length, 1, `${shape}：这种情况是 QA 自己要先查清的（待核实），实际 pending_verification=${JSON.stringify((out.pending_verification ?? []).map((p) => ({ check_id: p.check_id, pending_kind: p.pending_kind, severity_if_confirmed: p.severity_if_confirmed })))}`)
  assert.equal(pending[0].pending_kind, 'check-qa', `${shape}：待核实种类应为 check-qa：${JSON.stringify(pending[0].pending_kind)}`)
  assert.equal(pending[0].owner, 'qa', `${shape}：待核实归 QA 自查，不算作者的问题：${pending[0].owner}`)
  assert.notEqual(out.outcome, 'passed', `${shape}：浏览器检查没跑通，不能当整轮通过：${out.outcome}`)
}

test('基线轮失败但对照不成立（runner_error）：不得把它报成 pre-existing「在基线版本上也失败」（PR #1398：h2 还通过了）', async () => {
  const run = await runPr({ number: 3201, h2Passing: true })
  // 先确认这是 #1398 的形态：首跑失败、同一提交重跑通过、基线轮也失败
  const entry = (run.out.failed_checks ?? []).find((c) => c.check_id === 'e2e-browser')
  assert.equal(entry?.evidence?.first_run, 'fail', `先决条件：首跑失败 ${brief(entry)}`)
  assert.equal(entry?.evidence?.rerun, 'pass', `先决条件：同提交重跑通过（#1398 的形态）${brief(entry)}`)
  assert.equal(entry?.evidence?.base_run, 'fail', `先决条件：基线轮也失败 ${brief(entry)}`)
  assertNotCalledPreExisting(run, 'PR #1398 形态')
})

test('基线轮失败但对照不成立（runner_error）：三轮同样失败也不能当成已核实的老问题（PR #1391 / #1392）', async () => {
  const run = await runPr({ number: 3202, h2Passing: false })
  const entry = (run.out.failed_checks ?? []).find((c) => c.check_id === 'e2e-browser')
  assert.equal(entry?.evidence?.first_run, 'fail', `先决条件：首跑失败 ${brief(entry)}`)
  assert.equal(entry?.evidence?.rerun, 'fail', `先决条件：重跑仍失败（#1391 / #1392 的形态）${brief(entry)}`)
  assert.equal(entry?.evidence?.base_run, 'fail', `先决条件：基线轮也失败 ${brief(entry)}`)
  assertNotCalledPreExisting(run, 'PR #1391/#1392 形态')
})
