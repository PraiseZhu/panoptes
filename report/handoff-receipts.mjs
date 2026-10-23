// 作者回执（统一成一种，Praise 2026-10-23）：首行固定写
//   结论：已修 #<修复PR号>   /   结论：不修，<原因>   /   结论：误报，<原因>
// （「结论」可加粗，半角、全角冒号都认，下面可以接人话正文）。私信之后在原 PR 下的评论、交接仓 issue 下的评论都认这一种；
// 作者的修复 PR 标题或正文写了「QA #原PR号」并合并后，自动当「已修」（fixPrResolutions）。
// issue 已关闭 / 打了 status:false-positive 等标签，也算这条问题有了结论，私信提醒和逾期通报都不再列它。
// issue 正文里的标记 <!-- mivo-qa-handoff: key=mq1-<fingerprint>-pr<N> pr=<N> --> 把 issue 对回原 PR 的那条问题。
// 同一口径的 Python 副本：pi-fallback bin/handoff.py（RECEIPT / parse_receipt / QA_REF）。
import { readFileSync, existsSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { routedRepos } from './handoff-routes.mjs'

export const RECEIPT_RE = /^\s*(?:\*\*)?结论(?:\*\*)?\s*[:：]\s*(?:\*\*)?\s*(已修|不修|误报)/m
// 一行回执：结论 + （已修时的）修复 PR 号 + 原因
const RECEIPT_LINE = /^\s*(?:\*\*)?结论(?:\*\*)?\s*[:：]\s*(?:\*\*)?\s*(已修|不修|误报)(?:\*\*)?\s*(?:(?:[\w.-]+\/[\w.-]+)?#(\d+))?\s*[，,、:：;；]?\s*(?:原因\s*[:：]?\s*)?(.*)$/
// 修复 PR 引用原 PR：QA #N / QA: #N / QA：#N / QA: xindong/mivo-canvas-plugin#N
export const qaRefRe = (n) => new RegExp(`QA\\s*[:：]?\\s*(?:[\\w.-]+\\/[\\w.-]+)?#${Number(n)}(?!\\d)`, 'i')
export const QA_REF_ALL = /QA\s*[:：]?\s*(?:[\w.-]+\/[\w.-]+)?#(\d+)(?!\d)/gi
const MARKER_RE = /<!--\s*mivo-qa-handoff:\s*key=(\S+)\s+pr=(\d+)/
const RESOLVED_LABELS = new Set(['status:false-positive', 'status:closed', 'status:fixed', 'status:wontfix', 'status:resolved'])
// QA 自己（PraiseZhu）的评论不算作者回执
export const QA_LOGINS = new Set((process.env.MIVO_QA_HANDOFF_QA_LOGINS || 'PraiseZhu').split(',').map(s => s.trim()).filter(Boolean))

export function parseReceipt(body) {
  const m = String(body || '').match(RECEIPT_RE)
  return m ? m[1] : null
}
// { kind, fix_pr, reason, first_line }：先看首个非空行（约定的写法）；兜底认任意一行以「结论：」开头（旧回执）
export function parseReceiptDetail(body) {
  const lines = String(body || '').replace(/<!--[\s\S]*?-->/g, '').split(/\r?\n/)
  const first = lines.find((l) => l.trim())
  for (const [line, isFirst] of [[first, true], ...lines.map((l) => [l, false])]) {
    const m = String(line ?? '').match(RECEIPT_LINE)
    if (m) return { kind: m[1], fix_pr: m[2] ? Number(m[2]) : null, reason: m[3].replace(/\*\*/g, '').trim().slice(0, 300) || null, first_line: isFirst }
  }
  return null
}
// 作者已合并的修复 PR（标题或正文写了「QA #原PR号」）→ 「已修」结论。prs: [{ number, title, body, mergedAt|merged_at, files? }]
// 一条问题有文件时，修复 PR 必须改到其中至少一个文件才算（和 followup-match 的修复 PR 规则一致）
export function fixPrResolutions(prs, { pr, files = [] }) {
  const re = qaRefRe(pr)
  return (prs ?? []).filter((p) => (p.mergedAt || p.merged_at) && Number(p.number) !== Number(pr) && re.test(`${p.title ?? ''}\n${p.body ?? ''}`)
    && (!files.length || !p.files || p.files.some((f) => files.includes(f))))
    .map((p) => ({ pr: Number(pr), kind: '已修', by: 'fix-pr-merged', fix_pr: Number(p.number), at: p.mergedAt || p.merged_at, url: p.url ?? null }))
}
export function parseMarker(body) {
  const m = String(body || '').match(MARKER_RE)
  if (!m) return null
  return { key: m[1], pr: Number(m[2]), fingerprint: m[1].replace(/-pr\d+$/, '') }
}

// 交接仓列表：$MIVO_QA_HANDOFF_REPOS（逗号分隔 owner/repo）；否则 handoff-routes.json 里的仓 + QA/handoff/*/issues.json 里的 issue url
export function handoffRepos(QA) {
  if (process.env.MIVO_QA_HANDOFF_REPOS !== undefined) return process.env.MIVO_QA_HANDOFF_REPOS.split(',').map(s => s.trim()).filter(Boolean)
  const repos = new Set(routedRepos()), dir = join(QA, 'handoff')
  if (!existsSync(dir)) return [...repos]
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
  const receipt = [...(comments || [])].reverse().find(c => !QA_LOGINS.has(c.user?.login) && parseReceiptDetail(c.body))
  if (receipt) { const d = parseReceiptDetail(receipt.body); return { ...base, kind: d.kind, fix_pr: d.fix_pr, reason: d.reason, by: receipt.user?.login, at: receipt.created_at, receipt_url: receipt.html_url } }
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
