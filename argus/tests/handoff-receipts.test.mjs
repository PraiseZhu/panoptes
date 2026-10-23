import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, chmodSync, existsSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { tmpdir } from 'node:os'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { parseReceipt, parseMarker, resolutionFor, resolvedBy, loadResolutions } from '../../report/handoff-receipts.mjs'

const TRACK = join(dirname(fileURLToPath(import.meta.url)), '../../report/track-followups.mjs')
const MARK = '<!-- mivo-qa-handoff: key=mq1-d67c944fa2c60236330b-pr1023 pr=1023 v=1 -->'
const issue = (extra = {}) => ({ number: 1, state: 'open', labels: [{ name: 'handoff' }], title: '[P1] #1023 无 Key 时配方参考图可绕过 4 张提交上限', body: `正文\n${MARK}\n`, html_url: 'https://github.com/aj0928/aj0928-handoff/issues/1', comments: 1, ...extra })
const cmt = (body, login = 'aj0928') => ({ user: { login }, body, created_at: '2026-10-20T02:19:00Z', html_url: 'https://github.com/aj0928/aj0928-handoff/issues/1#c1' })

test('parseReceipt: 三种结论格式，其他不算', () => {
  assert.equal(parseReceipt('结论：误报，无 Key 时配方请求到不了 mivoTryImage'), '误报')
  assert.equal(parseReceipt('结论: 已修 #1130'), '已修')
  assert.equal(parseReceipt('**结论**：不修，原因：设计如此'), '不修')
  assert.equal(parseReceipt('我看下\n结论：已修 #1'), null, '回执要写在第一行有意义的话里（2026-10-24 起不再在后面的行里兜底找）')
  assert.equal(parseReceipt('@aj0928\n结论：已修 #1'), '已修', '只有 @ 的行跳过')
  assert.equal(parseReceipt('复核结论：误报成立'), null)
  assert.equal(parseReceipt('还在看'), null)
})
test('parseMarker 取 PR 和指纹', () => {
  assert.deepEqual(parseMarker(MARK), { key: 'mq1-d67c944fa2c60236330b-pr1023', pr: 1023, fingerprint: 'mq1-d67c944fa2c60236330b' })
  assert.equal(parseMarker('no marker'), null)
})
test('resolutionFor: 作者回执 / QA 自己评论不算 / 关闭 / 误报标签', () => {
  assert.equal(resolutionFor('r', issue(), [cmt('结论：误报，xx')]).kind, '误报')
  assert.equal(resolutionFor('r', issue(), [cmt('结论：误报，xx', 'PraiseZhu')]), null)
  assert.equal(resolutionFor('r', issue(), [cmt('先看看')]), null)
  assert.equal(resolutionFor('r', issue({ state: 'closed' }), []).kind, 'closed')
  assert.equal(resolutionFor('r', issue({ labels: ['status:false-positive'] }), []).kind, '误报')
  assert.equal(resolutionFor('r', issue({ body: 'x' }), [cmt('结论：已修 #2')]), null)
})
test('resolvedBy: 按主指纹（mq1- 前缀可有可无）或标题，覆盖率那条不受影响', () => {
  const rs = [resolutionFor('r', issue(), [cmt('结论：误报，x')])]
  assert.ok(resolvedBy(rs, { pr: 1023, fingerprints: ['mq1-d67c944fa2c60236330b'] }))
  assert.ok(resolvedBy(rs, { pr: 1023, fingerprints: ['d67c944fa2c60236330b'] }))
  assert.ok(resolvedBy(rs, { pr: 1023, fingerprints: [], title: '无 Key 时配方参考图可绕过 4 张提交上限' }))
  assert.equal(resolvedBy(rs, { pr: 1023, fingerprints: ['mq1-faaf6c3350b171a11110'], title: 'new-code coverage 84.82% < 90%' }), null)
  assert.equal(resolvedBy(rs, { pr: 1098, fingerprints: ['mq1-d67c944fa2c60236330b'] }), null)
})
test('loadResolutions 只对有评论的 issue 拉评论，跳过 PR 和无标记 issue', () => {
  const calls = []
  const gh = (p) => { calls.push(p); if (p.includes('/comments')) return [cmt('结论：误报，x')]; return [issue(), issue({ number: 3, body: 'x' }), issue({ number: 4, pull_request: {} })] }
  const rs = loadResolutions(['aj0928/aj0928-handoff'], gh)
  assert.equal(rs.length, 1); assert.equal(rs[0].issue, 1)
  assert.deepEqual(calls, ['repos/aj0928/aj0928-handoff/issues?state=all&per_page=100', 'repos/aj0928/aj0928-handoff/issues/1/comments?per_page=100'])
})

// --- track-followups.mjs 端到端：假 gh + 假发送器，不碰真实飞书/Slack
const HOURS = 3600e3
function fixture({ receipt = true, handoffFail = false, receiptText = '结论：误报，无 Key 时到不了 mivoTryImage' } = {}) {
  const root = mkdtempSync(join(tmpdir(), 'argus-fu-')), QA = join(root, 'qa'), bin = join(root, 'bin'), log = join(root, 'calls.log')
  for (const d of ['reports/merged', 'followup', 'handoff/aj0928', 'report/sent']) mkdirSync(join(QA, d), { recursive: true })
  mkdirSync(bin)
  const sentAt = new Date(Date.now() - 100 * HOURS).toISOString()
  writeFileSync(join(QA, 'author-map.json'), JSON.stringify({ mivo_authors: [
    { github_login: 'aj0928', name: '严健', feishu_open_id: 'ou_aj' },
    { github_login: 'bob', name: '鲍勃', feishu_open_id: 'ou_bob' }] }))
  const f = (fp, sev, title, extra = {}) => ({ fingerprint: fp, pr_fingerprint: `${fp}-pr`, severity: sev, title, file: 'cindyplugin/main.js', source: 'code-review', evidence: { recommendation: '改' }, ...extra })
  writeFileSync(join(QA, 'reports/merged/PR-1023.json'), JSON.stringify({ prs: [{ number: 1023, author: 'aj0928', title: 'feat: 2.1', findings: [f('mq1-aaa', 'P1', '无 Key 时配方参考图可绕过 4 张提交上限')] }] }))
  writeFileSync(join(QA, 'reports/merged/PR-2000.json'), JSON.stringify({ prs: [{ number: 2000, author: 'bob', title: 'fix: 别的', findings: [f('mq1-bbb', 'P1', '缓存键没有区分用户导致串号')] }] }))
  writeFileSync(join(QA, 'feishu-sent.json'), JSON.stringify({
    'xindong/mivo-canvas-plugin#1023:mq1-aaa': { open_id: 'ou_aj', sent_at: sentAt, message_id: 'm1' },
    'xindong/mivo-canvas-plugin#2000:mq1-bbb': { open_id: 'ou_bob', sent_at: sentAt, message_id: 'm2' } }))
  writeFileSync(join(QA, 'handoff/aj0928/issues.json'), JSON.stringify({ issues: [{ pr: 1023, url: 'https://github.com/aj0928/aj0928-handoff/issues/1' }] }))
  const handoffIssue = { number: 1, state: 'open', labels: [], title: '[P1] #1023 x', body: '<!-- mivo-qa-handoff: key=mq1-aaa-pr1023 pr=1023 v=1 -->', html_url: 'u', comments: 1 }
  const fakeGh = `#!/usr/bin/env node
const fs=require('fs');const a=process.argv.slice(2);fs.appendFileSync(${JSON.stringify(log)},'gh '+a.join(' ')+'\\n')
if(a[0]==='pr'){console.log('[]');process.exit(0)}
const p=a[a.length-1]
if(p.startsWith('repos/aj0928/aj0928-handoff/')){ if(${handoffFail}){console.error('HTTP 404');process.exit(1)}
  if(p.includes('/comments'))console.log(JSON.stringify({user:{login:'aj0928'},body:${JSON.stringify(receipt ? receiptText : '在看')},created_at:'2026-10-20T02:19:00Z',html_url:'c'}))
  else console.log(JSON.stringify(${JSON.stringify(handoffIssue)})); process.exit(0)}
process.exit(0)`
  writeFileSync(join(bin, 'gh'), fakeGh); chmodSync(join(bin, 'gh'), 0o755)
  const sender = (name, out) => { const p = join(root, name); writeFileSync(p, `const fs=require('fs');const a=process.argv.slice(2);const i=a.indexOf('--text-file')>=0?a.indexOf('--text-file'):a.indexOf('--content-file');fs.appendFileSync(${JSON.stringify(log)},'${name} '+a.join(' ')+'\\n'+(i>=0?fs.readFileSync(a[i+1],'utf8')+'\\n':''));console.log(${JSON.stringify(out)})`); return p }
  const env = { ...process.env, PATH: `${bin}:${process.env.PATH}`, QA_HOME: QA, MIVO_QA_HANDOFF_REPOS: '', MIVO_SLACK_BOT_TOKEN: '',
    MIVO_QA_DM_SENDER: sender('dm.cjs', JSON.stringify({ ok: true })),
    MIVO_QA_GROUP_SENDER: sender('feishu.cjs', JSON.stringify({ ok: true })),
    MIVO_QA_SLACK_SENDER: sender('slack.cjs', 'ok ts=1.2 channel=C0CAH3W61N2 replies=0') }
  delete env.MIVO_QA_HANDOFF_REPOS
  delete env.MIVO_QA_FOLLOWUP_STATE; delete env.MIVO_QA_FOLLOWUP_HOLDS
  return { QA, log, env }
}
const run = (fx, args) => JSON.parse(execFileSync('node', [TRACK, ...args], { env: fx.env, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }))
const calls = (fx) => existsSync(fx.log) ? readFileSync(fx.log, 'utf8') : ''

test('track-followups: 交接仓回执“误报”的 #1023 不再出现在提醒里，也不发感谢私信', () => {
  const fx = fixture()
  const s = run(fx, [])
  assert.deepEqual(s.handoff_resolved.map(x => [x.pr, x.kind, x.issue]), [[1023, '误报', 'aj0928/aj0928-handoff#1']])
  assert.ok(!s.reminded.some(r => r.login === 'aj0928'))
  assert.deepEqual(s.reminded.map(r => r.items.map(i => i.pr)), [[2000]])
  assert.equal(s.thanked.length, 0)
  const st = JSON.parse(readFileSync(join(fx.QA, 'followup/state.json'), 'utf8'))
  assert.equal(st.items['xindong/mivo-canvas-plugin#1023:mq1-aaa'].status, 'closed')
  assert.equal(st.items['xindong/mivo-canvas-plugin#1023:mq1-aaa'].closed_via, 'handoff-receipt')
  assert.deepEqual([st.items['xindong/mivo-canvas-plugin#1023:mq1-aaa'].evidence[0].reason, st.items['xindong/mivo-canvas-plugin#1023:mq1-aaa'].evidence[0].fix_pr], ['无 Key 时到不了 mivoTryImage', null], '回执原因落进 state（台账读它）')
  assert.doesNotMatch(calls(fx), /--open-id ou_aj/)
})
test('track-followups: 交接仓回执「结论：已修 #1130」→ state 的证据带 fix_pr，台账能认出已修', () => {
  const fx = fixture({ receiptText: '结论：已修 #1130\n根因：…' })
  run(fx, [])
  const ev = JSON.parse(readFileSync(join(fx.QA, 'followup/state.json'), 'utf8')).items['xindong/mivo-canvas-plugin#1023:mq1-aaa'].evidence[0]
  assert.deepEqual([ev.type, ev.kind, ev.fix_pr], ['handoff-receipt', '已修', 1130])
})
test('track-followups: 没有回执照常提醒', () => {
  const fx = fixture({ receipt: false })
  const s = run(fx, ['--dry-run'])
  assert.equal(s.handoff_resolved.length, 0)
  assert.ok(s.drafts.some(d => d.open_id === 'ou_aj' && d.text.includes('#1023')))
})
test('track-followups: 交接仓读失败 → 不发提醒（宁可漏一天，不提醒已结论的）', () => {
  const fx = fixture({ handoffFail: true })
  const s = run(fx, ['--dry-run'])
  assert.ok(s.handoff.failed)
  assert.equal(s.drafts.length, 0)
})
test('--group-overdue: 飞书群 + Slack 各发一次，交接仓已结论的 PR 不进通报', () => {
  const fx = fixture()
  const s = run(fx, ['--overdue-only', '--group-overdue'])
  assert.deepEqual(s.overdue.notices.map(n => n.pr), [2000])
  assert.ok(s.overdue.candidates.some(c => c.pr === 1023 && c.skip === 'handoff receipt'))
  assert.equal(s.overdue.sent.ok, true)
  assert.equal(s.overdue.slack.ok, true)
  const log = calls(fx)
  assert.match(log, /feishu\.cjs --chat-id oc_ecd450e0bed37d49bc033f3cd07cd9af .*--dedupe-key mivo-overdue-notice:/)
  assert.match(log, /slack\.cjs --text-file \S+ --dedupe-key mivo-overdue-notice:/)
  assert.match(log, /<https:\/\/github\.com\/xindong\/mivo-canvas-plugin\/pull\/2000\|#2000>/)
  assert.match(log, /@鲍勃/)
  assert.doesNotMatch(log, /#1023/)
})
test('--group-overdue --dry-run: 两边都只写预览，不调发送器', () => {
  const fx = fixture()
  const s = run(fx, ['--overdue-only', '--group-overdue', '--dry-run'])
  assert.ok(existsSync(s.overdue.preview) && existsSync(s.overdue.slack_preview))
  assert.match(readFileSync(s.overdue.slack_preview, 'utf8'), /逾期未回应通报/)
  assert.doesNotMatch(calls(fx), /feishu\.cjs|slack\.cjs/)
})
test('--group-overdue: Slack 失败记 errors，飞书记账不受影响', () => {
  const fx = fixture()
  writeFileSync(fx.env.MIVO_QA_SLACK_SENDER, "console.error('error MIVO_SLACK_BOT_TOKEN not set');process.exit(1)")
  const s = run(fx, ['--overdue-only', '--group-overdue'])
  assert.equal(s.overdue.sent.ok, true)
  assert.equal(s.overdue.slack.ok, false)
  assert.ok(s.errors.some(e => /overdue slack/.test(e.error)))
  const st = JSON.parse(readFileSync(join(fx.QA, 'followup/state.json'), 'utf8'))
  assert.ok(st.overdue_notified['xindong/mivo-canvas-plugin#2000:mq1-bbb'])
})
