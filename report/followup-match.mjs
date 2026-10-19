// 回应与问题逐条匹配：一条回应只收起它明确针对的那条问题（Mivo QA 运维规则）。
// item: { problems: [主问题标题], files: [主问题自己的文件], coverage: bool, sole: bool }
//   sole = 该 PR 只剩这一条未关闭的非覆盖率问题 → 作者任何回复都算回应它。
// ev:   { type, text?, path? }  — pr-references（引用原 PR 号的修复 PR）视为覆盖全部。
const norm = (s) => String(s || '').toLowerCase().replace(/\s+/g, '')
// 只认明确的“全部”说法；'CI 全部通过'、'都不会' 不算
const ALL = /(这几条|这些|以上|上面|所有问题|几处).{0,6}(都|全部)|全部(不用改|不改|已修|已改|改了|修了|处理)|都(不用改|不改|已修|已改|改了|修了|处理了?)|\ball (of )?(these|the) (issues|findings|comments)\b|\bboth (are )?(fixed|addressed)\b/i
const COVERAGE = /coverage|覆盖率|补测试|补单测|加测试/i
// 太常见的文件名必须带目录才算引用
const COMMON_FILE = /^(index\.\w+|main\.\w+|run\.mjs|readme\.md|agents\.md|claude\.md|package\.json|package-lock\.json|tsconfig\.json|types?\.ts|utils?\.\w+|constants?\.\w+|config\.\w+)$/i
export const isCoverage = (f) => f.source === 'coverage-gate' || /coverage|覆盖率/i.test(f.title || '')

// 标题引用：标题里任意连续 4 个汉字原样出现在回应里（英文交给文件规则）
function quotesTitle(title, text) {
  const x = norm(text)
  for (const run of String(title || '').match(/[\u4e00-\u9fff]{4,}/g) || [])
    for (let i = 0; i + 4 <= run.length; i++) if (x.includes(run.slice(i, i + 4))) return true
  return false
}
function mentionsFile(f, text) {
  const base = f.split('/').pop()
  if (COMMON_FILE.test(base)) return f.includes('/') && text.includes(f)
  return new RegExp(`(^|[^\\w.-])${base.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![\\w-])`).test(text)
}

export function matchesItem(ev, item) {
  if (ev.type === 'pr-references') return true
  if (item.sole && !item.coverage) return true
  const text = `${ev.text || ''}\n${ev.path || ''}`
  if (ALL.test(text)) return true
  if (item.coverage) return COVERAGE.test(text)
  if ((item.files || []).some(f => f && mentionsFile(f, text))) return true
  return (item.problems || []).some(p => quotesTitle(p, text))
}
