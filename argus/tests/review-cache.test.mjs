// 可复现的代码审查：同一提交 + diff + 审查提示词 / 版本 + 模型 → 复用缓存的结论（reused + 来源），--fresh-review 强制重审（已有缓存时只做对比，缓存结论有效）；
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
// 统一分级：P3 不输出（只计数）；CONFIRMED + 四项齐全的 P2 才进 findings
const answers = [
  { status: 'passed', summary: 'r1', findings: [{ severity: 'P3', category: 'test-coverage', file: 'src/a.ts', line: 1, title: 'add a test', verdict: 'PLAUSIBLE' }] },
  { status: 'passed', summary: 'r2', findings: [{ severity: 'P2', category: 'correctness', file: 'src/a.ts', line: 1, title: 'something else', trigger: 't', behavior: 'b', impact: 'i', evidence: 'src/a.ts:1', verdict: 'CONFIRMED' }] },
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

test('--fresh-review on a cached commit compares only: cache not overwritten, differing verdict → cached stays authoritative; other commit / diff → new review', async () => {
  calls = []; answer = 1
  const q = memoryQuota({ pool: 'test' })
  const c = await review({ fresh: true, quota: q })
  assert.equal(calls.length, 1, 'fresh review calls the model')
  assert.equal(c.code_review.fresh_requested, true)
  const fc = c.code_review.fresh_comparison
  assert.equal(fc.same, false)
  assert.equal(fc.authoritative, 'cached')
  assert.equal(fc.cache_overwritten, false)
  assert.equal(fc.fresh_verdict.max_severity, 'P2')
  assert.equal(fc.cached_verdict.max_severity, null, 'P3 is not output')
  assert.equal(c.code_review.verdict.max_severity, null, 'report keeps the cached verdict')
  assert.equal(c.code_review.reused, true)
  assert.equal(q.summary().used_by_run['ai-review'], 1, 'the fresh call consumes quota')
  const d = await review()
  assert.equal(calls.length, 1)
  assert.equal(d.code_review.reused, true)
  assert.equal(d.code_review.verdict.max_severity, null, 'cache entry unchanged after a fresh review')
  answer = 0
  const e = await review({ fresh: true })
  assert.equal(calls.length, 2)
  assert.equal(e.code_review.fresh_comparison.same, true)
  assert.equal(e.code_review.verdict.max_severity, null)
  answer = 1
  await review({ sha: 'deadbee2' })
  assert.equal(calls.length, 3, 'different commit → new review')
  await review({ f: [{ ...files[0], patch: '@@ -1 +1 @@\n+export const a = 3' }] })
  assert.equal(calls.length, 4, 'different diff → new review')
})

test('--fresh-review without a cached entry reviews and stores normally', async () => {
  calls = []; answer = 0
  const c = await review({ sha: 'feed005', fresh: true })
  assert.equal(calls.length, 1)
  assert.equal(c.code_review.fresh_comparison, null)
  assert.equal(c.code_review.reused, false)
  const d = await review({ sha: 'feed005' })
  assert.equal(calls.length, 1)
  assert.equal(d.code_review.reused, true)
})

test('cache key components: commit, base, diff hash, prompt hash/version, model, reference context', () => {
  const k = M.reviewCacheKey({ sha: 's', baseSha: 'b', files })
  assert.deepEqual(Object.keys(k).sort(), ['context_hash', 'diff_hash', 'key', 'model', 'prompt_hash', 'prompt_version', 'review_base_sha', 'reviewed_sha'])
  assert.notEqual(M.reviewCacheKey({ sha: 's', baseSha: 'b', files, reference: { rulingsText: 'new ruling' } }).key, k.key, 'a new pre-merge ruling → not reused')
  assert.notEqual(M.reviewCacheKey({ sha: 's', baseSha: 'b', files, model: 'other' }).key, k.key)
  assert.notEqual(M.reviewCacheKey({ sha: 's', baseSha: 'b', files, promptHash: 'p2' }).key, k.key)
  assert.equal(M.reviewCacheKey({ sha: 's', baseSha: 'b', files }).key, k.key)
})
