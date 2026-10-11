#!/usr/bin/env node
// 以飞书 Mivo 机器人身份发 QA 日报（富文本 post 或消息卡片 interactive）。
// 用法：
//   node send-feishu-report.mjs (--open-id <id> | --chat-id <id>) --msg-type post|interactive \
//        --content-file <json> [--dedupe-key <key>] [--allow-owner] [--dry-run]
//   node send-feishu-report.mjs --list-chats          # 只读：列出机器人所在的群（GET /im/v1/chats）
// content-file：post 为 {"zh_cn":{"title":..,"content":[[...]]}}（也接受外层再包一层 {"post":{...}}）；
//               interactive 为卡片 JSON（{"config":..,"header":..,"elements":[..]} 或 schema 2.0）。
// 凭证：MIVO_FEISHU_APP_ID（必填）、MIVO_FEISHU_APP_SECRET（必填，绝不输出）；
//       环境变量优先，否则读 $QA_SECRETS_FILE（默认 /home/box/.config/mivo-qa/secrets.env）。
// 护栏：
//   - --open-id 与 --chat-id 二选一
//   - --chat-id 必须列在 $QA_HOME/report/allowed-chats.json（字符串数组，或 [{chat_id,name}]；格式见同目录
//     allowed-chats.example.json；真实名单不进 git）；默认 [] => 任何群都发不出去
//   - --open-id 必须出现在 author-map.json（mivo_authors 或 roster）；notify_via_feishu=false 的需加 --allow-owner
//   - --open-id 私聊时，内容里的 @（post 的 at 元素 / 卡片 <at id=..> / user_id=..）只能指向收件人本人，否则拒绝（防止私聊测试推送给他人）
//   - --dedupe-key 时查/写 feishu-sent.json，key 已存在则跳过
// exit: 0 sent/skipped/listed · 1 Feishu/API error · 2 usage/config error · 3 recipient refused
import { readFileSync, writeFileSync, existsSync, renameSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { randomUUID, createHash } from 'node:crypto'

const API = 'https://open.feishu.cn/open-apis'
const ROOT = process.env.QA_HOME || process.env.MIVO_QA_ROOT || '/workspace/mivo-qa'
const AUTHOR_MAP = process.env.MIVO_QA_AUTHOR_MAP || join(ROOT, 'author-map.json')
const SENT_LOG = process.env.MIVO_QA_FEISHU_SENT || join(ROOT, 'feishu-sent.json')
// 名单留在数据目录（不随代码进 git）：脚本放在 panoptes/report/，$QA_HOME/report/send-feishu-report.mjs 是指向它的软链
const ALLOWED_CHATS = process.env.MIVO_QA_ALLOWED_CHATS || join(ROOT, 'report', 'allowed-chats.json')
const SECRETS_FILE = process.env.QA_SECRETS_FILE || '/home/box/.config/mivo-qa/secrets.env'

function out(obj, code = 0) { process.stdout.write(JSON.stringify(obj, null, 2) + '\n'); process.exit(code) }

function parseArgs(argv) {
  const a = { allowOwner: false, dryRun: false, listChats: false }
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i]
    const val = () => { const v = argv[++i]; if (v === undefined || v.startsWith('--')) out({ ok: false, error: 'bad_args', msg: `${k} 需要一个值` }, 2); return v }
    if (k === '--open-id') a.openId = val()
    else if (k === '--chat-id') a.chatId = val()
    else if (k === '--msg-type') a.msgType = val()
    else if (k === '--content-file') a.contentFile = val()
    else if (k === '--dedupe-key') a.dedupeKey = val()
    else if (k === '--allow-owner') a.allowOwner = true
    else if (k === '--dry-run') a.dryRun = true
    else if (k === '--list-chats') a.listChats = true
    else if (k === '-h' || k === '--help') { process.stdout.write(readFileSync(fileURLToPath(import.meta.url), 'utf8').split('\n').filter(l => l.startsWith('//')).join('\n') + '\n'); process.exit(0) }
    else out({ ok: false, error: 'bad_args', msg: `未知参数：${k}` }, 2)
  }
  if (a.listChats) return a
  if (!!a.openId === !!a.chatId) out({ ok: false, error: 'bad_args', msg: '--open-id 与 --chat-id 必须二选一' }, 2)
  if (!['post', 'interactive'].includes(a.msgType)) out({ ok: false, error: 'bad_args', msg: '--msg-type 只能是 post 或 interactive' }, 2)
  if (!a.contentFile) out({ ok: false, error: 'bad_args', msg: '缺少 --content-file' }, 2)
  return a
}

function credential(name) {
  if (process.env[name]) return process.env[name]
  if (!existsSync(SECRETS_FILE)) return null
  for (const line of readFileSync(SECRETS_FILE, 'utf8').split('\n')) {
    const m = line.match(/^\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*?)\s*$/)
    if (m && m[1] === name) return m[2].replace(/^(['"])(.*)\1$/, '$2') || null
  }
  return null
}

function checkOpenId(openId, allowOwner) {
  let d
  try { d = JSON.parse(readFileSync(AUTHOR_MAP, 'utf8')) } catch (e) { out({ ok: false, error: 'author_map_unreadable', msg: `读不到 author-map（${e.code || e.name}）` }, 2) }
  let entry = null
  for (const sec of ['mivo_authors', 'roster']) for (const e of d[sec] || []) if (e?.feishu_open_id === openId && !entry) entry = e
  if (!entry) out({ ok: false, error: 'unknown_open_id', msg: 'open_id 不在 author-map.json 里，拒绝发送' }, 3)
  if (entry.notify_via_feishu === false && !allowOwner) out({ ok: false, error: 'notify_disabled', msg: '该 open_id 标记 notify_via_feishu=false；确需发送请加 --allow-owner' }, 3)
}

function checkChatId(chatId) {
  let list
  try { list = JSON.parse(readFileSync(ALLOWED_CHATS, 'utf8')) } catch (e) { out({ ok: false, error: 'allowed_chats_unreadable', msg: `读不到 allowed-chats.json（${e.code || e.name}），拒绝发群` }, 3) }
  if (!Array.isArray(list)) out({ ok: false, error: 'allowed_chats_invalid', msg: 'allowed-chats.json 必须是数组' }, 3)
  const ids = list.map(x => (typeof x === 'string' ? x : x?.chat_id)).filter(Boolean)
  if (!ids.includes(chatId)) out({ ok: false, error: 'chat_not_allowed', msg: `chat_id 不在 allowed-chats.json（当前允许 ${ids.length} 个），拒绝发送` }, 3)
}

function loadContent(file, msgType) {
  let c
  try { c = JSON.parse(readFileSync(file, 'utf8')) } catch (e) { out({ ok: false, error: 'content_unreadable', msg: `content-file 不是合法 JSON（${e.message.slice(0, 200)}）` }, 2) }
  if (msgType === 'post') {
    if (c.post) c = c.post
    if (!c.zh_cn && !c.en_us) out({ ok: false, error: 'content_invalid', msg: 'post 内容需要 zh_cn 或 en_us 节点' }, 2)
  } else {
    if (c.card) c = c.card
    if (!c.elements && !c.body) out({ ok: false, error: 'content_invalid', msg: 'interactive 卡片需要 elements（或 schema 2.0 的 body）' }, 2)
  }
  return c
}

// 收集内容里所有 @ 目标：post/卡片对象里的 {tag:'at', user_id|id}，以及字符串里的 <at id=..>/<at user_id="..">/<at email=..>
function mentionTargets(content) {
  const ids = new Set()
  const walk = v => {
    if (typeof v === 'string') { for (const m of v.matchAll(/<at\s+(?:user_id|id|email)\s*=\s*['"]?([^'">\s]+)/g)) ids.add(m[1]); return }
    if (Array.isArray(v)) return v.forEach(walk)
    if (v && typeof v === 'object') {
      if (v.tag === 'at') ids.add(v.user_id ?? v.id ?? v.open_id ?? '(missing)')
      Object.values(v).forEach(walk)
    }
  }
  walk(content)
  return [...ids]
}

function loadSent() { return existsSync(SENT_LOG) ? JSON.parse(readFileSync(SENT_LOG, 'utf8')) : {} }
function saveSent(log) { const tmp = `${SENT_LOG}.tmp-${process.pid}`; writeFileSync(tmp, JSON.stringify(log, null, 2) + '\n'); renameSync(tmp, SENT_LOG) }

async function tenantToken() {
  const appId = credential('MIVO_FEISHU_APP_ID')
  if (!appId) out({ ok: false, error: 'missing_app_id', msg: 'MIVO_FEISHU_APP_ID 未设置（环境变量或 secrets 文件）' }, 2)
  const appSecret = credential('MIVO_FEISHU_APP_SECRET')
  if (!appSecret) out({ ok: false, error: 'missing_secret', msg: 'MIVO_FEISHU_APP_SECRET 未设置（环境变量或 secrets 文件）' }, 2)
  const resp = await fetch(`${API}/auth/v3/tenant_access_token/internal`, {
    method: 'POST', headers: { 'content-type': 'application/json; charset=utf-8' },
    body: JSON.stringify({ app_id: appId, app_secret: appSecret }), signal: AbortSignal.timeout(20_000),
  })
  const data = await resp.json().catch(() => ({}))
  if (data.code !== 0) out({ ok: false, stage: 'tenant_access_token', http: resp.status, code: data.code ?? null, msg: data.msg ?? null }, 1)
  return data.tenant_access_token
}

async function listChats() {
  const token = await tenantToken()
  const chats = []
  let pageToken = ''
  for (let i = 0; i < 50; i++) {
    const u = new URL(`${API}/im/v1/chats`)
    u.searchParams.set('page_size', '100')
    if (pageToken) u.searchParams.set('page_token', pageToken)
    const resp = await fetch(u, { headers: { authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(20_000) })
    const data = await resp.json().catch(() => ({}))
    if (data.code !== 0) out({ ok: false, stage: 'list_chats', http: resp.status, code: data.code ?? null, msg: data.msg ?? null }, 1)
    for (const c of data.data?.items || []) chats.push({ chat_id: c.chat_id, name: c.name, chat_status: c.chat_status, external: c.external })
    if (!data.data?.has_more) break
    pageToken = data.data.page_token
  }
  out({ ok: true, count: chats.length, chats })
}

async function main() {
  const args = parseArgs(process.argv.slice(2))
  if (args.listChats) return listChats()

  if (args.chatId) checkChatId(args.chatId)
  else checkOpenId(args.openId, args.allowOwner)
  const content = loadContent(args.contentFile, args.msgType)
  if (args.openId) {
    const others = mentionTargets(content).filter(id => id !== args.openId)
    if (others.length) out({ ok: false, error: 'dm_mentions_others', msg: `私聊内容里有 ${others.length} 个 @ 指向收件人以外的人，拒绝发送（私聊测试只能 @ 收件人本人）` }, 3)
  }
  const receiveIdType = args.chatId ? 'chat_id' : 'open_id'
  const receiveId = args.chatId || args.openId

  if (args.dedupeKey && Object.hasOwn(loadSent(), args.dedupeKey)) {
    out({ ok: true, skipped: true, reason: 'dedupe_key_exists', dedupe_key: args.dedupeKey, message_id: loadSent()[args.dedupeKey].message_id })
  }
  if (args.dryRun) out({ ok: true, dry_run: true, receive_id_type: receiveIdType, msg_type: args.msgType })

  const token = await tenantToken()
  const uuid = args.dedupeKey ? createHash('sha256').update(args.dedupeKey).digest('hex').slice(0, 32) : randomUUID()
  const resp = await fetch(`${API}/im/v1/messages?receive_id_type=${receiveIdType}&uuid=${encodeURIComponent(uuid)}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json; charset=utf-8', authorization: `Bearer ${token}` },
    body: JSON.stringify({ receive_id: receiveId, msg_type: args.msgType, content: JSON.stringify(content) }),
    signal: AbortSignal.timeout(20_000),
  })
  const data = await resp.json().catch(() => ({}))
  if (data.code !== 0) out({ ok: false, stage: 'send_message', http: resp.status, code: data.code ?? null, msg: data.msg ?? null, field_violations: data.error?.field_violations ?? undefined }, 1)

  const messageId = data.data?.message_id
  if (args.dedupeKey) {
    const sent = loadSent()
    sent[args.dedupeKey] = { [receiveIdType]: receiveId, msg_type: args.msgType, sent_at: new Date().toISOString(), message_id: messageId }
    saveSent(sent)
  }
  out({ ok: true, message_id: messageId, receive_id_type: receiveIdType, receive_id: receiveId, msg_type: args.msgType, dedupe_key: args.dedupeKey || null })
}

main().catch(e => out({ ok: false, error: 'exception', msg: String(e?.message || e).slice(0, 500) }, 1))
