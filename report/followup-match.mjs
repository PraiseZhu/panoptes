// 回应与问题逐条匹配：一条回应只收起它明确针对的那条问题（Mivo QA 运维规则）。
// item: { problems: [title...], files: [path...], coverage: bool }
// ev:   { type, text?, path? }  — pr-references（引用原 PR 号的修复 PR）沿用原行为，视为覆盖全部。
const norm = (s) => String(s || '').toLowerCase().replace(/\s+/g, '')
const ALL = /全部|都不|都改|都修|都已|都处理|\ball\b|\bboth\b/i
const COVERAGE = /coverage|覆盖率|测试|\btests?\b|单测/i
export const isCoverage = (f) => f.source === 'coverage-gate' || /coverage|覆盖率/i.test(f.title || '')

// 标题关键片段：任意连续 4 字（去空白后）出现在回应里即视为引用
function quotesTitle(title, text) {
  const t = norm(title), x = norm(text)
  for (let i = 0; i + 4 <= t.length; i++) {
    const w = t.slice(i, i + 4)
    if (/[a-z\u4e00-\u9fff]{2}/.test(w) && x.includes(w)) return true
  }
  return false
}

export function matchesItem(ev, item) {
  if (ev.type === 'pr-references') return true
  const text = `${ev.text || ''}\n${ev.path || ''}`
  if (ALL.test(text)) return true
  if (item.coverage && COVERAGE.test(text)) return true
  if ((item.files || []).some(f => f && (text.includes(f) || text.includes(f.split('/').pop())))) return true
  return (item.problems || []).some(p => !isCoverage({ title: p }) && quotesTitle(p, text))
}
