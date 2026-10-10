import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

process.env.ARGUS_ROOT = mkdtempSync(join(tmpdir(), 'argus-test-'))

const { decideOutcome, compareKey, failureKeys } = await import('../src/decide.mjs')
const { classifyChange, globToRegExp } = await import('../src/classify.mjs')
const { redact, toReadableTitle } = await import('../src/util.mjs')
const { openDb, insertJob, nextQueuedJob, markRunning, finishJob, recoverRunning, setMeta } = await import('../src/db.mjs')
const { buildDailyReport } = await import('../src/report.mjs')
const { defectFingerprint, buildIssue } = await import('../src/publish.mjs')

const r = (id, ok, kind = 'product') => ({ id, nameZh: id === 'unit' ? '单元测试' : id, kind, ok, exitCode: ok ? 0 : 1, tail: ok ? '' : 'build failed', commandTemplate: ['node', 'build.mjs'], failureParseStatus: 'not_detected', failureDetails: [] })
const setupOk = r('install', true, 'setup')
const typedStep = (id, names, { passed = [], ...extra } = {}) => ({ ...r(id, false), failures: names, failureDetails: names.map((name) => ({ name, type: 'assertion' })), failureParseStatus: names.length ? 'complete' : 'incomplete', testOutcomeStatus: 'complete', testOutcomes: [...names.map((name) => ({ caseId: name, failureName: name, status: 'failed' })), ...passed.map((name) => ({ caseId: name, failureName: name, status: 'passed' }))], ...extra })
const named = (names, extra = {}) => [setupOk, typedStep('unit', names, extra)]
const passed = (names) => named([], { passed: names, ok: true, exitCode: 0, failureParseStatus: 'complete' })

test('decide: 全部通过', () => {
  assert.equal(decideOutcome({ first: [setupOk, r('unit', true)] }).outcome, 'passed')
})

test('decide: 安装失败记基础设施失败', () => {
  assert.equal(decideOutcome({ first: [r('install', false, 'setup')] }).outcome, 'infra_failed')
})

test('decide: 重跑通过记不稳定', () => {
  const d = decideOutcome({ first: [setupOk, r('unit', false)], rerun: [setupOk, r('unit', true)] })
  assert.equal(d.outcome, 'flaky')
})

test('decide: 合并前同样失败，不算本 PR 引入', () => {
  const fail = [setupOk, r('unit', false)]
  assert.equal(decideOutcome({ first: fail, rerun: fail, base: fail }).outcome, 'passed_known_failures')
})

test('decide: 老失败不能掩盖新引入的失败用例', () => {
  const withNames = named
  const d = decideOutcome({
    first: withNames(['a.test.ts > old', 'b.test.ts > new']),
    rerun: withNames(['a.test.ts > old', 'b.test.ts > new']),
    base: withNames(['a.test.ts > old'], { passed: ['b.test.ts > new'] }),
    latest: withNames(['a.test.ts > old', 'b.test.ts > new']),
    latestIsSame: false,
  })
  assert.equal(d.outcome, 'defect')
  assert.deepEqual(d.failedTests, ['b.test.ts > new'])
  assert.equal(d.knownFailureCount, 1)
})

test('decide: 用例级重跑只剩老失败时保留未复现失败', () => {
  const withNames = named
  const d = decideOutcome({
    first: withNames(['a > old', 'b > new']),
    rerun: withNames(['a > old'], { passed: ['b > new'] }),
    base: withNames(['a > old'], { passed: ['b > new'] }),
  })
  assert.equal(d.outcome, 'passed_known_failures')
  assert.equal(d.knownFailureCount, 1)
  assert.deepEqual(d.knownOnlyStepIds, [], '混合未复现失败的步骤仍不能算全是老失败')
  assert.equal(d.transientFailureCount, 1)
  assert.deepEqual(d.transientFailures, [{ key: 'unit::b > new', type: 'assertion' }])
})

test('decide: 未复现失败在待核实、新回归和后续修复中均保留，未知对照不计为通过', () => {
  const first = named(['old', 'new', 'transient'])
  const rerun = named(['old', 'new'], { passed: ['transient'] })
  const base = named(['old'], { passed: ['new', 'transient'] })
  const green = passed(['old', 'new', 'transient'])
  for (const [extra, outcome] of [
    [{ baseKnown: false }, 'needs_review'],
    [{ latestIsSame: true }, 'defect'],
    [{ latest: green }, 'fixed_later'],
  ]) {
    const d = decideOutcome({ first, rerun, base, ...extra })
    assert.equal(d.outcome, outcome)
    assert.equal(d.transientFailureCount, 1)
    assert.deepEqual(d.transientFailures, [{ key: 'unit::transient', type: 'assertion' }])
    if (outcome === 'defect') assert.deepEqual(d.failureKeys, ['unit::new'])
  }
  const incomplete = decideOutcome({ first, rerun: named(['old'], { failureParseStatus: 'incomplete' }), base })
  assert.equal(incomplete.outcome, 'needs_review')
  assert.equal(incomplete.transientFailureCount, 0)
  assert.deepEqual(incomplete.transientFailures, [])
  const flaky = decideOutcome({ first, rerun: green })
  assert.equal(flaky.outcome, 'flaky')
  assert.equal(flaky.transientFailureCount, 3)
})

test('decide: 最新主干已修好，不发 issue', () => {
  const fail = [setupOk, r('unit', false)]
  const ok = [setupOk, r('unit', true)]
  assert.equal(decideOutcome({ first: fail, rerun: fail, base: ok, latest: ok, latestIsSame: false }).outcome, 'fixed_later')
})

test('decide: 三重确认后才是缺陷', () => {
  const fail = [setupOk, r('unit', false)]
  const ok = [setupOk, r('unit', true)]
  const d = decideOutcome({ first: fail, rerun: fail, base: ok, latest: fail, latestIsSame: false })
  assert.equal(d.outcome, 'defect')
  assert.deepEqual(d.failedSteps, ['unit'])
  assert.equal(d.attributed, true)
})

test('decide: 合并前版本未知时不提 issue，记待核实', () => {
  const fail = [setupOk, r('unit', false)]
  assert.equal(decideOutcome({ first: fail, rerun: fail, baseKnown: false, latestIsSame: true }).outcome, 'needs_review')
})

const green = passed(['new', 'guard', 'explicit timeout budget assertion', 'a', 'b'])

test('decide: 合并前那轮超时（认不出用例）时，不能把新失败当老失败', () => {
  const d = decideOutcome({ first: named(['new']), rerun: named(['new']), base: named([], { timedOut: true }) })
  assert.equal(d.outcome, 'needs_review')
})

test('decide: 合并版本认不出用例、合并前有具体失败时，记待核实', () => {
  const d = decideOutcome({ first: named([]), rerun: named([]), base: named(['old']) })
  assert.equal(d.outcome, 'needs_review')
})

test('decide: 最新主干没跑起来或拿不到时，记待核实', () => {
  const d = decideOutcome({ first: named(['new']), rerun: named(['new']), base: green, latest: null, latestIsSame: false })
  assert.equal(d.outcome, 'needs_review')
  const setupBroken = [{ ...setupOk, ok: false }]
  assert.equal(decideOutcome({ first: named(['new']), rerun: named(['new']), base: green, latest: setupBroken, latestIsSame: false }).outcome, 'needs_review')
})

test('decide: 最新主干认不出用例时，记待核实', () => {
  const d = decideOutcome({ first: named(['new']), rerun: named(['new']), base: green, latest: named([]), latestIsSame: false })
  assert.equal(d.outcome, 'needs_review')
})

test('decide: 合并前整步通过、合并后整步失败且主干仍整步失败，按整步确认缺陷', () => {
  const fail = [setupOk, r('build', false)]
  const d = decideOutcome({ first: fail, rerun: fail, base: [setupOk, r('build', true)], latest: fail, latestIsSame: false })
  assert.equal(d.outcome, 'defect')
  assert.deepEqual(d.failedSteps, ['build'])
})

test('decide: 重跑那轮认不出具体用例时不能确认该失败重现', () => {
  const d = decideOutcome({ first: named(['new']), rerun: named([]), base: green, latestIsSame: true })
  assert.equal(d.outcome, 'needs_review')
})

test('decide: 没有实际检查时不能报告通过', () => {
  for (const first of [[], [setupOk], [setupOk, { ...r('browser', false), unavailable: true, exitCode: 2 }]]) {
    assert.equal(decideOutcome({ first }).outcome, 'blocked')
  }
})

test('decide: 任一对照超时、不可用或未退出都不能确认同一失败', () => {
  const incomplete = [
    { timedOut: true, exitCode: null },
    { unavailable: true, exitCode: 2 },
    { exitCode: null },
  ]
  for (const extra of incomplete) {
    const unknown = named([], extra)
    for (const args of [
      { first: named(['new']), rerun: unknown, base: green, latestIsSame: true },
      { first: named([]), rerun: named([]), base: unknown, latestIsSame: true },
      { first: named([]), rerun: named([]), base: green, latest: unknown, latestIsSame: false },
    ]) {
      assert.equal(decideOutcome(args).outcome, 'needs_review', JSON.stringify(args))
    }
  }
  const timeout = named([], { timedOut: true, exitCode: null })
  assert.equal(decideOutcome({ first: timeout, rerun: timeout, base: timeout }).outcome, 'needs_review')
  assert.equal(decideOutcome({ first: timeout, rerun: named([]), base: green, latestIsSame: true }).outcome, 'needs_review')
})

test('decide: 老失败步骤只来自完整对照，不能混入未知步骤或混合失败步骤', () => {
  const step = typedStep
  const first = [step('old', ['known']), step('mixed', ['known', 'new']), step('unknown', ['new'])]
  const rerun = [step('old', ['known']), step('mixed', ['known', 'new']), step('unknown', [], { timedOut: true, exitCode: null })]
  const base = [step('old', ['known']), step('mixed', ['known'], { passed: ['new'] }), r('unknown', true)]
  const d = decideOutcome({ first, rerun, base, latestIsSame: true })
  assert.equal(d.outcome, 'defect')
  assert.deepEqual(d.failedSteps, ['mixed'])
  assert.deepEqual(d.knownOnlyStepIds, ['old'])
  const latest = [step('mixed', [], { passed: ['known', 'new'], ok: true, exitCode: 0, failureParseStatus: 'complete' })]
  assert.equal(decideOutcome({ first, rerun, base, latest, latestIsSame: false }).outcome, 'needs_review', '一个新失败后来通过，不能替其它未知步骤宣称已修复')
})

test('decide: 内部超时、运行器异常和未知原因都不能按重复名字确认 #843', () => {
  for (const type of ['test_timeout', 'runner_error', 'unknown']) {
    const fail = named(['guard'], { failureDetails: [{ name: 'guard', type }] })
    for (const latestIsSame of [true, false]) {
      const d = decideOutcome({ first: fail, rerun: fail, base: green, latest: fail, latestIsSame })
      assert.equal(d.outcome, 'needs_review')
      assert.notEqual(d.attributed, true)
      assert.equal(d.failureKeys, undefined)
      assert.ok(d.unverifiedFailures.some((f) => f.type === type))
      if (type === 'test_timeout') assert.match(d.reasonZh, /内部超时/)
    }
  }
})

test('decide: 同名类型改变在重跑、基线或最新轮都不算 same 或 absent', () => {
  const assertion = named(['guard'])
  const timeout = named(['guard'], { failureDetails: [{ name: 'guard', type: 'test_timeout' }] })
  for (const args of [
    { first: assertion, rerun: timeout, base: green, latestIsSame: true },
    { first: timeout, rerun: assertion, base: green, latestIsSame: true },
    { first: assertion, rerun: assertion, base: timeout, latestIsSame: true },
    { first: assertion, rerun: assertion, base: green, latest: timeout, latestIsSame: false },
  ]) {
    const d = decideOutcome(args)
    assert.equal(d.outcome, 'needs_review')
    assert.ok(d.unverifiedFailures.some((f) => f.reason === 'failure_type_changed'))
  }
  assert.equal(compareKey('unit::guard', timeout, assertion), 'unknown')
})

test('decide: 缺类型的 legacy 失败不猜断言；缺 reporter 不经 whole fallback 判通过或缺陷', () => {
  const legacy = [{ id: 'unit', kind: 'product', ok: false, exitCode: 1, failures: ['guard'] }]
  assert.equal(decideOutcome({ first: legacy, rerun: legacy, base: green, latestIsSame: true }).outcome, 'needs_review')
  const noType = named(['guard'], { failureDetails: null })
  assert.equal(decideOutcome({ first: noType, rerun: noType, base: green, latestIsSame: true }).outcome, 'needs_review')
  const incomplete = named([])
  assert.deepEqual(failureKeys(incomplete), ['unit::*'])
  assert.equal(decideOutcome({ first: incomplete, rerun: incomplete, base: green, latestIsSame: true }).outcome, 'needs_review')
  assert.equal(decideOutcome({ first: incomplete, rerun: green }).outcome, 'needs_review')
  assert.equal(compareKey('unit::guard', named(['guard'])), 'unknown', '无参考类型不能猜 same')
})

test('decide: 明确正文异常和性能阈值断言保留确认能力', () => {
  for (const type of ['assertion', 'test_error']) {
    const fail = named(['explicit timeout budget assertion'], { failureDetails: [{ name: 'explicit timeout budget assertion', type }] })
    assert.equal(decideOutcome({ first: fail, rerun: fail, base: green, latest: fail }).outcome, 'defect')
  }
  const unknown = named(['guard'], { failureDetails: [{ name: 'guard', type: 'unknown' }] })
  assert.equal(decideOutcome({ first: unknown, rerun: green }).outcome, 'flaky', '完整复跑明确通过可记录消失')
})

test('decide: 混合真实回归、内部超时和老失败只确认真实回归', () => {
  const fail = named(['regression', 'guard', 'old'], { failureDetails: [{ name: 'regression', type: 'assertion' }, { name: 'guard', type: 'test_timeout' }, { name: 'old', type: 'assertion' }] })
  const d = decideOutcome({ first: fail, rerun: fail, base: named(['old'], { passed: ['regression', 'guard'] }), latest: fail })
  assert.equal(d.outcome, 'defect')
  assert.deepEqual(d.failureKeys, ['unit::regression'])
  assert.deepEqual(d.failedTests, ['regression'])
  assert.deepEqual(d.knownOnlyStepIds, [])
  assert.equal(d.knownFailureCount, 1)
  assert.ok(d.unverifiedFailures.some((f) => f.key === 'unit::guard' && f.type === 'test_timeout'))
})

test('decide: 最新轮部分未知也计入 unclearCount，不能混入确认键', () => {
  const fail = named(['a', 'b'])
  const latest = named(['a', 'b'], { failureDetails: [{ name: 'a', type: 'assertion' }, { name: 'b', type: 'test_timeout' }] })
  const d = decideOutcome({ first: fail, rerun: fail, base: green, latest })
  assert.equal(d.outcome, 'defect')
  assert.deepEqual(d.failureKeys, ['unit::a'])
  assert.equal(d.unclearCount, 1)
  assert.ok(d.unverifiedFailures.some((f) => f.phase === 'latest' && f.key === 'unit::b'))
})

test('classify: 只改文档是 A，未知路径不进 A，高风险路径是 C', () => {
  assert.equal(classifyChange([{ filename: 'docs/a.md' }, { filename: 'README.md' }]).riskClass, 'A')
  assert.equal(classifyChange([{ filename: 'docs/a.md' }, { filename: 'src/x.ts' }]).riskClass, 'B')
  assert.equal(classifyChange([{ filename: 'scripts/run.md' }]).riskClass, 'B')
  assert.equal(classifyChange([{ filename: 'src/store/persist.ts' }], { cPaths: ['src/**/*persist*'] }).riskClass, 'C')
  assert.ok(globToRegExp('**/*.md').test('a/b/c.md'))
  assert.ok(!globToRegExp('docs/*').test('docs/a/b.md'))
})

test('classify: Mivo 只改 public/changelog.json 走文档通道，其它仓和夹带源码不走', () => {
  const mivo = JSON.parse(readFileSync(new URL('../config/profiles/mivo-canvas-plugin.json', import.meta.url)))
  const cindy = JSON.parse(readFileSync(new URL('../config/profiles/cindy.json', import.meta.url)))
  assert.equal(classifyChange([{ filename: 'public/changelog.json' }], mivo).riskClass, 'A')
  assert.equal(classifyChange([{ filename: 'changelog.json' }], mivo).riskClass, 'B')
  assert.equal(classifyChange([{ filename: 'public/changelog.json' }, { filename: 'src/x.ts' }], mivo).riskClass, 'B')
  assert.equal(classifyChange([{ filename: 'public/changelog.json' }], cindy).riskClass, 'B')
})

test('redact: 去掉 token、邮箱、个人路径、open_id', () => {
  const fakeToken = ['gh', 'p_', 'x'.repeat(30)].join('')
  const s = redact(`${fakeToken} a@b.com /Users/praise/x ou_0123456789abcdef0123456789 Bearer abcdefghijklmnop`)
  assert.ok(!s.includes(fakeToken))
  assert.ok(!s.includes('a@b.com'))
  assert.ok(!s.includes('/Users/praise'))
  assert.ok(!s.includes('ou_0123'))
  assert.ok(!s.includes('abcdefghijklmnop'))
  const t = redact('at file://~/Library/Caches/PraiseZhu/argus-sandbox/cache/runs/1/src/sum.test.mjs:6:10 and /Users/praise/Library/Caches/x/y.js')
  assert.ok(!t.includes('Library'), t)
})

test('失败用例名识别：vitest / pytest / node:test', async () => {
  const { toFailureName } = await import('../src/exec.mjs')
  assert.equal(toFailureName('\x1b[31m FAIL \x1b[39m src/a.test.ts > suite > works 12ms'), 'src/a.test.ts > suite > works')
  assert.equal(toFailureName('FAILED tests/test_x.py::test_y - AssertionError: boom'), 'tests/test_x.py::test_y')
  assert.equal(toFailureName('✖ sum adds two numbers (0.8ms)'), 'sum adds two numbers')
  assert.equal(toFailureName('✖ failing tests:'), null)
  assert.equal(toFailureName('[design-literal-ratchet] FAIL src/App.css:12016 新增裸颜色'), null)
})

test('标题去掉英文前缀', () => {
  assert.equal(toReadableTitle('fix(library): 生成图待入库状态 (#618)'), '生成图待入库状态')
})

const reg = {
  owner: { login: 'PraiseZhu', id: 1 },
  issueRepo: 'PraiseZhu/pr-qa-agent-deploy',
  enabledSince: '2026-09-23T00:00:00+08:00',
  projects: [{ repo: 'xindong/mivo-canvas-plugin', displayName: 'Mivo 插件' }, { repo: 'makecindy/cindy', displayName: 'Cindy' }],
}

function mkJob(db, n, extra = {}) {
  insertJob(db, { repo: 'xindong/mivo-canvas-plugin', prNumber: n, prNodeId: `N${n}`, prTitle: `feat(x): 功能 ${n}`, prUrl: `https://github.com/x/y/pull/${n}`, authorLogin: 'PraiseZhu', baseRef: 'main', mergedAt: `2026-09-23T0${n}:00:00Z`, mergeSha: `sha${n}`, ...extra })
}

test('db: 同一合并版本只入账一次；重启把 running 放回队列', () => {
  const db = openDb(join(process.env.ARGUS_ROOT, 'a.sqlite'))
  mkJob(db, 1)
  mkJob(db, 1)
  assert.equal(db.prepare('SELECT COUNT(*) n FROM jobs').get().n, 1)
  const j = nextQueuedJob(db)
  markRunning(db, j.id)
  assert.equal(recoverRunning(db), 1)
  assert.equal(nextQueuedJob(db).id, j.id)
})

test('日报：有缺陷时结论先说问题，并给 issue 链接，不含代码痕迹', () => {
  const db = openDb(join(process.env.ARGUS_ROOT, 'b.sqlite'))
  setMeta(db, 'heartbeat_at', new Date().toISOString())
  mkJob(db, 1)
  mkJob(db, 2)
  insertJob(db, { repo: 'makecindy/cindy', prNumber: 9, prNodeId: 'N9', prTitle: 'fix: 修东西', prUrl: 'u', authorLogin: 'PraiseZhu', baseRef: 'main', mergedAt: '2026-09-23T05:00:00Z', mergeSha: 's9', status: 'done', outcome: 'not_enrolled', detail: { reasonZh: 'x' } })
  const results = [{ id: 'install', kind: 'setup', ok: true, nameZh: '安装依赖' }, { id: 'unit', kind: 'product', ok: true, nameZh: '单元测试' }, { id: 'build', kind: 'product', ok: true, nameZh: '打包构建' }]
  finishJob(db, 1, { outcome: 'passed', riskClass: 'B', detail: { coverage: { applicable: [], covered: [], missed: [], undetermined: [] }, results, ai: { status: 'ok', risksZh: ['大图导入没测到'], suspectZh: '' } } })
  finishJob(db, 2, { outcome: 'defect', riskClass: 'C', detail: { results, failedSteps: ['unit'], reasonZh: '合并后检查失败，合并前通过，最新主干仍然失败', issueUrl: 'https://github.com/PraiseZhu/pr-qa-agent-deploy/issues/1' } })
  const { text, jobCount } = buildDailyReport({ db, reg, now: new Date(Date.now() + 1000), diskFreeGb: 25 })
  assert.equal(jobCount, 3)
  const lines = text.split('\n')
  assert.equal(lines[2], lines.find((l) => l.startsWith('结论')), '结论必须是正文第一行')
  assert.match(lines[2], /^结论：今天有 1 件事要你处理：.*#2 有问题。另有 1 个 PR 的已执行检查未见新问题。$/)
  assert.ok(text.includes('❌ '), text)
  assert.ok(text.includes('新提 issue：https://github.com/PraiseZhu/pr-qa-agent-deploy/issues/1'))
  assert.ok(text.includes('【要做】打开'), text)
  assert.ok(text.includes('测试没覆盖到的地方') && text.indexOf('测试没覆盖到的地方') > text.indexOf('附录（AI 提示和运行状态）'), 'AI 风险提示只进附录')
  assert.ok(text.includes('【可选】Cindy 有 PR 合并但还没接入自动测试'))
  assert.ok(!/sha\d|feat\(|npm |\/Users\//.test(text), text)
})

test('日报：服务停了要在第一行说清楚', () => {
  const db = openDb(join(process.env.ARGUS_ROOT, 'c.sqlite'))
  setMeta(db, 'heartbeat_at', new Date(Date.now() - 3 * 3600_000).toISOString())
  const { text } = buildDailyReport({ db, reg, now: new Date() })
  assert.match(text.split('\n')[2], /^结论：今天有 1 件事要你处理：QA 服务停了。$/)
  assert.ok(text.includes('【要做】QA 服务从'), text)
})

test('issue：首行 @ 作者，指纹不含 PR 号和 SHA', () => {
  const results = [{ ...r('unit', false), tail: 'FAIL src/a.test.ts > renders 12ms' }]
  const fp1 = defectFingerprint('a/b', { failedSteps: ['unit'] }, results)
  const fp2 = defectFingerprint('a/b', { failedSteps: ['unit'] }, [{ ...r('unit', false), tail: 'FAIL src/a.test.ts > renders 99ms' }])
  assert.equal(defectFingerprint('a/b', { failedSteps: ['unit'], failureKeys: ['unit::x > y'] }, results), defectFingerprint('a/b', { failedSteps: ['unit'], failureKeys: ['unit::x > y'] }, []))
  assert.equal(fp1, fp2)
  const job = { id: 3, repo: 'a/b', pr_number: 7, pr_url: 'u', pr_title: 'fix: x', merge_sha: 'abcdef1234567890' }
  const { title, body } = buildIssue({ job, ownerLogin: 'PraiseZhu', decision: { failedSteps: ['unit'], reasonZh: 'r' }, results: [{ ...results[0], cmdText: 'npm run test:unit' }], ai: null, fingerprint: fp1, publicationId: 'p1', displayName: 'B' })
  assert.ok(title.startsWith('[PR-QA]'))
  assert.equal(body.split('\n')[0], '@PraiseZhu')
  assert.ok(body.includes('argus-publication: p1'))
})
