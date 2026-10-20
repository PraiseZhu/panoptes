// 10-20 #1105：网关（Responses API 转换）要求 response_format=json_object 时输入消息里出现 "json" 这个词，否则 400。
// system 会被转成 instructions，不算 input，所以 user 消息里必须有 json。兜底：公共函数校验 + 扫描所有调用 + 三处调用实测请求体。全部 mock。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-jsonobj-'))
const util = await import('../src/util.mjs')
const { chat } = await import('../src/mingqa.mjs')
const { analyzePr } = await import('../src/ai.mjs')
const { generateTargetedTests } = await import('../src/aitests.mjs')

const SRC = join(dirname(fileURLToPath(import.meta.url)), '../src')
const secrets = { ARGUS_LLM_BASE_URL: 'http://gw.invalid/v1', ARGUS_LLM_API_KEY: 'k' }
const hasJsonInInput = (messages) => messages.some((m) => m.role !== 'system' && /json/i.test(String(m.content ?? '')))
const capture = (content) => { const f = async (_u, init) => { f.bodies.push(JSON.parse(init.body)); return { ok: true, status: 200, json: async () => ({ choices: [{ message: { content }, finish_reason: 'stop' }], usage: { total_tokens: 1 } }) } }; f.bodies = []; return f }

test('json_object 兜底：公共函数 jsonObjectBody 在输入消息没有 json 时拒绝发请求', () => {
  assert.equal(typeof util.jsonObjectBody, 'function', 'util.mjs 应导出 jsonObjectBody')
  assert.throws(() => util.jsonObjectBody({ model: 'm', response_format: { type: 'json_object' }, messages: [{ role: 'system', content: '只输出 JSON' }, { role: 'user', content: '你好' }] }), /json/)
  const body = JSON.parse(util.jsonObjectBody({ model: 'm', response_format: { type: 'json_object' }, messages: [{ role: 'user', content: 'reply in Json' }] }))
  assert.equal(body.response_format.type, 'json_object')
})

test('json_object 兜底：src 里每处 json_object 请求都经过 jsonObjectBody', () => {
  const offenders = readdirSync(SRC).filter((f) => f.endsWith('.mjs') && f !== 'util.mjs')
    .filter((f) => { const s = readFileSync(join(SRC, f), 'utf8'); return /json_object/.test(s) && !/jsonObjectBody\(/.test(s) })
  assert.deepEqual(offenders, [])
})

test('json_object 兜底：pr-summary（analyzePr）请求的 user 消息里有 json', async () => {
  const f = capture('{"summaryZh":"x"}')
  const db = { prepare: () => ({ get: () => ({ calls: 0, tokens: 0 }), run: () => {}, all: () => [] }) }
  const quota = { check: () => ({ ok: true }), consume: () => {} }
  await analyzePr({ db, secrets, riskClass: 'B', pr: { title: 't', body: '' }, files: [], profile: {}, results: [], outcome: 'clean', quota, fetchImpl: f, retryDelayMs: 0, sleep: async () => {} })
  assert.equal(f.bodies.length, 1)
  assert.ok(hasJsonInInput(f.bodies[0].messages))
})

test('json_object 兜底：ai-tests（generateTargetedTests）请求的 user 消息里有 json', async () => {
  const f = capture('{"tests":[]}')
  const quota = { check: () => ({ ok: true }), consume: () => {} }
  await generateTargetedTests({ db: null, secrets, riskClass: 'B', pr: { title: 't', body: '' }, files: [], profile: { aiTests: { frameworkZh: 'node:test', pathGlobs: ['tests/**'] } }, gaps: ['exception'], quota, fetchImpl: f, netRetryDelayMs: 0, sleep: async () => {} })
  assert.equal(f.bodies.length, 1)
  assert.ok(hasJsonInInput(f.bodies[0].messages))
})

test('json_object 兜底：代码审查 chat 输入没有 json 时不发请求，返回 config 错误', async () => {
  const f = capture('{}')
  const res = await chat({ secrets, messages: [{ role: 'system', content: 'JSON only' }, { role: 'user', content: 'hi' }], fetchImpl: f, netRetryDelayMs: 0, sleep: async () => {} })
  assert.equal(res.ok, false); assert.equal(res.error.kind, 'config'); assert.equal(f.bodies.length, 0)
})
