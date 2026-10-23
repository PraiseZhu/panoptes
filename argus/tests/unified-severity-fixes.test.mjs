// 统一分级复查修正（2026-10-24）：人工确认能发到作者、代码审查的宽松 JSON、复核必须真读到文件、gate / verdict 和最终结论一致、
// 覆盖率目标识别、钉住值比对留痕、p0_note 写法。复查实验（review-argus/exp/e3、e4、e5、e11、e12、e14）在这里变成回归测试
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, readFileSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { execFileSync, spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = mkdtempSync(join(tmpdir(), 'argus-unified-fixes-'))
process.env.ARGUS_CACHE_DIR ??= join(root, 'cache')
const N = await import('../src/nightly.mjs')
const M = await import('../src/mingqa.mjs')
const V = await import('../src/review-verify.mjs')
const C = await import('../src/coverage.mjs')
const { verifyLeads } = await import('../src/verify-lead.mjs')
const { notifiable } = await import('../../report/finding-policy.mjs')

// ── 人工确认：移进 findings 后作者口径要放行 ───────────────────────────
const aiLead = () => ({ pending_kind: 'ai-test', owner: 'qa', label_zh: '待核实（QA 自查）', kind_zh: 'AI 补测在当前代码上失败', pending_reason: 'r', pending_reason_zh: 'z',
  source: 'ai-test', check_id: 'ai-test-1', fingerprint: 'fpA', pr_fingerprint: 'fpA-pr918', severity_if_confirmed: 'P2', title: 'AI-generated test fails', status: 'unconfirmed', verified: false, test_backed: false })
test('verify-ai-lead：确认的 AI 补测线索移进 findings 后去掉待核实标记、status confirmed，作者口径（notifiable 新发提醒）放行；摘要重算；--unset 原样还原', () => {
  const report = { summary: { prs_by_severity: { P0: 0, P1: 0, P2: 0, P3: 1, untested: 0 }, findings_by_severity: { P0: 0, P1: 0, P2: 0, P3: 0 }, findings_total: 0, pending_verification: { total: 1, by_kind: { 'ai-test': 1 } } },
    prs: [{ number: 918, severity: 'P3', verdict: 'partially-verified', findings: [], finding_sources: [], pending_summary: { total: 1, by_kind: { 'ai-test': 1 } }, pending_verification: [aiLead()] }] }
  verifyLeads(report, { pr: 918, checkId: 'ai-test-1', by: 'daily-review', at: '2026-10-24T01:00:00Z' })
  const p = report.prs[0], f = p.findings[0]
  for (const k of ['pending_kind', 'owner', 'label_zh', 'kind_zh', 'pending_reason', 'pending_reason_zh']) assert.equal(k in f, false, k)
  assert.deepEqual([f.severity, f.status, f.verified], ['P2', 'confirmed', true])
  assert.equal(notifiable(f, { newNotice: true }), true)
  assert.deepEqual([p.finding_sources, 'pending_summary' in p, report.summary.prs_by_severity.P2, report.summary.findings_total, report.summary.pending_verification.total], [['ai-test'], false, 1, 1, 0])
  verifyLeads(report, { pr: 918, checkId: 'ai-test-1', unset: true })
  assert.deepEqual(p.pending_verification[0], { ...aiLead(), verified_at: null, verified_by: null, verified_note: null }, '撤销后回到移动前（verified* 清空）')
  assert.deepEqual([p.findings.length, p.severity, p.verdict, report.summary.pending_verification.total], [0, 'P3', 'partially-verified', 1])
})

test('verify-ai-lead：复核没证实 / 没跑成的 AI 单独 P1，人核实后按 fingerprint 移进 findings（verification confirmed / manual），作者口径放行；只按 check_id 选不中', () => {
  const pend = { pending_kind: 'review-p1-unconfirmed', owner: 'qa', label_zh: '待核实（QA 自查）', kind_zh: 'k', source: 'code-review', check_id: 'code-review', fingerprint: 'mq1-r', pr_fingerprint: 'mq1-r-pr7',
    severity_if_confirmed: 'P1', verdict: 'CONFIRMED', title: 't', file: 'a.ts', line: 3, verification: { status: 'not-run', status_reason: 'ai-verify daily cap reached' } }
  const report = { prs: [{ number: 7, severity: 'P3', verdict: 'partially-verified', findings: [], pending_verification: [structuredClone(pend)], code_review: { ran: true, status: 'passed' } }] }
  assert.equal(verifyLeads(report, { pr: 7, checkId: 'code-review' }).length, 0)
  verifyLeads(report, { pr: 7, fingerprint: 'mq1-r', by: 'praise', note: '复现测试 #1300 在被测提交上失败', at: '2026-10-24T02:00:00Z' })
  const p = report.prs[0], f = p.findings[0]
  assert.deepEqual([f.severity, f.verification.status, f.verification.by, p.severity, p.verdict, p.code_review.status], ['P1', 'confirmed', 'manual', 'P1', 'needs-attention', 'failed'])
  assert.equal(notifiable(f, { newNotice: true }), true)
  verifyLeads(report, { pr: 7, fingerprint: 'mq1-r', unset: true })
  assert.deepEqual(p.pending_verification[0], pend)
  assert.equal(p.code_review.status, 'passed')
})

// ── 代码审查：模型回了好几个对象 ───────────────────────────────
test('代码审查的宽松 JSON：取最后一个带 findings 的对象并记 json_loose；一个都没有按解析失败；严格单个对象照旧', () => {
  const draft = { status: 'passed', summary: 'draft', findings: [] }
  const finalDoc = { status: 'failed', summary: 'final', findings: [{ severity: 'P2', title: 'x' }] }
  const loose = M.parseModelJson(`${JSON.stringify(draft)}\n${JSON.stringify(finalDoc)}`)
  assert.deepEqual(M.pickReviewJson(loose).json, finalDoc)
  assert.equal(M.pickReviewJson(loose).loose, true)
  assert.equal(M.pickReviewJson(M.parseModelJson('{"action":"x"}{"note":1}')), null)
  const strict = M.parseModelJson(JSON.stringify(draft))
  assert.equal(M.pickReviewJson(strict), strict)
})

// ── 复核会话：搜索命中、越界读、读目录都不算读过 ───────────────────────
function repo() {
  const dir = mkdtempSync(join(root, 'git-'))
  const git = (...a) => execFileSync('git', ['-C', dir, ...a], { encoding: 'utf8', env: { ...process.env, GIT_AUTHOR_NAME: 't', GIT_AUTHOR_EMAIL: 't@t', GIT_COMMITTER_NAME: 't', GIT_COMMITTER_EMAIL: 't@t' } })
  git('init', '-q'); mkdirSync(join(dir, 'src'))
  writeFileSync(join(dir, 'src/a.ts'), 'export function entry() {\n  return sink()\n}\n')
  writeFileSync(join(dir, 'src/b.ts'), 'export function sink() {\n  throw new Error("boom")\n}\n')
  git('add', '.'); git('commit', '-qm', 'c')
  return V.repoReaderFor(dir, git('rev-parse', 'HEAD').trim())
}
test('只读仓库视图：越过文件末尾、读目录、空路径都返回 ok:false 并写明没有读到内容；读到时带实际显示的行范围', () => {
  const r = repo()
  const eof = r.read('src/a.ts', 5000)
  assert.equal(eof.ok, false); assert.match(eof.text, /超过文件末尾/)
  const d = r.read('src')
  assert.equal(d.ok, false); assert.match(d.text, /目录/)
  const ok = r.read('src/a.ts', 2, 3)
  assert.deepEqual([ok.ok, ok.start, ok.end, ok.total], [true, 2, 3, 3])
  assert.match(r.grep('sink').text, /不算读过文件/)
})
const scripted = (acts) => { let i = 0; return async () => ({ ok: true, json: acts[Math.min(i++, acts.length - 1)], tokens: 1, model: 'm' }) }
const reachable = { action: 'final', verdict: 'reachable', reasoning: 'r', call_path: [{ file: 'src/a.ts', line: 2 }, { file: 'src/b.ts', line: 2 }] }
const std = { version: 'eced0deeeaa8', text: '统一分级' }
const verify = (acts) => V.verifyReviewFinding({ secrets: {}, finding: { title: 't', file: 'src/b.ts', line: 2 }, reader: repo(), context: { pr: { number: 1 } }, std, chat: scripted(acts), model: 'm' })
test('复核判可达：调用链里的文件只被搜到、没 read 过 → 判断不了；越界读不算读过；行号要落在读过的范围里；都读到了才证实', async () => {
  assert.equal((await verify([{ action: 'grep', pattern: 'sink' }, reachable])).status, 'undetermined')
  assert.equal((await verify([{ action: 'read', path: 'src/a.ts' }, { action: 'read', path: 'src/b.ts', start: 5000 }, reachable])).status, 'undetermined')
  assert.equal((await verify([{ action: 'read', path: 'src/a.ts', start: 1, end: 1 }, { action: 'read', path: 'src/b.ts' }, reachable])).status, 'undetermined', 'a.ts:2 不在读过的 1-1 行里')
  assert.equal((await verify([{ actions: [{ action: 'read', path: 'src/a.ts' }, { action: 'read', path: 'src/b.ts' }] }, reachable])).status, 'confirmed')
})

// ── code_review 的 gate / verdict 跟着最终结论 ────────────────────────
test('复核否掉 AI 单独的 P1 之后，code_review.status / gate / verdict 一致（passed），审查原结论留在 reviewer_*', () => {
  const out = { findings: [], pending_verification: [{ source: 'code-review', pending_kind: 'review-p1-unconfirmed' }],
    code_review: { ran: true, status: 'failed', reviewer_status: 'failed', gate: { status: 'failed', blockingFindings: 1, problems: ['code-review-status-failed'] }, verdict: { status: 'failed', max_severity: 'P1', findings: [] } } }
  N.settleCodeReviewStatus(out)
  const cr = out.code_review
  assert.deepEqual([cr.status, cr.gate.status, cr.gate.blockingFindings, cr.verdict.status, cr.verdict.max_severity, cr.pending_count], ['passed', 'passed', 0, 'passed', null, 1])
  assert.deepEqual([cr.reviewer_gate.status, cr.reviewer_verdict.max_severity], ['failed', 'P1'])
  out.findings.push({ source: 'code-review', severity: 'P1' })
  N.settleCodeReviewStatus(out)
  assert.deepEqual([cr.status, cr.gate.status, cr.verdict.max_severity, cr.reviewer_gate.status], ['failed', 'failed', 'P1', 'failed'])
  const notRun = { findings: [], code_review: { ran: false, status: 'error' } }
  N.settleCodeReviewStatus(notRun); assert.equal(notRun.code_review.status, 'error')
})

// ── 覆盖率目标：现状描述不算目标 ──────────────────────────────
const gate = { values: { new_code: 93, lines: 70, branches: 60, functions: 55 } }
const target = (text) => C.evaluateStatedTarget(C.statedCoverageTarget([{ source: 'pr-body', text }]), gate)
test('PR 写明的覆盖率目标：「达到 95%」「96%，达到要求」是现状，不是目标；「目标 90%」按就近的说法取；只写 line 不按全仓比', () => {
  assert.deepEqual(target('新代码覆盖率达到 95%（目标 90%）') && [target('新代码覆盖率达到 95%（目标 90%）').pct, target('新代码覆盖率达到 95%（目标 90%）').status], [90, 'met'])
  assert.equal(target('本 PR 新增代码覆盖率 96%，达到要求'), null)
  const t3 = target('Coverage target: new lines ≥ 90%')
  assert.deepEqual([t3.pct, t3.metric, t3.status], [90, 'new_code', 'met'])
  const t4 = target('line coverage at least 80%')
  assert.deepEqual([t4.metric, t4.status], ['new_code', 'met'], '没写全仓 → 不按全仓 70% 比')
  assert.deepEqual([target('全仓行覆盖率不低于 80%').metric, target('全仓行覆盖率不低于 80%').status], ['lines', 'missed'])
  assert.equal(target('新代码覆盖率 ≥ 95%').status, 'missed')
  assert.equal(target('当前覆盖率 81%'), null)
  assert.equal(target('> 覆盖率 85%（引用块）'), null, '引用块的 > 不是比较符')
})

// ── 钉住值每日比对：读不到上游要留痕；锁文件换了要重查 ───────────────────────
test('severity_pin：读不到上游时写原因，并且当天只写一条 note', () => {
  const dir = mkdtempSync(join(root, 'pin-'))
  const f = join(dir, 'severity-pin.json')
  writeFileSync(f, JSON.stringify({ status: 'error', at: '2026-10-24T01:00:00Z', source_repo: 'PraiseZhu/Review-PR', path: 'mivo/config/severity.md', upstream_ref: 'main', message: 'HTTP 404: Not Found' }))
  const h = () => ({ notes: [] })
  const now = new Date('2026-10-24T02:00:00Z')
  const h1 = h(); N.recordSeverityPin(h1, f, { now })
  assert.equal(h1.severity_pin.message, 'HTTP 404: Not Found'); assert.equal(h1.notes.length, 1); assert.match(h1.notes[0], /没跑成.*HTTP 404/)
  const h2 = h(); N.recordSeverityPin(h2, f, { now }); assert.equal(h2.notes.length, 0, '同一天第二次不再写')
  const h3 = h(); N.recordSeverityPin(h3, f, { now: new Date('2026-10-25T02:00:00Z') }); assert.equal(h3.notes.length, 1, '第二天再提醒')
})
test('check-severity-pin --once-per-day：当天缓存的结论只在钉住值没换时沿用；error 不缓存', () => {
  const dir = mkdtempSync(join(root, 'pinrun-'))
  const state = join(dir, 'state.json')
  const fakeGh = join(dir, 'gh.mjs')
  writeFileSync(fakeGh, '#!/usr/bin/env node\nprocess.stdout.write(Buffer.from(require("fs").readFileSync(process.env.UPSTREAM)).toString("base64"))\n'.replace('require("fs")', '(await import("node:fs"))'))
  execFileSync('chmod', ['+x', fakeGh])
  const upstream = join(dir, 'upstream.md')
  writeFileSync(upstream, readFileSync(join(here, '../config/severity.md')))
  const lock = JSON.parse(readFileSync(join(here, '../config/severity.lock.json'), 'utf8'))
  const run = () => spawnSync(process.execPath, [join(here, '../../scripts/check-severity-pin.mjs'), '--once-per-day', '--quiet'], { encoding: 'utf8',
    env: { ...process.env, ARGUS_GH_BIN: fakeGh, UPSTREAM: upstream, PANOPTES_SEVERITY_PIN_STATE: state, PANOPTES_SEVERITY_PIN_LOG: join(dir, 'log') } })
  assert.equal(run().status, 0)
  const day = JSON.parse(readFileSync(state, 'utf8')).day
  // 当天缓存成 drift、但记录的钉住值和现在的锁文件不一样（人当天更新过钉住值）→ 重查，不再报 drift
  writeFileSync(state, JSON.stringify({ day, status: 'drift', pinned_sha256: 'f'.repeat(64) }))
  assert.equal(run().status, 0)
  assert.equal(JSON.parse(readFileSync(state, 'utf8')).pinned_sha256, lock.sha256)
  // 同一天、钉住值没变的 drift → 直接沿用（退出 4，不查上游）
  writeFileSync(state, JSON.stringify({ day, status: 'drift', pinned_sha256: lock.sha256 }))
  assert.equal(run().status, 4)
  // error 不缓存：下一次照样去查
  writeFileSync(state, JSON.stringify({ day, status: 'error', pinned_sha256: lock.sha256 }))
  assert.equal(run().status, 0)
})

// ── p0_note 按实际有没有复查最新主干写 ──────────────────────────
test('p0_note：没复查最新主干、按设计不复查（secret-scan）、复查仍失败分别写清楚', () => {
  assert.match(N.p0NoteFor({ step: { noLatestRecheck: true }, latestSha: 'x', latestIsSame: false, latestRun: null }), /not re-checked on latest main by design/)
  assert.match(N.p0NoteFor({ step: {}, latestSha: null }), /latest main not re-checked/)
  assert.match(N.p0NoteFor({ step: {}, latestSha: 'x', latestIsSame: false, latestRun: 'fail' }), /still failing on latest main/)
  assert.match(N.p0NoteFor({ step: {}, latestSha: 'x', latestIsSame: true }), /tested commit is the latest main/)
  assert.match(N.p0NoteFor({ step: {}, latestSha: 'x', latestIsSame: false, latestRun: 'timeout' }), /re-check incomplete \(timeout\)/)
})
