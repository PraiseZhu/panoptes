#!/usr/bin/env node
// Pi line stand-in for panoptes report/send-feishu-report.mjs (MIVO_QA_GROUP_SENDER) and report/slack/send-slack-report.mjs
// (MIVO_QA_SLACK_SENDER), used ONLY by followup.py's live remind run of report/track-followups.mjs --group-overdue (10-24).
// It never sends: a --dedupe-key that is already in the sent log is reported as skipped (as the real senders do); otherwise
// the message is appended to $PI_FOLLOWUP_QUEUE (JSON lines) and reported as sent, so the tool records state.overdue_notified
// exactly as with the real senders. followup.py turns the queue into feishu.group / slack.post actions; bin/outbound.py sends
// them through its gate with the real senders and the same dedupe keys.
// Feishu CLI (= send-feishu-report.mjs): --chat-id ID --msg-type post --content-file JSON --dedupe-key KEY  -> prints JSON
// Slack  CLI (= send-slack-report.mjs):  --text-file PATH --dedupe-key KEY [--channel C]                  -> prints "ok …" / "skip …"
import { readFileSync, appendFileSync, existsSync } from 'node:fs'
import { join } from 'node:path'
const QA = process.env.QA_HOME || process.env.MIVO_QA_ROOT || '/workspace/mivo-qa'
const QUEUE = process.env.PI_FOLLOWUP_QUEUE
const v = process.argv.slice(2), a = {}
const feishu = v.includes('--chat-id')
const say = (ok, o) => {
  process.stdout.write((feishu ? JSON.stringify(o) : `${o.skipped ? 'skip' : ok ? 'ok' : 'error'} ${o.dedupe_key || ''} ${o.error || ''}`.trim()) + '\n')
  process.exit(ok ? 0 : 2)
}
for (let i = 0; i < v.length; i++) {
  if (['--chat-id', '--msg-type', '--content-file', '--text-file', '--dedupe-key', '--channel'].includes(v[i])) a[v[i].slice(2)] = v[++i]
  else say(false, { ok: false, error: 'bad_args', msg: `未知参数：${v[i]}` })
}
if (!QUEUE) say(false, { ok: false, error: 'no_queue', msg: 'PI_FOLLOWUP_QUEUE 未设置' })
if (!a['dedupe-key']) say(false, { ok: false, error: 'bad_args', msg: '需要 --dedupe-key' })
const sentFile = feishu ? (process.env.MIVO_QA_FEISHU_SENT || join(QA, 'feishu-sent.json')) : join(QA, 'report/slack/sent.json')
let sent = {}
try { sent = existsSync(sentFile) ? JSON.parse(readFileSync(sentFile, 'utf8')) : {} } catch (e) { say(false, { ok: false, error: 'sent_unreadable' }) }
const keys = feishu ? [a['dedupe-key']] : [a['dedupe-key'], `${a['dedupe-key']}:slack`]   // outbound sends the Slack copy as <key>:slack
if (keys.some(k => Object.hasOwn(sent, k))) say(true, { ok: true, skipped: true, reason: 'dedupe_key_exists', dedupe_key: a['dedupe-key'] })
let item
if (feishu) {
  if (a['msg-type'] !== 'post' || !a['content-file']) say(false, { ok: false, error: 'bad_args', msg: '需要 --msg-type post --content-file' })
  let content
  try { content = JSON.parse(readFileSync(a['content-file'], 'utf8')) } catch (e) { say(false, { ok: false, error: 'content_unreadable' }) }
  item = { kind: 'feishu.group', chat_id: a['chat-id'], msg_type: 'post', content, dedupe_key: a['dedupe-key'] }
} else {
  if (!a['text-file']) say(false, { ok: false, error: 'bad_args', msg: '需要 --text-file' })
  const text = readFileSync(a['text-file'], 'utf8').replace(/\s+$/, '')
  if (!text) say(false, { ok: false, error: 'empty_text', dedupe_key: a['dedupe-key'] })
  item = { kind: 'slack.post', text, channel: a.channel || null, dedupe_key: a['dedupe-key'] }
}
appendFileSync(QUEUE, JSON.stringify({ ...item, queued_at: new Date().toISOString() }) + '\n')
say(true, { ok: true, queued: true, dedupe_key: a['dedupe-key'], message_id: null })
