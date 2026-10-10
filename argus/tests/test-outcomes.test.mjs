import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, symlinkSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { normalizeTestOutcomes, findTestOutcome } from '../src/test-outcomes.mjs'

const root = mkdtempSync(join(tmpdir(), 'argus-outcomes-'))
process.env.ARGUS_ROOT = root
const { validateTestReport } = await import('../src/config.mjs')
const { newRunDir: createRunDir, runSteps, cleanupRun } = await import('../src/runner.mjs')
const runDirs = []
const newRunDir = (...args) => { const dir = createRunDir(...args); runDirs.push(dir); return dir }
after(async () => {
  for (const dir of runDirs) await cleanupRun(null, dir)
  rmSync(root, { recursive: true, force: true })
})

function vitest(statuses) {
  return { numTotalTests: statuses.length, numPassedTests: statuses.filter((s) => s === 'passed').length,
    numFailedTests: statuses.filter((s) => s === 'failed').length,
    numPendingTests: statuses.filter((s) => ['pending', 'skipped'].includes(s)).length,
    numTodoTests: statuses.filter((s) => s === 'todo').length,
    testResults: [{ name: '/src/a.test.ts', status: statuses.includes('failed') ? 'failed' : 'passed',
      assertionResults: statuses.map((status, i) => ({ title: `case ${i}`, ancestorTitles: ['contract'], status })) }] }
}
const normalize = (report) => normalizeTestOutcomes({ format: 'vitest-json', raw: JSON.stringify(report), sourceRoot: '/src', runId: 'run', stepId: 'unit', checkedOutSha: 'a'.repeat(40) })

test('Vitest 明确区分执行、跳过、todo和缺失，保留原有失败名', () => {
  const r = normalize(vitest(['passed', 'failed', 'pending', 'skipped', 'todo']))
  assert.equal(r.testOutcomeStatus, 'complete')
  assert.deepEqual(r.testOutcomes.map((x) => x.status), ['passed', 'failed', 'skipped', 'skipped', 'todo'])
  assert.equal(findTestOutcome(r, 'a.test.ts > contract > case 0').status, 'passed')
  assert.equal(findTestOutcome(r, 'a.test.ts > contract > case 2').status, 'skipped')
  assert.equal(findTestOutcome(r, 'missing').status, 'unknown')
  assert.equal(r.testOutcomeSource.checkedOutSha, 'a'.repeat(40))
})

test('无终态、计数矛盾、文件加载失败、越界路径、坏JSON不能证明通过', () => {
  const cases = [
    { ...vitest(['passed']), numTotalTests: 2 },
    { ...vitest(['passed']), numPassedTests: undefined },
    { ...vitest(['passed']), unhandledErrors: [{}] },
    { ...vitest([]), testResults: [{ name: '/src/a.test.ts', status: 'failed', assertionResults: [] }] },
    { ...vitest(['passed']), testResults: [{ ...vitest(['passed']).testResults[0], name: '/other/a.test.ts' }] },
  ]
  for (const c of cases) assert.equal(normalize(c).testOutcomeStatus, 'incomplete')
  assert.equal(normalizeTestOutcomes({ format: 'vitest-json', raw: '{' }).testOutcomeStatus, 'incomplete')
  assert.throws(() => validateTestReport({ format: 'vitest-json', path: '${RUN}/../old.json' }))
  assert.throws(() => validateTestReport({ format: 'unknown', path: '${RUN}/result.json' }))
})

test('同名或重复身份不能碰巧匹配到一个通过记录', () => {
  const r = normalize(vitest(['passed']))
  r.testOutcomes.push({ ...r.testOutcomes[0], caseId: 'another-file' })
  assert.equal(findTestOutcome(r, 'a.test.ts > contract > case 0').status, 'unknown')
})

test('真实 Node reporter 不把 skip/todo/suite 当通过，运行器保存同轮证据', async () => {
  const runDir = newRunDir(1, 'node'), src = join(runDir, 'src')
  mkdirSync(src)
  writeFileSync(join(src, 'behavior.test.mjs'), "import {test,describe} from 'node:test'; import assert from 'node:assert/strict'; describe('group',()=>{test('pass',()=>assert.equal(1,1));test('skip',{skip:true},()=>assert.fail());test.todo('todo');});\n")
  const profile = { steps: [{ id: 'unit', kind: 'product', cmd: [process.execPath, '--test', '--test-reporter=spec', '--test-reporter=${NODE_TEST_REPORTER}', '--test-reporter-destination=stdout', '--test-reporter-destination=${TEST_REPORT}', 'behavior.test.mjs'], testReport: { format: 'node-events', path: '${RUN}/reports/node.jsonl' } }] }
  const [r] = await runSteps(profile, src, runDir, { execution: { checkedOutSha: 'b'.repeat(40) } })
  assert.equal(r.ok, true, r.tail)
  assert.equal(r.testOutcomeStatus, 'complete', JSON.stringify(r))
  assert.equal(findTestOutcome(r, 'pass').status, 'passed')
  assert.equal(findTestOutcome(r, 'skip').status, 'skipped')
  assert.equal(findTestOutcome(r, 'todo').status, 'todo')
  assert.equal(findTestOutcome(r, 'group').status, 'unknown')
  assert.equal(r.testOutcomeSource.checkedOutSha, 'b'.repeat(40))
  const saved = readFileSync(join(runDir, 'reports/node.jsonl'), 'utf8')
  assert.ok(!saved.includes('AssertionError') && !saved.includes('stack'))
})

test('旧报告先清除，缺失的新报告不复用上一轮pass', async () => {
  const runDir = newRunDir(2, 'stale'), src = join(runDir, 'src'), reports = join(runDir, 'reports')
  mkdirSync(src); mkdirSync(reports)
  writeFileSync(join(reports, 'old.json'), JSON.stringify(vitest(['passed'])))
  const [r] = await runSteps({ steps: [{ id: 'unit', kind: 'product', cmd: [process.execPath, '-e', 'process.exit(0)'], testReport: { format: 'vitest-json', path: '${RUN}/reports/old.json' } }] }, src, runDir)
  assert.equal(r.testOutcomeStatus, 'incomplete')
  assert.equal(findTestOutcome(r, 'case 0').status, 'unknown')
  rmSync(reports, { recursive: true })
  symlinkSync(root, reports, 'dir')
  await assert.rejects(runSteps({ steps: [{ id: 'unit', kind: 'product', cmd: ['x'], testReport: { format: 'vitest-json', path: '${RUN}/reports/escape.json' } }] }, src, runDir), /symlink/)
})

test('金丝雀实际npm命令可接收两个reporter并输出执行证据', async () => {
  const runDir = newRunDir(3, 'npm'), src = join(runDir, 'src')
  mkdirSync(src)
  writeFileSync(join(src, 'package.json'), JSON.stringify({ type: 'module', scripts: { test: 'node --test' } }))
  writeFileSync(join(src, 'sum.test.mjs'), "import {test} from 'node:test';import assert from 'node:assert/strict';test('sum works',()=>assert.equal(1+1,2));\n")
  const profile = JSON.parse(readFileSync(new URL('../config/profiles/argus-canary.json', import.meta.url)))
  const [r] = await runSteps({ steps: profile.steps.filter((s) => s.id === 'unit') }, src, runDir)
  assert.equal(r.ok, true, r.tail)
  assert.equal(r.testOutcomeStatus, 'complete', JSON.stringify(r))
  assert.equal(findTestOutcome(r, 'sum works').status, 'passed')
})
