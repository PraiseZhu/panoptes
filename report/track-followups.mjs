#!/usr/bin/env node
// Follow-up tracker for Mivo QA Feishu DMs to PR authors of xindong/mivo-canvas-plugin.
// Usage: node track-followups.mjs [--dry-run] [--no-remind] [--group-overdue]
// Runtime state lives outside the repo: $MIVO_QA_FOLLOWUP_STATE (default /workspace/mivo-qa/followup/state.json).
import { readFileSync, writeFileSync, existsSync, readdirSync, mkdirSync, mkdtempSync, rmSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { tmpdir } from 'node:os'
import { execFileSync } from 'node:child_process'
const feedbackBlock = (pr) => `怎么告诉我处理好了：
· 改了：提修复 PR，标题或描述里写上原 PR 号（比如 #${pr}）
· 不用改：在原 PR 下留言写一句原因
· 飞书私信回复我收不到，别回这里
这两种我每天都会自动查，看到了就不再提醒 🙃`;


const QA = process.env.QA_HOME || '/workspace/mivo-qa'
const REPO = 'xindong/mivo-canvas-plugin'
const OWNER_OPEN_ID = 'ou_b23d82331a27b89e6484f3464fce144e' // PraiseZhu: handled by another bot
const STATE = process.env.MIVO_QA_FOLLOWUP_STATE || join(QA, 'followup/state.json')
const SENT = join(QA, 'feishu-sent.json')
const HOLDS = process.env.MIVO_QA_FOLLOWUP_HOLDS || join(QA, 'followup/holds.json')
const SENDER = join(QA, 'scripts/send-feishu-dm.mjs')
const MAX_REMINDERS = 7
const DRY = process.argv.includes('--dry-run')
const NO_REMIND = process.argv.includes('--no-remind') // 只查回复、发感谢，不发提醒
const GROUP_OVERDUE = process.argv.includes('--group-overdue') // 逾期未回应群通报，默认关

const readJson = (p, d) => (existsSync(p) ? JSON.parse(readFileSync(p, 'utf8')) : d)
const bjDate = (d = new Date()) => new Date(d.getTime() + 8 * 3600e3).toISOString().slice(0, 10)
const today = bjDate()
const summary = { dry_run: DRY, held: [], date: today, reminded: [], thanked: [], escalated: [], errors: [], drafts: [], items: [] }

function gh(args) { return JSON.parse(execFileSync('gh', args, { encoding: 'utf8', maxBuffer: 64 << 20 }) || 'null') }
function ghPaged(path) {
  const out = execFileSync('gh', ['api', '--paginate', '--jq', '.[] | tojson', path], { encoding: 'utf8', maxBuffer: 64 << 20 })
  return out.split('\n').filter(Boolean).map(l => JSON.parse(l))
}

// --- author map: only notify-enabled people with a resolved open_id
const amap = readJson(join(QA, 'author-map.json'), {})
const byLogin = new Map()
for (const m of amap.mivo_authors || []) if (m.feishu_open_id && m.notify_via_feishu !== false) byLogin.set(m.github_login, m)

// --- findings: current per-PR report plus history versions (the DM'd finding may have been superseded)
function findingsFor(pr) {
  const dir = join(QA, 'reports/merged')
  const files = [join(dir, `PR-${pr}.json`)]
  const hdir = join(dir, 'history')
  if (existsSync(hdir)) for (const f of readdirSync(hdir)) if (new RegExp(`^PR-${pr}\\.v\\d+\\.json$`).test(f)) files.push(join(hdir, f))
  let author = null, title = null
  const findings = []
  for (const f of files) {
    const r = readJson(f, null); if (!r) continue
    for (const p of r.prs || []) if (p.number === pr) { author ||= p.author; title ||= p.title; findings.push(...(p.findings || [])) }
  }
  return { author, title, findings }
}

// --- tracked items from the sent log
const sent = readJson(SENT, {})
const state = readJson(STATE, { version: 1, items: {} })
const re = new RegExp(`^${REPO.replace('/', '\\/')}#(\\d+):(.+)$`)
for (const [key, v] of Object.entries(sent)) {
  if (key.startsWith('mivo-daily-report:')) continue
  const m = key.match(re); if (!m || !v.open_id || v.open_id === OWNER_OPEN_ID) continue
  const pr = Number(m[1])
  const fps = new Set([m[2], ...(v.covered_fingerprints || [])])
  const { author, findings } = findingsFor(pr)
  const hit = []
  for (const f of findings) {
    const fp = f.fingerprint, pfp = f.pr_fingerprint
    if ((fps.has(fp) || [...fps].some(x => pfp && pfp.startsWith(x))) && !hit.some(h => h.fingerprint === fp)) hit.push(f)
  }
  hit.sort((a, b) => (b.fingerprint === m[2]) - (a.fingerprint === m[2])) // DM key's own fingerprint first
  const it = state.items[key] ||= { pr, login: author, open_id: v.open_id, dm_sent_at: v.sent_at, status: 'open', reminders: 0, reminder_dates: [] }
  it.fingerprints = [...fps]
  it.files = [...new Set(hit.map(f => f.file).filter(Boolean))]
  it.problems = hit.map(f => f.title || f.test || f.check_name).filter(Boolean)
  if (!it.login) summary.errors.push({ key, error: 'author not found in report' })
}

// --- holds: [{pr, fingerprint?, reason, held_at}] matched against the DM-recorded PR + fingerprints
const holds = readJson(HOLDS, [])
const holdFor = (it) => holds.find(h => Number(h.pr) === it.pr && (!h.fingerprint || it.fingerprints.includes(h.fingerprint)))

// --- follow-up detection
const prCache = new Map()
function authorPrsSince(login, since) {
  const k = `${login}|${since.slice(0, 10)}`
  if (!prCache.has(k)) {
    const list = gh(['pr', 'list', '-R', REPO, '--author', login, '--state', 'all', '--limit', '100',
      '--search', `created:>=${since.slice(0, 10)}`, '--json', 'number,title,body,createdAt,url'])
    prCache.set(k, list.filter(p => p.createdAt > since).map(p => ({ ...p, files: ghPaged(`repos/${REPO}/pulls/${p.number}/files?per_page=100`).map(f => f.filename) })))
  }
  return prCache.get(k)
}
function detect(it) {
  const ev = []
  const since = it.dm_sent_at, who = it.login
  const after = (x) => (x.created_at || x.submitted_at || '') > since && x.user?.login === who
  for (const c of ghPaged(`repos/${REPO}/issues/${it.pr}/comments?per_page=100`)) if (after(c)) ev.push({ type: 'pr-comment', at: c.created_at, url: c.html_url })
  for (const c of ghPaged(`repos/${REPO}/pulls/${it.pr}/reviews?per_page=100`)) if (after(c)) ev.push({ type: 'pr-review', at: c.submitted_at, url: c.html_url })
  for (const c of ghPaged(`repos/${REPO}/pulls/${it.pr}/comments?per_page=100`)) if (after(c)) ev.push({ type: 'pr-review-comment', at: c.created_at, url: c.html_url })
  const ref = new RegExp(`#${it.pr}(?!\\d)`)
  for (const p of authorPrsSince(who, since)) {
    if (p.number === it.pr) continue
    // 只改到同一文件不算跟进；必须引用原 PR 号，改到的文件仅作附注
    const touched = p.files.filter(f => it.files.includes(f))
    if (ref.test(`${p.title}\n${p.body || ''}`)) ev.push({ type: 'pr-references', pr: p.number, at: p.createdAt, url: p.url, files: touched })
  }
  return ev
}

// --- messages (template B voice: cool, restrained, no pressure)
const oneLine = (s) => String(s).replace(/\s+/g, ' ').trim()
function remindText(items) {
  const lines = items.map(it => `· #${it.pr} https://github.com/${REPO}/pull/${it.pr} — ${oneLine(it.problems[0] || '上次私聊提到的问题')}`)
  return `之前私聊提过的${items.length > 1 ? '几处' : '这处'}，还没看到动静 👀\n\n${lines.join('\n')}\n\n不催你，就是提一下，免得${items.length > 1 ? '它们' : '它'}自己躺到下周~\n\n${feedbackBlock(items[0].pr)}`
}
function thanksText(items) {
  const prs = items.map(it => `#${it.pr}`).join('、')
  return `${prs} 那边看到你跟进了，我这边就收起来了，不会再来提醒。\n谢了，省得我惦记 😌`
}

function send(openId, text, key) {
  const entry = { open_id: openId, dedupe_key: key, text }
  if (DRY) { summary.drafts.push(entry); return { ok: true, dry_run: true } }
  const d = mkdtempSync(join(tmpdir(), 'mivo-fu-')); const tf = join(d, 'msg.txt')
  try {
    writeFileSync(tf, text)
    let outp
    try { outp = execFileSync('node', [SENDER, '--open-id', openId, '--text-file', tf, '--dedupe-key', key], { encoding: 'utf8' }) }
    catch (e) { outp = e.stdout || '' }
    // send-feishu-dm.mjs prints pretty-printed JSON (multi-line); parse the whole stdout, not the last line.
    let r; try { r = JSON.parse(String(outp).trim()) } catch { r = { ok: false, error: 'unparseable sender output' } }
    return { ok: !!r.ok, skipped: !!r.skipped, error: r.ok ? null : (r.error || r.msg || 'send failed') }
  } finally { rmSync(d, { recursive: true, force: true }) }
}

// --- main
const byAuthor = new Map()
for (const [key, it] of Object.entries(state.items)) {
  const rec = { key, pr: it.pr, login: it.login, status_before: it.status, reminders: it.reminders }
  summary.items.push(rec)
  if (it.status !== 'open' || !it.login) continue
  const person = byLogin.get(it.login)
  if (!person || person.feishu_open_id !== it.open_id) { rec.skip = 'author not in author-map (notify-enabled) or open_id mismatch'; summary.errors.push({ key, error: rec.skip }); continue }
  try { rec.evidence = detect(it) } catch (e) { summary.errors.push({ key, error: `gh: ${oneLine(e.message).slice(0, 200)}` }); continue }
  rec.followed_up = rec.evidence.length > 0
  const g = byAuthor.get(it.login) || { openId: it.open_id, remind: [], thank: [] }
  const hold = holdFor(it)
  if (hold) rec.held = { reason: hold.reason, held_at: hold.held_at }
  if (rec.followed_up) g.thank.push(it)
  else if (hold) { /* held: no reminder, not counted toward escalation */ }
  else if (it.reminders >= MAX_REMINDERS) { if (!DRY) { it.status = 'escalated'; it.escalated_at = new Date().toISOString() } summary.escalated.push({ pr: it.pr, login: it.login, reminders: it.reminders }) }
  else g.remind.push(it)
  if (hold && !rec.followed_up) summary.held.push({ pr: it.pr, login: it.login, reason: hold.reason })
  rec._it = it; byAuthor.set(it.login, g)
}
for (const [login, g] of byAuthor) {
  if (g.thank.length) {
    const key = `followup-thanks:${login}:${[...new Set(g.thank.map(i => i.pr))].sort((a, b) => a - b).join(',')}`
    const r = send(g.openId, thanksText(g.thank), key)
    if (r.ok) { summary.thanked.push({ login, prs: g.thank.map(i => i.pr), skipped: r.skipped || false }); if (!DRY) for (const it of g.thank) { it.status = 'closed'; it.closed_at = new Date().toISOString(); it.evidence = summary.items.find(x => x._it === it)?.evidence } }
    else summary.errors.push({ login, error: r.error, key })
  }
  const due = g.remind.filter(it => !it.reminder_dates.includes(today))
  if (g.remind.length && !NO_REMIND) {
    const key = `followup-remind:${login}:${today}`
    const r = send(g.openId, remindText(g.remind), key)
    if (r.ok) { summary.reminded.push({ login, prs: g.remind.map(i => i.pr), skipped: r.skipped || false }); if (!DRY && !r.skipped) for (const it of due) { it.reminders++; it.reminder_dates.push(today) } }
    else summary.errors.push({ login, error: r.error, key })
  }
}
for (const x of summary.items) delete x._it

// --- 逾期未回应通报 (--group-overdue): P0/P1/有具体建议的 P2 通知作者（私信或群日报 @）后 72h 无回应 → 群里合并通报一次
if (GROUP_OVERDUE) {
  const GROUP_CHAT = 'oc_ecd450e0bed37d49bc033f3cd07cd9af'
  const GROUP_SENDER = join(QA, 'report/send-feishu-report.mjs')
  const PREVIEW = join(dirname(STATE), 'overdue-preview.post.json')
  const OVERDUE_MS = 72 * 3600e3
  const allByLogin = new Map((amap.mivo_authors || []).map(m => [m.github_login, m]))
  const byOpenId = new Map((amap.mivo_authors || []).filter(m => m.feishu_open_id).map(m => [m.feishu_open_id, m]))
  const ov = summary.overdue = { candidates: [], notices: [], gaps: [], sent: null }
  state.overdue_notified ||= {}
  // 通知事件：私信（feishu-sent 成功记录，含 PraiseZhu 自己）+ 群日报 @（report/sent/<发送日>/mentions.json，时间取当天 mivo-daily-report 发送时间）
  const notices = new Map() // pr -> { login, at, via:Set }
  const note = (pr, login, at, via) => { if (!login || !at) return; const n = notices.get(pr); if (!n) notices.set(pr, { login, at, via: new Set([via]) }); else { n.via.add(via); if (at < n.at) n.at = at } }
  for (const [key, v] of Object.entries(sent)) { const m = key.match(re); if (m && v.message_id && v.open_id) note(Number(m[1]), byOpenId.get(v.open_id)?.github_login, v.sent_at, '私信') }
  const groupSentAt = {}
  for (const [k, v] of Object.entries(sent)) if (k.startsWith('mivo-daily-report:') && v.message_id) groupSentAt[bjDate(new Date(v.sent_at))] = v.sent_at
  const sdir = join(QA, 'report/sent')
  if (existsSync(sdir)) for (const d of readdirSync(sdir).filter(d => /^\d{4}-\d{2}-\d{2}$/.test(d)).sort()) {
    const ms = readJson(join(sdir, d, 'mentions.json'), [])
    if (ms.length && !groupSentAt[d]) { ov.gaps.push({ date: d, gap: '有 mentions.json 但找不到当天群日报发送记录，通知时间未知，跳过' }); continue }
    for (const x of ms) note(Number(x.pr), x.login, groupSentAt[d], '群日报@')
  }
  const isQualifying = (f) => {
    if (f.severity === 'P0' || f.severity === 'P1') return f.source !== 'ai-test' || f.verified === true
    if (f.severity !== 'P2') return false
    if (f.source === 'coverage-gate' || f.source === 'ai-test' || /coverage/i.test(f.title || '')) return false
    return !!oneLine(f.evidence?.recommendation || '')
  }
  const now = Date.now()
  for (const [pr, n] of [...notices].sort((a, b) => a[0] - b[0])) {
    const { author, title, findings } = findingsFor(pr)
    const login = author || n.login
    const seen = new Set(), qual = []
    for (const f of findings) if (!seen.has(f.fingerprint) && seen.add(f.fingerprint) && isQualifying(f)) qual.push(f)
    if (!qual.length) continue
    const it = { pr, login, dm_sent_at: n.at, files: [...new Set(qual.map(f => f.file).filter(Boolean))], fingerprints: qual.map(f => f.fingerprint) }
    const rec = { pr, login, notified_at: n.at, via: [...n.via], issues: qual.map(f => ({ severity: f.severity, title: f.title })) }
    ov.candidates.push(rec)
    const hold = holdFor(it); if (hold) { rec.skip = `hold: ${hold.reason}`; continue }
    const age = now - Date.parse(n.at)
    if (age < OVERDUE_MS) { rec.skip = 'not yet 72h'; continue }
    const fresh = qual.filter(f => !state.overdue_notified[`${REPO}#${pr}:${f.fingerprint}`])
    if (!fresh.length) { rec.skip = 'already notified'; continue }
    try { rec.evidence = detect(it) } catch (e) { summary.errors.push({ pr, error: `overdue gh: ${oneLine(e.message).slice(0, 200)}` }); continue }
    if (rec.evidence.length) { rec.skip = 'responded'; continue }
    const person = allByLogin.get(login)
    if (!person?.feishu_open_id) ov.gaps.push({ pr, login, gap: '作者没有 open_id，无法 @，改写名字' })
    ov.notices.push({ pr, login, title, person, days: Math.floor(age / 86400e3), notified_at: n.at, via: [...n.via], issues: fresh })
  }
  if (ov.notices.length) {
    const R = `https://github.com/${REPO}/pull/`
    const t = s => ({ tag: 'text', text: s })
    const short = (s) => oneLine(s || '').replace(/^\w+(\([^)]*\))?!?:\s*/, '').slice(0, 40)
    const lvl = { P0: 'P0 阻断', P1: 'P1 严重', P2: 'P2 一般' }
    const lines = [[t(`以下 ${ov.notices.length} 个 PR 的问题已通知作者超过 3 天，还没看到回应（PR 下留言/评审，或引用原 PR 号的修复 PR）。QA 结论和发版打包挂钩，这几条没有结论会卡在待确认状态。`)], [t(' ')]]
    for (const x of ov.notices) {
      const head = [{ tag: 'a', text: `#${x.pr}`, href: R + x.pr }, t(` ${short(x.title)} — `)]
      head.push(x.person?.feishu_open_id ? { tag: 'at', user_id: x.person.feishu_open_id, user_name: x.person.name } : t(x.person?.name || x.login))
      lines.push(head)
      for (const f of x.issues) lines.push([t(`· ${lvl[f.severity]}${f.test_backed ? '（自动测试已复现）' : '（审查结论，测试未复现）'}：${oneLine(f.title)}`)])
      lines.push([t(`· 已通知 ${x.days} 天（${x.via.join('、')}，${bjDate(new Date(x.notified_at)).slice(5).replace('-', '月')}日），至今未见回应。`)])
      lines.push([t(' ')])
    }
    lines.push([t('修了就开修复 PR 并在描述里写原 PR 号，不修就在 PR 下留言说明就行（觉得是误报也可以直接留言说明）。看到回应后这条就收起，不会重复通报。')])
    const post = { zh_cn: { title: `Mivo QA · 逾期未回应通报 · ${Number(today.slice(5, 7))}月${Number(today.slice(8))}日`, content: lines } }
    ov.text = [post.zh_cn.title, ...lines.map(l => l.map(e => e.tag === 'at' ? `@${e.user_name}` : e.text).join(''))].join('\n')
    if (DRY) { writeFileSync(PREVIEW, JSON.stringify(post, null, 2)); ov.preview = PREVIEW }
    else {
      const d = mkdtempSync(join(tmpdir(), 'mivo-ov-')); const cf = join(d, 'post.json')
      try {
        writeFileSync(cf, JSON.stringify(post))
        let outp; try { outp = execFileSync('node', [GROUP_SENDER, '--chat-id', GROUP_CHAT, '--msg-type', 'post', '--content-file', cf, '--dedupe-key', `mivo-overdue-notice:${today}`], { encoding: 'utf8' }) } catch (e) { outp = e.stdout || '' }
        let r; try { r = JSON.parse(String(outp).trim()) } catch { r = { ok: false, error: 'unparseable sender output' } }
        ov.sent = { ok: !!r.ok, skipped: !!r.skipped, error: r.ok ? null : (r.error || 'send failed') }
        if (r.ok) for (const x of ov.notices) for (const f of x.issues) state.overdue_notified[`${REPO}#${x.pr}:${f.fingerprint}`] = { at: new Date().toISOString(), date: today }
      } finally { rmSync(d, { recursive: true, force: true }) }
    }
  }
}
if (!DRY) { mkdirSync(dirname(STATE), { recursive: true }); state.updated_at = new Date().toISOString(); writeFileSync(STATE, JSON.stringify(state, null, 2)) }
if (summary.escalated.length) console.error(`ESCALATED (${MAX_REMINDERS} reminders, no follow-up): ${summary.escalated.map(e => `#${e.pr} @${e.login}`).join(', ')}`)
if (summary.overdue?.text) console.error(`--- overdue notice${DRY ? ' (dry-run preview)' : ''} ---\n${summary.overdue.text}\n---`)
for (const n of summary.overdue?.notices || []) delete n.person
console.log(JSON.stringify(summary, null, 2))
