import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const root = mkdtempSync(join(tmpdir(), 'argus-evidence-'))
process.env.ARGUS_ROOT = root
const { writeEvidence, summarizeStep, toKnownOnlySteps } = await import('../src/control.mjs')
after(() => rmSync(root, { recursive: true, force: true }))

test('证据 v2 留下成功汇总、各轮版本和计划，同时脱敏且重试不覆盖旧事实', () => {
  const sha = 'a'.repeat(40)
  const token = `ghp_${'x'.repeat(25)}`
  const profile = { steps: [{ id: 'unit', nameZh: '单测', kind: 'product', covers: ['unit'], cmd: ['node', '--test', '--token', 'fake-sensitive-value'] }] }
  const result = { id: 'unit', kind: 'product', ok: true, exitCode: 0, timedOut: false, durationMs: 50, requestedSha: sha, checkedOutSha: sha, runLabel: 'm1', commandTemplate: profile.steps[0].cmd, covers: ['unit'], testOutcomeStatus: 'complete', testOutcomes: [{ caseId: 'guard', failureName: 'guard', status: 'passed' }], testOutcomeSource: { checkedOutSha: sha, stepId: 'unit', runId: 'm1' }, tail: `arbitrary output password=hidden-value\n# tests 2\n# pass 2\n# fail 0\n# duration_ms 10\n${token}` }
  const job = { id: 5, repo: 'fixture/repo', pr_number: 8, merge_sha: sha }
  const input = { decision: { outcome: 'passed' }, first: [result], riskClass: 'B', profile, plan: { stepIds: ['unit'], skipped: [] }, triage: { rows: [{ key: 'unit', verdict: 'applicable' }] }, coverage: { applicable: ['unit'], covered: ['unit'], missed: [] } }
  const file = writeEvidence(job, input)
  const path = join(root, 'app/evidence', file)
  const original = readFileSync(path, 'utf8')
  const evidence = JSON.parse(original)
  assert.equal(evidence.schemaVersion, 2)
  assert.match(evidence.profileHash, /^[a-f0-9]{64}$/)
  assert.equal(evidence.executions.first.checkedOutSha, sha)
  assert.equal(evidence.executions.base, null)
  assert.deepEqual(evidence.profileSteps[0].covers, ['unit'])
  assert.ok(evidence.runs.first[0].summary.lines.includes('# tests 2'))
  assert.equal(evidence.runs.first[0].tail, '')
  assert.equal(evidence.runs.first[0].failureDetails, null, '旧输入缺字段，不伪造断言类型')
  assert.equal(evidence.runs.first[0].failureParseStatus, null)
  assert.equal(evidence.runs.first[0].testOutcomeStatus, 'complete')
  assert.equal(evidence.runs.first[0].testOutcomes[0].status, 'passed')
  assert.equal(evidence.runs.first[0].testOutcomeSource.checkedOutSha, sha)
  assert.ok(!original.includes(token) && !original.includes('hidden-value') && !original.includes('fake-sensitive-value'))
  assert.equal(evidence.runs.first[0].commandTemplate.at(-1), '<redacted>')
  assert.equal(statSync(path).mode & 0o777, 0o600)
  const second = writeEvidence(job, { ...input, decision: { outcome: 'needs_review' } })
  assert.notEqual(second, file)
  assert.equal(readFileSync(path, 'utf8'), original)
  assert.equal(JSON.parse(readFileSync(join(root, 'app/evidence', second))).decision.outcome, 'needs_review')
})

test('失败类型与未核实原因保存在 evidence v2，新增字段同样脱敏', () => {
  const token = `ghp_${'y'.repeat(25)}`
  const name = `guard ${token}`
  const r = { id: 'unit', kind: 'product', ok: false, exitCode: 1, timedOut: false, failures: [name], failureDetails: [{ name, type: 'test_timeout' }], failureParseStatus: 'complete', tail: '' }
  const file = writeEvidence({ id: 6 }, {
    decision: { outcome: 'needs_review', unverifiedFailures: [{ key: `unit::${name}`, type: 'test_timeout', phase: 'rerun', reason: 'internal_timeout' }], transientFailureCount: 1, transientFailures: [{ key: `unit::${name}`, type: 'assertion' }] },
    first: [r], rerun: [r], base: [{ ...r, ok: true, exitCode: 0, failures: [], failureDetails: [] }], latest: null,
    profile: { steps: [] }, plan: { stepIds: [] },
  })
  const text = readFileSync(join(root, 'app/evidence', file), 'utf8')
  const evidence = JSON.parse(text)
  assert.equal(evidence.schemaVersion, 2)
  assert.equal(evidence.runs.first[0].failureParseStatus, 'complete')
  assert.equal(evidence.runs.rerun[0].failureDetails[0].type, 'test_timeout')
  assert.equal(evidence.decision.unverifiedFailures[0].reason, 'internal_timeout')
  assert.equal(evidence.decision.transientFailureCount, 1)
  assert.equal(evidence.decision.transientFailures[0].type, 'assertion')
  assert.ok(!evidence.decision.transientFailures[0].key.includes(token))
  assert.ok(!text.includes(token))
})

test('AI 有效性摘要不把失败或只打印通过当成植入错误验证', () => {
  assert.equal(summarizeStep({ aiGenerated: true, ok: false, tail: '✖ failed' }).mutationVerified, false)
  assert.equal(summarizeStep({ aiGenerated: true, ok: true, tail: '✔ first only' }).mutationVerified, false)
  assert.equal(summarizeStep({ aiGenerated: true, ok: true, tail: '✔ test（植入错误后能发现问题）' }).mutationVerified, true)
})

test('已确认旧失败只消费判定器明确给出的步骤，不把混合失败全算覆盖', () => {
  const results = [{ id: 'unit', ok: false }, { id: 'mixed', ok: false }]
  assert.deepEqual(toKnownOnlySteps(results, { outcome: 'needs_review', knownOnlyStepIds: ['unit'] }), ['unit'])
  assert.deepEqual(toKnownOnlySteps(results, { outcome: 'passed_known_failures', knownOnlyStepIds: [] }), [])
})
