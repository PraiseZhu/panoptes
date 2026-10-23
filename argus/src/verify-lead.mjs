// 给 AI 补测线索（source: "ai-test"）打 verified 标记。旧报告里线索在 prs[].findings[]，只改 verified* 这几个字段，其他字段原样保留；
// 新报告里线索在 prs[].pending_verification[]，见 promotePending
import { pendingSummary } from './pending.mjs'
export const VERIFY_FIELDS = ['verified', 'verified_at', 'verified_by', 'verified_note']

export function isAiLead(f) { return f?.source === 'ai-test' }

// 未核实：verified 缺省 / null / false 都算未核实；只有 === true 才算已核实
export function isVerified(f) { return f?.verified === true }

export function matchLead(f, sel) {
  if (!isAiLead(f)) return false
  if (sel.fingerprint && (f.fingerprint === sel.fingerprint || f.pr_fingerprint === sel.fingerprint)) return true
  if (sel.checkId && f.check_id === sel.checkId) return true
  return false
}

// 统一分级（2026-10-23）起，AI 补测线索在 prs[].pending_verification[]（待核实）：人确认后（verified）才移进 findings、
// 按原定等级（severity_if_confirmed，P2）计入，PR 的等级 / 结论 / 摘要跟着重算；撤销（--unset）就移回待核实。旧报告的线索仍在 findings 里，照旧只改 verified*。
// 移进 findings 时去掉待核实的标记（pending_kind / owner / label_zh / kind_zh / pending_reason*，ai-test 的 status unconfirmed → confirmed），
// 原值存在 promoted_from_pending 里，--unset 原样还原 —— 否则 finding-policy notifiable() 仍把它当待核实，永远不发作者。
// 代码审查的待核实条目（AI 单独 P1 复核没证实 / 没跑成、PLAUSIBLE 等）也能由人核实后移进 findings（只按 --fingerprint 选，
// check_id 都是 code-review），带 verification { status: 'confirmed', by: 'manual', at, note }，notifiable 才会放行。
const RANK = { P0: 0, P1: 1, P2: 2, P3: 3 }
const SEVS = ['P0', 'P1', 'P2', 'P3']
const PENDING_MARKS = ['pending_kind', 'owner', 'label_zh', 'kind_zh', 'pending_reason', 'pending_reason_zh']
// 人可以核实后移进 findings 的代码审查待核实种类
export const MANUAL_REVIEW_KINDS = ['review-p1-unconfirmed', 'review-plausible', 'review-unverified', 'review-incomplete', 'review-unknown-level']
const isReviewPending = (f) => f?.source === 'code-review' && MANUAL_REVIEW_KINDS.includes(f.pending_kind)
const matchReview = (f, sel) => isReviewPending(f) && Boolean(sel.fingerprint) && (f.fingerprint === sel.fingerprint || f.pr_fingerprint === sel.fingerprint)
const matchPending = (f, sel) => matchLead(f, sel) || matchReview(f, sel)

function recomputePr(p) {
  const sevs = (p.findings ?? []).map((f) => f.severity).filter((s) => s in RANK)
  if (p.severity in RANK || p.severity == null) p.severity = sevs.length ? sevs.reduce((m, s) => (RANK[s] < RANK[m] ? s : m), 'P3') : 'P3'
  const top = p.severity
  if (['clean', 'partially-verified', 'review-suggested', 'needs-attention'].includes(p.verdict)) {
    p.verdict = ['P0', 'P1'].includes(top) ? 'needs-attention' : top === 'P2' ? 'review-suggested' : (p.pending_verification ?? []).length ? 'partially-verified' : p.verdict === 'clean' ? 'clean' : p.verdict
  }
  if (Array.isArray(p.finding_sources)) p.finding_sources = [...new Set((p.findings ?? []).map((f) => f.source))]
  if (p.pending_verification?.length) p.pending_summary = pendingSummary(p.pending_verification)
  else delete p.pending_summary
  // 代码审查的结论字段跟着最终的 findings 走（同 nightly settleCodeReviewStatus）
  const cr = p.code_review
  if (cr?.ran === true) {
    const blocking = (p.findings ?? []).filter((f) => f.source === 'code-review' && ['P0', 'P1'].includes(f.severity))
    cr.status = blocking.length ? 'failed' : 'passed'
    cr.pending_count = (p.pending_verification ?? []).filter((x) => x.source === 'code-review').length
  }
}
// 整份报告的 summary（有才改）：等级计数和待核实汇总按 prs 重算
function recomputeSummary(report) {
  const sm = report?.summary
  if (!sm || typeof sm !== 'object') return
  const prs = report.prs ?? []
  const all = prs.flatMap((p) => p.findings ?? [])
  if (sm.prs_by_severity) sm.prs_by_severity = Object.fromEntries([...SEVS, 'untested'].map((s) => [s, prs.filter((p) => p.severity === s).length]))
  if (sm.findings_by_severity) sm.findings_by_severity = Object.fromEntries(SEVS.map((s) => [s, all.filter((f) => f.severity === s).length]))
  if ('findings_total' in sm) sm.findings_total = all.length
  if (sm.test_backed_findings_by_severity) sm.test_backed_findings_by_severity = Object.fromEntries(SEVS.map((s) => [s, all.filter((f) => f.test_backed && f.severity === s).length]))
  sm.pending_verification = pendingSummary(prs.flatMap((p) => p.pending_verification ?? []))
}

function toFinding(f, { at, by, note }) {
  const { severity_if_confirmed, ...rest } = f
  const saved = Object.fromEntries(PENDING_MARKS.filter((k) => k in rest).map((k) => [k, rest[k]]))
  for (const k of PENDING_MARKS) delete rest[k]
  if ('status' in rest) saved.status = rest.status
  if (rest.source === 'code-review') {
    if ('verification' in rest) saved.verification = rest.verification
    return { ...rest, severity: severity_if_confirmed ?? 'P1', verification: { status: 'confirmed', by: 'manual', ...(by ? { who: by } : {}), at, note }, promoted_from_pending: saved }
  }
  return { ...rest, ...(rest.status === 'unconfirmed' ? { status: 'confirmed' } : {}), severity: severity_if_confirmed ?? 'P2', verified: true, verified_at: at, verified_by: by, verified_note: note, promoted_from_pending: saved }
}
function toPendingAgain(f) {
  const { promoted_from_pending: saved, severity, ...rest } = f
  const back = { ...rest, ...(typeof saved === 'object' && saved ? saved : {}), severity_if_confirmed: severity }
  if (rest.source === 'code-review') { if (!(saved && 'verification' in saved)) delete back.verification }
  else Object.assign(back, { verified: false, verified_at: null, verified_by: null, verified_note: null })
  return back
}

function promotePending(p, sel, { at, by, note, unset }, changed) {
  if (unset) {
    const back = (p.findings ?? []).filter((f) => f.promoted_from_pending && matchPending({ ...f, pending_kind: f.promoted_from_pending?.pending_kind }, sel))
    for (const f of back) {
      ;(p.pending_verification ??= []).push(toPendingAgain(f))
      changed.push({ pr: p.number, check_id: f.check_id, fingerprint: f.fingerprint, moved: 'findings→pending_verification' })
    }
    if (back.length) p.findings = p.findings.filter((f) => !back.includes(f))
    return
  }
  const hit = (p.pending_verification ?? []).filter((f) => matchPending(f, sel))
  for (const f of hit) {
    ;(p.findings ??= []).push(toFinding(f, { at, by, note }))
    changed.push({ pr: p.number, check_id: f.check_id, fingerprint: f.fingerprint, moved: 'pending_verification→findings' })
  }
  if (hit.length) p.pending_verification = p.pending_verification.filter((f) => !hit.includes(f))
}

export function verifyLeads(report, { pr, fingerprint, checkId, by = null, note = null, at = new Date().toISOString(), unset = false } = {}) {
  if (!fingerprint && !checkId) throw new Error('need --fingerprint or --check-id')
  const prs = Array.isArray(report?.prs) ? report.prs : []
  const changed = []
  for (const p of prs) {
    if (pr != null && Number(p.number) !== Number(pr)) continue
    const moved = []
    promotePending(p, { fingerprint, checkId }, { at, by, note, unset }, moved)
    if (moved.length) recomputePr(p)
    changed.push(...moved)
    // 移动过的条目的交接用例：确认 / 撤销同步（下面的循环只管还在 findings 里的旧报告线索）
    for (const m of moved) for (const c of p.handoff?.cases ?? []) if (c.qa_status === 'failed' && m.check_id && c.evidence?.check_id === m.check_id) c.confirmed = !unset
    const touched = new Set(moved.map((m) => m.fingerprint))
    for (const f of [...(p.findings ?? []), ...(unset ? p.pending_verification ?? [] : [])]) {
      if (!matchLead(f, { fingerprint, checkId })) continue
      if (touched.has(f.fingerprint)) continue
      if (unset) Object.assign(f, { verified: false, verified_at: null, verified_by: null, verified_note: null })
      else Object.assign(f, { verified: true, verified_at: at, verified_by: by, verified_note: note })
      changed.push({ pr: p.number, check_id: f.check_id, fingerprint: f.fingerprint })
      // 交接用例的 failed 只是线索：同一条补测被确认后才把 confirmed 置 true
      for (const c of p.handoff?.cases ?? []) if (c.qa_status === 'failed' && f.check_id && c.evidence?.check_id === f.check_id) c.confirmed = !unset
    }
  }
  if (changed.some((c) => c.moved)) recomputeSummary(report)
  return changed
}
