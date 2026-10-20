// PR 自己新增 / 修改的测试覆盖了哪些维度（#1099：PR 补了失败分支和锁竞争的测试，AI 补测却还把这些当缺口）。
// 只认真的跑过（pass / fail，不是 unavailable / 超时）且确实执行了这个测试文件的步骤；维度靠测试新增行里的关键词判断。
import { globToRegExp, isTestPath } from './classify.mjs'

// 维度 → 测试新增行（含文件名）里出现即算覆盖的关键词。只列 AI 补测会去补的、能从测试代码看出来的几项
export const PR_TEST_DIMENSION_HINTS = Object.freeze({
  exception: /toThrow|\.rejects\b|\bthrow\b|mockRejectedValue|\bcatch\s*\(|pytest\.raises|assert\.(rejects|throws)|失败|错误|异常/,
  // 要更具体的信号（按单词边界）：重试 / 拒绝 和断言同时出现；单有 callback、setTimeout、mockResolvedValue 不算
  'async-callback-retry': { test: (t) => /\bretr(y|ies|ied|ying)\b|\brejects?\b|\brejected\b|mockRejectedValue(Once)?\b|\bRATE_LIMITED\b|重试/i.test(t) && /\bexpect\s*\(|\bassert\b|\.rejects\b|\btoThrow\b|pytest\.raises|断言/.test(t) },
  // 并发信号（Promise.all / concurrent / race / 并发 / 竞争）和锁（lock / mutex / 锁，单词边界，block 之类不算）同时出现
  concurrency: { test: (t) => /\bPromise\.(all|allSettled|race)\b|\bconcurren(t|cy|tly)\b|\bparallel\b|\brace\b|并发|竞争/i.test(t) && /\b(lock|locks|locked|locking|mutex)\b|锁/i.test(t) },
})
// 改到测试本身就意味着单测、回归被执行
const ALWAYS = ['unit', 'regression']

const matches = (path, globs) => (globs ?? []).some((g) => globToRegExp(g).test(path))
const ranOk = (r) => r && r.kind !== 'setup' && !r.unavailable && !r.timedOut

// 这个测试文件被哪一步真的执行了：测试报告完整时以报告为准；否则看 runsFiles（没写 runsFiles 的单测步骤收其它步骤不收的文件）
function ranBy(file, { results, profile }) {
  const steps = profile?.steps ?? []
  const claimed = steps.some((s) => matches(file, s.runsFiles))
  return (results ?? []).filter(ranOk).find((r) => {
    if (r.testOutcomeStatus === 'complete' && Array.isArray(r.testOutcomes)) return r.testOutcomes.some((t) => String(t.caseId).split(' > ')[0] === file)
    const s = steps.find((x) => x.id === r.id)
    if (!s) return false
    if (s.runsFiles) return matches(file, s.runsFiles)
    return !claimed && (s.covers ?? []).includes('unit')
  }) ?? null
}

const addedText = (f) => String(f.patch ?? '').split('\n').filter((l) => l.startsWith('+') && !l.startsWith('+++')).join('\n')

// → { <维度>: [测试文件…] }
export function prTestDimensions({ files, results, profile }) {
  const out = {}
  for (const f of files ?? []) {
    if (f.status === 'removed' || !isTestPath(f.filename) || !ranBy(f.filename, { results, profile })) continue
    const text = `${f.filename}\n${addedText(f)}`
    const keys = [...ALWAYS, ...Object.keys(PR_TEST_DIMENSION_HINTS).filter((k) => PR_TEST_DIMENSION_HINTS[k].test(text))]
    for (const k of keys) (out[k] ??= []).push(f.filename)
  }
  return out
}
