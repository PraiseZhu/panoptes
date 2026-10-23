// 待核实（QA 自查）：按统一分级（config/severity.md）证实不了的、或不是作者问题的条目，不进 prs[].findings，
// 单列在 prs[].pending_verification[]。它们不定 P 级、不让 PR 变 needs-attention、不 @、不私信、不开交接 issue、不计逾期；
// 由 QA 自己先查清（复核触发路径、写复现测试、重跑），查实了再按统一分级定级进 findings。
// 「怀疑」不是等级（Praise 2026-10-23）。

export const PENDING_KINDS = Object.freeze({
  'review-plausible': 'AI 代码审查怀疑但排除不了（PLAUSIBLE）',
  'review-unverified': 'AI 代码审查自己列为待核实（证据不足 / 写明的设计有疑问）',
  'review-incomplete': 'AI 代码审查缺触发条件、错误行为、实际影响、证据或行号',
  'review-unknown-level': 'AI 代码审查给了不认识的等级',
  'review-p1-unconfirmed': 'AI 代码审查单独发现的 P1，复核触发路径没能证实',
  'review-ruled': '和合并前已裁定的同一问题对得上（不成立 / P3 / 有意设计），代码没变',
  'check-qa': '检查失败但不是作者的问题（unverified / setup-failed / timeout / flaky）',
  'ai-test': 'AI 补测在当前代码上失败（未确认，不算测试佐证）',
  'own-tests': 'PR 改过的测试没被任何检查跑到（可能是 QA 自己没配上）',
  'qa-error': '测试系统自己出错，PR 本身没测到',
})
export const PENDING_LABEL_ZH = '待核实（QA 自查）'

// 一条 finding 形状的东西 → 待核实条目。原来会定的等级只作参考（severity_if_confirmed），不参与任何计数
export function toPending(kind, f, { reason = null, reasonZh = null, extra = {} } = {}) {
  if (!PENDING_KINDS[kind]) throw new Error(`unknown pending kind ${kind}`)
  const { severity, ...rest } = f ?? {}
  return {
    ...rest,
    pending_kind: kind, owner: 'qa', label_zh: PENDING_LABEL_ZH, kind_zh: PENDING_KINDS[kind],
    severity_if_confirmed: severity ?? null, test_backed: false,
    pending_reason: reason, pending_reason_zh: reasonZh,
    ...extra,
  }
}

export const pendingSummary = (items) => {
  const by = {}
  for (const p of items ?? []) by[p.pending_kind] = (by[p.pending_kind] ?? 0) + 1
  return { total: (items ?? []).length, by_kind: by }
}
