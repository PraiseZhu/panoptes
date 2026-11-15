// 白盒覆盖率：单测步骤用 vitest --coverage（v8）跑，读 coverage-summary.json（总体）与 coverage-final.json
// （逐行），再与 PR diff 的新增行求交，得到「新代码覆盖率」。数字照常写进报告当信息（和参考线 90 比出 pass / fail）。
// 统一分级（Praise 2026-10-23）：覆盖率低于参考线本身不是问题，不出 finding；只有 PR 正文或 docs/pr/<N>/qa.md 自己写明了
// 覆盖率目标却没达到，才按「自己定的验收没达成」记一条 P2（statedCoverageTarget + evaluateStatedTarget）。
import { TEST_HELPER_RE, isTestPath, requiredStepsForFile } from './classify.mjs'
import { readFileSync, existsSync, statSync, rmSync, readdirSync } from 'node:fs'
import { join, relative, isAbsolute, resolve, sep, basename } from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFileSync } from 'node:child_process'

// 门槛与指标取自 ming-qa G2「Unit Tests And Coverage」（skills/quality-assurance-agent/references/quality-gates.md；
// 配置键 qualityGates.coverage.{lines, branches, functions, newCode}）。ming-qa 把 New-code coverage 定义为
// 新代码的（行）覆盖率、默认 90，没有给出更细的算法；这里按同一口径：PR 新增 / 修改的可执行行中被执行到的比例。
export const COVERAGE_THRESHOLDS = Object.freeze({ lines: 80, branches: 70, functions: 80, new_code: 90 })
export const COVERAGE_SOURCE = 'ming-qa G2 Unit Tests And Coverage (references/quality-gates.md; qualityGates.coverage lines/branches/functions/newCode)'

// vitest 追加参数：只写 JSON 报告，不让 vitest 自己按阈值判失败（门禁由我们算，失败不能伪装成单测失败）
// v8 插桩让用例变慢（cindyplugin library* / depositBindingLock 在默认 5 s 下超时）：覆盖率轮次调高单个用例 / 钩子超时。
// 只用于覆盖率轮次（它的通过 / 失败不参与判定），判定用的首轮不受影响。ARGUS_COVERAGE_TEST_TIMEOUT_MS 可调（默认 30000）
export const coverageTestTimeoutMs = (env = process.env) => { const n = Number(env.ARGUS_COVERAGE_TEST_TIMEOUT_MS); return Number.isInteger(n) && n > 0 ? n : 30_000 }
export function vitestCoverageArgs(dir) {
  const t = coverageTestTimeoutMs()
  return ['--coverage.enabled=true', '--coverage.provider=v8', '--coverage.reporter=json-summary', '--coverage.reporter=json',
    `--coverage.reportsDirectory=${dir}`, '--coverage.reportOnFailure=true', '--coverage.clean=true', `--testTimeout=${t}`, `--hookTimeout=${t}`]
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
    const spans = [] // [起行, 止行, 执行过]：跨多行的语句与分支范围；改动落在语句中间（展开参数、JSX 里的三元）时按包住它的最内层范围算（#1096）
    for (const [id, loc] of Object.entries(fc.statementMap ?? {})) {
      const line = loc?.start?.line
      if (!Number.isInteger(line)) continue
      const hit = (fc.s?.[id] ?? 0) > 0
      exec.set(line, exec.get(line) || hit)
      if (loc.end?.line > line) spans.push([line, loc.end.line, hit])
    }
    for (const [id, br] of Object.entries(fc.branchMap ?? {})) {
      (br?.locations ?? []).forEach((loc, i) => {
        if (Number.isInteger(loc?.start?.line) && loc.end?.line > loc.start.line) spans.push([loc.start.line, loc.end.line, (fc.b?.[id]?.[i] ?? 0) > 0])
      })
    }
    const lines = [...exec.keys()].sort((a, b) => a - b)
    files[rel.split('\\').join('/')] = { lines, covered: lines.filter((l) => exec.get(l)), ...(spans.length ? { spans } : {}) }
  }
  return files
}

// 独立于旧 files 形状的补充证据；旧报告和旧消费者无需补默认值。
export function compactCoverageEvidence(final, srcRoot) {
  const files = compactFinal(final, srcRoot)
  for (const [path, fc] of Object.entries(final ?? {})) {
    const rel = (isAbsolute(fc?.path ?? path) ? relative(resolve(srcRoot), fc?.path ?? path) : fc?.path ?? path).replaceAll('\\', '/')
    if (!files[rel]) continue
    const ranges = (map, counts, branch) => Object.entries(map ?? {}).flatMap(([id, item]) => {
      const locations = branch ? item.locations ?? [] : [item.loc ?? item.decl]
      return locations.flatMap((loc, arm) => Number.isInteger(loc?.start?.line) ? [{
        start: loc.start.line, end: loc.end?.line ?? loc.start.line,
        key: `${loc.start.line}:${loc.start.column ?? 0}-${loc.end?.line ?? loc.start.line}:${loc.end?.column ?? 0}${branch ? `:${arm}` : ''}`,
        covered: (branch ? counts?.[id]?.[arm] : counts?.[id]) > 0,
      }] : [])
    })
    files[rel] = { ...files[rel], load_status: fc.load_status ?? (fc.all === true ? 'unloaded' : 'loaded'),
      branches: ranges(fc.branchMap, fc.b, true), functions: ranges(fc.fnMap, fc.f, false),
      sources: fc.coverage_sources ?? [], failures: fc.coverage_failures ?? [] }
  }
  return files
}

// 同一输入同时用于 unit、node-test 和 browser；covers/exercises 仅证明分配，不能证明源码被加载。
export function coverageRunnerState(result, step = null) {
  const c = result?.coverage
  const status = result?.status
  if (result?.timedOut || result?.timed_out || status === 'timeout') return { status: 'unavailable', reason: 'coverage-runner-timeout' }
  if (result?.unavailable || result?.runner_error || result?.runnerError || result?.environment_unavailable
    || ['unavailable', 'runner_error', 'environment_unavailable'].includes(status)) return { status: 'unavailable', reason: 'coverage-runner-unavailable' }
  if (result?.skipped || ['skipped', 'not_applicable', 'not-applicable'].includes(status)) return { status: 'not_evaluated', reason: 'coverage-step-not-run' }
  if (['not_measurable', 'not_evaluated', 'not-evaluated'].includes(c?.status)) return { status: c.status.replaceAll('-', '_'), reason: 'coverage-not-collected' }
  if (c && c.status !== 'ok') return { status: 'unavailable', reason: 'coverage-report-unavailable' }
  if (Object.values(c?.evidence ?? c?.files ?? {}).some((f) => f.failures?.length)) return { status: 'unavailable', reason: 'coverage-shards-incomplete' }
  const maps = [c?.files, c?.evidence].filter((f) => f != null)
  if (maps.length) {
    if (maps.some((f) => Object.keys(f).length)) return { status: 'ok', reason: null }
    if (c.unmeasurable?.length) return { status: 'not_measurable', reason: 'source-not-measurable' }
    return { status: 'unavailable', reason: 'coverage-not-produced' }
  }
  if (step?.coveragePolicy?.status === 'not_measurable') return { status: 'not_measurable', reason: 'step-coverage-not-measurable' }
  return { status: 'not_evaluated', reason: 'coverage-not-collected' }
}

const metricEvidence = (total, covered, reason = null, emptyStatus = 'not_evaluated') => ({
  status: total ? (total === covered ? 'pass' : 'fail') : emptyStatus,
  total, covered: total ? covered : null, pct: total ? Math.round(covered / total * 10000) / 100 : null,
  ...(reason ? { reason } : {}),
})

export function coverageMapping({ added, results = [], profile = null }) {
  const assigned = new Map(Object.keys(added ?? {}).map((file) => [file, profile ? requiredStepsForFile(profile, { filename: file }) : results.map((r) => r.id)]))
  const steps = new Map((profile?.steps ?? []).map((s) => [s.id, s]))
  const runs = [...results, ...[...new Set([...assigned.values()].flat())].filter((id) => !results.some((r) => r.id === id)).map((id) => ({ id }))]
  const sources = runs.map((r) => ({ step: r.id, provider: r.coverage?.kind ?? (r.coverage ? 'istanbul' : null), ...coverageRunnerState(r, steps.get(r.id)) }))
  const measured = runs.filter((r) => coverageRunnerState(r, steps.get(r.id)).status === 'ok').map((r) => ({ ...r,
    measuredFiles: mergeCoverageFiles(r.coverage.evidence, r.coverage.files) }))
  const files = []
  for (const [file, rows] of Object.entries(added ?? {})) {
    if (!isCoverageSource(file)) continue
    const loaded = measured.flatMap((r) => {
      const fc = r.measuredFiles[file]
      return fc && fc.load_status !== 'unloaded' ? [{ step: r.id, fc }] : []
    })
    const unmeasurable = measured.some((r) => r.coverage.unmeasurable?.some((f) => f.file === file))
    const fileSources = sources.filter((s) => assigned.get(file).includes(s.step))
    const gaps = fileSources.filter((s) => s.status !== 'ok')
    const gap = ['unavailable', 'not_measurable', 'not_evaluated'].map((status) => gaps.find((s) => s.status === status)).find(Boolean)
    const missingStatus = unmeasurable ? 'not_measurable' : gap?.status ?? (PLAIN_JS_RE.test(file) ? 'not_measurable' : fileSources.length ? 'fail' : 'not_evaluated')
    const missingReason = unmeasurable ? 'source-not-measurable' : gap?.reason ?? (missingStatus === 'not_measurable' ? 'source-coverage-not-collected'
      : fileSources.length ? 'assigned-steps-did-not-load-source' : 'no-coverage-step-assigned')
    const lines = []
    for (const row of rows) {
      const measuredBy = [], coveredBy = []
      for (const { step, fc } of loaded) {
        const span = (fc.spans ?? []).filter(([a, b]) => a <= row.line && row.line <= b).sort((a, b) => (a[1] - a[0]) - (b[1] - b[0]))[0]
        if ((fc.lines ?? []).includes(row.line) || (looksLikeCode(row.text) && span)) {
          measuredBy.push(step)
          if ((fc.covered ?? []).includes(row.line) || (!(fc.lines ?? []).includes(row.line) && span?.[2])) coveredBy.push(step)
        }
      }
      if (!measuredBy.length && !looksLikeCode(row.text)) continue
      lines.push({ line: row.line, status: measuredBy.length ? (coveredBy.length ? 'pass' : 'fail') : loaded.length ? 'not_evaluated' : missingStatus,
        measured_by: [...new Set(measuredBy)], covered_by: [...new Set(coveredBy)], ...(!loaded.length ? { reason: missingReason } : {}) })
    }
    const changedMetric = (name) => {
      const ranges = new Map()
      for (const { step, fc } of loaded) for (const range of fc[name] ?? []) {
        if (!rows.some((r) => r.line >= range.start && r.line <= range.end && looksLikeCode(r.text))) continue
        const key = range.key ?? `${range.start}-${range.end}`
        const cur = ranges.get(key) ?? { ...range, measured_by: [], covered_by: [] }
        cur.covered ||= range.covered
        cur.measured_by.push(step)
        if (range.covered) cur.covered_by.push(step)
        ranges.set(key, cur)
      }
      const items = [...ranges.values()].map((r) => ({ ...r, measured_by: [...new Set(r.measured_by)], covered_by: [...new Set(r.covered_by)] }))
      return { ...metricEvidence(items.length, items.filter((r) => r.covered_by.length).length,
        items.length ? null : loaded.length ? 'changed-ranges-not-measured' : missingReason, loaded.length ? 'not_evaluated' : missingStatus === 'fail' ? 'not_evaluated' : missingStatus), ranges: items }
    }
    const evaluated = lines.filter((r) => ['pass', 'fail'].includes(r.status))
    files.push({ file, load_status: loaded.length ? 'loaded' : missingStatus === 'fail' ? 'unloaded' : 'unknown',
      loaded_by: [...new Set(loaded.map((r) => r.step))],
      measurement_gaps: gaps,
      sources: loaded.flatMap(({ step, fc }) => (fc.sources?.length ? fc.sources : [{ provider: fc.source ?? 'istanbul' }]).map((s) => ({ ...s, step }))),
      lines: { ...metricEvidence(evaluated.length, evaluated.filter((r) => r.status === 'pass').length, loaded.length ? null : missingReason,
        lines.length ? lines.find((r) => r.status !== 'not_evaluated')?.status ?? 'not_evaluated' : 'not_evaluated'), changed: lines },
      branches: changedMetric('branches'), functions: changedMetric('functions') })
  }
  return { schema: 'coverage.mapping/v1', sources, files, blocking: false, finding_eligible: false }
}

// 读 vitest 输出目录；单测失败或没产出报告 → unavailable（不是不达标）
export function readCoverageDir(dir, srcRoot, { maxBytes = 256 * 1024 * 1024 } = {}) {
  const summaryFile = join(dir, 'coverage-summary.json')
  const finalFile = join(dir, 'coverage-final.json')
  if (!existsSync(summaryFile)) return { status: 'unavailable', reason: 'coverage-summary.json not produced' }
  try {
    const total = JSON.parse(readFileSync(summaryFile, 'utf8')).total ?? {}
    const out = { status: 'ok', total: { lines: pct(total.lines), branches: pct(total.branches), functions: pct(total.functions), statements: pct(total.statements) }, files: null }
    if (existsSync(finalFile) && statSync(finalFile).size <= maxBytes) {
      const final = JSON.parse(readFileSync(finalFile, 'utf8'))
      out.files = compactFinal(final, srcRoot)
      out.evidence = compactCoverageEvidence(final, srcRoot)
      if (Object.values(out.evidence).some((f) => f.failures?.length)) {
        out.status = 'unavailable'
        out.reason = 'coverage shard data incomplete'
      }
    }
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
export const isCoverageSource = (file) => SOURCE_RE.test(file) && !NOT_SOURCE_RE.test(file) && !isTestPath(file) && !TEST_HELPER_RE.test(file)
// 未被测试加载的文件没有可执行行信息：按「像代码的新增行」保守计为未覆盖
const looksLikeCode = (t) => { const s = t.trim(); return Boolean(s) && !/^(?:\/\/|\/\*|\*|\*\/)/.test(s) && !/^(?:import\b|export\s+(?:type|interface)\b|type\s|interface\s|[{}()[\];,]+$)/.test(s) }

// 普通 JS（.js/.mjs/.cjs）多是 node 直接跑的脚本或宿主入口（cindyplugin/main.js、scripts/*.mjs）：vitest 不加载、
// V8 也没收到数据时说不清测没测 → 标「无法度量」，不进分母。TS/TSX 都能被 vitest 加载，没加载就是没测，照旧按 0 计。
const PLAIN_JS_RE = /\.[cm]?js$/
export function newCodeCoverage(added, files) {
  let executable = 0, covered = 0
  const perFile = []
  const unloaded = []
  const notMeasurable = []
  for (const [file, rows] of Object.entries(added ?? {})) {
    if (!isCoverageSource(file)) continue
    const fc = files?.[file]
    if (fc && fc.load_status !== 'unloaded') {
      const exec = new Set(fc.lines), cov = new Set(fc.covered)
      // 不是语句起始行、但落在多行语句或分支范围里的像代码的改动行 → 按包住它的最内层范围是否执行过计
      const inner = (l) => (fc.spans ?? []).filter(([a, b]) => a <= l && l <= b).sort((x, y) => (x[1] - x[0]) - (y[1] - y[0]))[0]
      for (const r of rows) {
        const sp = !exec.has(r.line) && looksLikeCode(r.text) && inner(r.line)
        if (sp) { exec.add(r.line); if (sp[2]) cov.add(r.line) }
      }
      const e = rows.filter((r) => exec.has(r.line))
      const c = e.filter((r) => cov.has(r.line))
      if (!e.length) continue
      executable += e.length; covered += c.length
      perFile.push({ file, status: c.length === e.length ? 'pass' : 'fail', load_status: fc.load_status ?? 'loaded', executable: e.length, covered: c.length, uncovered_lines: e.filter((r) => !cov.has(r.line)).map((r) => r.line).slice(0, 50), ...(fc.source ? { source: fc.source } : {}) })
    } else {
      const e = rows.filter((r) => looksLikeCode(r.text))
      if (!e.length) continue
      if (PLAIN_JS_RE.test(file)) {
        notMeasurable.push(file)
        perFile.push({ file, status: 'not_measurable', load_status: 'unknown', executable: e.length, covered: null, not_measurable: true, reason: 'not loaded by vitest and no V8 data from node --test / subprocess runs' })
        continue
      }
      executable += e.length
      unloaded.push(file)
      perFile.push({ file, status: 'fail', load_status: 'unloaded', executable: e.length, covered: 0, not_loaded_by_tests: true, uncovered_lines: e.map((r) => r.line).slice(0, 50) })
    }
  }
  return { status: executable ? (covered === executable ? 'pass' : 'fail') : notMeasurable.length ? 'not_measurable' : 'not_evaluated', pct: executable ? Math.round((covered / executable) * 10000) / 100 : null, executable_lines: executable, covered_lines: covered, unloaded_files: unloaded, not_measurable_files: notMeasurable, files: perFile }
}

// ── NODE_V8_COVERAGE 原始数据（node --test、子进程）→ 与 compactFinal 同形的 { file: { lines, covered } } ──
// 只收源码树里的 JS/TS；测试把文件复制到临时目录再跑的（file:///tmp/xxx/a/b.mjs），按路径后缀对回仓里的同名文件。
// 可执行行：像代码的非空行（与未加载文件同一口径）；已覆盖：行尾字符落在执行次数 > 0 的最内层块里（多个进程取并集）。
export function repoFileIndex(srcRoot) {
  try { return execFileSync('git', ['-C', srcRoot, 'ls-files'], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024, stdio: ['ignore', 'pipe', 'ignore'] }).split('\n').filter(Boolean) }
  catch { return [] }
}
export function mapToRepo(absPath, srcRoot, index) {
  const root = resolve(srcRoot)
  if (absPath.startsWith(root + sep)) return relative(root, absPath).split(sep).join('/')
  const segs = absPath.split(sep).filter(Boolean)
  // 最长后缀优先；至少两段（目录/文件），且在仓里唯一
  for (let k = segs.length - 1; k >= 2; k--) {
    const suffix = segs.slice(-k).join('/')
    const hits = index.filter((f) => f === suffix || f.endsWith(`/${suffix}`))
    if (hits.length === 1) return hits[0]
    if (hits.length > 1) return null
  }
  return null
}
// 复制到临时目录、目录名也改了的（gh-app-router 测试把代码复制进 /tmp/x/praisebuild/）：文件名在仓里唯一、
// 且 V8 顶层脚本长度与仓里文件一致才认
function sameFileByName(abs, sc, srcRoot, index) {
  const name = basename(abs)
  const hits = index.filter((f) => f === name || f.endsWith(`/${name}`))
  if (hits.length !== 1) return null
  const len = Math.max(0, ...(sc.functions ?? []).flatMap((fn) => fn.ranges ?? []).map((r) => r.endOffset))
  try { return readFileSync(join(srcRoot, hits[0]), 'utf8').length === len ? hits[0] : null } catch { return null }
}
export function readV8Dir(dir, srcRoot, { index = null, maxBytes = 512 * 1024 * 1024, diagnostics = [] } = {}) {
  if (!dir || !existsSync(dir)) { diagnostics.push({ status: 'unavailable', reason: 'coverage-directory-missing' }); return {} }
  const idx = index ?? repoFileIndex(srcRoot)
  const byFile = new Map()
  let bytes = 0
  const names = readdirSync(dir).filter((n) => n.endsWith('.json')).sort()
  if (!names.length) diagnostics.push({ status: 'unavailable', reason: 'coverage-not-produced' })
  for (const name of names) {
    const f = join(dir, name)
    bytes += statSync(f).size
    if (bytes > maxBytes) { diagnostics.push({ status: 'unavailable', reason: 'coverage-byte-budget-exceeded' }); break }
    let doc
    try { doc = JSON.parse(readFileSync(f, 'utf8')) } catch { diagnostics.push({ status: 'unavailable', reason: 'coverage-report-unreadable' }); continue }
    if (!Array.isArray(doc?.result)) { diagnostics.push({ status: 'unavailable', reason: 'coverage-report-invalid' }); continue }
    for (const sc of doc.result ?? []) {
      if (!String(sc.url).startsWith('file://')) continue
      let abs
      try { abs = fileURLToPath(sc.url) } catch { continue }
      if (abs.includes(`${sep}node_modules${sep}`)) continue
      const rel = mapToRepo(abs, srcRoot, idx) ?? sameFileByName(abs, sc, srcRoot, idx)
      if (!rel || !isCoverageSource(rel)) continue
      ;(byFile.get(rel) ?? byFile.set(rel, []).get(rel)).push(sc.functions ?? [])
    }
  }
  const files = {}
  for (const [rel, runs] of byFile) {
    let src
    try { src = readFileSync(join(srcRoot, rel), 'utf8') } catch { continue }
    const hit = new Uint8Array(src.length)
    let validRuns = 0
    for (const fns of runs) {
      // 脚本长度和仓里源文件不一致（转译过的 TS、被改过的副本）：偏移对不上，这次运行的数据不认
      if (Math.max(0, ...fns.flatMap((fn) => fn.ranges ?? []).map((r) => r.endOffset)) !== src.length) continue
      validRuns++
      const cnt = new Int32Array(src.length).fill(-1)
      const ranges = fns.flatMap((fn) => fn.ranges ?? []).sort((a, b) => (b.endOffset - b.startOffset) - (a.endOffset - a.startOffset))
      for (const r of ranges) cnt.fill(r.count, Math.max(0, r.startOffset), Math.min(r.endOffset, src.length))
      for (let i = 0; i < src.length; i++) if (cnt[i] > 0) hit[i] = 1
    }
    if (!validRuns) { diagnostics.push({ file: rel, status: 'not_measurable', reason: 'source-length-mismatch' }); continue }
    const lines = [], cov = []
    let off = 0
    src.split('\n').forEach((text, i) => {
      if (looksLikeCode(text)) {
        lines.push(i + 1)
        // 行尾最后一个非空字符所在的块执行过才算（`export function f() {` 的声明在顶层执行，但函数体没进去）
        const k = off + text.trimEnd().length - 1
        if (hit[k]) cov.push(i + 1)
      }
      off += text.length + 1
    })
    files[rel] = { lines, covered: cov, source: 'v8' }
  }
  return files
}

export function readV8Report(dir, srcRoot, options = {}) {
  const diagnostics = []
  let files = {}
  try { files = readV8Dir(dir, srcRoot, { ...options, diagnostics }) }
  catch { diagnostics.push({ status: 'unavailable', reason: 'coverage-report-unreadable' }) }
  const failures = diagnostics.filter((d) => d.status === 'unavailable')
  const sources = [{ provider: 'v8', ...(options.stepId ? { step: options.stepId } : {}) }]
  const evidence = Object.fromEntries(Object.entries(files).map(([file, fc]) => [file, { ...fc, load_status: 'loaded', sources }]))
  return { status: failures.length ? 'unavailable' : 'ok', kind: 'v8', total: null, files, evidence,
    failures, unmeasurable: diagnostics.filter((d) => d.status === 'not_measurable'),
    ...(failures.length ? { reason: failures[0].reason } : {}) }
}

// 主报告（vitest istanbul）里没有的文件，用 V8 数据补上；主报告里有的文件，子进程跑到的行也算覆盖（只认主报告的可执行行）
export function mergeCoverageFiles(primary, ...extras) {
  const out = { ...(primary ?? {}) }
  for (const ex of extras) for (const [f, fc] of Object.entries(ex ?? {})) {
    if (primary?.[f]) {
      const exec = new Set(primary[f].lines)
      out[f] = { ...out[f], ...mergeFileEvidence(out[f], fc), covered: [...new Set([...out[f].covered, ...fc.covered.filter((l) => exec.has(l))])].sort((a, b) => a - b) }
      continue
    }
    const cur = out[f]
    out[f] = cur ? { ...cur, ...mergeFileEvidence(cur, fc), lines: [...new Set([...cur.lines, ...fc.lines])].sort((a, b) => a - b), covered: [...new Set([...cur.covered, ...fc.covered])].sort((a, b) => a - b), source: cur.source ?? fc.source ?? 'v8' } : fc
  }
  return out
}

function mergeFileEvidence(a, b) {
  const out = {}
  for (const key of ['sources', 'failures', 'spans']) {
    if (a[key] || b[key]) out[key] = [...new Map([...(a[key] ?? []), ...(b[key] ?? [])].map((v) => [JSON.stringify(v), v])).values()]
  }
  for (const key of ['branches', 'functions']) {
    if (!a[key] && !b[key]) continue
    const ranges = new Map()
    for (const r of [...(a[key] ?? []), ...(b[key] ?? [])]) {
      const k = r.key ?? `${r.start}-${r.end}`
      ranges.set(k, { ...r, covered: r.covered || ranges.get(k)?.covered || false })
    }
    out[key] = [...ranges.values()]
  }
  if (a.load_status || b.load_status) out.load_status = [a.load_status, b.load_status].includes('loaded') || a.source === 'v8' || b.source === 'v8' ? 'loaded' : a.load_status ?? b.load_status
  return out
}

export const NEW_CODE_METHOD = 'ming-qa newCode = line coverage of new code. Changed lines: git diff <base_sha> <tested_sha> (merged: first parent of the merge commit; open: merge-base with the target branch); '
  + 'added/modified lines in non-test JS/TS source files are intersected with v8 statement start lines from coverage-final.json; a code-like changed line that is not a statement start but lies inside a multi-line statement or a branch range counts as the innermost such range; '
  + 'lines that are not executable statements are ignored; files vitest did not load are taken from raw NODE_V8_COVERAGE data of the same run (node --test steps and child processes; code-like lines, covered = the last non-blank character of the line is inside a V8 block with count > 0; copies run from a temp dir are mapped back by path suffix, or by a repo-unique file name when the script length matches; for files vitest did load, lines covered only by child processes are added to covered); '
  + 'files with no data at all count their code-like added lines as uncovered, except plain .js/.mjs/.cjs files (node entry scripts / host entry), which are listed as not_measurable and left out of pct. '
  + 'pct = covered / executable changed lines; null (not evaluated) when the diff has no executable source lines.'

// blocking: false / level: 'info'（2026-10-26）：status 名不改（下游兼容），但覆盖率永不阻断、不进台账。
// 结论：新代码覆盖率低于参考线 → status fail（只是信息，不出 finding）；拿不到覆盖率 → unavailable（不判 fail，单独说明）
// 只拿新代码覆盖率和参考线比。全仓 lines / branches / functions 只作基线趋势数据：
// 记在 baseline 里（附参考线 BASELINE_REFERENCE），results 里标 'baseline'，永远不让门禁失败、不产生 finding。
export const GATED_METRICS = Object.freeze(['new_code'])
export const BASELINE_METRICS = Object.freeze(['lines', 'branches', 'functions'])
export function coverageGate({ coverage, added, thresholds = COVERAGE_THRESHOLDS, stepId = 'unit', diffError = null }) {
  const gated = Object.fromEntries(GATED_METRICS.map((k) => [k, thresholds[k]]))
  const reference = Object.fromEntries(BASELINE_METRICS.map((k) => [k, thresholds[k]]))
  const gate = { step: stepId, provider: 'v8 (vitest --coverage)', thresholds_source: COVERAGE_SOURCE, thresholds: gated, gated_metrics: [...GATED_METRICS], baseline: null, values: null, results: null, status: 'unavailable', pass: null, new_code: null, new_code_method: NEW_CODE_METHOD, reason: null, blocking: false, level: 'info' }
  if (!coverage || coverage.status !== 'ok') { gate.reason = coverage?.reason ?? 'unit step did not run with coverage'; return gate }
  const nc = diffError || !coverage.files ? null : newCodeCoverage(added, mergeCoverageFiles(coverage.evidence, coverage.files))
  gate.new_code = nc ?? { pct: null, reason: diffError ?? 'per-line coverage unavailable' }
  gate.values = { lines: coverage.total?.lines ?? null, branches: coverage.total?.branches ?? null, functions: coverage.total?.functions ?? null, new_code: nc?.pct ?? null }
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
  if (notEvaluated) gate.reason = `new-code coverage not evaluated: ${gate.new_code?.reason ?? (gate.new_code?.executable_lines === 0 ? 'the diff adds no executable lines in non-test JS/TS sources' : 'unknown')}`
  return gate
}

// ── PR 自己写明的覆盖率目标（统一分级「P2 必修的三类」第 2 类：自己定的验收没达成）──────────────
// 只认紧挨在百分数前面的目标说法：比较符（≥ / >= / >）或「目标 / 门槛 / 不低于 / 不少于 / 至少」、at least / target / threshold /
// minimum / no less than，中间只能有空白、冒号、「为 / 是」。对现状的描述（「当前覆盖率 81%」「覆盖率达到 95%」「96%，达到要求」）不算。
// 指标按每个百分数就近判断（同一小句里的说法，没有就取句中离它最近的说法）：新代码 / 增量 / 本 PR / new code / new lines → new_code；
// 写了「全仓 / 整体 / overall / total …」才按全仓行覆盖率（lines）比 —— 只写 line coverage、行覆盖率不按全仓比；
// 分支 → branches、函数 → functions（全仓数据）；新代码的分支 / 函数覆盖率 QA 不测 → new_code_branches / new_code_functions（not-evaluated）；
// 没说 → new_code。
const COV_WORD = /覆盖率|coverage/i
const PCT = /(\d{1,3}(?:\.\d+)?)\s*[%％]/g
const TARGET_BEFORE = /(?:[≥⩾]|>=|＞=|[>＞]|不低于|不少于|至少|目标(?:值)?|门槛|at\s+least|no\s+less\s+than|minimum(?:\s+of)?|targets?(?:\s+of)?|threshold(?:\s+of)?)\s*(?:[:：=]|为|是|is)?\s*$/i
const METRIC_WORDS = [
  ['new', /新代码|新增代码|增量|本\s*PR|改动(?:的)?(?:代码|行)|新增行|new[-\s]?code|new\s+lines?|changed\s+(?:code|lines?|files?)|\bdiff\b|\bpatch\b/gi],
  ['repo', /全仓|整体|整个仓库|总体|全量|overall|\btotal\b|whole[-\s]?repo|repo[-\s]?wide|project[-\s]?wide|entire\s+(?:repo|project|codebase)/gi],
  ['branch', /分支|branch(?:es)?/gi],
  ['func', /函数|functions?\b/gi],
]
const CLAUSE_SEP = /[，,；;。|｜（）()【】[\]]/
function metricWords(text) {
  const hits = []
  for (const [kind, re] of METRIC_WORDS) for (const m of String(text).matchAll(re)) hits.push({ kind, index: m.index })
  return hits
}
function metricFor(line, index, length) {
  // 所在小句：前后最近的分隔符之间
  let s = index, e = index + length
  while (s > 0 && !CLAUSE_SEP.test(line[s - 1])) s--
  while (e < line.length && !CLAUSE_SEP.test(line[e])) e++
  let kinds = new Set(metricWords(line.slice(s, e)).map((h) => h.kind))
  if (!kinds.size) {
    // 小句里没说：取整句里离这个百分数最近的说法（先看前面）
    const hits = metricWords(line)
    const before = hits.filter((h) => h.index < index).sort((a, b) => b.index - a.index)[0]
    const after = hits.filter((h) => h.index > index).sort((a, b) => a.index - b.index)[0]
    const near = before ?? after
    kinds = new Set(near ? [near.kind] : [])
  }
  const isNew = kinds.has('new')
  if (kinds.has('branch')) return isNew ? 'new_code_branches' : 'branches'
  if (kinds.has('func')) return isNew ? 'new_code_functions' : 'functions'
  if (kinds.has('repo') && !isNew) return 'lines'
  return 'new_code'
}
export function statedCoverageTarget(sources) {
  for (const { source, text } of sources ?? []) {
    let fence = false
    for (const raw of String(text ?? '').replace(/<!--[\s\S]*?-->/g, '').split(/\r?\n/)) {
      if (/^\s*(```|~~~)/.test(raw)) { fence = !fence; continue }
      if (fence || !COV_WORD.test(raw)) continue
      // 引用块的「>」不是比较符
      const line = raw.replace(/^\s*(?:>\s*)+/, '').trim()
      for (const m of line.matchAll(PCT)) {
        const pct = Number(m[1])
        if (!(pct > 0 && pct <= 100)) continue
        if (!TARGET_BEFORE.test(line.slice(Math.max(0, m.index - 24), m.index))) continue
        return { pct, metric: metricFor(line, m.index, m[0].length), source, quote: line.slice(0, 300) }
      }
    }
  }
  return null
}
// 目标对实测：实测拿不到（或是 QA 不测的指标）→ not-evaluated（不出 finding）；低于目标 → missed
export function evaluateStatedTarget(target, gate) {
  if (!target) return null
  if (target.metric === 'new_code_branches' || target.metric === 'new_code_functions') {
    return { ...target, value: null, status: 'not-evaluated', reason: 'the PR states a new-code branch / function coverage target; QA measures new-code line coverage only' }
  }
  const value = gate?.values?.[target.metric] ?? null
  if (value == null) return { ...target, value: null, status: 'not-evaluated', reason: gate?.reason ?? 'coverage not measured' }
  return { ...target, value, status: value < target.pct ? 'missed' : 'met' }
}
