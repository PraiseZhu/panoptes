// 日报骨架：结论先回答要不要你处理；每个 PR 一个明确判定；下一步只放要做的事；AI 猜测只进附录
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

process.env.ARGUS_ROOT = mkdtempSync(join(tmpdir(), 'argus-report-'))
const { openDb, insertJob, setMeta } = await import('../src/db.mjs')
const { buildDailyReport, findVaguePhrases, VAGUE_PHRASES, previousDayCoverage, toVerdict } = await import('../src/report.mjs')

const reg = { owner: { login: 'PraiseZhu', id: 1 }, issueRepo: 'x/deploy', enabledSince: '2026-09-01T00:00:00Z', projects: [
  { repo: 'x/mivo', displayName: 'Mivo 插件', profileData: { steps: [] } },
  { repo: 'x/akb', displayName: 'AKB2 知识库', profileData: { steps: [] } },
] }
const results = [{ id: 'install', kind: 'setup', ok: true, nameZh: '安装依赖' }, { id: 'unit', kind: 'product', ok: false, nameZh: '单元测试' }, { id: 'lint', kind: 'quality', ok: true, nameZh: '代码规范' }]

let n = 0
function freshDb() {
  const db = openDb(join(process.env.ARGUS_ROOT, `r${++n}.sqlite`))
  setMeta(db, 'heartbeat_at', new Date().toISOString())
  for (const p of reg.projects) setMeta(db, `scan_ok_at:${p.repo}`, new Date().toISOString())
  return db
}
function job(db, repo, pr, title, outcome, detail) {
  insertJob(db, { repo, prNumber: pr, prNodeId: `N${repo}${pr}`, prTitle: title, prUrl: `u${pr}`, authorLogin: 'PraiseZhu', baseRef: 'main', mergedAt: new Date(Date.now() - 3600_000).toISOString(), mergeSha: `s${pr}`, status: 'done', outcome, detail: { coverage: { applicable: [], covered: [], missed: [], undetermined: [] }, ...detail } })
}
const noIssues = { issues: [] }

test('日报：混合老失败的重跑通过数量出现在 PR 结果中，旧记录没有伪造数量', () => {
  const db = freshDb()
  job(db, 'x/mivo', 835, 'fix: 混合失败', 'passed_known_failures', {
    results, failedSteps: ['unit'], knownFailureCount: 2,
    transientFailureCount: 8, transientFailures: Array.from({ length: 8 }, (_, i) => ({ key: `unit::src/private.test.mjs > detail ${i}`, type: 'assertion' })),
    coverage: { applicable: ['unit'], covered: [], missed: ['unit'], undetermined: [] },
  })
  job(db, 'x/mivo', 836, 'fix: 旧记录', 'passed_known_failures', { results, failedSteps: ['unit'], knownFailureCount: 2 })
  const { text } = buildDailyReport({ db, reg, now: new Date(), issues: noIssues })
  const start = text.indexOf('Mivo 插件 #835「')
  const rerun = text.indexOf('重跑结果：8 项首次失败、重跑通过，原因尚未确认。')
  assert.ok(rerun > start && rerun < text.indexOf('👉 下一步'), text)
  assert.match(text, /老失败：Mivo 插件 2 个/)
  assert.equal(text.match(/重跑结果：/g).length, 1)
  assert.ok(!text.includes('private.test.mjs'), '日报只展示数量，不泄露用例路径')
  assert.ok(!text.includes('已确认是这个 PR 带来的'))
  assert.deepEqual(findVaguePhrases(text), [])
  db.close()
})

test('#742 那天的数据：结论是「今天不用你处理」，AI 猜测和留意只进附录，下一步是不用操作', () => {
  const db = freshDb()
  const ai742 = { status: 'ok', risksZh: ['摘要不变时旧批准可跨版本复用，需确认仍适用于当前上下文', '未验证真实 PR 评论往返，权限和编辑记录场景仍需留意'], suspectZh: '实现仍明确排除 PR 作者的评论，因此作者自批仍会被拒绝，与 PR 目标相反' }
  job(db, 'x/mivo', 742, 'fix(ci): Windows 控制面批准允许作者自批并复用 digest', 'passed_known_failures', { results, failedSteps: ['unit'], knownFailureCount: 2, ai: ai742 })
  job(db, 'x/mivo', 716, 'fix: 修复重评、基线归因和浏览器清理误报', 'passed_known_failures', { results, failedSteps: ['unit'], knownFailureCount: 2, ai: { status: 'ok', risksZh: ['本地化 Windows 进程列表输出可能导致清理无法确认'] } })
  job(db, 'x/akb', 115, 'fix: GitHub 早报不再被限流饿死', 'passed_known_failures', { results, failedSteps: ['unit'], knownFailureCount: 129 })
  const { text, todo } = buildDailyReport({ db, reg, now: new Date(), issues: noIssues, diskFreeGb: 540 })
  const lines = text.split('\n')
  assert.equal(todo, 0)
  assert.equal(lines[2], '结论：今天不用你处理。更新了 3 个 PR 的结果，已执行检查未见新问题，没有新 issue，也没有没解决的 issue。')
  assert.ok(text.includes('✅ Mivo 插件 #742「Windows 控制面批准允许作者自批并复用 digest」：已执行检查未见新问题。'), text)
  const nextIdx = lines.indexOf('👉 下一步')
  assert.equal(lines[nextIdx + 1], '不用操作。')
  const appendix = text.indexOf('附录（AI 提示和运行状态）')
  assert.ok(text.indexOf('AI 的猜测（Mivo 插件 #742') > appendix, 'AI 猜测只能出现在附录')
  assert.ok(text.indexOf('测试没覆盖到的地方（Mivo 插件 #742') > appendix)
  assert.ok(text.indexOf('老失败') > appendix)
  assert.deepEqual(findVaguePhrases(text), [])
})

test('每种结论都落到四种判定之一，模板里没有含糊说法', () => {
  const db = freshDb()
  const outcomes = ['passed', 'passed_known_failures', 'docs_only', 'fixed_later', 'flaky', 'defect', 'needs_review', 'not_enrolled', 'expired', 'infra_failed', 'blocked']
  outcomes.forEach((o, i) => job(db, 'x/mivo', 100 + i, `t${i}`, o, { results, failedSteps: ['unit'], reasonZh: '原因', issueUrl: o === 'defect' ? 'https://h/1' : undefined }))
  const { text } = buildDailyReport({ db, reg, now: new Date(), issues: noIssues })
  const prLines = text.split('\n').filter((l) => /^(✅|❌|⏸|❓) /.test(l))
  assert.equal(prLines.length, outcomes.length)
  for (const l of prLines) assert.match(l, /：(已执行检查未见新问题|有问题，要你处理|未完成验证|拿不准，要你看|仅文档，未运行测试)。/)
  assert.deepEqual(findVaguePhrases(text), [])
  for (const p of VAGUE_PHRASES) assert.ok(!text.split('附录（AI 提示和运行状态）')[0].includes(p), `正文出现含糊说法：${p}`)
})

test('有问题和拿不准的 PR 进结论并各有一条【要做】；标记过不修的算不用管', () => {
  const db = freshDb()
  job(db, 'x/mivo', 750, 'fix: 导出', 'defect', { results, failedSteps: ['unit'], issueUrl: 'https://h/3', issueAction: 'created' })
  job(db, 'x/akb', 118, 'feat: 检索', 'needs_review', { results, failedSteps: ['unit'], reasonZh: '认不出具体是哪个用例失败，没法和合并前对比' })
  job(db, 'x/akb', 119, 'fix: 旧问题', 'defect', { results, failedSteps: ['unit'], issueUrl: 'https://h/2', issueAction: 'suppressed' })
  const { text, todo } = buildDailyReport({ db, reg, now: new Date(), issues: noIssues })
  assert.equal(todo, 2)
  assert.match(text.split('\n')[2], /^结论：今天有 2 件事要你处理：Mivo 插件 #750 有问题；AKB2 知识库 #118 拿不准。另有 1 个 PR 的已执行检查未见新问题。$/)
  assert.ok(text.includes('❌ Mivo 插件 #750'))
  assert.ok(text.includes('❓ AKB2 知识库 #118'))
  assert.ok(text.includes('✅ AKB2 知识库 #119'), '标记不修的不再要你处理')
  assert.equal(text.split('\n').filter((l) => l.includes('【要做】')).length, 2)
})

test('Cindy 已声明不做宿主真实使用时，日报不再把端到端和界面写成没测到', () => {
  const db = freshDb()
  const cindyReg = { ...reg, projects: [...reg.projects, { repo: 'makecindy/cindy', displayName: 'Cindy', profileData: { steps: [], notApplicable: { e2e: 'x', visual: 'x', ui: 'x' } } }] }
  job(db, 'makecindy/cindy', 4728, 'feat: 会话', 'passed', {
    results: results.map((r) => ({ ...r, ok: true })),
    coverage: { applicable: ['unit', 'e2e', 'visual', 'ui'], covered: ['unit'], missed: ['e2e', 'visual', 'ui'] },
  })
  const { text } = buildDailyReport({ db, reg: cindyReg, now: new Date(), issues: noIssues })
  assert.match(text, /覆盖：这次改动涉及 1 个方面，现有检查覆盖 1 个/)
  assert.ok(!text.includes('没测到的见附录'), text)
  assert.ok(!text.includes('真实使用流程'), text)
  assert.ok(!text.includes('界面长什么样'), text)
  assert.ok(!text.includes('界面上的结果'), text)
})

test('附录丢掉已取消的 Cindy 宿主提示，其它风险照常保留', () => {
  const db = freshDb()
  job(db, 'x/mivo', 666, 'feat: 像素', 'passed', {
    results: results.map((r) => ({ ...r, ok: true })),
    ai: { status: 'ok', risksZh: ['未在真实 Cindy 宿主验证；后续接入后可能与插件实际画面不一致', '真实插件宿主的流占用和冷启动未验证', '导出失败时半成品必须清掉'] },
  })
  const { text } = buildDailyReport({ db, reg, now: new Date(), issues: noIssues })
  assert.ok(!text.includes('Cindy 宿主'), text)
  assert.ok(!text.includes('真实插件宿主'), text)
  assert.ok(!text.includes('冷启动未验证'), text)
  assert.ok(text.includes('导出失败时半成品必须清掉'), text)
})

test('没跑成的检查进主结论和验证明细，不宣称通过', () => {
  const db = freshDb()
  job(db, 'x/mivo', 760, 'feat: 画布', 'passed', { results: results.map((r) => ({ ...r, ok: true })), unavailableSteps: [{ id: 'e2e', nameZh: '浏览器端到端', reasonZh: '浏览器没起来' }] })
  const { text } = buildDailyReport({ db, reg, now: new Date(), issues: noIssues })
  assert.match(text.split('\n')[2], /1 个 PR 验证不完整/)
  assert.match(text, /⏸ Mivo 插件 #760.*未完成验证/)
  assert.ok(text.indexOf('没跑成的检查（Mivo 插件 #760）：浏览器端到端，浏览器没起来') < text.indexOf('附录（AI 提示和运行状态）'), text)
})

test('读不到 issue 状态时照发并说明，结论不说「没有没解决的 issue」', () => {
  const db = freshDb()
  const { text } = buildDailyReport({ db, reg, now: new Date(), issues: { error: true } })
  assert.ok(text.includes('这次没读到 GitHub 上的 issue 状态'))
  assert.ok(!text.split('\n')[2].includes('没有没解决的 issue'), text)
})

test('测试打扰了桌面（弹出系统密码框）时，结论要你处理', () => {
  const db = freshDb()
  job(db, 'x/mivo', 770, 'feat: 画布', 'passed', { results: results.map((r) => ({ ...r, ok: true })), disturbedSteps: ['浏览器端到端'] })
  const { text, todo } = buildDailyReport({ db, reg, now: new Date(), issues: noIssues })
  assert.equal(todo, 1)
  assert.match(text.split('\n')[2], /测试打扰了 Mini 桌面/)
  assert.ok(text.includes('【要做】浏览器端到端运行时 Mini 上弹出了系统密码框'))
})

test('已有失败和有效补测分别核实；缺覆盖不能被无新失败掩盖', () => {
  const db = freshDb()
  job(db, 'x/mivo', 823, 'fix: 安全校验', 'passed_known_failures', {
    results, failedSteps: ['unit'], knownFailureCount: 2,
    coverage: { applicable: ['security'], covered: [], missed: ['security'], undetermined: [] },
    aiTests: { status: 'ok', tests: [{ id: 'ai-test-1', result: 'fail', purposeZh: '安全边界' }] },
  })
  const { text, todo } = buildDailyReport({ db, reg, now: new Date(), issues: noIssues })
  assert.equal(todo, 1)
  assert.match(text, /⏸ Mivo 插件 #823.*未完成验证/)
  assert.match(text, /AI 补测未全部通过有效性验证/)
  assert.match(text, /当前任务不会自动重跑/)
  assert.ok(!text.includes('接入对应测试后会自动补上'))
})

test('适用性未知只在没有有效检查覆盖时构成缺口；重跑通过不推断产品无责', () => {
  const good = { results: [{ id: 'unit', kind: 'product', ok: true }], coverage: { missed: [], undetermined: ['unit'], undeterminedMissed: [] } }
  assert.equal(toVerdict({ outcome: 'passed' }, good).key, 'ok')
  assert.equal(toVerdict({ outcome: 'passed' }, { ...good, coverage: { ...good.coverage, undeterminedMissed: ['exception'] } }).key, 'ok', '已跑过检查时，灰区不再标未完成验证')
  assert.equal(toVerdict({ outcome: 'passed' }, { coverage: { missed: [], undeterminedMissed: ['exception'] } }).key, 'pending', '没跑过检查时，灰区仍是缺口')
  const flaky = toVerdict({ outcome: 'flaky' }, good)
  assert.equal(flaky.key, 'unsure')
  assert.match(flaky.reasonZh, /不能排除产品间歇性问题/)
})

test('灰区且已跑过检查不进【要做】；涉及方面未覆盖仍催办', () => {
  const db = freshDb()
  const ran = results.map((r) => ({ ...r, ok: true }))
  job(db, 'x/mivo', 881, 'chore: 更新日志补扫', 'passed_known_failures', {
    results: ran,
    coverage: { applicable: ['db'], covered: ['db'], missed: [], undetermined: ['exception', 'async-callback-retry'], undeterminedMissed: ['exception', 'async-callback-retry'] },
  })
  job(db, 'x/mivo', 823, 'fix: 安全校验', 'passed_known_failures', {
    results: ran,
    coverage: { applicable: ['security'], covered: [], missed: ['security'], undetermined: [] },
  })
  const { text, todo } = buildDailyReport({ db, reg, now: new Date(), issues: noIssues })
  assert.equal(todo, 1)
  assert.match(text, /✅ Mivo 插件 #881.*已执行检查未见新问题/)
  assert.ok(!text.includes('⏸ Mivo 插件 #881'), text)
  assert.match(text, /⏸ Mivo 插件 #823.*未完成验证/)
  assert.match(text, /1 个涉及方面未覆盖/)
  assert.ok(!/适用性未确认且缺少覆盖证据/.test(text.split('附录（AI 提示和运行状态）')[0]), text)
})

test('昨日分母按上海合并自然日；跨日完成、未接入、文档和未跑分别计数', () => {
  const db = freshDb()
  const today = '2026-10-03T03:00:00Z'
  const add = (n, outcome, detail, mergedAt = '2026-10-02T04:00:00Z', repo = 'x/mivo') => {
    insertJob(db, { repo, prNumber: n, prNodeId: `day${n}`, prTitle: '验证', prUrl: 'local', authorLogin: 'PraiseZhu', baseRef: 'main', mergedAt, mergeSha: `s${n}`, status: outcome ? 'done' : 'queued', outcome, detail })
  }
  add(1, 'passed', { results: [{ id: 'unit', kind: 'product', ok: true }] }, '2026-10-01T16:00:00Z')
  add(2, 'passed_known_failures', { results, coverage: { missed: ['api'] } })
  add(3, 'docs_only', {})
  add(4, 'not_enrolled', {})
  add(5, null, {})
  add(6, 'infra_failed', { results: [{ id: 'install', kind: 'setup', ok: false }] })
  add(7, 'passed', {}, '2026-10-02T16:00:00Z')
  add(8, 'passed', {}, '2026-10-01T15:59:59Z')
  add(9, 'passed', {}, '2026-10-02T04:00:00Z', 'x/unregistered')
  assert.deepEqual(previousDayCoverage({ db, reg, now: new Date(today) }), { day: '2026-10-02', observed: 6, executed: 2, docs: 1, notEnrolled: 1, notRun: 2, incomplete: 1, coverageUnknown: 1 })
})

test('日报明确扫描范围和分母缺口，不把文档或未接入称为测过', () => {
  const db = freshDb()
  setMeta(db, 'last_poll_error', 'offline')
  job(db, 'x/mivo', 999, 'docs: 说明', 'docs_only', {})
  const { text } = buildDailyReport({ db, reg, now: new Date(), issues: noIssues })
  assert.match(text, /仅登记仓库中 PraiseZhu 的已合并 PR/)
  assert.match(text, /扫描有缺口，入账数不代表完整合并数/)
  assert.ok(!/测了 1 个 PR|测了哪些 PR/.test(text))
})


test('旧记录缺失类型证据时不能把未知写成完整通过', () => {
  const d = { results: [{ id: 'unit', kind: 'product', ok: true }] }
  const v = toVerdict({ outcome: 'passed_known_failures' }, d)
  assert.equal(v.key, 'pending')
  assert.match(v.reasonZh, /缺少测试类型覆盖证据/)
})
