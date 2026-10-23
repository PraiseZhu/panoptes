// 作者回执统一成一种（Praise 2026-10-23）：首行「结论：已修 #N / 不修，原因… / 误报，原因…」，原 PR 评论和交接 issue 评论都认；
// 写了「QA #原PR号」的修复 PR 合并后自动当「已修」，私信追踪和交接 issue 一起收起；
// 作者口径（finding-policy.mjs）：旧报告里的覆盖率 P2、AI 补测线索、flaky / unverified、PLAUSIBLE 审查结论不再提醒、不计逾期。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, chmodSync, existsSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { tmpdir } from 'node:os'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { parseReceipt, parseReceiptDetail, fixPrResolutions, qaRefRe, resolutionFor } from '../../report/handoff-receipts.mjs'
import { notifiable, authorFindings } from '../../report/finding-policy.mjs'
import { matchesItem, namesItem } from '../../report/followup-match.mjs'

const TRACK = join(dirname(fileURLToPath(import.meta.url)), '../../report/track-followups.mjs')

test('回执解析：首行「结论：…」，可加粗，半角 / 全角冒号；已修带修复 PR 号，不修 / 误报带原因', () => {
  assert.deepEqual(parseReceiptDetail('结论：不修，原因：设计如此\n\n详细：见 PR 正文「产品意图」'), { kind: '不修', fix_pr: null, reason: '设计如此', first_line: true })
  assert.deepEqual(parseReceiptDetail('**结论**: 已修 #1240'), { kind: '已修', fix_pr: 1240, reason: null, first_line: true })
  assert.equal(parseReceiptDetail('**结论：误报**，裁剪格 generation 里没有 taskId').reason, '裁剪格 generation 里没有 taskId')
  assert.equal(parseReceiptDetail('结论：已修 xindong/mivo-canvas-plugin#1222').fix_pr, 1222)
  assert.equal(parseReceiptDetail('复核结论：误报成立'), null)
  assert.equal(parseReceiptDetail('还在看'), null)
  assert.equal(parseReceiptDetail('先看了一下\n结论：误报，x').first_line, false, '旧回执（结论不在首行）兜底照认')
  assert.equal(parseReceipt('结论：不修，原因…'), '不修', '旧接口不变')
})

test('交接 issue 回执带上修复 PR 号与原因（台账据此认「已修 #N」）', () => {
  const issue = { number: 1, state: 'open', labels: [], title: '[P1] #1023 x', body: '<!-- mivo-qa-handoff: key=mq1-aaa-pr1023 pr=1023 v=1 -->', html_url: 'u' }
  const r = resolutionFor('r', issue, [{ user: { login: 'aj0928' }, body: '结论：已修 #1130\n改了 cindyplugin/main.js', created_at: 't', html_url: 'c' }])
  assert.deepEqual([r.kind, r.fix_pr], ['已修', 1130])
})

test('「QA #原PR号」：QA #N / QA: #N / QA：#N / QA: owner/repo#N 都认；只写 #N 的不算统一格式（兜底规则另算）', () => {
  for (const t of ['QA #2000', 'QA: #2000', 'QA：#2000', 'qa:#2000', 'QA: xindong/mivo-canvas-plugin#2000']) assert.ok(qaRefRe(2000).test(t), t)
  for (const t of ['fix #2000', 'QA #20001', 'QA #200']) assert.ok(!qaRefRe(2000).test(t), t)
  const prs = [{ number: 2100, title: 'fix: 串号 (QA #2000)', body: '', mergedAt: '2026-10-23T01:00:00Z', files: ['src/cache.ts'] },
    { number: 2101, title: 'QA #2000 进行中', body: '', mergedAt: null, files: ['src/cache.ts'] },
    { number: 2102, title: 'QA #2000 只改文档', body: '', mergedAt: '2026-10-23T01:00:00Z', files: ['README.md'] }]
  assert.deepEqual(fixPrResolutions(prs, { pr: 2000, files: ['src/cache.ts'] }).map((r) => [r.fix_pr, r.kind, r.by]), [[2100, '已修', 'fix-pr-merged']])
})

test('作者口径：新报告 findings 都算；旧报告里的覆盖率 P2、未核实 AI 补测、flaky / unverified、PLAUSIBLE、qa-error、own-tests 不算', () => {
  assert.equal(notifiable({ severity: 'P1', source: 'code-review', verdict: 'CONFIRMED', verification: { status: 'confirmed' } }), true)
  assert.equal(notifiable({ severity: 'P1', source: 'code-review', verdict: 'CONFIRMED', verification: { status: 'undetermined' } }), false)
  assert.equal(notifiable({ severity: 'P1', source: 'code-review', verdict: 'PLAUSIBLE' }), false, '#1191 那种 PLAUSIBLE P1')
  assert.equal(notifiable({ severity: 'P1', source: 'code-review', verdict: 'CONFIRMED' }), true, '旧报告的 CONFIRMED 审查结论（没有 verification 字段）按当时结论')
  assert.equal(notifiable({ severity: 'P2', source: 'coverage-gate', title: 'new-code coverage 81% < 90%' }), false)
  assert.equal(notifiable({ severity: 'P2', source: 'stated-acceptance', title: 'PR 自己写明的覆盖率目标没达到' }), true)
  assert.equal(notifiable({ severity: 'P2', source: 'ai-test', verified: false }), false)
  assert.equal(notifiable({ severity: 'P2', source: 'ai-test', verified: true }), true)
  for (const status of ['flaky', 'unverified', 'timeout', 'setup-failed', 'pre-existing']) assert.equal(notifiable({ severity: 'P2', source: 'check', status }), false, status)
  assert.equal(notifiable({ severity: 'P0', source: 'check', status: 'introduced', test_backed: true }), true)
  assert.equal(notifiable({ severity: 'P2', source: 'qa-error' }), false)
  assert.equal(notifiable({ severity: 'P2', source: 'own-tests' }), false)
  assert.equal(notifiable({ severity: 'P3', source: 'check', status: 'introduced' }), false)
  assert.equal(notifiable({ severity: 'P1', source: 'code-review', verdict: 'CONFIRMED', pending_kind: 'review-plausible' }), false)
  assert.deepEqual(authorFindings({ findings: [{ severity: 'P2', source: 'coverage-gate' }, { severity: 'P2', source: 'code-review', verdict: 'CONFIRMED', title: 'b' }, { severity: 'P1', source: 'check', status: 'introduced', title: 'a' }] }).map((f) => f.title), ['a', 'b'])
})

test('PR 评论回执匹配：点名了某条就只收那条；没点名任何一条 → 收整条私信；修复 PR 合并算回应', () => {
  const a = { files: ['src/a.ts'], problems: ['缓存键没有区分用户导致串号'] }
  assert.ok(namesItem('结论：误报，src/a.ts 只在单用户下用', a))
  assert.ok(matchesItem({ type: 'pr-comment', text: '结论：不修，原因：设计如此', receipt: { kind: '不修' }, receipt_generic: true }, a))
  assert.ok(!matchesItem({ type: 'pr-comment', text: '结论：不修，src/b.ts 那条', receipt: { kind: '不修' }, receipt_generic: false }, a))
  assert.ok(matchesItem({ type: 'fix-pr-merged', pr: 2100 }, a))
})

// --- track-followups.mjs 端到端：假 gh + 假发送器，不碰真实飞书 / Slack / GitHub
const HOURS = 3600e3
function fixture({ prComment = null, fixPr = null, legacyFinding = null } = {}) {
  const root = mkdtempSync(join(tmpdir(), 'argus-rc-')), QA = join(root, 'qa'), bin = join(root, 'bin'), log = join(root, 'calls.log')
  for (const d of ['reports/merged', 'followup', 'handoff/aj0928', 'report/sent']) mkdirSync(join(QA, d), { recursive: true })
  mkdirSync(bin)
  const sentAt = new Date(Date.now() - 100 * HOURS).toISOString()
  writeFileSync(join(QA, 'author-map.json'), JSON.stringify({ mivo_authors: [{ github_login: 'bob', name: '鲍勃', feishu_open_id: 'ou_bob' }] }))
  const finding = legacyFinding ?? { fingerprint: 'mq1-bbb', pr_fingerprint: 'mq1-bbb-pr2000', severity: 'P1', title: '缓存键没有区分用户导致串号', file: 'src/cache.ts', source: 'code-review', verdict: 'CONFIRMED', evidence: { recommendation: '改' } }
  writeFileSync(join(QA, 'reports/merged/PR-2000.json'), JSON.stringify({ prs: [{ number: 2000, author: 'bob', title: 'fix: 别的', findings: [finding] }] }))
  writeFileSync(join(QA, 'feishu-sent.json'), JSON.stringify({ [`xindong/mivo-canvas-plugin#2000:${finding.fingerprint}`]: { open_id: 'ou_bob', sent_at: sentAt, message_id: 'm2' } }))
  writeFileSync(join(QA, 'handoff/aj0928/issues.json'), JSON.stringify({ issues: [] }))
  const comment = prComment ? { user: { login: 'bob' }, body: prComment, created_at: new Date().toISOString(), html_url: 'https://github.com/x/pull/2000#c1' } : null
  const prs = fixPr ? [{ number: 2100, title: fixPr.title, body: fixPr.body ?? '', createdAt: new Date(Date.now() - HOURS).toISOString(), url: 'https://github.com/x/pull/2100', mergedAt: fixPr.mergedAt ?? null }] : []
  const fakeGh = `#!/usr/bin/env node
const fs=require('fs');const a=process.argv.slice(2);fs.appendFileSync(${JSON.stringify(log)},'gh '+a.join(' ')+'\\n')
if(a[0]==='pr'){console.log(${JSON.stringify(JSON.stringify(prs))});process.exit(0)}
const p=a[a.length-1]
if(p==='repos/xindong/mivo-canvas-plugin/issues/2000/comments?per_page=100'){ ${comment ? `console.log(${JSON.stringify(JSON.stringify(comment))})` : ''}; process.exit(0)}
if(p==='repos/xindong/mivo-canvas-plugin/pulls/2100/files?per_page=100'){console.log(JSON.stringify({filename:'src/cache.ts'}));process.exit(0)}
process.exit(0)`
  writeFileSync(join(bin, 'gh'), fakeGh); chmodSync(join(bin, 'gh'), 0o755)
  const sender = (name, out) => { const p = join(root, name); writeFileSync(p, `const fs=require('fs');const a=process.argv.slice(2);const i=a.indexOf('--text-file')>=0?a.indexOf('--text-file'):a.indexOf('--content-file');fs.appendFileSync(${JSON.stringify(log)},'${name} '+a.join(' ')+'\\n'+(i>=0?fs.readFileSync(a[i+1],'utf8')+'\\n':''));console.log(${JSON.stringify(out)})`); return p }
  const env = { ...process.env, PATH: `${bin}:${process.env.PATH}`, QA_HOME: QA, MIVO_SLACK_BOT_TOKEN: '',
    MIVO_QA_DM_SENDER: sender('dm.cjs', JSON.stringify({ ok: true })), MIVO_QA_GROUP_SENDER: sender('feishu.cjs', JSON.stringify({ ok: true })), MIVO_QA_SLACK_SENDER: sender('slack.cjs', 'ok ts=1 channel=C replies=0') }
  delete env.MIVO_QA_HANDOFF_REPOS; delete env.MIVO_QA_FOLLOWUP_STATE; delete env.MIVO_QA_FOLLOWUP_HOLDS
  env.MIVO_QA_HANDOFF_REPOS = ''
  return { QA, log, env }
}
const run = (fx, args) => JSON.parse(execFileSync('node', [TRACK, ...args], { env: fx.env, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }))
const stateOf = (fx) => JSON.parse(readFileSync(join(fx.QA, 'followup/state.json'), 'utf8')).items['xindong/mivo-canvas-plugin#2000:mq1-bbb']
const calls = (fx) => (existsSync(fx.log) ? readFileSync(fx.log, 'utf8') : '')

test('track-followups：原 PR 评论首行「结论：不修，原因…」→ 识别为回执，私信追踪收起，不再提醒', () => {
  const fx = fixture({ prComment: '结论：不修，原因：这是 PR 正文写明的产品设计\n\n补充说明……' })
  const s = run(fx, [])
  assert.equal(s.reminded.length, 0)
  assert.deepEqual(s.thanked.map((t) => t.login), ['bob'])
  const it = stateOf(fx)
  assert.equal(it.status, 'closed')
  assert.ok(it.evidence.some((e) => e.type === 'pr-comment' && e.receipt?.kind === '不修' && e.receipt?.reason === '这是 PR 正文写明的产品设计'))
})

test('track-followups：没有任何回应 → 照常提醒；提醒文案是统一的回执说法', () => {
  const fx = fixture()
  const s = run(fx, ['--dry-run'])
  const d = s.drafts.find((x) => x.open_id === 'ou_bob' && x.dedupe_key.startsWith('followup-remind:'))
  assert.ok(d)
  assert.match(d.text, /「QA #2000」，合并后自动算已修/)
  assert.match(d.text, /第一行写「结论：不修，原因…」或「结论：误报，原因…」/)
})

test('track-followups：写了「QA #2000」的修复 PR 合并后 → 私信追踪收起（fix-pr-merged），逾期通报也不再列它', () => {
  const fx = fixture({ fixPr: { title: 'fix: 缓存键按用户区分 (QA #2000)', mergedAt: new Date().toISOString() } })
  const s = run(fx, [])
  assert.equal(s.reminded.length, 0)
  const it = stateOf(fx)
  assert.equal(it.status, 'closed')
  assert.ok(it.evidence.some((e) => e.type === 'fix-pr-merged' && e.pr === 2100 && e.receipt?.kind === '已修'))
  const fx2 = fixture({ fixPr: { title: 'fix: 缓存键按用户区分 (QA #2000)', mergedAt: new Date().toISOString() } })
  const o = run(fx2, ['--overdue-only', '--group-overdue', '--dry-run'])
  assert.deepEqual(o.overdue.notices, [])
  assert.ok(o.overdue.candidates.some((c) => c.pr === 2000 && c.skip === 'responded'))
})

test('track-followups：旧报告里只差覆盖率 / PLAUSIBLE 的私信条目 → 统一分级下不算作者的问题，不提醒、不感谢，记 not-author-facing', () => {
  const fx = fixture({ legacyFinding: { fingerprint: 'mq1-bbb', pr_fingerprint: 'mq1-bbb-pr2000', severity: 'P1', title: '沿用原槽任务 ID 的隐藏裁剪节点会被误删', file: 'src/a.ts', source: 'code-review', verdict: 'PLAUSIBLE', evidence: { recommendation: '改' } } })
  const s = run(fx, [])
  assert.equal(s.reminded.length, 0)
  assert.equal(s.thanked.length, 0)
  assert.deepEqual(s.not_author_facing.map((x) => x.pr), [2000])
  assert.equal(stateOf(fx).closed_via, 'not-author-facing')
  assert.doesNotMatch(calls(fx), /dm\.cjs/)
})
