// 报告字段：not-tested、qa-error 记 P2、system_health.heartbeat / complete；白盒覆盖率门禁
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-fields-'))
const { planTargets, processTargets, finalizeHealth, notTestedEntry, parseDeadline, selectIssueCandidates } = await import('../src/nightly.mjs')
const { parseAddedLines, compactFinal, newCodeCoverage, coverageGate, readCoverageDir, vitestCoverageArgs, isCoverageSource, COVERAGE_THRESHOLDS } = await import('../src/coverage.mjs')
const { coverageDirFor } = await import('../src/runner.mjs')
const { run, setBudget } = await import('../src/exec.mjs')

const health = () => ({ status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], notes: [] })
const disc = [{ number: 1, reason: 'merged' }, { number: 2, reason: 'merged' }, { number: 3, reason: 'open-updated' }]

test('发现了但没测的 PR 仍进 prs[]：severity 固定 untested（绝不是 P3）、tested=false，都带原因', () => {
  // --pr：只报告点名的 PR；别的发现结果只记在 excluded（→ system_health.discovery.excluded_by_pr_filter）
  const a = planTargets(disc, { prs: [2] })
  assert.deepEqual(a.targets.map((t) => [t.number, t.reason]), [[2, 'merged']])
  assert.deepEqual(a.notTested, [])
  assert.deepEqual(a.excluded, [1, 3])
  const b = planTargets(disc, { limit: 2 })
  assert.deepEqual(b.targets.map((t) => t.number), [1, 2])
  assert.deepEqual(b.notTested.map((p) => [p.number, p.severity, p.not_tested_code]), [[3, 'untested', 'over-limit']])
  assert.ok(b.notTested.every((p) => p.not_tested_reason && Array.isArray(p.findings)))
  const c = planTargets(disc, {})
  assert.equal(c.targets.length, 3)
  assert.equal(c.notTested.length, 0)
})

test('qa-error 记 P2（不再是 null）并登记到 qa_tool_failures；心跳记录每个 PR 的起止与耗时', async () => {
  const h = health()
  const startedAt = new Date()
  const { prs, heartbeat } = await processTargets(disc.slice(0, 2), {
    health: h, startedAt,
    processOne: async (t) => { if (t.number === 2) throw new Error('boom ghp_abcdefghijklmnopqrstuvwxyz0123456789'); return { number: t.number, verdict: 'clean', severity: 'P3', findings: [], checks: [] } },
  })
  assert.equal(prs[1].verdict, 'qa-error')
  assert.equal(prs[1].severity, 'P2')
  assert.ok(!prs[1].error.includes('ghp_abcdefghijklmnopqrstuvwxyz0123456789'), 'error is redacted')
  assert.deepEqual(h.qa_tool_failures.map((f) => [f.pr, f.stage]), [[2, 'pipeline']])
  assert.equal(h.heartbeat, heartbeat)
  assert.deepEqual(heartbeat.prs.map((b) => [b.number, b.verdict]), [[1, 'clean'], [2, 'qa-error']])
  for (const b of heartbeat.prs) { assert.ok(Date.parse(b.started_at) <= Date.parse(b.finished_at)); assert.equal(typeof b.duration_s, 'number') }
  finalizeHealth(h, prs, startedAt)
  assert.equal(h.status, 'failed')
  assert.equal(h.complete, false)
  assert.ok(heartbeat.run_finished_at && typeof heartbeat.duration_s === 'number')
})

test('complete / all_discovered_tested：--pr 排除不影响 complete，但不算全部测过', () => {
  const ok = { number: 1, verdict: 'clean', severity: 'P3' }
  const h1 = finalizeHealth(health(), [ok, notTestedEntry({ number: 2, reason: 'merged' }, 'excluded-by-pr-filter', 'x')])
  assert.equal(h1.complete, true)
  assert.equal(h1.all_discovered_tested, false)
  const h2 = finalizeHealth(health(), [ok])
  assert.equal(h2.complete, true)
  assert.equal(h2.all_discovered_tested, true)
  assert.equal(h2.status, 'ok')
  const h3 = health(); h3.qa_tool_failures.push({ stage: 'discovery', message: 'x' })
  assert.equal(finalizeHealth(h3, []).complete, false, 'discovery failure is never complete')
  const h4 = finalizeHealth(health(), [ok, notTestedEntry({ number: 3, reason: 'merged' }, 'pr-timeout', 'y')])
  assert.equal(h4.complete, false)
})

test('单 PR 时间预算：超时的子进程被终止，PR 记 not-tested（untested，pr-timeout），部分结果保留', async () => {
  const h = health()
  const { prs } = await processTargets([{ number: 9, reason: 'merged' }, { number: 10, reason: 'merged' }], {
    health: h, opts: { prTimeoutMin: 0.01 }, // 600ms
    processOne: async (t) => {
      if (t.number === 10) return { number: 10, verdict: 'clean', severity: 'P3', findings: [], checks: [] }
      const r = await run('sleep', ['30'], { timeoutMs: 60_000 })
      assert.equal(r.timedOut, true)
      assert.equal(r.budgetExceeded, true)
      const r2 = await run('true', [], {})
      assert.equal(r2.budgetExceeded, true, 'no new process starts after the budget is spent')
      return { number: 9, verdict: 'review-suggested', severity: 'P2', findings: [], checks: [{ id: 'unit', status: 'timeout' }] }
    },
  })
  assert.equal(prs[0].verdict, 'not-tested')
  assert.equal(prs[0].not_tested_code, 'pr-timeout')
  assert.equal(prs[0].severity, 'untested')
  assert.equal(prs[0].partial_result.verdict, 'review-suggested')
  assert.equal(prs[1].verdict, 'clean', 'next PR gets a fresh budget')
  assert.deepEqual(h.qa_tool_failures.map((f) => [f.pr, f.stage]), [[9, 'pr-timeout']])
  setBudget(null)
  assert.equal((await run('true', [], {})).ok, true, 'budget cleared after the run')
})

test('整晚截止时间：到点后未开始的 PR 记 not-tested（deadline）', async () => {
  const h = health()
  // 用相对真实时钟的未来时间：overBudget() 看的是真实 Date.now()，写死日期的话过了那一刻测试就会自己坏掉
  const startedAt = new Date(Date.now() + 24 * 3600_000)
  let t = startedAt.getTime()
  const { prs } = await processTargets(disc.slice(0, 2), {
    health: h, startedAt, opts: { deadline: new Date(startedAt.getTime() + 1000).toISOString() }, now: () => t,
    processOne: async (x) => { t += 5000; return { number: x.number, verdict: 'clean', severity: 'P3', findings: [], checks: [] } },
  })
  setBudget(null)
  assert.equal(prs[0].verdict, 'clean', 'started before the deadline')
  assert.equal(prs[1].verdict, 'not-tested')
  assert.equal(prs[1].not_tested_code, 'deadline')
  assert.equal(parseDeadline('09:00', new Date('2026-10-10T19:00:00Z')), Date.parse('2026-10-11T09:00:00+08:00'))
  assert.equal(parseDeadline('09:00', new Date('2026-10-11T02:00:00Z')), Date.parse('2026-10-12T09:00:00+08:00'), 'HH:MM already passed today → tomorrow')
  assert.throws(() => parseDeadline('nine'))
})

test('diff 新增行解析（-U0）', () => {
  const diff = ['diff --git a/src/a.ts b/src/a.ts', '--- a/src/a.ts', '+++ b/src/a.ts', '@@ -1,0 +2,2 @@', '+const a = 1', '+// note', '@@ -10 +12 @@', '-old', '+new()',
    'diff --git a/gone.ts b/gone.ts', '--- a/gone.ts', '+++ /dev/null', '@@ -1 +0,0 @@', '-x'].join('\n')
  const added = parseAddedLines(diff)
  assert.deepEqual(Object.keys(added), ['src/a.ts'])
  assert.deepEqual(added['src/a.ts'].map((r) => r.line), [2, 3, 12])
})

test('覆盖率：istanbul 逐行压缩、新代码覆盖率（未被加载的文件保守计为未覆盖）', () => {
  const root = '/r/src'
  const final = { '/r/src/src/a.ts': { path: '/r/src/src/a.ts', statementMap: { 0: { start: { line: 2 } }, 1: { start: { line: 12 } }, 2: { start: { line: 20 } } }, s: { 0: 3, 1: 0, 2: 1 } } }
  const files = compactFinal(final, root)
  assert.deepEqual(files['src/a.ts'], { lines: [2, 12, 20], covered: [2, 20] })
  const added = { 'src/a.ts': [{ line: 2, text: 'const a = 1' }, { line: 3, text: '// c' }, { line: 12, text: 'f()' }], 'src/new.ts': [{ line: 1, text: "import x from 'y'" }, { line: 2, text: 'export function g() {' }, { line: 3, text: '  return 1' }, { line: 4, text: '}' }], 'src/a.test.ts': [{ line: 1, text: 'test()' }], 'README.md': [{ line: 1, text: 'x' }] }
  const nc = newCodeCoverage(added, files)
  assert.equal(nc.executable_lines, 4) // a.ts: 2,12 ; new.ts: 2 code-like lines
  assert.equal(nc.covered_lines, 1)
  assert.equal(nc.pct, 25)
  assert.deepEqual(nc.unloaded_files, ['src/new.ts'])
  assert.equal(isCoverageSource('src/a.test.ts'), false)
  assert.equal(isCoverageSource('vite.config.ts'), false)
  assert.equal(isCoverageSource('server/x.mjs'), true)
  assert.equal(newCodeCoverage({ 'docs/a.md': [{ line: 1, text: 'x' }] }, files).pct, null)
})

test('覆盖率门禁：只有新代码 < 90% 才 fail；全仓 lines/branches/functions 只记基线；拿不到覆盖率 unavailable', () => {
  assert.deepEqual(COVERAGE_THRESHOLDS, { lines: 80, branches: 70, functions: 80, new_code: 90 })
  const files = { 'src/a.ts': { lines: [1, 2], covered: [1, 2] } }
  const ok = coverageGate({ coverage: { status: 'ok', total: { lines: 85, branches: 72, functions: 90 }, files }, added: { 'src/a.ts': [{ line: 1, text: 'a()' }] } })
  assert.equal(ok.status, 'pass'); assert.equal(ok.pass, true); assert.equal(ok.values.new_code, 100)
  assert.match(ok.new_code_method, /ming-qa newCode/)
  assert.match(ok.thresholds_source, /ming-qa G2/)
  const bad = coverageGate({ coverage: { status: 'ok', total: { lines: 85, branches: 60, functions: 90 }, files }, added: { 'src/b.ts': [{ line: 1, text: 'b()' }] } })
  assert.equal(bad.status, 'fail'); assert.deepEqual(bad.failed, ['new_code'])
  assert.equal(bad.results.branches, 'baseline'); assert.equal(bad.baseline.branches, 60); assert.equal(bad.baseline.reference.branches, 70)
  assert.deepEqual(bad.thresholds, { new_code: 90 })
  // 全仓指标再低也不让门禁失败（只是趋势数据）
  const lowGlobal = coverageGate({ coverage: { status: 'ok', total: { lines: 10, branches: 5, functions: 3 }, files }, added: { 'src/a.ts': [{ line: 1, text: 'a()' }] } })
  assert.equal(lowGlobal.status, 'pass'); assert.deepEqual(lowGlobal.failed, []); assert.equal(lowGlobal.baseline.lines, 10)
  const noDiff = coverageGate({ coverage: { status: 'ok', total: { lines: 85, branches: 72, functions: 90 }, files }, added: null, diffError: 'base unknown' })
  assert.equal(noDiff.status, 'not-evaluated'); assert.equal(noDiff.pass, null); assert.equal(noDiff.results.new_code, 'not-evaluated'); assert.match(noDiff.reason, /base unknown/)
  const na = coverageGate({ coverage: { status: 'unavailable', reason: 'coverage-summary.json not produced' } })
  assert.equal(na.status, 'unavailable'); assert.equal(na.pass, null)
})

test('覆盖率门禁 P2 不会成为 issue 候选（即使作者是 PraiseZhu）', () => {
  const report = { date: 'd', repo: 'r', prs: [{ number: 1, author: 'PraiseZhu', findings: [{ source: 'coverage-gate', check_id: 'coverage-gate', severity: 'P2', fingerprint: 'cg' }] }], system_health: { status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [] } }
  assert.equal(selectIssueCandidates(report).candidates.length, 0)
})

test('覆盖率只在独立的 cov 轮次开启（判定轮次从不带覆盖率）、目录不能逃出 run 目录；读取后删掉大文件只留 summary', () => {
  const runDir = mkdtempSync(join(tmpdir(), 'cov-run-'))
  const step = { id: 'unit', coverage: { dir: '${RUN}/coverage/unit' } }
  assert.equal(coverageDirFor(step, runDir, { runLabel: 'cov' }), join(runDir, 'coverage/unit'))
  assert.equal(coverageDirFor(step, runDir, { runLabel: 'h1' }), null, 'verdict runs never carry coverage')
  assert.equal(coverageDirFor(step, runDir, { runLabel: 'h2' }), null)
  assert.equal(coverageDirFor(step, runDir, { runLabel: 'b' }), null)
  assert.equal(coverageDirFor({ id: 'lint' }, runDir, { runLabel: 'h1' }), null)
  assert.throws(() => coverageDirFor({ id: 'u', coverage: { dir: '/etc/x' } }, runDir, { runLabel: 'cov' }))
  process.env.ARGUS_COVERAGE = '0'
  assert.equal(coverageDirFor(step, runDir, { runLabel: 'cov' }), null)
  delete process.env.ARGUS_COVERAGE
  assert.ok(vitestCoverageArgs('/x').includes('--coverage.reportsDirectory=/x'))
  assert.ok(!vitestCoverageArgs('/x').some((a) => a.includes('thresholds')), 'vitest must not fail the unit step on thresholds')
  const dir = join(runDir, 'coverage/unit'); mkdirSync(dir, { recursive: true })
  assert.equal(readCoverageDir(dir, '/r').status, 'unavailable')
  writeFileSync(join(dir, 'coverage-summary.json'), JSON.stringify({ total: { lines: { pct: 81.5 }, branches: { pct: 70 }, functions: { pct: 79.9 }, statements: { pct: 80 } } }))
  writeFileSync(join(dir, 'coverage-final.json'), JSON.stringify({ '/r/src/a.ts': { path: '/r/src/a.ts', statementMap: { 0: { start: { line: 1 } } }, s: { 0: 1 } } }))
  const c = readCoverageDir(dir, '/r')
  assert.equal(c.status, 'ok'); assert.equal(c.total.functions, 79.9); assert.deepEqual(c.files['src/a.ts'], { lines: [1], covered: [1] })
  assert.equal(existsSync(join(dir, 'coverage-final.json')), false)
})

test('tested_at / 运行时间戳：ISO 8601 带 +08:00；not-tested 与 qa-error 的 tested_at 为 null', async () => {
  const { toShanghaiIso } = await import('../src/util.mjs')
  assert.equal(toShanghaiIso(new Date('2026-10-10T19:40:12.345Z')), '2026-10-11T03:40:12+08:00')
  assert.equal(toShanghaiIso('nope'), null)
  const e = notTestedEntry({ number: 5, reason: 'merged' }, 'over-limit', 'x')
  assert.ok('tested_at' in e && e.tested_at === null)
  const h = health()
  const { prs, heartbeat } = await processTargets([{ number: 6, reason: 'merged' }], { health: h, processOne: async () => { throw new Error('x') } })
  assert.equal(prs[0].tested_at, null)
  assert.match(prs[0].test_started_at, /\+08:00$/)
  assert.match(heartbeat.run_started_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00$/)
})

test('审查与测试同一提交：base..tested 的 git diff 解析成 GitHub files 形状', async () => {
  const { splitDiffFiles } = await import('../src/nightly.mjs')
  const text = ['diff --git a/src/a.ts b/src/a.ts', 'index 1..2 100644', '--- a/src/a.ts', '+++ b/src/a.ts', '@@ -1,2 +1,3 @@', ' keep', '-old', '+new', '+more',
    'diff --git a/src/n.ts b/src/n.ts', 'new file mode 100644', '--- /dev/null', '+++ b/src/n.ts', '@@ -0,0 +1 @@', '+x',
    'diff --git a/img.png b/img.png', 'deleted file mode 100644', 'Binary files a/img.png and /dev/null differ'].join('\n')
  const files = splitDiffFiles(text)
  assert.deepEqual(files.map((f) => [f.filename, f.status, f.additions, f.deletions]), [['src/a.ts', 'modified', 2, 1], ['src/n.ts', 'added', 1, 0], ['img.png', 'removed', 0, 0]])
  assert.match(files[0].patch, /^@@ -1,2 \+1,3 @@\n keep\n-old\n\+new\n\+more$/)
  assert.equal(files[2].patch, null)
  // 同一份 diff 给覆盖率用
  assert.deepEqual(parseAddedLines(text)['src/a.ts'].map((r) => r.line), [2, 3])
})

test('夜间全量把每个测完的已合并 PR 写成 merged/PR-N.json（同格式、可被复用）；--pr 运行默认不写', async () => {
  const { mergedReportFor, writeMergedEnabled, loadMergedResult } = await import('../src/nightly.mjs')
  assert.equal(writeMergedEnabled({}), true)
  assert.equal(writeMergedEnabled({ prs: [1] }), false)
  assert.equal(writeMergedEnabled({ prs: [1], writeMerged: true }), true)
  assert.equal(writeMergedEnabled({ writeMerged: false }), false)
  const pr = { number: 898, state: 'merged', verdict: 'clean', severity: 'P3', tested_sha: 'a'.repeat(40), merge_sha: 'a'.repeat(40), tested_at: '2026-10-11T04:00:00+08:00', findings: [] }
  const report = { schema_version: '1.0', date: '2026-10-10', run: { id: 'r', args: { prs: null } }, summary: {}, prs: [pr, { number: 2, state: 'open', verdict: 'clean' }],
    system_health: { status: 'degraded', qa_tool_failures: [{ pr: 2, stage: 'x' }, { pr: 898, stage: 'y' }, { stage: 'janitor' }], env_problems: [], llm_errors: [], heartbeat: { prs: [{ number: 898 }, { number: 2 }] } } }
  const doc = mergedReportFor(report, pr)
  assert.equal(doc.schema_version, '1.0')
  assert.deepEqual(doc.prs.map((p) => p.number), [898])
  assert.equal(doc.prs[0].tested_at, '2026-10-11T04:00:00+08:00')
  assert.deepEqual(doc.system_health.qa_tool_failures.map((f) => f.stage), ['y', 'janitor'])
  assert.deepEqual(doc.system_health.heartbeat.prs, [{ number: 898 }])
  assert.equal(doc.summary.pr_count, 1)
  const dir = mkdtempSync(join(tmpdir(), 'merged-'))
  writeFileSync(join(dir, 'PR-898.json'), JSON.stringify(doc))
  assert.ok(loadMergedResult(898, 'a'.repeat(40), { mergedDir: dir }), 'nightly-written merged report is reusable')
})

test('test_backed：只有失败检查佐证才是 true；每个发现都有 source；PR 级汇总；issue 规则按 test_backed', async () => {
  const { annotateTestBacked, isTestBacked, FINDING_SOURCES } = await import('../src/nightly.mjs')
  assert.deepEqual(FINDING_SOURCES, ['check', 'code-review', 'coverage-gate', 'qa-error', 'ai-test'])
  const prs = annotateTestBacked([
    { number: 1, findings: [{ source: 'check', severity: 'P2' }, { source: 'code-review', severity: 'P1' }, { source: 'coverage-gate', severity: 'P2' }] },
    { number: 2, findings: [{ source: 'code-review', severity: 'P1' }] },
    { number: 3, findings: [{ severity: 'P1', status: 'introduced' }] }, // 旧报告：没有 source 的是失败检查
    { number: 4, verdict: 'not-tested', findings: [] },
  ])
  assert.deepEqual(prs[0].findings.map((f) => f.test_backed), [true, false, false])
  assert.deepEqual([prs[0].test_backed, prs[0].test_backed_severity], [true, 'P2'])
  assert.deepEqual([prs[1].test_backed, prs[1].test_backed_severity], [false, null])
  assert.equal(prs[2].findings[0].source, 'check'); assert.equal(prs[2].test_backed, true)
  assert.equal(prs[3].test_backed, false)
  assert.equal(isTestBacked({ source: 'check', test_backed: false }), false, 'explicit flag wins')
  // qa-error 自带 source: qa-error、test_backed: false 的发现
  const h = health()
  const { prs: out } = await processTargets([{ number: 7, reason: 'merged' }], { health: h, repo: 'o/r', processOne: async () => { throw new Error('crash') } })
  assert.deepEqual(out[0].findings.map((f) => [f.source, f.test_backed, f.severity]), [['qa-error', false, 'P2']])
  // issue：P1 但不是 test_backed → 不提
  const report = { date: 'd', repo: 'r', prs: [{ number: 9, author: 'PraiseZhu', findings: [{ source: 'check', test_backed: false, severity: 'P1', fingerprint: 'x' }, { source: 'check', test_backed: true, severity: 'P1', fingerprint: 'y' }] }], system_health: { status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [] } }
  const { candidates, skipped } = selectIssueCandidates(report)
  assert.deepEqual(candidates.map((c) => c.fingerprint), ['y'])
  assert.match(skipped[0].reason, /not test-backed/)
})

test('诚实：三个维度有一个没真正跑过就不能是 clean；跳过的步骤都有原因', async () => {
  const { dimensionsOf, verdictOf } = await import('../src/nightly.mjs')
  const c = (id, dimension, status) => ({ id, dimension, status, kind: 'product' })
  const all = [c('unit', 'white-box', 'pass'), c('api', 'gray-box', 'pass'), c('e2e', 'black-box', 'pass')]
  const d1 = dimensionsOf(all)
  assert.deepEqual(d1.dimensions_missing, [])
  assert.equal(verdictOf({ findings: [], checks: all, ...d1 }), 'clean')
  const noBlack = [c('unit', 'white-box', 'pass'), c('api', 'gray-box', 'pass'), c('e2e', 'black-box', 'unavailable')]
  const d2 = dimensionsOf(noBlack)
  assert.deepEqual(d2.dimensions_missing, ['black-box'])
  assert.equal(verdictOf({ findings: [], checks: noBlack, ...d2 }), 'partially-verified')
  const skippedBlack = [c('unit', 'white-box', 'pass'), c('api', 'gray-box', 'fail')]
  const d3 = dimensionsOf(skippedBlack)
  assert.deepEqual(d3.dimensions_run['gray-box'], ['api'], 'a failing check still means the dimension ran')
  assert.equal(verdictOf({ findings: [], checks: skippedBlack, ...d3 }), 'partially-verified')
  assert.deepEqual(dimensionsOf([]).dimensions_missing, ['white-box', 'gray-box', 'black-box'])
})

test('每个条目都有 merged_at（没测的也从发现结果带上）与 merged_at_shanghai', async () => {
  const { prMeta, annotateTestBacked } = await import('../src/nightly.mjs')
  const t = { number: 898, reason: 'merged', meta: prMeta({ title: 't', user: { login: 'u' }, merged_at: '2026-10-10T02:00:00Z', merge_commit_sha: 'm', state: 'closed' }) }
  const [e] = annotateTestBacked([notTestedEntry(t, 'over-limit', 'x')])
  assert.equal(e.merged_at, '2026-10-10T02:00:00Z')
  assert.equal(e.merged_at_shanghai, '2026-10-10T10:00:00+08:00')
  assert.equal(e.state, 'merged')
  const [o] = annotateTestBacked([{ number: 1, findings: [] }])
  assert.ok('merged_at' in o && o.merged_at === null && o.merged_at_shanghai === null)
})

test('finalizeHealth：复用的合并即测结果来自 degraded 运行时，本次 system_health 至少 degraded', async () => {
  const { finalizeHealth } = await import('../src/nightly.mjs')
  const mk = () => ({ status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], notes: [] })
  const h = finalizeHealth(mk(), [{ number: 7, verdict: 'clean', reused: true, reused_report_system_health: 'degraded' }])
  assert.equal(h.status, 'degraded')
  assert.match(h.notes.at(-1), /#7 \(degraded\)/)
  assert.equal(finalizeHealth(mk(), [{ number: 8, verdict: 'clean', reused: true, reused_report_system_health: 'ok' }]).status, 'ok')
  assert.equal(finalizeHealth(mk(), [{ number: 9, verdict: 'clean' }]).status, 'ok')
})
