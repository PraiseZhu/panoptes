// 给 AI 补测线索（source: "ai-test"）打 verified 标记。旧报告里线索在 prs[].findings[]，只改 verified* 这几个字段，其他字段原样保留；
// 新报告里线索在 prs[].pending_verification[]，见 promotePending
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
// 按原定等级（severity_if_confirmed，P2）计入，PR 的等级 / 结论跟着重算；撤销（--unset）就移回待核实。旧报告的线索仍在 findings 里，照旧只改 verified*
const RANK = { P0: 0, P1: 1, P2: 2, P3: 3 }
function recomputePr(p) {
  const sevs = (p.findings ?? []).map((f) => f.severity).filter((s) => s in RANK)
  if (p.severity in RANK || p.severity == null) p.severity = sevs.length ? sevs.reduce((m, s) => (RANK[s] < RANK[m] ? s : m), 'P3') : 'P3'
  const top = p.severity
  if (['clean', 'partially-verified', 'review-suggested', 'needs-attention'].includes(p.verdict)) {
    p.verdict = ['P0', 'P1'].includes(top) ? 'needs-attention' : top === 'P2' ? 'review-suggested' : (p.pending_verification ?? []).length ? 'partially-verified' : p.verdict === 'clean' ? 'clean' : p.verdict
  }
}
function promotePending(p, sel, { at, by, note, unset }, changed) {
  if (unset) {
    const back = (p.findings ?? []).filter((f) => matchLead(f, sel) && f.promoted_from_pending)
    for (const f of back) {
      const { promoted_from_pending, severity, ...rest } = f
      Object.assign(rest, { verified: false, verified_at: null, verified_by: null, verified_note: null, severity_if_confirmed: severity })
      ;(p.pending_verification ??= []).push(rest)
      changed.push({ pr: p.number, check_id: f.check_id, fingerprint: f.fingerprint, moved: 'findings→pending_verification' })
    }
    if (back.length) p.findings = p.findings.filter((f) => !back.includes(f))
    return
  }
  const hit = (p.pending_verification ?? []).filter((f) => matchLead(f, sel))
  for (const f of hit) {
    const { severity_if_confirmed, ...rest } = f
    ;(p.findings ??= []).push({ ...rest, severity: severity_if_confirmed ?? 'P2', verified: true, verified_at: at, verified_by: by, verified_note: note, promoted_from_pending: true })
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
    const touched = new Set(moved.map((m) => m.check_id))
    for (const f of [...(p.findings ?? []), ...(unset ? p.pending_verification ?? [] : [])]) {
      if (!matchLead(f, { fingerprint, checkId })) continue
      if (unset) Object.assign(f, { verified: false, verified_at: null, verified_by: null, verified_note: null })
      else Object.assign(f, { verified: true, verified_at: at, verified_by: by, verified_note: note })
      if (!touched.has(f.check_id)) changed.push({ pr: p.number, check_id: f.check_id, fingerprint: f.fingerprint })
      // 交接用例的 failed 只是线索：同一条补测被确认后才把 confirmed 置 true
      for (const c of p.handoff?.cases ?? []) if (c.qa_status === 'failed' && f.check_id && c.evidence?.check_id === f.check_id) c.confirmed = !unset
    }
  }
  return changed
}
