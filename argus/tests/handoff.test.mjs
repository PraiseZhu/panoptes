// Mivo PR 交接文件（docs/pr/<N>/review.md、qa.md）：读取、解析、接入夜间流程。夹具取自 xindong/mivo-canvas-plugin
// PR #1116（已合并，d1e31327）的 docs/pr/_template/ 和 scripts/ci/fixtures/pr-handoff/valid-full/。模型全部 mock。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = mkdtempSync(join(tmpdir(), 'argus-handoff-'))
process.env.ARGUS_ROOT = root
process.env.ARGUS_GH_BIN = join(here, 'fixtures/argus-fake-gh.mjs')
process.env.ARGUS_GH_RETRY_MS = '1'
process.env.ARGUS_E2E_GH_STATE = join(root, 'github-fixture.json')
after(() => rmSync(root, { recursive: true, force: true }))

const { parseHandoff, toHandoffReport, settleHandoff, splitHandoffCases } = await import('../src/handoff.mjs')
const { getHandoff } = await import('../src/github.mjs')
const { classifyChange } = await import('../src/classify.mjs')
const { toValidTests } = await import('../src/aitests.mjs')
const { verifyLeads } = await import('../src/verify-lead.mjs')
const { memoryQuota } = await import('../src/quota.mjs')
const N = await import('../src/nightly.mjs')
const { MIRRORS_DIR } = await import('../src/paths.mjs')

const fx = (name) => readFileSync(join(here, 'fixtures/handoff', name), 'utf8')
const FULL = { review: fx('full-review.md'), qa: fx('full-qa.md') }
const TEMPLATE = { review: fx('template-review.md'), qa: fx('template-qa.md') }
const health = () => ({ status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], notes: [] })

test('handoff parse: Mivo 填好的示例 → A / R（五个字段）/ B / Q / 第 5 节，半角冒号和半角括号也认', () => {
  const h = parseHandoff(FULL)
  assert.equal(h.present, true)
  assert.deepEqual(h.acceptance, [{ id: 'A1', text: 'A 画布提交的任务，结果只落在 A 画布' }])
  assert.deepEqual(Object.keys(h.risks[0]), ['id', 'title', 'trigger', 'breaks', 'guard', 'falsify', 'tests'])
  assert.equal(h.risks[0].title, '切画布后迟到结果写错画布')
  assert.equal(h.risks[0].tests, 'Q1')
  assert.deepEqual(h.boundaries, [{ id: 'B1', text: '跨插件实例的迟到结果', type: '没验证' }])
  const q = h.cases[0]
  assert.equal(q.id, 'Q1'); assert.deepEqual(q.covers, ['R1', 'A1'])
  assert.equal(q.level, 'contract', '「层次: contract」半角冒号')
  assert.match(q.expect, /B 不出现新节点/, '「期望(看得见)」半角括号'); assert.match(q.expect, /没有复活占位/)
  assert.match(q.counter_proof, /^撤掉 commitGenerationResultImpl/)
  assert.match(q.automation, /CrossScene\.test\.ts/)
  assert.equal(q.author_status, 'PASS')
  assert.deepEqual(h.left, [{ ref: 'B1', why: '没有两个插件实例的环境', env: 'Cindy 实机，两个实例' }])
})

test('handoff parse: Mivo 轻档示例（valid-light）→ 第 5 节「无」行不算留给 QA 的条目；层次大小写不敏感', () => {
  const h = parseHandoff({ review: fx('light-review.md'), qa: fx('light-qa.md') })
  assert.equal(h.present, true)
  assert.deepEqual(h.risks, [])
  assert.deepEqual(h.left, [])
  assert.equal(h.cases[0].level, 'static')
  assert.deepEqual(splitHandoffCases(h).auto, [])
  assert.equal(parseHandoff({ ...FULL, qa: FULL.qa.replace('层次: contract', '层次: Contract') }).cases[0].level, 'contract')
})

test('handoff parse: 层次按校验器整词匹配，自动层次优先；第 5 节「无（理由）」也跳过', () => {
  const lv = (cell) => parseHandoff({ ...FULL, qa: FULL.qa.replace('层次: contract', `层次: ${cell}`) }).cases[0].level
  assert.equal(lv('unit（vitest）'), 'unit')
  assert.equal(lv('unit + macOS'), 'unit')
  assert.equal(lv('cindy-mac + browser-fixture'), 'browser-fixture')
  assert.equal(lv('cindy-windows（实机）'), 'cindy-windows')
  const qa = fx('light-qa.md').replace('| 无 | 无 | 无 |', '| 无（纯文档） | 无 | 无 |')
  assert.deepEqual(parseHandoff({ review: fx('light-review.md'), qa }).left, [])
})

test('handoff parse: 原样模板 → present:true，条目字段为空，不报错', () => {
  const h = parseHandoff(TEMPLATE)
  assert.equal(h.present, true)
  assert.deepEqual(h.risks, [{ id: 'R1', title: '', trigger: '', breaks: '', guard: '', falsify: '', tests: '' }])
  assert.deepEqual(h.boundaries, [{ id: 'B1', text: '', type: '' }])
  assert.equal(h.cases[0].level, ''); assert.equal(h.cases[0].expect, ''); assert.equal(h.cases[0].author_status, null)
  assert.deepEqual(splitHandoffCases(h).auto, [], '模板没有可补测的条目')
})

test('handoff parse: 只认 schema mivo-pr-handoff/v1；没有文件头或别的 schema → present:false 带 reason；先去 HTML 注释；全角冒号', () => {
  assert.deepEqual(parseHandoff({}), { present: false, reason: 'no handoff files (docs/pr/<N>/review.md, qa.md)' })
  assert.match(parseHandoff({ review: '# 没有文件头\n' }).reason, /no front-matter/)
  assert.match(parseHandoff({ ...FULL, qa: FULL.qa.replace('mivo-pr-handoff/v1', 'mivo-pr-handoff/v2') }).reason, /qa\.md schema 'mivo-pr-handoff\/v2'/)
  const qa = FULL.qa.replace('- 层次: contract', '<!-- - 层次：paid -->\n- 层次：unit').replace('### Q1', '<!--\n### Q9 被注释掉（覆盖 R1）\n- 层次：paid\n-->\n### Q1')
  const h = parseHandoff({ ...FULL, qa })
  assert.deepEqual(h.cases.map((c) => [c.id, c.level]), [['Q1', 'unit']])
})

test('handoff getHandoff: 200 读到文本、404 → null、symlink / 跟随到别的路径 → null 并记 env_problems', async () => {
  const b64 = (s) => Buffer.from(s).toString('base64')
  const calls = []
  const get = async (path) => {
    calls.push(path)
    if (path.includes('/review.md')) return { type: 'file', path: 'docs/pr/7/review.md', encoding: 'base64', content: b64('R') }
    throw Object.assign(new Error('Not Found'), { httpStatus: 404 })
  }
  assert.deepEqual(await getHandoff('o/r', 7, 'sha1', { get }), { review: 'R', qa: null })
  assert.deepEqual(calls, ['repos/o/r/contents/docs/pr/7/review.md?ref=sha1', 'repos/o/r/contents/docs/pr/7/qa.md?ref=sha1'])
  const h = health()
  const link = async (path) => path.includes('review') ? { type: 'symlink', path: 'docs/pr/7/review.md', target: '../../../secrets' } : { type: 'file', path: 'README.md', encoding: 'base64', content: b64('x') }
  assert.deepEqual(await getHandoff('o/r', 7, 'sha1', { get: link, health: h }), { review: null, qa: null })
  assert.equal(h.env_problems.length, 2)
  assert.match(h.env_problems[0].message, /review\.md is not a regular file \(type symlink\)/)
  assert.match(h.env_problems[1].message, /resolves to README\.md/)
  await assert.rejects(getHandoff('o/r', 7, 's', { get: async () => { throw Object.assign(new Error('boom'), { httpStatus: 500 }) } }), /boom/)
})

test('handoff classify: 只加 docs/pr/<N>/ 的 PR 仍是 A；代码 PR 的分级不受交接文件影响', () => {
  const H = [{ filename: 'docs/pr/9/review.md' }, { filename: 'docs/pr/9/qa.md' }]
  assert.equal(classifyChange(H).riskClass, 'A')
  const profile = { cPaths: ['src/store/**'] }
  for (const code of [[{ filename: 'src/a.ts' }], [{ filename: 'src/store/x.ts' }], [{ filename: 'README.md' }]]) {
    assert.deepEqual(classifyChange([...code, ...H], profile).riskClass, classifyChange(code, profile).riskClass)
    assert.deepEqual(classifyChange([...code, ...H], profile).reasonZh, classifyChange(code, profile).reasonZh)
  }
  // 交接文件不进代码审查 / 新代码覆盖率的 diff
  const diff = 'diff --git a/src/a.ts b/src/a.ts\n+x\ndiff --git a/docs/pr/9/qa.md b/docs/pr/9/qa.md\n+y\n'
  assert.equal(N.stripHandoffDiff(diff), 'diff --git a/src/a.ts b/src/a.ts\n+x\n')
})

test('handoff report: 每条 case 有 qa_status 和稳定 key；verified / failed（confirmed:false，verify-lead 确认后才 true）/ not-run 带 reason / manual', () => {
  const qa = FULL.qa.replace('### Q1', '### Q2 Windows 拖拽（覆盖 R1）\n\n- 层次：cindy-windows\n- 期望：x\n\n### Q3 发布页（覆盖 A1）\n\n- 层次：github-live\n\n### Q1')
  const h = parseHandoff({ ...FULL, qa })
  const rep = toHandoffReport(h, 42)
  assert.deepEqual(rep.cases.map((c) => [c.id, c.key, c.qa_status]), [['Q1', 'handoff:42:Q1', 'pending'], ['B1', 'handoff:42:B1', 'pending'], ['Q2', 'handoff:42:Q2', 'manual'], ['Q3', 'handoff:42:Q3', 'not-run']])
  assert.deepEqual(rep.manual, [{ id: 'Q2', level: 'cindy-windows', why: 'cindy-windows needs a real device or a paid run; not run by argus' }])
  assert.equal(rep.cases[0].author_status, 'PASS')
  const ai = { status: 'ok', model_calls: 1, tests: [{ id: 'ai-test-1', handoff: 'Q1', result: 'fails-on-current', file: 'src/q1.test.ts', reproduced: true }, { id: 'ai-test-2', handoff: 'B1', result: 'discarded', reason: 'mutation check failed' }] }
  settleHandoff(rep, ai)
  assert.deepEqual(rep.cases.map((c) => [c.qa_status, c.confirmed, c.reason?.split(':')[0] ?? null]), [['failed', false, 'lead only'], ['not-run', null, 'no-reliable-test'], ['manual', null, null], ['not-run', null, 'unsupported-level github-live']])
  // verify-lead 按 check_id 确认 AI 补测线索时，同一条交接用例的 confirmed 跟着变
  const report = { prs: [{ number: 42, handoff: rep, findings: [{ source: 'ai-test', check_id: 'ai-test-1', fingerprint: 'f' }] }] }
  verifyLeads(report, { pr: 42, checkId: 'ai-test-1' })
  assert.equal(rep.cases[0].confirmed, true)
  const r2 = settleHandoff(toHandoffReport(h, 42), { status: 'ok', model_calls: 1, tests: [{ id: 'ai-test-1', handoff: 'Q1', result: 'kept', file: 'f' }] })
  assert.equal(r2.cases[0].qa_status, 'verified')
  const r3 = settleHandoff(toHandoffReport(h, 42), { status: 'capped', reason: 'quota', model_calls: 0, tests: [] })
  assert.equal(r3.cases[0].reason, 'quota-exhausted')
  assert.equal(settleHandoff(toHandoffReport(h, 42), null, 'docs-only').cases[0].reason, 'docs-only')
  assert.deepEqual(toHandoffReport({ present: false, reason: 'x' }, 42), { present: false, reason: 'x', cases: [], manual: [] })
})

test('handoff aitests: 交接用例排在覆盖缺口之前、带反证；只在分档额度内（不加调用次数），配额用 quota.mjs', async () => {
  const profile = { aiTests: { frameworkZh: 'vitest', pathGlobs: ['src/**/*.test.ts'], run: 'x' }, steps: [] }
  // 只有交接用例编号的测试也算有效；不认识的编号丢弃
  const t = (handoff) => ({ kind: 'coverage', filePath: 'src/a.test.ts', content: 'x', covers: [], handoff, mutation: { file: 'src/a.ts', find: 'a', replace: 'b' } })
  assert.deepEqual(toValidTests({ tests: [t('Q1'), t('Q9')] }, { profile, gaps: [], handoffIds: ['Q1'] }).map((x) => x.handoff), ['Q1'])
  const prompts = []
  const fetchImpl = async (_u, init) => { prompts.push(JSON.parse(init.body).messages); return { ok: true, status: 200, json: async () => ({ choices: [{ message: { content: '{"tests":[]}' }, finish_reason: 'stop' }], usage: { total_tokens: 1 } }) } }
  const quota = memoryQuota()
  const a = { out: { triage: { status: 'ok', applicable: ['boundary'] }, coverage_gate: { new_code: { files: [{ file: 'src/a.ts', uncovered_lines: [3] }] } } }, repo: 'o/r', pr: { title: 't', body: '' },
    files: [{ filename: 'src/a.ts', status: 'modified', additions: 1, deletions: 0 }], profile, riskClass: 'B', sha: 'abc', common: {}, db: null,
    secrets: { ARGUS_LLM_BASE_URL: 'http://gw.invalid/v1', ARGUS_LLM_API_KEY: 'k' }, health: health(), number: 42, results: [], quota, handoff: parseHandoff(FULL), llm: { fetchImpl, netRetryDelayMs: 0 } }
  await N.runAiTests(a)
  assert.equal(a.out.ai_test_tier.tier, 'S'); assert.equal(prompts.length, 1, 'S 档 1 次调用，交接用例不加调用')
  assert.equal(quota.summary().used_by_run['ai-test'], 1)
  const user = prompts[0][1].content
  assert.ok(user.indexOf('作者交接的测试用例') < user.indexOf('改动里没被现有测试执行到的行'), '交接用例在覆盖缺口之前')
  assert.match(user, /- Q1（层次 contract）：切画布后迟到结果.*反证（优先当作植入错误）：撤掉/)
  assert.match(user, /- B1（留给独立 QA 的条目）/)
  assert.match(prompts[0][0].content, /优先按反证构造植入错误；仍然要在合并后的代码上通过、植入错误后失败/)
  assert.deepEqual(settleHandoff(toHandoffReport(a.handoff, 42), a.out.ai_tests).cases.map((c) => c.reason), ['no-reliable-test', 'no-reliable-test'])
  // 额度用尽：不调模型，reason quota-exhausted
  const spent = memoryQuota({ env: { ARGUS_QUOTA_AI_TEST: '0' } })
  const b = { ...a, out: { triage: { status: 'ok', applicable: [] } }, quota: spent }
  await N.runAiTests(b)
  assert.equal(prompts.length, 1)
  assert.deepEqual(settleHandoff(toHandoffReport(a.handoff, 42), b.out.ai_tests).cases.map((c) => c.reason), ['quota-exhausted', 'quota-exhausted'])
  // 文档 / 配置类 PR：分档 none，不补测
  const c = { ...a, out: { triage: { status: 'ok', applicable: [] } }, files: [{ filename: 'config/a.yml', additions: 1, deletions: 0 }], quota: memoryQuota() }
  await N.runAiTests(c)
  assert.equal(c.out.ai_test_tier.tier, 'none'); assert.equal(prompts.length, 1)
})

// 端到端：假 GitHub（contents 接口给交接文件）+ 本地 git 镜像，跑 processPr。不调模型（无 LLM 凭证）
function fixturePr(number, { handoff }) {
  const repo = `fixture/pr${number}`
  const src = join(root, `source-${number}`)
  mkdirSync(src)
  const git = (...args) => execFileSync('git', ['-C', src, '-c', 'user.name=F', '-c', 'user.email=f@example.invalid', ...args], { encoding: 'utf8' }).trim()
  git('init', '-q', '-b', 'main')
  writeFileSync(join(src, 'value.mjs'), 'export const v = 1\n')
  git('add', '.'); git('commit', '-qm', 'base')
  const baseSha = git('rev-parse', 'HEAD')
  writeFileSync(join(src, 'value.mjs'), 'export const v = 2\n')
  const files = [{ filename: 'value.mjs', status: 'modified', additions: 1, deletions: 1, patch: '-1\n+2' }]
  if (handoff) {
    mkdirSync(join(src, `docs/pr/${number}`), { recursive: true })
    for (const k of ['review', 'qa']) { writeFileSync(join(src, `docs/pr/${number}/${k}.md`), handoff[k]); files.push({ filename: `docs/pr/${number}/${k}.md`, status: 'added', additions: 50, deletions: 0 }) }
  }
  git('add', '.'); git('commit', '-qm', `change (#${number})`)
  const mergeSha = git('rev-parse', 'HEAD')
  mkdirSync(MIRRORS_DIR, { recursive: true })
  execFileSync('git', ['clone', '-q', '--bare', src, join(MIRRORS_DIR, `fixture__pr${number}.git`)])
  const contents = handoff ? Object.fromEntries(['review', 'qa'].map((k) => [`docs/pr/${number}/${k}.md`, { type: 'file', path: `docs/pr/${number}/${k}.md`, encoding: 'base64', content: Buffer.from(handoff[k]).toString('base64') }])) : {}
  const pr = { number, title: 'change', html_url: 'https://example.invalid', user: { login: 'f' }, base: { ref: 'main', sha: baseSha }, head: { sha: 'f'.repeat(40) }, merged_at: new Date().toISOString(), merge_commit_sha: mergeSha, state: 'closed', additions: 1, deletions: 1 }
  writeFileSync(process.env.ARGUS_E2E_GH_STATE, JSON.stringify({ pr, baseSha, files, contents, calls: [] }))
  const profile = { steps: [{ id: 'install', kind: 'setup', cmd: ['true'] }, { id: 'unit', kind: 'product', nameZh: '单元测试', covers: ['unit'], cmd: ['true'] }] }
  return { repo, profile, mergeSha }
}
const run = async (number, handoff) => {
  const { repo, profile, mergeSha } = fixturePr(number, { handoff })
  const h = health()
  const out = await N.processPr({ repo, project: { repo, profileData: profile }, number, discovery: 'merged', secrets: {}, db: null, health: h, evidenceRoot: join(root, `ev-${number}`), opts: { noReview: true, reuseMerged: false } })
  const calls = JSON.parse(readFileSync(process.env.ARGUS_E2E_GH_STATE, 'utf8')).calls
  return { out, h, calls, mergeSha }
}
// 和交接无关、每次都不同的字段
const stable = (o) => { const { handoff, number, merged_at, evidence_dir, test_started_at, tested_at, duration_s, merge_sha, tested_sha, base_sha, changed_files, triage, checks, ...rest } = o; return { ...rest, checks: checks.map(({ duration_ms, ended_at, ...c }) => c), triage: { ...triage, duration_ms: null } } }

test('handoff e2e processPr: SC-1 带交接的已合并代码 PR → present:true、每条 case 有 qa_status；SC-2 没交接 → present:false、其余字段一致；SC-3 分级不变', async () => {
  const withH = await run(3101, FULL)
  assert.ok(withH.calls.includes(`repos/fixture/pr3101/contents/docs/pr/3101/review.md?ref=${withH.mergeSha}`), 'ref = merge commit')
  const ho = withH.out.handoff
  assert.equal(ho.present, true)
  assert.ok(ho.cases.length >= 2 && ho.cases.every((c) => ['verified', 'failed', 'not-run', 'manual'].includes(c.qa_status)))
  assert.deepEqual(ho.cases.map((c) => [c.key, c.qa_status, c.reason]), [['handoff:3101:Q1', 'not-run', 'ai-tests-skipped'], ['handoff:3101:B1', 'not-run', 'ai-tests-skipped']])
  assert.equal(withH.out.changed_files, 3)
  const without = await run(3102, null)
  assert.deepEqual(without.out.handoff, { present: false, reason: 'no handoff files (docs/pr/<N>/review.md, qa.md)', cases: [], manual: [] })
  assert.deepEqual(without.h.env_problems, [])
  assert.equal(withH.out.risk_class, without.out.risk_class)
  assert.deepEqual(stable(withH.out), stable(without.out), 'apart from handoff, the report is the same as for the PR without handoff files')
  // 只加交接文件的 PR：A 类，跳过代码审查，cases 记 docs-only
  const { repo, profile } = fixturePr(3103, { handoff: FULL })
  const st = JSON.parse(readFileSync(process.env.ARGUS_E2E_GH_STATE, 'utf8'))
  st.files = st.files.filter((f) => f.filename.startsWith('docs/pr/'))
  writeFileSync(process.env.ARGUS_E2E_GH_STATE, JSON.stringify(st))
  const a = await N.processPr({ repo, project: { repo, profileData: profile }, number: 3103, discovery: 'merged', secrets: {}, db: null, health: health(), evidenceRoot: join(root, 'ev-3103'), opts: { reuseMerged: false } })
  assert.equal(a.risk_class, 'A'); assert.equal(a.verdict, 'docs-only')
  assert.equal(a.code_review.status, 'skipped')
  assert.equal(a.ai_test_tier.tier, 'none')
  assert.deepEqual(a.handoff.cases.map((c) => [c.qa_status, c.reason]), [['not-run', 'docs-only'], ['not-run', 'docs-only']])
})
