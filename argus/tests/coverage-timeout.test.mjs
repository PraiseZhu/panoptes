import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { coverageTimeoutMin } from '../src/nightly.mjs'

// 10-25 #1166：覆盖率轮次和首轮并行，单测 120 分钟超时被杀 → coverage_gate unavailable（「unit step timed out」）
test('coverage round gets a relaxed timeout (parallel ×2, serial ×1.5, capped, env override)', () => {
  assert.equal(coverageTimeoutMin({ timeoutMin: 120 }, true, {}), 240)
  assert.equal(coverageTimeoutMin({ timeoutMin: 120 }, false, {}), 180)
  assert.equal(coverageTimeoutMin({ timeoutMin: 200 }, true, {}), 300)
  assert.equal(coverageTimeoutMin({}, true, {}), 40)
  assert.equal(coverageTimeoutMin({ timeoutMin: 120 }, true, { ARGUS_COVERAGE_TIMEOUT_FACTOR: '3', ARGUS_COVERAGE_TIMEOUT_MAX_MIN: '600' }), 360)
  assert.equal(coverageTimeoutMin({ timeoutMin: 500 }, true, {}), 500)   // never below the step's own timeout
})

test('the cov round runs with the relaxed profile, the judged first round keeps the original one', () => {
  const src = readFileSync(new URL('../src/nightly.mjs', import.meta.url), 'utf8')
  assert.match(src, /executeOnSha\(\{ \.\.\.common, profile: covProfile\(/)
  assert.match(src, /executeOnSha\(\{ \.\.\.common, label: 'h1', sha/)
})
