// 作者回执统一成一种（Praise 2026-10-23）：首行「结论：已修 #N / 不修，原因… / 误报，原因…」，原 PR 评论和交接 issue 评论都认；
// 写了「QA #原PR号」的修复 PR 合并后自动当「已修」，私信追踪和交接 issue 一起收起；
// 作者口径（finding-policy.mjs）：10-24 起新发的提醒里，旧报告的覆盖率 P2、AI 补测线索、flaky / unverified、PLAUSIBLE、没复核的 AI P1 不发；
// 在那之前已经发给作者的问题照老流程提醒、计逾期（Praise 2026-10-23）。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, chmodSync, existsSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { tmpdir } from 'node:os'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { parseReceipt, parseReceiptDetail, fixPrResolutions, qaRefRe, resolutionFor } from '../../report/handoff-receipts.mjs'
import { notifiable, authorFindings, legacyOverdueQualifying } from '../../report/finding-policy.mjs'
import { matchesItem, namesItem } from '../../report/followup-match.mjs'

const TRACK = join(dirname(fileURLToPath(import.meta.url)), '../../report/track-followups.mjs')

test('回执解析：首行「结论：…」，可加粗，半角 / 全角冒号；已修带修复 PR 号，不修 / 误报带原因', () => {
  assert.deepEqual(parseReceiptDetail('结论：不修，原因：设计如此\n\n详细：见 PR 正文「产品意图」'), { kind: '不修', fix_pr: null, reason: '设计如此', via: 'line' })
  assert.deepEqual(parseReceiptDetail('**结论**: 已修 #1240'), { kind: '已修', fix_pr: 1240, reason: null, via: 'line' })
  assert.equal(parseReceiptDetail('**结论：误报**，裁剪格 generation 里没有 taskId').reason, '裁剪格 generation 里没有 taskId')
  assert.equal(parseReceiptDetail('结论：已修 xindong/mivo-canvas-plugin#1222').fix_pr, 1222)
  assert.equal(parseReceiptDetail('复核结论：误报成立'), null)
  assert.equal(parseReceiptDetail('还在看'), null)
  assert.equal(parseReceiptDetail('先看了一下\n结论：误报，x'), null, '结论不在第一行有意义的话里不算（和 Pi 线 handoff.py 同一口径）')
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
  assert.equal(notifiable({ severity: 'P1', source: 'code-review', verdict: 'CONFIRMED' }, { newNotice: true }), false, '新发提醒：没复核过的 AI P1 是待核实')
  assert.equal(notifiable({ severity: 'P2', source: 'code-review', verdict: 'CONFIRMED' }, { newNotice: true }), true)
  assert.equal(notifiable({ severity: 'P1', source: 'code-review', verdict: 'CONFIRMED', verification: { status: 'confirmed' } }, { newNotice: true }), true)
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
function fixture({ prComment = null, fixPr = null, legacyFinding = null, sentUnderUnified = false, author = 'bob', commenter = null, sentRecord = null, mentions = null } = {}) {
  const root = mkdtempSync(join(tmpdir(), 'argus-rc-')), QA = join(root, 'qa'), bin = join(root, 'bin'), log = join(root, 'calls.log')
  for (const d of ['reports/merged', 'followup', 'handoff/aj0928', 'report/sent']) mkdirSync(join(QA, d), { recursive: true })
  mkdirSync(bin)
  const sentAt = new Date(Date.now() - 100 * HOURS).toISOString()
  writeFileSync(join(QA, 'author-map.json'), JSON.stringify({ mivo_authors: [{ github_login: author, name: '鲍勃', feishu_open_id: 'ou_bob' }, { github_login: 'aj0928', name: '严健', feishu_open_id: 'ou_aj' }] }))
  const finding = legacyFinding ?? { fingerprint: 'mq1-bbb', pr_fingerprint: 'mq1-bbb-pr2000', severity: 'P1', title: '缓存键没有区分用户导致串号', file: 'src/cache.ts', source: 'code-review', verdict: 'CONFIRMED', evidence: { recommendation: '改' } }
  writeFileSync(join(QA, 'reports/merged/PR-2000.json'), JSON.stringify({ prs: [{ number: 2000, author, title: 'fix: 别的', merged_at: new Date(Date.now() - 120 * HOURS).toISOString(), findings: [finding] }] }))
  writeFileSync(join(QA, 'feishu-sent.json'), JSON.stringify({ [`xindong/mivo-canvas-plugin#2000:${finding.fingerprint}`]: sentRecord ?? { open_id: 'ou_bob', sent_at: sentAt, message_id: 'm2' } }))
  if (mentions) { mkdirSync(join(QA, 'report/sent', mentions.day), { recursive: true }); writeFileSync(join(QA, 'report/sent', mentions.day, 'mentions.json'), JSON.stringify(mentions.list)); mkdirSync(join(QA, 'report/slack'), { recursive: true }); writeFileSync(join(QA, 'report/slack/sent.json'), JSON.stringify({ [`mivo-daily-report:${mentions.day}`]: { channel: 'C', ts: '1.2', sent_at: mentions.sentAt } })) }
  writeFileSync(join(QA, 'handoff/aj0928/issues.json'), JSON.stringify({ issues: [] }))
  const comment = prComment ? { user: { login: commenter ?? author }, body: prComment, created_at: new Date().toISOString(), html_url: 'https://github.com/x/pull/2000#c1' } : null
  const prs = fixPr ? [{ number: 2100, title: fixPr.title, body: fixPr.body ?? '', createdAt: new Date(Date.now() - HOURS).toISOString(), url: 'https://github.com/x/pull/2100', mergedAt: fixPr.mergedAt ?? null, files: [{ path: fixPr.file ?? 'src/cache.ts' }] }] : []
  const fakeGh = `#!/usr/bin/env node
const fs=require('fs');const a=process.argv.slice(2);fs.appendFileSync(${JSON.stringify(log)},'gh '+a.join(' ')+'\\n')
if(a[0]==='pr'){console.log(${JSON.stringify(JSON.stringify(prs))});process.exit(0)}
const p=a[a.length-1]
if(p==='repos/xindong/mivo-canvas-plugin/issues/2000/comments?per_page=100'){ ${comment ? `console.log(${JSON.stringify(JSON.stringify(comment))})` : ''}; process.exit(0)}
if(p==='repos/xindong/mivo-canvas-plugin/pulls/2100/files?per_page=100'){console.log(JSON.stringify({filename:${JSON.stringify(fixPr?.file ?? 'src/cache.ts')}}));process.exit(0)}
process.exit(0)`
  writeFileSync(join(bin, 'gh'), fakeGh); chmodSync(join(bin, 'gh'), 0o755)
  const sender = (name, out) => { const p = join(root, name); writeFileSync(p, `const fs=require('fs');const a=process.argv.slice(2);const i=a.indexOf('--text-file')>=0?a.indexOf('--text-file'):a.indexOf('--content-file');fs.appendFileSync(${JSON.stringify(log)},'${name} '+a.join(' ')+'\\n'+(i>=0?fs.readFileSync(a[i+1],'utf8')+'\\n':''));console.log(${JSON.stringify(out)})`); return p }
  const env = { ...process.env, PATH: `${bin}:${process.env.PATH}`, QA_HOME: QA, MIVO_SLACK_BOT_TOKEN: '',
    MIVO_QA_DM_SENDER: sender('dm.cjs', JSON.stringify({ ok: true })), MIVO_QA_GROUP_SENDER: sender('feishu.cjs', JSON.stringify({ ok: true })), MIVO_QA_SLACK_SENDER: sender('slack.cjs', 'ok ts=1 channel=C replies=0') }
  delete env.MIVO_QA_HANDOFF_REPOS; delete env.MIVO_QA_FOLLOWUP_STATE; delete env.MIVO_QA_FOLLOWUP_HOLDS
  // 私信在统一分级生效之前（默认）还是之后发出：测试不随真实日期变
  env.MIVO_QA_UNIFIED_SINCE = new Date(Date.now() - (sentUnderUnified ? 200 : 50) * HOURS).toISOString()
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

test('老流程：10-24 之前已经发给作者的问题（哪怕新口径下是 PLAUSIBLE / 只差覆盖率）照旧提醒、计逾期', () => {
  const plausible = { fingerprint: 'mq1-bbb', pr_fingerprint: 'mq1-bbb-pr2000', severity: 'P1', title: '沿用原槽任务 ID 的隐藏裁剪节点会被误删', file: 'src/a.ts', source: 'code-review', verdict: 'PLAUSIBLE', evidence: { recommendation: '改' } }
  const fx = fixture({ legacyFinding: plausible })
  const s = run(fx, ['--dry-run'])
  assert.equal(s.not_author_facing, undefined)
  assert.ok(s.drafts.some((x) => x.open_id === 'ou_bob' && x.dedupe_key.startsWith('followup-remind:')))
  const o = run(fixture({ legacyFinding: plausible }), ['--overdue-only', '--group-overdue', '--dry-run'])
  assert.deepEqual(o.overdue.notices.map((x) => x.pr), [2000], '老流程的逾期口径：P1 照算')
  const cov = { fingerprint: 'mq1-bbb', pr_fingerprint: 'mq1-bbb-pr2000', severity: 'P2', source: 'coverage-gate', title: 'new-code coverage 81% < 90%', file: 'src/a.ts' }
  const c = run(fixture({ legacyFinding: cov }), ['--dry-run'])
  assert.ok(c.drafts.some((x) => x.dedupe_key.startsWith('followup-remind:')), '覆盖率私信也照旧提醒')
  assert.equal(legacyOverdueQualifying(cov), false, '覆盖率本来就不进逾期通报（老规矩）')
  assert.equal(legacyOverdueQualifying({ severity: 'P2', source: 'code-review', evidence: { recommendation: '改一下' } }), true)
  assert.equal(legacyOverdueQualifying({ severity: 'P2', source: 'code-review', evidence: {} }), false)
})

test('track-followups：10-24 起发出的私信，对应问题统一分级下都不算作者的问题（PLAUSIBLE / 没复核的 AI P1）→ 不提醒、不感谢，记 not-author-facing', () => {
  for (const verdict of ['PLAUSIBLE', 'CONFIRMED']) {
    const fx = fixture({ sentUnderUnified: true, legacyFinding: { fingerprint: 'mq1-bbb', pr_fingerprint: 'mq1-bbb-pr2000', severity: 'P1', title: '沿用原槽任务 ID 的隐藏裁剪节点会被误删', file: 'src/a.ts', source: 'code-review', verdict, evidence: { recommendation: '改' } } })
    const s = run(fx, [])
    assert.equal(s.reminded.length, 0, verdict)
    assert.equal(s.thanked.length, 0, verdict)
    assert.deepEqual(s.not_author_facing.map((x) => x.pr), [2000], verdict)
    assert.equal(stateOf(fx).closed_via, 'not-author-facing', verdict)
    assert.doesNotMatch(calls(fx), /dm\.cjs/, verdict)
  }
  const o = run(fixture({ sentUnderUnified: true, legacyFinding: { fingerprint: 'mq1-bbb', pr_fingerprint: 'mq1-bbb-pr2000', severity: 'P1', title: 'x', file: 'src/a.ts', source: 'code-review', verdict: 'CONFIRMED', evidence: { recommendation: '改' } } }), ['--overdue-only', '--group-overdue', '--dry-run'])
  assert.deepEqual(o.overdue.notices, [], '没复核过的 AI P1 不进逾期通报')
})

test('回执解析：共用用例（argus/tests/fixtures/receipt-cases.json，Pi 线 test/receipt-cases.json 是同一份）逐条一致', () => {
  const cases = JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'fixtures/receipt-cases.json'), 'utf8'))
  assert.ok(cases.length >= 100)
  for (const c of cases) {
    const r = parseReceiptDetail(c.in, { pr: c.pr ?? null })
    assert.deepEqual(r && { kind: r.kind, fix_pr: r.fix_pr, reason: r.reason }, c.out && { kind: c.out.kind, fix_pr: c.out.fix_pr ?? null, reason: c.out.reason ?? null }, JSON.stringify(c.in))
  }
})

test('回应匹配：只说覆盖率的「结论：不修」回执不收非覆盖率问题；bot 状态贴不算回应；讲覆盖率的回复点名标题要连续 6 个字', () => {
  const a = { files: ['src/a.ts'], problems: ['同名附件无法可靠保持节点 ID 与附件顺序的对应关系'], coverage: false, sole: true }
  const cov = { files: [], problems: ['new-code coverage 81% < 90%'], coverage: true }
  const r = { type: 'pr-comment', text: '结论：不修，原因：覆盖率不另补', receipt: { kind: '不修' }, receipt_generic: true }
  assert.equal(matchesItem(r, a), false)
  assert.equal(matchesItem(r, cov), true)
  assert.equal(matchesItem({ type: 'pr-comment', text: '盯梢停下了，没有改代码\n<!-- mivo-watcher-receipt task=blocked-notice kind=external -->' }, a), false)
  assert.equal(matchesItem({ type: 'pr-review-comment', text: '这是一条 P2 级（建议类）反馈…讨论到此关闭\n<!-- mivo-watcher-receipt task=auto-close -->' }, a), false)
  assert.equal(matchesItem({ type: 'pr-comment', text: '不用改。（覆盖率不另补，附件顺序别处保证）' }, { ...a, sole: false }), false)
  assert.equal(matchesItem({ type: 'pr-comment', text: '不用改。（覆盖率不另补；附件顺序的对应关系已有测试）' }, { ...a, sole: false }), true)
})

test('track-followups：路滨宇的 PR，严健（路由负责人）在原 PR 下回「结论：…」也算回应', () => {
  const fx = fixture({ author: 'lubinyu-lab', commenter: 'aj0928', prComment: '结论：误报，原因：生产路径里到不了' })
  const s = run(fx, [])
  assert.equal(s.reminded.length, 0)
  assert.equal(stateOf(fx).status, 'closed')
  const fx2 = fixture({ author: 'lubinyu-lab', commenter: 'someone-else', prComment: '结论：误报，原因：x' })
  assert.equal(run(fx2, ['--dry-run']).drafts.some((d) => d.dedupe_key.startsWith('followup-remind:')), true, '别人回的不算')
})

test('track-followups：写了「QA #2000」的修复 PR 没改到问题所在的文件 → 不算已修', () => {
  const s = run(fixture({ fixPr: { title: 'fix: 别处 (QA #2000)', mergedAt: new Date().toISOString(), file: 'src/other.ts' } }), ['--dry-run'])
  assert.ok(s.drafts.some((d) => d.dedupe_key.startsWith('followup-remind:')))
})

test('逾期通报的通知时间：Pi 线补的别名私信记录（没有 message_id）算通知过；群日报 @ 认 github 字段、按数据日取 Slack 发送时间', () => {
  const at = new Date(Date.now() - 100 * HOURS).toISOString()
  const o = run(fixture({ sentRecord: { open_id: 'ou_bob', sent_at: at, alias_of: 'dm:2026-10-22:2000', by: 'pi-line dm-compat' } }), ['--overdue-only', '--group-overdue', '--dry-run'])
  assert.deepEqual(o.overdue.notices.map((x) => [x.pr, x.via]), [[2000, ['私信']]])
  const m = run(fixture({ sentRecord: { open_id: 'ou_other', sent_at: at }, mentions: { day: '2026-10-20', sentAt: at, list: [{ pr: [2000], github: 'bob', name: '鲍勃' }] } }), ['--overdue-only', '--group-overdue', '--dry-run'])
  assert.deepEqual(m.overdue.notices.map((x) => [x.pr, x.via]), [[2000, ['群日报@']]])
  assert.deepEqual(m.overdue.gaps, [])
})

test('逾期通报：自己的 PR（PraiseZhu）不进群里的通报（由修 PR 的 bot 和台账跟）', () => {
  const o = run(fixture({ author: 'PraiseZhu' }), ['--overdue-only', '--group-overdue', '--dry-run'])
  assert.deepEqual(o.overdue.notices, [])
  assert.ok(o.overdue.candidates.some((c) => c.pr === 2000 && /own PR/.test(c.skip)))
})

test('track-followups.mjs 被 import 时拒绝执行，不发私信、不写 state（2026-10-24 03:08 一次 import 冒烟检查真发了 4 条作者私信）', () => {
  const fx = fixture({ prComment: '结论：不修，原因：x' })
  let err = ''
  try { execFileSync('node', ['--input-type=module', '-e', `await import(${JSON.stringify(TRACK)})`], { env: fx.env, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }) }
  catch (e) { err = String(e.stderr) }
  assert.match(err, /is a CLI/)
  assert.equal(existsSync(join(fx.QA, 'followup/state.json')), false)
  assert.doesNotMatch(calls(fx), /dm\.cjs|gh /)
})
