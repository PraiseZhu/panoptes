// AI 补写针对性测试：分诊说「涉及」、但现有检查覆盖不到的方面，让 gpt-6-luna 用项目自己的测试框架补写测试。
// 每个补测都要带一个「植入错误」：测试在当前代码上通过、植入错误后失败，才算有效；否则丢弃。
// 补测发现的问题在试运行期只记「拿不准，要你看」，不自动提 issue。
import { aiUsageToday, addAiUsage } from './db.mjs'
import { globToRegExp } from './classify.mjs'
import { toDimensionLabel } from './matrix.mjs'
import { redact, toShanghaiDay, truncate } from './util.mjs'

const MODEL = 'openai/gpt-6-luna'
const EFFORT = { B: 'high', C: 'xhigh' }
const DAILY_CAP = Number(process.env.ARGUS_AITEST_DAILY_CAP ?? 20)
const MAX_TESTS = 2
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
export function toValidTests(raw, { profile, gaps }) {
  const allowed = (profile.aiTests?.pathGlobs ?? []).map(globToRegExp)
  const gapSet = new Set(gaps)
  const out = []
  for (const t of Array.isArray(raw?.tests) ? raw.tests : []) {
    if (out.length >= MAX_TESTS) break
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
export function toAiTestSteps(tests, profile) {
  return tests.map((t, i) => ({
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
  }))
}

export async function generateTargetedTests({ db, secrets, riskClass, pr, files, profile, gaps, now = new Date() }) {
  if (!profile.aiTests) return { status: 'skipped', reasonZh: '这个项目还没配置 AI 补测', tests: [] }
  if (riskClass !== 'B' && riskClass !== 'C') return { status: 'skipped', reasonZh: '只改文档', tests: [] }
  if (!gaps.length) return { status: 'skipped', reasonZh: '涉及的方面现有检查都覆盖了', tests: [] }
  if (!secrets.ARGUS_LLM_BASE_URL || !secrets.ARGUS_LLM_API_KEY) return { status: 'skipped', reasonZh: '模型凭证没配置', tests: [] }
  const day = `gen:${toShanghaiDay(now)}`
  if (aiUsageToday(db, day).calls >= DAILY_CAP) return { status: 'skipped', reasonZh: `今天的 AI 补测额度（${DAILY_CAP} 次）用完了`, tests: [] }
  const user = redact([
    `项目：${profile.displayName ?? ''}`,
    `产品意图：${profile.intentZh ?? '（没写）'}`,
    `测试框架：${profile.aiTests.frameworkZh}；新测试文件路径必须匹配：${profile.aiTests.pathGlobs.join('、')}`,
    `需要补测的方面（维度 key → 含义）：${gaps.map((k) => `${k} → ${toDimensionLabel(k)}`).join('；')}`,
    `PR 标题：${pr.title}`,
    `PR 描述：\n${truncate(pr.body ?? '', 4000)}`,
    `改动内容：${buildDiff(files)}`,
  ].join('\n\n'))
  try {
    const resp = await fetch(`${secrets.ARGUS_LLM_BASE_URL.replace(/\/$/, '')}/v1/chat/completions`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${secrets.ARGUS_LLM_API_KEY}` },
      body: JSON.stringify({ model: MODEL, reasoning_effort: EFFORT[riskClass], response_format: { type: 'json_object' }, max_completion_tokens: 16_000, messages: [{ role: 'system', content: SYSTEM }, { role: 'user', content: user }] }),
      signal: AbortSignal.timeout(8 * 60_000),
    })
    const data = await resp.json().catch(() => ({}))
    addAiUsage(db, day, data?.usage?.total_tokens ?? 0)
    if (!resp.ok) return { status: 'error', reasonZh: `模型网关返回 ${resp.status}`, tests: [] }
    const tests = toValidTests(JSON.parse(data.choices?.[0]?.message?.content ?? '{}'), { profile, gaps })
    return { status: tests.length ? 'ok' : 'empty', reasonZh: tests.length ? null : 'AI 没写出可用的测试', tests }
  } catch (err) {
    return { status: 'error', reasonZh: `AI 补测失败（${redact(err.message).slice(0, 80)}）`, tests: [] }
  }
}
