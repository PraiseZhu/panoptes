#!/usr/bin/env node
// 用飞书 Mivo 机器人给指定 open_id 发纯文本私信。
// 用法：node send-feishu-dm.mjs --open-id <id> --text-file <path> [--dedupe-key <key>] [--allow-owner]
// 凭证：MIVO_FEISHU_APP_ID、MIVO_FEISHU_APP_SECRET（都必填，绝不输出）；环境变量优先，
//       否则从 $QA_SECRETS_FILE（默认 /home/box/.config/mivo-qa/secrets.env）读取。
// 路径：$QA_HOME（默认 /workspace/mivo-qa；旧名 MIVO_QA_ROOT 仍可用）下的 author-map.json / feishu-sent.json，
//       可分别用 MIVO_QA_AUTHOR_MAP / MIVO_QA_FEISHU_SENT 覆盖。
// 护栏：
//   - open_id 必须出现在 author-map.json 里（mivo_authors 或 roster）
//   - author-map 里 notify_via_feishu=false 的 open_id，除非传 --allow-owner，否则拒绝
//   - 传了 --dedupe-key 时查/写 feishu-sent.json，key 已存在则跳过
import { readFileSync, writeFileSync, existsSync, renameSync } from 'node:fs'
import { join } from 'node:path'
import { randomUUID, createHash } from 'node:crypto'

const API = 'https://open.feishu.cn/open-apis'
const ROOT = process.env.QA_HOME || process.env.MIVO_QA_ROOT || '/workspace/mivo-qa'
const AUTHOR_MAP = process.env.MIVO_QA_AUTHOR_MAP || join(ROOT, 'author-map.json')
const SENT_LOG = process.env.MIVO_QA_FEISHU_SENT || join(ROOT, 'feishu-sent.json')
const SECRETS_FILE = process.env.QA_SECRETS_FILE || '/home/box/.config/mivo-qa/secrets.env'
const USAGE = `用法 / usage:
  node send-feishu-dm.mjs --open-id <id> --text-file <path> [--dedupe-key <key>] [--allow-owner]

  --open-id ID       recipient; must appear in author-map.json (mivo_authors or roster)
  --text-file PATH   plain-text message body (UTF-8)
  --dedupe-key KEY   skip if KEY is already in feishu-sent.json; record it after a successful send
  --allow-owner      allow recipients marked notify_via_feishu=false
  -h, --help         this help

env: MIVO_FEISHU_APP_ID, MIVO_FEISHU_APP_SECRET (or in $QA_SECRETS_FILE),
     QA_HOME, MIVO_QA_AUTHOR_MAP, MIVO_QA_FEISHU_SENT
paths now: author-map=${AUTHOR_MAP}  sent-log=${SENT_LOG}
exit: 0 sent/skipped · 1 Feishu/API error · 2 usage/config error · 3 recipient refused`

function out(obj, code = 0) {
  process.stdout.write(JSON.stringify(obj, null, 2) + '\n')
  process.exit(code)
}

function parseArgs(argv) {
  const a = { allowOwner: false }
  // A value flag with a missing/empty value is a usage error (an empty --dedupe-key must not silently disable dedupe).
  const val = (k, i) => {
    const v = argv[i]
    if (v === undefined || v === '' || v.startsWith('--')) out({ ok: false, error: 'bad_args', msg: `${k} 需要一个非空值` }, 2)
    return v
  }
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i]
    if (k === '--open-id') a.openId = val(k, ++i)
    else if (k === '--text-file') a.textFile = val(k, ++i)
    else if (k === '--dedupe-key') a.dedupeKey = val(k, ++i)
    else if (k === '--allow-owner') a.allowOwner = true
    else if (k === '-h' || k === '--help') { process.stdout.write(USAGE + '\n'); process.exit(0) }
    else out({ ok: false, error: 'bad_args', msg: `未知参数：${k}` }, 2)
  }
  if (!a.openId || !a.textFile) out({ ok: false, error: 'bad_args', msg: '用法：--open-id <id> --text-file <path> [--dedupe-key <key>] [--allow-owner]（--help 查看说明）' }, 2)
  return a
}

function loadAuthorMap() {
  let d
  try { d = JSON.parse(readFileSync(AUTHOR_MAP, 'utf8')) } catch (e) {
    out({ ok: false, error: 'author_map_unreadable', msg: `读不到 author-map：${AUTHOR_MAP}（${e.code || e.name}）` }, 2)
  }
  const known = new Set()
  const noNotify = new Set()
  for (const sec of ['mivo_authors', 'roster']) {
    for (const e of d[sec] || []) {
      if (!e?.feishu_open_id) continue
      known.add(e.feishu_open_id)
      if (e.notify_via_feishu === false) noNotify.add(e.feishu_open_id)
    }
  }
  return { known, noNotify }
}

// Credentials: env first, then KEY=VALUE lines from the secrets file. Values are never printed.
function credential(name) {
  if (process.env[name]) return process.env[name]
  if (!existsSync(SECRETS_FILE)) return null
  for (const line of readFileSync(SECRETS_FILE, 'utf8').split('\n')) {
    const m = line.match(/^\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*?)\s*$/)
    if (m && m[1] === name) return m[2].replace(/^(['"])(.*)\1$/, '$2') || null
  }
  return null
}

function loadSent() {
  if (!existsSync(SENT_LOG)) return {}
  return JSON.parse(readFileSync(SENT_LOG, 'utf8'))
}

function saveSent(log) {
  const tmp = `${SENT_LOG}.tmp-${process.pid}`
  writeFileSync(tmp, JSON.stringify(log, null, 2) + '\n')
  renameSync(tmp, SENT_LOG)
}

async function tenantToken(appId, appSecret) {
  const resp = await fetch(`${API}/auth/v3/tenant_access_token/internal`, {
    method: 'POST',
    headers: { 'content-type': 'application/json; charset=utf-8' },
    body: JSON.stringify({ app_id: appId, app_secret: appSecret }),
    signal: AbortSignal.timeout(20_000),
  })
  const data = await resp.json().catch(() => ({}))
  if (data.code !== 0) return { error: { stage: 'tenant_access_token', http: resp.status, code: data.code ?? null, msg: data.msg ?? null } }
  return { token: data.tenant_access_token }
}

async function main() {
  const args = parseArgs(process.argv.slice(2))
  const appId = credential('MIVO_FEISHU_APP_ID')
  const appSecret = credential('MIVO_FEISHU_APP_SECRET')
  if (!appId || !appSecret) out({ ok: false, error: 'missing_secret', msg: 'MIVO_FEISHU_APP_ID / MIVO_FEISHU_APP_SECRET 未设置（环境变量或 secrets 文件）' }, 2)

  const { known, noNotify } = loadAuthorMap()
  if (!known.has(args.openId)) out({ ok: false, error: 'unknown_open_id', msg: 'open_id 不在 author-map.json 里，拒绝发送' }, 3)
  if (noNotify.has(args.openId) && !args.allowOwner) out({ ok: false, error: 'notify_disabled', msg: '该 open_id 在 author-map 中 notify_via_feishu=false；如确需发送请加 --allow-owner' }, 3)

  const text = readFileSync(args.textFile, 'utf8').replace(/\s+$/, '')
  if (!text) out({ ok: false, error: 'empty_text', msg: '消息内容为空' }, 2)

  let sent
  if (args.dedupeKey) {
    sent = loadSent()
    if (Object.hasOwn(sent, args.dedupeKey)) out({ ok: true, skipped: true, reason: 'dedupe_key_exists', dedupe_key: args.dedupeKey, message_id: sent[args.dedupeKey].message_id })
  }

  const t = await tenantToken(appId, appSecret)
  if (t.error) out({ ok: false, ...t.error }, 1)

  // uuid 让飞书在 1 小时内对同一请求去重；有 dedupe-key 时用它的 sha256 派生（截断原文会让前缀相同的不同 key 互相吞掉），否则随机
  const uuid = args.dedupeKey ? createHash('sha256').update(args.dedupeKey).digest('hex').slice(0, 32) : randomUUID()
  const url = `${API}/im/v1/messages?receive_id_type=open_id&uuid=${encodeURIComponent(uuid)}`
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json; charset=utf-8', authorization: `Bearer ${t.token}` },
    body: JSON.stringify({ receive_id: args.openId, msg_type: 'text', content: JSON.stringify({ text }) }),
    signal: AbortSignal.timeout(20_000),
  })
  const data = await resp.json().catch(() => ({}))
  if (data.code !== 0) out({ ok: false, stage: 'send_message', http: resp.status, code: data.code ?? null, msg: data.msg ?? null }, 1)

  const messageId = data.data?.message_id
  if (args.dedupeKey) {
    sent = loadSent() // re-read right before writing so a concurrent sender's entries are not lost
    sent[args.dedupeKey] = { open_id: args.openId, sent_at: new Date().toISOString(), message_id: messageId }
    saveSent(sent)
  }
  out({ ok: true, message_id: messageId, open_id: args.openId, dedupe_key: args.dedupeKey || null })
}

// Error messages never contain the token or app secret (they only travel in request bodies/headers).
main().catch(e => out({ ok: false, error: 'exception', msg: String(e?.message || e).slice(0, 500) }, 1))
