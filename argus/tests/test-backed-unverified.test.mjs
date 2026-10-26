import { test } from 'node:test'
import assert from 'node:assert/strict'
import { isTestBacked, annotateTestBacked, selectIssueCandidates } from '../src/nightly.mjs'

// 10-26：报告里 6 条 source=check、status=unverified 的发现带着 test_backed=true（状态后改、布尔值没回写），被当成测试佐证
test('a failed check that is unverified / flaky / timeout is never test-backed, whatever the stored flag says', () => {
  for (const status of ['unverified', 'flaky', 'timeout', 'setup-failed']) assert.equal(isTestBacked({ source: 'check', status, test_backed: true }), false, status)
  for (const status of ['introduced', 'pre-existing', 'fixed-later']) assert.equal(isTestBacked({ source: 'check', status, test_backed: true }), true, status)
  assert.equal(isTestBacked({ source: 'check', status: 'introduced', flaky: true }), false)
  assert.equal(isTestBacked({ status: 'introduced' }), true)                 // very old report, source missing = check
  assert.equal(isTestBacked({ source: 'code-review', test_backed: true }), true)
  assert.equal(isTestBacked({ source: 'code-review' }), false)
})

test('annotate + issue selection drop the stale flag', () => {
  const prs = [{ number: 1, author: 'PraiseZhu', findings: [{ source: 'check', status: 'unverified', test_backed: true, severity: 'P1', fingerprint: 'x' }] }]
  annotateTestBacked(prs)
  assert.equal(prs[0].findings[0].test_backed, false)
  assert.equal(prs[0].test_backed, false)
  assert.equal(selectIssueCandidates({ prs }).candidates.filter((c) => c.kind === 'finding').length, 0)
})
