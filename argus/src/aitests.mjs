// AI 补写针对性测试：分诊说「涉及」、但现有检查覆盖不到的方面，让 gpt-6-luna 用项目自己的测试框架补写测试。
// 每个补测都要带一个「植入错误」：测试在当前代码上通过、植入错误后失败，才算有效；否则丢弃。
// 补测发现的问题在试运行期只记「拿不准，要你看」，不自动提 issue。
import { aiUsageToday, addAiUsage } from './db.mjs'
import { globToRegExp } from './classify.mjs'
import { toDimensionLabel } from './matrix.mjs'
import { redact, toShanghaiDay, truncate } from './util.mjs'
import { toChatCompletionsUrl } from './util.mjs'

const MODEL = 'openai/gpt-6-luna'
const EFFORT = { B: 'high', C: 'xhigh' }
const DAILY_CAP = Number(process.env.ARGUS_AITEST_DAILY_CAP ?? 20)
const MAX_TESTS = 2 // Mac 流程（control.mjs）的默认值；夜间流程按 aiTestCaps().maxTestsPerCall 传入
const MAX_TEST_CHARS = 20_000
const DIFF_CAP = 40_000
const SKIP_DIFF = /(^|\/)(package-lock\.json|uv\.lock|pnpm-lock\.yaml|yarn\.lock)$|\.(png|jpg|jpeg|gif|webp|svg|ico|mp4|woff2?)$/i

const SYSTEM = `你是资深测试工程师。给一个已合并的 PR 补写针对性自动化测试，只用项目已有的测试框架，不引入新依赖。
要求：
1. 每个测试只验证一件事，断言明确；不访问网络、不读写测试目录以外的文件、不依赖真实账号。
2. 测试必须在「PR 合并后的正确代码」上通过。
3. 每个测试必须附一个「植入错误」：对某个被测源文件做一次文本替换（find 在文件中恰好出现一次），替换后测试应当失败。用来证明测试真的测到了东西。
4. 如果改动和 PR 目标或产品意图看起来矛盾，可以写一个验证预期行为的测试，并把 kind 标为 suspicion。
5. 写不出可靠测试就返回空数组，不要硬写。
只输出 JSON：{"tests":[{"kind":"coverage|suspicion","purposeZh":"一句人话说明测什么","covers":["维度 key"],"filePath":"新测试文件路径","content":"完整测试代码","mutation":{"file":"源文件路径","find":"原文片段","replace":"植入错误后的片段"}}]}`

function buildDiff(files) {
  let out = ''
  for (const f of files) {
    if (SKIP_DIFF.test(f.filename)) continue
    out += `\n--- ${f.filename} (+${f.additions} -${f.deletions})\n${f.patch ?? '（无文本差异）'}\n`
    if (out.length > DIFF_CAP) break
  }
  return truncate(out, DIFF_CAP)
}

// 需要补测的方面：分诊判「涉及」，且本次要跑的步骤里没有一步声明覆盖它
export function toGapDimensions({ profile, triage, stepIds }) {
  const planned = new Set(stepIds ?? profile.steps.map((s) => s.id))
  const covered = new Set(profile.steps.filter((s) => planned.has(s.id)).flatMap((s) => s.covers ?? []))
  return (triage?.rows ?? []).filter((r) => r.verdict === 'applicable' && !covered.has(r.key)).map((r) => r.key)
}

// 校验模型给出的测试：路径必须落在项目允许的补测位置，覆盖的维度必须在缺口里，植入错误要完整
export function toValidTests(raw, { profile, gaps, maxTests = MAX_TESTS }) {
  const allowed = (profile.aiTests?.pathGlobs ?? []).map(globToRegExp)
  const gapSet = new Set(gaps)
  const out = []
  for (const t of Array.isArray(raw?.tests) ? raw.tests : []) {
    if (out.length >= maxTests) break
    const path = String(t.filePath ?? '')
    if (!path || path.includes('..') || path.startsWith('/') || !allowed.some((re) => re.test(path))) continue
    if (typeof t.content !== 'string' || !t.content.trim() || t.content.length > MAX_TEST_CHARS) continue
    const m = t.mutation ?? {}
    if (!m.file || String(m.file).includes('..') || String(m.file).startsWith('/') || typeof m.find !== 'string' || !m.find || typeof m.replace !== 'string' || m.find === m.replace) continue
    const covers = (Array.isArray(t.covers) ? t.covers : []).filter((k) => gapSet.has(k))
    if (!covers.length && t.kind !== 'suspicion') continue
    out.push({ kind: t.kind === 'suspicion' ? 'suspicion' : 'coverage', purposeZh: String(t.purposeZh ?? '补写的测试').slice(0, 60), covers, filePath: path, content: t.content, mutation: { file: m.file, find: m.find, replace: m.replace } })
  }
  return out
}

// 补测变成普通步骤：放在所有步骤最后跑，免得新文件让别的检查看到「工作区不干净」
export function toAiTestSteps(tests, profile, { offset = 0 } = {}) {
  return tests.map((t, j) => { const i = j + offset; return {
    id: `ai-test-${i + 1}`,
    nameZh: `AI 补测：${t.purposeZh}`,
    kind: 'product',
    aiGenerated: true,
    aiKind: t.kind,
    covers: t.covers,
    unavailableExitCodes: [2, 4],
    timeoutMin: 10,
    cmd: ['node', '${RUN}/tools/ai-test-run.mjs', `\${RUN}/ai-test-${i + 1}.json`],
    aiTest: { ...t, runCmd: profile.aiTests.run, runFileStrip: profile.aiTests.runFileStrip ?? '' },
  } })
}

// 模型回包 → JSON。回包缺 choices（网关没给内容）照旧当空对象；但 content 是空串或半截 JSON
// （常见于推理把 max_completion_tokens 用完，finish_reason=length）以前直接 JSON.parse 抛
// 「Unexpected end of JSON input」，原因看不出来（PR #966，2026-10-14）。现在明确说出是空内容还是被截断，
// 并标 retryable：同一 PR 还有调用次数时，nightly 会再试一轮。
export function parseModelJson(data) {
  const choice = data?.choices?.[0]
  const finish = choice?.finish_reason ?? null
  const content = choice?.message?.content
  if (content == null) return { ok: true, value: {}, finish_reason: finish }
  const text = String(content)
  const fr = finish ? `，finish_reason=${finish}` : ''
  if (!text.trim()) return { ok: false, kind: 'empty', finish_reason: finish, reasonZh: `AI 补测失败（模型返回空内容${fr}）` }
  try { return { ok: true, value: JSON.parse(text), finish_reason: finish } }
  catch { return { ok: false, kind: 'truncated', finish_reason: finish, reasonZh: `AI 补测失败（模型返回的 JSON 不完整${fr}）` } }
}

export async function generateTargetedTests({ db, secrets, riskClass, pr, files, profile, gaps, now = new Date(), maxTests = MAX_TESTS, highRiskFiles = [], feedback = [], timeoutMs = 8 * 60_000, quota = null, fetchImpl = fetch }) {
  if (!profile.aiTests) return { status: 'skipped', reasonZh: '这个项目还没配置 AI 补测', tests: [] }
  if (riskClass !== 'B' && riskClass !== 'C') return { status: 'skipped', reasonZh: '只改文档', tests: [] }
  if (!gaps.length) return { status: 'skipped', reasonZh: '涉及的方面现有检查都覆盖了', tests: [] }
  if (!secrets.ARGUS_LLM_BASE_URL || !secrets.ARGUS_LLM_API_KEY) return { status: 'skipped', reasonZh: '模型凭证没配置', tests: [] }
  const day = `gen:${toShanghaiDay(now)}`
  // 夜间流程传 quota（按种类 / 池子分开计数，见 quota.mjs）；Mac 流程（control.mjs）沿用旧的单一日上限
  if (quota) {
    const q = quota.check('ai-test')
    if (!q.ok) return { status: 'capped', reasonZh: `ai-test daily cap reached (${q.pool} pool ${q.used}/${q.cap})`, quota: q, tests: [] }
  } else if (aiUsageToday(db, day).calls >= DAILY_CAP) return { status: 'skipped', reasonZh: `今天的 AI 补测额度（${DAILY_CAP} 次）用完了`, tests: [] }
  const user = redact([
    `项目：${profile.displayName ?? ''}`,
    `产品意图：${profile.intentZh ?? '（没写）'}`,
    `测试框架：${profile.aiTests.frameworkZh}；新测试文件路径必须匹配：${profile.aiTests.pathGlobs.join('、')}`,
    `需要补测的方面（维度 key → 含义）：${gaps.map((k) => `${k} → ${toDimensionLabel(k)}`).join('；')}`,
    `最多写 ${maxTests} 个测试。`,
    highRiskFiles.length ? `高风险改动（C 类路径，优先给这些写测试）：${highRiskFiles.slice(0, 30).join('、')}` : null,
    feedback.length ? `上一轮写的测试没有通过有效性验证（不要重复同样的写法）：\n${feedback.map((f) => `- ${f}`).join('\n')}` : null,
    `PR 标题：${pr.title}`,
    `PR 描述：\n${truncate(pr.body ?? '', 4000)}`,
    `改动内容：${buildDiff(files)}`,
  ].filter((x) => x != null).join('\n\n'))
  try {
    const resp = await fetchImpl(toChatCompletionsUrl(secrets.ARGUS_LLM_BASE_URL), {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${secrets.ARGUS_LLM_API_KEY}` },
      body: JSON.stringify({ model: MODEL, reasoning_effort: EFFORT[riskClass], response_format: { type: 'json_object' }, max_completion_tokens: 16_000, messages: [{ role: 'system', content: SYSTEM }, { role: 'user', content: user }] }),
      signal: AbortSignal.timeout(Math.max(30_000, Math.floor(Number(timeoutMs)) || 30_000)),
    })
    const data = await resp.json().catch(() => ({}))
    if (quota) quota.consume('ai-test', data?.usage?.total_tokens ?? 0)
    else addAiUsage(db, day, data?.usage?.total_tokens ?? 0)
    if (!resp.ok) return { status: 'error', reasonZh: `模型网关返回 ${resp.status}`, tests: [] }
    const parsed = parseModelJson(data)
    if (!parsed.ok) return { status: 'error', retryable: true, reasonZh: parsed.reasonZh, finish_reason: parsed.finish_reason, usage: data?.usage ?? null, model: MODEL, tests: [] }
    const raw = parsed.value
    const tests = toValidTests(raw, { profile, gaps, maxTests })
    const offered = Array.isArray(raw?.tests) ? raw.tests.length : 0
    return { status: tests.length ? 'ok' : 'empty', reasonZh: tests.length ? null : 'AI 没写出可用的测试', tests, offered, rejected_by_validation: Math.max(0, offered - tests.length), usage: data?.usage ?? null, model: MODEL }
  } catch (err) {
    return { status: 'error', reasonZh: `AI 补测失败（${redact(err.message).slice(0, 80)}）`, tests: [] }
  }
}

// ── 夜间流程（Linux）用的补测编排参数与纯函数 ───────────────────────
// 补测只针对这些「现有检查常覆盖不到」的维度（Jev 判为 applicable 且没有已跑的检查声明覆盖）
export const AI_GAP_DIMENSIONS = Object.freeze(['integration', 'boundary', 'exception', 'security', 'permission-boundary', 'state-transition', 'async-callback-retry', 'data-consistency', 'concurrency', 'business-rule', 'privacy', 'db'])

// 每个 PR 的上限（环境变量可调）：模型调用次数、总墙钟时间（生成 + 执行）、每次调用最多几个测试
export function aiTestCaps(env = process.env) {
  const num = (v, d, min = 0) => { const n = Number(v); return Number.isFinite(n) && n >= min ? n : d }
  return {
    enabled: env.ARGUS_AITESTS !== '0',
    maxCalls: num(env.ARGUS_AITEST_MAX_CALLS, 2, 0),
    maxWallMin: num(env.ARGUS_AITEST_MAX_MIN, 25, 1),
    maxTestsPerCall: num(env.ARGUS_AITEST_MAX_TESTS, 3, 1),
  }
}

// 缺口维度：适用（Jev 明确判定 applicable）∩ 补测维度清单 ∩ 没有已跑成（pass / fail）的检查声明覆盖
export function toAiGaps({ applicability, profile, results }) {
  const ran = new Set((results ?? []).filter((r) => r.kind !== 'setup' && !r.unavailable && !r.timedOut).map((r) => r.id))
  const covered = new Set(profile.steps.filter((s) => ran.has(s.id)).flatMap((s) => s.covers ?? []))
  return (applicability?.applicable ?? []).filter((k) => AI_GAP_DIMENSIONS.includes(k) && !covered.has(k))
}

// 「测试无效」：补测在当前代码上失败，但失败原因是测试自己没备好前提（缺测试数据 / 文件、导入了不存在的模块或导出），
// 还没走到断言。这类失败说明不了被测代码有问题 → 按 discarded 处理（invalid: true，显示「测试无效」），不产生 finding。
// 只要输出里有断言失败（AssertionError / expected … to …）就不算无效：例如 expect(...).toThrow('X') 却收到 ENOENT，仍是 fails-on-current。
export const AI_TEST_INVALID_ZH = '测试无效'
// missing-module 也包括测试自己的 vi.mock 工厂少导出了被测代码要用的函数（vitest: No "x" export is defined on the "…" mock，#915 10-15）
const INVALID_SIGNATURES = [
  { kind: 'missing-file', re: /\bENOENT\b|no such file or directory/i },
  { kind: 'missing-module', re: /Cannot find module ['"]|Cannot find package ['"]|Failed to resolve import|Failed to load url|Does the file exist\?|does not provide an export named|No "[^"]+" export is defined on the "[^"]+" mock/ },
]
const ASSERTION_FAILURE = /AssertionError|\bexpected\b[^\n]*\bto (be|equal|throw|have|contain|match|deep|strictly|include|satisfy)/i
export function toInvalidTest(tail) {
  const text = String(tail ?? '').replace(/\x1b\[[0-9;]*m/g, '')
  if (!text || ASSERTION_FAILURE.test(text)) return null
  for (const { kind, re } of INVALID_SIGNATURES) {
    const line = text.split('\n').find((l) => re.test(l))
    if (line) return { kind, line: line.trim().slice(0, 240) }
  }
  return null
}

// 补测执行结果 → 判定：kept（当前代码通过且植入错误后失败）/ discarded（植入错误对不上、植入后仍通过、没法跑、测试无效）/
// fails-on-current（在当前代码上就失败：可能是缺陷，P2、test_backed=false，复现后才可升级）
export function toAiTestResult(r) {
  if (!r) return { result: 'discarded', reason: 'not run (time budget / setup failed)' }
  if (r.timedOut) return { result: 'discarded', reason: 'timed out' }
  if (r.ok) return { result: 'kept', reason: 'passes on the tested commit and fails with the injected bug' }
  if (r.unavailable && r.exitCode === 4) return { result: 'discarded', reason: 'mutation check failed (injected bug not caught, or mutation did not match the source)' }
  if (r.unavailable) return { result: 'discarded', reason: `could not run: ${r.unavailableReasonZh ?? `exit ${r.exitCode}`}` }
  const invalid = toInvalidTest(r.tail)
  if (invalid) return { result: 'discarded', invalid: true, invalid_kind: invalid.kind, display_zh: AI_TEST_INVALID_ZH,
    reason: `test invalid (${AI_TEST_INVALID_ZH}): failed before any assertion because the test's own precondition is missing (${invalid.kind}: ${invalid.line})` }
  return { result: 'fails-on-current', reason: 'fails on the tested commit' }
}

// ── 分级补测上限（按 PR 类型 + 改动规模）────────────────────────────
// 类型：docs（文档 / 变更日志 / 纯配置）→ 不补测；ci（只改 CI/workflow）→ S；tests（只改测试）→ S；
//       deps（只改锁文件 / 依赖清单）→ S；product（有产品源码）→ 按产品代码改动行数 + 文件数分 S/M/L/XL。
// 行数 = additions + deletions，不计锁文件和生成文件。环境变量 ARGUS_AITEST_MAX_* 显式给出时覆盖对应上限（reason 里注明）。
export const AI_TEST_TIERS = Object.freeze({
  none: { maxCalls: 0, maxTestsPerCall: 0, maxWallMin: 0 },
  S: { maxCalls: 1, maxTestsPerCall: 2, maxWallMin: 10 },
  M: { maxCalls: 2, maxTestsPerCall: 3, maxWallMin: 20 },
  L: { maxCalls: 3, maxTestsPerCall: 3, maxWallMin: 30 },
  XL: { maxCalls: 4, maxTestsPerCall: 4, maxWallMin: 40 },
})
const TIER_RE = {
  lock: /(^|\/)(package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|bun\.lockb?|uv\.lock|poetry\.lock|Pipfile\.lock|Cargo\.lock|go\.sum|composer\.lock|Gemfile\.lock)$/,
  generated: /(^|\/)(dist|build|out|coverage|vendor|node_modules|__generated__|generated)\/|\.min\.(js|css)$|\.map$|\.snap$|\.generated\.|\.pb\.go$|_pb2\.py$/,
  docs: /\.(md|mdx|rst|txt|adoc)$|^docs\/|(^|\/)(LICENSE|CHANGELOG|CHANGES|NOTICE|AUTHORS)(\.[^/]*)?$/i,
  ci: /^\.github\/|^\.gitlab-ci\.yml$|^\.circleci\/|(^|\/)Jenkinsfile$|^\.buildkite\//,
  tests: /(^|\/)(tests?|__tests__|spec|e2e|fixtures)\/|\.(test|spec)\.[^/]+$|(^|\/)test_[^/]+\.py$|_test\.(go|py)$/,
  depManifest: /(^|\/)(package\.json|pyproject\.toml|requirements[^/]*\.txt|Cargo\.toml|go\.mod|Gemfile)$/,
  config: /(^|\/)\.[^/]+rc(\.[^/]+)?$|(^|\/)\.(editorconfig|gitignore|gitattributes|npmrc|nvmrc|prettierignore|eslintignore|dockerignore)$|\.(ya?ml|toml|ini|cfg|conf|env\.example)$|(^|\/)(tsconfig|jsconfig)[^/]*\.json$/,
}
export function fileKind(path) {
  if (TIER_RE.lock.test(path)) return 'lock'
  if (TIER_RE.generated.test(path)) return 'generated'
  if (TIER_RE.ci.test(path)) return 'ci'
  if (TIER_RE.tests.test(path)) return 'tests'
  if (TIER_RE.depManifest.test(path)) return 'deps'
  if (TIER_RE.docs.test(path)) return 'docs'
  if (TIER_RE.config.test(path)) return 'config'
  return 'product'
}
export function sizeTier(lines, files) {
  if (lines > 1000 || files > 30) return 'XL'
  if (lines >= 300) return 'L'
  if (lines >= 50) return 'M'
  return 'S'
}

// files: GitHub PR files（filename, additions, deletions）；riskClass 'A' = 现有分类器判为只改文档
export function aiTestTier({ files = [], riskClass = null, env = process.env } = {}) {
  const kinds = files.map((f) => ({ f, k: fileKind(f.filename ?? '') }))
  const product = kinds.filter((x) => x.k === 'product')
  const productLines = product.reduce((s, x) => s + (Number(x.f.additions) || 0) + (Number(x.f.deletions) || 0), 0)
  const n = files.length
  const has = (k) => kinds.some((x) => x.k === k)
  let type, tier, reason
  if (!n) { type = 'unknown'; tier = 'M'; reason = 'changed-file list unavailable: default tier M' }
  else if (product.length) { type = 'product'; tier = sizeTier(productLines, product.length); reason = `product source: ${productLines} lines in ${product.length} file(s)` }
  else if (riskClass === 'A' || kinds.every((x) => ['docs', 'config', 'generated'].includes(x.k))) { type = 'docs'; tier = 'none'; reason = 'docs/changelog/config-only change: no AI tests' }
  else if (has('ci') && kinds.every((x) => ['ci', 'docs', 'config', 'generated'].includes(x.k))) { type = 'ci'; tier = 'S'; reason = 'CI/workflow-only change: small tier' }
  else if (has('tests') && kinds.every((x) => ['tests', 'docs', 'config', 'generated'].includes(x.k))) { type = 'tests'; tier = 'S'; reason = 'tests-only change: small tier' }
  else { type = 'deps'; tier = 'S'; reason = 'dependency/lockfile/CI/test change without product source: small tier' }
  const caps = { enabled: env.ARGUS_AITESTS !== '0', ...AI_TEST_TIERS[tier] }
  const base = aiTestCaps(env)
  const overrides = []
  if (tier !== 'none') {
    for (const [key, name] of [['maxCalls', 'ARGUS_AITEST_MAX_CALLS'], ['maxWallMin', 'ARGUS_AITEST_MAX_MIN'], ['maxTestsPerCall', 'ARGUS_AITEST_MAX_TESTS']]) {
      if (env[name] != null && env[name] !== '') { caps[key] = base[key]; overrides.push(`${name}=${base[key]}`) }
    }
  }
  if (overrides.length) reason += `; env override ${overrides.join(', ')}`
  return { caps, tier: { tier, reason, product_lines: productLines, files: n, product_files: product.length, type } }
}

// 每日 ai-test 额度是全局上限：剩余额度 < 分级调用次数时降级（只降调用次数），并记录
export function degradeForQuota(result, quotaCheck) {
  if (!quotaCheck || !result.caps.maxCalls) return result
  const remaining = Math.max(0, (quotaCheck.cap ?? 0) - (quotaCheck.used ?? 0))
  if (remaining >= result.caps.maxCalls) return { ...result, tier: { ...result.tier, quota_remaining: remaining, degraded: false } }
  const from = result.caps.maxCalls
  return { caps: { ...result.caps, maxCalls: remaining },
    tier: { ...result.tier, quota_remaining: remaining, degraded: true, degraded_from_calls: from, reason: `${result.tier.reason}; degraded: daily ai-test quota has ${remaining} call(s) left (< tier ${from})` } }
}
