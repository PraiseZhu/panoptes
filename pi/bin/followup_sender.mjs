#!/usr/bin/env node
// Pi line stand-in for panoptes scripts/send-feishu-dm.mjs, used ONLY as MIVO_QA_DM_SENDER for report/track-followups.mjs
// in followup.py's pi mode. It never sends anything: a DM whose --dedupe-key is already in feishu-sent.json is reported as
// skipped (same as the real sender); otherwise {open_id, dedupe_key, text} is appended to $PI_FOLLOWUP_QUEUE (JSON lines) and
// reported as ok, so track-followups updates followup/state.json exactly like the Grok routine did. followup.py turns the
// queue into feishu.dm actions; bin/outbound.py sends them with the real sender and the same dedupe key.
// CLI = the real sender's: --open-id ID --text-file PATH [--dedupe-key KEY] [--allow-owner]. Prints JSON; never prints secrets.
import { readFileSync, appendFileSync, existsSync } from 'node:fs'
import { join } from 'node:path'
const QA = process.env.QA_HOME || process.env.MIVO_QA_ROOT || '/workspace/mivo-qa'
const SENT = process.env.MIVO_QA_FEISHU_SENT || join(QA, 'feishu-sent.json')
const QUEUE = process.env.PI_FOLLOWUP_QUEUE
const out = (o, code = 0) => { process.stdout.write(JSON.stringify(o) + '\n'); process.exit(code) }
const a = {}; const v = process.argv.slice(2)
for (let i = 0; i < v.length; i++) {
  if (['--open-id', '--text-file', '--dedupe-key'].includes(v[i])) a[v[i].slice(2)] = v[++i]
  else if (v[i] !== '--allow-owner') out({ ok: false, error: 'bad_args', msg: `未知参数：${v[i]}` }, 2)
}
if (!QUEUE) out({ ok: false, error: 'no_queue', msg: 'PI_FOLLOWUP_QUEUE 未设置' }, 2)
if (!a['open-id'] || !a['text-file'] || !a['dedupe-key']) out({ ok: false, error: 'bad_args', msg: '需要 --open-id --text-file --dedupe-key' }, 2)
const text = readFileSync(a['text-file'], 'utf8').replace(/\s+$/, '')
if (!text) out({ ok: false, error: 'empty_text' }, 2)
let sent = {}; try { sent = existsSync(SENT) ? JSON.parse(readFileSync(SENT, 'utf8')) : {} } catch (e) { out({ ok: false, error: 'sent_unreadable', msg: String(e.message).slice(0, 200) }, 1) }
if (Object.hasOwn(sent, a['dedupe-key'])) out({ ok: true, skipped: true, reason: 'dedupe_key_exists', dedupe_key: a['dedupe-key'] })
appendFileSync(QUEUE, JSON.stringify({ open_id: a['open-id'], dedupe_key: a['dedupe-key'], text, queued_at: new Date().toISOString() }) + '\n')
out({ ok: true, queued: true, dedupe_key: a['dedupe-key'], message_id: null })
