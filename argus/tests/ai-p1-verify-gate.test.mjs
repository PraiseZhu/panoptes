// 2026-10-26（Praise）：AI 单独报的 P1 只有「复现测试在 head 上失败」或「Opus 整仓复核证实」才发作者，否则进待核实
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

process.env.ARGUS_VERIFY_CACHE_DIR = mkdtempSync(join(tmpdir(), 'argus-vgate-'))
delete process.env.ARGUS_VERIFY_MODEL
const N = await import('../src/nightly.mjs')
const { memoryQuota } = await import('../src/quota.mjs')
const { notifiable } = await import('../../report/finding-policy.mjs')

const SHA = 'a'.repeat(40)
const entry = (extra = {}) => ({ source: 'code-review', test_backed: false, check_id: 'code-review', severity: 'P1', llm_severity: 'P1', verdict: 'CONFIRMED', title: 't', file: 'src/a.ts', line: 3, fingerprint: `fp-${Math.random()}`, evidence: {}, ...extra })
const reader = { read: (p) => ({ ok: true, path: p, start: 1, end: 999, text: `${p}\n1: x` }), grep: () => ({ ok: true, text: '' }), ls: () => ({ ok: true, text: '' }) }
const verdict = (v) => { const models = []; let i = 0; const fn = async ({ model }) => { models.push(model); if (i++ === 0) return { ok: true, json: { action: 'read', path: 'src/a.ts' }, tokens: 1, model }; return { ok: true, json: v === 'reachable' ? { action: 'final', verdict: 'reachable', reasoning: 'r', call_path: [{ file: 'src/a.ts', line: 3 }] } : { action: 'final', verdict: v, reasoning: 'r' }, tokens: 1, model } }; fn.models = models; return fn }
const failingTest = { id: 'ai1-1', result: 'fails-on-current', reproduced: true, file: 'src/a.repro.test.ts', mutation_file: 'src/a.ts', purpose: 'p' }

async function run({ v, aiTests = [], e = entry() }) {
  const out = { findings: [], pending_verification: [], quota_capped: [], ai_tests: { tests: aiTests } }
  const chat = verdict(v)
  await N.verifyReviewP1s({ entries: [e], out, repo: 'o/r', pr: { number: 1, title: 't' }, sha: SHA, profile: {}, secrets: {}, health: { llm_errors: [], notes: [] }, number: 1, quota: memoryQuota({ pool: 'test' }), opts: { freshReview: true }, prDiff: { files: [] }, handoffRaw: null, rulingsText: '', deps: { chat, reader } })
  return { out, chat }
}

test('复核默认用 Opus', async () => {
  const { chat } = await run({ v: 'not-reachable' })
  assert.equal(N.VERIFY_MODEL, 'anthropic/claude-opus-5-5')
  assert.ok(chat.models.length && chat.models.every((m) => m === 'anthropic/claude-opus-5-5'))
})

test('Opus 证实 → 进 findings（confirmed_by opus）', async () => {
  const { out } = await run({ v: 'reachable' })
  assert.equal(out.findings.length, 1)
  assert.equal(out.findings[0].verification.status, 'confirmed')
  assert.deepEqual(out.findings[0].verification.confirmed_by, ['opus'])
  assert.equal(notifiable(out.findings[0], { newNotice: true }), true)
})

test('Opus 没证实、但复现测试在 head 上重跑仍失败 → 进 findings（repro-test，test_backed）', async () => {
  const { out } = await run({ v: 'not-reachable', aiTests: [failingTest] })
  assert.equal(out.pending_verification.length, 0)
  const f = out.findings[0]
  assert.equal(f.verification.status, 'confirmed'); assert.equal(f.verification.method, 'repro-test')
  assert.equal(notifiable(f, { newNotice: true }), true)
  assert.equal(f.verification.opus_status, 'refuted'); assert.equal(f.verification.repro.test_id, 'ai1-1'); assert.equal(f.test_backed, true)
})

test('两样都没有 → pending_verification，不发作者；只失败一次（没重跑复现）或别的文件的失败不算复现', async () => {
  for (const aiTests of [[], [{ ...failingTest, reproduced: false }], [{ ...failingTest, reproduced: null }], [{ ...failingTest, mutation_file: 'src/other.ts', file: 'src/other.test.ts' }], [{ ...failingTest, result: 'kept' }]]) {
    const { out } = await run({ v: 'cannot-determine', aiTests })
    assert.equal(out.findings.length, 0, JSON.stringify(aiTests))
    assert.equal(out.pending_verification[0].pending_kind, 'review-p1-unconfirmed')
    assert.notEqual(out.pending_verification[0].verification.status, 'confirmed')
    assert.equal(notifiable(out.pending_verification[0], { newNotice: true }), false)
  }
})
