// 审查第 4–7 项：漏扫、日报误导、测试隔离、日报重复发送
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { existsSync } from 'node:fs'

const here = dirname(fileURLToPath(import.meta.url))
const root = mkdtempSync(join(tmpdir(), 'argus-hard-'))
process.env.ARGUS_ROOT = root
process.env.ARGUS_GH_BIN = join(here, 'fake-gh.mjs')
process.env.ARGUS_GH_RETRY_MS = '1'
const statePath = join(root, 'gh.json')
process.env.FAKE_GH_STATE = statePath

const { openDb, insertJob, finishJob, setMeta, getMeta, getOutbox, markRunning, requeueJob } = await import('../src/db.mjs')
const { pollOnce } = await import('../src/control.mjs')
const { buildDailyReport } = await import('../src/report.mjs')
const { sendDailyReport } = await import('../src/daily.mjs')
const { runSteps, newRunDir } = await import('../src/runner.mjs')
const { toPlainZh } = await import('../src/util.mjs')

let n = 0
const freshDb = () => openDb(join(root, `h${++n}.sqlite`))
const reg = { owner: { login: 'PraiseZhu', id: 7 }, issueRepo: 'x/deploy', enabledSince: '2026-09-01T00:00:00Z', projects: [{ repo: 'a/b', displayName: 'B 项目', profileData: { steps: [] } }] }

const pr = (i, updated) => ({ number: i, node_id: `N${i}`, title: `t${i}`, html_url: `u${i}`, user: { id: 7, login: 'PraiseZhu' }, base: { ref: 'main' }, merged_at: updated, merge_commit_sha: `m${i}`, updated_at: updated })

test('扫描翻到上限还没扫完：报错且不推进水位', async () => {
  const pulls = Array.from({ length: 3000 }, (_, i) => pr(i + 1, new Date(Date.now() - i * 1000).toISOString()))
  writeFileSync(statePath, JSON.stringify({ issues: [], comments: {}, faults: [], pulls }))
  const db = freshDb()
  await assert.rejects(pollOnce(db, reg), /没扫到|读取失败/)
  assert.equal(getMeta(db, 'scan_ok_at:a/b'), null)
})

test('扫描完整：推进水位；下次从水位前 2 小时扫，停机再久也能补回', async () => {
  const pulls = [pr(1, new Date(Date.now() - 3 * 86400_000).toISOString())]
  writeFileSync(statePath, JSON.stringify({ issues: [], comments: {}, faults: [], pulls }))
  const db = freshDb()
  assert.equal(await pollOnce(db, reg), 1)
  assert.ok(getMeta(db, 'scan_ok_at:a/b'))
  setMeta(db, 'scan_ok_at:a/b', new Date(Date.now() - 40 * 86400_000).toISOString())
  const old = [pr(2, new Date(Date.now() - 39 * 86400_000).toISOString())]
  writeFileSync(statePath, JSON.stringify({ issues: [], comments: {}, faults: [], pulls: old }))
  const longReg = { ...reg, enabledSince: '2026-01-01T00:00:00Z' }
  assert.equal(await pollOnce(db, longReg), 1, '39 天前合并、水位在 40 天前的 PR 也要入账')
})

function job(db, i, outcome, detail = {}, repo = 'a/b', title = `fix: 第 ${i} 个`) {
  insertJob(db, { repo, prNumber: i, prNodeId: `J${i}`, prTitle: title, prUrl: `u${i}`, authorLogin: 'PraiseZhu', baseRef: 'main', mergedAt: `2026-09-20T0${i}:00:00Z`, mergeSha: `s${i}` })
  finishJob(db, db.prepare('SELECT id FROM jobs WHERE pr_node_id = ?').get(`J${i}`).id, { outcome, detail: { coverage: { applicable: [], covered: [], missed: [], undetermined: [] }, ...detail } })
}

test('日报：只有未接入项目时不说「测了 N 个」', () => {
  const db = freshDb()
  setMeta(db, 'heartbeat_at', new Date().toISOString())
  job(db, 1, 'not_enrolled', { reasonZh: 'x' })
  const { text } = buildDailyReport({ db, reg, now: new Date(Date.now() + 1000) })
  assert.match(text, /结论：今天不用你处理。更新了 1 个 PR 的结果：0 个已执行检查未见新问题，1 个未完成验证/)
  assert.ok(text.includes('⏸ B 项目 #1'), text)
  assert.ok(text.includes('【可选】B 项目 有 PR 合并但还没接入自动测试'), text)
})

test('日报：issue 还没发出去时不说「已记到 issue」', () => {
  const db = freshDb()
  setMeta(db, 'heartbeat_at', new Date().toISOString())
  job(db, 1, 'defect', { issuePending: true, failedSteps: ['unit'], results: [{ id: 'unit', nameZh: '单元测试', kind: 'product', ok: false }], reasonZh: 'r' })
  const { text } = buildDailyReport({ db, reg, now: new Date(Date.now() + 1000) })
  assert.match(text, /issue 还在发布中/)
  assert.ok(!/已记到 issue/.test(text))
})

test('日报：PR 标题里的命令、路径、提交号不外露', () => {
  const db = freshDb()
  setMeta(db, 'heartbeat_at', new Date().toISOString())
  job(db, 1, 'passed', { results: [{ id: 'unit', nameZh: '单元测试', kind: 'product', ok: true }] }, 'a/b', 'fix: 调整 npm run build 与 src/index.ts；sha 123abc')
  const { text } = buildDailyReport({ db, reg, now: new Date(Date.now() + 1000) })
  assert.ok(!/npm|src\/|index\.ts|123abc/.test(text), text)
  assert.equal(toPlainZh('支持 UI/UX 与 A/B 测试'), '支持 UI/UX 与 A/B 测试')
})

const secrets = { ARGUS_FEISHU_APP_ID: 'a', ARGUS_FEISHU_APP_SECRET: 'b', ARGUS_FEISHU_OPEN_ID: 'c' }
function stubFeishu({ sendDelayMs = 50, fail = false } = {}) {
  const calls = { send: 0 }
  globalThis.fetch = async (url) => {
    if (String(url).includes('tenant_access_token')) return { json: async () => ({ code: 0, tenant_access_token: 't' }) }
    calls.send++
    await new Promise((r) => setTimeout(r, sendDelayMs))
    if (fail) throw new Error('socket hang up')
    return { status: 200, json: async () => ({ code: 0, data: { message_id: 'om_1' } }) }
  }
  return calls
}

test('日报：两个进程同时发，只有一个真正发出', async () => {
  const db = freshDb()
  setMeta(db, 'heartbeat_at', new Date().toISOString())
  const calls = stubFeishu()
  const [a, b] = await Promise.all([sendDailyReport({ db, reg, secrets }), sendDailyReport({ db, reg, secrets })])
  assert.deepEqual([a.status, b.status].sort(), ['busy', 'sent'])
  assert.equal(calls.send, 1)
  assert.equal((await sendDailyReport({ db, reg, secrets })).status, 'already_sent')
  assert.equal(calls.send, 1)
})

test('日报：结果不明超过去重窗口就不再重发，下一份提示并合并内容', async () => {
  const db = freshDb()
  setMeta(db, 'heartbeat_at', new Date().toISOString())
  stubFeishu({ fail: true })
  const day1 = new Date()
  const dayKey = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(day1)
  await assert.rejects(sendDailyReport({ db, reg, secrets, now: day1 }))
  const calls2 = stubFeishu()
  const res = await sendDailyReport({ db, reg, secrets, now: new Date(day1.getTime() + 60 * 60_000) })
  assert.equal(res.status, 'gave_up')
  assert.equal(calls2.send, 0)
  assert.equal(getOutbox(db, `daily:${dayKey}`).status, 'given_up')
  const next = buildDailyReport({ db, reg, now: new Date(day1.getTime() + 86400_000) })
  assert.ok(next.text.includes(`${Number(dayKey.slice(5, 7))}月${Number(dayKey.slice(8, 10))}日的日报可能没送到`), next.text)
})

test('隔离：测试步骤读不到家目录里的文件，读写自己的测试目录正常', { skip: process.platform !== 'darwin' || !existsSync('/usr/bin/sandbox-exec') }, async () => {
  const runDir = newRunDir(1, 'sbx')
  const secretLike = join(here, '..', 'package.json') // 位于家目录下
  const script = `const fs=require('fs');fs.writeFileSync(${JSON.stringify(join(runDir, 'ok.txt'))},'1');try{fs.readFileSync(${JSON.stringify(secretLike)});process.exit(3)}catch(e){process.exit(e.code==='EPERM'?0:4)}`
  const profile = { steps: [{ id: 'probe', nameZh: '隔离探针', kind: 'product', cmd: [process.execPath, '-e', script] }] }
  const [res] = await runSteps(profile, runDir, runDir)
  assert.equal(res.exitCode, 0, res.tail)
  assert.equal(readFileSync(join(runDir, 'ok.txt'), 'utf8'), '1')
})

test('日报：按项目汇总全部 issue 的实时状态，包括之前没解决的和刚被关掉的', () => {
  const db = freshDb()
  setMeta(db, 'heartbeat_at', new Date().toISOString())
  const now = new Date()
  const ago = (d) => new Date(now.getTime() - d * 86400_000).toISOString()
  setMeta(db, 'last_report_cutoff', ago(1))
  const boardReg = { ...reg, projects: [
    { repo: 'a/b', displayName: 'B 项目', profileData: { steps: [] } },
    { repo: 'c/d', displayName: 'D 项目', profileData: { steps: [] } },
    { repo: 'e/f', displayName: 'F 项目', profileData: { steps: [] } },
    { repo: 'x/canary', displayName: '金丝雀', canary: true, profileData: { steps: [] } },
  ] }
  const url = 'https://github.com/x/deploy/issues/3'
  for (const i of [1, 2]) {
    insertJob(db, { repo: 'a/b', prNumber: i, prNodeId: `B${i}`, prTitle: 't', prUrl: 'u', authorLogin: 'PraiseZhu', baseRef: 'main', mergedAt: ago(5), mergeSha: `s${i}`, status: 'done', outcome: 'defect', detail: { issueUrl: url, ai: { status: 'ok', failureZh: '导出的图片会丢透明通道。' } } })
  }
  // 这两个缺陷是 5 天前测完的老问题，不属于本次日报窗口
  db.prepare('UPDATE jobs SET finished_at = ?').run(ago(5))
  const body = (repo, n) => `@PraiseZhu\n## 发生了什么\nPR [${repo}#${n}](u) 合并后……`
  const issues = { issues: [
    { number: 3, html_url: url, state: 'open', state_reason: null, created_at: ago(5), title: '[PR-QA] B 项目 #1 合并后「单元测试」失败', body: body('a/b', 1) },
    { number: 4, html_url: 'h4', state: 'closed', state_reason: 'completed', created_at: ago(3), closed_at: ago(0.5), title: '[PR-QA] D 项目 #7 合并后「打包构建」失败', body: body('c/d', 7) },
    { number: 5, html_url: 'h5', state: 'closed', state_reason: 'completed', created_at: ago(9), closed_at: ago(8), title: '[PR-QA] D 项目 #2 合并后「代码规范」失败', body: body('c/d', 2) },
    { number: 6, html_url: 'h6', state: 'open', state_reason: null, created_at: ago(0.2), title: '[PR-QA] 金丝雀 #9 合并后「单元测试」失败', body: body('x/canary', 9) },
  ] }
  const { text } = buildDailyReport({ db, reg: boardReg, now, issues })
  assert.match(text.split('\n').find((l) => l.startsWith('结论')), /今天有 1 件事要你处理：1 个之前的 issue 没解决/)
  assert.ok(text.includes(`• B 项目 #1 合并后单元测试失败（已挂 5 天，出现过 2 次）：${url}`), text)
  assert.ok(text.includes('已修好关掉：D 项目 #7 合并后打包构建失败'), text)
  assert.ok(!text.includes('代码规范'), '窗口之前关掉的不再提')
  assert.ok(!text.includes('金丝雀 #9'), '金丝雀仓的 issue 不进日报')
  assert.ok(text.includes('【要做】处理之前提的 1 个还没解决的 issue（最久的已经挂了 5 天）'), text)
  assert.ok(!/\[PR-QA\]|argus-/.test(text), text)
})

test('日报：读不到 issue 状态时照发，并说明这部分缺了', () => {
  const db = freshDb()
  setMeta(db, 'heartbeat_at', new Date().toISOString())
  const { text } = buildDailyReport({ db, reg, now: new Date(Date.now() + 1000), issues: { error: true } })
  assert.ok(text.includes('这次没读到 GitHub 上的 issue 状态'), text)
  assert.ok(!text.includes('所有项目都没有没解决的 issue'), text)
})

test('服务正常停止打断的任务回到队列，不算一次尝试', () => {
  const db = freshDb()
  insertJob(db, { repo: 'a/b', prNumber: 9, prNodeId: 'S9', prTitle: 't', prUrl: 'u', authorLogin: 'PraiseZhu', baseRef: 'main', mergedAt: new Date().toISOString(), mergeSha: 's9' })
  const id = db.prepare('SELECT id FROM jobs').get().id
  for (let i = 0; i < 4; i++) { markRunning(db, id); requeueJob(db, id, { refundAttempt: true }) }
  assert.equal(db.prepare('SELECT attempts FROM jobs WHERE id = ?').get(id).attempts, 0)
  markRunning(db, id); requeueJob(db, id)
  assert.equal(db.prepare('SELECT attempts FROM jobs WHERE id = ?').get(id).attempts, 1, '出错重试照常计数')
})
