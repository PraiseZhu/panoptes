import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-fb-root-'))
process.env.ARGUS_VERIFY_CACHE_DIR ??= join(process.env.ARGUS_ROOT, 'review-verifications')
process.env.ARGUS_REVIEW_CACHE_DIR ??= join(process.env.ARGUS_ROOT, 'review-verdicts')
const { chat, codeReview, responseFormatFor, LLM_MODEL, REVIEW_FALLBACK_MODEL } = await import('../src/mingqa.mjs')
const { verifyReviewFinding, settleVerdict, VERIFY_RESPONSE_SCHEMA } = await import('../src/review-verify.mjs')
const N = await import('../src/nightly.mjs')
const { VERIFY_MODEL } = N
const { memoryQuota } = await import('../src/quota.mjs')

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
  assert.equal(bodies[0].response_format.json_schema.strict, undefined) // non-strict unless the caller sets strict
})

test('verifyReviewFinding passes the verify schema to chat', async () => {
  const seen = []
  const stub = async (a) => { seen.push(a); return { ok: true, json: { action: 'final', verdict: 'not-reachable', reasoning: 'r' } } }
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
const solBad = { ok: false, error: { kind: 'bad_json', message: 'sol: model returned non-JSON or no usable object (7 chars)' } }
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
  const { out, calls } = await run([bad, solBad])
  assert.deepEqual(calls, [LLM_MODEL, REVIEW_FALLBACK_MODEL])
  assert.equal(out.status, 'error')
  assert.equal(out.error.kind, 'bad_json')
  assert.equal(out.error.message, bad.error.message) // the ORIGINAL (luna) error, not sol's
  assert.equal(out.fallback.error.message, solBad.error.message)
  assert.equal(out.fallback.ok, false)
})

test('non-bad_json errors do not trigger the fallback', async () => {
  const { out, calls } = await run([{ ok: false, error: { kind: 'http', status: 400, message: 'x' } }])
  assert.deepEqual(calls, [LLM_MODEL])
  assert.equal(out.error.kind, 'http')
})

// 验收 10-29：nightly 报告要带上 code_review.fallback（成功 / 失败两条路径）
async function nightly(cr) {
  const out = { quota_capped: [], findings: [], pending_verification: [], checks: [] }
  const health = { notes: [], llm_errors: [], qa_tool_failures: [], env_problems: [] }
  await N.runCodeReview({ out, repo: 'o/r', pr: { ...pr, body: '' }, files, prDiff: { files }, sha: 'a'.repeat(40), baseSha: 'b'.repeat(40), results: [], profile: {}, evDir: mkdtempSync(join(tmpdir(), 'argus-fb-ev-')), secrets, health, number: 1340, quota: memoryQuota({ pool: 'test' }), opts: { freshReview: true }, deps: { codeReview: async () => cr } })
  return out
}
test('nightly carries code_review.fallback into the report (ok and error)', async () => {
  const fallback = { from: LLM_MODEL, to: REVIEW_FALLBACK_MODEL, reason: 'x', ok: true }
  const ok = await nightly({ status: 'ok', model: REVIEW_FALLBACK_MODEL, tokens: 1, doc: { status: 'passed', summary: 's', findings: [], unverified: [], residualRisks: [] }, gate: null, reviewPath: '/x', fallback })
  assert.deepEqual(ok.code_review.fallback, fallback)
  const failed = { ...fallback, ok: false, error: solBad.error }
  const err = await nightly({ status: 'error', error: { ...bad.error, fallback: failed }, fallback: failed })
  assert.deepEqual(err.code_review.fallback, failed)
  assert.equal(err.code_review.ran, false)
})

// 验收 10-29 实测：Anthropic json_schema 下 Opus 省掉可选字段，CR-001 最后一步只回了这个（real/cr-001-verify.log call 6）
const RECORDED_CR001_FINAL = { action: 'final', verdict: 'not-reachable' }
const RECORDED_CR001_STEP1 = { actions: [{ action: 'read', path: 'scripts/ops/setup-mivo-review-runner.sh', start: 100, verdict: 'cannot-determine' }, { action: 'grep', pattern: 'check_restricted_user', verdict: 'cannot-determine' }] }
const variants = VERIFY_RESPONSE_SCHEMA.schema.anyOf
const finals = variants.filter((v) => v.properties?.action?.enum?.[0] === 'final')

test('verify schema: per-action variants with required; final requires verdict+reasoning, reachable also call_path', () => {
  assert.ok(Array.isArray(variants) && variants.length >= 5)
  for (const v of variants) assert.ok(v.required?.length, 'every variant has required')
  assert.equal(finals.length, 2)
  for (const f of finals) for (const k of ['action', 'verdict', 'reasoning']) assert.ok(f.required.includes(k), `final requires ${k}`)
  const reach = finals.find((f) => f.properties.verdict.enum.includes('reachable'))
  assert.deepEqual(reach.properties.verdict.enum, ['reachable'])
  assert.ok(reach.required.includes('call_path'))
  assert.ok(!finals.find((f) => f !== reach).properties.verdict.enum.includes('reachable'))
  const read = variants.find((v) => v.properties?.action?.enum?.[0] === 'read')
  assert.deepEqual(read.required, ['action', 'path', 'start', 'end'])
  const batch = variants.find((v) => v.properties?.actions)
  assert.deepEqual(batch.required, ['actions'])
  for (const item of batch.properties.actions.items.anyOf) assert.ok(item.required.includes('action'))
  // recorded CR-001 final is missing a field the schema requires
  assert.ok(finals.every((f) => f.required.some((k) => !(k in RECORDED_CR001_FINAL))))
})

test('recorded Anthropic final without reasoning is NOT accepted as refuted (stays pending)', async () => {
  assert.equal(settleVerdict(RECORDED_CR001_FINAL, new Map()).status, 'undetermined')
  assert.equal(settleVerdict({ action: 'final', verdict: 'intended-design' }, new Map()).status, 'undetermined')
  assert.equal(settleVerdict({ action: 'final', verdict: 'intended-design', reasoning: '  ' }, new Map()).status, 'undetermined')
  // end-to-end through verifyReviewFinding with the recorded reply sequence
  const replies = [{ ok: true, json: RECORDED_CR001_STEP1, model: VERIFY_MODEL }, { ok: true, json: RECORDED_CR001_FINAL, model: VERIFY_MODEL }]
  const reader = { read: () => ({ ok: true, text: 'x', path: 'scripts/ops/setup-mivo-review-runner.sh', start: 100, end: 200 }), grep: () => ({ ok: true, text: '(no matches)' }), ls: () => ({ ok: true, text: '' }) }
  const r = await verifyReviewFinding({ secrets, finding: { file: 'scripts/ops/setup-mivo-review-runner.sh', line: 126, title: 'CR-001' }, reader, context: {}, std: { version: 'x', text: '' }, chat: async () => replies.shift(), model: VERIFY_MODEL })
  assert.equal(r.status, 'undetermined')
  assert.notEqual(r.status, 'refuted')
  assert.match(r.status_reason, /without reasoning/)
  // with reasoning it is still refuted
  assert.equal(settleVerdict({ ...RECORDED_CR001_FINAL, reasoning: '组检查覆盖了' }, new Map()).status, 'refuted')
})
