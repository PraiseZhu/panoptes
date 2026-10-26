// 2026-10-26（Praise）：AI 报的 P0 要 Opus 证实 + head 上的复现测试，不再静默降成 P1
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

process.env.ARGUS_VERIFY_CACHE_DIR = mkdtempSync(join(tmpdir(), 'argus-p0-'))
delete process.env.ARGUS_VERIFY_MODEL
const N = await import('../src/nightly.mjs')
const { memoryQuota } = await import('../src/quota.mjs')
const { notifiable } = await import('../../report/finding-policy.mjs')

const entry = () => ({ source: 'code-review', test_backed: false, check_id: 'code-review', severity: 'P0', llm_severity: 'P0', verdict: 'CONFIRMED', title: 't', file: 'src/a.ts', line: 3, fingerprint: `fp-${Math.random()}`, evidence: {} })
const reader = { read: (p) => ({ ok: true, path: p, start: 1, end: 999, text: `${p}\n1: x` }), grep: () => ({ ok: true, text: '' }), ls: () => ({ ok: true, text: '' }) }
const chatFor = (v) => { const models = []; let i = 0; const fn = async ({ model }) => { models.push(model); if (i++ === 0) return { ok: true, json: { action: 'read', path: 'src/a.ts' }, tokens: 1, model }; return { ok: true, json: v === 'reachable' ? { action: 'final', verdict: 'reachable', call_path: [{ file: 'src/a.ts', line: 3 }] } : { action: 'final', verdict: v }, tokens: 1, model } }; fn.models = models; return fn }
const repro = { id: 'ai1-1', result: 'fails-on-current', reproduced: true, file: 'src/a.repro.test.ts', mutation_file: 'src/a.ts', purpose: 'p' }

async function run(v, aiTests) {
  const out = { findings: [], pending_verification: [], quota_capped: [], ai_tests: { tests: aiTests } }
  const chat = chatFor(v)
  await N.verifyReviewP1s({ entries: [entry()], out, repo: 'o/r', pr: { number: 1, title: 't' }, sha: 'a'.repeat(40), profile: {}, secrets: {}, health: { llm_errors: [], notes: [] }, number: 1, quota: memoryQuota({ pool: 'test' }), opts: { freshReview: true }, prDiff: { files: [] }, handoffRaw: null, rulingsText: '', deps: { chat, reader } })
  return { out, chat }
}

test('Opus 证实 + 复现测试 → P0 进 findings，发作者', async () => {
  const { out, chat } = await run('reachable', [repro])
  assert.ok(chat.models.every((m) => m === 'anthropic/claude-opus-5-5'))
  assert.equal(out.findings[0].severity, 'P0')
  assert.deepEqual(out.findings[0].verification.confirmed_by, ['opus', 'repro-test'])
  assert.equal(notifiable(out.findings[0], { newNotice: true }), true)
})

test('缺一样（只有 Opus / 只有复现 / 都没有）→ 待核实，仍是 P0，绝不变成 P1', async () => {
  for (const [v, t] of [['reachable', []], ['not-reachable', [repro]], ['cannot-determine', []]]) {
    const { out } = await run(v, t)
    assert.equal(out.findings.length, 0, v)
    assert.ok(!out.findings.some((f) => f.severity === 'P1'))
    const p = out.pending_verification[0]
    assert.equal(p.pending_kind, 'review-p0-unconfirmed'); assert.equal(p.severity_if_confirmed, 'P0'); assert.equal(p.p0_unconfirmed, true)
    assert.equal(notifiable(p, { newNotice: true }), false)
  }
})
