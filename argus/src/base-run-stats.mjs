// 合并前版本（base）运行的成功率：每个（PR, 检查, 用例）取最新报告里的 evidence.base_run 一次。
// 成功 = base 上跑通（pass / not-failing）；fail = base 上同样失败；其余（not-run / timeout / unavailable）= 没跑成。
// 失败按检查分原因：api-probe → 探针/环境；trial-only（bench）→ 机器负载；其余 → 主干本身坏了（或真不稳定）
export function baseRunStats(reports, { trialOnly = ['bench'] } = {}) {
  const rows = new Map()
  for (const r of reports) for (const p of r?.prs ?? []) for (const c of p.failed_checks ?? []) {
    const br = c?.evidence?.base_run
    if (br == null) continue
    rows.set(`${p.number}|${c.check_id}|${c.test ?? '*'}`, { pr: p.number, check: c.check_id, test: c.test ?? null, base_run: br, day: String(p.tested_at ?? '').slice(0, 10) })
  }
  const all = [...rows.values()]
  const ok = all.filter((x) => x.base_run === 'pass' || x.base_run === 'not-failing').length
  const fail = all.filter((x) => x.base_run === 'fail')
  const cause = (x) => (x.check === 'api-probe' ? 'probe-or-env' : trialOnly.includes(x.check) ? 'machine-load' : 'main-broken')
  const by_cause = {}
  for (const x of fail) by_cause[cause(x)] = (by_cause[cause(x)] ?? 0) + 1
  return { total: all.length, ok, fail: fail.length, not_run: all.length - ok - fail.length, rate: all.length ? ok / all.length : null, by_cause, rows: all }
}
