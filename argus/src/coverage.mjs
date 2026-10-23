// 白盒覆盖率：单测步骤用 vitest --coverage（v8）跑，读 coverage-summary.json（总体）与 coverage-final.json
// （逐行），再与 PR diff 的新增行求交，得到「新代码覆盖率」。数字照常写进报告当信息（和参考线 90 比出 pass / fail）。
// 统一分级（Praise 2026-10-23）：覆盖率低于参考线本身不是问题，不出 finding；只有 PR 正文或 docs/pr/<N>/qa.md 自己写明了
// 覆盖率目标却没达到，才按「自己定的验收没达成」记一条 P2（statedCoverageTarget + evaluateStatedTarget）。
import { TEST_HELPER_RE } from './classify.mjs'
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
export const isCoverageSource = (file) => SOURCE_RE.test(file) && !NOT_SOURCE_RE.test(file) && !TEST_HELPER_RE.test(file)
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
    if (fc) {
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
      perFile.push({ file, executable: e.length, covered: c.length, uncovered_lines: e.filter((r) => !cov.has(r.line)).map((r) => r.line).slice(0, 50), ...(fc.source ? { source: fc.source } : {}) })
    } else {
      const e = rows.filter((r) => looksLikeCode(r.text))
      if (!e.length) continue
      if (PLAIN_JS_RE.test(file)) {
        notMeasurable.push(file)
        perFile.push({ file, executable: e.length, covered: null, not_measurable: true, reason: 'not loaded by vitest and no V8 data from node --test / subprocess runs' })
        continue
      }
      executable += e.length
      unloaded.push(file)
      perFile.push({ file, executable: e.length, covered: 0, not_loaded_by_tests: true, uncovered_lines: e.map((r) => r.line).slice(0, 50) })
    }
  }
  return { pct: executable ? Math.round((covered / executable) * 10000) / 100 : null, executable_lines: executable, covered_lines: covered, unloaded_files: unloaded, not_measurable_files: notMeasurable, files: perFile }
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
export function readV8Dir(dir, srcRoot, { index = null, maxBytes = 512 * 1024 * 1024 } = {}) {
  if (!dir || !existsSync(dir)) return {}
  const idx = index ?? repoFileIndex(srcRoot)
  const byFile = new Map()
  let bytes = 0
  for (const name of readdirSync(dir).filter((n) => n.endsWith('.json'))) {
    const f = join(dir, name)
    bytes += statSync(f).size
    if (bytes > maxBytes) break
    let doc
    try { doc = JSON.parse(readFileSync(f, 'utf8')) } catch { continue }
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
    for (const fns of runs) {
      // 脚本长度和仓里源文件不一致（转译过的 TS、被改过的副本）：偏移对不上，这次运行的数据不认
      if (Math.max(0, ...fns.flatMap((fn) => fn.ranges ?? []).map((r) => r.endOffset)) !== src.length) continue
      const cnt = new Int32Array(src.length).fill(-1)
      const ranges = fns.flatMap((fn) => fn.ranges ?? []).sort((a, b) => (b.endOffset - b.startOffset) - (a.endOffset - a.startOffset))
      for (const r of ranges) cnt.fill(r.count, Math.max(0, r.startOffset), Math.min(r.endOffset, src.length))
      for (let i = 0; i < src.length; i++) if (cnt[i] > 0) hit[i] = 1
    }
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

// 主报告（vitest istanbul）里没有的文件，用 V8 数据补上；主报告里有的文件，子进程跑到的行也算覆盖（只认主报告的可执行行）
export function mergeCoverageFiles(primary, ...extras) {
  const out = { ...(primary ?? {}) }
  for (const ex of extras) for (const [f, fc] of Object.entries(ex ?? {})) {
    if (primary?.[f]) {
      const exec = new Set(primary[f].lines)
      out[f] = { ...out[f], covered: [...new Set([...out[f].covered, ...fc.covered.filter((l) => exec.has(l))])].sort((a, b) => a - b) }
      continue
    }
    const cur = out[f]
    out[f] = cur ? { lines: [...new Set([...cur.lines, ...fc.lines])].sort((a, b) => a - b), covered: [...new Set([...cur.covered, ...fc.covered])].sort((a, b) => a - b), source: 'v8' } : fc
  }
  return out
}

export const NEW_CODE_METHOD = 'ming-qa newCode = line coverage of new code. Changed lines: git diff <base_sha> <tested_sha> (merged: first parent of the merge commit; open: merge-base with the target branch); '
  + 'added/modified lines in non-test JS/TS source files are intersected with v8 statement start lines from coverage-final.json; a code-like changed line that is not a statement start but lies inside a multi-line statement or a branch range counts as the innermost such range; '
  + 'lines that are not executable statements are ignored; files vitest did not load are taken from raw NODE_V8_COVERAGE data of the same run (node --test steps and child processes; code-like lines, covered = the last non-blank character of the line is inside a V8 block with count > 0; copies run from a temp dir are mapped back by path suffix, or by a repo-unique file name when the script length matches; for files vitest did load, lines covered only by child processes are added to covered); '
  + 'files with no data at all count their code-like added lines as uncovered, except plain .js/.mjs/.cjs files (node entry scripts / host entry), which are listed as not_measurable and left out of pct. '
  + 'pct = covered / executable changed lines; null (not evaluated) when the diff has no executable source lines.'

// 结论：新代码覆盖率低于参考线 → status fail（只是信息，不出 finding）；拿不到覆盖率 → unavailable（不判 fail，单独说明）
// 只拿新代码覆盖率和参考线比。全仓 lines / branches / functions 只作基线趋势数据：
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
  if (notEvaluated) gate.reason = `new-code coverage not evaluated: ${gate.new_code?.reason ?? (gate.new_code?.executable_lines === 0 ? 'the diff adds no executable lines in non-test JS/TS sources' : 'unknown')}`
  return gate
}

// ── PR 自己写明的覆盖率目标（统一分级「P2 必修的三类」第 2 类：自己定的验收没达成）──────────────
// 只认写成「目标 / 门槛」的说法（≥ / 不低于 / 至少 / 以上 / 目标 / 要求 / at least / target …），不认对现状的描述（「当前覆盖率 81%」）。
// 指标：提到新代码 / 增量 / 本 PR → new_code；全仓 / 整体 / overall → lines；分支 → branches；函数 → functions；没说 → new_code。
const COV_WORD = /覆盖率|coverage/i
const TARGET_WORD = /[≥>]=?|不低于|不少于|至少|以上|达到|目标|门槛|要求|at\s+least|minimum|target|threshold|no\s+less\s+than|\+\s*$/i
const PCT = /(\d{1,3}(?:\.\d+)?)\s*%/g
function metricOf(line) {
  if (/新代码|新增代码|增量|改动|本\s*PR|new[-\s]code|changed|diff|patch/i.test(line)) return 'new_code'
  if (/分支|branch/i.test(line)) return 'branches'
  if (/函数|function/i.test(line)) return 'functions'
  if (/全仓|整体|整个仓库|总体|overall|total|repo|project|lines?\b|行覆盖/i.test(line)) return 'lines'
  return 'new_code'
}
export function statedCoverageTarget(sources) {
  for (const { source, text } of sources ?? []) {
    let fence = false
    for (const raw of String(text ?? '').replace(/<!--[\s\S]*?-->/g, '').split(/\r?\n/)) {
      if (/^\s*(```|~~~)/.test(raw)) { fence = !fence; continue }
      if (fence || !COV_WORD.test(raw)) continue
      const line = raw.trim()
      // 百分号前后 12 个字符内要有目标说法（「≥ 90%」「90% 以上」「目标 90%」）
      for (const m of line.matchAll(PCT)) {
        const pct = Number(m[1])
        if (!(pct > 0 && pct <= 100)) continue
        const around = line.slice(Math.max(0, m.index - 12), m.index + m[0].length + 6)
        if (!TARGET_WORD.test(around)) continue
        return { pct, metric: metricOf(line), source, quote: line.slice(0, 300) }
      }
    }
  }
  return null
}
// 目标对实测：实测拿不到 → not-evaluated（不出 finding）；低于目标 → missed
export function evaluateStatedTarget(target, gate) {
  if (!target) return null
  const value = target.metric === 'new_code' ? gate?.values?.new_code ?? null : gate?.values?.[target.metric] ?? null
  if (value == null) return { ...target, value: null, status: 'not-evaluated', reason: gate?.reason ?? 'coverage not measured' }
  return { ...target, value, status: value < target.pct ? 'missed' : 'met' }
}
