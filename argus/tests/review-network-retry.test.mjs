import assert from 'node:assert/strict'
import test from 'node:test'
import { chat } from '../src/mingqa.mjs'

// 10-18: PR #943 / #1081 code review failed with "fetch failed" after bbe1bea — the review call used bare fetch with no network retry.
const secrets = { ARGUS_LLM_BASE_URL: 'http://gw.invalid/v1', ARGUS_LLM_API_KEY: 'k' }
const ok = () => new Response(JSON.stringify({ choices: [{ message: { content: '{"status":"passed","findings":[]}' } }], usage: { total_tokens: 5 } }), { status: 200 })
const netErr = () => Object.assign(new TypeError('fetch failed'), { cause: { code: 'UND_ERR_SOCKET', message: 'other side closed' } })

test('code-review chat retries gateway socket drops in place and succeeds', async () => {
  let calls = 0
  const fetchImpl = async () => { calls++; if (calls < 3) throw netErr(); return ok() }
  const res = await chat({ secrets, messages: [{ role: 'user', content: 'json' }], fetchImpl, netRetryDelayMs: 0, sleep: async () => {} })
  assert.equal(res.ok, true)
  assert.equal(calls, 3)
})

test('code-review chat gives up after network retries on every attempt and reports kind=network', async () => {
  let calls = 0
  const fetchImpl = async () => { calls++; throw netErr() }
  const res = await chat({ secrets, messages: [{ role: 'user', content: 'json' }], fetchImpl, netRetryDelayMs: 0, sleep: async () => {} })
  assert.equal(res.ok, false)
  assert.equal(res.error.kind, 'network')
  assert.equal(calls, 15) // 3 outer attempts x 5 in-place tries
  // 10-19: 10-18 runs only recorded "fetch failed"; keep the undici cause and elapsed time for diagnosis
  assert.match(res.error.message, /^fetch failed \(UND_ERR_SOCKET other side closed\) after \d+ ms$/)
})

test('code-review chat does not retry timeouts in place', async () => {
  let calls = 0
  const fetchImpl = async () => { calls++; throw Object.assign(new Error('timed out'), { name: 'TimeoutError' }) }
  const res = await chat({ secrets, messages: [{ role: 'user', content: 'json' }], fetchImpl, netRetryDelayMs: 0, sleep: async () => {} })
  assert.equal(res.ok, false)
  assert.equal(calls, 2) // timeouts: still only 2 outer attempts (the 3rd is for network errors only), no in-place retry
})
