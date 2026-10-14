import assert from 'node:assert/strict'
import test from 'node:test'
import { analyzePr, isTransientGatewayError } from '../src/ai.mjs'

// Regression #16 (PR #950): gateway LiteLLM failover leaked its internal '_retry_last_failed_deployment_id'
// into the Azure request → 400 Unknown parameter. pr-summary had no retry, so one gateway hiccup degraded the run.
const LEAK = "litellm.BadRequestError: AzureException BadRequestError - Unknown parameter: '_retry_last_failed_deployment_id'."

test('isTransientGatewayError classifies gateway-internal leaks, 5xx, 429 as transient; real 400s not', () => {
  assert.equal(isTransientGatewayError(400, LEAK), true)
  assert.equal(isTransientGatewayError(502, ''), true)
  assert.equal(isTransientGatewayError(429, ''), true)
  assert.equal(isTransientGatewayError(400, "Unknown parameter: 'temperature'."), false)
  assert.equal(isTransientGatewayError(400, 'maximum context length exceeded'), false)
  assert.equal(isTransientGatewayError(401, ''), false)
})

const base = () => {
  const consumed = []
  return {
    consumed,
    args: {
      db: null, secrets: { ARGUS_LLM_BASE_URL: 'http://gw.invalid/v1', ARGUS_LLM_API_KEY: 'k' }, riskClass: 'B',
      pr: { title: 't', body: '' }, files: [], profile: {}, results: [], outcome: 'ok', retryDelayMs: 0,
      quota: { check: () => ({ ok: true }), consume: (k, t) => consumed.push([k, t]) },
    },
  }
}
const reply = (status, json) => ({ ok: status < 400, status, json: async () => json })

test('pr-summary retries once on the leaked-internal-param 400 and succeeds', async () => {
  const { args, consumed } = base()
  let calls = 0
  const fetchImpl = async () => (++calls === 1
    ? reply(400, { error: { message: LEAK } })
    : reply(200, { usage: { total_tokens: 5 }, choices: [{ message: { content: '{"summaryZh":"好"}' } }] }))
  const r = await analyzePr({ ...args, fetchImpl })
  assert.equal(calls, 2)
  assert.equal(r.status, 'ok')
  assert.equal(r.summaryZh, '好')
  assert.equal(consumed.length, 2)
})

test('pr-summary does not retry a genuine 400', async () => {
  const { args } = base()
  let calls = 0
  const fetchImpl = async () => { calls++; return reply(400, { error: { message: 'maximum context length exceeded' } }) }
  const r = await analyzePr({ ...args, fetchImpl })
  assert.equal(calls, 1)
  assert.equal(r.status, 'error')
})

test('pr-summary gives up after bounded attempts on persistent transient error', async () => {
  const { args } = base()
  let calls = 0
  const fetchImpl = async () => { calls++; return reply(400, { error: { message: LEAK } }) }
  const r = await analyzePr({ ...args, fetchImpl })
  assert.equal(calls, 3)
  assert.equal(r.status, 'error')
  assert.match(r.reasonZh, /400/)
})
