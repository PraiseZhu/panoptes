import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createFailureParser } from '../src/exec.mjs'
import { decideOutcome } from '../src/decide.mjs'

// 2026-10-30 nightly PR-1454 h1/logs/unit.log（去掉 ANSI）。vitest 默认 reporter 把「同一行断言失败的
// it.each 用例」合成一个诊断块：块内先连续打 N 个 FAIL 头，再给一份共同的 AssertionError。
// 报告里这些用例的 failure_type 是 unknown，同块最后一个却是 assertion（见 reports/merged/PR-1454.json
// failed_checks：...：report-open 与 ...：report-fstat 都是 unknown，而 ...：parent-close 是 assertion）。
const FILE = 'scripts/ops/review-capacity-check/workspace-creation.native.test.mjs'
const RETRY = '目录创建后异常清理原空目录并允许重试'
const KEEP = '准备失败保留无法确认的对象并报告'

const GROUPED_LOG = ` RUN  v3.2.4 /workspace/mivo-qa/argus-root/cache/runs/n1454-h1-mvukk45n/src

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[10/30]⎯

 FAIL  ${FILE} > ${RETRY}：open
 FAIL  ${FILE} > ${RETRY}：fstat
 FAIL  ${FILE} > ${RETRY}：owner
 FAIL  ${FILE} > ${RETRY}：mode
 FAIL  ${FILE} > ${RETRY}：report-open
 FAIL  ${FILE} > ${RETRY}：report-fstat
 FAIL  ${FILE} > ${RETRY}：parent-close
AssertionError: expected false to be true // Object.is equality

- Expected
+ Received

- true
+ false

 ❯ ${FILE}:139:24
    137|   expect(result.failed).toBe(true)
    138|   expect(result.targetExists).toBe(false)
    139|   expect(result.retry).toBe(true)
       |                        ^
    140|   expect(result.handlesClosed).toBe(true)
    141| })

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[11/30]⎯

 FAIL  ${FILE} > ${KEEP}：replace
 FAIL  ${FILE} > ${KEEP}：link
 FAIL  ${FILE} > ${KEEP}：nonempty
AssertionError: expected false to be true // Object.is equality

- Expected
+ Received

- true
+ false

 ❯ ${FILE}:146:31
    144|   const result = probe(fault)
    145|   expect(result.failed).toBe(true)
    146|   expect(result.targetExists).toBe(true)
       |                               ^
    147|   expect(result.retained).toBe(true)
    148|   expect(result.handlesClosed).toBe(true)

 Tests 10 failed | 133 passed (143)
 Duration  12.30s
`

const RETRY_CASES = ['open', 'fstat', 'owner', 'mode', 'report-open', 'report-fstat', 'parent-close']
const KEEP_CASES = ['replace', 'link', 'nonempty']

const parse = (text) => { const p = createFailureParser(); p.push('stderr', text); return p.finish() }

test('vitest 一个诊断块覆盖多个 it.each 失败用例时，块内每个用例都拿到同一个失败类型', () => {
  const r = parse(GROUPED_LOG)
  assert.equal(r.failureParseStatus, 'complete', r.failureParseStatus)
  const typeOf = (name) => r.failureDetails.find((f) => f.name === name)?.type
  for (const kase of RETRY_CASES) {
    assert.equal(typeOf(`${FILE} > ${RETRY}：${kase}`), 'assertion', `${RETRY}：${kase} 与同块其余用例共用同一份 AssertionError 诊断`)
  }
  for (const kase of KEEP_CASES) {
    assert.equal(typeOf(`${FILE} > ${KEEP}：${kase}`), 'assertion', `${KEEP}：${kase} 与同块其余用例共用同一份 AssertionError 诊断`)
  }
})

test('同块类型认全后，合并前版本同样失败的用例才能确认为老失败，不退回待核实', () => {
  const parsed = parse(GROUPED_LOG)
  assert.deepEqual(parsed.failures.length, 10)
  const step = (extra = {}) => ({
    id: 'unit', nameZh: '单元测试', kind: 'product', ok: false, exitCode: 1, signal: null, timedOut: false,
    failures: parsed.failures, failureDetails: parsed.failureDetails, failureParseStatus: 'complete',
    testOutcomeStatus: 'complete',
    testOutcomes: [
      ...parsed.failures.map((n) => ({ caseId: n, failureName: n, status: 'failed' })),
      { caseId: `${FILE} > 健康用例`, failureName: '健康用例', status: 'passed' },
    ],
    ...extra,
  })
  const install = { id: 'install', nameZh: '安装依赖', kind: 'setup', ok: true, exitCode: 0 }
  const d = decideOutcome({ first: [install, step()], rerun: [install, step()], base: [install, step()], latestIsSame: true })
  assert.equal(d.outcome, 'passed_known_failures', `${d.outcome} / ${d.reasonZh} / ${JSON.stringify(d.unverifiedFailures)}`)
  assert.equal(d.knownFailureCount, 10)
})
