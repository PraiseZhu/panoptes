// AI 补测：只补分诊说涉及但没被覆盖的方面；输出严格校验；执行器靠「植入错误」证明测试有效；补测确认的问题不自动提 issue
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, writeFileSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
process.env.ARGUS_ROOT = mkdtempSync(join(tmpdir(), 'argus-aitest-'))
const { toGapDimensions, toValidTests, toAiTestSteps } = await import('../src/aitests.mjs')
const { toTrialDecision } = await import('../src/control.mjs')
const RUNNER = join(here, '..', 'src', 'ai-test-run.mjs')

const profile = {
  aiTests: { frameworkZh: 'node:test', pathGlobs: ['*.argus.test.mjs', 'tests/argus/*.test.mjs'], run: ['node', '--test', '{file}'] },
  steps: [
    { id: 'install', kind: 'setup', cmd: ['x'] },
    { id: 'unit', kind: 'product', cmd: ['x'], covers: ['unit', 'functional'] },
    { id: 'e2e', kind: 'product', cmd: ['x'], optional: true, covers: ['e2e'] },
  ],
}
const triage = { status: 'ok', rows: [
  { key: 'unit', verdict: 'applicable' }, { key: 'boundary', verdict: 'applicable' }, { key: 'e2e', verdict: 'applicable' },
  { key: 'concurrency', verdict: 'undetermined' }, { key: 'visual', verdict: 'looks_unrelated' },
] }
const good = { kind: 'coverage', purposeZh: '空列表求和为 0', covers: ['boundary'], filePath: 'sum.argus.test.mjs', content: 'x', mutation: { file: 'sum.mjs', find: 'a', replace: 'b' } }

test('缺口 = 分诊判涉及、但这次要跑的步骤都不覆盖的方面；被跳过的步骤不算覆盖', () => {
  assert.deepEqual(toGapDimensions({ profile, triage, stepIds: ['install', 'unit', 'e2e'] }), ['boundary'])
  assert.deepEqual(toGapDimensions({ profile, triage, stepIds: ['install', 'unit'] }), ['boundary', 'e2e'])
})

test('模型输出校验：路径越界、不在允许位置、没有植入错误、覆盖不在缺口里的都丢掉；最多 2 个', () => {
  const bad = [
    { ...good, filePath: '../x.argus.test.mjs' },
    { ...good, filePath: 'src/x.test.mjs' },
    { ...good, mutation: { file: 'sum.mjs', find: 'a', replace: 'a' } },
    { ...good, mutation: null },
    { ...good, covers: ['visual'] },
  ]
  assert.deepEqual(toValidTests({ tests: bad }, { profile, gaps: ['boundary'] }), [])
  const ok = toValidTests({ tests: [good, { ...good, kind: 'suspicion', covers: [] }, good] }, { profile, gaps: ['boundary'] })
  assert.equal(ok.length, 2)
  assert.equal(ok[1].kind, 'suspicion')
  assert.deepEqual(toValidTests({ tests: 'nope' }, { profile, gaps: ['boundary'] }), [])
  const steps = toAiTestSteps(ok, profile)
  assert.equal(steps[0].id, 'ai-test-1')
  assert.deepEqual(steps[0].unavailableExitCodes, [2, 4])
  assert.equal(steps[0].aiTest.runCmd, profile.aiTests.run)
})

function project() {
  const dir = mkdtempSync(join(tmpdir(), 'argus-aiproj-'))
  writeFileSync(join(dir, 'package.json'), JSON.stringify({ type: 'module' }))
  writeFileSync(join(dir, 'sum.mjs'), 'export const sum = (xs) => xs.reduce((a, b) => a + b, 0)\n')
  return dir
}
function runSpec(dir, spec) {
  const specPath = join(dir, '..', `${Date.now()}-${Math.random()}.json`)
  writeFileSync(specPath, JSON.stringify({ runCmd: ['node', '--test', '{file}'], purposeZh: '空列表求和为 0', ...spec }))
  // 本测试自己跑在 node --test 里，NODE_TEST_CONTEXT 会让里层 node --test 永远退出 0，真实运行时没有这个变量
  const { NODE_TEST_CONTEXT, ...env } = process.env
  return spawnSync(process.execPath, [RUNNER, specPath], { cwd: dir, env, encoding: 'utf8', timeout: 60_000 })
}
const testBody = "import test from 'node:test'\nimport assert from 'node:assert/strict'\nimport { sum } from './sum.mjs'\ntest('empty', () => assert.equal(sum([]), 0))\n"

test('执行器：有效补测退出 0，植入错误后还原源码、删掉补测文件', () => {
  const dir = project()
  const before = readFileSync(join(dir, 'sum.mjs'), 'utf8')
  const r = runSpec(dir, { filePath: 'sum.argus.test.mjs', content: testBody, mutation: { file: 'sum.mjs', find: ', 0)', replace: ', 1)' } })
  assert.equal(r.status, 0, r.stdout + r.stderr)
  assert.match(r.stdout, /✔ 空列表求和为 0（植入错误后能发现问题）/)
  assert.equal(readFileSync(join(dir, 'sum.mjs'), 'utf8'), before, '源码必须还原')
  assert.ok(!existsSync(join(dir, 'sum.argus.test.mjs')), '补测文件必须删掉')
})

test('执行器：测试命令在子目录里跑时，传给它的路径去掉前缀', () => {
  const dir = project()
  const r = runSpec(dir, { filePath: 'pkg/sum.argus.test.mjs', runFileStrip: 'pkg/', runCmd: ['node', '-e', 'process.exit(process.argv[1] === "sum.argus.test.mjs" ? 0 : 1)', '{file}'], content: 'x', mutation: { file: 'sum.mjs', find: ', 0)', replace: ', 1)' } })
  assert.equal(r.status, 4, '命令拿到去掉前缀的路径就会通过；植入错误后仍通过，所以判无效')
})

test('执行器：植入错误后仍通过或对不上源码 → 退出 4（无效，丢弃）', () => {
  const dir = project()
  const r1 = runSpec(dir, { filePath: 'sum.argus.test.mjs', content: testBody, mutation: { file: 'sum.mjs', find: '(a, b)', replace: '(a, b )' } })
  assert.equal(r1.status, 4, r1.stdout)
  assert.match(r1.stdout, /植入错误后测试仍然通过/)
  const r2 = runSpec(dir, { filePath: 'sum.argus.test.mjs', content: testBody, mutation: { file: 'sum.mjs', find: 'not-in-file', replace: 'x' } })
  assert.equal(r2.status, 4)
  assert.ok(!existsSync(join(dir, 'sum.argus.test.mjs')))
})

test('执行器：在当前代码上失败 → 退出 1 并输出 ✖；和已有文件重名 → 退出 2 且不覆盖', () => {
  const dir = project()
  const failing = testBody.replace('sum([]), 0', 'sum([]), 5')
  const r = runSpec(dir, { filePath: 'tests/argus/sum.test.mjs', content: failing.replace("'./sum.mjs'", "'../../sum.mjs'"), mutation: { file: 'sum.mjs', find: ', 0)', replace: ', 1)' } })
  assert.equal(r.status, 1)
  assert.match(r.stdout, /✖ 空列表求和为 0/)
  assert.ok(!existsSync(join(dir, 'tests')), '执行器新建的空目录也要删掉')
  const r2 = runSpec(dir, { filePath: 'sum.mjs', content: 'x', mutation: { file: 'sum.mjs', find: 'a', replace: 'b' } })
  assert.equal(r2.status, 2)
  assert.ok(readFileSync(join(dir, 'sum.mjs'), 'utf8').includes('reduce'))
})

test('补测确认的问题只记拿不准；和其它检查一起确认时，只按其它检查提 issue', () => {
  const aiSteps = [{ id: 'ai-test-1', aiGenerated: true }]
  const onlyAi = toTrialDecision({ outcome: 'defect', failureKeys: ['ai-test-1::空列表'], failedSteps: ['ai-test-1'] }, aiSteps)
  assert.equal(onlyAi.outcome, 'needs_review')
  assert.equal(onlyAi.aiFound, true)
  const mixed = toTrialDecision({ outcome: 'defect', failureKeys: ['unit::a', 'ai-test-1::空列表'], failedSteps: ['unit', 'ai-test-1'], failedTests: ['a', '空列表'] }, aiSteps)
  assert.equal(mixed.outcome, 'defect')
  assert.deepEqual(mixed.failureKeys, ['unit::a'])
  assert.deepEqual(mixed.failedSteps, ['unit'])
  assert.deepEqual(mixed.failedTests, ['a'])
  const perf = toTrialDecision({ outcome: 'defect', failureKeys: ['bench::*'], failedSteps: ['bench'] }, [{ id: 'bench', nameZh: '性能基准', trialOnly: true }])
  assert.equal(perf.outcome, 'needs_review')
  assert.equal(perf.aiFound, false)
  assert.match(perf.reasonZh, /^性能基准在合并后失败/)
  const passed = { outcome: 'passed' }
  assert.equal(toTrialDecision(passed, aiSteps), passed)
})

// ── 分级补测上限 ──
import * as T from '../src/aitests.mjs'
const F = (filename, a = 0, d = 0) => ({ filename, additions: a, deletions: d })
test('ai-test tier: docs/config-only → none, ci/tests/deps → S', () => {
  assert.equal(T.aiTestTier({ files: [F('README.md', 10), F('CHANGELOG.md', 3), F('config/app.yaml', 2)], env: {} }).tier.tier, 'none')
  assert.equal(T.aiTestTier({ files: [F('src/x.ts', 1)], riskClass: 'A', env: {} }).tier.tier, 'S') // product source wins over class A label
  const ci = T.aiTestTier({ files: [F('.github/workflows/ci.yml', 400)], env: {} })
  assert.deepEqual([ci.tier.type, ci.tier.tier, ci.caps.maxCalls, ci.caps.maxTestsPerCall, ci.caps.maxWallMin], ['ci', 'S', 1, 2, 10])
  assert.equal(T.aiTestTier({ files: [F('tests/a.test.mjs', 900)], env: {} }).tier.type, 'tests')
  assert.equal(T.aiTestTier({ files: [F('package-lock.json', 5000)], env: {} }).tier.type, 'deps')
})
test('ai-test tier: product size thresholds, lockfiles/generated excluded', () => {
  const t = (files) => T.aiTestTier({ files, env: {} })
  assert.equal(t([F('src/a.ts', 30, 19)]).tier.tier, 'S')
  assert.equal(t([F('src/a.ts', 30, 20)]).tier.tier, 'M')
  assert.equal(t([F('server/a.py', 299)]).tier.tier, 'M')
  assert.equal(t([F('server/a.py', 300)]).tier.tier, 'L')
  assert.equal(t([F('plugin/a.js', 1000)]).tier.tier, 'L')
  assert.equal(t([F('plugin/a.js', 1001)]).tier.tier, 'XL')
  assert.equal(t(Array.from({ length: 31 }, (_, i) => F(`src/f${i}.ts`, 1))).tier.tier, 'XL')
  const mixed = t([F('src/a.ts', 10), F('package-lock.json', 9000), F('dist/bundle.min.js', 5000), F('tests/a.test.ts', 800)])
  assert.deepEqual([mixed.tier.tier, mixed.tier.product_lines, mixed.tier.files, mixed.tier.type], ['S', 10, 4, 'product'])
  assert.deepEqual(t([F('src/a.ts', 2000)]).caps, { enabled: true, maxCalls: 4, maxTestsPerCall: 4, maxWallMin: 40 })
})
test('ai-test tier: env override and daily-quota degrade', () => {
  const o = T.aiTestTier({ files: [F('src/a.ts', 2000)], env: { ARGUS_AITEST_MAX_CALLS: '1' } })
  assert.equal(o.caps.maxCalls, 1); assert.match(o.tier.reason, /ARGUS_AITEST_MAX_CALLS=1/)
  const base = T.aiTestTier({ files: [F('src/a.ts', 500)], env: {} })
  const ok = T.degradeForQuota(base, { cap: 50, used: 10 })
  assert.deepEqual([ok.caps.maxCalls, ok.tier.degraded, ok.tier.quota_remaining], [3, false, 40])
  const d = T.degradeForQuota(base, { cap: 50, used: 49 })
  assert.deepEqual([d.caps.maxCalls, d.tier.degraded, d.tier.degraded_from_calls], [1, true, 3])
  assert.match(d.tier.reason, /degraded/)
  assert.equal(T.degradeForQuota(base, { cap: 50, used: 50 }).caps.maxCalls, 0)
})
