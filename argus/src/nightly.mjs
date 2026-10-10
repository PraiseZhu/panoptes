// 夜间 QA（Linux / Mivo QA）：发现前一天（上海时区）的 PR → 在隔离沙箱里跑项目检查 → 失败三重对照 →
// ming-qa 代码审查 → 写 reports/YYYY-MM-DD.json。默认绝不写 GitHub；提 issue 只在 --create-issues 且
// ARGUS_ALLOW_ISSUES=1 时才可能发生。
import { mkdirSync, writeFileSync, renameSync, existsSync, readFileSync, statfsSync } from 'node:fs'
import { join, resolve, dirname, basename } from 'node:path'
import { createHash, randomUUID } from 'node:crypto'
import { hostname } from 'node:os'
import { execFileSync } from 'node:child_process'
import { loadRegistry, loadSecrets } from './config.mjs'
import { openDb } from './db.mjs'
import { listNightlyPrs, getPull, getCommit, getBranchHead, listPullFiles, shanghaiDayWindow, findIssueByMarkerLabel, createIssue } from './github.mjs'
import { classifyChange } from './classify.mjs'
import { executeOnSha, ensureMirror, mergeBase, sandboxMode, janitor } from './runner.mjs'
import { decideOutcome, failedIds, setupFailed, failureKeys, compareKey } from './decide.mjs'
import { analyzePr } from './ai.mjs'
import { evaluateJev } from './jev.mjs'
import { toMatrixQuestions, toMatrixState, toTriage, toStepPlan, toCoverage } from './matrix.mjs'
import { codeReview, toBox, BOXES, LLM_MODEL, mingQaVersion, mingQaAvailable } from './mingqa.mjs'
import { ALL_DIRS, CACHE_DIR, EVIDENCE_DIR, REPORTS_DIR, CODE_DIR } from './paths.mjs'
import { log, redact, tail, toShanghaiDay, toShanghaiIso } from './util.mjs'
import { setBudget, overBudget } from './exec.mjs'
import { coverageGate, parseAddedLines, COVERAGE_THRESHOLDS } from './coverage.mjs'

const SCHEMA_VERSION = '1.0'
const SEV_RANK = { P0: 0, P1: 1, P2: 2, P3: 3 }
const maxSev = (list) => list.reduce((m, s) => (SEV_RANK[s] < SEV_RANK[m] ? s : m), 'P3')
const sha256 = (s) => createHash('sha256').update(String(s)).digest('hex')

export function previousShanghaiDay(now = new Date()) {
  return toShanghaiDay(new Date(Date.parse(`${toShanghaiDay(now)}T00:00:00+08:00`) - 12 * 3600_000))
}

// ── 指纹 ─────────────────────────────────────────────
// fingerprint：与 PR 号、SHA、时间无关（同一问题跨 PR / 跨夜可去重）；pr_fingerprint：同一 PR 内去重。
const normalize = (s) => String(s ?? '').replace(/\x1b\[[0-9;]*m/g, '').replace(/\b[0-9a-f]{7,40}\b/g, '<sha>').replace(/\d+(\.\d+)?\s*(ms|s|m)\b/g, '<dur>').replace(/:\d+:\d+/g, ':<l>:<c>').replace(/\/tmp\/[^\s'"]+|\/workspace\/[^\s'"]+/g, '<path>').replace(/\s+/g, ' ').trim().toLowerCase()
export function fingerprintOf(repo, parts) { return `mq1-${sha256([repo, ...parts.map(normalize)].join('|')).slice(0, 20)}` }
const prFingerprint = (fp, n) => `${fp}-pr${n}`

function wholeStepSignature(r) {
  const lines = String(r?.tail ?? '').split('\n').filter((l) => /error|fail|✗|×|assert/i.test(l)).slice(-5)
  return lines.map(normalize).join('|') || `exit:${r?.exitCode}`
}

// ── 单个失败键的归因（逐项，不依赖整体判定）────────────────
function classifyKey(key, { first, rerun, base, latest, latestIsSame }) {
  if (!rerun) return { status: 'unverified', why: 'no rerun available' }
  const vsRerun = compareKey(key, rerun, first)
  if (vsRerun === 'absent') return { status: 'flaky', why: 'unstable: failed once, passed on a clean rerun of the same commit; not attributed to this PR' }
  if (vsRerun === 'unknown') return { status: 'unverified', why: 'rerun result incomplete or failure type not comparable' }
  if (!base) return { status: 'unverified', why: 'reproduced, but the base revision could not be run for comparison' }
  const vsBase = compareKey(key, base, first)
  if (vsBase === 'same') return { status: 'pre-existing', why: 'also fails on the base revision' }
  if (vsBase === 'unknown') return { status: 'unverified', why: 'reproduced; base comparison incomplete' }
  if (!latestIsSame && latest) {
    const vsLatest = compareKey(key, latest, first)
    if (vsLatest === 'absent') return { status: 'fixed-later', why: 'introduced by this PR but already passing on latest main' }
    if (vsLatest === 'unknown') return { status: 'introduced', why: 'fails here and passes on base; latest-main comparison incomplete' }
  }
  return { status: 'introduced', why: 'fails reproducibly on this PR and passes on the base revision' }
}

// 严重度规则（文档见 README-linux.md）：
//  introduced：产品类检查（构建/类型/单测/接口/E2E）→ P1；若是已合并 PR 且最新主干仍失败 → P0（主干已坏）；质量守卫类 → P2
//  unverified / setup 失败 / flaky（不稳定：首跑失败、同一提交干净重跑通过）→ P2；pre-existing / fixed-later → P3；
//  试运行检查（trialOnly）最高 P2。flaky 与 PR 无关，test_backed=false（不是测试佐证的缺陷，不触发通知 / issue）
export function severityFor({ status, step, merged }) {
  let sev
  if (status === 'introduced') sev = step.kind === 'quality' ? 'P2' : merged ? 'P0' : 'P1'
  else if (['unverified', 'setup-failed', 'timeout', 'flaky'].includes(status)) sev = 'P2'
  else sev = 'P3'
  if (step.trialOnly && SEV_RANK[sev] < SEV_RANK.P2) sev = 'P2'
  return sev
}

function evidenceTail(r, name) {
  const lines = String(r?.tail ?? '').replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '').split('\n')
  let idx = name ? lines.findIndex((l) => l.includes(String(name).split(' > ').pop())) : -1
  const slice = idx >= 0 ? lines.slice(Math.max(0, idx - 3), idx + 25) : lines.slice(-30)
  return redact(slice.join('\n')).slice(-3000)
}

// ── 单个 PR ───────────────────────────────────────────
export async function processPr({ repo, project, number, discovery, secrets, db, health, evidenceRoot, opts }) {
  const startedAt = Date.now()
  const baseProfile = { ...project.profileData, displayName: project.displayName }
  const pr = await getPull(repo, number)
  const files = await listPullFiles(repo, number)
  const merged = Boolean(pr.merged_at)
  const out = {
    number, title: pr.title, author: pr.user?.login ?? null, url: pr.html_url,
    state: merged ? 'merged' : pr.state, draft: Boolean(pr.draft), discovery_reason: discovery,
    created_at: pr.created_at, updated_at: pr.updated_at, merged_at: pr.merged_at,
    base_ref: pr.base?.ref, head_sha: pr.head?.sha, merge_sha: pr.merge_commit_sha ?? null, tested_sha: null, base_sha: null,
    changed_files: files.length, additions: pr.additions, deletions: pr.deletions,
    risk_class: null, severity: 'P3', verdict: null, outcome: null, outcome_reason: null,
    checks: [], failed_checks: [], code_review: null, findings: [], ai_summary: null, coverage: null, coverage_gate: null,
    evidence_dir: null, duration_s: null,
    // tested_at：这个 PR 得出结论的时间（上海时区 ISO 8601，带 +08:00）；test_started_at：开始处理的时间
    test_started_at: toShanghaiIso(new Date(startedAt)), tested_at: null, reviewed_sha: null, review_base_sha: null,
  }
  // 合并即测（on-merge）已经在同一个 merge commit 上测过：直接引用它的结果，不重测
  if (merged && opts.reuseMerged !== false) {
    const reused = loadMergedResult(number, pr.merge_commit_sha, { mergedDir: opts.mergedDir, selfOut: opts.out, requireReview: !opts.noReview })
    if (reused) {
      health.notes.push(`PR #${number}: reused on-merge result ${reused.file} (same merge commit ${pr.merge_commit_sha.slice(0, 12)}), not re-tested`)
      return { ...reused.pr, tested_at: reused.pr.tested_at ?? toShanghaiIso(reused.generatedAt ?? NaN), discovery_reason: discovery, reused: true, reused_from: reused.file, reused_report_generated_at: reused.generatedAt, reused_report_system_health: reused.systemHealth }
    }
  }
  const evDir = join(evidenceRoot, `pr-${number}`)
  mkdirSync(evDir, { recursive: true })
  out.evidence_dir = evDir
  if (!merged && pr.state !== 'open') { out.tested = false; out.verdict = 'skipped'; out.outcome_reason = 'PR is closed without merge'; out.tested_at = toShanghaiIso(); return out }

  const { riskClass, reasonZh: classReason } = classifyChange(files, baseProfile)
  out.risk_class = riskClass
  out.risk_reason = classReason
  if (riskClass === 'A') {
    out.skipped_steps = baseProfile.steps.filter((x) => x.kind !== 'setup').map((x) => ({ id: x.id, nameZh: x.nameZh, reason: 'docs-only change: no checks run', dimension: toBox(x, baseProfile) }))
    Object.assign(out, dimensionsOf([])); out.tested = false; out.verdict = 'docs-only'; out.outcome = 'docs_only'; out.outcome_reason = classReason
    // 文档改动也做代码审查（同一提交规则：merged → merge-base..merge commit；open → merge-base..head）；不跑测试，覆盖率门禁不适用
    out.coverage_gate = DOCS_ONLY_COVERAGE_GATE()
    if (!opts.noReview) {
      const sha = merged ? pr.merge_commit_sha : pr.head.sha
      const extraRefspecs = merged ? [] : [`+refs/pull/${number}/head:refs/pull/${number}/head`]
      const baseSha = await reviewBaseSha({ repo, pr, sha, merged, extraRefspecs })
      out.base_sha = baseSha
      const prDiff = await diffForReview({ repo, sha, baseSha, extraRefspecs })
      await runCodeReview({ out, repo, pr, files, prDiff, sha, baseSha, results: [], profile: baseProfile, evDir, secrets, health, number })
      out.sha_consistent = out.reviewed_sha == null || out.reviewed_sha === sha
      if (!out.sha_consistent) health.notes.push(`PR #${number}: docs-only review looked at ${String(out.reviewed_sha).slice(0, 12)} instead of ${String(sha).slice(0, 12)} (${out.review_diff_source})`)
    }
    out.severity = out.findings.length ? maxSev(out.findings.map((f) => f.severity)) : 'P3'
    out.duration_s = Math.round((Date.now() - startedAt) / 1000); out.tested_at = toShanghaiIso()
    return out
  }

  // 被测版本：已合并 → merge commit；开放 → PR head（从 refs/pull/N/head 取，支持 fork）
  const sha = merged ? pr.merge_commit_sha : pr.head.sha
  const extraRefspecs = merged ? [] : [`+refs/pull/${number}/head:refs/pull/${number}/head`]
  out.tested_sha = sha

  // 分诊（Jev，可选）；没有 key 时所有可选检查都跑
  let jev = { ok: false, reasonZh: '没有配置 Jev 的 key（ARGUS_TYPESAFE_API_KEY），可选检查全部执行' }
  if (secrets.ARGUS_TYPESAFE_API_KEY) jev = await evaluateJev({ state: toMatrixState({ pr, files, profile: baseProfile }), questions: toMatrixQuestions(baseProfile), secrets })
  const triage = toTriage({ profile: baseProfile, files, jev })
  if (secrets.ARGUS_TYPESAFE_API_KEY && !jev.ok) health.llm_errors.push({ pr: number, stage: 'triage(jev)', message: jev.reasonZh })
  const plan = toStepPlan({ profile: baseProfile, riskClass, triage })
  for (const sk of plan.skipped) sk.reason ??= 'triage: change does not touch what this optional step covers'
  if (opts.skipSteps?.length) {
    for (const id of plan.stepIds.filter((x) => opts.skipSteps.includes(x))) {
      const st = baseProfile.steps.find((x) => x.id === id)
      plan.skipped.push({ id, nameZh: st?.nameZh, reason: 'skipped by --skip-step', reasonZh: '运行参数 --skip-step 跳过' })
    }
    plan.stepIds = plan.stepIds.filter((id) => !opts.skipSteps.includes(id))
  }
  const profile = baseProfile
  const common = { repo, jobId: `n${number}`, profile, extraRefspecs, keepTo: evDir }

  // 覆盖率轮次：独立检出、只跑单测（带 --coverage），与首轮并行；它的通过 / 失败不参与任何判定
  const covStep = profile.steps.find((x) => x.coverage && plan.stepIds.includes(x.id))
  const covRun = covStep && process.env.ARGUS_COVERAGE !== '0'
    ? ensureMirror(repo, sha, { extraRefspecs }).then(() => executeOnSha({ ...common, label: 'cov', sha, stepIds: [covStep.id] })).catch((err) => ({ error: err }))
    : Promise.resolve(null)
  let first = await executeOnSha({ ...common, label: 'h1', sha, stepIds: plan.stepIds })
  if (setupFailed(first)) {
    health.qa_tool_failures.push({ pr: number, stage: 'setup', message: 'dependency install failed, retrying once', detail: tail(redact(first.find((r) => r.kind === 'setup')?.tail ?? ''), 600) })
    first = await executeOnSha({ ...common, label: 'h1r', sha, stepIds: plan.stepIds })
  }
  // 对照版本：已合并 → merge commit 的第一个父提交；开放 → PR head 与目标分支的 merge-base
  const baseSha = await reviewBaseSha({ repo, pr, sha, merged, extraRefspecs })
  out.base_sha = baseSha
  // 同一份 diff（base..tested）同时给覆盖率门禁和代码审查用：审查的就是被测的那个提交
  const prDiff = await diffForReview({ repo, sha, baseSha, extraRefspecs })
  const cov = await covRun
  out.coverage_gate = computeCoverageGate({ first: Array.isArray(cov) ? cov : [], prDiff, covError: cov?.error })

  let rerun = null, base = null, latest = null, latestIsSame = true
  const failed = failedIds(first)
  if (!setupFailed(first) && failed.length) {
    rerun = await executeOnSha({ ...common, label: 'h2', sha, stepIds: failed })
    const still = failed.filter((id) => failedIds(rerun).includes(id))
    if (still.length && baseSha) base = await executeOnSha({ ...common, label: 'b', sha: baseSha, stepIds: still })
    if (merged) {
      const prelim = decideOutcome({ first, rerun, base, latest: null, baseKnown: Boolean(baseSha), latestIsSame: true })
      if (prelim.outcome === 'defect') {
        const head = await getBranchHead(repo, pr.base.ref)
        latestIsSame = Boolean(head) && head === sha
        if (head && !latestIsSame) latest = await executeOnSha({ ...common, label: 'l', sha: head, stepIds: prelim.failedSteps })
        out.latest_main_sha = head
      }
    }
  }
  const decision = decideOutcome({ first, rerun, base, latest, baseKnown: Boolean(baseSha), latestIsSame })
  out.outcome = decision.outcome
  out.outcome_reason = decision.reasonZh
  out.coverage = toCoverage({ profile, triage, results: first })
  // 计划要跑却没有结果的步骤（安装失败后中止、时间预算用完等）也列进 skipped_steps
  const ranIds = new Set(first.map((r) => r.id))
  for (const id of plan.stepIds) {
    if (ranIds.has(id)) continue
    const st = profile.steps.find((x) => x.id === id)
    if (st?.kind === 'setup') continue
    plan.skipped.push({ id, nameZh: st?.nameZh, reason: setupFailed(first) ? 'not run: dependency install failed' : 'not run: run stopped before this step (time budget / shutdown)', reasonZh: '没跑到' })
  }
  out.skipped_steps = plan.skipped.map((sk) => ({ ...sk, dimension: toBox(profile.steps.find((x) => x.id === sk.id) ?? { id: sk.id }, profile) }))

  // 检查清单（每步带维度）
  const stepById = Object.fromEntries(profile.steps.map((s) => [s.id, s]))
  for (const r of first) {
    const step = stepById[r.id] ?? { id: r.id, kind: r.kind }
    out.checks.push({
      id: r.id, name: r.nameZh, kind: r.kind, dimension: toBox(step, profile), qa_dimensions: step.covers ?? [],
      status: r.unavailable ? 'unavailable' : r.timedOut ? 'timeout' : r.ok ? 'pass' : 'fail',
      exit_code: r.exitCode, duration_ms: r.durationMs, failure_count: r.failures?.length ?? 0,
      ...(r.unavailable ? { unavailable_reason: r.unavailableReasonZh } : {}),
    })
    if (r.unavailable) health.qa_tool_failures.push({ pr: number, stage: r.id, dimension: toBox(step, profile), message: `check could not run: ${r.unavailableReasonZh}` })
    if (r.timedOut) health.qa_tool_failures.push({ pr: number, stage: r.id, dimension: toBox(step, profile), message: `check timed out after ${Math.round(r.durationMs / 60000)} min` })
  }

  // 失败检查 → findings
  if (setupFailed(first)) {
    const r = first.find((x) => x.kind === 'setup' && !x.ok)
    const fp = fingerprintOf(repo, ['setup', r.id, wholeStepSignature(r)])
    out.failed_checks.push({ check_id: r.id, check_name: r.nameZh, dimension: 'white-box', test: null, status: 'setup-failed', severity: 'P2', attribution: 'dependency install failed twice; no checks could run', fingerprint: fp, pr_fingerprint: prFingerprint(fp, number), evidence: { tested_sha: sha, exit_code: r.exitCode, timed_out: r.timedOut, log_tail: evidenceTail(r), log_file: join(evDir, 'h1r/logs', `${r.id}.log`) } })
    health.env_problems.push({ pr: number, message: `setup step '${r.id}' failed twice (exit ${r.exitCode})` })
  }
  for (const key of failureKeys(first)) {
    const stepId = key.slice(0, key.indexOf('::'))
    const test = key.slice(key.indexOf('::') + 2)
    const r = first.find((x) => x.id === stepId)
    const step = stepById[stepId] ?? { id: stepId, kind: r?.kind }
    let { status, why } = classifyKey(key, { first, rerun, base, latest, latestIsSame })
    if (r?.timedOut) { status = 'timeout'; why = 'check timed out; no comparable failure evidence' }
    const named = test !== '*'
    const fp = fingerprintOf(repo, ['check', stepId, named ? test : wholeStepSignature(r)])
    const type = r?.failureDetails?.find((f) => f.name === test)?.type ?? null
    const compareOn = (results) => { const x = results?.find((y) => y.id === stepId); return x ? (x.unavailable ? 'unavailable' : x.ok ? 'pass' : (named ? (x.failures?.includes(test) ? 'fail' : 'not-failing') : 'fail')) : 'not-run' }
    out.failed_checks.push({
      check_id: stepId, check_name: r?.nameZh, dimension: toBox(step, profile), qa_dimensions: step.covers ?? [],
      test: named ? test : null, failure_type: type, status, flaky: status === 'flaky', related_to_pr: status === 'flaky' ? false : undefined,
      severity: severityFor({ status, step, merged }), attribution: why,
      fingerprint: fp, pr_fingerprint: prFingerprint(fp, number),
      evidence: {
        tested_sha: sha, base_sha: baseSha, exit_code: r?.exitCode, timed_out: Boolean(r?.timedOut),
        first_run: 'fail', rerun: compareOn(rerun), base_run: compareOn(base), ...(latest ? { latest_main_run: compareOn(latest) } : {}),
        command: (step.cmd ?? []).join(' '), log_tail: evidenceTail(r, named ? test : null), log_file: join(evDir, 'h1/logs', `${stepId}.log`),
      },
    })
  }

  // ming-qa 代码审查（白盒）
  if (!opts.noReview) await runCodeReview({ out, repo, pr, files, prDiff, sha, baseSha, results: first, profile, evDir, secrets, health, number })
  // 人话摘要（Argus 原有 AI 解读，可选）
  if (!opts.noReview) {
    const ai = await analyzePr({ db, secrets, riskClass, pr, files, profile, results: first, outcome: decision.outcome })
    if (ai.status === 'ok') out.ai_summary = { summary: ai.summaryZh, risks: ai.risksZh, suspect: ai.suspectZh || null, failure_explanation: ai.failureZh || null }
    else if (ai.status === 'error') health.llm_errors.push({ pr: number, stage: 'pr-summary', model: LLM_MODEL, message: `${ai.reasonZh} ${ai.detail ?? ''}`.trim() })
  }

  // 覆盖率门禁不达标 → P2 建议复查（source: coverage-gate，不是失败的检查，按规则永远不会单独开 issue）
  const gate = out.coverage_gate
  if (gate?.status === 'fail') {
    const fp = fingerprintOf(repo, ['coverage-gate', gate.step, gate.failed.join(',')])
    out.findings.push({
      source: 'coverage-gate', test_backed: false, check_id: 'coverage-gate', dimension: 'white-box', severity: 'P2',
      title: `coverage below gate: ${gate.failed.map((k) => `${k} ${gate.values[k]}% < ${gate.thresholds[k]}%`).join(', ')}`,
      fingerprint: fp, pr_fingerprint: prFingerprint(fp, number),
      evidence: { tested_sha: sha, base_sha: baseSha, values: gate.values, thresholds: gate.thresholds, new_code_files: (gate.new_code?.files ?? []).slice(0, 20) },
    })
  }
  out.findings = [...out.failed_checks.map((c) => ({ source: 'check', test_backed: checkIsTestBacked(c), ...c })), ...out.findings]
  out.severity = out.findings.length ? maxSev(out.findings.map((f) => f.severity)) : 'P3'
  Object.assign(out, dimensionsOf(out.checks))
  out.tested = true
  out.verdict = verdictOf(out)
  out.duration_s = Math.round((Date.now() - startedAt) / 1000)
  out.tested_at = toShanghaiIso()
  // 测试与审查必须是同一个提交
  out.sha_consistent = out.reviewed_sha == null || out.reviewed_sha === out.tested_sha
  if (!out.sha_consistent) health.notes.push(`PR #${number}: tests ran on ${String(out.tested_sha).slice(0, 12)} but code review looked at ${String(out.reviewed_sha).slice(0, 12)} (${out.review_diff_source})`)
  return out
}

// 文档改动（risk A）的覆盖率门禁：没有单测运行，不是失败也不是 unavailable
export const DOCS_ONLY_COVERAGE_GATE = () => ({ status: 'not-applicable', reason: 'docs-only change' })

// 对照版本：已合并 → merge commit 的第一个父提交；开放 → PR head 与目标分支的 merge-base
async function reviewBaseSha({ repo, pr, sha, merged, extraRefspecs }) {
  if (merged) return (await getCommit(repo, sha))?.parents?.[0]?.sha ?? null
  const mirror = await ensureMirror(repo, sha, { extraRefspecs })
  return mergeBase(mirror, sha, pr.base.sha)
}

// ming-qa 代码审查（白盒）。审查文件来自本地镜像 base..tested 的 diff（已合并：merge-base..merge commit；开放：merge-base..head）。
// 本地 diff 拿不到时才退回 GitHub 的 PR 文件列表（那是 PR head 的 diff），并在 system_health.notes 里说明。
async function runCodeReview({ out, repo, pr, files, prDiff, sha, baseSha, results, profile, evDir, secrets, health, number }) {
  const reviewFiles = prDiff.files ?? files
  out.reviewed_sha = prDiff.files ? sha : (pr.head?.sha ?? null)
  out.review_base_sha = prDiff.files ? baseSha : (pr.base?.sha ?? null)
  out.review_diff_source = prDiff.files ? 'local git diff base_sha..tested_sha' : `github pr files (local diff unavailable: ${prDiff.error})`
  const cr = await codeReview({ secrets, pr, files: reviewFiles, results, profile, outDir: join(evDir, 'code-review') })
  if (cr.status !== 'ok') {
    health.llm_errors.push({ pr: number, stage: 'code-review', model: LLM_MODEL, ...cr.error })
    out.code_review = { status: cr.status, error: cr.error }
    return
  }
  if (cr.gateError) health.qa_tool_failures.push({ pr: number, stage: 'ming-qa assert-code-review', message: cr.gateError.message })
  out.code_review = { status: cr.doc.status, summary: cr.doc.summary, gate: cr.gate, model: cr.model, tokens: cr.tokens, findings_count: cr.doc.findings.length, residual_risks: cr.doc.residualRisks, file: cr.reviewPath }
  for (const f of cr.doc.findings) {
    if (f.verdict === 'REJECTED') continue
    // LLM 单独给出的结论最高记 P1：P0 需要真实执行的失败作证（原始等级保留在 llm_severity）
    const severity = f.severity === 'P0' ? 'P1' : f.severity
    const fp = fingerprintOf(repo, ['code-review', f.file, f.symbol || '', f.category])
    out.findings.push({
      source: 'code-review', test_backed: false, check_id: 'code-review', dimension: 'white-box', severity, llm_severity: f.severity, verdict: f.verdict,
      category: f.category, title: f.title, file: f.file, line: f.line, symbol: f.symbol,
      fingerprint: fp, pr_fingerprint: prFingerprint(fp, number),
      evidence: { evidence: f.evidence, failure_scenario: f.failureScenario, recommendation: f.recommendation, reviewed_sha: out.reviewed_sha },
    })
  }
}

// base..tested 的完整 diff：一份文本，解析出审查用的文件列表（GitHub files 形状）和覆盖率用的新增行
async function diffForReview({ repo, sha, baseSha, extraRefspecs }) {
  if (!baseSha) return { text: null, files: null, error: 'base revision unknown' }
  try {
    const mirror = await ensureMirror(repo, sha, { extraRefspecs })
    // run() 只保留输出尾部，这里用同步调用拿全量
    const text = execFileSync('git', ['-C', mirror, 'diff', '--no-color', '--no-ext-diff', '--no-renames', '-U3', baseSha, sha], { encoding: 'utf8', maxBuffer: 256 * 1024 * 1024, timeout: 120_000 })
    return { text, files: splitDiffFiles(text), error: null }
  } catch (err) { return { text: null, files: null, error: String(err.message).slice(0, 200) } }
}

// `git diff` 文本 → [{ filename, status, additions, deletions, patch }]（与 GitHub PR files 接口同形）
export function splitDiffFiles(text) {
  const files = []
  let cur = null
  for (const line of String(text ?? '').split('\n')) {
    const m = line.match(/^diff --git a\/(.+) b\/(.+)$/)
    if (m) { cur = { filename: m[2], status: 'modified', additions: 0, deletions: 0, patch: null, _hunks: [] }; files.push(cur); continue }
    if (!cur) continue
    if (!cur._hunks.length) {
      if (line.startsWith('new file mode')) cur.status = 'added'
      else if (line.startsWith('deleted file mode')) cur.status = 'removed'
      else if (line.startsWith('+++ /dev/null')) cur.status = 'removed'
      else if (line.startsWith('+++ b/')) cur.filename = line.slice(6)
      if (!line.startsWith('@@')) continue
    }
    cur._hunks.push(line)
    if (line.startsWith('+') && !line.startsWith('+++')) cur.additions++
    else if (line.startsWith('-') && !line.startsWith('---')) cur.deletions++
  }
  for (const f of files) { f.patch = f._hunks.length ? f._hunks.join('\n') : null; delete f._hunks }
  return files
}

// 覆盖率门禁：取首轮里带 coverage 的步骤（单测），与 base..tested 的 diff 新增行求交
function computeCoverageGate({ first, prDiff, covError = null }) {
  const r = first.find((x) => x.coverage)
  if (!r) {
    const setup = first.find((x) => x.kind === 'setup' && !x.ok)
    return coverageGate({ coverage: { status: 'unavailable', reason: covError ? `coverage run failed: ${redact(String(covError.message)).slice(0, 200)}` : setup ? 'coverage run: dependency install failed' : 'unit step not planned or coverage disabled' } })
  }
  // 覆盖率轮次里单测本身的结果只作说明（插桩可能让个别用例失败），不影响判定
  const underCoverage = { exit_code: r.exitCode, timed_out: Boolean(r.timedOut), failed_tests: (r.failures ?? []).length, note: 'unit run under v8 coverage; its pass/fail is informational only and never used for the verdict' }
  if (r.coverage.status !== 'ok') return { ...coverageGate({ coverage: r.coverage, stepId: r.id }), unit_run_under_coverage: underCoverage }
  const diffError = prDiff.text == null ? `new-code coverage not evaluated: ${prDiff.error}` : null
  return { ...coverageGate({ coverage: r.coverage, added: diffError ? null : parseAddedLines(prDiff.text), stepId: r.id, diffError }), unit_run_under_coverage: underCoverage }
}

// 三个维度各自是否真的跑了检查（pass / fail 才算跑了；unavailable / timeout 不算）
export function dimensionsOf(checks) {
  const run = Object.fromEntries(Object.keys(BOXES).map((b) => [b, []]))
  for (const c of checks ?? []) if (['pass', 'fail'].includes(c.status) && c.kind !== 'setup' && run[c.dimension]) run[c.dimension].push(c.id)
  return { dimensions_run: run, dimensions_missing: Object.keys(run).filter((b) => !run[b].length) }
}

// clean 只能在：没有发现、所有检查都跑成、三个维度都真的跑过时给出
export function verdictOf(pr) {
  if (pr.outcome === 'infra_failed') return 'qa-env-problem'
  if (pr.findings.some((f) => ['P0', 'P1'].includes(f.severity))) return 'needs-attention'
  if (pr.findings.some((f) => f.severity === 'P2')) return 'review-suggested'
  if (pr.checks.some((c) => c.status === 'unavailable' || c.status === 'timeout')) return 'partially-verified'
  if ((pr.dimensions_missing ?? []).length) return 'partially-verified'
  return 'clean'
}

// ── 系统健康 ──────────────────────────────────────────
// Linux browser for Mivo's journey: the local wrapper (scripts/chromium-linux.sh → /usr/local/bin/chromium) when installed,
// otherwise Debian's launcher. Must match the profile's linux BD2_CHROME.
const LINUX_CHROMIUM = existsSync('/usr/local/bin/chromium') ? '/usr/local/bin/chromium' : '/usr/bin/chromium'

function envSnapshot() {
  const ver = (cmd, args) => { try { return execFileSync(cmd, args, { encoding: 'utf8', timeout: 15_000 }).trim().split('\n')[0] } catch { return null } }
  let free = null
  try { const s = statfsSync(CACHE_DIR); free = Math.round((s.bavail * s.bsize) / 1024 ** 3) } catch { /* ignore */ }
  return {
    host: hostname(), platform: process.platform, node: process.version,
    sandbox: sandboxMode({}), bwrap: ver('bwrap', ['--version']), chromium: ver(LINUX_CHROMIUM, ['--version']), chromium_path: LINUX_CHROMIUM,
    playwright: (() => { try { return JSON.parse(readFileSync(join(CODE_DIR, 'node_modules/playwright/package.json'), 'utf8')).version } catch { return null } })(),
    python: ver(process.env.ARGUS_PYTHON ?? 'python3', ['--version']), disk_free_gb: free,
  }
}


// ── 合并即测结果的复用 / 输出路径 ─────────────────────────
export const mergedReportsDir = () => process.env.ARGUS_MERGED_REPORTS_DIR ?? join(REPORTS_DIR, 'merged')
export const mergedReportPath = (n, dir = mergedReportsDir()) => join(dir, `PR-${n}.json`)
const REUSABLE_VERDICTS = new Set(['clean', 'review-suggested', 'needs-attention', 'partially-verified', 'docs-only'])

// 只复用「同一个 merge commit、真实跑完」的结果；PR 号对不上、测的不是这个提交、或上次自身出错的都不复用
export function loadMergedResult(number, mergeSha, { mergedDir, selfOut, requireReview = false } = {}) {
  const file = mergedReportPath(number, mergedDir)
  if (selfOut && resolve(selfOut) === resolve(file)) return null
  if (!mergeSha || !existsSync(file)) return null
  let doc
  try { doc = JSON.parse(readFileSync(file, 'utf8')) } catch { return null }
  const pr = (doc.prs ?? []).find((p) => p.number === number)
  if (!pr || pr.state !== 'merged' || !REUSABLE_VERDICTS.has(pr.verdict)) return null
  // 旧的 docs-only 结果没有代码审查：审查开着时重做（文档改动也要审查同一个 merge commit）
  if (requireReview && pr.verdict === 'docs-only' && !pr.code_review) return null
  const tested = pr.verdict === 'docs-only' ? (doc.merged_sha ?? pr.merge_sha ?? null) : pr.tested_sha
  if (!tested || tested !== mergeSha) return null
  return { file, pr, generatedAt: doc.generated_at ?? null, systemHealth: doc.system_health?.status ?? null }
}

// --out：显式输出路径。为了不覆盖每日报告，拒绝写到 REPORTS_DIR/YYYY-MM-DD.json 形式的文件
export function resolveOutPath(out, day) {
  if (!out) return join(REPORTS_DIR, `${day}.json`)
  const abs = resolve(out)
  if (resolve(dirname(abs)) === resolve(REPORTS_DIR) && /^\d{4}-\d{2}-\d{2}\.json$/.test(basename(abs))) throw new Error(`--out 不能指向每日报告文件：${abs}`)
  return abs
}

// ── 报告 ──────────────────────────────────────────────
function writeReport(report, file) {
  mkdirSync(dirname(file), { recursive: true })
  const tmp = `${file}.tmp-${process.pid}`
  writeFileSync(tmp, JSON.stringify(report, null, 2) + '\n')
  renameSync(tmp, file)
  return file
}

// ── 提 issue（默认关闭；本次部署不启用）─────────────────
// 只有 --create-issues 且环境变量 ARGUS_ALLOW_ISSUES=1 同时满足才会执行。
// 目标固定为私有仓 PraiseZhu/pr-qa-agent-deploy（绝不往被测仓 xindong/mivo-canvas-plugin 提），
// labels: qa-auto + cindy-dispatch；按 fingerprint 去重（含已关闭的 issue），已存在则跳过，不评论不重开。
// 正文带 `<!-- mivo-qa-fingerprint: … -->` 标记和一个 ```json mivo-qa-finding``` 证据块，供下游 lifeline/doctor.mjs 解析。
export const ISSUE_REPO = 'PraiseZhu/pr-qa-agent-deploy'
export const ISSUE_LABELS = ['qa-auto', 'cindy-dispatch']
export const fingerprintMarker = (fp) => `<!-- mivo-qa-fingerprint: ${fp} -->`

export function buildIssuePayload(report, pr, f) {
  const finding = {
    schema: 'mivo-qa-finding/v1', fingerprint: f.fingerprint, pr_fingerprint: f.pr_fingerprint,
    report_date: report.date, report_file: `reports/${report.date}.json`, run_id: report.run?.id,
    repo: report.repo, pr: { number: pr.number, title: pr.title, author: pr.author, url: pr.url, state: pr.state, tested_sha: pr.tested_sha, base_sha: pr.base_sha },
    source: f.source, check_id: f.check_id, check_name: f.check_name ?? null, dimension: f.dimension, qa_dimensions: f.qa_dimensions ?? [],
    severity: f.severity, status: f.status ?? null, attribution: f.attribution ?? null, test: f.test ?? null, failure_type: f.failure_type ?? null,
    file: f.file ?? null, line: f.line ?? null, symbol: f.symbol ?? null, category: f.category ?? null, verdict: f.verdict ?? null,
    evidence: { ...(f.evidence ?? {}), log_tail: undefined }, evidence_dir: pr.evidence_dir,
  }
  const what = f.source === 'code-review' ? `code review: ${f.title}` : `${f.check_name ?? f.check_id}${f.test ? ` › ${f.test}` : ''}`
  const title = `[Mivo QA][${f.severity}] ${report.repo}#${pr.number} ${what}`.slice(0, 240)
  const body = [
    `Mivo QA nightly (${report.date}, Asia/Shanghai) found a **${f.severity}** problem in ${report.repo}#${pr.number} — ${pr.title} (@${pr.author}).`,
    '',
    `- PR: ${pr.url} (${pr.state}), tested ${pr.tested_sha ?? '?'} vs base ${pr.base_sha ?? '?'}`,
    `- Check: ${f.check_name ?? f.check_id} (${f.dimension})`,
    f.test ? `- Test: \`${f.test}\`` : null,
    f.status ? `- Status: ${f.status} — ${f.attribution}` : null,
    f.file ? `- Location: \`${f.file}${f.line ? `:${f.line}` : ''}\`${f.symbol ? ` (${f.symbol})` : ''}` : null,
    f.evidence?.rerun ? `- Runs: first=fail, rerun=${f.evidence.rerun}, base=${f.evidence.base_run}${f.evidence.latest_main_run ? `, latest main=${f.evidence.latest_main_run}` : ''}` : null,
    f.evidence?.evidence ? `\n**Evidence**\n\n${f.evidence.evidence}` : null,
    f.evidence?.failure_scenario ? `\n**Failure scenario**\n\n${f.evidence.failure_scenario}` : null,
    f.evidence?.recommendation ? `\n**Recommendation**\n\n${f.evidence.recommendation}` : null,
    f.evidence?.log_tail ? `\n<details><summary>Log tail</summary>\n\n\`\`\`\n${String(f.evidence.log_tail).slice(-3000)}\n\`\`\`\n</details>` : null,
    '',
    '```json mivo-qa-finding',
    JSON.stringify(finding, null, 2),
    '```',
    '',
    fingerprintMarker(f.fingerprint),
  ].filter((x) => x !== null).join('\n')
  return { title, body: redact(body), labels: ISSUE_LABELS }
}

// 哪些东西可以变成 issue（纯函数，便于本地核对）：
//  ① PR finding：必须有测试证据（source === 'check'，即一个失败的检查），P0/P1，且 PR 作者在白名单（PraiseZhu）。
//     只来自 LLM 代码审查（source === 'code-review'）的 finding 无论等级都不提 issue，只留在报告里。
//  ② system_health：QA 自身的故障（qa_tool_failures / env_problems / llm_errors）照常提，不受作者限制。
export const ISSUE_AUTHORS = ['PraiseZhu']
const HEALTH_KINDS = { qa_tool_failures: 'QA tool failure', env_problems: 'environment problem', llm_errors: 'LLM error' }

export function selectIssueCandidates(report, { authors = ISSUE_AUTHORS } = {}) {
  const out = []
  const skipped = []
  for (const pr of report.prs ?? []) {
    for (const f of (pr.findings ?? []).filter((x) => ['P0', 'P1'].includes(x.severity))) {
      // 规则按 test_backed（有失败检查佐证）判断，不只看严重度；旧报告没有该字段时按 source === 'check'
      const why = !isTestBacked(f) ? `not test-backed (source ${f.source ?? 'unknown'}; e.g. llm-review-only)`
        : !authors.includes(pr.author) ? `author ${pr.author} not in ${authors.join(',')}`
          : null
      if (why) skipped.push({ pr: pr.number, fingerprint: f.fingerprint, severity: f.severity, source: f.source, reason: why })
      else out.push({ kind: 'finding', pr, finding: f, fingerprint: f.fingerprint })
    }
  }
  const h = report.system_health ?? {}
  for (const [key, label] of Object.entries(HEALTH_KINDS)) {
    for (const e of h[key] ?? []) {
      const fp = fingerprintOf(report.repo, ['system-health', key, e.stage ?? '', e.kind ?? '', e.message ?? ''])
      out.push({ kind: 'health', category: key, label, entry: e, fingerprint: fp })
    }
  }
  return { candidates: out, skipped }
}

export function buildHealthIssuePayload(report, c) {
  const e = c.entry
  const block = { schema: 'mivo-qa-health/v1', fingerprint: c.fingerprint, report_date: report.date, report_file: `reports/${report.date}.json`, run_id: report.run?.id, repo: report.repo, category: c.category, entry: e, system_health_status: report.system_health?.status, environment: report.system_health?.environment }
  const body = [
    `Mivo QA nightly (${report.date}, Asia/Shanghai) hit a QA ${c.label}${e.pr ? ` while testing ${report.repo}#${e.pr}` : ''}.`,
    '',
    e.stage ? `- Stage: ${e.stage}` : null,
    `- Message: ${e.message ?? ''}`,
    e.detail ? `\n\`\`\`\n${String(e.detail).slice(-2000)}\n\`\`\`` : null,
    '',
    '```json mivo-qa-health',
    JSON.stringify(block, null, 2),
    '```',
    '',
    fingerprintMarker(c.fingerprint),
  ].filter((x) => x !== null).join('\n')
  return { title: `[Mivo QA][system_health] ${c.label}: ${String(e.stage ?? e.message ?? '').slice(0, 120)}`.slice(0, 240), body: redact(body), labels: ISSUE_LABELS }
}

export function buildIssuePayloadFor(report, c) {
  return c.kind === 'health' ? buildHealthIssuePayload(report, c) : buildIssuePayload(report, c.pr, c.finding)
}

async function createIssues(report) {
  const { candidates, skipped } = selectIssueCandidates(report)
  const results = skipped.map((x) => ({ ...x, action: 'not-eligible' }))
  for (const c of candidates) {
    const existing = await findIssueByMarkerLabel(ISSUE_REPO, ISSUE_LABELS[0], fingerprintMarker(c.fingerprint))
    if (existing) { results.push({ kind: c.kind, pr: c.pr?.number ?? null, fingerprint: c.fingerprint, action: 'exists', issue: existing.html_url }); continue }
    const issue = await createIssue(ISSUE_REPO, buildIssuePayloadFor(report, c))
    results.push({ kind: c.kind, pr: c.pr?.number ?? null, fingerprint: c.fingerprint, action: 'created', issue: issue?.html_url })
  }
  return results
}

// 发现阶段就拿得到的 PR 基本信息：没测的 / 出错的条目也带 merged_at 等字段（日报按 merged_at 换算上海时区筛选）
export function prMeta(pr) {
  if (!pr) return {}
  return { title: pr.title ?? null, author: pr.user?.login ?? null, url: pr.html_url ?? null, state: pr.merged_at ? 'merged' : (pr.state ?? null), merged_at: pr.merged_at ?? null, merge_sha: pr.merge_commit_sha ?? null }
}

// test_backed：只有失败检查（source: check）佐证的发现才是 true；LLM 审查、覆盖率门禁、QA 自身出错都是 false。
// 下游通知 / 提 issue 规则以它为准，而不是只看严重度。
export const FINDING_SOURCES = ['check', 'code-review', 'coverage-gate', 'qa-error']
// flaky（重跑通过）的失败检查不算测试佐证：它没有重现，不能当成缺陷去通知
export const checkIsTestBacked = (c) => c?.status !== 'flaky' && c?.flaky !== true
export const isTestBacked = (f) => (typeof f?.test_backed === 'boolean' ? f.test_backed : f?.source === 'check' && checkIsTestBacked(f))
export function annotateTestBacked(prs) {
  for (const pr of prs) {
    pr.findings = (pr.findings ?? []).map((f) => { const g = { ...f, source: f.source ?? 'check' }; return { ...g, test_backed: isTestBacked(g) } })
    const backed = pr.findings.filter((f) => f.test_backed)
    pr.test_backed = backed.length > 0
    pr.test_backed_severity = backed.length ? maxSev(backed.map((f) => f.severity)) : null
    pr.finding_sources = [...new Set(pr.findings.map((f) => f.source))]
    if (!('merged_at' in pr)) pr.merged_at = null
    pr.merged_at_shanghai = pr.merged_at ? toShanghaiIso(pr.merged_at) : null
  }
  return prs
}

// 发现了但这次没测的 PR：仍进 prs[]，verdict=not-tested 并写明原因。
// severity 固定为 "untested"（不是 P 级，绝不显示成 P3）；tested: false。超时 / 截止时间另记 qa_tool_failures。
export function notTestedEntry(t, code, reason) {
  return { number: t.number, ...(t.meta ?? {}), discovery_reason: t.reason, verdict: 'not-tested', severity: 'untested', tested: false, not_tested_code: code, not_tested_reason: reason, tested_at: null, findings: [], failed_checks: [], checks: [], coverage_gate: null }
}

// --deadline：HH:MM（上海时间，指开始之后最近的那个时刻）或完整 ISO 时间
export function parseDeadline(v, now = new Date()) {
  const m = String(v).match(/^(\d{1,2}):(\d{2})$/)
  if (m) {
    const day = toShanghaiDay(now)
    let t = Date.parse(`${day}T${m[1].padStart(2, '0')}:${m[2]}:00+08:00`)
    if (t <= now.getTime()) t += 24 * 3600_000
    return t
  }
  const t = Date.parse(v)
  if (!Number.isFinite(t)) throw new Error(`--deadline 需要 HH:MM 或 ISO 时间：${v}`)
  return t
}

// 发现结果 → 本次要测的目标 + 发现了但不测的 PR（not-tested，带原因）
export function planTargets(discovered, opts = {}) {
  let targets = discovered
  if (opts.prs?.length) {
    const byNum = Object.fromEntries(discovered.map((t) => [t.number, t]))
    targets = opts.prs.map((n) => ({ number: n, reason: byNum[n]?.reason ?? 'manual (--pr)', meta: byNum[n]?.meta }))
  }
  const notTested = []
  if (opts.prs?.length) for (const t of discovered) if (!opts.prs.includes(t.number)) notTested.push(notTestedEntry(t, 'excluded-by-pr-filter', 'not selected by --pr in this run'))
  if (opts.limit && targets.length > opts.limit) {
    for (const t of targets.slice(opts.limit)) notTested.push(notTestedEntry(t, 'over-limit', `beyond --limit ${opts.limit}`))
    targets = targets.slice(0, opts.limit)
  }
  return { targets, notTested }
}

// 逐个 PR 执行：每个 PR 有心跳（开始/结束/耗时）；qa-error 记 P2 并登记 qa_tool_failures；
// 超出单 PR 时间预算或整晚截止时间 → not-tested（部分结果放 partial_result）
export async function processTargets(targets, { opts = {}, health, startedAt = new Date(), processOne, now = () => Date.now(), repo = null }) {
  const prTimeoutMs = opts.prTimeoutMin ? opts.prTimeoutMin * 60_000 : null
  const deadlineMs = opts.deadline ? parseDeadline(opts.deadline, startedAt) : null
  const heartbeat = { run_started_at: toShanghaiIso(startedAt), run_finished_at: null, duration_s: null, last_beat_at: toShanghaiIso(startedAt), pr_timeout_min: opts.prTimeoutMin ?? null, deadline: deadlineMs ? new Date(deadlineMs).toISOString() : null, prs: [] }
  health.heartbeat = heartbeat
  const prs = []
  for (const t of targets) {
    if (deadlineMs && now() >= deadlineMs) {
      const e = notTestedEntry(t, 'deadline', `run deadline ${new Date(deadlineMs).toISOString()} reached before this PR started`)
      prs.push(e)
      health.qa_tool_failures.push({ pr: t.number, stage: 'deadline', message: e.not_tested_reason })
      continue
    }
    log('info', 'nightly pr start', { pr: t.number })
    process.stderr.write(`[panoptes] PR #${t.number} (${t.reason}) …\n`)
    const beat = { number: t.number, started_at: toShanghaiIso(new Date(now())), finished_at: null, duration_s: null, verdict: null }
    heartbeat.prs.push(beat)
    const t0 = now()
    const byTimeout = prTimeoutMs != null && (deadlineMs == null || t0 + prTimeoutMs <= deadlineMs)
    const budget = Math.min(prTimeoutMs ? t0 + prTimeoutMs : Infinity, deadlineMs ?? Infinity)
    setBudget(Number.isFinite(budget) ? budget : null)
    let result = null
    try {
      result = await processOne(t)
    } catch (err) {
      if (!overBudget()) {
        health.qa_tool_failures.push({ pr: t.number, stage: 'pipeline', message: redact(err.message).slice(0, 800) })
        const msg = redact(err.message).slice(0, 800)
        const fp = fingerprintOf(repo ?? '', ['qa-error', 'pipeline', msg])
        result = { number: t.number, ...(t.meta ?? {}), discovery_reason: t.reason, verdict: 'qa-error', severity: 'P2', tested: false, error: msg, failed_checks: [], checks: [], coverage_gate: null, test_started_at: beat.started_at, tested_at: null,
          findings: [{ source: 'qa-error', test_backed: false, check_id: 'pipeline', dimension: null, severity: 'P2', title: 'QA pipeline error (the PR itself was not verified)', fingerprint: fp, pr_fingerprint: prFingerprint(fp, t.number), evidence: { error: msg } }] }
      }
    }
    if (overBudget()) {
      const code = byTimeout ? 'pr-timeout' : 'deadline'
      const why = byTimeout ? `per-PR time budget of ${opts.prTimeoutMin} min exceeded` : `run deadline ${new Date(deadlineMs).toISOString()} reached`
      const e = notTestedEntry(t, code, why)
      if (result) e.partial_result = { verdict: result.verdict, checks: result.checks, evidence_dir: result.evidence_dir ?? null }
      health.qa_tool_failures.push({ pr: t.number, stage: code, message: why })
      result = e
    }
    setBudget(null)
    prs.push(result)
    beat.finished_at = toShanghaiIso(new Date(now()))
    beat.duration_s = Math.round((now() - t0) / 1000)
    beat.verdict = result.verdict
    heartbeat.last_beat_at = beat.finished_at
    process.stderr.write(`[panoptes] PR #${t.number} done: ${result.verdict} ${result.severity ?? ''} (${beat.duration_s}s)\n`)
  }
  return { prs, heartbeat }
}

// status / complete / all_discovered_tested / 心跳收尾
export function finalizeHealth(health, prs, startedAt = new Date(), now = () => Date.now()) {
  health.status = health.qa_tool_failures.some((f) => ['discovery', 'pipeline'].includes(f.stage)) || prs.some((p) => p.verdict === 'qa-error') ? 'failed'
    : (health.qa_tool_failures.length || health.env_problems.length || health.llm_errors.length) ? 'degraded' : 'ok'
  // complete：发现成功，且本次要测的每个 PR 都有最终结论（没有 qa-error，没有因超时 / 截止时间而没测）。
  // all_discovered_tested：当天发现的每个 PR 都有最终结论（--pr / --limit 排除的也算没测）。
  const unfinished = (p) => p.verdict === 'qa-error' || (p.verdict === 'not-tested' && ['pr-timeout', 'deadline'].includes(p.not_tested_code))
  const discoveryOk = !health.qa_tool_failures.some((f) => f.stage === 'discovery')
  health.complete = discoveryOk && !prs.some(unfinished)
  health.all_discovered_tested = discoveryOk && !prs.some((p) => unfinished(p) || p.verdict === 'not-tested')
  const hb = health.heartbeat ??= { run_started_at: toShanghaiIso(startedAt), prs: [] }
  hb.run_finished_at = toShanghaiIso(new Date(now()))
  hb.duration_s = Math.round((now() - startedAt.getTime()) / 1000)
  hb.last_beat_at = hb.run_finished_at
  return health
}

// ── 入口 ──────────────────────────────────────────────
export async function runNightly(opts = {}) {
  for (const d of ALL_DIRS) mkdirSync(d, { recursive: true })
  const startedAt = new Date()
  const day = opts.date ?? previousShanghaiDay(startedAt)
  shanghaiDayWindow(day)
  const outFile = resolveOutPath(opts.out, day)
  if (opts.deadline) parseDeadline(opts.deadline, startedAt) // 参数错误在任何工作开始前报出
  const health = { status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], environment: envSnapshot(), discovery: null, notes: [] }
  const reg = loadRegistry()
  const repo = opts.repo ?? 'xindong/mivo-canvas-plugin'
  const project = reg.projects.find((p) => p.repo === repo)
  if (!project?.profileData) throw new Error(`${repo} 没有在 registry 里配置 profile`)
  let secrets = {}
  try { secrets = loadSecrets() } catch (err) { health.env_problems.push({ message: `secrets file unreadable: ${err.message}` }) }
  for (const k of ['ARGUS_LLM_BASE_URL', 'ARGUS_LLM_API_KEY']) if (!secrets[k]) health.env_problems.push({ message: `missing secret ${k === 'ARGUS_LLM_BASE_URL' ? 'QA_LLM_BASE_URL' : 'QA_LLM_API_KEY'}` })
  if (!secrets.ARGUS_TYPESAFE_API_KEY) health.notes.push('Jev triage not configured (no ARGUS_TYPESAFE_API_KEY): every optional check runs for every PR')
  if (!mingQaAvailable()) health.env_problems.push({ message: 'ming-qa qa_agent.py not found; code-review gate unavailable' })
  const env = health.environment
  if (env.sandbox !== 'bwrap') health.env_problems.push({ message: `sandbox mode is '${env.sandbox}' (expected bwrap)` })
  if (!env.chromium) health.env_problems.push({ message: `${LINUX_CHROMIUM} missing: browser E2E will be unavailable` })
  if (env.disk_free_gb != null && env.disk_free_gb < 12) health.env_problems.push({ message: `low disk: ${env.disk_free_gb} GB free` })
  const db = openDb()
  const runId = randomUUID()
  const evidenceRoot = join(EVIDENCE_DIR, 'nightly', day, runId.slice(0, 8))
  mkdirSync(evidenceRoot, { recursive: true })
  await janitor().catch((err) => health.qa_tool_failures.push({ stage: 'janitor', message: err.message }))

  // 发现
  let targets = []
  try {
    const disc = await listNightlyPrs(repo, day)
    health.discovery = { window_start: disc.window.startIso, window_end: disc.window.endIso, found: disc.prs.map((x) => ({ number: x.pr.number, reason: x.reason })) }
    targets = disc.prs.map((x) => ({ number: x.pr.number, reason: x.reason, meta: prMeta(x.pr) }))
  } catch (err) {
    health.qa_tool_failures.push({ stage: 'discovery', message: redact(err.message).slice(0, 500) })
  }
  const plan = planTargets(targets, opts)
  const { prs } = await processTargets(plan.targets, {
    opts, health, startedAt, repo,
    processOne: (t) => processPr({ repo, project, number: t.number, discovery: t.reason, secrets, db, health, evidenceRoot, opts }),
  })
  prs.push(...plan.notTested)
  annotateTestBacked(prs)

  const allFindings = prs.flatMap((p) => p.findings ?? [])
  finalizeHealth(health, prs, startedAt)
  const report = {
    schema_version: SCHEMA_VERSION,
    tool: 'panoptes (argus + ming-qa)',
    date: day,
    timezone: 'Asia/Shanghai',
    repo,
    generated_at: new Date().toISOString(),
    run: {
      id: runId, started_at: startedAt.toISOString(), finished_at: new Date().toISOString(),
      started_at_shanghai: toShanghaiIso(startedAt), finished_at_shanghai: toShanghaiIso(),
      duration_s: Math.round((Date.now() - startedAt.getTime()) / 1000),
      args: { date: opts.date ?? null, prs: opts.prs ?? null, out: opts.out ?? null, reuse_merged: opts.reuseMerged !== false, create_issues: Boolean(opts.createIssues), limit: opts.limit ?? null, pr_timeout_min: opts.prTimeoutMin ?? null, deadline: opts.deadline ?? null, write_merged: writeMergedEnabled(opts), write_open: writeOpenEnabled(opts) },
      argus_commit: (() => { try { return execFileSync('git', ['-C', CODE_DIR, 'rev-parse', '--short', 'HEAD'], { encoding: 'utf8' }).trim() } catch { return null } })(),
      ming_qa_commit: await mingQaVersion(), llm_model: LLM_MODEL, evidence_root: evidenceRoot,
    },
    dimensions: BOXES,
    severity_scale: { P0: 'main/release broken or data loss: introduced failure on a merged PR that still fails on latest main', P1: 'introduced, reproducible product-check failure (blocks merge) or LLM code-review P0/P1', P2: 'quality-guard regression, unverified failure, unstable (flaky: failed then passed on rerun; not test-backed), QA could not verify, or review suggestion', P3: 'pre-existing / fixed-later failure, or informational only' },
    summary: {
      pr_count: prs.length,
      prs_by_severity: Object.fromEntries(['P0', 'P1', 'P2', 'P3', 'untested'].map((s) => [s, prs.filter((p) => p.severity === s).length])),
      findings_by_severity: Object.fromEntries(['P0', 'P1', 'P2', 'P3'].map((s) => [s, allFindings.filter((f) => f.severity === s).length])),
      findings_total: allFindings.length,
      reused_from_on_merge: prs.filter((p) => p.reused).map((p) => p.number),
      tested_count: prs.filter((p) => !['not-tested', 'qa-error'].includes(p.verdict)).length,
      not_tested: prs.filter((p) => p.verdict === 'not-tested').map((p) => ({ number: p.number, code: p.not_tested_code, reason: p.not_tested_reason })),
      qa_errors: prs.filter((p) => p.verdict === 'qa-error').map((p) => p.number),
      test_backed_findings_by_severity: Object.fromEntries(['P0', 'P1', 'P2', 'P3'].map((s) => [s, allFindings.filter((f) => f.test_backed && f.severity === s).length])),
      prs_test_backed: prs.filter((p) => p.test_backed).map((p) => p.number),
    },
    prs,
    issues: { enabled: false, repo: ISSUE_REPO, labels: ISSUE_LABELS, dedup: 'fingerprint marker in body, label qa-auto, open+closed', policy: 'P0/P1 findings backed by a failing check on PRs authored by PraiseZhu; system_health failures always; LLM-review-only findings never', results: [] },
    system_health: health,
  }
  if (opts.createIssues) {
    if (process.env.ARGUS_ALLOW_ISSUES !== '1') report.issues = { enabled: false, repo: ISSUE_REPO, labels: ISSUE_LABELS, reason: '--create-issues given but ARGUS_ALLOW_ISSUES!=1; nothing created' }
    else {
      try { report.issues = { enabled: true, repo: ISSUE_REPO, labels: ISSUE_LABELS, results: await createIssues(report) } }
      catch (err) { report.issues = { enabled: true, error: redact(err.message).slice(0, 500) }; health.qa_tool_failures.push({ stage: 'issues', message: redact(err.message).slice(0, 500) }) }
    }
  }
  report.merged_reports = writePerPrReports(report, { opts, outFile, kind: 'merged' })
  if (writeOpenEnabled(opts)) report.open_reports = writePerPrReports(report, { opts, outFile, kind: 'open' })
  const file = writeReport(report, outFile)
  return { file, report }
}

// 夜间全量（没有 --pr）默认把每个真实测完的已合并 PR 也写成 reports/merged/PR-N.json（与合并即测同格式），
// 供日报与之后的复用读取。--no-write-merged 关闭；--write-merged 在 --pr 运行里也打开。
export const writeMergedEnabled = (opts) => opts.writeMerged ?? !opts.prs?.length
// --write-open：同样的单 PR 格式写 reports/open/PR-N.json。开放 PR 无论结果都写（含 not-tested / qa-error，
// 带具体原因），这样 deadline 截断的 PR 也有文件。默认关。
export const writeOpenEnabled = (opts) => Boolean(opts.writeOpen)
export const openReportsDir = () => process.env.ARGUS_OPEN_REPORTS_DIR ?? join(REPORTS_DIR, 'open')
export const openReportPath = (n, dir = openReportsDir()) => join(dir, `PR-${n}.json`)

const UNTESTED_VERDICTS = new Set(['not-tested', 'qa-error'])
export function mergedReportFor(report, pr, { kind = 'merged' } = {}) {
  const own = (list) => (list ?? []).filter((e) => e.pr == null || e.pr === pr.number)
  const h = report.system_health
  const findings = pr.findings ?? []
  const untested = UNTESTED_VERDICTS.has(pr.verdict)
  return {
    ...report,
    ...(kind === 'merged' ? { merged_sha: pr.merge_sha ?? pr.tested_sha ?? null } : { head_sha: pr.tested_sha ?? pr.head_sha ?? null }),
    written_by: 'nightly',
    run: { ...report.run, args: { ...report.run.args, prs: [pr.number] }, source_report: null },
    summary: {
      pr_count: 1,
      prs_by_severity: Object.fromEntries(['P0', 'P1', 'P2', 'P3', 'untested'].map((s) => [s, pr.severity === s ? 1 : 0])),
      findings_by_severity: Object.fromEntries(['P0', 'P1', 'P2', 'P3'].map((s) => [s, findings.filter((f) => f.severity === s).length])),
      findings_total: findings.length, reused_from_on_merge: pr.reused ? [pr.number] : [], tested_count: untested ? 0 : 1,
      not_tested: pr.verdict === 'not-tested' ? [{ number: pr.number, code: pr.not_tested_code, reason: pr.not_tested_reason }] : [],
      qa_errors: pr.verdict === 'qa-error' ? [pr.number] : [],
      test_backed_findings_by_severity: Object.fromEntries(['P0', 'P1', 'P2', 'P3'].map((s) => [s, findings.filter((f) => f.test_backed && f.severity === s).length])),
      prs_test_backed: pr.test_backed ? [pr.number] : [],
    },
    prs: [pr],
    issues: { enabled: false, reason: 'written from a nightly run; issues are only handled by the run itself' },
    system_health: { ...h, qa_tool_failures: own(h.qa_tool_failures), env_problems: own(h.env_problems), llm_errors: own(h.llm_errors),
      heartbeat: h.heartbeat ? { ...h.heartbeat, prs: (h.heartbeat.prs ?? []).filter((b) => b.number === pr.number) } : null },
    merged_reports: undefined,
    open_reports: undefined,
  }
}

// 单 PR 文件的选择规则：merged → 真实测完且非复用；open → 所有开放 PR（未测的也写，带原因）
const PER_PR_KINDS = {
  merged: { enabled: writeMergedEnabled, path: (n, opts) => mergedReportPath(n, opts.mergedDir), select: (pr) => pr.state === 'merged' && !pr.reused && REUSABLE_VERDICTS.has(pr.verdict) },
  open: { enabled: writeOpenEnabled, path: (n, opts) => openReportPath(n, opts.openDir), select: (pr) => pr.state === 'open' && pr.not_tested_code !== 'excluded-by-pr-filter' },
}

export function writePerPrReports(report, { opts, outFile, kind }) {
  const k = PER_PR_KINDS[kind]
  if (!k.enabled(opts)) return []
  const written = []
  for (const pr of report.prs) {
    if (!k.select(pr)) continue
    const file = k.path(pr.number, opts)
    if (resolve(file) === resolve(outFile)) continue
    const doc = mergedReportFor(report, pr, { kind })
    doc.run.source_report = outFile
    try { writeReport(doc, file); written.push({ pr: pr.number, file }) } catch (err) { report.system_health.qa_tool_failures.push({ pr: pr.number, stage: `write-${kind}-report`, message: redact(err.message).slice(0, 300) }) }
  }
  return written
}
