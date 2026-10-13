import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { tmpdir } from 'node:os'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { verifyLeads, isVerified } from '../src/verify-lead.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const cli = join(here, '..', '..', 'scripts', 'verify-ai-lead.mjs')
const sample = () => ({ schema_version: '1.0', extra: { keep: 1 }, prs: [{ number: 918, findings: [
  { source: 'ai-test', check_id: 'ai-test-1', fingerprint: 'fpA', pr_fingerprint: 'pfA', severity: 'P2', status: 'unconfirmed', verified: false, verified_at: null, verified_by: null, verified_note: null },
  { source: 'ai-test', check_id: 'ai-test-2', fingerprint: 'fpB', pr_fingerprint: 'pfB', severity: 'P2' },
  { source: 'check', check_id: 'unit', fingerprint: 'fpA', severity: 'P1' }] }, { number: 919, findings: [{ source: 'ai-test', check_id: 'ai-test-1', fingerprint: 'fpZ' }] }] })

test('verified 默认：nightly 写 ai-test finding 时带 verified:false + verified_at/by/note:null', () => {
  const src = readFileSync(join(here, '..', 'src', 'nightly.mjs'), 'utf8')
  const line = src.split('\n').find((l) => l.includes("status: 'unconfirmed', reproduced: t.reproduced"))
  assert.match(line, /verified: false, verified_at: null, verified_by: null, verified_note: null/)
})

test('isVerified：只有 === true 才算；缺省 / null / false 都是未核实', () => {
  assert.equal(isVerified({}), false); assert.equal(isVerified({ verified: null }), false)
  assert.equal(isVerified({ verified: false }), false); assert.equal(isVerified({ verified: 'true' }), false)
  assert.equal(isVerified({ verified: true }), true)
})

test('verifyLeads：按 fingerprint / pr_fingerprint / check_id 只标 ai-test 线索，限定 PR，其他字段不动', () => {
  const r = sample(); const before = structuredClone(r)
  const ch = verifyLeads(r, { pr: 918, fingerprint: 'fpA', by: 'daily-review', note: 'n', at: '2026-10-13T03:00:00Z' })
  assert.deepEqual(ch, [{ pr: 918, check_id: 'ai-test-1', fingerprint: 'fpA' }])
  const f = r.prs[0].findings[0]
  assert.deepEqual([f.verified, f.verified_at, f.verified_by, f.verified_note], [true, '2026-10-13T03:00:00Z', 'daily-review', 'n'])
  assert.deepEqual({ ...f, verified: false, verified_at: null, verified_by: null, verified_note: null }, before.prs[0].findings[0])
  assert.deepEqual(r.prs[0].findings.slice(1), before.prs[0].findings.slice(1)) // non-ai finding with same fp untouched
  assert.deepEqual(r.prs[1], before.prs[1]); assert.deepEqual(r.extra, before.extra)
  assert.equal(verifyLeads(r, { pr: 918, fingerprint: 'pfB' }).length, 1)
  assert.equal(verifyLeads(r, { pr: 919, checkId: 'ai-test-1' })[0].fingerprint, 'fpZ')
  verifyLeads(r, { pr: 918, checkId: 'ai-test-1', unset: true }); assert.equal(r.prs[0].findings[0].verified, false)
  assert.throws(() => verifyLeads(r, { pr: 918 }), /fingerprint/)
})

test('CLI scripts/verify-ai-lead.mjs：写回只改 verified*；没匹配 → exit 1；dry-run 不写', () => {
  const dir = mkdtempSync(join(tmpdir(), 'argus-verify-')); const file = join(dir, 'PR-918.json')
  writeFileSync(file, JSON.stringify(sample(), null, 2) + '\n')
  const run = (...a) => execFileSync(process.execPath, [cli, '--file', file, ...a], { encoding: 'utf8' })
  run('--pr', '918', '--fingerprint', 'fpB', '--dry-run'); assert.equal(JSON.parse(readFileSync(file, 'utf8')).prs[0].findings[1].verified, undefined)
  const out = JSON.parse(run('--pr', '918', '--fingerprint', 'fpB', '--by', 'daily-review', '--at', '2026-10-13T03:00:00Z'))
  assert.equal(out.changed.length, 1)
  const r = JSON.parse(readFileSync(file, 'utf8')); const exp = sample()
  Object.assign(exp.prs[0].findings[1], { verified: true, verified_at: '2026-10-13T03:00:00Z', verified_by: 'daily-review', verified_note: null })
  assert.deepEqual(r, exp)
  assert.throws(() => run('--pr', '918', '--fingerprint', 'nope'), (e) => e.status === 1)
})
