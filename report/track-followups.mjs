#!/usr/bin/env node
// Follow-up tracker for Mivo QA Feishu DMs to PR authors of xindong/mivo-canvas-plugin.
// Usage: node track-followups.mjs [--dry-run]
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
    const touched = p.files.filter(f => it.files.includes(f))
    if (touched.length) ev.push({ type: 'pr-touches-file', pr: p.number, at: p.createdAt, url: p.url, files: touched })
    if (ref.test(`${p.title}\n${p.body || ''}`)) ev.push({ type: 'pr-references', pr: p.number, at: p.createdAt, url: p.url })
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
    let r; try { r = JSON.parse(outp.trim().split('\n').pop()) } catch { r = { ok: false, error: 'unparseable sender output' } }
    return { ok: !!r.ok, skipped: !!r.skipped, error: r.ok ? null : (r.error || 'send failed') }
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
    const key = `followup-thanks:${login}:${g.thank.map(i => i.pr).sort((a, b) => a - b).join(',')}`
    const r = send(g.openId, thanksText(g.thank), key)
    if (r.ok) { summary.thanked.push({ login, prs: g.thank.map(i => i.pr), skipped: r.skipped || false }); if (!DRY) for (const it of g.thank) { it.status = 'closed'; it.closed_at = new Date().toISOString(); it.evidence = summary.items.find(x => x._it === it)?.evidence } }
    else summary.errors.push({ login, error: r.error, key })
  }
  const due = g.remind.filter(it => !it.reminder_dates.includes(today))
  if (g.remind.length) {
    const key = `followup-remind:${login}:${today}`
    const r = send(g.openId, remindText(g.remind), key)
    if (r.ok) { summary.reminded.push({ login, prs: g.remind.map(i => i.pr), skipped: r.skipped || false }); if (!DRY && !r.skipped) for (const it of due) { it.reminders++; it.reminder_dates.push(today) } }
    else summary.errors.push({ login, error: r.error, key })
  }
}
for (const x of summary.items) delete x._it
if (!DRY) { mkdirSync(dirname(STATE), { recursive: true }); state.updated_at = new Date().toISOString(); writeFileSync(STATE, JSON.stringify(state, null, 2)) }
if (summary.escalated.length) console.error(`ESCALATED (${MAX_REMINDERS} reminders, no follow-up): ${summary.escalated.map(e => `#${e.pr} @${e.login}`).join(', ')}`)
console.log(JSON.stringify(summary, null, 2))
