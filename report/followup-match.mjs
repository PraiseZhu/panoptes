// 回应与问题逐条匹配：一条回应只收起它明确针对的那条问题（Mivo QA 运维规则）。
// item: { problems: [主问题标题], files: [主问题自己的文件], coverage: bool, sole: bool }
//   sole = 该 PR 只剩这一条未关闭的非覆盖率问题 → 作者任何回复（明说覆盖率的除外）都算回应它。
// ev:   { type, text?, path? }  — pr-references（引用原 PR 号的修复 PR）视为覆盖全部。
import { BOT_STATUS_RE } from './handoff-receipts.mjs'
const norm = (s) => String(s || '').toLowerCase().replace(/\s+/g, '')
// 只认明确的“全部”说法；'CI 全部通过'、'都不会' 不算
const ALL = /(这几条|这些|以上|上面|所有问题|几处).{0,6}(都|全部)(不用改|不改|已修|已改|改了|修了|处理|是误报)|全部(不用改|不改|已修|已改|改了|修了|处理)|都(不用改|不改|已修|已改|改了|修了|处理了?)|\ball (of )?(these|the) (issues|findings|comments)\b|\bboth (are )?(fixed|addressed)\b/i
const COVERAGE = /coverage|覆盖率|补测试|补单测|加测试/i
// 太常见的文件名必须带目录才算引用
const COMMON_FILE = /^(index\.\w+|main\.\w+|run\.mjs|readme\.md|agents\.md|claude\.md|package\.json|package-lock\.json|tsconfig\.json|types?\.ts|utils?\.\w+|constants?\.\w+|config\.\w+)$/i
export const isCoverage = (f) => f.source === 'coverage-gate' || /coverage|覆盖率/i.test(f.title || '')

// 标题引用：标题里任意连续 n 个汉字（默认 4）原样出现在回应里（英文交给文件规则）
function quotesTitle(title, text, n = 4) {
  const x = norm(text)
  for (const run of String(title || '').match(new RegExp(`[\\u4e00-\\u9fff]{${n},}`, 'g')) || [])
    for (let i = 0; i + n <= run.length; i++) if (x.includes(run.slice(i, i + n))) return true
  return false
}
function mentionsFile(f, text) {
  const base = f.split('/').pop()
  if (COMMON_FILE.test(base)) return f.includes('/') && text.includes(f)
  return new RegExp(`(^|[^\\w.-])${base.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![\\w-])`).test(text)
}

// 引用原 PR 号的修复 PR 是否算跟进（防"只提了一嘴"误收起）：
//  - item.ignore_prs: [号码] → 这些 PR 永不算跟进（日报 bot 重开条目时写入当初误判的 PR）
//  - item.reopened: true → 同上，另外 reopen_prior_evidence 里记录的 PR 也一并忽略
//  - item.files 非空时，PR 必须改到其中至少一个文件；只引用 PR 号、不碰相关文件不算
//    （item.files 为空——如覆盖率问题没有具体文件——无法判断，维持只看引用）
export function ignoredPrs(item) {
  const s = new Set((item.ignore_prs || []).map(Number))
  if (item.reopened || item.reopen_prior_evidence) for (const e of item.reopen_prior_evidence || []) if (e.pr) s.add(Number(e.pr))
  return s
}
export function prCountsAsFollowup(p, item) {
  if (p.number === item.pr) return false
  if (!new RegExp(`#${item.pr}(?!\\d)`).test(`${p.title}\n${p.body || ''}`)) return false
  if (ignoredPrs(item).has(Number(p.number))) return false
  const files = item.files || []
  if (files.length && !(p.files || []).some(f => files.includes(f))) return false
  return true
}

// reports/acceptance 下属于该 PR 的报告：PR-943.json / review-PR-943.json / PR-943-nocache.json（不含 PR-9431）
export const isAcceptanceReportFor = (name, pr) => new RegExp(`^(review-)?PR-${pr}(-[\\w-]+)?\\.json$`).test(name)

// 回应里点名了这条问题（文件或标题）—— 用来判断一条「结论：…」回执是针对某几条，还是针对整个私信
export function namesItem(text, item) {
  const t = String(text || '')
  return (item.files || []).some(f => f && mentionsFile(f, t)) || (item.problems || []).some(p => quotesTitle(p, t))
}

export function matchesItem(ev, item) {
  if (ev.type === 'pr-references' || ev.type === 'fix-pr-merged') return true
  // bot 的状态贴（盯梢停下 blocked-notice、P2 自动关讨论 auto-close、vigil-handoff 交接）不是对 QA 问题的回应
  if (BOT_STATUS_RE.test(String(ev.text || ''))) return false
  if (!String(ev.text || '').trim()) return false // 空正文（如行内回复自动生成的空 review）不算回应
  const text = `${ev.text || ''}\n${ev.path || ''}`
  const talksCoverage = COVERAGE.test(text)
  // 「结论：…」回执（统一格式）：点名了哪条就收哪条；没点名任何一条 → 回的是整条私信 —— 但明说覆盖率的只算覆盖率那条
  if (ev.receipt && ev.receipt_generic && (item.coverage || !talksCoverage)) return true
  if (item.sole && !item.coverage && !talksCoverage) return true // 明说覆盖率的回复只算覆盖率那条
  if (ALL.test(text)) return true
  if (item.coverage) return talksCoverage
  if ((item.files || []).some(f => f && mentionsFile(f, text))) return true
  // 讲覆盖率的回复点名非覆盖率问题要更确定：标题至少连续 6 个字（4 个字太容易撞上，如「附件顺序」）
  return (item.problems || []).some(p => quotesTitle(p, text, talksCoverage ? 6 : 4))
}

// 感谢/提醒里的条目名：标题截到第一个句读（。；，：,;:，切点前至少 8 字），最多 30 字；超长按字截断但不切断英文单词，截断时加 '…'
export function shortTitle(title, max = 30) {
  const t = String(title || '').replace(/\s+/g, ' ').replace(/^[\s。；，：,;:]+/, '').trim() // 开头的句读去掉，不返回空串
  const cs = Array.from(t)
  const p = cs.findIndex((ch, i) => i >= 8 && /[。；，：,;:]/.test(ch)) // 切点前至少留 8 字
  if (p >= 0 && p <= max) return cs.slice(0, p).join('').trim()
  if (cs.length <= max) return t
  let cut = max
  const word = /[A-Za-z0-9_]/
  if (word.test(cs[cut - 1]) && word.test(cs[cut])) { const sp = cs.lastIndexOf(' ', cut - 1); if (sp > 0) cut = sp }
  return cs.slice(0, cut).join('').trim() + '…'
}
