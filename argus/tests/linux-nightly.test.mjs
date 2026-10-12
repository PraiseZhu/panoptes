// Linux 夜间 QA：平台覆盖、上海日窗口、指纹稳定性、bwrap 隔离、ming-qa 维度与审查文档规范化
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { spawnSync } from 'node:child_process'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-linux-'))
const { applyPlatformOverrides, withSecretAliases } = await import('../src/config.mjs')
const { shanghaiDayWindow } = await import('../src/github.mjs')
const { previousShanghaiDay, fingerprintOf } = await import('../src/nightly.mjs')
const { bwrapArgs, sandboxMode } = await import('../src/runner.mjs')
const { toBox, toCodeReviewDoc } = await import('../src/mingqa.mjs')
const { toChatCompletionsUrl } = await import('../src/util.mjs')

test('平台覆盖：按 id 合并步骤 env、删除 null 步骤、追加 extraSteps', () => {
  const prof = { env: { A: '1' }, steps: [{ id: 'e2e', env: { X: 'mac' }, requires: [1] }, { id: 'gone' }, { id: 'unit' }],
    platforms: { linux: { env: { B: '2' }, steps: { e2e: { env: { X: 'linux' }, requires: [] }, gone: null }, extraSteps: [{ id: 'pw' }] } } }
  const out = applyPlatformOverrides(prof, 'linux')
  assert.deepEqual(out.env, { A: '1', B: '2' })
  assert.deepEqual(out.steps.map((s) => s.id), ['e2e', 'unit', 'pw'])
  assert.deepEqual(out.steps[0].env, { X: 'linux' })
  assert.equal(applyPlatformOverrides(prof, 'darwin'), prof)
})

test('上海日窗口与「前一天」', () => {
  const w = shanghaiDayWindow('2026-10-10')
  assert.equal(w.startIso, '2026-10-09T16:00:00.000Z')
  assert.equal(w.endIso, '2026-10-10T16:00:00.000Z')
  assert.equal(previousShanghaiDay(new Date('2026-10-10T18:30:00Z')), '2026-10-10') // 上海 10-11 02:30
  assert.equal(previousShanghaiDay(new Date('2026-10-10T15:30:00Z')), '2026-10-09') // 上海 10-10 23:30
  assert.throws(() => shanghaiDayWindow('2026/10/10'))
})

test('指纹不随 SHA、耗时、临时路径、行列号变化', () => {
  const a = fingerprintOf('o/r', ['check', 'unit', 'src/a.test.ts > works (12ms) at /tmp/argus-1/x:10:5 abcdef1234567'])
  const b = fingerprintOf('o/r', ['check', 'unit', 'src/a.test.ts > works (873ms) at /tmp/argus-2/y:99:1 1234567abcdef'])
  assert.equal(a, b)
  assert.match(a, /^mq1-[0-9a-f]{20}$/)
  assert.notEqual(a, fingerprintOf('o/r', ['check', 'lint', 'src/a.test.ts > works']))
})

test('凭证别名与网关地址补全', () => {
  assert.deepEqual(withSecretAliases({ QA_LLM_BASE_URL: 'u', QA_LLM_API_KEY: 'k' }), { QA_LLM_BASE_URL: 'u', QA_LLM_API_KEY: 'k', ARGUS_LLM_BASE_URL: 'u', ARGUS_LLM_API_KEY: 'k' })
  assert.equal(withSecretAliases({ ARGUS_LLM_API_KEY: 'a', QA_LLM_API_KEY: 'b' }).ARGUS_LLM_API_KEY, 'a')
  assert.equal(toChatCompletionsUrl('https://g.example/'), 'https://g.example/v1/chat/completions')
  assert.equal(toChatCompletionsUrl('https://g.example/v1'), 'https://g.example/v1/chat/completions')
})

test('三维度归类：profile.boxes 优先，其次按 kind/covers', () => {
  assert.equal(toBox({ id: 'x', kind: 'product', covers: ['e2e'] }), 'black-box')
  assert.equal(toBox({ id: 'x', kind: 'product', covers: ['api'] }), 'gray-box')
  assert.equal(toBox({ id: 'lint', kind: 'quality' }), 'white-box')
  assert.equal(toBox({ id: 'plugin', kind: 'product', covers: ['regression'] }, { boxes: { plugin: 'gray-box' } }), 'gray-box')
})

test('审查文档规范化成 ming-qa code-review.json 形状', () => {
  const doc = toCodeReviewDoc({ findings: [{ severity: 'p1', file: 'a.ts', line: '12', title: 't', evidence: 'e', verdict: 'confirmed' }, { severity: 'weird' }] }, [{ filename: 'a.ts' }])
  assert.equal(doc.status, 'failed')
  assert.deepEqual(doc.scope.files, ['a.ts'])
  assert.equal(doc.findings[0].severity, 'P1')
  assert.equal(doc.findings[0].line, 12)
  assert.equal(doc.findings[0].verdict, 'CONFIRMED')
  assert.equal(doc.findings[1].severity, 'P2')
  assert.equal(doc.findings[1].verdict, 'PLAUSIBLE')
})

test('bwrap 隔离：/home 与 /workspace 不可见，根只读，run 目录可写', { skip: process.platform !== 'linux' || sandboxMode() !== 'bwrap' }, async () => {
  const runDir = mkdtempSync(join(tmpdir(), 'argus-sbx-'))
  const outside = join(tmpdir(), `argus-outside-${process.pid}`)
  writeFileSync(outside, 'secret')
  // ARGUS_ROOT 若在 /workspace 下，缓存目录会被挂回；除此之外 /workspace 必须为空
  const { CACHE_DIR } = await import('../src/paths.mjs')
  // Node 安装目录（nvm 在 /workspace/_tools/nvm 时）也只读挂回，它的顶层目录可见
  const nodePrefix = dirname(dirname(process.execPath))
  const allowedWs = [CACHE_DIR, nodePrefix].filter((p) => p.startsWith('/workspace/')).map((p) => p.split('/')[2])
  const grepArgs = allowedWs.map((x) => `-e "${x}"`).join(' ') || '-e __none__'
  const script = `(ls -d /home/*/.config /home/*/.ssh 2>/dev/null; ls -A /root 2>/dev/null) | wc -l; ls -A /workspace 2>/dev/null | grep -vx ${grepArgs} | wc -l; cat ${outside} 2>&1 | head -c 200 | tr -d "\n"; echo; touch /usr/argus-probe 2>/dev/null && echo rootrw || echo rootro; touch ${runDir}/ok && echo runrw`
  const res = spawnSync('/usr/bin/bwrap', [...bwrapArgs(runDir), '--', 'sh', '-c', script], { encoding: 'utf8' })
  // /home 下只剩只读挂回的 Node 安装目录（nvm），没有 .config / .ssh 等
  const lines = res.stdout.trim().split('\n')
  assert.equal(lines[0], '0')
  assert.equal(lines[1], '0')
  assert.match(lines[2], /No such file/)
  assert.equal(lines[3], 'rootro')
  assert.equal(lines[4], 'runrw')
  assert.ok(existsSync(join(runDir, 'ok')))
})

test('issue 载荷（不联网）：私有仓、qa-auto + cindy-dispatch、指纹标记与可解析证据块', async () => {
  const { buildIssuePayload, ISSUE_REPO, ISSUE_LABELS, fingerprintMarker } = await import('../src/nightly.mjs')
  assert.equal(ISSUE_REPO, 'PraiseZhu/pr-qa-agent-deploy')
  assert.deepEqual(ISSUE_LABELS, ['qa-auto', 'cindy-dispatch'])
  const report = { date: '2026-10-10', repo: 'xindong/mivo-canvas-plugin', run: { id: 'r1' } }
  const pr = { number: 878, title: 't', author: 'a', url: 'u', state: 'open', tested_sha: 'x', base_sha: 'y', evidence_dir: '/e' }
  const f = { source: 'check', check_id: 'unit', check_name: '单元测试', dimension: 'white-box', severity: 'P1', status: 'introduced', attribution: 'why', test: 'a > b', fingerprint: 'mq1-abc', pr_fingerprint: 'mq1-abc-pr878', evidence: { rerun: 'fail', base_run: 'not-failing', log_tail: 'boom token=ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaa' } }
  const p = buildIssuePayload(report, pr, f)
  assert.deepEqual(p.labels, ISSUE_LABELS)
  assert.ok(p.body.includes(fingerprintMarker('mq1-abc')))
  const json = JSON.parse(p.body.split('```json mivo-qa-finding\n')[1].split('\n```')[0])
  assert.equal(json.schema, 'mivo-qa-finding/v1')
  assert.equal(json.fingerprint, 'mq1-abc')
  assert.equal(json.pr.number, 878)
  assert.ok(!p.body.includes('ghp_aaaa'))
})

test('issue 资格：只有失败检查佐证的 P0/P1 + 作者 PraiseZhu；LLM 审查单独结论永不提；system_health 照提', async () => {
  const { selectIssueCandidates, buildIssuePayloadFor, fingerprintMarker } = await import('../src/nightly.mjs')
  const chk = (sev, fp) => ({ source: 'check', check_id: 'unit', dimension: 'white-box', severity: sev, status: 'introduced', fingerprint: fp, evidence: {} })
  const cr = (sev, fp) => ({ source: 'code-review', check_id: 'code-review', dimension: 'white-box', severity: sev, verdict: 'CONFIRMED', fingerprint: fp, evidence: {} })
  const report = {
    date: '2026-10-10', repo: 'xindong/mivo-canvas-plugin', run: { id: 'r' },
    prs: [
      { number: 1, author: 'PraiseZhu', findings: [chk('P1', 'a'), chk('P0', 'b'), chk('P2', 'c'), cr('P1', 'd'), cr('P0', 'e')] },
      { number: 2, author: 'someone-else', findings: [chk('P0', 'f'), cr('P1', 'g')] },
    ],
    system_health: { status: 'degraded', qa_tool_failures: [{ pr: 2, stage: 'e2e-browser', message: 'check could not run: x' }], env_problems: [{ message: 'low disk: 5 GB free' }], llm_errors: [{ pr: 2, stage: 'code-review', kind: 'http', status: 502, message: 'bad gateway' }] },
  }
  const { candidates, skipped } = selectIssueCandidates(report)
  assert.deepEqual(candidates.filter((c) => c.kind === 'finding').map((c) => c.fingerprint).sort(), ['a', 'b'])
  const hc = candidates.filter((c) => c.kind === 'health')
  assert.equal(hc.length, 1, 'system_health 不受作者限制，且一次运行聚合成一个候选')
  assert.equal(hc[0].items.length, 3)
  assert.deepEqual(skipped.map((s) => s.fingerprint).sort(), ['d', 'e', 'f', 'g'])
  assert.ok(skipped.filter((s) => s.source === 'code-review').every((s) => /llm-review-only/.test(s.reason)))
  for (const c of candidates) {
    const p = buildIssuePayloadFor(report, c)
    assert.ok(p.body.includes(fingerprintMarker(c.fingerprint)))
    assert.deepEqual(p.labels, ['qa-auto', 'cindy-dispatch'])
  }
  // 健康指纹稳定、与运行无关
  const again = selectIssueCandidates({ ...report, run: { id: 'other' } }).candidates.filter((c) => c.kind === 'health').map((c) => c.fingerprint)
  assert.deepEqual(again, candidates.filter((c) => c.kind === 'health').map((c) => c.fingerprint))
})

test('createIssue 只能从选出的候选里走（报告里 review-only 的 P1 不会成为候选）', async () => {
  const { selectIssueCandidates } = await import('../src/nightly.mjs')
  const report = { date: 'd', repo: 'r', prs: [{ number: 893, author: 'PraiseZhu', findings: [{ source: 'code-review', severity: 'P1', fingerprint: 'x' }] }], system_health: { status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [] } }
  assert.equal(selectIssueCandidates(report).candidates.length, 0)
})

test('合并即测结果复用：同 PR、同 merge commit、真实跑完才复用', async () => {
  const { loadMergedResult, mergedReportPath } = await import('../src/nightly.mjs')
  const { mkdirSync } = await import('node:fs')
  const dir = mkdtempSync(join(tmpdir(), 'argus-merged-'))
  mkdirSync(dir, { recursive: true })
  const write = (n, pr, extra = {}) => writeFileSync(mergedReportPath(n, dir), JSON.stringify({ generated_at: 'g', prs: [pr], system_health: { status: 'ok' }, ...extra }))
  write(10, { number: 10, state: 'merged', verdict: 'clean', tested_sha: 'aaa' })
  assert.equal(loadMergedResult(10, 'aaa', { mergedDir: dir }).pr.number, 10)
  assert.equal(loadMergedResult(10, 'bbb', { mergedDir: dir }), null, '不同 merge commit 不复用')
  assert.equal(loadMergedResult(11, 'aaa', { mergedDir: dir }), null, '没有文件不复用')
  assert.equal(loadMergedResult(10, 'aaa', { mergedDir: dir, selfOut: mergedReportPath(10, dir) }), null, '写自己的文件时不复用自己')
  write(12, { number: 12, state: 'merged', verdict: 'qa-error', tested_sha: 'ccc' })
  assert.equal(loadMergedResult(12, 'ccc', { mergedDir: dir }), null, '上次出错不复用')
  write(13, { number: 99, state: 'merged', verdict: 'clean', tested_sha: 'ddd' })
  assert.equal(loadMergedResult(13, 'ddd', { mergedDir: dir }), null, 'PR 号对不上不复用')
  write(14, { number: 14, state: 'merged', verdict: 'docs-only', tested_sha: null, merge_sha: 'eee' })
  assert.ok(loadMergedResult(14, 'eee', { mergedDir: dir }), '只改文档按 merge_sha 复用')
  writeFileSync(mergedReportPath(15, dir), '{broken')
  assert.equal(loadMergedResult(15, 'x', { mergedDir: dir }), null)
})

test('--out：默认写每日报告；显式路径照写；不能指向每日报告文件', async () => {
  const { resolveOutPath } = await import('../src/nightly.mjs')
  const { REPORTS_DIR } = await import('../src/paths.mjs')
  assert.equal(resolveOutPath(null, '2026-10-10'), join(REPORTS_DIR, '2026-10-10.json'))
  assert.equal(resolveOutPath(join(REPORTS_DIR, 'merged/PR-893.json'), '2026-10-10'), join(REPORTS_DIR, 'merged/PR-893.json'))
  assert.throws(() => resolveOutPath(join(REPORTS_DIR, '2026-10-09.json'), '2026-10-10'), /每日报告/)
})
