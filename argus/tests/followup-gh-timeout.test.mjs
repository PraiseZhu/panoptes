// 11-09 15:27 followup（track-followups.mjs --group-overdue）被 followup.py 的 900s 超时杀掉（rc=1，state / 汇总都没写，排队的 2 条提醒丢了）：
// 工具里的 gh 调用全是串行、没有超时；同一作者 PR 的改动文件按每个通知日重复拉。这里用假 gh 复现：
//   ① 一次 gh 卡住 → 单次超时后按 gh 失败处理（记 errors、逾期通报暂停），整次照常结束；
//   ② gh 总预算用完 → 之后的调用直接失败，不再等；state 照常写；
//   ③ 同一个作者 PR 的 files 每次运行只拉一次。
// 假 gh + 假发送器 + 临时目录，不碰真实飞书 / Slack / GitHub。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, chmodSync, existsSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { tmpdir } from 'node:os'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const TRACK = join(dirname(fileURLToPath(import.meta.url)), '../../report/track-followups.mjs')
const H = 3600e3
const R = 'xindong/mivo-canvas-plugin'

// bob 有两个被私信的 PR（#2000 100h 前、#2001 200h 前通知，两个不同的通知日），都过了 72h；bob 1h 前开了 #2100（没引用原 PR）
function fixture({ hangPath = null } = {}) {
  const root = mkdtempSync(join(tmpdir(), 'argus-futo-')), QA = join(root, 'qa'), bin = join(root, 'bin'), log = join(root, 'calls.log')
  for (const d of ['reports/merged', 'followup', 'report/sent']) mkdirSync(join(QA, d), { recursive: true })
  mkdirSync(bin)
  writeFileSync(join(QA, 'author-map.json'), JSON.stringify({ mivo_authors: [{ github_login: 'bob', name: '鲍勃', feishu_open_id: 'ou_bob' }] }))
  const sent = {}
  for (const [pr, age] of [[2000, 100], [2001, 200]]) {
    const f = { fingerprint: `mq1-${pr}`, pr_fingerprint: `mq1-${pr}-pr`, severity: 'P1', title: `问题 ${pr} 缓存键没有区分用户`, file: `src/f${pr}.ts`, source: 'code-review', verdict: 'CONFIRMED', evidence: { recommendation: '改' } }
    writeFileSync(join(QA, `reports/merged/PR-${pr}.json`), JSON.stringify({ prs: [{ number: pr, author: 'bob', title: `feat: ${pr}`, merged_at: new Date(Date.now() - (age + 20) * H).toISOString(), findings: [f] }] }))
    sent[`${R}#${pr}:mq1-${pr}`] = { open_id: 'ou_bob', sent_at: new Date(Date.now() - age * H).toISOString(), message_id: `m${pr}` }
  }
  writeFileSync(join(QA, 'feishu-sent.json'), JSON.stringify(sent))
  const prs = [{ number: 2100, title: 'feat: 别的事', body: '', createdAt: new Date(Date.now() - H).toISOString(), url: 'https://github.com/x/pull/2100', mergedAt: null }]
  writeFileSync(join(bin, 'gh'), `#!/usr/bin/env node
const fs=require('fs');const a=process.argv.slice(2);fs.appendFileSync(${JSON.stringify(log)},'gh '+a.join(' ')+'\\n')
const p=a[a.length-1]
if(${JSON.stringify(hangPath)}&&p===${JSON.stringify(hangPath)}){setTimeout(()=>{},120000);return}
if(a[0]==='pr'){console.log(a.includes('--author')?${JSON.stringify(JSON.stringify(prs))}:'[]');process.exit(0)}
if(p==='repos/${R}/pulls/2100/files?per_page=100'){console.log(JSON.stringify({filename:'src/other.ts'}));process.exit(0)}
process.exit(0)`)
  chmodSync(join(bin, 'gh'), 0o755)
  const sender = (name, out) => { const p = join(root, name); writeFileSync(p, `require('fs').appendFileSync(${JSON.stringify(log)},'${name} '+process.argv.slice(2).join(' ')+'\\n');console.log(${JSON.stringify(out)})`); return p }
  const env = { ...process.env, PATH: `${bin}:${process.env.PATH}`, QA_HOME: QA, MIVO_SLACK_BOT_TOKEN: '', MIVO_QA_HANDOFF_REPOS: '',
    MIVO_QA_UNIFIED_SINCE: new Date(Date.now() - 50 * H).toISOString(),   // both DMs predate it: legacy flow, like receipts-unified.test.mjs
    MIVO_QA_DM_SENDER: sender('dm.cjs', JSON.stringify({ ok: true })), MIVO_QA_GROUP_SENDER: sender('feishu.cjs', JSON.stringify({ ok: true })), MIVO_QA_SLACK_SENDER: sender('slack.cjs', 'ok ts=1 channel=C replies=0') }
  for (const k of ['MIVO_QA_FOLLOWUP_STATE', 'MIVO_QA_FOLLOWUP_HOLDS', 'MIVO_QA_SLACK_SENT', 'MIVO_QA_GH_TIMEOUT_MS', 'MIVO_QA_GH_BUDGET_MS', 'PRAISE_QA_GH_TOKEN', 'PRAISE_QA_GH_OWN_TOKEN']) delete env[k]
  return { QA, log, env }
}
// 整次运行的上限 20s：修前卡住的 gh 会让它等满 120s → 这里抛 ETIMEDOUT（= 线上 900s 被杀）
const run = (fx, args, extra = {}) => {
  const t0 = Date.now()
  const s = JSON.parse(execFileSync('node', [TRACK, ...args], { env: { ...fx.env, ...extra }, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 20000, killSignal: 'SIGKILL' }))
  return { s, ms: Date.now() - t0 }
}
const calls = (fx) => (existsSync(fx.log) ? readFileSync(fx.log, 'utf8') : '')
const errText = (s) => JSON.stringify(s.errors)

test('followup：一次 gh 调用卡住 → 单次超时后按 gh 失败处理，整次照常结束、写 state，逾期通报本次不发', () => {
  const fx = fixture({ hangPath: `repos/${R}/issues/2000/comments?per_page=100` })
  const { s, ms } = run(fx, ['--group-overdue'], { MIVO_QA_GH_TIMEOUT_MS: '1500' })
  assert.ok(ms < 15000, `run took ${ms}ms`)
  assert.match(errText(s), /#2000|2000/)
  assert.ok(s.errors.some((e) => /^(gh|overdue gh): /.test(e.error || '')), errText(s))
  assert.match(s.overdue.blocked || '', /下次重跑/)
  assert.ok(!calls(fx).includes('feishu.cjs'), 'no overdue notice on a run with a failed gh read')
  assert.ok(existsSync(join(fx.QA, 'followup/state.json')), 'state written')
})

test('followup：gh 总预算用完 → 之后的 gh 调用直接失败，不再等；交接仓读不成就不发提醒、不发通报，state 照常写', () => {
  const fx = fixture()
  const { s } = run(fx, ['--group-overdue'], { MIVO_QA_GH_BUDGET_MS: '1', MIVO_QA_HANDOFF_REPOS: 'bob/bob-handoff' })
  assert.match(errText(s), /gh budget 1ms used up/)
  assert.equal(s.reminded.length, 0)
  assert.match(s.overdue.blocked || '', /下次重跑/)
  assert.ok(!/^dm\.cjs|^feishu\.cjs/m.test(calls(fx)), calls(fx))
  assert.ok(existsSync(join(fx.QA, 'followup/state.json')))
})

test('followup：同一个作者 PR 的改动文件每次运行只拉一次（不再按每个通知日重复拉）', () => {
  const fx = fixture()
  const { s } = run(fx, ['--dry-run', '--group-overdue'])
  assert.equal(s.errors.length, 0, errText(s))
  const authorLists = calls(fx).split('\n').filter((l) => l.startsWith('gh pr list') && l.includes('--author bob'))
  assert.ok(authorLists.length >= 2, `two notice dates -> two author PR lists, got ${authorLists.length}`)
  const filesCalls = calls(fx).split('\n').filter((l) => l.includes(`repos/${R}/pulls/2100/files`))
  assert.equal(filesCalls.length, 1, filesCalls.join('\n'))
})
