// AI 补测回包是空串 / 半截 JSON 时：以前 JSON.parse 直接抛「Unexpected end of JSON input」（PR #966，2026-10-14），
// 看不出原因。现在给出明确原因 + finish_reason，并标 retryable。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

process.env.ARGUS_ROOT = mkdtempSync(join(tmpdir(), 'argus-aitest-empty-'))
const { parseModelJson, generateTargetedTests } = await import('../src/aitests.mjs')

test('parseModelJson: empty content → not ok, kind empty, keeps finish_reason', () => {
  const r = parseModelJson({ choices: [{ finish_reason: 'length', message: { content: '' } }] })
  assert.equal(r.ok, false)
  assert.equal(r.kind, 'empty')
  assert.equal(r.finish_reason, 'length')
  assert.match(r.reasonZh, /空内容/)
  assert.match(r.reasonZh, /finish_reason=length/)
  assert.doesNotMatch(r.reasonZh, /Unexpected end of JSON input/)
})

test('parseModelJson: truncated JSON → not ok, kind truncated', () => {
  const r = parseModelJson({ choices: [{ finish_reason: 'length', message: { content: '{"tests": [{"kind": "cov' } }] })
  assert.equal(r.ok, false)
  assert.equal(r.kind, 'truncated')
  assert.match(r.reasonZh, /JSON 不完整/)
})

test('parseModelJson: missing choices stays an empty object (unchanged behaviour); valid JSON parses', () => {
  assert.deepEqual(parseModelJson({}), { ok: true, value: {}, finish_reason: null })
  const r = parseModelJson({ choices: [{ finish_reason: 'stop', message: { content: '{"tests":[]}' } }] })
  assert.equal(r.ok, true)
  assert.deepEqual(r.value, { tests: [] })
})

test('generateTargetedTests: empty model content → status error, clear reason; length already retried at a higher cap is not retryable', async () => {
  const fetchImpl = async () => ({ ok: true, status: 200, json: async () => ({ choices: [{ finish_reason: 'length', message: { content: '' } }], usage: { total_tokens: 16000 } }) })
  const consumed = []
  const quota = { check: () => ({ ok: true }), consume: (k, t) => consumed.push([k, t]) }
  const profile = { aiTests: { frameworkZh: 'vitest', pathGlobs: ['src/**/*.ai.test.ts'], run: ['x'] }, steps: [] }
  const gen = await generateTargetedTests({ db: null, secrets: { ARGUS_LLM_BASE_URL: 'http://127.0.0.1:9', ARGUS_LLM_API_KEY: 'k' }, riskClass: 'B',
    pr: { title: 't', body: '' }, files: [], profile, gaps: ['boundary'], quota, fetchImpl, netRetryDelayMs: 0, sleep: async () => {} })
  assert.equal(gen.status, 'error')
  assert.equal(gen.retryable, false)
  assert.equal(gen.finish_reason, 'length')
  assert.match(gen.reasonZh, /模型返回空内容，finish_reason=length/)
  assert.deepEqual(gen.tests, [])
  assert.deepEqual(consumed, [['ai-test', 16000], ['ai-test', 16000]])
})

// finish_reason=length 表示推理已经把 max_completion_tokens 用完。nightly 以前会再开一轮 generateTargetedTests，
// 请求体不变（仍是 16_000），等于白打（#2059 第 3 轮截断后第 4 轮仍 16k；#2010 最后一轮截断后整段 ai_tests=error）。
// 重试必须换更高的上限，且发生在同一次 generateTargetedTests 里，这样最后一轮被截断时也还有一次有效机会。
test('generateTargetedTests: finish_reason=length retries with a higher max_completion_tokens, not the same request', async () => {
  const bodies = []
  const fetchImpl = async (_url, init) => {
    const body = JSON.parse(init.body)
    bodies.push(body)
    if (bodies.length === 1) {
      return { ok: true, status: 200, json: async () => ({ choices: [{ finish_reason: 'length', message: { content: '{"tests": [{"kind": "cov' } }], usage: { total_tokens: 40054 } }) }
    }
    return { ok: true, status: 200, json: async () => ({ choices: [{ finish_reason: 'stop', message: { content: '{"tests":[]}' } }], usage: { total_tokens: 100 } }) }
  }
  const consumed = []
  const quota = { check: () => ({ ok: true }), consume: (k, t) => consumed.push([k, t]) }
  const profile = { aiTests: { frameworkZh: 'vitest', pathGlobs: ['src/**/*.ai.test.ts'], run: ['x'] }, steps: [] }
  const gen = await generateTargetedTests({ db: null, secrets: { ARGUS_LLM_BASE_URL: 'http://127.0.0.1:9', ARGUS_LLM_API_KEY: 'k' }, riskClass: 'B',
    pr: { title: 't', body: '' }, files: [], profile, gaps: ['boundary'], quota, fetchImpl, netRetryDelayMs: 0, sleep: async () => {} })
  assert.ok(bodies.length >= 2, `length 截断后应当再请求一次（实际 ${bodies.length} 次）`)
  const first = bodies[0].max_completion_tokens
  const second = bodies[1].max_completion_tokens
  assert.equal(first, 16_000)
  assert.ok(second > first, `重试的 max_completion_tokens 必须高于第一次（${first} → ${second}），不能再用同一个上限`)
  assert.notEqual(JSON.stringify(bodies[0]), JSON.stringify(bodies[1]), '重试不能是同一个请求')
  assert.equal(gen.status, 'empty')
  assert.equal(gen.reasonZh, 'AI 没写出可用的测试')
  assert.deepEqual(consumed, [['ai-test', 40054], ['ai-test', 100]])
})
