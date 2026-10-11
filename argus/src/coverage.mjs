// 白盒覆盖率门禁：单测步骤用 vitest --coverage（v8）跑，读 coverage-summary.json（总体）与 coverage-final.json
// （逐行），再与 PR diff 的新增行求交，得到「新代码覆盖率」。低于门槛 → P2（只是建议复查，永远不会单独变成 issue）。
import { readFileSync, existsSync, statSync, rmSync } from 'node:fs'
import { join, relative, isAbsolute, resolve } from 'node:path'

// 门槛与指标取自 ming-qa G2「Unit Tests And Coverage」（skills/quality-assurance-agent/references/quality-gates.md；
// 配置键 qualityGates.coverage.{lines, branches, functions, newCode}）。ming-qa 把 New-code coverage 定义为
// 新代码的（行）覆盖率、默认 90，没有给出更细的算法；这里按同一口径：PR 新增 / 修改的可执行行中被执行到的比例。
export const COVERAGE_THRESHOLDS = Object.freeze({ lines: 80, branches: 70, functions: 80, new_code: 90 })
export const COVERAGE_SOURCE = 'ming-qa G2 Unit Tests And Coverage (references/quality-gates.md; qualityGates.coverage lines/branches/functions/newCode)'

// vitest 追加参数：只写 JSON 报告，不让 vitest 自己按阈值判失败（门禁由我们算，失败不能伪装成单测失败）
export function vitestCoverageArgs(dir) {
  return ['--coverage.enabled=true', '--coverage.provider=v8', '--coverage.reporter=json-summary', '--coverage.reporter=json',
    `--coverage.reportsDirectory=${dir}`, '--coverage.reportOnFailure=true', '--coverage.clean=true']
}

const pct = (x) => (typeof x?.pct === 'number' && Number.isFinite(x.pct) ? x.pct : null)

// coverage-final.json（istanbul 格式）→ 每个文件「可执行行」与「已覆盖行」。只保留压缩后的行号，丢掉原始大文件。
export function compactFinal(final, srcRoot) {
  const files = {}
  const root = resolve(srcRoot)
  for (const [abs, fc] of Object.entries(final ?? {})) {
    const p = fc?.path ?? abs
    const rel = isAbsolute(p) ? relative(root, p) : p
    if (!rel || rel.startsWith('..')) continue
    const exec = new Map()
    for (const [id, loc] of Object.entries(fc.statementMap ?? {})) {
      const line = loc?.start?.line
      if (!Number.isInteger(line)) continue
      const hit = (fc.s?.[id] ?? 0) > 0
      exec.set(line, exec.get(line) || hit)
    }
    const lines = [...exec.keys()].sort((a, b) => a - b)
    files[rel.split('\\').join('/')] = { lines, covered: lines.filter((l) => exec.get(l)) }
  }
  return files
}

// 读 vitest 输出目录；单测失败或没产出报告 → unavailable（不是不达标）
export function readCoverageDir(dir, srcRoot, { maxBytes = 256 * 1024 * 1024 } = {}) {
  const summaryFile = join(dir, 'coverage-summary.json')
  const finalFile = join(dir, 'coverage-final.json')
  if (!existsSync(summaryFile)) return { status: 'unavailable', reason: 'coverage-summary.json not produced' }
  try {
    const total = JSON.parse(readFileSync(summaryFile, 'utf8')).total ?? {}
    const out = { status: 'ok', total: { lines: pct(total.lines), branches: pct(total.branches), functions: pct(total.functions), statements: pct(total.statements) }, files: null }
    if (existsSync(finalFile) && statSync(finalFile).size <= maxBytes) out.files = compactFinal(JSON.parse(readFileSync(finalFile, 'utf8')), srcRoot)
    // 原始逐行报告可能很大，压缩后删除，证据目录里只留 summary
    rmSync(finalFile, { force: true })
    return out
  } catch (err) {
    return { status: 'unavailable', reason: `coverage report unreadable: ${String(err.message).slice(0, 200)}` }
  }
}

// `git diff -U0 base head` → { file: [新增/修改后的行号] }
export function parseAddedLines(diff) {
  const out = {}
  let file = null
  let line = 0
  for (const raw of String(diff ?? '').split('\n')) {
    if (raw.startsWith('+++ ')) { const p = raw.slice(4).trim(); file = p === '/dev/null' ? null : p.replace(/^b\//, ''); continue }
    if (raw.startsWith('--- ')) continue
    const h = raw.match(/^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@/)
    if (h) { line = Number(h[1]); continue }
    if (!file) continue
    if (raw.startsWith('+')) { (out[file] ??= []).push({ line, text: raw.slice(1) }); line++ }
    else if (raw.startsWith(' ')) line++
  }
  return out
}

const SOURCE_RE = /\.(?:[cm]?[jt]sx?)$/
const NOT_SOURCE_RE = /(?:^|\/)(?:node_modules|dist|coverage|_tmp|__mocks__|__fixtures__|fixtures|test|tests|__tests__)\/|\.(?:test|spec|bench|d)\.[cm]?[jt]sx?$|(?:^|\/)(?:vite|vitest|eslint|playwright)\.[^/]*config\.[cm]?[jt]s$|\.config\.[cm]?[jt]s$/
export const isCoverageSource = (file) => SOURCE_RE.test(file) && !NOT_SOURCE_RE.test(file)
// 未被测试加载的文件没有可执行行信息：按「像代码的新增行」保守计为未覆盖
const looksLikeCode = (t) => { const s = t.trim(); return Boolean(s) && !/^(?:\/\/|\/\*|\*|\*\/)/.test(s) && !/^(?:import\b|export\s+(?:type|interface)\b|type\s|interface\s|[{}()[\];,]+$)/.test(s) }

export function newCodeCoverage(added, files) {
  let executable = 0, covered = 0
  const perFile = []
  const unloaded = []
  for (const [file, rows] of Object.entries(added ?? {})) {
    if (!isCoverageSource(file)) continue
    const fc = files?.[file]
    if (fc) {
      const exec = new Set(fc.lines), cov = new Set(fc.covered)
      const e = rows.filter((r) => exec.has(r.line))
      const c = e.filter((r) => cov.has(r.line))
      if (!e.length) continue
      executable += e.length; covered += c.length
      perFile.push({ file, executable: e.length, covered: c.length, uncovered_lines: e.filter((r) => !cov.has(r.line)).map((r) => r.line).slice(0, 50) })
    } else {
      const e = rows.filter((r) => looksLikeCode(r.text))
      if (!e.length) continue
      executable += e.length
      unloaded.push(file)
      perFile.push({ file, executable: e.length, covered: 0, not_loaded_by_tests: true, uncovered_lines: e.map((r) => r.line).slice(0, 50) })
    }
  }
  return { pct: executable ? Math.round((covered / executable) * 10000) / 100 : null, executable_lines: executable, covered_lines: covered, unloaded_files: unloaded, files: perFile }
}

export const NEW_CODE_METHOD = 'ming-qa newCode = line coverage of new code. Changed lines: git diff <base_sha> <tested_sha> (merged: first parent of the merge commit; open: merge-base with the target branch); '
  + 'added/modified lines in non-test JS/TS source files are intersected with v8 statement start lines from coverage-final.json; '
  + 'lines that are not executable statements are ignored; files never loaded by the unit tests count their code-like added lines as uncovered. '
  + 'pct = covered / executable changed lines; null (not evaluated) when the diff has no executable source lines.'

// 门禁结论：任何一项低于门槛 → fail（P2）；拿不到覆盖率 → unavailable（不判 fail，单独说明）
// 门禁只看新代码覆盖率（< new_code 阈值 → 不达标 → 一条 P2）。全仓 lines / branches / functions 只作基线趋势数据：
// 记在 baseline 里（附参考线 BASELINE_REFERENCE），results 里标 'baseline'，永远不让门禁失败、不产生 finding。
export const GATED_METRICS = Object.freeze(['new_code'])
export const BASELINE_METRICS = Object.freeze(['lines', 'branches', 'functions'])
export function coverageGate({ coverage, added, thresholds = COVERAGE_THRESHOLDS, stepId = 'unit', diffError = null }) {
  const gated = Object.fromEntries(GATED_METRICS.map((k) => [k, thresholds[k]]))
  const reference = Object.fromEntries(BASELINE_METRICS.map((k) => [k, thresholds[k]]))
  const gate = { step: stepId, provider: 'v8 (vitest --coverage)', thresholds_source: COVERAGE_SOURCE, thresholds: gated, gated_metrics: [...GATED_METRICS], baseline: null, values: null, results: null, status: 'unavailable', pass: null, new_code: null, new_code_method: NEW_CODE_METHOD, reason: null }
  if (!coverage || coverage.status !== 'ok') { gate.reason = coverage?.reason ?? 'unit step did not run with coverage'; return gate }
  const nc = diffError || !coverage.files ? null : newCodeCoverage(added, coverage.files)
  gate.new_code = nc ?? { pct: null, reason: diffError ?? 'per-line coverage unavailable' }
  gate.values = { lines: coverage.total.lines, branches: coverage.total.branches, functions: coverage.total.functions, new_code: nc?.pct ?? null }
  gate.baseline = { role: 'trend-only (recorded, never gated, never a finding)', lines: gate.values.lines, branches: gate.values.branches, functions: gate.values.functions, reference }
  gate.results = Object.fromEntries([
    ...BASELINE_METRICS.map((k) => [k, 'baseline']),
    ...GATED_METRICS.map((k) => { const v = gate.values[k]; return [k, v == null ? 'not-evaluated' : v >= gated[k] ? 'pass' : 'fail'] }),
  ])
  const failed = GATED_METRICS.filter((k) => gate.results[k] === 'fail')
  const notEvaluated = GATED_METRICS.some((k) => gate.results[k] === 'not-evaluated')
  gate.status = failed.length ? 'fail' : notEvaluated ? 'not-evaluated' : 'pass'
  gate.pass = failed.length ? false : notEvaluated ? null : true
  gate.failed = failed
  if (notEvaluated) gate.reason = `new-code coverage not evaluated: ${gate.new_code?.reason ?? 'unknown'}`
  return gate
}
