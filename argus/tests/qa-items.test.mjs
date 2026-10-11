// 新增的 QA 条目：接口探测（本机签发 / 没账号跳过 / 幂等）、步骤扩展（passEnv / 宿主程序）、
// 新的 CI 检查步骤、指纹与去重、覆盖率基线、报告保留、补测、维度分诊
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { mkdtempSync, writeFileSync, readFileSync, mkdirSync, existsSync, readdirSync, chmodSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createPublicKey, createVerify } from 'node:crypto'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-items-'))
const probe = await import('../src/probe-run.mjs')
const { passEnvFor, resolveStepBinary } = await import('../src/runner.mjs')
const { loadProfile } = await import('../src/config.mjs')
const nightly = await import('../src/nightly.mjs')
const { toApplicability, toTriage, DIMENSIONS } = await import('../src/matrix.mjs')
const catchup = await import('../src/catchup.mjs')

// ── 接口探测 ────────────────────────────────────────────
function startServer(handler) {
  return new Promise((res) => { const s = createServer(handler).listen(0, '127.0.0.1', () => res({ s, base: `http://127.0.0.1:${s.address().port}` })) })
}

test('本机签发的 Cindy JWT：RS256、iss/aud/sub/exp 正确，公钥 JWKS 能验签', () => {
  const iss = probe.createLocalIssuer({ issuer: 'https://auth.cindy.com.cn', audience: 'argus-qa:mivo-canvas' })
  const tok = iss.sign('user-a')
  const [h, p, sig] = tok.split('.')
  const header = JSON.parse(Buffer.from(h, 'base64url')); const claims = JSON.parse(Buffer.from(p, 'base64url'))
  assert.equal(header.alg, 'RS256'); assert.equal(header.kid, iss.jwks.keys[0].kid)
  assert.equal(claims.iss, 'https://auth.cindy.com.cn'); assert.equal(claims.aud, 'argus-qa:mivo-canvas'); assert.equal(claims.sub, 'user-a')
  assert.ok(claims.exp > Date.now() / 1000)
  assert.equal(iss.jwks.keys[0].d, undefined, 'JWKS 只含公钥')
  const v = createVerify('RSA-SHA256'); v.update(`${h}.${p}`)
  assert.ok(v.verify(createPublicKey({ key: iss.jwks.keys[0], format: 'jwk' }), Buffer.from(sig, 'base64url')))
})

test('本机签发接缝探测：只在被测仓导出 setCindyJwksLoader 时可用', () => {
  const root = mkdtempSync(join(tmpdir(), 'seam-'))
  assert.equal(probe.localOidcSupported(root), false)
  mkdirSync(join(root, 'server/lib'), { recursive: true })
  writeFileSync(join(root, 'server/lib/assetAuth.ts'), 'export function x() {}\n')
  assert.equal(probe.localOidcSupported(root), false)
  writeFileSync(join(root, 'server/lib/assetAuth.ts'), 'export const setCindyJwksLoader = (f) => {}\n')
  assert.equal(probe.localOidcSupported(root), true)
})

test('测试账号：没有 → no test account；token 直接用；密码模式没 token 地址 → 仍是 missing', async () => {
  assert.deepEqual(await probe.resolveAccount({}), { status: 'missing', reason: 'no test account' })
  assert.equal((await probe.resolveAccount({ MIVO_QA_TEST_TOKEN: 't' })).via, 'token')
  assert.match((await probe.resolveAccount({ MIVO_QA_TEST_USER: 'u', MIVO_QA_TEST_PASSWORD: 'p' })).reason, /no test account/)
  const ok = await probe.resolveAccount({ MIVO_QA_TEST_USER: 'u', MIVO_QA_TEST_PASSWORD: 'p', MIVO_QA_TEST_TOKEN_URL: 'http://x/token' }, async (_u, init) => {
    assert.match(init.body, /grant_type=password/)
    return { ok: true, status: 200, json: async () => ({ access_token: 'AT' }) }
  })
  assert.deepEqual(ok, { status: 'ok', via: 'password-grant', token: 'AT' })
})

test('登录态用例没有账号 → 记 skipped（原因 no test account），绝不算通过', async () => {
  const { s, base } = await startServer((req, res) => { res.writeHead(req.headers.authorization ? 200 : 401); res.end('{}') })
  try {
    const r = await probe.runRequests(base, [
      { id: 'anon', name: '未登录 401', path: '/p', expect: { status: 401 } },
      { id: 'mine', name: '登录后列表', path: '/p', auth: 'account', requires: 'account', expect: { status: 200 } },
    ], { account: { status: 'missing', reason: 'no test account' } })
    assert.deepEqual(r.checks.map((c) => [c.id, c.status]), [['anon', 'pass']])
    assert.deepEqual(r.skipped, [{ id: 'mine', name: '登录后列表', reason: 'no test account' }])
  } finally { s.close() }
})

test('幂等：repeat + expectRepeat + sameAs；capture 的值能代入后续请求和期望', async () => {
  let n = 0
  const { s, base } = await startServer((req, res) => {
    if (req.method === 'POST') { n++; res.writeHead(n === 1 ? 201 : 200, { 'content-type': 'application/json' }); return res.end(JSON.stringify({ id: 'abc', created: n === 1 })) }
    res.writeHead(req.url === '/items/abc' ? 200 : 404, { 'content-type': 'application/json' }); res.end(JSON.stringify({ id: 'abc' }))
  })
  try {
    const r = await probe.runRequests(base, [
      { id: 'up', name: '上传', method: 'POST', path: '/items', body: {}, repeat: 2, expect: { status: 201 }, expectRepeat: { status: 200 }, sameAs: ['id'], capture: { itemId: 'id' } },
      { id: 'get', name: '读回', path: '/items/${itemId}', expect: { status: 200, json: { id: '${itemId}' } } },
    ], {})
    assert.deepEqual(r.checks.map((c) => c.status), ['pass', 'pass'], JSON.stringify(r.checks))
  } finally { s.close() }
})

test('步骤 passEnv 只透传列出的、已设置的变量；宿主程序按 env → 候选 → PATH 找，找不到记没跑成', () => {
  assert.deepEqual(passEnvFor({ passEnv: ['MIVO_QA_TEST_TOKEN', 'MIVO_QA_TEST_USER', 'bad-name'] }, { MIVO_QA_TEST_TOKEN: 'x', OTHER: 'y', 'bad-name': 'z' }), { MIVO_QA_TEST_TOKEN: 'x' })
  const dir = mkdtempSync(join(tmpdir(), 'bin-'))
  const bin = join(dir, 'gitleaks'); writeFileSync(bin, '#!/bin/sh\n'); chmodSync(bin, 0o755)
  const step = { binary: { var: 'GITLEAKS', env: 'ARGUS_GITLEAKS_BIN', names: ['gitleaks'], candidates: ['/nonexistent/gitleaks'] } }
  assert.equal(resolveStepBinary(step, { ARGUS_GITLEAKS_BIN: bin, PATH: '' }).vars.GITLEAKS, bin)
  assert.deepEqual(resolveStepBinary(step, { PATH: dir }).ro, [bin])
  assert.ok(resolveStepBinary(step, { PATH: '' }).unmet)
})

// ── profile：补齐的 CI 检查 ───────────────────────────────
test('profile：补齐目标仓 CI 的 7 项检查，命令与目标 workflow / package.json 一致，维度与严重度一致', () => {
  const p = loadProfile('mivo-canvas-plugin')
  const by = Object.fromEntries(p.steps.map((s) => [s.id, s]))
  assert.deepEqual(by['web-build'].cmd, ['npm', 'run', 'build'])
  assert.deepEqual(by.design.cmd, ['npm', 'run', 'verify:design'])
  assert.deepEqual(by.design.env, { MIVO_GUARD_BASE_SHA: '${DIFF_BASE_SHA}', MIVO_LITERAL_RATCHET_ENFORCE: '1' })
  assert.deepEqual(by['internal-literals'].cmd, ['node', 'scripts/ci/check-internal-literals.mjs'])
  assert.deepEqual(by['endpoint-literals'].cmd, ['node', 'scripts/ci/check-endpoint-literals.mjs'])
  assert.match(by['host-locale'].cmd.join(' '), /node cindyplugin\/check-host-locale\.mjs cindyplugin\/dist/)
  assert.match(by['vendor-split'].cmd.join(' '), /node cindyplugin\/check-vendor-split\.mjs cindyplugin\/dist/)
  assert.match(by['secret-scan'].cmd.join(' '), /detect --source \. .*--log-opts "\$1\.\.HEAD" --no-banner --redact=100/)
  assert.equal(by['secret-scan'].noLatestRecheck, true)
  const kinds = { 'web-build': 'product', design: 'quality', 'internal-literals': 'quality', 'endpoint-literals': 'quality', 'host-locale': 'quality', 'vendor-split': 'quality', 'secret-scan': 'product' }
  for (const [id, k] of Object.entries(kinds)) assert.equal(by[id].kind, k, id)
  assert.equal(p.boxes.design, 'white-box'); assert.equal(p.boxes['secret-scan'], 'white-box'); assert.equal(p.boxes['web-build'], 'gray-box')
  assert.equal(nightly.severityFor({ status: 'introduced', step: by.design, merged: true }), 'P2')
  assert.equal(nightly.severityFor({ status: 'introduced', step: by['secret-scan'], merged: true }), 'P0')
  assert.equal(nightly.severityFor({ status: 'introduced', step: by['web-build'], merged: false }), 'P1')
  // 旧数据迁移旅程 + 接口探测的登录态
  assert.equal(by['e2e-legacy-migration'].optional, true)
  assert.ok(by['e2e-legacy-migration'].covers.includes('data-consistency'))
  assert.equal(by['api-probe'].probe.localOidc != null, true)
  assert.ok(by['api-probe'].passEnv.includes('MIVO_QA_TEST_USER'))
  assert.ok(by['api-probe'].probe.requests.some((r) => r.requires === 'account'))
  assert.ok(by['api-probe'].probe.requests.some((r) => (r.repeat ?? 1) > 1), 'has idempotency probes')
})

test('secret-scan 不做最新主干复查：合并后引入的泄露保持 introduced（P0），不会因后续提交变成 fixed-later', () => {
  const mk = (ok) => [{ id: 'secret-scan', ok, exitCode: ok ? 0 : 1, failures: [], failureParseStatus: 'not_detected', testOutcomeStatus: 'not_applicable', tail: 'leaks found: 1' }]
  const args = { first: mk(false), rerun: mk(false), base: mk(true), latest: mk(true), latestIsSame: false }
  assert.equal(nightly.classifyKey('secret-scan::*', args).status, 'fixed-later')
  assert.equal(nightly.classifyKey('secret-scan::*', { ...args, skipLatest: true }).status, 'introduced')
})

test('接口探测报告里跳过的子用例 → skipped_steps（parent_step 指回 api-probe）；check 带 sub_checks', () => {
  const profile = { steps: [{ id: 'api-probe', kind: 'product' }], boxes: { 'api-probe': 'gray-box' } }
  const rep = { checks: [{ id: 'a', status: 'pass' }, { id: 'b', status: 'fail' }], skipped: [{ id: 'mine', name: '登录后列表', reason: 'no test account' }], auth: { account: 'missing' } }
  assert.deepEqual(nightly.probeSkippedSteps([{ id: 'api-probe', probeReport: rep }], profile), [{ id: 'api-probe/mine', nameZh: '登录后列表', reason: 'no test account', parent_step: 'api-probe', dimension: 'gray-box' }])
  assert.deepEqual(nightly.probeSummary(rep), { total: 3, passed: 1, failed: 1, skipped: 1, auth: { account: 'missing' } })
})

// ── 指纹与去重 ───────────────────────────────────────────
test('代码审查指纹含归一化标题哈希：同文件同类别不同问题不撞；空白 / 大小写 / 行号差异不影响', () => {
  const f = { file: 'src/a.ts', symbol: 'save', category: 'correctness' }
  const a = nightly.codeReviewFingerprint('r', { ...f, title: 'Missing await on save() at line 12' })
  const b = nightly.codeReviewFingerprint('r', { ...f, title: 'missing  await on save()   at line 40' })
  const c = nightly.codeReviewFingerprint('r', { ...f, title: 'Race between two saves' })
  assert.equal(a, b); assert.notEqual(a, c)
  assert.match(nightly.titleHash('x'), /^[0-9a-f]{12}$/)
})

test('复发判定（按指纹）：开放 → 评论；修好关闭 → 重开；not_planned / wontfix 标签 → 沉默；没有 → 新开', () => {
  const fp = 'mq1-aaa'
  const c = { fingerprint: fp }
  const body = nightly.fingerprintMarker(fp)
  assert.equal(nightly.recurrenceDecision([], c).action, 'create')
  assert.equal(nightly.recurrenceDecision([{ number: 1, state: 'open', body }], c).action, 'comment')
  assert.equal(nightly.recurrenceDecision([{ number: 1, state: 'closed', state_reason: 'completed', body }], c).action, 'reopen')
  assert.equal(nightly.recurrenceDecision([{ number: 1, state: 'closed', state_reason: null, body }], c).action, 'reopen')
  assert.equal(nightly.recurrenceDecision([{ number: 1, state: 'closed', state_reason: 'not_planned', body }], c).action, 'suppressed')
  assert.equal(nightly.recurrenceDecision([{ number: 1, state: 'closed', state_reason: 'completed', labels: [{ name: 'wontfix' }], body }], c).action, 'suppressed')
  // 开放的优先；都关闭时看最新那条
  assert.equal(nightly.recurrenceDecision([{ number: 1, state: 'open', body }, { number: 2, state: 'closed', state_reason: 'not_planned', body }], c).issue.number, 1)
  assert.equal(nightly.recurrenceDecision([{ number: 1, state: 'closed', state_reason: 'not_planned', body }, { number: 2, state: 'closed', state_reason: 'completed', body }], c).action, 'reopen')
  // 别的指纹不算
  assert.equal(nightly.recurrenceDecision([{ number: 1, state: 'open', body: nightly.fingerprintMarker('mq1-other') }], c).action, 'create')
})

test('issue 正文：带指纹标记，finding 块带 merge_sha；不再有合并提交去重键', () => {
  const report = { date: '2026-10-10', repo: 'xindong/mivo-canvas-plugin', run: { id: 'r' } }
  const pr = { number: 9, title: 't', author: 'PraiseZhu', url: 'u', state: 'merged', merge_sha: 'm'.repeat(40), tested_sha: 'm'.repeat(40) }
  const f = { source: 'check', check_id: 'unit', severity: 'P0', fingerprint: 'mq1-x', pr_fingerprint: 'mq1-x-pr9', dimension: 'gray-box' }
  const { body } = nightly.buildIssuePayload(report, pr, f)
  assert.ok(body.includes('<!-- mivo-qa-fingerprint: mq1-x -->'))
  assert.ok(!body.includes('mivo-qa-dedup'))
  const block = JSON.parse(body.match(/```json mivo-qa-finding\n([\s\S]*?)\n```/)[1])
  assert.equal(block.merge_sha, 'm'.repeat(40)); assert.equal(block.dedup_key, undefined)
})

// ── 报告保留 ─────────────────────────────────────────────
test('报告不就地改写：再次写同一路径时原件改名为 .v1 / .v2，新报告记 previous_version', () => {
  const dir = mkdtempSync(join(tmpdir(), 'keep-'))
  const f = join(dir, 'PR-7.json')
  nightly.writeReport({ n: 1 }, f)
  nightly.writeReport({ n: 2 }, f)
  nightly.writeReport({ n: 3 }, f)
  assert.deepEqual(readdirSync(dir).sort(), ['PR-7.json', 'history'])
  assert.deepEqual(readdirSync(join(dir, 'history')).sort(), ['PR-7.v1.json', 'PR-7.v2.json'])
  assert.equal(JSON.parse(readFileSync(join(dir, 'history/PR-7.v1.json'), 'utf8')).n, 1)
  const cur = JSON.parse(readFileSync(f, 'utf8'))
  assert.equal(cur.n, 3); assert.equal(cur.previous_version, 'history/PR-7.v2.json')
  assert.equal(JSON.parse(readFileSync(join(dir, 'history/PR-7.v2.json'), 'utf8')).previous_version, 'history/PR-7.v1.json', 'path is relative to the current file\'s directory')
})

// ── 维度分诊 ─────────────────────────────────────────────
test('维度适用性：Jev 成功时 29 个维度全部有明确结论 + 理由，没有 undetermined', () => {
  const profile = { notApplicable: { 'provably-fair': '没有可证明公平' }, pathHints: [] }
  const answers = Object.fromEntries(DIMENSIONS.map((d, i) => [d.key, { noul: (i % 10) / 9 }]))
  const triage = { ...toTriage({ profile, files: [], jev: { ok: true, model: 'jev-1.13.0', answers } }), jevStatus: 'ok' }
  const a = toApplicability(triage)
  assert.equal(a.total, 29); assert.equal(DIMENSIONS.length, 29)
  assert.deepEqual(a.undetermined, [])
  assert.equal(a.applicable.length + a.not_applicable.length, 29)
  for (const [k, d] of Object.entries(a.dimensions)) { assert.ok(['applicable', 'not-applicable'].includes(d.applicability), k); assert.ok(d.reason, k) }
  assert.equal(a.dimensions['provably-fair'].source, 'profile')
  const mid = toApplicability({ ...triage, rows: triage.rows.map((r) => (r.source === 'jev' ? { ...r, p: 0.55 } : r)) })
  assert.equal(Object.values(mid.dimensions).filter((d) => d.source === 'jev').every((d) => d.applicability === 'applicable' && d.confidence === 'low'), true)
})

test('维度适用性：没配 Jev / Jev 失败 → 未判定的维度标 undetermined；失败记 llm_errors 和 system_health.triage', () => {
  const profile = { notApplicable: {}, pathHints: [] }
  const t = { ...toTriage({ profile, files: [], jev: { ok: false, reasonZh: 'Jev 返回 503（服务出错）' } }), jevStatus: 'failed' }
  const a = toApplicability(t)
  assert.equal(a.status, 'failed'); assert.equal(a.undetermined.length, 29)
  const health = { llm_errors: [] }
  nightly.recordTriage(health, 5, a)
  assert.equal(health.triage.failed[0].pr, 5); assert.equal(health.llm_errors[0].stage, 'triage(jev)')
  assert.equal(nightly.TRIAGE_NOTE_NOT_CONFIGURED, '维度适用性未判定（Jev 未配置）')
  assert.equal(toApplicability({ ...t, jevStatus: 'not-configured' }).status, 'not-configured')
})

// ── 补测 ─────────────────────────────────────────────────
function catchFixture() {
  const reportsDir = mkdtempSync(join(tmpdir(), 'cu-'))
  const mergedDir = join(reportsDir, 'merged'); mkdirSync(mergedDir)
  const write = (n, extra) => writeFileSync(join(mergedDir, `PR-${n}.json`), JSON.stringify({ merged_sha: extra.merged_sha, prs: [{ number: n, verdict: 'clean', ...extra }] }))
  return { reportsDir, mergedDir, write }
}
const NOW = new Date('2026-10-11T02:00:00Z') // 上海 10-11 10:00
const PRS = [
  { number: 3, merged_at: '2026-10-10T03:00:00Z', merge_sha: 'c'.repeat(40), day: '2026-10-10' },
  { number: 1, merged_at: '2026-10-09T17:00:00Z', merge_sha: 'a'.repeat(40), day: '2026-10-10' },
  { number: 2, merged_at: '2026-10-10T01:00:00Z', merge_sha: 'b'.repeat(40), day: '2026-10-10' },
]

test('补测：天数是上海的昨天和今天；缺口 = 缺文件 / tested_sha ≠ merge commit / 没有结论；docs-only 看 merged_sha', () => {
  assert.deepEqual(catchup.catchUpDays(NOW), ['2026-10-10', '2026-10-11'])
  const { mergedDir, write } = catchFixture()
  write(1, { tested_sha: 'a'.repeat(40) })
  write(2, { tested_sha: 'x'.repeat(40) })
  write(3, { verdict: 'docs-only', merged_sha: 'c'.repeat(40) })
  assert.equal(catchup.gapOf(PRS[1], mergedDir), null)
  assert.match(catchup.gapOf(PRS[2], mergedDir).reason, /^stale/)
  assert.equal(catchup.gapOf(PRS[0], mergedDir), null)
  assert.equal(catchup.gapOf({ number: 4, merge_sha: 'd'.repeat(40) }, mergedDir).reason, 'missing')
  write(5, { verdict: 'qa-error', tested_sha: 'e'.repeat(40) })
  assert.match(catchup.gapOf({ number: 5, merge_sha: 'e'.repeat(40) }, mergedDir).reason, /unfinished/)
})

test('listMergedPrs：gh pr list（只读），按上海窗口过滤、按合并时间从早到晚', () => {
  const calls = []
  const rows = [{ number: 9, mergedAt: '2026-10-08T10:00:00Z', mergeCommit: { oid: 'z' } }, ...PRS.map((p) => ({ number: p.number, mergedAt: p.merged_at, mergeCommit: { oid: p.merge_sha } }))]
  const list = catchup.listMergedPrs('o/r', ['2026-10-10', '2026-10-11'], { gh: 'gh', run: (cmd, args) => { calls.push([cmd, ...args]); return { status: 0, stdout: JSON.stringify(rows) } } })
  assert.deepEqual(list.map((p) => p.number), [1, 2, 3])
  assert.ok(calls[0].includes('--state') && calls[0].includes('merged'))
  assert.ok(!calls[0].some((a) => /create|edit|comment/.test(a)))
})

test('补测：没缺口时什么都不做（不跑、不写文件）', () => {
  const { reportsDir, write } = catchFixture()
  for (const p of PRS) write(p.number, { tested_sha: p.merge_sha })
  let ran = 0
  const r = catchup.catchUp({ repo: 'o/r', now: NOW, reportsDir, list: () => PRS, runOne: () => { ran++; return 0 } })
  assert.equal(r.status, 'nothing-to-do'); assert.equal(ran, 0)
  assert.equal(existsSync(join(reportsDir, 'catch-up')), false)
})

test('补测：按顺序测缺口；锁超时停下留到下次；同一 PR 最多 3 次，放弃记进 system_health 和夜间日报', () => {
  const { reportsDir, write } = catchFixture()
  write(1, { tested_sha: 'a'.repeat(40) })
  const order = []
  // 第一次：PR 2 补上，PR 3 等锁超时 → 停
  let r = catchup.catchUp({ repo: 'o/r', now: NOW, reportsDir, list: () => PRS.slice().sort((a, b) => Date.parse(a.merged_at) - Date.parse(b.merged_at)), runOne: (p) => { order.push(p.number); if (p.number === 2) { write(2, { tested_sha: p.merge_sha }); return 0 } return 75 } })
  assert.deepEqual(order, [2, 3]); assert.equal(r.stopped, 'lock-timeout'); assert.equal(r.system_health.gave_up.length, 0)
  // 第二、三次：PR 3 仍然没补上 → 第 3 次放弃
  for (let i = 0; i < 2; i++) r = catchup.catchUp({ repo: 'o/r', now: NOW, reportsDir, list: () => PRS, runOne: () => 1 })
  assert.equal(r.system_health.gave_up.length, 1); assert.equal(r.system_health.gave_up[0].pr, 3); assert.equal(r.system_health.gave_up[0].attempts, 3)
  assert.equal(r.system_health.status, 'degraded')
  // 第四次：放弃的不再试，安静退出
  let ran = 0
  r = catchup.catchUp({ repo: 'o/r', now: NOW, reportsDir, list: () => PRS, runOne: () => { ran++; return 0 } })
  assert.equal(r.status, 'nothing-to-do'); assert.equal(ran, 0)
  const g = catchup.recentGiveUps({ reportsDir, now: Date.now() })
  assert.equal(g.length, 1); assert.equal(g[0].stage, 'catch-up'); assert.match(g[0].message, /gave up after 3 attempts/)
})

test('legacy-migration journey: a revision without the migration code is unavailable (exit 2) and still writes report.json', async () => {
  const { spawnSync } = await import('node:child_process')
  const cwd = mkdtempSync(join(tmpdir(), 'lmj-'))
  const out = join(cwd, 'out')
  const r = spawnSync(process.execPath, [join(import.meta.dirname, '../src/tools/legacy-migration-journey.mjs'), out], { cwd, encoding: 'utf8', timeout: 30_000 })
  assert.equal(r.status, 2, r.stderr)
  assert.ok(existsSync(join(out, 'report.json')))
  assert.match(r.stdout + r.stderr, /persistIdbStorage/)
})

// ── AI 补测（夜间）──────────────────────────────────────
const ai = await import('../src/aitests.mjs')

test('AI 补测缺口：只取 Jev 判为适用、在补测维度清单里、且没有已跑成的检查覆盖的维度', () => {
  const profile = { steps: [{ id: 'unit', covers: ['unit'] }, { id: 'api-probe', covers: ['permission-boundary'] }, { id: 'e2e', covers: ['db'] }] }
  const applicability = { applicable: ['unit', 'boundary', 'permission-boundary', 'db', 'visual', 'concurrency'] }
  const results = [{ id: 'unit', ok: true }, { id: 'api-probe', ok: false }, { id: 'e2e', unavailable: true }]
  assert.deepEqual(ai.toAiGaps({ applicability, profile, results }), ['boundary', 'db', 'concurrency'])
  assert.equal(ai.AI_GAP_DIMENSIONS.length, 12)
})

test('AI 补测上限：默认 2 次模型调用 / 25 分钟 / 每次 3 个，可用环境变量改；ARGUS_AITESTS=0 关闭', () => {
  assert.deepEqual(ai.aiTestCaps({}), { enabled: true, maxCalls: 2, maxWallMin: 25, maxTestsPerCall: 3 })
  assert.deepEqual(ai.aiTestCaps({ ARGUS_AITEST_MAX_CALLS: '1', ARGUS_AITEST_MAX_MIN: '10', ARGUS_AITEST_MAX_TESTS: '2', ARGUS_AITESTS: '0' }), { enabled: false, maxCalls: 1, maxWallMin: 10, maxTestsPerCall: 2 })
  assert.equal(ai.aiTestCaps({ ARGUS_AITEST_MAX_CALLS: 'x' }).maxCalls, 2)
})

test('AI 补测判定：通过且植入错误被发现 → kept；植入错误无效 / 没法跑 → discarded；当前代码失败 → fails-on-current', () => {
  assert.equal(ai.toAiTestResult({ ok: true }).result, 'kept')
  assert.equal(ai.toAiTestResult({ ok: false, unavailable: true, exitCode: 4 }).result, 'discarded')
  assert.equal(ai.toAiTestResult({ ok: false, unavailable: true, exitCode: 2, unavailableReasonZh: '重名' }).result, 'discarded')
  assert.equal(ai.toAiTestResult({ ok: false, exitCode: 1 }).result, 'fails-on-current')
  assert.equal(ai.toAiTestResult(null).result, 'discarded')
  assert.equal(ai.toAiTestResult({ timedOut: true }).result, 'discarded')
})

test('AI 补测步骤编号可接着上一轮编；每次调用的测试数上限生效', () => {
  const profile = { aiTests: { run: ['x', '{file}'], pathGlobs: ['src/**/*.argus.test.ts'] } }
  const t = { kind: 'coverage', purposeZh: 'p', covers: ['boundary'], filePath: 'src/a.argus.test.ts', content: 'x', mutation: { file: 'src/a.ts', find: 'a', replace: 'b' } }
  assert.deepEqual(ai.toAiTestSteps([t, t], profile, { offset: 3 }).map((s) => s.id), ['ai-test-4', 'ai-test-5'])
  assert.equal(ai.toValidTests({ tests: [t, t, t, t] }, { profile, gaps: ['boundary'], maxTests: 3 }).length, 3)
})

test('coverage.dimension_status：applicable_total / covered_total，检查覆盖、AI 补测覆盖、缺口、不适用', () => {
  const triage = { dimensions: { unit: { applicability: 'applicable' }, boundary: { applicability: 'applicable' }, db: { applicability: 'applicable' }, visual: { applicability: 'not-applicable' } } }
  const profile = { steps: [{ id: 'unit', covers: ['unit'] }] }
  const aiRes = { tests: [{ id: 'ai-test-1', result: 'kept', covers: ['boundary'] }, { id: 'ai-test-2', result: 'discarded', covers: ['db'] }] }
  const c = nightly.withDimensionStatus({ covered: ['unit'] }, triage, aiRes, profile, [{ id: 'unit', ok: true }])
  assert.equal(c.applicable_total, 3); assert.equal(c.covered_total, 2)
  assert.deepEqual(c.covered, ['unit', 'boundary'], 'covered derived from dimension_status')
  assert.equal(c.dimension_status.unit.status, 'covered'); assert.equal(c.dimension_status.boundary.status, 'covered-by-ai-test')
  assert.equal(c.dimension_status.db.status, 'gap'); assert.equal(c.dimension_status.visual.status, 'not-applicable')
  assert.deepEqual(c.ai_covered, ['boundary']); assert.deepEqual(c.gaps, ['db'])
})

test('appliesIf：被测版本没有检查对象（如 index.html）→ 不适用（不是没跑成，也不进 checks）', async () => {
  const { toNotApplicable } = await import('../src/runner.mjs')
  const src = mkdtempSync(join(tmpdir(), 'na-'))
  const step = { appliesIf: [{ file: 'index.html', reasonZh: 'no web entry' }] }
  assert.equal(toNotApplicable(step, src), 'no web entry')
  writeFileSync(join(src, 'index.html'), '<html></html>')
  assert.equal(toNotApplicable(step, src), null)
  const p = loadProfile('mivo-canvas-plugin')
  assert.deepEqual(p.steps.find((s) => s.id === 'web-build').appliesIf.map((r) => r.file), ['index.html'])
  assert.deepEqual(p.steps.find((s) => s.id === 'host-locale').appliesIf.map((r) => r.file), ['cindyplugin/check-host-locale.mjs'])
})

// ── 不稳定失败与合并前版本对比 ────────────────────────────
test('flaky 的 base 对比：没对比时写「未与合并前版本对比」；对比了记基线结果，严重度仍是 P2', () => {
  const none = nightly.flakyBaseComparison({ baseSha: 'b'.repeat(40), baseRun: 'not-run' })
  assert.equal(none.compared, false); assert.ok(none.note.includes('未与合并前版本对比')); assert.ok(none.note_zh.startsWith('未与合并前版本对比'))
  assert.ok(nightly.flakyBaseComparison({ baseSha: null, baseRun: 'not-run' }).note.includes('base revision unknown'))
  assert.equal(nightly.flakyBaseComparison({ baseSha: 'b', baseRun: 'timeout' }).compared, false)
  const fails = nightly.flakyBaseComparison({ baseSha: 'b', baseRun: 'fail' })
  assert.equal(fails.result, 'also-fails-on-base'); assert.ok(!fails.note.includes('未与合并前版本对比'))
  assert.equal(nightly.flakyBaseComparison({ baseSha: 'b', baseRun: 'pass' }).result, 'passes-on-base')
  assert.equal(nightly.flakyBaseComparison({ baseSha: 'b', baseRun: 'not-failing' }).result, 'passes-on-base')
  assert.equal(nightly.severityFor({ status: 'flaky', step: { kind: 'product' }, merged: true }), 'P2')
})

// ── 自检 / 自举 → system_health；内存护栏 ────────────────────
test('bootstrap 状态进 system_health：装好了记 note；装不上记 qa_tool_failures(stage bootstrap) + env_problems', () => {
  const h = () => ({ qa_tool_failures: [], env_problems: [], notes: [] })
  const ok = h(); nightly.recordBootstrap(ok, null); assert.equal(ok.bootstrap, undefined)
  const clean = h(); nightly.recordBootstrap(clean, { ran: false, missing_before: [], missing_after: [] })
  assert.equal(clean.bootstrap.ran, false); assert.equal(clean.qa_tool_failures.length, 0); assert.equal(clean.notes.length, 0)
  const fixed = h(); nightly.recordBootstrap(fixed, { ran: true, missing_before: ['missing: node24 — x', 'missing: apt:chromium'], missing_after: [] })
  assert.match(fixed.notes[0], /installed them: node24, apt:chromium/); assert.equal(fixed.qa_tool_failures.length, 0)
  const broken = h(); nightly.recordBootstrap(broken, { ran: true, missing_before: ['missing: apt:chromium'], missing_after: ['missing: chromium — /usr/lib/chromium/chromium'], log: '/l' })
  assert.equal(broken.qa_tool_failures[0].stage, 'bootstrap'); assert.equal(broken.env_problems[0].message, 'chromium — /usr/lib/chromium/chromium')
})

test('内存护栏：可用内存低于阈值或 ARGUS_MAX_PARALLEL=1 → 覆盖率轮次串行', () => {
  assert.equal(nightly.memAvailableGb('MemTotal: 16386504 kB\nMemAvailable:    3482656 kB\n'), 3.3)
  assert.equal(nightly.memAvailableGb('nothing'), null)
  assert.equal(nightly.parallelDecision({ freeGb: 3.3, minGb: 8, maxParallel: 2 }).parallel, false)
  assert.equal(nightly.parallelDecision({ freeGb: 10, minGb: 8, maxParallel: 2 }).parallel, true)
  assert.equal(nightly.parallelDecision({ freeGb: 10, minGb: 8, maxParallel: 1 }).parallel, false)
  assert.equal(nightly.parallelDecision({ freeGb: null, minGb: 8, maxParallel: 2 }).parallel, false)
})

// ── coverage 汇总只来自 dimension_status（#906：applicable_total=4 / covered_total=4 却列 exception 为 missed、4 个 undetermined）──
test('coverage 汇总从 dimension_status 推出：旧规则的 applicable / missed / undetermined 被覆盖，不会和 Jev 矛盾', () => {
  const triage = { dimensions: { unit: { applicability: 'applicable' }, exception: { applicability: 'applicable' }, data: { applicability: 'applicable' }, 'api-oracle': { applicability: 'applicable' }, visual: { applicability: 'not-applicable' } } }
  const profile = { steps: [{ id: 'unit', covers: ['unit', 'data', 'api-oracle'] }] }
  const aiRes = { tests: [{ id: 'ai-test-1', result: 'kept', covers: ['exception'] }] }
  // 旧规则（p ≥ 0.7）算出来的值：和 Jev 结论矛盾
  const legacy = { applicable: ['unit', 'exception'], covered: ['unit'], missed: ['exception'], undetermined: ['data', 'negative-path', 'api-oracle', 'sideEffects'], undeterminedMissed: [], passRate: 1, coverage: 0.5 }
  const c = nightly.withDimensionStatus(legacy, triage, aiRes, profile, [{ id: 'unit', ok: true }])
  assert.equal(c.applicable_total, 4); assert.equal(c.covered_total, 4)
  assert.deepEqual(c.applicable, ['unit', 'exception', 'data', 'api-oracle'])
  assert.deepEqual(c.covered, ['unit', 'exception', 'data', 'api-oracle'])
  assert.deepEqual(c.missed, []); assert.deepEqual(c.gaps, []); assert.deepEqual(c.undetermined, [])
  assert.equal(c.coverage, 1); assert.equal(c.passRate, 1, 'passRate is about checks and stays')
  assert.equal(c.not_applicable_total, 1); assert.equal(c.undetermined_total, 0)
  // 每个汇总都能从 dimension_status 重新数出来
  const ds = c.dimension_status
  for (const k of c.missed) assert.equal(ds[k].status, 'gap')
  for (const k of c.covered) assert.ok(['covered', 'covered-by-ai-test'].includes(ds[k].status))
  assert.equal(c.applicable_total, Object.values(ds).filter((d) => d.applicability === 'applicable').length)
})

test('coverage 汇总：Jev 没判定的维度 → undetermined（不算 applicable，也不算 missed）', () => {
  const dims = { unit: { applicability: 'applicable', status: 'gap', checks: [], ai_tests: [] }, db: { applicability: 'undetermined', status: 'undetermined', checks: ['unit'], ai_tests: [] }, ui: { applicability: 'undetermined', status: 'undetermined', checks: [], ai_tests: [] } }
  const t = nightly.coverageTotalsFrom(dims)
  assert.deepEqual(t.applicable, ['unit']); assert.deepEqual(t.missed, ['unit']); assert.equal(t.covered_total, 0); assert.equal(t.coverage, 0)
  assert.deepEqual(t.undetermined, ['db', 'ui']); assert.deepEqual(t.undeterminedMissed, ['ui']); assert.equal(t.undetermined_total, 2)
  assert.equal(nightly.coverageTotalsFrom({}).coverage, null)
})

// ── skipped_steps 去重（#906 / #898：web-build 既是 not applicable 又是「run stopped before this step」）──
test('skipped_steps：同一步骤只出现一次，保留第一个（最具体的）理由', () => {
  const list = [
    { id: 'bench', reason: 'triage: change does not touch what this optional step covers' },
    { id: 'web-build', reason: 'not applicable to this revision: no web entry (index.html)' },
    { id: 'web-build', reason: 'not run: run stopped before this step (time budget / shutdown)' },
  ]
  const d = nightly.dedupeSkippedSteps(list)
  assert.deepEqual(d.map((x) => x.id), ['bench', 'web-build'])
  assert.match(d[1].reason, /^not applicable/)
  assert.deepEqual(nightly.dedupeSkippedSteps(undefined), [])
})

// ── 运行来源 run.trigger ────────────────────────────────
test('run.trigger：event / catch-up / manual，默认 manual；其他值报错', () => {
  assert.equal(nightly.normalizeTrigger(undefined), 'manual')
  assert.equal(nightly.normalizeTrigger(''), 'manual')
  for (const t of ['event', 'catch-up', 'manual']) assert.equal(nightly.normalizeTrigger(t), t)
  assert.throws(() => nightly.normalizeTrigger('cron'), /--trigger must be one of/)
})

test('补测子运行：--trigger catch-up；只有 createIssues 时才带 --create-issues，否则 --no-issues', async () => {
  const { catchUpChildArgs } = await import('../src/catchup.mjs')
  const pr = { number: 905, day: '2026-10-11' }
  const a = catchUpChildArgs(pr, { script: '/x/run-nightly.sh', reportsDir: '/r' })
  assert.deepEqual(a.slice(0, 9), ['/x/run-nightly.sh', '--pr', '905', '--date', '2026-10-11', '--write-merged', '--wait-lock', '14400', '--no-issues'])
  assert.deepEqual(a.slice(9), ['--trigger', 'catch-up', '--out', '/r/catch-up/PR-905.json'])
  const b = catchUpChildArgs(pr, { script: '/x/run-nightly.sh', reportsDir: '/r', createIssues: true })
  assert.ok(b.includes('--create-issues') && !b.includes('--no-issues'))
})
