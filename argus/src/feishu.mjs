// Doki 飞书机器人私聊：只允许发给 secrets 里固定的 open_id
import { redact } from './util.mjs'

const API = 'https://open.feishu.cn/open-apis'

async function tenantToken(secrets) {
  const resp = await fetch(`${API}/auth/v3/tenant_access_token/internal`, {
    method: 'POST',
    headers: { 'content-type': 'application/json; charset=utf-8' },
    body: JSON.stringify({ app_id: secrets.ARGUS_FEISHU_APP_ID, app_secret: secrets.ARGUS_FEISHU_APP_SECRET }),
    signal: AbortSignal.timeout(20_000),
  })
  const data = await resp.json()
  if (data.code !== 0) throw Object.assign(new Error(`飞书鉴权失败：code=${data.code} ${data.msg}`), { definite: true })
  return data.tenant_access_token
}

// uuid 让飞书在 1 小时内对同一请求去重，重试不会重复发
export async function sendOwnerText(secrets, text, uuid) {
  if (!secrets.ARGUS_FEISHU_APP_ID || !secrets.ARGUS_FEISHU_APP_SECRET || !secrets.ARGUS_FEISHU_OPEN_ID) {
    throw Object.assign(new Error('飞书凭证或 open_id 没配置'), { definite: true })
  }
  const token = await tenantToken(secrets)
  const url = `${API}/im/v1/messages?receive_id_type=open_id${uuid ? `&uuid=${encodeURIComponent(uuid.slice(0, 50))}` : ''}`
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json; charset=utf-8', authorization: `Bearer ${token}` },
    body: JSON.stringify({ receive_id: secrets.ARGUS_FEISHU_OPEN_ID, msg_type: 'text', content: JSON.stringify({ text: redact(text) }) }),
    signal: AbortSignal.timeout(20_000),
  })
  const data = await resp.json().catch(() => ({}))
  if (data.code !== 0) throw Object.assign(new Error(`飞书发送失败：code=${data.code} ${data.msg}`), { definite: resp.status >= 400 && resp.status < 500 })
  return { messageId: data.data?.message_id }
}
