// 哪些 finding 算作者的问题（@ / 私信 / 开交接 issue / 计逾期）—— 统一分级（审查机 Review-PR mivo/config/severity.md，Praise 2026-10-23）。
// 新报告（带 severity_standard）里 prs[].findings 只剩按统一分级成立的问题，待核实的在 prs[].pending_verification[]（不发作者）。
// 这里同时给旧报告兜底：旧报告里的这些一律不算作者的问题 ——
//   覆盖率 P2（coverage-gate；PR 自己写明目标没达到的是 stated-acceptance，算）、未核实的 AI 补测线索（ai-test，verified !== true）、
//   own-tests、qa-error、unverified / setup-failed / timeout / flaky 的检查、pre-existing / fixed-later、PLAUSIBLE 的审查结论、
//   未确认的交接用例失败、判了误报的。
// 同一口径的副本：mivo-qa/ledger/build-ledger.mjs notifiable()、pi-fallback bin/finding_policy.py notifiable()；改这里要一起改。
export const AUTHOR_SEVERITIES = ['P0', 'P1', 'P2']

export function notifiable(f) {
  if (!f || !AUTHOR_SEVERITIES.includes(f.severity) || f.pending_kind) return false
  if (f.qa_status === 'failed' && f.confirmed === false) return false
  if (f.pre_existing || f.not_a_pr_finding) return false
  if ((f.calibration || {}).to === '误报' || /false_positive|misreport|REJECTED/i.test(String(f.verdict || ''))) return false
  switch (f.source ?? 'check') {
    case 'code-review':
      if (String(f.verdict || '').toUpperCase() === 'PLAUSIBLE') return false
      // 新报告：AI 单独的 P1 只有整仓复核证实了才在 findings 里；旧报告没有 verification 字段，按当时的结论
      return f.verification ? f.verification.status === 'confirmed' : true
    case 'stated-acceptance': return true
    case 'ai-test': return f.verified === true
    case 'coverage-gate': case 'own-tests': case 'qa-error': return false
    default: // 失败的检查：只有本 PR 引入（introduced）的算；很早的旧报告没有 status，按当时的 test_backed
      return f.status == null ? f.test_backed !== false && f.flaky !== true : f.status === 'introduced'
  }
}

// 一个 prs[] 条目里作者的问题（主问题在前：等级高的、审查结论先于其他、报告顺序）
export function authorFindings(pr) {
  const rank = { P0: 0, P1: 1, P2: 2 }
  return (pr?.findings ?? []).map((f, i) => [i, f]).filter(([, f]) => notifiable(f))
    .sort((a, b) => rank[a[1].severity] - rank[b[1].severity] || a[0] - b[0]).map(([, f]) => f)
}
