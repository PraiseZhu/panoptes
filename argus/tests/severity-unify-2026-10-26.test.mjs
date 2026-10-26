// 统一严重度标准（Praise 2026-10-26 批准）：合并阻断口径与项目仓库 CONTRIBUTING 一致（P2 先只记录）、显示名「P0 紧急」、
// coverage_gate 不阻断、p0_candidate 样例确实生成、统一分级钉到 651265f（内容不变）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { createHash } from 'node:crypto'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
process.env.ARGUS_CACHE_DIR ??= join(mkdtempSync(join(tmpdir(), 'argus-sev-unify-')), 'cache')
const M = await import('../src/mingqa.mjs')
const S = await import('../src/severity.mjs')
const C = await import('../src/coverage.mjs')
const { buildP0CandidateReport } = await import('./fixtures-p0-candidate.mjs')

const full = { file: 'src/a.ts', line: 3, trigger: 't', behavior: 'b', impact: 'i', evidence: 'src/a.ts:3', verdict: 'CONFIRMED' }
const doc = (findings) => M.toCodeReviewDoc({ findings }, [{ filename: 'src/a.ts' }])

test('阻断口径：P0 一律阻断（转了 issue 也阻断）', () => {
  for (const extra of [{}, { tracked_issue: 12 }, { issue: '#12' }]) {
    const d = doc([{ severity: 'P0', ...full, ...extra }])
    assert.equal(d.status, 'failed'); assert.equal(d.blocking, true); assert.deepEqual(d.blocking_ids, ['CR-001'])
  }
})

test('阻断口径：P1 未转 issue 阻断；已转 issue 不阻断', () => {
  assert.equal(doc([{ severity: 'P1', ...full }]).status, 'failed')
  const t = doc([{ severity: 'P1', ...full, tracked_issue: 77 }])
  assert.equal(t.status, 'passed'); assert.equal(t.findings[0].tracked_issue, 77); assert.deepEqual(t.would_block, [])
})

test('阻断口径：P2 未转 issue 只标 would_block，不阻断（report-only）；已转 issue 不标', () => {
  const d = doc([{ severity: 'P2', ...full }, { severity: 'P2', ...full, tracked_issue: 5 }])
  assert.equal(d.status, 'passed'); assert.equal(d.blocking, false)
  assert.deepEqual(d.would_block, ['CR-001'])
  assert.match(d.blocking_policy, /p2-untracked-report-only/)
  assert.equal(M.P2_BLOCKING_ENFORCED, false)
  // 收紧后（一周后）的同一口径：未转 issue 的 P2 阻断
  const g = M.blockingGate(d.findings, { enforceP2: true })
  assert.equal(g.blocking, true); assert.deepEqual(g.blocking_ids, ['CR-001']); assert.deepEqual(g.would_block, [])
})

test('阻断口径：非法 issue 号不算已转', () => {
  assert.equal(doc([{ severity: 'P1', ...full, tracked_issue: 'abc' }]).status, 'failed')
  assert.equal(doc([{ severity: 'P1', ...full, tracked_issue: 0 }]).status, 'failed')
})

test('审查提示词里的 P0/P1 措辞不变（只改显示名，不动模型输入）', () => {
  assert.match(M.reviewSystemPrompt({ version: 'x', text: '' }), /status must be "failed" if any P0\/P1 finding exists, else "passed"\./)
})

test('显示名：P0 统一为「P0 紧急」，不再出现「P0 阻断」', () => {
  assert.equal(S.SEVERITY_LEVEL_NAMES.P0, 'P0 紧急')
  for (const f of ['../../report/REPORT-STYLE.md', '../../report/track-followups.mjs', '../../pi/bin/daily.py']) {
    const s = readFileSync(join(here, f), 'utf8')
    assert.ok(!s.includes('P0 阻断'), f); assert.ok(s.includes('P0 紧急'), f)
  }
})

test('coverage_gate：blocking false / level info（低于参考线也一样），status 名不改', () => {
  const g = C.coverageGate({ coverage: { status: 'ok', total: { lines: 50, branches: 50, functions: 50 }, files: null }, added: new Map() })
  assert.equal(g.blocking, false); assert.equal(g.level, 'info')
  const u = C.coverageGate({ coverage: null, added: new Map() })
  assert.equal(u.blocking, false); assert.equal(u.status, 'unavailable')
})

test('p0_candidate：已合并 + 引入的产品类失败 + 主干仍失败的样例确实生成 p0_candidate（等级仍 P1），和 pi/test 的样例一致', async () => {
  const built = await buildP0CandidateReport()
  const [pr] = built.prs
  assert.equal(pr.severity, 'P1'); assert.equal(pr.failed_checks[0].p0_candidate, true); assert.equal(pr.findings[0].p0_candidate, true)
  assert.deepEqual(built, JSON.parse(readFileSync(join(here, '../../pi/test/p0-candidate-report.json'), 'utf8')))
})

test('统一分级钉到 Review-PR 651265f，副本 sha256 不变', () => {
  const lock = JSON.parse(readFileSync(join(here, '../config/severity.lock.json'), 'utf8'))
  assert.equal(lock.ref, '651265ffb5baacef7866e6ad20b77eed30cfd791')
  assert.equal(createHash('sha256').update(readFileSync(join(here, '../config/severity.md'))).digest('hex'), lock.sha256)
  assert.equal(lock.sha256, 'eced0deeeaa83fa8b3324d2a1a86436c21f4b33d4f2f4fa099b5f18ed0f328c5')
})
