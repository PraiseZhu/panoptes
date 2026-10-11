// 模型调用额度：按种类分开计数（ai-test / jev / ai-review / pr-summary），生产池与测试池互不占用；
// 用尽 → status capped（显示「额度用尽未补测」），和普通 gap 分开，记进 system_health
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const root = mkdtempSync(join(tmpdir(), 'argus-quota-'))
process.env.ARGUS_ROOT ??= root
const Q = await import('../src/quota.mjs')
const N = await import('../src/nightly.mjs')
const { openDb } = await import('../src/db.mjs')
const { generateTargetedTests } = await import('../src/aitests.mjs')

test('pool: manual → test, event / catch-up → production, --quota-pool overrides', () => {
  assert.equal(Q.resolvePool({ trigger: 'manual' }), 'test')
  assert.equal(Q.resolvePool({ trigger: 'event' }), 'production')
  assert.equal(Q.resolvePool({ trigger: 'catch-up' }), 'production')
  assert.equal(Q.resolvePool({ trigger: 'event', quotaPool: 'test' }), 'test')
  assert.equal(Q.resolvePool({ trigger: 'manual', quotaPool: 'production' }), 'production')
  assert.throws(() => Q.resolvePool({ quotaPool: 'prod' }))
})

test('caps: defaults ai-test 40 / ai-review 70 / jev 150, env per kind and pool, legacy env, quota.json', () => {
  assert.deepEqual(Q.quotaCaps('production', {}).caps, { 'ai-test': 40, jev: 150, 'ai-review': 70, 'pr-summary': 40 })
  const env = { ARGUS_QUOTA_AI_TEST: '5', ARGUS_QUOTA_TEST_AI_REVIEW: '3', ARGUS_AI_DAILY_CAP: '9' }
  assert.equal(Q.quotaCaps('production', env).caps['ai-test'], 5)
  assert.equal(Q.quotaCaps('production', env).caps['ai-review'], 70)
  assert.equal(Q.quotaCaps('production', env).caps['pr-summary'], 9, 'legacy ARGUS_AI_DAILY_CAP still honoured for production')
  assert.equal(Q.quotaCaps('test', env).caps['ai-review'], 3)
  assert.equal(Q.quotaCaps('test', env).caps['pr-summary'], 40, 'legacy env does not touch the test pool')
  const file = join(root, 'quota-file.json')
  writeFileSync(file, JSON.stringify({ production: { jev: 12 }, test: { jev: 2 } }))
  assert.equal(Q.quotaCaps('production', { ARGUS_QUOTA_FILE: file }).caps.jev, 12)
  assert.equal(Q.quotaCaps('test', { ARGUS_QUOTA_FILE: file }).caps.jev, 2)
  assert.equal(Q.quotaCaps('test', { ARGUS_QUOTA_FILE: file, ARGUS_QUOTA_TEST_JEV: '7' }).caps.jev, 7, 'env wins over the file')
})

test('separate counters per kind; the test pool never consumes production quota', () => {
  const db = openDb(join(root, 'q.sqlite'))
  const day = () => '2026-10-11'
  const env = { ARGUS_QUOTA_AI_TEST: '2', ARGUS_QUOTA_TEST_AI_TEST: '1' }
  const prod = Q.createQuota({ db, pool: 'production', env, day })
  const drill = Q.createQuota({ db, pool: 'test', env, day })
  drill.consume('ai-test', 100)
  assert.equal(drill.check('ai-test').ok, false, 'test pool cap 1 reached')
  assert.deepEqual([prod.check('ai-test').ok, prod.check('ai-test').used], [true, 0], 'production untouched by the drill')
  prod.consume('ai-test'); prod.consume('ai-review'); prod.consume('jev')
  assert.equal(prod.check('ai-test').used, 1)
  assert.equal(prod.check('ai-review').used, 1)
  prod.consume('ai-test')
  const c = prod.check('ai-test')
  assert.deepEqual([c.ok, c.used, c.cap], [false, 2, 2])
  assert.equal(prod.check('ai-review').ok, true, 'ai-review has its own counter')
  // another day starts fresh
  assert.equal(Q.createQuota({ db, pool: 'production', env, day: () => '2026-10-12' }).check('ai-test').ok, true)
  const e = prod.markCapped('ai-test', { pr: 893, dimensions: ['boundary'] })
  assert.match(e.message, /额度用尽未补测/)
  assert.deepEqual(prod.summary().capped.map((x) => [x.pr, x.kind, x.pool]), [[893, 'ai-test', 'production']])
  assert.deepEqual(prod.summary().used_by_run, { 'ai-test': 2, jev: 1, 'ai-review': 1, 'pr-summary': 0 })
  db.close()
})

test('generateTargetedTests with an exhausted quota returns capped and makes no model call', async () => {
  const quota = Q.memoryQuota({ pool: 'production', env: { ARGUS_QUOTA_AI_TEST: '0' } })
  const realFetch = globalThis.fetch
  globalThis.fetch = () => { throw new Error('must not call the model') }
  try {
    const r = await generateTargetedTests({ db: null, secrets: { ARGUS_LLM_BASE_URL: 'https://x.invalid', ARGUS_LLM_API_KEY: 'k' }, riskClass: 'B', pr: { title: 't' }, files: [], profile: { aiTests: { pathGlobs: ['**'] } }, gaps: ['boundary'], quota })
    assert.equal(r.status, 'capped')
    assert.equal(r.quota.cap, 0)
  } finally { globalThis.fetch = realFetch }
})

test('code review with an exhausted ai-review quota: status capped, no model call, recorded on the PR', async () => {
  const quota = Q.memoryQuota({ pool: 'test', env: { ARGUS_QUOTA_TEST_AI_REVIEW: '0' } })
  const out = { quota_capped: [], findings: [] }
  const health = { notes: [], llm_errors: [], qa_tool_failures: [] }
  const realFetch = globalThis.fetch
  globalThis.fetch = () => { throw new Error('must not call the model') }
  try {
    await N.runCodeReview({ out, repo: 'r', pr: { number: 893 }, files: [], prDiff: { files: [] }, sha: 'a', baseSha: 'b', results: [], profile: {}, evDir: root, secrets: { ARGUS_LLM_BASE_URL: 'x', ARGUS_LLM_API_KEY: 'k' }, health, number: 893, quota })
  } finally { globalThis.fetch = realFetch }
  assert.equal(out.code_review.status, 'capped')
  assert.equal(out.code_review.display_zh, '额度用尽未补测')
  assert.deepEqual(out.quota_capped.map((c) => [c.kind, c.pool]), [['ai-review', 'test']])
})

const triage = (status, dims) => ({ status, dimensions: dims })
test('dimension_status: AI-test gaps left by the cap are "capped", not "gap"; Jev cap marks undetermined dimensions', () => {
  const profile = { steps: [{ id: 'unit', covers: ['unit'] }] }
  const results = [{ id: 'unit', kind: 'product', ok: true }]
  const t = triage('ok', { unit: { applicability: 'applicable' }, boundary: { applicability: 'applicable' }, exception: { applicability: 'applicable' }, privacy: { applicability: 'not-applicable' } })
  const ai = { capped: true, capped_gaps: ['boundary'], tests: [] }
  const cov = N.withDimensionStatus({}, t, ai, profile, results)
  assert.equal(cov.dimension_status.boundary.status, 'capped')
  assert.equal(cov.dimension_status.boundary.display_zh, '额度用尽未补测')
  assert.equal(cov.dimension_status.exception.status, 'gap')
  assert.deepEqual(cov.gaps, ['exception'])
  assert.deepEqual(cov.capped, ['boundary'])
  assert.equal(cov.capped_total, 1)
  assert.deepEqual([cov.applicable_total, cov.covered_total], [3, 1])
  const j = N.withDimensionStatus({}, triage('capped', { unit: { applicability: 'applicable' }, boundary: { applicability: 'undetermined' } }), null, profile, results)
  assert.equal(j.dimension_status.boundary.status, 'capped')
  assert.equal(j.dimension_status.boundary.capped_by, 'jev')
  assert.equal(j.undetermined_total, 1)
  // without a cap the same dimension stays undetermined
  assert.equal(N.withDimensionStatus({}, triage('failed', { boundary: { applicability: 'undetermined' } }), null, profile, results).dimension_status.boundary.status, 'undetermined')
})

test('system_health: capped entries land in quota.capped + notes and make the run degraded (not a qa_tool_failure)', () => {
  const health = { status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], notes: [] }
  const quota = Q.memoryQuota({ pool: 'production', env: { ARGUS_QUOTA_AI_TEST: '0' } })
  quota.markCapped('ai-test', { pr: 893, dimensions: ['boundary'] })
  N.recordQuota(health, quota.summary())
  N.finalizeHealth(health, [{ number: 893, verdict: 'clean' }])
  assert.equal(health.status, 'degraded')
  assert.equal(health.qa_tool_failures.length, 0)
  assert.equal(health.quota.pool, 'production')
  assert.ok(health.notes.some((n) => n.startsWith('额度用尽未补测：#893 ai-test')))
})
