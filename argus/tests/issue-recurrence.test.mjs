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
