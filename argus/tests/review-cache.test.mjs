// 可复现的代码审查：同一提交 + diff + 审查提示词 / 版本 + 模型 → 复用缓存的结论（reused + 来源），--fresh-review 强制重审；
// 请求带 temperature 0（+ 固定 seed）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const root = mkdtempSync(join(tmpdir(), 'argus-review-cache-'))
process.env.ARGUS_ROOT ??= root
process.env.ARGUS_REVIEW_CACHE_DIR = join(root, 'review-verdicts')
const N = await import('../src/nightly.mjs')
const M = await import('../src/mingqa.mjs')
const { memoryQuota } = await import('../src/quota.mjs')

const files = [{ filename: 'src/a.ts', status: 'modified', additions: 1, deletions: 0, patch: '@@ -1 +1 @@\n+export const a = 2' }]
let calls = []
let answer = 0
const answers = [
  { status: 'passed', summary: 'r1', findings: [{ severity: 'P3', category: 'test-coverage', file: 'src/a.ts', line: 1, title: 'add a test', verdict: 'PLAUSIBLE' }] },
  { status: 'passed', summary: 'r2', findings: [{ severity: 'P2', category: 'correctness', file: 'src/a.ts', line: 1, title: 'something else', verdict: 'PLAUSIBLE' }] },
]
globalThis.fetch = async (url, init) => {
  calls.push(JSON.parse(init.body))
  const content = JSON.stringify(answers[Math.min(answer++, answers.length - 1)])
  return { ok: true, status: 200, json: async () => ({ model: 'openai/gpt-6-luna', usage: { total_tokens: 100 }, choices: [{ message: { content } }] }) }
}
const secrets = { ARGUS_LLM_BASE_URL: 'https://gateway.invalid/v1', ARGUS_LLM_API_KEY: 'k' }
async function review({ sha = 'c0ffee1', fresh = false, f = files, quota = memoryQuota({ pool: 'test' }) } = {}) {
  const out = { quota_capped: [], findings: [] }
  const health = { notes: [], llm_errors: [], qa_tool_failures: [] }
  await N.runCodeReview({ out, repo: 'xindong/mivo-canvas-plugin', pr: { number: 893, title: 't', user: { login: 'x' } }, files: f, prDiff: { files: f }, sha, baseSha: 'base111', results: [], profile: {}, evDir: mkdtempSync(join(root, 'ev-')), secrets, health, number: 893, quota, opts: { freshReview: fresh, runId: 'run-1' } })
  return out
}

test('second review of the same commit + diff + prompt + model reuses the verdict (no model call, no quota), identical findings', async () => {
  calls = []; answer = 0
  const q = memoryQuota({ pool: 'test' })
  const a = await review({ quota: q })
  assert.equal(calls.length, 1)
  assert.equal(calls[0].temperature, 0, 'temperature 0 sent')
  assert.equal(typeof calls[0].seed, 'number')
  assert.equal(a.code_review.reused, false)
  const b = await review({ quota: q })
  assert.equal(calls.length, 1, 'no second model call')
  assert.equal(b.code_review.reused, true)
  assert.equal(b.code_review.reused_from.pr, 893)
  assert.equal(b.code_review.reused_from.run_id, 'run-1')
  assert.equal(b.code_review.reused_from.reviewed_sha, 'c0ffee1')
  assert.deepEqual(b.code_review.verdict, a.code_review.verdict)
  assert.deepEqual(b.findings.map((f) => [f.severity, f.fingerprint]), a.findings.map((f) => [f.severity, f.fingerprint]))
  assert.equal(b.code_review.cache_key.key, a.code_review.cache_key.key)
  assert.equal(q.summary().used_by_run['ai-review'], 1, 'reuse does not consume quota')
})

test('--fresh-review forces a new model call and replaces the cached entry; other commit / diff → new review', async () => {
  calls = []; answer = 1
  const c = await review({ fresh: true })
  assert.equal(calls.length, 1)
  assert.equal(c.code_review.reused, false)
  assert.equal(c.code_review.fresh_requested, true)
  assert.equal(c.code_review.verdict.max_severity, 'P2')
  const d = await review()
  assert.equal(calls.length, 1)
  assert.equal(d.code_review.reused, true)
  assert.equal(d.code_review.verdict.max_severity, 'P2', 'the fresh verdict is the one reused afterwards')
  await review({ sha: 'deadbee2' })
  assert.equal(calls.length, 2, 'different commit → new review')
  await review({ f: [{ ...files[0], patch: '@@ -1 +1 @@\n+export const a = 3' }] })
  assert.equal(calls.length, 3, 'different diff → new review')
})

test('cache key components: commit, base, diff hash, prompt hash/version, model', () => {
  const k = M.reviewCacheKey({ sha: 's', baseSha: 'b', files })
  assert.deepEqual(Object.keys(k).sort(), ['diff_hash', 'key', 'model', 'prompt_hash', 'prompt_version', 'review_base_sha', 'reviewed_sha'])
  assert.notEqual(M.reviewCacheKey({ sha: 's', baseSha: 'b', files, model: 'other' }).key, k.key)
  assert.notEqual(M.reviewCacheKey({ sha: 's', baseSha: 'b', files, promptHash: 'p2' }).key, k.key)
  assert.equal(M.reviewCacheKey({ sha: 's', baseSha: 'b', files }).key, k.key)
})
