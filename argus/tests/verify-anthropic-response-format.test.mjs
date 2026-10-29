// 10-29（PR #1340）：review-verify 整段落成
//   verifier call failed: http litellm.BadRequestError: NovaException - {"error":{"message":
//   "Anthropic structured output requires json_schema; json_object is not supported.","type":"invalid_request_error", …}}
// 复核默认模型是 anthropic/claude-opus-5-5（nightly.mjs VERIFY_MODEL），而 mingqa chat() 对所有模型都发
// response_format={"type":"json_object"}；Anthropic 的结构化输出只认 json_schema，网关直接 400，复核一条都跑不成。
// 网关按 Anthropic 的规则 fake（不起真网络）：收到 json_object 就 400 + 同一句报错，收到 json_schema 或不发
// response_format 才回 JSON —— 所以修法可以是发 json_schema，也可以是这家模型不发 response_format。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

process.env.ARGUS_VERIFY_CACHE_DIR = mkdtempSync(join(tmpdir(), 'argus-verify-format-'))
delete process.env.ARGUS_VERIFY_MODEL
const { chat } = await import('../src/mingqa.mjs')
const N = await import('../src/nightly.mjs')
const { memoryQuota } = await import('../src/quota.mjs')

const OPUS = 'anthropic/claude-opus-5-5'
const ANTHROPIC_400 = 'Anthropic structured output requires json_schema; json_object is not supported.'
const secrets = { ARGUS_LLM_BASE_URL: 'http://gw.invalid/v1', ARGUS_LLM_API_KEY: 'k' }

// Anthropic 系模型的网关：json_object → 400（原文照抄 #1340 的报错）；json_schema / 不带 response_format → 200
function gateway(content) {
  const bodies = []
  const fetchImpl = async (_url, init) => {
    const body = JSON.parse(init.body)
    bodies.push(body)
    if (/claude|anthropic/i.test(String(body.model ?? '')) && body.response_format?.type === 'json_object') {
      return new Response(JSON.stringify({ error: { message: ANTHROPIC_400, type: 'invalid_request_error', param: null, code: 'invalid_request' } }), { status: 400, headers: { 'content-type': 'application/json' } })
    }
    return new Response(JSON.stringify({ choices: [{ message: { content }, finish_reason: 'stop' }], usage: { total_tokens: 7 }, model: body.model }), { status: 200, headers: { 'content-type': 'application/json' } })
  }
  fetchImpl.bodies = bodies
  return fetchImpl
}

const reader = { read: (p) => ({ ok: true, path: p, start: 1, end: 999, text: `${p}\n1: x` }), grep: () => ({ ok: true, text: '' }), ls: () => ({ ok: true, text: '' }) }
const entry = () => ({ source: 'code-review', test_backed: false, check_id: 'code-review', severity: 'P1', llm_severity: 'P1', verdict: 'CONFIRMED', title: 't', file: 'src/a.ts', line: 3, fingerprint: 'fp-1340', evidence: {} })

test('复核模型 anthropic/claude-opus-5-5 不发 json_object：网关按 Anthropic 规则回 400 时这次调用仍然成功', async () => {
  const f = gateway('{"action":"final","verdict":"not-reachable"}')
  const res = await chat({ secrets, model: OPUS, messages: [{ role: 'system', content: '只回 JSON' }, { role: 'user', content: '先读出错位置附近的代码，只回 JSON' }], fetchImpl: f, netRetryDelayMs: 0, sleep: async () => {} })
  assert.equal(res.ok, true, `复核调用失败：${JSON.stringify(res.error ?? null)}`)
  assert.equal(res.json.action, 'final')
  assert.ok(f.bodies.length >= 1)
  for (const body of f.bodies) assert.notEqual(body.response_format?.type, 'json_object', 'Anthropic 模型收到 json_object 必然 400（应发 json_schema 或不发 response_format）')
})

test('review-verify 走真实 chat 跑完整复核：不再把 #1340 记成 verifier call failed', async () => {
  const realFetch = globalThis.fetch
  const f = gateway('{"action":"final","verdict":"not-reachable"}')
  globalThis.fetch = f
  try {
    const out = { findings: [], pending_verification: [], quota_capped: [], ai_tests: { tests: [] } }
    const health = { llm_errors: [], notes: [] }
    await N.verifyReviewP1s({
      entries: [entry()], out, repo: 'o/r', pr: { number: 1340, title: 't' }, sha: 'a'.repeat(40), profile: {}, secrets, health, number: 1340,
      quota: memoryQuota({ pool: 'test' }), opts: { freshReview: true }, prDiff: { files: [] }, handoffRaw: null, rulingsText: '',
      deps: { reader },
    })
    assert.deepEqual(health.llm_errors, [], `复核仍报错：${JSON.stringify(health.llm_errors)}`)
    assert.equal(out.pending_verification.length, 1)
    assert.equal(out.pending_verification[0].verification.status, 'refuted')
    assert.ok(f.bodies.length >= 1, '复核会话应真的调用过模型')
  } finally {
    globalThis.fetch = realFetch
  }
})

test('其他模型照旧用 json_object（修这一条不能把 JSON 模式整体去掉）', async () => {
  const f = gateway('{"status":"passed","findings":[]}')
  const res = await chat({ secrets, model: 'openai/gpt-6-luna', messages: [{ role: 'user', content: '只回 JSON' }], fetchImpl: f, netRetryDelayMs: 0, sleep: async () => {} })
  assert.equal(res.ok, true, JSON.stringify(res.error ?? null))
  assert.equal(f.bodies[0].response_format?.type, 'json_object')
})
