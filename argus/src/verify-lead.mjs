// 给 AI 补测线索（prs[].findings[] 里 source: "ai-test"）打 verified 标记；只改这几个字段，其他字段原样保留
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

export function verifyLeads(report, { pr, fingerprint, checkId, by = null, note = null, at = new Date().toISOString(), unset = false } = {}) {
  if (!fingerprint && !checkId) throw new Error('need --fingerprint or --check-id')
  const prs = Array.isArray(report?.prs) ? report.prs : []
  const changed = []
  for (const p of prs) {
    if (pr != null && Number(p.number) !== Number(pr)) continue
    for (const f of p.findings ?? []) {
      if (!matchLead(f, { fingerprint, checkId })) continue
      if (unset) Object.assign(f, { verified: false, verified_at: null, verified_by: null, verified_note: null })
      else Object.assign(f, { verified: true, verified_at: at, verified_by: by, verified_note: note })
      changed.push({ pr: p.number, check_id: f.check_id, fingerprint: f.fingerprint })
    }
  }
  return changed
}
