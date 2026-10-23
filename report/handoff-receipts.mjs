// 作者回执（统一成一种，Praise 2026-10-23；写法细节见下面 RECEIPT_KINDS 上的说明）：首行固定写
//   结论：已修 #<修复PR号>   /   结论：不修，<原因>   /   结论：误报，<原因>
// （「结论」可加粗，半角、全角冒号都认，下面可以接人话正文）。私信之后在原 PR 下的评论、交接仓 issue 下的评论都认这一种；
// 修复 PR 标题或正文写了「QA #原PR号」并合并（改到问题所在的文件）后，自动当「已修」（fixPrResolutions，不限作者）。
// issue 已关闭 / 打了 status:false-positive 等标签，也算这条问题有了结论，私信提醒和逾期通报都不再列它。
// issue 正文里的标记 <!-- mivo-qa-handoff: key=mq1-<fingerprint>-pr<N> pr=<N> --> 把 issue 对回原 PR 的那条问题。
// 同一口径的 Python 副本：pi-fallback bin/handoff.py（RECEIPT / parse_receipt / QA_REF）。
import { readFileSync, existsSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { routedRepos } from './handoff-routes.mjs'

// 一行回执的写法（2026-10-24 定稿，和 pi-fallback bin/handoff.py parse_receipt 同一口径；共用用例 argus/tests/fixtures/receipt-cases.json）：
// 看评论的第一行有意义的话 —— 先去掉 HTML 注释和 BOM，跳过空行、代码块（含内容）、引用行（>）、只有 @ 的行；不再在后面的行里兜底找。
// 「结论」可加粗，半角 / 全角冒号；结论词（同义词归并：已修复 / 已改 → 已修；不成立 → 误报；不用改 / 不需要改 / 无需修改 / 不改 → 不修）
// 后面必须是行尾、空白、*、# 或标点，所以「结论：不修改…」「结论：误报率…」不算。已修时取后面第一个（owner/repo）#N 当修复 PR，
// 允许夹「（」「PR」「见」「，」；等于原 PR 号的不算修复 PR。原因去掉开头的标点和「原因：」、去掉 **，最多 300 字。
// 盯梢 bot 的回帖（带 <!-- mivo-watcher-receipt 标记、没有「结论：」行）按它的固定话术认：「确认此意见成立并已修复」/「要不要改代码：已改」→ 已修，
// 「在当前 head 上不成立」→ 误报，「要不要改代码：（本轮）不改 / 不用 / 不需要」→ 不修。bot 的状态贴（task=blocked-notice / auto-close、
// <!-- vigil-handoff ）任何时候都不算回执
const RECEIPT_KINDS = { 已修复: '已修', 已修: '已修', 已改: '已修', 误报: '误报', 不成立: '误报', 不需要改: '不修', 不用改: '不修', 无需修改: '不修', 不修: '不修', 不改: '不修' }
const KIND_ALT = Object.keys(RECEIPT_KINDS).sort((x, y) => y.length - x.length).join('|')
const RECEIPT_LINE = new RegExp(`^(?:\\*\\*)?结论(?:\\*\\*)?[ \\t]*[:：][ \\t]*(?:\\*\\*)?[ \\t]*(${KIND_ALT})(?=$|[\\s*#，,、:：;；。.!！(（])(.*)$`)
const FIX_REF = /^[\s*（(，,、:：]*(?:(?:见|PR)[\s*（(，,、:：]*)*(?:[\w.-]+\/[\w.-]+)?#([0-9]+)[）)]?/i
const REASON_LEAD = /^[\s*，,、:：;；。.!！（()）]*(?:原因[ \t]*[:：])?[\s*]*/
const LINE_SPLIT = /\r\n|\r|\n|\u2028|\u2029/
export const BOT_STATUS_RE = /<!--\s*(?:vigil-handoff\b|mivo-watcher-receipt\b[^>]*\btask=(?:blocked-notice|auto-close)\b)/
const WATCHER_RECEIPT_RE = /<!--\s*mivo-watcher-receipt\b/
// 修复 PR 引用原 PR：QA #N / QA: #N / QA：#N / QA: xindong/mivo-canvas-plugin#N
// 「QA」后面可以夹最多 8 个非 # 的字（「QA 回访 」「QA: 」），然后是一串原 PR 号（/ , ， 、 和 & 隔开，可带 owner/repo），
// 一个修复 PR 对应多份 QA 报告时写成「QA 回访 #904/#910/#898/#884」（2026-10-24：#918 这样写，原来只认第一个号）。
// 「QA」前不能是字母（SQA、FAQA 不算）。同一口径的 Python 副本：pi-fallback bin/handoff.py qa_ref
const QA_REF_HEAD = '(?<![A-Za-z])QA[^#\\n]{0,8}?'
const QA_REF_ONE = '(?:[\\w.-]+\\/[\\w.-]+)?#'
const QA_REF_SEP = '\\s*[\\/,，、&和]\\s*'
export const qaRefRe = (n) => new RegExp(`${QA_REF_HEAD}(?:${QA_REF_ONE}\\d+(?!\\d)${QA_REF_SEP})*${QA_REF_ONE}${Number(n)}(?!\\d)`, 'i')
// 一段文字里「QA …」引用到的全部原 PR 号
export function qaRefs(text) {
  const out = new Set()
  for (const m of String(text ?? '').matchAll(new RegExp(`${QA_REF_HEAD}((?:${QA_REF_ONE}\\d+(?!\\d)(?:${QA_REF_SEP})?)+)`, 'gi')))
    for (const x of m[1].matchAll(/#(\d+)(?!\d)/g)) out.add(Number(x[1]))
  return [...out]
}
export const QA_REF_ALL = /(?<![A-Za-z])QA[^#\n]{0,8}?(?:[\w.-]+\/[\w.-]+)?#(\d+)(?!\d)/gi
const MARKER_RE = /<!--\s*mivo-qa-handoff:\s*key=(\S+)\s+pr=(\d+)/
const RESOLVED_LABELS = new Set(['status:false-positive', 'status:closed', 'status:fixed', 'status:wontfix', 'status:resolved'])
// QA 自己（PraiseZhu）的评论不算作者回执
export const QA_LOGINS = new Set((process.env.MIVO_QA_HANDOFF_QA_LOGINS || 'PraiseZhu').split(',').map(s => s.trim()).filter(Boolean))
// 兼容旧接口：只回结论种类
export const RECEIPT_RE = new RegExp(`^(?:\\*\\*)?结论(?:\\*\\*)?[ \\t]*[:：][ \\t]*(?:\\*\\*)?[ \\t]*(${KIND_ALT})`)

export function receiptLine(body) {
  let fence = false
  for (const line of String(body ?? '').replace(/<!--[\s\S]*?-->/g, '').replace(/\ufeff/g, '').split(LINE_SPLIT)) {
    const t = line.trim()
    if (/^(```|~~~)/.test(t)) { fence = !fence; continue }
    if (fence || !t || t.startsWith('>') || /^(?:@[\w.-]+[\s,，、]*)+$/.test(t)) continue
    return t
  }
  return null
}
function watcherReceipt(body) {
  const b = String(body ?? '')
  if (!WATCHER_RECEIPT_RE.test(b)) return null
  if (/确认此意见成立并已修复/.test(b) || /要不要改代码(?:\*\*)?\s*[:：]\s*已改/.test(b)) return '已修'
  if (/在当前\s*head\s*上不成立/i.test(b)) return '误报'
  if (/要不要改代码(?:\*\*)?\s*[:：]\s*(?:本轮)?(?:不改|不用|不需要)/.test(b)) return '不修'
  return null
}
export function parseReceipt(body) { return parseReceiptDetail(body)?.kind ?? null }
// { kind, fix_pr, reason, via: 'line' | 'watcher' } 或 null；pr = 原 PR 号（「已修 #<原PR号>」不当修复 PR）
export function parseReceiptDetail(body, { pr = null } = {}) {
  if (BOT_STATUS_RE.test(String(body ?? ''))) return null
  const m = (receiptLine(body) ?? '').match(RECEIPT_LINE)
  if (m) {
    const kind = RECEIPT_KINDS[m[1]]
    let rest = m[2], fix = null
    if (kind === '已修') {
      const f = rest.match(FIX_REF)
      if (f) { rest = rest.slice(f[0].length); if (pr == null || Number(f[1]) !== Number(pr)) fix = Number(f[1]) }
    }
    const reason = rest.replace(REASON_LEAD, '').replace(/\*\*/g, '').trim().slice(0, 300)
    return { kind, fix_pr: fix, reason: reason || null, via: 'line' }
  }
  const w = watcherReceipt(body)
  return w ? { kind: w, fix_pr: null, reason: null, via: 'watcher' } : null
}
// 已合并的修复 PR（标题或正文写了「QA #原PR号」）→ 「已修」结论 —— 私信跟进、逾期通报、交接 issue 统一用这一条规则（不限作者）：
// 已合并；不是原 PR 本身；不在该条的 ignore_prs 里；问题有文件时必须改到其中至少一个文件，PR 的文件清单拿不到就不认。
// prs: [{ number, title, body, mergedAt|merged_at, url, files: [path] | [{ path|filename }] }]
const fileOf = (f) => (typeof f === 'string' ? f : f?.path ?? f?.filename ?? null)
export function fixPrResolutions(prs, { pr, files = [], ignore = [] }) {
  const re = qaRefRe(pr)
  const skip = new Set((ignore ?? []).map(Number))
  return (prs ?? []).filter((p) => (p.mergedAt || p.merged_at) && Number(p.number) !== Number(pr) && !skip.has(Number(p.number)) && re.test(`${p.title ?? ''}\n${p.body ?? ''}`)
    && (!files.length || (Array.isArray(p.files) && p.files.map(fileOf).some((f) => files.includes(f)))))
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
  const receipt = [...(comments || [])].reverse().find(c => !QA_LOGINS.has(c.user?.login) && parseReceiptDetail(c.body, { pr: mk.pr }))
  if (receipt) { const d = parseReceiptDetail(receipt.body, { pr: mk.pr }); return { ...base, kind: d.kind, fix_pr: d.fix_pr, reason: d.reason, by: receipt.user?.login, at: receipt.created_at, receipt_url: receipt.html_url } }
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
