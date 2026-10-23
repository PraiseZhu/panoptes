// 把多份报告（批量运行 / 单 PR 文件 merged/ open/）合成一份日报形状的 full-YYYY-MM-DD.json。
// 同一 PR 出现多次时以后面的来源为准；prs[] 条目原样保留，summary 重算，system_health 聚合（最差状态、问题清单合并）。
import { writeReport, isTestBacked } from './nightly.mjs'
import { pendingSummary } from './pending.mjs'
import { readFileSync, writeFileSync, renameSync, mkdirSync } from 'node:fs'
import { dirname } from 'node:path'
import { randomUUID } from 'node:crypto'
import { toShanghaiIso } from './util.mjs'

const SEVS = ['P0', 'P1', 'P2', 'P3']
const RANK = { P0: 0, P1: 1, P2: 2, P3: 3 }
const STATUS_RANK = { ok: 0, degraded: 1, failed: 2 }
const backed = (f) => isTestBacked({ source: 'check', ...f })

export function summarize(prs) {
  const all = prs.flatMap((p) => p.findings ?? [])
  return {
    pr_count: prs.length,
    prs_by_severity: Object.fromEntries([...SEVS, 'untested'].map((s) => [s, prs.filter((p) => p.severity === s).length])),
    findings_by_severity: Object.fromEntries(SEVS.map((s) => [s, all.filter((f) => f.severity === s).length])),
    findings_total: all.length,
    reused_from_on_merge: prs.filter((p) => p.reused).map((p) => p.number),
    tested_count: prs.filter((p) => !['not-tested', 'qa-error'].includes(p.verdict)).length,
    not_tested: prs.filter((p) => p.verdict === 'not-tested').map((p) => ({ number: p.number, code: p.not_tested_code, reason: p.not_tested_reason })),
    qa_errors: prs.filter((p) => p.verdict === 'qa-error').map((p) => p.number),
    test_backed_findings_by_severity: Object.fromEntries(SEVS.map((s) => [s, all.filter((f) => backed(f) && f.severity === s).length])),
    prs_test_backed: prs.filter((p) => (p.findings ?? []).some(backed)).map((p) => p.number),
    pending_verification: pendingSummary(prs.flatMap((p) => p.pending_verification ?? [])),
  }
}

// sources: [{ file, doc }]，按优先级从低到高
export function assembleReport({ date, sources, order = null, now = new Date() }) {
  if (!sources.length) throw new Error('assemble: no sources')
  const byPr = new Map()
  for (const s of sources) for (const pr of s.doc.prs ?? []) byPr.set(pr.number, { pr, src: s })
  const numbers = order ?? [...byPr.keys()]
  const missing = numbers.filter((n) => !byPr.has(n))
  if (missing.length) throw new Error(`assemble: no entry for PR ${missing.join(', ')}`)
  const prs = numbers.map((n) => byPr.get(n).pr)
  const used = sources.filter((s) => numbers.some((n) => byPr.get(n).src === s))
  // 只聚合仍被采用的条目对应的问题；不带 pr 的全局问题来自每个被采用的来源
  const keep = (s, item) => item.pr == null || byPr.get(item.pr)?.src === s
  const tag = (s, list) => (list ?? []).filter((x) => keep(s, x))
  const health = {
    status: used.map((s) => s.doc.system_health?.status ?? 'ok').reduce((a, b) => (STATUS_RANK[b] > STATUS_RANK[a] ? b : a), 'ok'),
    qa_tool_failures: used.flatMap((s) => tag(s, s.doc.system_health?.qa_tool_failures)),
    env_problems: used.flatMap((s) => tag(s, s.doc.system_health?.env_problems)),
    llm_errors: used.flatMap((s) => tag(s, s.doc.system_health?.llm_errors)),
    environment: used.at(-1).doc.system_health?.environment ?? null,
    discovery: used.find((s) => s.doc.system_health?.discovery)?.doc.system_health.discovery ?? null,
    notes: [...new Set(used.flatMap((s) => s.doc.system_health?.notes ?? []))],
    heartbeat: {
      sources: used.map((s) => ({ file: s.file, run_started_at: s.doc.system_health?.heartbeat?.run_started_at ?? null, run_finished_at: s.doc.system_health?.heartbeat?.run_finished_at ?? null, duration_s: s.doc.system_health?.heartbeat?.duration_s ?? null })),
      prs: numbers.flatMap((n) => { const { src } = byPr.get(n); return (src.doc.system_health?.heartbeat?.prs ?? []).filter((b) => b.number === n) }),
    },
    complete: used.every((s) => s.doc.system_health?.complete !== false) && !prs.some((p) => p.verdict === 'qa-error' || ['pr-timeout', 'deadline'].includes(p.not_tested_code)),
    all_discovered_tested: !prs.some((p) => p.verdict === 'not-tested'),
  }
  if (health.status === 'ok' && (health.qa_tool_failures.length || health.env_problems.length || health.llm_errors.length)) health.status = 'degraded'
  if (prs.some((p) => p.verdict === 'qa-error')) health.status = 'failed'
  const base = used[0].doc
  return {
    schema_version: '1.0', tool: base.tool ?? 'panoptes (argus + ming-qa)', date, timezone: 'Asia/Shanghai', repo: base.repo,
    generated_at: now.toISOString(),
    run: { kind: 'assembled', id: randomUUID(), started_at: now.toISOString(), finished_at: now.toISOString(), started_at_shanghai: toShanghaiIso(now), finished_at_shanghai: toShanghaiIso(now), duration_s: 0,
      args: { date, prs: numbers }, argus_commit: used.at(-1).doc.run?.argus_commit ?? null, ming_qa_commit: used.at(-1).doc.run?.ming_qa_commit ?? null, llm_model: used.at(-1).doc.run?.llm_model ?? null, evidence_root: null },
    dimensions: base.dimensions, severity_scale: used.at(-1).doc.severity_scale ?? base.severity_scale,
    summary: summarize(prs),
    prs,
    issues: { enabled: false, reason: 'assembled report; issues are only handled by the runs themselves' },
    merged_reports: [], assembled_from: numbers.map((n) => { const { src } = byPr.get(n); return { pr: n, file: src.file, generated_at: src.doc.generated_at ?? null, run_id: src.doc.run?.id ?? null, system_health: src.doc.system_health?.status ?? null } }),
    system_health: health,
  }
}

export function assembleFiles({ date, files, out, order = null }) {
  const sources = files.map((file) => ({ file, doc: JSON.parse(readFileSync(file, 'utf8')) }))
  const doc = assembleReport({ date, sources, order })
  // 与 nightly 同一规则：已存在的报告先保留成 <名字>.vN.json，绝不就地改写
  writeReport(doc, out)
  return doc
}
