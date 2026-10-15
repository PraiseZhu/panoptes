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

test('generateTargetedTests: empty model content → status error, retryable, clear reason', async () => {
  const fetchImpl = async () => ({ ok: true, status: 200, json: async () => ({ choices: [{ finish_reason: 'length', message: { content: '' } }], usage: { total_tokens: 16000 } }) })
  const consumed = []
  const quota = { check: () => ({ ok: true }), consume: (k, t) => consumed.push([k, t]) }
  const profile = { aiTests: { frameworkZh: 'vitest', pathGlobs: ['src/**/*.ai.test.ts'], run: ['x'] }, steps: [] }
  const gen = await generateTargetedTests({ db: null, secrets: { ARGUS_LLM_BASE_URL: 'http://127.0.0.1:9', ARGUS_LLM_API_KEY: 'k' }, riskClass: 'B',
    pr: { title: 't', body: '' }, files: [], profile, gaps: ['boundary'], quota, fetchImpl })
  assert.equal(gen.status, 'error')
  assert.equal(gen.retryable, true)
  assert.equal(gen.finish_reason, 'length')
  assert.match(gen.reasonZh, /模型返回空内容，finish_reason=length/)
  assert.deepEqual(gen.tests, [])
  assert.deepEqual(consumed, [['ai-test', 16000]])
})
