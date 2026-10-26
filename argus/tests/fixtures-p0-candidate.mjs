// 构造样例（Praise 2026-10-26 裁决第 1 题）：已合并、本 PR 引入的产品类检查失败、最新主干复查仍失败 → nightly 生成 p0_candidate。
// buildP0CandidateReport() 用 nightly.mjs 里真实的 p0Candidate / severityFor / p0NoteFor 拼出 merged/PR-N.json 的形状；
// pi/test/p0-candidate-report.json 是它的输出，pi/test/test_severity_unify.py 用它证明合并官的 P0 告警能被触发。
export async function buildP0CandidateReport() {
  const N = await import('../src/nightly.mjs')
  const step = { id: 'unit', kind: 'product' }, status = 'introduced', merged = true
  const check = {
    check_id: 'unit', status, severity: N.severityFor({ status, step, merged }),
    ...(N.p0Candidate({ status, step, merged }) ? { p0_candidate: true, p0_note: N.p0NoteFor({ step, latestSha: '0'.repeat(40), latestIsSame: false, latestRun: 'fail' }) } : {}),
  }
  return { prs: [{ number: 9001, merged: true, severity: check.severity, failed_checks: [check], findings: [{ source: 'check', test_backed: true, ...check }] }] }
}
