// 日报发送：同一天只有一条 outbox；内容生成一次后固定，重试发同一份
import { getOutbox, enqueueOutbox, updateOutbox, setMeta, claimOutbox, releaseOutbox } from './db.mjs'
import { buildDailyReport } from './report.mjs'
import { sendOwnerText } from './feishu.mjs'
import { listQaIssues } from './github.mjs'
import { log, toShanghaiDay } from './util.mjs'
import { statfsSync } from 'node:fs'
import { CACHE_DIR } from './paths.mjs'

function freeGb() {
  try {
    const s = statfsSync(CACHE_DIR)
    return Math.round((s.bavail * s.bsize) / 1024 ** 3)
  } catch {
    return null
  }
}

// 日报里的 issue 总览要实时状态（你在 GitHub 上关掉的也要算）；读不到时日报照发并说明
async function collectIssues(db, reg) {
  const repos = new Set([reg.issueRepo, ...db.prepare('SELECT DISTINCT issue_repo FROM defects').all().map((r) => r.issue_repo)].filter(Boolean))
  try {
    const issues = []
    for (const repo of repos) issues.push(...(await listQaIssues(repo)))
    return { issues }
  } catch (err) {
    log('error', 'issue board fetch failed', { err: err.message })
    return { error: true }
  }
}

// 飞书按 uuid 去重只管 1 小时：结果不明的日报只在 50 分钟内用同一个 uuid 重试；
// 过了窗口就不再发（避免重复私聊），下一份日报会提示并把内容合并进去（Jev retry_within_window 1.00）
const RETRY_WINDOW_MS = 50 * 60_000

export async function sendDailyReport({ db, reg, secrets, now = new Date(), dryRun = false }) {
  const day = toShanghaiDay(now)
  const key = `daily:${day}`
  let existing = getOutbox(db, key)
  if (existing?.status === 'sent') return { status: 'already_sent', day }
  if (existing && ['failed', 'unknown'].includes(existing.status) && (existing.tries >= 3 || now - new Date(existing.created_at) > RETRY_WINDOW_MS)) {
    if (existing.status !== 'given_up') {
      updateOutbox(db, existing.id, 'given_up', { reasonZh: '多次发送失败或结果不明且已超过去重窗口，不再重发' })
      setMeta(db, 'undelivered_report_day', day)
    }
    return { status: 'gave_up', day }
  }
  if (existing?.status === 'given_up') return { status: 'gave_up', day }

  if (dryRun) {
    const payload = existing ? JSON.parse(existing.payload_json) : buildDailyReport({ db, reg, now, diskFreeGb: freeGb(), issues: await collectIssues(db, reg) })
    return { status: 'dry_run', day, text: payload.text }
  }
  if (!existing) {
    const payload = buildDailyReport({ db, reg, now, diskFreeGb: freeGb(), issues: await collectIssues(db, reg) })
    existing = enqueueOutbox(db, 'feishu', key, { text: payload.text, cutoff: payload.cutoff })
  }
  // 09:00 的定时任务和 control 的兜底可能同时到这里：谁认领成功谁发
  if (!claimOutbox(db, existing.id)) return { status: 'busy', day }
  const payload = JSON.parse(existing.payload_json)
  try {
    const res = await sendOwnerText(secrets, payload.text, `argus-daily-${day}`)
    updateOutbox(db, existing.id, 'sent', res)
    setMeta(db, 'last_report_cutoff', payload.cutoff)
    setMeta(db, 'undelivered_report_day', '')
    log('info', 'daily report sent', { day })
    return { status: 'sent', day }
  } catch (err) {
    updateOutbox(db, existing.id, err.definite ? 'failed' : 'unknown', { error: err.message.slice(0, 300) })
    log('error', 'daily report failed', { day, err: err.message })
    throw err
  } finally {
    releaseOutbox(db, existing.id)
  }
}

export async function sendNotice({ secrets, text, uuid }) {
  return sendOwnerText(secrets, text, uuid)
}
