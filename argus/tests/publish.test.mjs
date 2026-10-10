// 发布链路：用假 gh 模拟「响应丢失」「回查断网」「读 issue 断网」「issue 已关闭」等情况
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = mkdtempSync(join(tmpdir(), 'argus-pub-'))
process.env.ARGUS_ROOT = root
process.env.ARGUS_GH_BIN = join(dirname(fileURLToPath(import.meta.url)), 'fake-gh.mjs')
process.env.ARGUS_GH_RETRY_MS = '1'
const statePath = join(root, 'gh.json')
process.env.FAKE_GH_STATE = statePath

const { openDb, pendingOutbox } = await import('../src/db.mjs')
const { publishDefect, retryPendingIssues } = await import('../src/publish.mjs')

const reg = { owner: { login: 'PraiseZhu', id: 1 }, issueRepo: 'PraiseZhu/deploy' }
const results = [{ id: 'unit', nameZh: '单元测试', kind: 'product', cmdText: 'npm test', ok: false, exitCode: 1, tail: '✖ sum works' }]
const decision = { outcome: 'defect', reasonZh: 'r', failedSteps: ['unit'], failedTests: ['sum works'], failureKeys: ['unit::sum works'] }
const mkJob = (id) => ({ id, repo: 'a/b', pr_number: id, pr_url: `u${id}`, pr_title: `fix: 第 ${id} 个`, merge_sha: 'abcdef1234567890' })

let dbN = 0
function setup(state = {}) {
  writeFileSync(statePath, JSON.stringify({ issues: [], comments: {}, faults: [], ...state }))
  return openDb(join(root, `db${++dbN}.sqlite`))
}
const gh = () => JSON.parse(readFileSync(statePath, 'utf8'))
const publish = (db, id) => publishDefect({ db, reg, job: mkJob(id), decision, results, ai: null, displayName: 'B' })

test('新建 issue 时响应丢失：下一轮先回查到已建好的那条，不重复建', async () => {
  const db = setup({ faults: [{ match: '^POST repos/PraiseZhu/deploy/issues$', mode: 'lost' }] })
  const first = await publish(db, 1)
  assert.equal(first.action, 'pending')
  assert.equal(gh().issues.length, 1)
  await retryPendingIssues(db, reg)
  assert.equal(gh().issues.length, 1)
  assert.equal(pendingOutbox(db, 'issue').length, 0)
})

test('响应丢失且回查也断网：保持不明、不补发；网络恢复后绑定原 issue', async () => {
  const db = setup({ faults: [
    { match: '^POST repos/PraiseZhu/deploy/issues$', mode: 'lost' },
    { match: '^GET repos/PraiseZhu/deploy/issues\\?', mode: 'network' },
    { match: '^GET repos/PraiseZhu/deploy/issues\\?', mode: 'network' },
    { match: '^GET repos/PraiseZhu/deploy/issues\\?', mode: 'network' },
  ] })
  await publish(db, 1)
  await retryPendingIssues(db, reg)
  assert.equal(gh().issues.length, 1, '回查失败时不能再发一条')
  assert.equal(pendingOutbox(db, 'issue').length, 1)
  await retryPendingIssues(db, reg)
  assert.equal(gh().issues.length, 1)
  assert.equal(pendingOutbox(db, 'issue').length, 0)
})

test('已有 issue 但读取状态断网：不新开，稍后补一条评论', async () => {
  const db = setup()
  await publish(db, 1)
  const s = gh()
  s.faults = [1, 2, 3].map(() => ({ match: '^GET repos/PraiseZhu/deploy/issues/1$', mode: 'network' }))
  writeFileSync(statePath, JSON.stringify(s))
  const res = await publish(db, 2)
  assert.equal(res.action, 'pending')
  assert.equal(gh().issues.length, 1)
  await retryPendingIssues(db, reg)
  assert.equal(gh().issues.length, 1)
  assert.equal(gh().comments['1'].length, 1)
})

test('评论响应丢失：回查到标记就不再发第二条', async () => {
  const db = setup()
  await publish(db, 1)
  const s = gh()
  s.faults = [{ match: '^POST repos/PraiseZhu/deploy/issues/1/comments$', mode: 'lost' }]
  writeFileSync(statePath, JSON.stringify(s))
  assert.equal((await publish(db, 2)).action, 'pending')
  await retryPendingIssues(db, reg)
  await retryPendingIssues(db, reg)
  assert.equal(gh().comments['1'].length, 1)
})

test('修好关掉后复发：重开原 issue；标记不修的不再打扰', async () => {
  const db = setup()
  await publish(db, 1)
  let s = gh()
  s.issues[0].state = 'closed'
  s.issues[0].state_reason = 'completed'
  writeFileSync(statePath, JSON.stringify(s))
  assert.equal((await publish(db, 2)).action, 'reopened')
  s = gh()
  assert.equal(s.issues.length, 1)
  assert.equal(s.issues[0].state, 'open')
  assert.equal(s.comments['1'].length, 1)
  s.issues[0].state = 'closed'
  s.issues[0].state_reason = 'not_planned'
  writeFileSync(statePath, JSON.stringify(s))
  assert.equal((await publish(db, 3)).action, 'suppressed')
  assert.equal(gh().comments['1'].length, 1)
  assert.equal(gh().issues.length, 1)
})

test('另一套部署（或重建账本）已为同一缺陷开过 issue：按指纹找到并追加评论，不重复开', async () => {
  const dbA = setup()
  await publish(dbA, 1)
  const dbB = openDb(join(root, `other-${Date.now()}.sqlite`))
  const res = await publishDefect({ db: dbB, reg, job: mkJob(2), decision, results, ai: null, displayName: 'B' })
  assert.equal(res.action, 'commented')
  assert.equal(gh().issues.length, 1)
  assert.equal(gh().comments['1'].length, 1)
})
