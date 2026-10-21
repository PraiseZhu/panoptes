// 适用性矩阵：ming-qa 的 29 个维度（4 执行层 / 10 用例类型 / 10 风险 / 5 判定依据）。
// 三件事分开：Jev 判断「涉及 / 拿不准」；「不适用」只来自 profile 明文；「测到没有」由代码按步骤的 covers 和真实结果计算。
import { classifyChange, globToRegExp, highRiskFiles, isTestPath, isTestsOnly } from './classify.mjs'
import { toReadableTitle } from './util.mjs'

const q = (what) => `Could this code change plausibly affect ${what}? Answer yes only if the changed files, title or description clearly touch it.`

export const DIMENSIONS = [
  // 执行层
  { key: 'unit', group: 'layer', labelZh: '单个函数和模块', questionEn: q('the behavior of individual functions or modules') },
  { key: 'integration', group: 'layer', labelZh: '模块之间的配合', questionEn: q('how several modules, a database, the file system, git or another local service work together') },
  { key: 'api', group: 'layer', labelZh: '接口', questionEn: q('an HTTP, RPC or tool interface that other programs call (routes, request or response shapes, status codes)') },
  { key: 'e2e', group: 'layer', labelZh: '真实使用流程（端到端）', questionEn: q('a complete user journey through the running app or UI, from opening it to finishing a task') },
  // 用例类型
  { key: 'functional', group: 'caseType', labelZh: '功能是否正确', questionEn: q('what a user-facing feature does') },
  { key: 'regression', group: 'caseType', labelZh: '老功能有没有被改坏', questionEn: q('existing features that already worked before this change') },
  { key: 'boundary', group: 'caseType', labelZh: '边界情况', questionEn: q('limits and edge values such as empty input, maximum sizes, zero, very long text or many items') },
  { key: 'exception', group: 'caseType', labelZh: '出错时的处理', questionEn: q('error handling: failures, timeouts, bad input, missing files or network errors') },
  { key: 'security', group: 'caseType', labelZh: '安全', questionEn: q('security: secrets, injection, unsafe input handling, authentication or sandboxing') },
  { key: 'permission', group: 'caseType', labelZh: '权限', questionEn: q('who is allowed to do what: login checks, roles, ownership or access rules') },
  { key: 'compatibility', group: 'caseType', labelZh: '兼容性', questionEn: q('support for different operating systems, browsers, runtimes, versions or older saved data') },
  { key: 'performance', group: 'caseType', labelZh: '性能', questionEn: q('speed, memory use, rendering cost, batch sizes or other performance-sensitive code') },
  { key: 'data', group: 'caseType', labelZh: '数据读写', questionEn: q('how data is stored, migrated, imported, exported or serialized') },
  { key: 'visual', group: 'caseType', labelZh: '界面长什么样', questionEn: q('what the user sees on screen: layout, styles, icons, text rendering or images') },
  // 风险
  { key: 'permission-boundary', group: 'risk', labelZh: '越权', questionEn: q('whether one user or caller could read or change something that belongs to another') },
  { key: 'money-reward-settlement', group: 'risk', labelZh: '奖励和结算', questionEn: q('money, rewards, balances, payments or settlement') },
  { key: 'state-transition', group: 'risk', labelZh: '状态切换', questionEn: q('state machines or status changes such as pending, running, done, failed, cancelled or retried') },
  { key: 'async-callback-retry', group: 'risk', labelZh: '异步、回调和重试', questionEn: q('asynchronous work, callbacks, queues, polling, timers or retry logic') },
  { key: 'data-consistency', group: 'risk', labelZh: '数据前后一致', questionEn: q('keeping data consistent across copies, caches, restarts or partial failures') },
  { key: 'negative-path', group: 'risk', labelZh: '不该成功的操作', questionEn: q('requests or actions that must be rejected or must fail safely') },
  { key: 'concurrency', group: 'risk', labelZh: '并发', questionEn: q('things running at the same time: locks, races, parallel jobs or shared resources') },
  { key: 'provably-fair', group: 'risk', labelZh: '可证明公平', questionEn: q('randomness that must be provably fair, such as draws or loot') },
  { key: 'business-rule', group: 'risk', labelZh: '业务规则', questionEn: q('product or business rules that decide what the system should do in a given situation') },
  { key: 'privacy', group: 'risk', labelZh: '隐私', questionEn: q('personal data, logs that may leak information, or what is sent to third parties') },
  // 判定依据
  { key: 'ui', group: 'oracle', labelZh: '界面上的结果', questionEn: q('something whose correctness is judged by what appears in the user interface') },
  { key: 'api-oracle', group: 'oracle', labelZh: '接口返回的结果', questionEn: q('something whose correctness is judged by an interface response') },
  { key: 'db', group: 'oracle', labelZh: '存下来的数据', questionEn: q('something whose correctness is judged by what ends up stored in a database or saved file') },
  { key: 'sideEffects', group: 'oracle', labelZh: '附带的影响', questionEn: q('side effects such as files written, messages sent, processes started or external calls made') },
  { key: 'negativeAssertions', group: 'oracle', labelZh: '不该发生的事', questionEn: q('something that must NOT happen, such as leftover files, duplicate messages or leaked data') },
]

export const DIMENSION_KEYS = new Set(DIMENSIONS.map((d) => d.key))
const LABEL = Object.fromEntries(DIMENSIONS.map((d) => [d.key, d.labelZh]))
export const toDimensionLabel = (key) => LABEL[key] ?? key

const APPLICABLE_P = 0.7
const UNRELATED_P = 0.3
const MAX_FILES = 200
const MAX_INTENT = 2000
const MAX_BODY = 1500

// 发给 Jev 的问题：profile 明写不适用的不问
export function toMatrixQuestions(profile) {
  const skip = profile.notApplicable ?? {}
  const questions = {}
  for (const d of DIMENSIONS) {
    if (skip[d.key]) continue
    questions[d.key] = { type: 'noul', instructions: d.questionEn, criteria: { true: 'The change touches this area', false: 'The change does not touch this area' } }
  }
  return questions
}

// 发给 Jev 的内容：只放标题、说明摘要、改动文件和行数，不放改动正文与日志（长内容会让 Jev 变差）
export function toMatrixState({ pr, files, profile }) {
  const sorted = [...files].sort((a, b) => (b.changes ?? 0) - (a.changes ?? 0))
  return {
    prTitle: toReadableTitle(pr?.title ?? ''),
    prDescription: String(pr?.body ?? '').slice(0, MAX_BODY),
    productIntent: String(profile.intentZh ?? '').slice(0, MAX_INTENT),
    changedFiles: sorted.slice(0, MAX_FILES).map((f) => f.filename),
    diffStat: { files: files.length, additions: files.reduce((s, f) => s + (f.additions ?? 0), 0), deletions: files.reduce((s, f) => s + (f.deletions ?? 0), 0) },
  }
}

// 路径规则给出的「一定涉及」：profile.pathHints = { "server/**": ["api"] }
export function toPathHits(profile, files) {
  const hits = new Map()
  for (const [glob, keys] of Object.entries(profile.pathHints ?? {})) {
    const re = globToRegExp(glob)
    const f = files.find((x) => re.test(x.filename))
    if (!f) continue
    for (const k of keys) if (!hits.has(k)) hits.set(k, f.filename)
  }
  return hits
}

// 文档：.md/.mdx/.rst/.txt、docs/ 下、任意层级的 LICENSE / CHANGELOG / README（可带扩展名）
const DOC_PATH_RE = /\.(md|mdx|rst|txt)$|(^|\/)docs\/|(^|\/)(LICENSE|LICENCE|COPYING|CHANGELOG|README)(\.[^/]*)?$/i
export const TESTS_ONLY_APPLICABLE = Object.freeze(['unit', 'regression'])

// 分诊结论：每个维度 applicable / not_applicable / looks_unrelated / undetermined
// handoff（可选）：parseHandoff 的结果。作者写的风险 R 和边界 B 只作补充上下文（context）带出去，不改任何维度判定
export function toTriage({ profile, files, jev, handoff = null }) {
  const skip = profile.notApplicable ?? {}
  const hits = toPathHits(profile, files)
  // 只改测试的 PR（#1101 只删了测试里 2 行，Jev 把 29 个维度全判不涉及）：至少单测和回归是适用的
  const testsOnly = isTestsOnly(files)
  // 改了非测试代码/脚本且自带测试（#1116：pr-handoff-gate.cjs + 4 个测试被判 N/A）：单测和回归同样适用
  const codeWithTests = !testsOnly && files.some((f) => isTestPath(f.filename))
    && files.some((f) => !isTestPath(f.filename) && !DOC_PATH_RE.test(f.filename))
  const rows = DIMENSIONS.map((d) => {
    if (skip[d.key]) return { key: d.key, verdict: 'not_applicable', source: 'profile', reasonZh: skip[d.key] }
    if (hits.has(d.key)) return { key: d.key, verdict: 'applicable', source: 'path', reasonZh: `改到了 ${hits.get(d.key)}` }
    if (testsOnly && TESTS_ONLY_APPLICABLE.includes(d.key)) return { key: d.key, verdict: 'applicable', source: 'tests-only', reasonZh: '只改了测试文件' }
    if (codeWithTests && TESTS_ONLY_APPLICABLE.includes(d.key)) return { key: d.key, verdict: 'applicable', source: 'ships-tests', reasonZh: '改了代码并自带测试' }
    if (!jev?.ok) return { key: d.key, verdict: 'undetermined', source: 'none' }
    const p = jev.answers[d.key]?.noul
    if (typeof p !== 'number') return { key: d.key, verdict: 'undetermined', source: 'none' }
    const verdict = p >= APPLICABLE_P ? 'applicable' : p <= UNRELATED_P ? 'looks_unrelated' : 'undetermined'
    return { key: d.key, verdict, source: 'jev', p }
  })
  return { status: jev?.ok ? 'ok' : 'skipped', reasonZh: jev?.ok ? null : jev?.reasonZh ?? '没做分诊', model: jev?.model ?? null, rows,
    ...(handoff?.present ? { context: { risks: handoff.risks, boundaries: handoff.boundaries } } : {}) }
}

const matchesAny = (path, globs) => globs.some((g) => globToRegExp(g).test(path))
// 步骤实际测的路径（step.exercises）：没写 = 整个仓；改动文件列表拿不到时也按碰到算（保守）
export function stepExercises(step, paths) {
  if (!step.exercises || !paths?.length) return true
  return paths.some((p) => matchesAny(p, step.exercises))
}
// 改动里有这一步自己执行的测试文件（step.runsFiles）：这一步必须跑
export const stepRunsChangedFile = (step, paths) => (paths ?? []).find((p) => matchesAny(p, step.runsFiles ?? []))

// 选步骤：基线（非 optional）步骤 B/C 级都跑；可选步骤在改动含它执行的测试文件、C 级且高风险文件落在它测的路径（exercises）里、
// 分诊没做、或覆盖的维度里有「涉及 / 拿不准」时才跑
export function toStepPlan({ profile, riskClass, triage, files = [] }) {
  const verdictOf = Object.fromEntries((triage?.rows ?? []).map((r) => [r.key, r.verdict]))
  const paths = files.map((f) => f.filename)
  const cHits = highRiskFiles(files, profile)
  const commentOnly = classifyChange(files, profile).changeKind === 'comment-only'
  const run = []
  const skipped = []
  for (const s of profile.steps) {
    if (s.kind === 'setup' || !s.optional) { run.push(s.id); continue }
    if (stepRunsChangedFile(s, paths)) { run.push(s.id); continue }
    if (commentOnly && (s.covers ?? []).includes('performance')) { skipped.push({ id: s.id, nameZh: s.nameZh, reasonZh: '只改了注释，不跑性能基准' }); continue }
    if (riskClass === 'C' && stepExercises(s, cHits)) { run.push(s.id); continue }
    if (!triage || triage.status !== 'ok') { run.push(s.id); continue }
    const wanted = (s.covers ?? []).filter((k) => ['applicable', 'undetermined'].includes(verdictOf[k]))
    if (wanted.length) run.push(s.id)
    else skipped.push({ id: s.id, nameZh: s.nameZh, reasonZh: `这次改动看起来不涉及${(s.covers ?? []).map(toDimensionLabel).join('、') || '它覆盖的方面'}` })
  }
  return { stepIds: run, skipped }
}

// 覆盖：某维度被判「涉及」，且有一步 covers 含它、该步真实跑过且没有这个 PR 带来的新失败，才算测到。
// knownOnlyStepIds：判定已确认只含合并前就有的老失败的步骤（例如单测里有几个老失败），它仍然测到了东西
// exercises：只有改动碰到步骤实际测的路径，这一步才算测到（files 不传 = 不做这层过滤）
export function toCoverage({ profile, triage, results, knownOnlyStepIds = [], files = [] }) {
  const paths = files.map((f) => f.filename)
  const knownOnly = new Set(knownOnlyStepIds)
  const aiSteps = new Set(profile.steps.filter((s) => s.aiGenerated).map((s) => s.id))
  // AI 补测只有通过当前代码和植入错误验证后才有效；合并前也失败不能替代有效性验证。
  const okSteps = new Set((results ?? []).filter((r) => r.kind !== 'setup' && !r.unavailable && !r.timedOut && r.testOutcomeStatus !== 'incomplete' && (r.ok || (knownOnly.has(r.id) && !aiSteps.has(r.id)))).map((r) => r.id))
  const coveredBy = (key) => profile.steps.filter((s) => okSteps.has(s.id) && (s.covers ?? []).includes(key) && stepExercises(s, paths)).map((s) => s.id)
  const applicable = (triage?.rows ?? []).filter((r) => r.verdict === 'applicable')
  const covered = applicable.filter((r) => coveredBy(r.key).length > 0)
  const ran = (results ?? []).filter((r) => r.kind !== 'setup' && !r.unavailable)
  return {
    applicable: applicable.map((r) => r.key),
    covered: covered.map((r) => r.key),
    missed: applicable.filter((r) => !coveredBy(r.key).length).map((r) => r.key),
    undetermined: (triage?.rows ?? []).filter((r) => r.verdict === 'undetermined').map((r) => r.key),
    undeterminedMissed: (triage?.rows ?? []).filter((r) => r.verdict === 'undetermined' && !coveredBy(r.key).length).map((r) => r.key),
    passRate: ran.length ? ran.filter((r) => r.ok).length / ran.length : null,
    coverage: applicable.length ? covered.length / applicable.length : null,
  }
}

// 报告用的维度适用性：每个维度都给出明确结论 applicable / not-applicable + 理由（不留 undetermined），
// 只要分诊成功（或维度由 profile / 路径规则决定）。Jev 概率的四段：
//   p ≥ 0.7 applicable（high）· 0.5 ≤ p < 0.7 applicable（low，偏向保守：拿不准按涉及算）·
//   0.3 < p < 0.5 not-applicable（low）· p ≤ 0.3 not-applicable（high）
// 分诊没做（没有 key）或失败 → 由 profile / 路径决定的维度照常给结论，其余 undetermined，并带原因。
export const APPLICABILITY_CUTOFF = 0.5
export function toApplicability(triage) {
  const dims = {}
  const fmt = (p) => p.toFixed(2)
  for (const d of DIMENSIONS) {
    const r = (triage?.rows ?? []).find((x) => x.key === d.key) ?? { key: d.key, verdict: 'undetermined', source: 'none' }
    let row
    if (r.source === 'profile') row = { applicability: 'not-applicable', source: 'profile', confidence: 'high', reason: `profile notApplicable: ${r.reasonZh}` }
    else if (r.source === 'tests-only') row = { applicability: 'applicable', source: 'tests-only', confidence: 'high', reason: 'tests-only change: the PR only changes test files, so unit and regression apply' }
    else if (r.source === 'path') row = { applicability: 'applicable', source: 'path', confidence: 'high', reason: `profile pathHints: ${r.reasonZh}` }
    else if (r.source === 'jev' && typeof r.p === 'number') {
      const p = r.p
      if (p >= APPLICABLE_P) row = { applicability: 'applicable', source: 'jev', confidence: 'high', p, reason: `Jev p=${fmt(p)} ≥ ${APPLICABLE_P}: the change touches ${d.key}` }
      else if (p >= APPLICABILITY_CUTOFF) row = { applicability: 'applicable', source: 'jev', confidence: 'low', p, reason: `Jev p=${fmt(p)} in [${APPLICABILITY_CUTOFF}, ${APPLICABLE_P}): leaning yes, treated as applicable (conservative)` }
      else if (p > UNRELATED_P) row = { applicability: 'not-applicable', source: 'jev', confidence: 'low', p, reason: `Jev p=${fmt(p)} in (${UNRELATED_P}, ${APPLICABILITY_CUTOFF}): leaning no` }
      else row = { applicability: 'not-applicable', source: 'jev', confidence: 'high', p, reason: `Jev p=${fmt(p)} ≤ ${UNRELATED_P}: the change does not touch ${d.key}` }
    } else row = { applicability: 'undetermined', source: 'none', confidence: null, reason: triage?.status === 'ok' ? 'Jev returned no answer for this dimension' : `triage not available: ${triage?.reasonZh ?? 'not run'}` }
    dims[d.key] = { label: d.labelZh, group: d.group, ...row }
  }
  const vals = Object.values(dims)
  return {
    status: triage?.status === 'ok' ? 'ok' : triage?.jevStatus ?? 'not-run',
    model: triage?.model ?? null,
    reason: triage?.status === 'ok' ? null : triage?.reasonZh ?? null,
    total: vals.length,
    applicable: Object.keys(dims).filter((k) => dims[k].applicability === 'applicable'),
    not_applicable: Object.keys(dims).filter((k) => dims[k].applicability === 'not-applicable'),
    undetermined: Object.keys(dims).filter((k) => dims[k].applicability === 'undetermined'),
    thresholds: { applicable_high: APPLICABLE_P, cutoff: APPLICABILITY_CUTOFF, not_applicable_high: UNRELATED_P },
    dimensions: dims,
  }
}
