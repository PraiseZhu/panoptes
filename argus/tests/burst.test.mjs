// burst 容量：单测分片（内存护栏、报告与覆盖率合并）、步骤结果复用、08:30 截止
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-burst-'))
const shard = await import('../src/shard.mjs')
const cache = await import('../src/stepcache.mjs')
const { processTargets } = await import('../src/nightly.mjs')
const { readCoverageDir } = await import('../src/coverage.mjs')
const health = () => ({ status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], notes: [] })
const step = { id: 'unit', shard: { max: 8, perShardGb: 1.5 } }

test('分片数：内存够 → 8 片（CPU 核数封顶）；内存紧 → 按预算降；其他分片步骤占用的预算要扣掉；ARGUS_UNIT_SHARDS 强制', () => {
  assert.equal(shard.shardPlan(step, { freeGb: 14, reserved: 0, cores: 8, env: { ARGUS_SHARD_MAX: '8', ARGUS_SHARD_BUDGET_GB: '10' } }).shards, 6, 'budget min(10, 14-1.5)=10 → 6 × 1.5 GB')
  assert.equal(shard.shardPlan(step, { freeGb: 14, reserved: 0, cores: 8, env: { ARGUS_SHARD_MAX: '8', ARGUS_SHARD_BUDGET_GB: '10', ARGUS_SHARD_GB: '1' } }).shards, 8)
  assert.equal(shard.shardPlan(step, { freeGb: 14, reserved: 0, cores: 4, env: { ARGUS_SHARD_MAX: '8', ARGUS_SHARD_BUDGET_GB: '10', ARGUS_SHARD_GB: '1' } }).shards, 4, 'never more than cores')
  assert.equal(shard.shardPlan(step, { freeGb: 5, reserved: 0, cores: 8, env: { ARGUS_SHARD_MAX: '8', ARGUS_SHARD_BUDGET_GB: '10' } }).shards, 2, '(5-1.5)/1.5')
  assert.equal(shard.shardPlan(step, { freeGb: 14, reserved: 9, cores: 8, env: { ARGUS_SHARD_MAX: '8', ARGUS_SHARD_BUDGET_GB: '10' } }).shards, 1, 'h1 already holds 9 GB → cov gets 1 shard')
  assert.equal(shard.shardPlan(step, { freeGb: 1, reserved: 0, cores: 8, env: { ARGUS_SHARD_MAX: '8', ARGUS_SHARD_BUDGET_GB: '10' } }).shards, 1, 'never below 1')
  assert.equal(shard.shardPlan(step, { freeGb: 1, reserved: 0, cores: 8, env: { ARGUS_SHARD_MAX: '8', ARGUS_SHARD_BUDGET_GB: '10', ARGUS_UNIT_SHARDS: '3' } }).shards, 3)
  assert.equal(shard.shardPlan({ id: 'lint' }, { freeGb: 14 }).shards, 1)
  const release = shard.reserveShards(6); assert.equal(shard.shardReservedGb(), 6); release(); release(); assert.equal(shard.shardReservedGb(), 0)
})

test('分片数默认封顶 2（共享机器防内存耗尽）', () => {
  assert.equal(shard.shardPlan(step, { freeGb: 14, reserved: 0, cores: 8, env: {} }).shards, 2)
  assert.equal(shard.shardPlan(step, { freeGb: 14, reserved: 0, cores: 8, env: { ARGUS_UNIT_SHARDS: '6' } }).shards, 2)
  assert.equal(shard.shardPlan(step, { freeGb: 1, reserved: 0, cores: 8, env: {} }).shards, 1)
  assert.equal(shard.shardPlan(step, { freeGb: 14, reserved: 3, cores: 8, env: {} }).shards, 1, 'default budget 3 GB: h1 holds 2 × 1.5 → cov 1 shard')
})

test('分片 JSON 报告合并：计数相加、testResults 拼接、任何一片失败 success=false', () => {
  const a = { numTotalTests: 3, numPassedTests: 3, numFailedTests: 0, numTotalTestSuites: 1, success: true, startTime: 20, testResults: [{ name: 'a.test.ts' }] }
  const b = { numTotalTests: 2, numPassedTests: 1, numFailedTests: 1, numTotalTestSuites: 1, success: false, startTime: 10, testResults: [{ name: 'b.test.ts' }] }
  const m = shard.mergeVitestJson([a, b])
  assert.equal(m.numTotalTests, 5); assert.equal(m.numFailedTests, 1); assert.equal(m.numTotalTestSuites, 2)
  assert.equal(m.success, false); assert.equal(m.startTime, 10); assert.deepEqual(m.testResults.map((t) => t.name), ['a.test.ts', 'b.test.ts'])
  assert.equal(shard.mergeVitestJson([a, { ...a, testResults: [] }]).success, true)
})

test('分片覆盖率合并：同一文件在两片都被加载 → 命中数按位置相加；总数按 istanbul 口径重算；新代码覆盖率读合并结果', () => {
  const loc = (l, c = 0) => ({ start: { line: l, column: c }, end: { line: l, column: c + 5 } })
  const file = (s, f, b) => ({ path: '/src/x.ts', statementMap: { 0: loc(1), 1: loc(2), 2: loc(3) }, s, fnMap: { 0: { name: 'f', decl: loc(1), loc: loc(1) } }, f, branchMap: { 0: { loc: loc(2), type: 'if', locations: [loc(2), loc(2, 6)] } }, b })
  const s1 = { '/src/x.ts': file({ 0: 1, 1: 0, 2: 0 }, { 0: 1 }, { 0: [1, 0] }) }
  const s2 = { '/src/x.ts': file({ 0: 2, 1: 1, 2: 0 }, { 0: 0 }, { 0: [0, 0] }), '/src/y.ts': { path: '/src/y.ts', statementMap: { 0: loc(1) }, s: { 0: 0 }, fnMap: {}, f: {}, branchMap: {}, b: {} } }
  const m = shard.mergeCoverageFinal([s1, s2])
  assert.deepEqual(m['/src/x.ts'].s, { 0: 3, 1: 1, 2: 0 }); assert.deepEqual(m['/src/x.ts'].b, { 0: [1, 0] }); assert.deepEqual(m['/src/x.ts'].f, { 0: 1 })
  assert.ok(m['/src/y.ts'], 'union of files')
  assert.deepEqual(s1['/src/x.ts'].s, { 0: 1, 1: 0, 2: 0 }, 'inputs untouched')
  const sum = shard.coverageSummaryOf(m).total
  assert.deepEqual([sum.lines.total, sum.lines.covered, sum.lines.pct], [4, 2, 50])
  assert.deepEqual([sum.statements.total, sum.statements.covered], [4, 2])
  assert.deepEqual([sum.functions.total, sum.functions.covered, sum.branches.total, sum.branches.covered], [1, 1, 2, 1])
  // 合并结果写成 vitest 的目录格式后，readCoverageDir 照常读（新代码覆盖率用 files）
  const dir = mkdtempSync(join(tmpdir(), 'cov-merge-'))
  writeFileSync(join(dir, 'coverage-final.json'), JSON.stringify(m)); writeFileSync(join(dir, 'coverage-summary.json'), JSON.stringify(shard.coverageSummaryOf(m)))
  const r = readCoverageDir(dir, '/src')
  assert.equal(r.status, 'ok'); assert.equal(r.total.lines, 50); assert.deepEqual(r.files['x.ts'], { lines: [1, 2, 3], covered: [1, 2] })
})

test('分片覆盖率合并：多个函数按各自位置对齐（fnMap 条目是 {name,decl,loc}），不会全挤进同一个函数', () => {
  const loc = (l) => ({ start: { line: l, column: 0 }, end: { line: l, column: 9 } })
  const fn = (n, l) => ({ name: n, decl: loc(l), loc: loc(l), line: l })
  const file = (f) => ({ path: '/src/z.ts', statementMap: {}, s: {}, fnMap: { 0: fn('a', 1), 1: fn('b', 5), 2: fn('c', 9) }, f, branchMap: {}, b: {} })
  const m = shard.mergeCoverageFinal([{ z: file({ 0: 1, 1: 0, 2: 0 }) }, { z: file({ 0: 0, 1: 2, 2: 0 }) }])
  assert.deepEqual(m.z.f, { 0: 1, 1: 2, 2: 0 })
  const sum = shard.coverageSummaryOf(m).total
  assert.deepEqual([sum.functions.total, sum.functions.covered], [3, 2])
})

function gitRepo(files) {
  const d = mkdtempSync(join(tmpdir(), 'cache-repo-'))
  const g = (...a) => spawnSync('git', ['-C', d, ...a], { encoding: 'utf8' })
  g('init', '-q'); g('config', 'user.email', 'a@b'); g('config', 'user.name', 'a')
  for (const [p, c] of Object.entries(files)) { mkdirSync(join(d, p, '..'), { recursive: true }); writeFileSync(join(d, p), c) }
  g('add', '-A'); g('commit', '-qm', 'x')
  return { dir: d, commit: (more) => { for (const [p, c] of Object.entries(more)) { mkdirSync(join(d, p, '..'), { recursive: true }); writeFileSync(join(d, p), c) } g('add', '-A'); g('commit', '-qm', 'y') } }
}

test('步骤结果复用：输入路径 tree 哈希 + lockfile 不变 → 复用（reused + 来源）；输入变了 / lockfile 变了 → 重跑；失败不缓存', () => {
  process.env.ARGUS_STEP_CACHE_DIR = mkdtempSync(join(tmpdir(), 'step-cache-'))
  const repo = gitRepo({ 'server/a.ts': '1', 'cindyplugin/p.ts': '1', 'package-lock.json': '{}' })
  const api = { id: 'api-inproc', cmd: ['vitest', 'run', 'server'], inputs: ['server'] }
  const k1 = cache.stepCacheKey({ step: api, label: 'h1', src: repo.dir })
  assert.ok(k1 && k1.inputs.server && k1.inputs['package-lock.json'])
  const passed = { id: 'api-inproc', ok: true, exitCode: 0, timedOut: false, durationMs: 11000, failures: [], testOutcomeStatus: 'complete' }
  assert.equal(cache.storeStepResult(k1, passed, { job: 'n900', label: 'h1', sha: 'aaa' }), true)
  // 另一个 PR 只改了 cindyplugin/：server 的 tree 哈希不变 → 复用
  repo.commit({ 'cindyplugin/p.ts': '2' })
  const k2 = cache.stepCacheKey({ step: api, label: 'h1', src: repo.dir })
  assert.equal(k2.key, k1.key)
  const hit = cache.lookupStepResult(k2)
  const r = cache.reusedResult(hit, { jobId: 'n901' })
  assert.equal(r.reused, true); assert.equal(r.ok, true); assert.equal(r.durationMs, 0)
  assert.deepEqual([r.reusedFrom.job, r.reusedFrom.sha, r.reusedFrom.original_duration_ms], ['n900', 'aaa', 11000])
  // 整棵树为输入的步骤（没写 inputs）这次要重跑
  const unit = { id: 'unit', cmd: ['vitest'] }
  assert.notEqual(cache.stepCacheKey({ step: unit, label: 'h1', src: repo.dir }).key, cache.stepCacheKey({ step: unit, label: 'h1', src: repo.dir, hashes: { '.': 'other', 'package-lock.json': 'x' } }).key)
  // lockfile 变了 → 键变
  repo.commit({ 'package-lock.json': '{"v":2}' })
  assert.notEqual(cache.stepCacheKey({ step: api, label: 'h1', src: repo.dir }).key, k1.key)
  repo.commit({ 'package-lock.json': '{}' })
  // server 变了 → 键变
  repo.commit({ 'server/a.ts': '2' })
  assert.equal(cache.lookupStepResult(cache.stepCacheKey({ step: api, label: 'h1', src: repo.dir })), null)
  // 失败 / 超时 / 报告不完整不缓存；覆盖率轮次与判定轮次分开；重跑 / 基线 / AI / bench 不复用
  assert.equal(cache.storeStepResult(k1, { ...passed, ok: false, exitCode: 1 }, {}), false)
  assert.equal(cache.storeStepResult(k1, { ...passed, testOutcomeStatus: 'incomplete' }, {}), false)
  assert.notEqual(cache.stepCacheKey({ step: api, label: 'cov', src: repo.dir }).key, cache.stepCacheKey({ step: api, label: 'h1', src: repo.dir }).key)
  for (const label of ['h2', 'b', 'ai1', 'air']) assert.equal(cache.stepCacheKey({ step: api, label, src: repo.dir }), null)
  assert.equal(cache.stepCacheKey({ step: { ...api, cache: false }, label: 'h1', src: repo.dir }), null)
  // 覆盖率轮次：插桩下单测失败也可以缓存，只要覆盖率数据完整
  const kc = cache.stepCacheKey({ step: unit, label: 'cov', src: repo.dir })
  assert.equal(cache.storeStepResult(kc, { id: 'unit', ok: false, exitCode: 1, coverage: { status: 'ok', total: {}, files: {} } }, {}), true)
  assert.equal(cache.storeStepResult(kc, { id: 'unit', ok: true, exitCode: 0, coverage: { status: 'unavailable' } }, {}), false)
  // 用到的替换变量（DIFF_BASE_SHA）进键
  const design = { id: 'design', cmd: ['npm', 'run', 'verify:design'], env: { MIVO_GUARD_BASE_SHA: '${DIFF_BASE_SHA}' } }
  assert.notEqual(cache.stepCacheKey({ step: design, label: 'h1', src: repo.dir, vars: { DIFF_BASE_SHA: 'a' } }).key, cache.stepCacheKey({ step: design, label: 'h1', src: repo.dir, vars: { DIFF_BASE_SHA: 'b' } }).key)
  // 每次运行不同的位置变量（\${TEST_REPORT}、\${RUN}、运行目录下的路径）不进键：两次运行同一棵树 → 同一个键（真跑 #893 时单测因此没命中）
  const u2 = { id: 'unit', cmd: ['npm', 'run', 'test:unit', '--', '--outputFile=${TEST_REPORT}'], testReport: { format: 'vitest-json', path: '${RUN}/reports/unit.json' }, shard: { max: 8 } }
  assert.equal(cache.stepCacheKey({ step: u2, label: 'h1', src: repo.dir, vars: { TEST_REPORT: '/c/runs/n893-h1-a/reports/unit.json', RUN: '/c/runs/n893-h1-a' } }).key,
    cache.stepCacheKey({ step: u2, label: 'h1', src: repo.dir, vars: { TEST_REPORT: '/c/runs/n893-h1-b/reports/unit.json', RUN: '/c/runs/n893-h1-b' } }).key)
  delete process.env.ARGUS_STEP_CACHE_DIR
})

test('--burst-cutoff：到点未开始的 PR → not-tested（burst-cutoff，说明补测稍后接着测），也进 qa_tool_failures', async () => {
  const h = health()
  // relative to the real clock: the per-PR budget timer (overBudget) uses Date.now(), so a fixed 08:00 start fails
  // once the real time is past the cutoff. Cutoff = real now + 30 min (Asia/Shanghai HH:MM).
  let t = Date.now()
  const cut = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(t + 30 * 60_000))
  const { prs } = await processTargets([{ number: 1, reason: 'merged' }, { number: 2, reason: 'merged' }], {
    health: h, opts: { deadline: cut, deadlineKind: 'burst-cutoff' }, startedAt: new Date(t), now: () => t,
    processOne: async (x) => { t += 40 * 60_000; return { number: x.number, verdict: 'clean', severity: 'P3', findings: [], checks: [] } },
  })
  assert.equal(prs[0].verdict, 'clean')
  assert.equal(prs[1].verdict, 'not-tested'); assert.equal(prs[1].not_tested_code, 'burst-cutoff')
  assert.match(prs[1].not_tested_reason, new RegExp(`burst cutoff ${cut} Asia/Shanghai .*catch-up`))
  assert.deepEqual(h.qa_tool_failures.map((f) => [f.pr, f.stage]), [[2, 'burst-cutoff']])
})

test('step-cache key includes the browser versions (system Chromium + Playwright and its Chromium): a browser upgrade → no reuse', () => {
  process.env.ARGUS_STEP_CACHE_DIR = mkdtempSync(join(tmpdir(), 'step-cache-br-'))
  const repo = gitRepo({ 'src/a.ts': '1', 'package-lock.json': '{}' })
  const journey = { id: 'browser-journey', cmd: ['node', 'journey.mjs'] }
  const b1 = { chromium: 'Chromium 154.0.8037.92 built on Debian', playwright: '1.64.0', playwright_chromium: '156.0.8078.4 (r1248)' }
  const k1 = cache.stepCacheKey({ step: journey, label: 'h1', src: repo.dir, browser: b1 })
  assert.equal(cache.stepCacheKey({ step: journey, label: 'h1', src: repo.dir, browser: { ...b1 } }).key, k1.key, 'same browsers → same key')
  assert.notEqual(cache.stepCacheKey({ step: journey, label: 'h1', src: repo.dir, browser: { ...b1, chromium: 'Chromium 155.0.1.1 built on Debian' } }).key, k1.key, 'system chromium upgrade')
  assert.notEqual(cache.stepCacheKey({ step: journey, label: 'h1', src: repo.dir, browser: { ...b1, playwright: '1.65.0' } }).key, k1.key, 'playwright upgrade')
  assert.notEqual(cache.stepCacheKey({ step: journey, label: 'h1', src: repo.dir, browser: { ...b1, playwright_chromium: '157.0.1.0 (r1260)' } }).key, k1.key, 'playwright chromium revision')
  // stored entry and reused_from carry the browser versions
  assert.equal(cache.storeStepResult(k1, { id: journey.id, ok: true, exitCode: 0, timedOut: false, durationMs: 5000, failures: [], testOutcomeStatus: 'complete' }, { job: 'n1', label: 'h1', sha: 'a' }), true)
  const r = cache.reusedResult(cache.lookupStepResult(k1), {})
  assert.deepEqual(r.reusedFrom.browser, b1)
  // default: computed from the real launcher / node_modules once per process
  const d = cache.stepCacheKey({ step: journey, label: 'h1', src: repo.dir })
  assert.deepEqual(Object.keys(d.browser), ['chromium', 'playwright', 'playwright_chromium'])
})

test('browserVersions reads chromium --version and the playwright package / browsers.json', () => {
  const dir = mkdtempSync(join(tmpdir(), 'br-'))
  const bin = join(dir, 'chromium')
  writeFileSync(bin, '#!/bin/sh\necho "Chromium 999.0.0.1 test"\n', { mode: 0o755 })
  mkdirSync(join(dir, 'node_modules/playwright'), { recursive: true }); mkdirSync(join(dir, 'node_modules/playwright-core'), { recursive: true })
  writeFileSync(join(dir, 'node_modules/playwright/package.json'), JSON.stringify({ version: '9.9.9' }))
  writeFileSync(join(dir, 'node_modules/playwright-core/browsers.json'), JSON.stringify({ browsers: [{ name: 'chromium', revision: '42', browserVersion: '200.0.0.0' }] }))
  assert.deepEqual(cache.browserVersions({ chromiumBin: bin, codeDir: dir }), { chromium: 'Chromium 999.0.0.1 test', playwright: '9.9.9', playwright_chromium: '200.0.0.0 (r42)' })
  assert.deepEqual(cache.browserVersions({ chromiumBin: join(dir, 'missing'), codeDir: join(dir, 'none') }), { chromium: 'unavailable', playwright: 'absent', playwright_chromium: 'absent' })
})
