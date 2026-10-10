// Jev（Typesafe System One）客户端：直连 api.typesafe.ai，固定模型版本；任何失败都返回 { ok:false }，不抛到任务流程外。
// 结果只作参考：分诊用它决定可选检查跑不跑、日报用它写覆盖说明，判定和 issue 永远不读它。
import { redact, sleep } from './util.mjs'

const API = process.env.ARGUS_TYPESAFE_API ?? 'https://api.typesafe.ai'
const DEFAULT_MODEL = 'jev-1.13.0'
const TIMEOUT_MS = 60_000
const RETRY_STATUSES = new Set([408, 429, 529, 500, 502, 503, 504])
const BACKOFF_MS = [1000, 2000, 4000]

function toValidAnswers(questions, answers) {
  if (!answers || typeof answers !== 'object') return false
  for (const [key, q] of Object.entries(questions)) {
    const a = answers[key]
    if (!a || a.type !== q.type) return false
    if (q.type === 'noul' && !(typeof a.noul === 'number' && a.noul >= 0 && a.noul <= 1)) return false
    if (q.type === 'choice') {
      const sum = Object.values(a.probabilities ?? {}).reduce((s, p) => s + p, 0)
      if (!(a.choice in (q.criteria ?? {})) || Math.abs(sum - 1) > 0.01) return false
    }
  }
  return true
}

export async function evaluateJev({ state, questions, secrets, retryDelays = BACKOFF_MS }) {
  const key = secrets?.ARGUS_TYPESAFE_API_KEY
  if (!key) return { ok: false, reasonZh: '没有配置 Jev 的 key' }
  const model = secrets.ARGUS_JEV_MODEL || DEFAULT_MODEL
  const body = JSON.stringify({ state, model, questions })
  for (let attempt = 0; ; attempt++) {
    let resp
    try {
      resp = await fetch(`${API}/v1/systemone`, {
        method: 'POST',
        headers: { authorization: `Bearer ${key}`, 'content-type': 'application/json', accept: 'application/json' },
        body,
        signal: AbortSignal.timeout(TIMEOUT_MS),
      })
    } catch (err) {
      if (attempt < retryDelays.length) { await sleep(retryDelays[attempt]); continue }
      return { ok: false, reasonZh: `连不上 Jev（${redact(err.message).slice(0, 80)}）` }
    }
    if (resp.status >= 200 && resp.status < 300) {
      let data
      try { data = await resp.json() } catch { return { ok: false, reasonZh: 'Jev 返回的不是合法 JSON' } }
      if (!toValidAnswers(questions, data.answers)) return { ok: false, reasonZh: 'Jev 返回格式不对（缺答案或概率不合法）' }
      return { ok: true, model: data.model, answers: data.answers, usage: data.usage ?? null }
    }
    if (RETRY_STATUSES.has(resp.status) && attempt < retryDelays.length) {
      const after = Number(resp.headers.get('retry-after'))
      await sleep(Number.isFinite(after) && after > 0 ? Math.min(after * 1000, 30_000) : retryDelays[attempt])
      continue
    }
    const hint = [401, 403].includes(resp.status) ? 'key 无效或没有权限' : resp.status === 422 ? '请求格式被拒绝' : resp.status === 429 ? '限流' : '服务出错'
    return { ok: false, reasonZh: `Jev 返回 ${resp.status}（${hint}）` }
  }
}

// doctor 用：只列模型，不产生费用
export async function probeJev(secrets) {
  const key = secrets?.ARGUS_TYPESAFE_API_KEY
  if (!key) return { status: '未配置' }
  try {
    const r = await fetch(`${API}/v1/models`, { headers: { authorization: `Bearer ${key}` }, signal: AbortSignal.timeout(20_000) })
    return { status: r.status }
  } catch (err) {
    return { error: redact(err.message).slice(0, 120) }
  }
}
