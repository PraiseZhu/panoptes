// Mivo PR 交接文件（docs/pr/<N>/review.md、qa.md，schema mivo-pr-handoff/v1）→ 结构化数据。
// 只读文本，不执行 Mivo 仓里的任何脚本。交接是作者的线索，不是结论：author_status 只作记录。
// 格式以 xindong/mivo-canvas-plugin docs/pr/_template/ 为准（2026-10-20 取自未合并的 PR #1116，模板定型后要对齐）。
export const HANDOFF_SCHEMA = 'mivo-pr-handoff/v1'
export const AUTO_LEVELS = ['unit', 'contract', 'browser-fixture']
export const MANUAL_LEVELS = ['cindy-mac', 'cindy-windows', 'paid']
const R_FIELDS = { 触发: 'trigger', 会坏成什么样: 'breaks', 现在靠什么挡住: 'guard', 怎么证明我错了: 'falsify', 测试: 'tests' }
const Q_FIELDS = { 层次: 'level', '期望(看得见)': 'expect_visible', '期望(看不见)': 'expect_hidden', 失败判据: 'fail_criteria', 反证: 'counter_proof', 自动化: 'automation' }

// 整个值只是模板占位符 <…> 时当空
const clean = (s) => { const v = String(s ?? '').trim(); return /^<[^<>]*>$/.test(v) ? '' : v }
const normLabel = (s) => s.replace(/（/g, '(').replace(/）/g, ')').replace(/\s+/g, '')

function header(text) {
  const m = /^---\r?\n([\s\S]*?)\r?\n---/.exec(text)
  if (!m) return null
  return Object.fromEntries(m[1].split(/\r?\n/).map((l) => /^(\w+)\s*:\s*(.*)$/.exec(l)).filter(Boolean).map((x) => [x[1], x[2].trim()]))
}
// ## N. 标题 → { N: body }
function sections(text) {
  const out = {}
  let cur = null
  for (const line of text.split(/\r?\n/)) {
    const m = /^##\s+(\d+)[.、]/.exec(line)
    if (m) { cur = m[1]; out[cur] = [] } else if (cur && !/^##\s/.test(line)) out[cur].push(line)
    else if (/^##\s/.test(line)) cur = null
  }
  return Object.fromEntries(Object.entries(out).map(([k, v]) => [k, v.join('\n')]))
}
// ### X1 标题 → [{ id, title, fields: { 标签: 值 } }]
function blocks(body, prefix) {
  const out = []
  for (const part of String(body ?? '').split(/^###\s+/m).slice(1)) {
    const [first, ...rest] = part.split(/\r?\n/)
    const m = new RegExp(`^(${prefix}\\d+)\\s*(.*)$`).exec(first.trim())
    if (!m) continue
    const fields = {}
    for (const l of rest) { const f = /^\s*-\s*([^：:]+?)\s*[：:]\s*(.*)$/.exec(l); if (f) fields[normLabel(f[1])] = clean(f[2]) }
    out.push({ id: m[1], title: m[2], fields })
  }
  return out
}
// markdown 表格数据行（跳过表头和分隔行）→ 单元格数组
function rows(body) {
  const lines = String(body ?? '').split(/\r?\n/).filter((l) => /^\s*\|/.test(l))
  return lines.slice(2).map((l) => l.trim().replace(/^\||\|$/g, '').split('|').map(clean))
}
const pick = (fields, map) => Object.fromEntries(Object.entries(map).map(([label, key]) => [key, fields[normLabel(label)] ?? '']))

export function parseHandoff({ review = null, qa = null } = {}) {
  if (review == null && qa == null) return { present: false, reason: 'no handoff files (docs/pr/<N>/review.md, qa.md)' }
  const texts = {}
  for (const [kind, raw] of Object.entries({ review, qa })) {
    if (raw == null) continue
    const text = String(raw).replace(/<!--[\s\S]*?-->/g, '')
    const h = header(text)
    if (!h) return { present: false, reason: `${kind}.md has no front-matter header` }
    if (h.schema !== HANDOFF_SCHEMA) return { present: false, reason: `${kind}.md schema '${h.schema ?? ''}' is not ${HANDOFF_SCHEMA}` }
    texts[kind] = text
  }
  const rv = sections(texts.review ?? ''), q = sections(texts.qa ?? '')
  const acceptance = [...String(rv['1'] ?? '').matchAll(/^\s*-\s*(A\d+)\s*[：:]\s*(.*)$/gm)].map((m) => ({ id: m[1], text: clean(m[2]) }))
  const risks = blocks(rv['3'], 'R').map((b) => ({ id: b.id, title: clean(b.title), ...pick(b.fields, R_FIELDS) }))
  const boundaries = rows(rv['4']).filter((c) => /^B\d+$/.test(c[0])).map((c) => ({ id: c[0], text: c[1] ?? '', type: c[2] ?? '' }))
  const authorStatus = Object.fromEntries(rows(q['4']).map((c) => [/^(Q\d+)/.exec(c[0] ?? '')?.[1], c[3] ?? '']).filter(([k]) => k))
  const cases = blocks(q['3'], 'Q').map((b) => {
    const cov = /[（(]\s*覆盖\s*([^）)]*)[）)]\s*$/.exec(b.title)
    const f = pick(b.fields, Q_FIELDS)
    return { id: b.id, title: clean(b.title.replace(cov?.[0] ?? '', '')), covers: cov ? cov[1].split(/[\s/、,，]+/).filter((x) => /^[RA]\d+$/.test(x)) : [],
      level: f.level, expect: [f.expect_visible, f.expect_hidden].filter(Boolean).join('；'), fail_criteria: f.fail_criteria, counter_proof: f.counter_proof, automation: f.automation,
      author_status: authorStatus[b.id] ?? null }
  })
  const left = rows(q['5']).map((c) => ({ ref: c[0] ?? '', why: c[1] ?? '', env: c[2] ?? '' }))
  return { present: true, reason: null, acceptance, risks, boundaries, cases, left }
}

// 交接用例分三路：自动补测（unit/contract/browser-fixture 的 Q + 第 5 节条目）/ 人工（cindy-*、paid）/ 其余层次不运行
export function splitHandoffCases(h) {
  const auto = [], manual = [], other = []
  for (const c of h?.cases ?? []) (AUTO_LEVELS.includes(c.level) ? auto : MANUAL_LEVELS.includes(c.level) ? manual : other).push(c)
  const ids = new Set((h?.cases ?? []).map((c) => c.id))
  for (const l of h?.left ?? []) if (l.ref && !ids.has(l.ref)) { ids.add(l.ref); auto.push({ id: l.ref, title: l.why, covers: [], level: null, expect: '', fail_criteria: '', counter_proof: '', automation: '', author_status: null, left_for_qa: true, env: l.env }) }
  return { auto, manual, other }
}

// 报告字段 prs[].handoff：{ present, reason, cases[], manual[] }。自动补测的用例先记 qa_status 'pending'，补测后 settleHandoff 定稿
export function toHandoffReport(h, number) {
  if (!h?.present) return { present: false, reason: h?.reason ?? null, cases: [], manual: [] }
  const { auto, manual, other } = splitHandoffCases(h)
  const c = (x, qa_status, reason = null) => ({ id: x.id, key: `handoff:${number}:${x.id}`, level: x.level || null, covers: x.covers, author_status: x.author_status, qa_status, confirmed: null, reason, evidence: null })
  return { present: true, reason: null,
    cases: [...auto.map((x) => c(x, 'pending')), ...manual.map((x) => c(x, 'manual')), ...other.map((x) => c(x, 'not-run', x.level ? `unsupported-level ${x.level}` : 'no-level'))],
    manual: manual.map((x) => ({ id: x.id, level: x.level, why: `${x.level} needs a real device or a paid run; not run by argus` })) }
}

// 补测结果写回：verified（当前代码通过、植入错误后失败）/ failed（只是线索，confirmed:false，经 verify-lead 复现确认后才算）/
// not-run（带 reason：quota-exhausted / no-reliable-test / ai-tests-<status> / 调用方给的 fallback，如 docs-only）
export function settleHandoff(report, ai, fallback = 'ai-tests-not-run') {
  for (const c of report?.cases ?? []) {
    if (c.qa_status !== 'pending') continue
    const tests = (ai?.tests ?? []).filter((t) => t.handoff === c.id)
    const kept = tests.find((t) => t.result === 'kept'), bad = tests.find((t) => t.result === 'fails-on-current')
    if (kept) Object.assign(c, { qa_status: 'verified', evidence: { check_id: kept.id, file: kept.file } })
    else if (bad) Object.assign(c, { qa_status: 'failed', confirmed: false, reason: 'lead only: AI test fails on the tested commit; confirm via verify-lead before treating it as a bug', evidence: { check_id: bad.id, file: bad.file, reproduced: bad.reproduced ?? null } })
    else Object.assign(c, { qa_status: 'not-run',
      reason: !ai ? fallback : ai.capped || ai.status === 'capped' ? 'quota-exhausted' : tests.length || ai.model_calls ? 'no-reliable-test' : `ai-tests-${ai.status}`,
      evidence: ai ? { ai_tests_status: ai.status, detail: ai.reason ?? null, discarded: tests.map((t) => ({ check_id: t.id, reason: t.reason })) } : null })
  }
  return report
}
