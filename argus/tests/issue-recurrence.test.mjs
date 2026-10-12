// 提 issue 的复发处理，用假 gh 端到端走一遍（不会碰真 GitHub）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const dir = mkdtempSync(join(tmpdir(), 'recur-'))
const statePath = join(dir, 'gh.json')
process.env.ARGUS_ROOT ??= dir
process.env.ARGUS_GH_BIN = join(dirname(fileURLToPath(import.meta.url)), 'fake-gh.mjs')
process.env.FAKE_GH_STATE = statePath
const nightly = await import('../src/nightly.mjs')

const REPO = 'PraiseZhu/pr-qa-agent-deploy'
const health = { status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [] }
const reportFor = (runId, fp) => ({ date: '2026-10-11', repo: 'xindong/mivo-canvas-plugin', run: { id: runId }, system_health: health,
  prs: [{ number: 7, author: 'PraiseZhu', title: 't', url: 'https://github.com/xindong/mivo-canvas-plugin/pull/7', state: 'merged', tested_sha: 'a'.repeat(40), base_sha: 'b'.repeat(40), merge_sha: 'a'.repeat(40),
    findings: [{ source: 'check', test_backed: true, check_id: 'unit', check_name: 'unit', severity: 'P0', status: 'introduced', attribution: 'x', fingerprint: fp, pr_fingerprint: `${fp}-pr7`, dimension: 'white-box', evidence: { rerun: 'fail', base_run: 'pass', log_tail: 'AssertionError: boom' } }] }] })
const setState = (issues, comments = {}) => writeFileSync(statePath, JSON.stringify({ issues, comments, log: [] }))
const state = () => JSON.parse(readFileSync(statePath, 'utf8'))
const issue = (n, fp, extra = {}) => ({ number: n, html_url: `https://github.com/x/y/issues/${n}`, state: 'open', state_reason: null, title: 'i', body: nightly.fingerprintMarker(fp), labels: ['qa-auto', 'cindy-dispatch'], ...extra })

test('没有同指纹 issue → 新开（labels qa-auto + cindy-dispatch）', async () => {
  setState([])
  const r = await nightly.createIssues(reportFor('run1', 'mq1-new'), { repo: REPO })
  assert.equal(r[0].action, 'created')
  const s = state()
  assert.equal(s.issues.length, 1); assert.deepEqual(s.issues[0].labels, ['qa-auto', 'cindy-dispatch'])
})

test('同指纹开放 issue → 评论新证据（同一次运行重复执行只评论一次），不新开', async () => {
  setState([issue(3, 'mq1-a')])
  const r = await nightly.createIssues(reportFor('run1', 'mq1-a'), { repo: REPO })
  assert.equal(r[0].action, 'commented')
  await nightly.createIssues(reportFor('run1', 'mq1-a'), { repo: REPO })
  let s = state()
  assert.equal(s.issues.length, 1); assert.equal(s.comments['3'].length, 1)
  assert.match(s.comments['3'][0].body, /Observed again/); assert.match(s.comments['3'][0].body, /AssertionError: boom/)
  await nightly.createIssues(reportFor('run2', 'mq1-a'), { repo: REPO })
  s = state(); assert.equal(s.comments['3'].length, 2, 'a new run adds new evidence')
})

test('修好关闭的 issue 复发 → 重开 + 评论', async () => {
  setState([issue(4, 'mq1-b', { state: 'closed', state_reason: 'completed' })])
  const r = await nightly.createIssues(reportFor('run1', 'mq1-b'), { repo: REPO })
  assert.equal(r[0].action, 'reopened')
  const s = state()
  assert.equal(s.issues[0].state, 'open'); assert.equal(s.issues.length, 1)
  assert.match(s.comments['4'][0].body, /Recurred/)
  assert.ok(s.log.some((l) => /^PATCH repos\/.*\/issues\/4$/.test(l)))
})

test('不修（not_planned 或 wontfix 标签）→ 沉默：不评论、不重开、不新开', async () => {
  setState([issue(5, 'mq1-c', { state: 'closed', state_reason: 'not_planned' }), issue(6, 'mq1-d', { state: 'closed', state_reason: 'completed', labels: ['qa-auto', 'wontfix'] })])
  const r1 = await nightly.createIssues(reportFor('run1', 'mq1-c'), { repo: REPO })
  const r2 = await nightly.createIssues(reportFor('run1', 'mq1-d'), { repo: REPO })
  assert.equal(r1[0].action, 'suppressed'); assert.equal(r2[0].action, 'suppressed')
  const s = state()
  assert.equal(s.issues.length, 2); assert.deepEqual(s.comments, {})
  assert.ok(!s.log.some((l) => /^(PATCH|POST)/.test(l)))
})

// ── system_health：一次运行一个 issue（清单），只收自修复后仍存在的问题；已有开放的 system_health issue 时只评论 ──
const healthReport = (runId, h) => ({ date: '2026-10-12', repo: 'xindong/mivo-canvas-plugin', run: { id: runId }, prs: [],
  system_health: { status: 'degraded', qa_tool_failures: [], env_problems: [], llm_errors: [], notes: [], ...h } })
const boxGone = () => {   // 2026-10-12 box replacement: bootstrap failed (apt lock), 7 items still missing
  const h = { qa_tool_failures: [], env_problems: [], notes: [] }
  const missing = ['apt:chromium', 'apt:cron', 'bwrap — x', 'chromium — /usr/lib/chromium/chromium', 'chromium-wrapper — y', 'catch-up-timer — z', 'cron-daemon — w'].map((m) => `missing: ${m}`)
  nightly.recordBootstrap(h, { ran: true, missing_before: missing, missing_after: missing, log: '/l' })
  return h
}

test('system_health：一次运行的多个环境问题 → 只开 ONE issue，正文带全部清单', async () => {
  setState([])
  const h = boxGone()
  assert.equal(h.qa_tool_failures.length + h.env_problems.length, 8)
  const r = await nightly.createIssues(healthReport('run-a', h), { repo: REPO })
  assert.deepEqual(r.map((x) => x.action), ['created'])
  const s = state()
  assert.equal(s.issues.length, 1)
  for (const m of ['apt:chromium', 'apt:cron', 'chromium-wrapper', 'catch-up-timer', 'cron-daemon', 'bootstrap-box.sh could not install 7']) assert.ok(s.issues[0].body.includes(m), m)
  assert.match(s.issues[0].title, /\[system_health\] 8 problems/)
})

test('system_health：已有开放 system_health issue（含旧版逐条 issue）→ 评论，不新开；同一运行只评论一次', async () => {
  setState([])
  await nightly.createIssues(healthReport('run-a', boxGone()), { repo: REPO })
  await nightly.createIssues(healthReport('run-b', { env_problems: [{ message: 'low disk: 5 GB free' }] }), { repo: REPO })
  await nightly.createIssues(healthReport('run-b', { env_problems: [{ message: 'low disk: 5 GB free' }] }), { repo: REPO })
  let s = state()
  assert.equal(s.issues.length, 1); assert.equal(s.comments[String(s.issues[0].number)].length, 1)
  assert.match(s.comments[String(s.issues[0].number)][0].body, /low disk/)
  // legacy per-entry issue (body has the mivo-qa-health block, different fingerprint) still open → comment on it
  setState([issue(12, 'mq1-legacy', { title: '[Mivo QA][system_health] environment problem: apt:cron', body: '```json mivo-qa-health\n{}\n```\n' + nightly.fingerprintMarker('mq1-legacy') })])
  const r = await nightly.createIssues(healthReport('run-c', boxGone()), { repo: REPO })
  assert.deepEqual(r.map((x) => x.action), ['commented'])
  s = state(); assert.equal(s.issues.length, 1); assert.equal(s.comments['12'].length, 1)
})

test('system_health：自举后已修好的（transient）只记在报告里，不提 issue', async () => {
  setState([])
  const h = { qa_tool_failures: [], env_problems: [], notes: [] }
  nightly.recordBootstrap(h, { ran: true, missing_before: ['missing: apt:chromium', 'missing: cron-daemon — c'], missing_after: [] })
  assert.deepEqual(h.bootstrap.self_repaired, ['apt:chromium', 'cron-daemon'])
  assert.equal(h.env_problems.length + h.qa_tool_failures.length, 0)
  let r = await nightly.createIssues(healthReport('run-d', h), { repo: REPO })
  assert.equal(r.length, 0); assert.equal(state().issues.length, 0)
  // entries explicitly marked transient / self_repaired are excluded as well
  r = await nightly.createIssues(healthReport('run-e', { env_problems: [{ message: 'apt lock held', transient: true }], qa_tool_failures: [{ stage: 'x', message: 'y', self_repaired: true }] }), { repo: REPO })
  assert.equal(r.length, 0); assert.equal(state().issues.length, 0)
  // partially repaired: only the leftover is reported; repaired list is in the issue as context
  const p = { qa_tool_failures: [], env_problems: [], notes: [] }
  nightly.recordBootstrap(p, { ran: true, missing_before: ['missing: apt:chromium', 'missing: cron-daemon — c'], missing_after: ['missing: cron-daemon — c'] })
  assert.deepEqual(p.bootstrap.self_repaired, ['apt:chromium'])
  r = await nightly.createIssues(healthReport('run-f', p), { repo: REPO })
  assert.deepEqual(r.map((x) => x.action), ['created'])
  const body = state().issues[0].body
  assert.match(body, /cron-daemon/); assert.match(body, /Self-repaired during this run.*apt:chromium/)
})
