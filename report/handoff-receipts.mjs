// 交接仓回执（Mivo QA 运维规则）：作者在 <login>-handoff 仓的 issue 下回 '结论：已修 #N / 不修，原因 / 误报，原因'，
// 或 issue 已关闭 / 打了 status:false-positive，就算这条问题有了结论，私信提醒和逾期通报都不再列它。
// issue 正文里的标记 <!-- mivo-qa-handoff: key=mq1-<fingerprint>-pr<N> pr=<N> --> 把 issue 对回原 PR 的那条问题。
import { readFileSync, existsSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

export const RECEIPT_RE = /^\s*(?:\*\*)?结论(?:\*\*)?\s*[:：]\s*(?:\*\*)?\s*(已修|不修|误报)/m
const MARKER_RE = /<!--\s*mivo-qa-handoff:\s*key=(\S+)\s+pr=(\d+)/
const RESOLVED_LABELS = new Set(['status:false-positive', 'status:closed', 'status:fixed', 'status:wontfix', 'status:resolved'])
// QA 自己（PraiseZhu）的评论不算作者回执
export const QA_LOGINS = new Set((process.env.MIVO_QA_HANDOFF_QA_LOGINS || 'PraiseZhu').split(',').map(s => s.trim()).filter(Boolean))

export function parseReceipt(body) {
  const m = String(body || '').match(RECEIPT_RE)
  return m ? m[1] : null
}
export function parseMarker(body) {
  const m = String(body || '').match(MARKER_RE)
  if (!m) return null
  return { key: m[1], pr: Number(m[2]), fingerprint: m[1].replace(/-pr\d+$/, '') }
}

// 交接仓列表：$MIVO_QA_HANDOFF_REPOS（逗号分隔 owner/repo）或 QA/handoff/*/issues.json 里的 issue url
export function handoffRepos(QA) {
  if (process.env.MIVO_QA_HANDOFF_REPOS !== undefined) return process.env.MIVO_QA_HANDOFF_REPOS.split(',').map(s => s.trim()).filter(Boolean)
  const repos = new Set(), dir = join(QA, 'handoff')
  if (!existsSync(dir)) return []
  for (const d of readdirSync(dir)) {
    const f = join(dir, d, 'issues.json'); if (!existsSync(f)) continue
    try { for (const i of JSON.parse(readFileSync(f, 'utf8')).issues || []) { const m = String(i.url || '').match(/github\.com\/([^/]+\/[^/]+)\/issues\//); if (m) repos.add(m[1]) } } catch {}
  }
  return [...repos]
}

// issue: { number, state, labels:[{name}|string], body, html_url, closed_at }；comments: [{ user:{login}, body, created_at, html_url }]
export function resolutionFor(repo, issue, comments) {
  const mk = parseMarker(issue.body); if (!mk) return null
  const labels = (issue.labels || []).map(l => typeof l === 'string' ? l : l.name)
  const base = { repo, issue: issue.number, url: issue.html_url, pr: mk.pr, key: mk.key, fingerprint: mk.fingerprint, title: String(issue.title || '').replace(/^\[P\d\]\s*#\d+\s*/, '') }
  const receipt = [...(comments || [])].reverse().find(c => !QA_LOGINS.has(c.user?.login) && parseReceipt(c.body))
  if (receipt) return { ...base, kind: parseReceipt(receipt.body), by: receipt.user?.login, at: receipt.created_at, receipt_url: receipt.html_url }
  const fp = labels.find(l => RESOLVED_LABELS.has(l))
  if (fp) return { ...base, kind: fp === 'status:false-positive' ? '误报' : fp.replace('status:', ''), by: 'label', at: issue.closed_at || null }
  if (String(issue.state).toLowerCase() === 'closed') return { ...base, kind: 'closed', by: 'issue-closed', at: issue.closed_at || null }
  return null
}

// 一条提醒项 / 逾期问题是否被某条回执收起：按指纹精确对上；没有指纹时按 PR + 标题
const fpNorm = (s) => String(s || '').replace(/^mq\d+-/, '')
export function resolvedBy(resolutions, { pr, fingerprints = [], title }) {
  const fps = new Set(fingerprints.map(fpNorm))
  return resolutions.find(r => r.pr === Number(pr) && (fps.has(fpNorm(r.fingerprint)) || (title && r.title && r.title === String(title).trim()))) || null
}

// ghPaged(path) -> array；读失败抛出，由调用方记 errors
export function loadResolutions(repos, ghPaged) {
  const out = []
  for (const repo of repos) {
    for (const issue of ghPaged(`repos/${repo}/issues?state=all&per_page=100`)) {
      if (issue.pull_request || !parseMarker(issue.body)) continue
      const comments = issue.comments > 0 ? ghPaged(`repos/${repo}/issues/${issue.number}/comments?per_page=100`) : []
      const r = resolutionFor(repo, issue, comments); if (r) out.push(r)
    }
  }
  return out
}
