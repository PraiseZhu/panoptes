// 统一分级（Praise 2026-10-23）：测试侧 P0–P3 以审查机 Review-PR mivo/config/severity.md 为准。
// 覆盖：钉住副本校验、审查提示词嵌原文、PLAUSIBLE / 不认识的等级 / 缺项 → 待核实、AI 单独 P1 先整仓复核（#1191、#1174 回归样本）、
// 合并前裁定、检查失败定级（已合并引入的产品失败是 P1 不是 P0）、unverified 不算测试佐证、覆盖率只在 PR 自己写明目标时才是 P2
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, copyFileSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'

const root = mkdtempSync(join(tmpdir(), 'argus-unified-'))
process.env.ARGUS_ROOT ??= root
process.env.ARGUS_REVIEW_CACHE_DIR = join(root, 'review-verdicts')
process.env.ARGUS_VERIFY_CACHE_DIR = join(root, 'review-verifications')
const N = await import('../src/nightly.mjs')
const M = await import('../src/mingqa.mjs')
const S = await import('../src/severity.mjs')
const R = await import('../src/rulings.mjs')
const V = await import('../src/review-verify.mjs')
const C = await import('../src/coverage.mjs')
const { memoryQuota } = await import('../src/quota.mjs')

const REPO = 'xindong/mivo-canvas-plugin'
const secrets = { ARGUS_LLM_BASE_URL: 'https://gateway.invalid/v1', ARGUS_LLM_API_KEY: 'k' }
let reviewCalls = []
let reviewAnswer = null
globalThis.fetch = async (url, init) => {
  const body = JSON.parse(init.body)
  reviewCalls.push(body)
  return { ok: true, status: 200, json: async () => ({ model: 'openai/gpt-6-luna', usage: { total_tokens: 10 }, choices: [{ message: { content: JSON.stringify(reviewAnswer) } }] }) }
}
const health = () => ({ notes: [], llm_errors: [], qa_tool_failures: [], env_problems: [] })
const fileOf = (name) => [{ filename: name, status: 'modified', additions: 3, deletions: 1, patch: '@@ -140,3 +140,5 @@\n+  const belongsToPreviousTask = x\n+  return hidden || generating' }]

async function review({ answer, files, pr, verifier = null, handoffRaw = null, deps = {}, quota = memoryQuota({ pool: 'test' }) }) {
  reviewCalls = []; reviewAnswer = answer
  const out = { quota_capped: [], findings: [], pending_verification: [], checks: [] }
  const h = health()
  await N.runCodeReview({ out, repo: REPO, pr, files, prDiff: { files }, sha: 'f0ec3229e28e5937195f3cc2c87701efb3c4cde7', baseSha: 'b'.repeat(40), results: [], profile: { intentZh: 'Mivo 画布' }, evDir: mkdtempSync(join(root, 'ev-')), secrets, health: h, number: pr.number, quota, opts: { freshReview: true }, handoffRaw,
    deps: { ...(verifier ? { chat: verifier, reader: deps.reader ?? fakeReader() } : {}), ...deps } })
  return { out, h }
}
function fakeReader(files = {}) {
  return {
    read: (path) => (files[path] ? { ok: true, path, text: `${path}\n${files[path]}` } : { ok: false, text: `no such file ${path}` }),
    grep: (pattern) => ({ ok: true, files: Object.keys(files).filter((f) => files[f].includes(pattern)), text: Object.keys(files).filter((f) => files[f].includes(pattern)).map((f) => `${f}:1:${pattern}`).join('\n') || '(no matches)' }),
    ls: () => ({ ok: true, text: Object.keys(files).join('\n') }),
  }
}
// 复核会话的假模型：按顺序回动作
const scripted = (acts) => { let i = 0; const seen = []; const fn = async ({ messages }) => { seen.push(messages); return { ok: true, json: acts[Math.min(i++, acts.length - 1)], tokens: 5, model: 'm' } }; fn.seen = seen; return fn }

// ── 钉住副本 ─────────────────────────────────────────────
test('统一分级：config/severity.md 与锁文件的 sha256 一致（= Review-PR 7afb7c2 的 eced0dee…），版本是 sha256 前 12 位', () => {
  const std = S.loadSeverityStandard({ fresh: true })
  assert.equal(std.sha256, 'eced0deeeaa83fa8b3324d2a1a86436c21f4b33d4f2f4fa099b5f18ed0f328c5')
  assert.equal(std.version, 'eced0deeeaa8')
  assert.equal(std.source_repo, 'PraiseZhu/Review-PR')
  assert.equal(std.path, 'mivo/config/severity.md')
  assert.match(std.text, /P2 必修的三类/)
  assert.equal(S.severityStandardRef().status, 'pinned')
})

test('统一分级：副本被改过（sha256 对不上）→ 读取失败；代码审查记 unavailable，不凭摘要定级', async () => {
  const dir = mkdtempSync(join(root, 'sev-'))
  const copy = join(dir, 'severity.md')
  copyFileSync(new URL('../config/severity.md', import.meta.url), copy)
  writeFileSync(copy, readFileSync(copy, 'utf8').replace('P1 严重缺陷', 'P1 随便'))
  assert.throws(() => S.loadSeverityStandard({ file: copy, fresh: true }), /does not match the pinned/)
  process.env.ARGUS_SEVERITY_FILE = copy
  assert.throws(() => S.loadSeverityStandard({ fresh: true }), /does not match the pinned/)
  const r = await M.codeReview({ secrets, pr: { number: 1, title: 't' }, files: fileOf('a.ts'), results: [], profile: {}, outDir: mkdtempSync(join(root, 'cr-')) })
  delete process.env.ARGUS_SEVERITY_FILE
  S.loadSeverityStandard({ fresh: true })
  assert.equal(r.status, 'unavailable')
  assert.equal(r.error.kind, 'severity-standard')
})

test('上游比对：同 sha → same；不同 → drift；读不到 → error', () => {
  const lock = S.readSeverityLock()
  const text = readFileSync(new URL('../config/severity.md', import.meta.url), 'utf8')
  assert.equal(S.compareUpstream({ lock, upstreamText: text }).status, 'same')
  assert.equal(S.compareUpstream({ lock, upstreamText: `${text}\n多一行` }).status, 'drift')
  assert.equal(S.compareUpstream({ lock, upstreamText: null }).status, 'error')
})

test('审查提示词嵌统一分级原文，不再手写 P0–P3 定义；要求触发条件 / 错误行为 / 实际影响 / 证据；P3 不输出', () => {
  const p = M.reviewSystemPrompt(S.loadSeverityStandard())
  assert.ok(p.includes(S.loadSeverityStandard().text), 'severity.md 原文整段嵌入')
  assert.match(p, /trigger（触发条件）、behavior（错误行为）、impact（实际影响）、evidence（证据）/)
  assert.match(p, /P3 不输出/)
  assert.doesNotMatch(p, /P0 = data loss/, '旧的手写定义已删除')
})

// ── 审查结论整理 ─────────────────────────────────────────
test('toCodeReviewDoc：PLAUSIBLE / 不认识的等级 / 缺项 → unverified（不进门禁）；P3 与 REJECTED 不输出；只有 CONFIRMED 的 P0/P1 让 status failed', () => {
  const full = { file: 'a.ts', line: 3, trigger: 't', behavior: 'b', impact: 'i', evidence: 'a.ts:3' }
  const doc = M.toCodeReviewDoc({ findings: [
    { severity: 'P1', ...full, title: 'suspect', verdict: 'PLAUSIBLE' },
    { severity: 'P2', ...full, title: 'real', verdict: 'CONFIRMED' },
    { severity: 'P1', file: 'a.ts', line: 3, title: 'no impact', evidence: 'e', verdict: 'CONFIRMED' },
    { severity: 'critical', ...full, title: 'odd level', verdict: 'CONFIRMED' },
    { severity: 'P3', ...full, title: 'add a test', verdict: 'CONFIRMED' },
    { severity: 'P1', ...full, title: 'rejected', verdict: 'REJECTED' },
  ], unverified: [{ title: 'design?', file: 'a.ts', kind: 'design-question', severity_if_confirmed: 'P1', reason: 'review.md A3 写明' }] }, [{ filename: 'a.ts' }])
  assert.deepEqual(doc.findings.map((f) => [f.severity, f.title]), [['P2', 'real']])
  assert.equal(doc.status, 'passed', 'PLAUSIBLE P1 不让 status 变 failed')
  assert.deepEqual(doc.unverified.map((u) => [u.kind, u.title]), [['plausible', 'suspect'], ['incomplete', 'no impact'], ['unknown-level', 'odd level'], ['design-question', 'design?']])
  assert.deepEqual(doc.unverified[1].missing, ['trigger', 'behavior', 'impact'])
  assert.deepEqual(doc.dropped, { p3: 1, rejected: 1 })
})

// ── 回归样本 #1191：PLAUSIBLE 的 P1（裁剪格 generation 没有 taskId，触发路径不可达）────────────
const F1191 = { severity: 'P1', verdict: 'PLAUSIBLE', category: 'data-consistency', file: 'src/store/generateImageSlotAction.ts', line: 150, symbol: 'isStaleImageSlotExtra', title: '沿用原槽任务 ID 的隐藏裁剪节点会被误删',
  trigger: '用户隐藏一个从原槽裁出的新格；若该节点沿用原槽上一轮生成的 generation.taskId', behavior: '再生成原槽时它被加入 staleExtraIds 并从画布移除', impact: '用户的裁剪结果丢失', evidence: 'belongsToPreviousTask 只校验节点 taskId 与原槽上一任务一致' }
test('#1191 回归：PLAUSIBLE 的 P1 进待核实 —— 不进 findings、code_review 不 failed、PR 不 needs-attention、不开 issue', async () => {
  const pr = { number: 1191, title: 'fix(slot)', body: '修复生成槽余量清理', user: { login: 'aj0928' }, merged_at: '2026-10-22T06:30:57Z' }
  const { out } = await review({ answer: { status: 'failed', summary: 's', findings: [F1191] }, files: fileOf(F1191.file), pr })
  assert.deepEqual(out.findings, [])
  assert.deepEqual(out.pending_verification.map((p) => [p.pending_kind, p.owner, p.severity_if_confirmed, p.test_backed]), [['review-plausible', 'qa', 'P1', false]])
  assert.equal(out.code_review.status, 'passed')
  assert.equal(out.code_review.reviewer_status, 'passed')
  const prEntry = { ...out, checks: [], dimensions_missing: [] }
  assert.equal(N.verdictOf(prEntry), 'partially-verified', '待核实不是 clean，也不是 needs-attention')
  const report = { date: 'd', repo: REPO, prs: [{ number: 1191, author: 'PraiseZhu', ...prEntry }], system_health: { qa_tool_failures: [], env_problems: [], llm_errors: [] } }
  assert.deepEqual(N.selectIssueCandidates(report).candidates, [])
})

// ── 回归样本 #1174：CONFIRMED 的 P1，但 docs/pr/1174/review.md A3 写明是有意设计 ─────────────
const F1174 = { severity: 'P1', verdict: 'CONFIRMED', category: 'correctness', file: 'scripts/loops/lifeline/nightly.mjs', line: 43, symbol: 'scheduleExit', title: 'Host 签出会错误地豁免未执行的 provider 层',
  trigger: 'provider 层 NOT_RUN 且对应 Host 检查因 HOST_ACCOUNT_SIGNED_OUT 未执行', behavior: 'scheduleExit 映射为退出码 0', impact: '夜巡被报告为成功', evidence: 'checkMatchesLayer 在 provider 层上允许 check.layer === host' }
const REVIEW_1174 = '---\nschema: mivo-pr-handoff/v1\n---\n## 1. 验收\n  - A3：夜巡 CLI 只对已执行所有选中合同、所有必需 Host/provider 层有同 case PASS check 或匹配的 signed-out 证据的 partial 报告返回 0\n'
test('#1174 回归：AI 单独的 P1 先整仓复核；复核判「写明的有意设计」→ 待核实，不发作者；审查和复核都拿到了 review.md 原文', async () => {
  const pr = { number: 1174, title: 'feat(lifeline)', body: '见 docs/pr/1174/review.md', user: { login: 'zhongxingtian-ai' }, merged_at: '2026-10-22T00:00:00Z' }
  const verifier = scripted([
    { action: 'read', path: 'scripts/loops/lifeline/nightly.mjs', start: 30, end: 60 },
    { action: 'final', verdict: 'intended-design', reasoning: 'review.md A3 写明 signed-out 证据可以满足 provider 层', design_reference: 'docs/pr/1174/review.md A3', call_path: [] },
  ])
  const { out } = await review({ answer: { status: 'failed', summary: 's', findings: [F1174] }, files: fileOf(F1174.file), pr, verifier, handoffRaw: { review: REVIEW_1174, qa: null },
    deps: { reader: fakeReader({ 'scripts/loops/lifeline/nightly.mjs': 'const ok = checkMatchesLayer(check, layer) || signedOut(check)' }) } })
  assert.ok(String(reviewCalls[0].messages[1].content).includes('A3：夜巡 CLI'), '审查提示词带 review.md 原文（数据）')
  assert.ok(String(verifier.seen[0][1].content).includes('A3：夜巡 CLI'), '复核会话也带 review.md 原文')
  assert.deepEqual(out.findings, [])
  assert.equal(out.pending_verification.length, 1)
  const p = out.pending_verification[0]
  assert.equal(p.pending_kind, 'review-p1-unconfirmed')
  assert.equal(p.verification.status, 'intended-design')
  assert.equal(p.severity_if_confirmed, 'P1')
  assert.equal(out.code_review.status, 'passed', '没证实的 P1 不让 code_review failed')
  assert.equal(out.code_review.reviewer_status, 'failed')
})

test('AI 单独的 P1：复核判可达、调用链里的文件都在会话里读过 → 证实，留在 findings（带 verification）', async () => {
  const pr = { number: 2001, title: 't', body: '', user: { login: 'x' }, merged_at: '2026-10-22T00:00:00Z' }
  const verifier = scripted([
    { action: 'grep', pattern: 'placeCroppedImageNode' },
    { action: 'read', path: 'src/store/nodeCreationSlice.ts' },
    { action: 'final', verdict: 'reachable', reasoning: '裁剪入口把原槽 generation 整个复制过来', supported_scenario: '用户裁剪生成中的图片', call_path: [{ file: 'src/store/nodeCreationSlice.ts', line: 1, note: 'copies generation' }, { file: F1191.file, line: 150, note: 'deletes' }] },
  ])
  const reader = fakeReader({ 'src/store/nodeCreationSlice.ts': 'placeCroppedImageNode generation: { ...slot.generation }', [F1191.file]: 'belongsToPreviousTask placeCroppedImageNode' })
  const { out } = await review({ answer: { status: 'failed', findings: [{ ...F1191, verdict: 'CONFIRMED' }] }, files: fileOf(F1191.file), pr, verifier, deps: { reader } })
  assert.equal(out.findings.length, 1)
  assert.equal(out.findings[0].severity, 'P1')
  assert.equal(out.findings[0].verification.status, 'confirmed')
  assert.equal(out.code_review.status, 'failed')
  assert.deepEqual(out.pending_verification, [])
})

test('复核：判可达却引用会话里没读过的文件 / 没给调用链 → 不算证实；复核调用失败 → not-run，都进待核实', async () => {
  assert.equal(V.settleVerdict({ verdict: 'reachable', call_path: [{ file: 'src/never-read.ts', line: 1 }] }, new Set(['src/a.ts'])).status, 'undetermined')
  assert.equal(V.settleVerdict({ verdict: 'reachable', call_path: [] }, new Set()).status, 'undetermined')
  assert.equal(V.settleVerdict({ verdict: 'not-reachable' }, new Set()).status, 'refuted')
  assert.equal(V.settleVerdict({ verdict: 'whatever' }, new Set()).status, 'undetermined')
  const pr = { number: 2002, title: 't', body: '', user: { login: 'x' }, merged_at: '2026-10-22T00:00:00Z' }
  const failing = async () => ({ ok: false, error: { kind: 'http', status: 502, message: 'bad gateway' } })
  const { out, h } = await review({ answer: { findings: [{ ...F1191, verdict: 'CONFIRMED', title: 'other' }] }, files: fileOf(F1191.file), pr, verifier: failing })
  assert.deepEqual(out.findings, [])
  assert.equal(out.pending_verification[0].verification.status, 'not-run')
  assert.ok(h.llm_errors.some((e) => e.stage === 'review-verify'))
})

test('复核额度（ai-verify）用尽 → 不调模型，P1 进待核实并记 quota_capped', async () => {
  const pr = { number: 2003, title: 't', body: '', user: { login: 'x' }, merged_at: '2026-10-22T00:00:00Z' }
  const q = memoryQuota({ pool: 'test', env: { ARGUS_QUOTA_TEST_AI_VERIFY: '0' } })
  let called = 0
  const { out } = await review({ answer: { findings: [{ ...F1174, title: 'capped one' }] }, files: fileOf(F1174.file), pr, verifier: async () => { called++; return { ok: true, json: {} } }, quota: q })
  assert.equal(called, 0)
  assert.equal(out.pending_verification[0].verification.status, 'not-run')
  assert.deepEqual(out.quota_capped.map((c) => c.kind), ['ai-verify'])
})

test('只读仓库视图：在被测提交上 read / grep / ls，路径带 .. 拒绝', () => {
  const dir = mkdtempSync(join(root, 'git-'))
  const git = (...a) => execFileSync('git', ['-C', dir, ...a], { encoding: 'utf8', env: { ...process.env, GIT_AUTHOR_NAME: 't', GIT_AUTHOR_EMAIL: 't@t', GIT_COMMITTER_NAME: 't', GIT_COMMITTER_EMAIL: 't@t' } })
  git('init', '-q')
  execFileSync('mkdir', ['-p', join(dir, 'src')])
  writeFileSync(join(dir, 'src/a.ts'), 'export function placeCroppedImageNode() {\n  return { generation: {} }\n}\n')
  git('add', '.'); git('commit', '-qm', 'c')
  const sha = git('rev-parse', 'HEAD').trim()
  const r = V.repoReaderFor(dir, sha)
  const read = r.read('src/a.ts', 2, 2)
  assert.equal(read.ok, true); assert.match(read.text, /2: {3}return \{ generation: \{\} \}/)
  const g = r.grep('placeCroppedImageNode')
  assert.deepEqual(g.files, ['src/a.ts'])
  assert.equal(r.grep('nothing-here').text, '(no matches)')
  assert.match(r.ls('src').text, /a\.ts/)
  assert.equal(r.read('../etc/passwd').ok, false)
})

// ── 合并前已裁定 ─────────────────────────────────────────
const cm = (id, login, body, extra = {}) => ({ id, user: { login }, body, created_at: `2026-10-22T0${id % 10}:00:00Z`, html_url: `https://x/${id}`, path: F1191.file, line: 150, ...extra })
test('合并前裁定：盯梢核实「不需要改代码」、维护者「维护者裁定不改」、mivo-ruling 记录；后来又判成立并修了的不算；分级版本不对的拒收', () => {
  const comments = [
    cm(1, 'greptile-apps[bot]', '**generating 裁剪格会被误删**'),
    cm(2, 'PraiseZhu', '**发生了什么**：盯梢修复轮按仓库统一分级核实了这条意见，结论是本轮不需要改代码（不成立、或属于 P3 建议）。\n\n核实依据：\n- 裁剪格自身 taskId 独立\n\n<!-- mivo-watcher-receipt task=live-1191 -->', { in_reply_to_id: 1 }),
    cm(3, 'PraiseZhu', '[P2] 别的问题\n\n<!-- mivo-finding key=aaaaaaaaaaaa round=1 head=bbbbbbb -->', { path: 'src/b.ts', line: 10 }),
    cm(4, 'aj0928', '维护者裁定不改：见 PR 正文「产品意图」', { in_reply_to_id: 3 }),
    cm(5, 'PraiseZhu', '[P1] 第三个\n\n<!-- mivo-finding key=cccccccccccc round=1 head=ddddddd -->', { path: 'src/c.ts', line: 5 }),
    cm(6, 'PraiseZhu', '判不成立\n<!-- mivo-watcher-receipt task=t -->\n<!-- mivo-ruling v=1 key=cccccccccccc head=' + 'e'.repeat(40) + ' verdict=invalid std=000000000000 by=w task=t -->', { in_reply_to_id: 5 }),
    cm(7, 'PraiseZhu', '[P2] 第四个', { path: 'src/d.ts', line: 1 }),
    cm(8, 'PraiseZhu', '结论是本轮不需要改代码\n<!-- mivo-watcher-receipt task=a -->', { in_reply_to_id: 7 }),
    cm(9, 'PraiseZhu', '**发生了什么**：盯梢修复轮确认此意见成立并已修复，修复提交 `578e38a74973`。\n<!-- mivo-watcher-receipt task=b -->', { in_reply_to_id: 7 }),
    cm(10, 'stranger', '维护者裁定不改：我说不改', { in_reply_to_id: 1 }),
  ]
  const threads = R.threadsFromReviewComments(comments)
  assert.equal(threads.length, 4)
  const { rulings, rejected } = R.collectPriorRulings({ threads, reviewer: 'PraiseZhu', maintainers: ['aj0928', 'PraiseZhu'], standardVersion: 'eced0deeeaa8' })
  assert.deepEqual(rulings.map((r) => [r.path, r.kind, r.verdict]).sort(), [[F1191.file, 'watcher', 'invalid-or-p3'], ['src/b.ts', 'maintainer', 'intended']].sort())
  assert.ok(rejected.some((r) => /分级版本/.test(r.reason)), 'mivo-ruling std 不是当前版本 → 拒收')
  assert.ok(rejected.some((r) => /不在维护者名单/.test(r.reason)))
  assert.ok(!rulings.some((r) => r.path === 'src/d.ts'), '后来又判成立并修了 → 以最新为准，不算裁定')
})

test('裁定只对裁定时的代码有效：回帖时刻的 head（按 PR 提交时间）到被测提交之间文件没改才用；同文件、行号差 ≤ 3 才算同一问题', () => {
  const headAt = R.headAtFrom([{ sha: 'h1', commit: { committer: { date: '2026-10-22T01:00:00Z' } } }, { sha: 'h2', commit: { committer: { date: '2026-10-22T05:00:00Z' } } }])
  assert.equal(headAt('2026-10-22T06:15:13Z'), 'h2')
  assert.equal(headAt('2026-10-22T02:00:00Z'), 'h1')
  const base = [{ kind: 'watcher', verdict: 'invalid-or-p3', path: 'a.ts', line: 150, at: '2026-10-22T06:15:13Z', ruled_head: null }]
  const [u] = R.withCodeState(base, { headAt, codeState: (h, p) => (h === 'h2' && p === 'a.ts' ? 'unchanged' : 'changed') })
  assert.equal(u.ruled_head, 'h2'); assert.equal(u.code_state, 'unchanged')
  assert.ok(R.matchPriorRuling([u], { severity: 'P1', file: 'a.ts', line: 152 }))
  assert.equal(R.matchPriorRuling([u], { severity: 'P1', file: 'a.ts', line: 160 }), null)
  assert.equal(R.matchPriorRuling([u], { severity: 'P1', llm_severity: 'P0', file: 'a.ts', line: 150 }), null, 'P0 不按裁定放过')
  const [c] = R.withCodeState(base, { headAt, codeState: () => 'changed' })
  assert.equal(R.matchPriorRuling([c], { severity: 'P1', file: 'a.ts', line: 150 }), null, '文件改过 → 只作参考')
  assert.match(R.rulingPromptSection([c]), /只作参考/)
})

test('合并前已裁定的同一问题、代码没变 → 审查即使再报也进待核实（review-ruled），不发作者；裁定写进审查提示词', async () => {
  const pr = { number: 1191, title: 't', body: '', user: { login: 'aj0928' }, merged_at: '2026-10-22T06:30:57Z' }
  const deps = {
    listPullReviewComments: async () => [cm(1, 'greptile-apps[bot]', 'generating 裁剪格会被误删'), cm(2, 'PraiseZhu', '结论是本轮不需要改代码（不成立、或属于 P3 建议）\n核实依据：\n- 裁剪格 taskId 独立\n<!-- mivo-watcher-receipt task=x -->', { in_reply_to_id: 1 })],
    listPullCommits: async () => [{ sha: 'h2', commit: { committer: { date: '2026-10-22T00:00:00Z' } } }],
    listTeamMembers: async () => ['PraiseZhu'],
    codeState: () => 'unchanged',
  }
  reviewCalls = []; reviewAnswer = { findings: [{ ...F1191, verdict: 'CONFIRMED' }] }
  const out = { quota_capped: [], findings: [], pending_verification: [], checks: [] }
  await N.runCodeReview({ out, repo: REPO, pr, files: fileOf(F1191.file), prDiff: { files: fileOf(F1191.file) }, sha: 's'.repeat(40), baseSha: 'b'.repeat(40), results: [], profile: { reviewRulings: { enabled: true, reviewer: 'PraiseZhu', maintainersTeam: 'xindong/art-support' } }, evDir: mkdtempSync(join(root, 'ev-')), secrets, health: health(), number: 1191, quota: memoryQuota({ pool: 'test' }), opts: { freshReview: true }, deps })
  assert.match(String(reviewCalls[0].messages[1].content), /盯梢核实：不成立或属于 P3；依据：「- 裁剪格 taskId 独立/)
  assert.deepEqual(out.findings, [])
  assert.equal(out.pending_verification[0].pending_kind, 'review-ruled')
  assert.equal(out.prior_rulings.used, 1)
})

// ── 检查失败的定级 ────────────────────────────────────────
test('检查失败定级：已合并 PR 引入的产品类失败是 P1（不是 P0，标 p0_candidate）；质量守卫 P2；unverified / setup-failed / timeout / flaky 不定级', () => {
  const product = { kind: 'product' }, quality = { kind: 'quality' }
  assert.equal(N.severityFor({ status: 'introduced', step: product, merged: true }), 'P1')
  assert.equal(N.severityFor({ status: 'introduced', step: product, merged: false }), 'P1')
  assert.equal(N.p0Candidate({ status: 'introduced', step: product, merged: true }), true)
  assert.equal(N.p0Candidate({ status: 'introduced', step: product, merged: false }), false)
  assert.equal(N.severityFor({ status: 'introduced', step: quality, merged: true }), 'P2')
  for (const s of ['unverified', 'setup-failed', 'timeout', 'flaky']) assert.equal(N.severityFor({ status: s, step: product, merged: true }), null, s)
  assert.equal(N.severityFor({ status: 'pre-existing', step: product, merged: true }), 'P3')
  assert.equal(N.severityFor({ status: 'fixed-later', step: product, merged: true }), 'P3')
})

test('test_backed：unverified / timeout / setup-failed / flaky 不算测试佐证；introduced / pre-existing / fixed-later 算', () => {
  for (const s of ['unverified', 'timeout', 'setup-failed', 'flaky']) assert.equal(N.checkIsTestBacked({ status: s }), false, s)
  for (const s of ['introduced', 'pre-existing', 'fixed-later']) assert.equal(N.checkIsTestBacked({ status: s }), true, s)
  const [pr] = N.annotateTestBacked([{ number: 1, findings: [{ source: 'check', status: 'unverified', severity: 'P2' }] }])
  assert.equal(pr.findings[0].test_backed, false)
  assert.equal(pr.test_backed, false)
})

// ── 覆盖率 ────────────────────────────────────────────
const gateOf = (pct) => ({ status: pct < 90 ? 'fail' : 'pass', values: { new_code: pct, lines: 70, branches: 60, functions: 55 }, thresholds: { new_code: 90 }, baseline: {}, new_code: { files: [] } })
test('覆盖率：没写目标的 PR 新代码覆盖率低于 90 不是 P2（只记信息）；PR 正文写了目标没达到是 P2（stated-acceptance）；达到了不报', () => {
  const g1 = gateOf(81)
  assert.equal(N.statedAcceptanceFinding({ gate: g1, body: '修了个 bug，补了测试', qaMd: null, repo: REPO, number: 1, sha: 's', baseSha: 'b' }), null)
  assert.equal(g1.stated_target, null)
  assert.match(g1.finding_policy, /informational/)
  const g2 = gateOf(81)
  const f = N.statedAcceptanceFinding({ gate: g2, body: '## 验收\n- 新代码覆盖率 ≥ 90%', qaMd: null, repo: REPO, number: 2, sha: 's', baseSha: 'b' })
  assert.equal(f.severity, 'P2'); assert.equal(f.source, 'stated-acceptance'); assert.equal(f.test_backed, false)
  assert.equal(g2.stated_target.status, 'missed'); assert.equal(g2.stated_target.source, 'pr-body')
  assert.ok(f.evidence.trigger && f.evidence.behavior && f.evidence.impact && f.evidence.recommendation)
  const g3 = gateOf(81)
  const fq = N.statedAcceptanceFinding({ gate: g3, body: '', qaMd: '## 5. 验收\n覆盖率目标：85%', repo: REPO, number: 3, sha: 's', baseSha: 'b' })
  assert.equal(fq.evidence.stated_target.source, 'docs/pr/3/qa.md')
  const g4 = gateOf(95)
  assert.equal(N.statedAcceptanceFinding({ gate: g4, body: '新代码覆盖率 ≥ 90%', qaMd: null, repo: REPO, number: 4, sha: 's', baseSha: 'b' }), null)
  assert.equal(g4.stated_target.status, 'met')
})

test('覆盖率目标识别：只认写成目标的说法，不认对现状的描述', () => {
  const t = (x) => C.statedCoverageTarget([{ source: 'pr-body', text: x }])
  assert.equal(t('当前覆盖率 81%'), null)
  assert.equal(t('coverage 92% (was 70%)'), null)
  assert.equal(t('```\ncoverage >= 90%\n```'), null, '代码块里的不算')
  assert.equal(t('新代码覆盖率 ≥ 90%').pct, 90)
  assert.equal(t('Line coverage at least 80% overall').metric, 'lines')
  assert.equal(t('分支覆盖率不低于 70%').metric, 'branches')
})

test('报告：severity_standard 指向钉住的原文，severity_scale 只有等级名称', () => {
  const ref = S.severityStandardRef()
  assert.deepEqual([ref.status, ref.sha256.slice(0, 12), ref.ref.slice(0, 7)], ['pinned', 'eced0deeeaa8', '7afb7c2'])
  assert.equal(S.SEVERITY_LEVEL_NAMES.P1, 'P1 严重缺陷')
})

test('system_health.severity_pin：每日比对的结果进报告；上游变了记一条 note，不挡运行', () => {
  const dir = mkdtempSync(join(root, 'pin-'))
  const f = join(dir, 'severity-pin.json')
  writeFileSync(f, JSON.stringify({ status: 'drift', at: 't', source_repo: 'PraiseZhu/Review-PR', path: 'mivo/config/severity.md', upstream_ref: 'main', pinned_sha256: 'eced0deeeaa8x', upstream_sha256: 'ffff' }))
  const h = health()
  N.recordSeverityPin(h, f)
  assert.equal(h.severity_pin.status, 'drift')
  assert.match(h.notes[0], /统一分级上游已变/)
  assert.deepEqual(h.env_problems, [], '不是环境故障，不开 system_health issue')
  const h2 = health(); N.recordSeverityPin(h2, join(dir, 'missing.json')); assert.equal(h2.severity_pin, undefined)
})

// sha256 小工具与 severity.mjs 一致
test('severityVersion = sha256 前 12 位（与审查机 SEVERITY_VERSION、盯梢裁决 std= 同一算法）', () => {
  const buf = readFileSync(new URL('../config/severity.md', import.meta.url))
  assert.equal(S.severityVersion(createHash('sha256').update(buf).digest('hex')), 'eced0deeeaa8')
})

// ── AI 补测线索：人确认后才从待核实移进 findings ───────────────────────
test('verify-ai-lead：待核实里的 AI 补测线索被人确认 → 移进 findings（P2，verified），PR 等级 / 结论重算；--unset 移回', async () => {
  const { verifyLeads } = await import('../src/verify-lead.mjs')
  const report = { prs: [{ number: 918, severity: 'P3', verdict: 'partially-verified', findings: [], pending_verification: [
    { pending_kind: 'ai-test', source: 'ai-test', check_id: 'ai-test-1', fingerprint: 'fpA', pr_fingerprint: 'fpA-pr918', severity_if_confirmed: 'P2', title: 'AI-generated test fails', verified: false }] }] }
  const ch = verifyLeads(report, { pr: 918, checkId: 'ai-test-1', by: 'daily-review', at: '2026-10-23T10:00:00Z' })
  assert.deepEqual(ch.map((c) => c.moved), ['pending_verification→findings'])
  const p = report.prs[0]
  assert.deepEqual([p.findings.length, p.findings[0].severity, p.findings[0].verified, p.pending_verification.length], [1, 'P2', true, 0])
  assert.deepEqual([p.severity, p.verdict], ['P2', 'review-suggested'])
  verifyLeads(report, { pr: 918, checkId: 'ai-test-1', unset: true })
  assert.deepEqual([p.findings.length, p.pending_verification.length, p.pending_verification[0].severity_if_confirmed, p.severity, p.verdict], [0, 1, 'P2', 'P3', 'partially-verified'])
})

test('模型回复不是单个 JSON：连写的几个对象 / 代码围栏 / 前后带话都能解析；复核会话一次执行连写的读 / 搜动作，同一回复里提前写的结论不算', async () => {
  assert.deepEqual(M.parseModelJson('{"a":1}{"b":2}').all, [{ a: 1 }, { b: 2 }])
  assert.deepEqual(M.parseModelJson('好的：```json\n{"c":3}\n```').json, { c: 3 })
  assert.throws(() => M.parseModelJson('没有对象'))
  const reader = fakeReader({ 'src/a.ts': 'placeCroppedImageNode', 'src/b.ts': 'deletes slot' })
  let call = 0
  const chatFake = async ({ messages }) => {
    call++
    if (call === 1) return { ok: true, json: { action: 'read', path: 'src/a.ts' }, json_all: [{ action: 'read', path: 'src/a.ts' }, { action: 'read', path: 'src/b.ts' }, { action: 'final', verdict: 'reachable', call_path: [{ file: 'src/a.ts', line: 1 }] }], tokens: 1 }
    assert.match(String(messages.at(-1).content), /src\/a\.ts[\s\S]*src\/b\.ts/, '两个读动作的结果一起回给模型')
    return { ok: true, json: { action: 'final', verdict: 'reachable', call_path: [{ file: 'src/a.ts', line: 1 }, { file: 'src/b.ts', line: 1 }] }, tokens: 1 }
  }
  const r = await V.verifyReviewFinding({ secrets, finding: { ...F1191, evidence: {} }, reader, context: { pr: { number: 1 } }, std: S.loadSeverityStandard(), chat: chatFake, model: 'm' })
  assert.equal(call, 2, '第一次回复里的结论没被采用（还没看到结果）')
  assert.equal(r.status, 'confirmed')
  assert.deepEqual(r.steps.map((s) => s.path), ['src/a.ts', 'src/b.ts'])
})
