import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { chat, codeReview, responseFormatFor, LLM_MODEL, REVIEW_FALLBACK_MODEL } from '../src/mingqa.mjs'
import { verifyReviewFinding, VERIFY_RESPONSE_SCHEMA } from '../src/review-verify.mjs'
import { VERIFY_MODEL } from '../src/nightly.mjs'

// 10-29: 复核模型 anthropic/* 发 json_object → 网关 400「Anthropic structured output requires json_schema」，P1/P2 复核全失败
const secrets = { ARGUS_LLM_BASE_URL: 'http://gw.invalid/v1', ARGUS_LLM_API_KEY: 'k' }
const reply = (content) => new Response(JSON.stringify({ choices: [{ message: { content } }], usage: { total_tokens: 3 } }), { status: 200 })
const capture = () => { const bodies = []; return { bodies, fetchImpl: async (_u, init) => { bodies.push(JSON.parse(init.body)); return reply('{"action":"final","verdict":"not-reachable"}') } } }
const msgs = [{ role: 'user', content: 'reply json' }]

test('anthropic verify model is sent response_format json_schema with the verify schema', async () => {
  const { bodies, fetchImpl } = capture()
  const res = await chat({ secrets, messages: msgs, model: VERIFY_MODEL, schema: VERIFY_RESPONSE_SCHEMA, fetchImpl, sleep: async () => {} })
  assert.equal(res.ok, true)
  assert.equal(bodies.length, 1)
  assert.equal(bodies[0].response_format.type, 'json_schema')
  assert.equal(bodies[0].response_format.json_schema.name, 'review_verify_step')
  assert.deepEqual(bodies[0].response_format.json_schema.schema, VERIFY_RESPONSE_SCHEMA.schema)
  assert.deepEqual(bodies[0].response_format.json_schema.schema.properties.action.enum, ['read', 'grep', 'ls', 'final'])
})

test('verifyReviewFinding passes the verify schema to chat', async () => {
  const seen = []
  const stub = async (a) => { seen.push(a); return { ok: true, json: { action: 'final', verdict: 'not-reachable' } } }
  const r = await verifyReviewFinding({ secrets, finding: { file: 'a.ts', line: 1, title: 't' }, reader: {}, context: {}, std: { version: 'x', text: '' }, chat: stub, model: VERIFY_MODEL })
  assert.equal(r.status, 'refuted')
  assert.equal(seen[0].schema, VERIFY_RESPONSE_SCHEMA)
})

test('non-anthropic models keep response_format json_object', async () => {
  for (const model of [LLM_MODEL, REVIEW_FALLBACK_MODEL, 'openai/gpt-6-luna']) {
    const { bodies, fetchImpl } = capture()
    await chat({ secrets, messages: msgs, model, schema: VERIFY_RESPONSE_SCHEMA, fetchImpl, sleep: async () => {} })
    assert.deepEqual(bodies[0].response_format, { type: 'json_object' })
  }
  assert.deepEqual(responseFormatFor('anthropic/x').type, 'json_schema')
})

// 10-29: #1340 审查模型 luna 两次回空 → bad_json，没有备用模型
const pr = { number: 1340, title: 't', user: { login: 'u' }, base: { repo: { full_name: 'o/r' } }, state: 'closed' }
const files = [{ filename: 'a.ts', status: 'modified', additions: 1, deletions: 0, patch: '@@ -1 +1 @@\n+x' }]
const good = { ok: true, json: { status: 'passed', summary: 's', findings: [] }, tokens: 1, ms: 1 }
const bad = { ok: false, error: { kind: 'bad_json', message: 'model returned non-JSON or no usable object (0 chars)' } }
async function run(responses) {
  const calls = [], logs = []
  const stub = async (a) => { calls.push(a.model); return responses.shift() }
  const out = await codeReview({ secrets, pr, files, results: [], profile: {}, outDir: mkdtempSync(join(tmpdir(), 'argus-fb-')), deps: { chat: stub }, log: (m) => logs.push(m) })
  return { out, calls, logs }
}

test('bad_json/empty review triggers exactly one retry with gpt-6-sol and records the fallback', async () => {
  const { out, calls, logs } = await run([bad, { ...good, model: REVIEW_FALLBACK_MODEL }])
  assert.deepEqual(calls, [LLM_MODEL, REVIEW_FALLBACK_MODEL])
  assert.equal(out.status, 'ok')
  assert.equal(out.model, REVIEW_FALLBACK_MODEL)
  assert.deepEqual({ from: out.fallback.from, to: out.fallback.to, ok: out.fallback.ok }, { from: LLM_MODEL, to: REVIEW_FALLBACK_MODEL, ok: true })
  assert.match(logs.join('\n'), /retrying once with openai\/gpt-6-sol/)
})

test('good json review does not retry', async () => {
  const { out, calls } = await run([good])
  assert.deepEqual(calls, [LLM_MODEL])
  assert.equal(out.status, 'ok')
  assert.equal(out.fallback, undefined)
})

test('sol also bad_json -> original bad_json error path (fallback recorded)', async () => {
  const { out, calls } = await run([bad, bad])
  assert.deepEqual(calls, [LLM_MODEL, REVIEW_FALLBACK_MODEL])
  assert.equal(out.status, 'error')
  assert.equal(out.error.kind, 'bad_json')
  assert.equal(out.fallback.ok, false)
})

test('non-bad_json errors do not trigger the fallback', async () => {
  const { out, calls } = await run([{ ok: false, error: { kind: 'http', status: 400, message: 'x' } }])
  assert.deepEqual(calls, [LLM_MODEL])
  assert.equal(out.error.kind, 'http')
})
