import assert from 'node:assert/strict'
import test from 'node:test'

// AbortSignal.timeout / setTimeout require an integer delay (Node throws ERR_OUT_OF_RANGE on 299999.5).
// AI-test remaining budget used to pass left/2 straight through; odd remainders must be floored.

test('AI-test timeoutMs from odd remaining budget is an integer (regression #16)', () => {
  const left = 599_999 // odd; left/2 === 299999.5
  const timeoutMs = Math.max(1, Math.floor(Math.min(8 * 60_000, left / 2)))
  assert.equal(timeoutMs, 299_999)
  assert.ok(Number.isInteger(timeoutMs))
  // Must not throw:
  const ac = AbortSignal.timeout(timeoutMs)
  assert.ok(ac)
})

test('AbortSignal.timeout rejects non-integer delay (documents the Node constraint)', () => {
  assert.throws(() => AbortSignal.timeout(299999.5), (err) => err?.code === 'ERR_OUT_OF_RANGE' || /integer/i.test(String(err)))
})
