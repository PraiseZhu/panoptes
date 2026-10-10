// 真实本地 Git → 隔离命令 → 多轮判定 → JSON 证据 → SQLite → 日报发送边界。
// GitHub 使用进程级 fake；模型显式不配置；飞书仅内存 fake，绝不外发。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = mkdtempSync(join(tmpdir(), 'argus-pipeline-'))
process.env.ARGUS_ROOT = root
process.env.ARGUS_GH_BIN = join(here, 'fixtures/argus-fake-gh.mjs')
process.env.ARGUS_GH_RETRY_MS = '1'
process.env.ARGUS_SECRETS_FILE = join(root, 'intentionally-no-secrets.env')
process.env.ARGUS_E2E_GH_STATE = join(root, 'github-fixture.json')
const { processJob } = await import('../src/control.mjs')
const { openDb, insertJob, finishJob, setMeta, getOutbox } = await import('../src/db.mjs')
const { sendDailyReport } = await import('../src/daily.mjs')
const { verifyCheckoutSha } = await import('../src/runner.mjs')
const { toVerdict, buildDailyReport } = await import('../src/report.mjs')
const { DIMENSIONS } = await import('../src/matrix.mjs')
const { EVIDENCE_DIR, MIRRORS_DIR, RUNS_DIR } = await import('../src/paths.mjs')
const fetchCalls = []
globalThis.fetch = async (url, init) => {
  assert.match(String(url), /^https:\/\/open\.feishu\.cn\//, '仅允许显式 fake 的飞书边界')
  fetchCalls.push({ url: String(url), body: JSON.parse(init.body) })
  if (String(url).includes('tenant_access_token')) return { json: async () => ({ code: 0, tenant_access_token: 'fixture-token' }) }
  return { status: 200, json: async () => ({ code: 0, data: { message_id: 'fixture-message' } }) }
}
const db = openDb(join(root, 'fixture.sqlite'))
after(() => { db.close(); rmSync(root, { recursive: true, force: true }) })

const GUARD_843 = 'src/store/generationEntryEnumerationGuard.test.ts > contract: no generation entry lacks placement intent > no_generation_entry_lacks_placement_intent: 全部出图调用点已声明意图(未声明条数==0)'

function fixture(number, { unavailable = false, fail = false, failIssues = false, vitestTimeout = false, nodeTimeout = false, transient = false, baseSkipped = false, rerunOnly = false } = {}) {
  const repo = `fixture/pr${number}`
  const src = join(root, `source-${number}`)
  mkdirSync(src)
  const git = (...args) => execFileSync('git', ['-C', src, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()
  git('init', '-q', '-b', 'main')
  writeFileSync(join(src, 'value.mjs'), 'export const value = 1\n')
  writeFileSync(join(src, 'behavior.test.mjs'), "import { test } from 'node:test'; import assert from 'node:assert/strict'; import { value } from './value.mjs'; test('value remains positive', () => assert.ok(value > 0))\n")
  if (baseSkipped) {
    writeFileSync(join(src, 'behavior.test.mjs'), "import {test} from 'node:test';import assert from 'node:assert/strict';import {value} from './value.mjs';test('value guard',{skip:true},()=>assert.ok(value>1));\n")
  } else if (rerunOnly) {
    const marker = join(root, `rerun-new-${number}`)
    writeFileSync(join(src, 'behavior.test.mjs'), `import {test} from 'node:test';import assert from 'node:assert/strict';import {existsSync,writeFileSync} from 'node:fs';import {value} from './value.mjs';const marker=${JSON.stringify(marker)};const first=!existsSync(marker);if(value!==1)writeFileSync(marker,'ran');test('old failure',()=>assert.fail());test('new in rerun',()=>assert.ok(value===1||first));\n`)
  } else if (vitestTimeout) {
    // #843 的失败形态：两项老断言失败，加一个内部超时；外层正常退出 1。
    const baseOutput = 'FAIL src/old.test.ts > old path\nAssertionError: fixture old path\nFAIL src/old.test.ts > old env\nAssertionError: fixture old env\n'
    const mergedOutput = `${baseOutput}FAIL ${GUARD_843}\nError: Test timed out in 5000ms.\n`
    writeFileSync(join(src, 'behavior.test.mjs'), `import { value } from './value.mjs'; process.stdout.write(value === 1 ? ${JSON.stringify(baseOutput + 'Tests 2 failed | 1 passed (3)\n')} : ${JSON.stringify(mergedOutput + 'Tests 3 failed (3)\n')}); process.exitCode=1;\n`)
  } else if (nodeTimeout) {
    writeFileSync(join(src, 'behavior.test.mjs'), "import { test } from 'node:test'; import { value } from './value.mjs'; test('node timeout guard', { timeout: 20 }, async () => { if (value !== 1) await new Promise(resolve => setTimeout(resolve, 200)); });\n")
  } else if (transient) {
    const marker = join(root, `rerun-${number}`)
    writeFileSync(join(src, 'behavior.test.mjs'), `import { test } from 'node:test'; import assert from 'node:assert/strict'; import { existsSync, writeFileSync } from 'node:fs'; import { value } from './value.mjs'; const marker = ${JSON.stringify(marker)}; const first = value !== 1 && !existsSync(marker); if (value !== 1) writeFileSync(marker, 'ran'); test('old failure', () => assert.equal(1, 2)); test('transient guard', () => assert.ok(!first));\n`)
  }
  git('add', '.'); git('commit', '-qm', 'base')
  const baseSha = git('rev-parse', 'HEAD')
  writeFileSync(join(src, 'value.mjs'), `export const value = ${baseSkipped ? 1 : fail ? 0 : 2}\n`)
  if (baseSkipped) writeFileSync(join(src, 'behavior.test.mjs'), readFileSync(join(src, 'behavior.test.mjs'), 'utf8').replace('skip:true', 'skip:false'))
  git('commit', '-qam', `change (#${number})`)
  const mergeSha = git('rev-parse', 'HEAD')
  mkdirSync(MIRRORS_DIR, { recursive: true })
  execFileSync('git', ['clone', '--bare', src, join(MIRRORS_DIR, `fixture__pr${number}.git`)], { stdio: 'pipe' })
  const profile = {
    pathHints: { 'value.mjs': ['unit', ...(unavailable ? ['e2e'] : [])] },
    notApplicable: Object.fromEntries(DIMENSIONS.filter((d) => !['unit', ...(unavailable ? ['e2e'] : [])].includes(d.key)).map((d) => [d.key, 'fixture scope'])),
    steps: [
      { id: 'install', kind: 'setup', nameZh: '准备', cmd: [process.execPath, '-e', ''] },
      { id: 'unit', kind: 'product', nameZh: '单元测试', covers: ['unit'], cmd: vitestTimeout ? [process.execPath, 'behavior.test.mjs'] : [process.execPath, '--test', '--test-reporter=spec', '--test-reporter=${NODE_TEST_REPORTER}', '--test-reporter-destination=stdout', '--test-reporter-destination=${TEST_REPORT}', 'behavior.test.mjs'], ...(vitestTimeout ? {} : { testReport: { format: 'node-events', path: '${RUN}/reports/unit.jsonl' } }) },
      ...(unavailable ? [{ id: 'browser', kind: 'product', nameZh: '浏览器端到端', optional: true, covers: ['e2e'], requires: [{ file: 'missing-browser.mjs', reasonZh: '旧版本没有浏览器脚本' }], cmd: [process.execPath, 'missing-browser.mjs'] }] : []),
    ],
  }
  const now = new Date().toISOString()
  const pr = { number, node_id: `fixture-${number}`, title: 'fix: 测试夹具', html_url: `https://example.invalid/pr/${number}`, user: { id: 99, login: 'fixture' }, base: { ref: 'main' }, merged_at: now, merge_commit_sha: mergeSha, commits: 1 }
  insertJob(db, { repo, prNumber: number, prNodeId: pr.node_id, prTitle: pr.title, prUrl: pr.html_url, authorLogin: 'fixture', baseRef: 'main', mergedAt: now, mergeSha })
  const job = db.prepare('SELECT * FROM jobs WHERE repo = ?').get(repo)
  writeFileSync(process.env.ARGUS_E2E_GH_STATE, JSON.stringify({ pr, baseSha, jobId: job.id, failIssues, calls: [] }))
  const reg = { owner: { login: 'fixture', id: 99 }, issueRepo: 'fixture/deploy', enabledSince: now, projects: [{ repo, displayName: '夹具项目', profileData: profile }] }
  return { src, job, reg, baseSha, mergeSha }
}

test('P1: 产品源码未变、基线跳过目标用例，不得归因或调用 issue', async () => {
  const f = fixture(1001, { baseSkipped: true })
  const before = execFileSync('git', ['-C', f.src, 'show', `${f.baseSha}:value.mjs`], { encoding: 'utf8' })
  assert.equal(before, readFileSync(join(f.src, 'value.mjs'), 'utf8'))
  const result = await processJob({ db, reg: f.reg, secrets: {}, job: f.job })
  assert.equal(result.outcome, 'needs_review')
  assert.match(result.detail.reasonZh, /目标用例实际执行/)
  const evidence = JSON.parse(readFileSync(join(EVIDENCE_DIR, result.detail.evidenceFile)))
  const base = evidence.runs.base.find((r) => r.id === 'unit')
  assert.equal(base.testOutcomeStatus, 'complete')
  assert.equal(base.testOutcomes.find((r) => r.failureName === 'value guard').status, 'skipped')
  const state = JSON.parse(readFileSync(process.env.ARGUS_E2E_GH_STATE))
  assert.equal(state.calls.filter((r) => r?.includes('/issues')).length, 0)
  assert.equal(db.prepare("SELECT count(*) n FROM outbox WHERE kind='issue'").get().n, 0)
})

test('P1: 重跑新增失败经真实 runner 留在 evidence、覆盖与日报中', async () => {
  const f = fixture(1002, { rerunOnly: true })
  const result = await processJob({ db, reg: f.reg, secrets: {}, job: f.job })
  assert.equal(result.outcome, 'needs_review')
  assert.ok(result.detail.unverifiedFailures.some((r) => r.key === 'unit::new in rerun' && r.phase === 'rerun'))
  assert.ok(result.detail.coverage.missed.includes('unit'))
  const verdict = toVerdict({ outcome: result.outcome }, result.detail)
  assert.equal(verdict.key, 'unsure')
  assert.match(verdict.reasonZh, /新的失败/)
  const evidence = JSON.parse(readFileSync(join(EVIDENCE_DIR, result.detail.evidenceFile)))
  assert.deepEqual(evidence.decision.knownOnlyStepIds, [])
  assert.ok(evidence.runs.rerun.find((r) => r.id === 'unit').testOutcomes.some((t) => t.failureName === 'new in rerun' && t.status === 'failed'))
  const state = JSON.parse(readFileSync(process.env.ARGUS_E2E_GH_STATE))
  assert.equal(state.calls.filter((r) => r?.includes('/issues')).length, 0)
})

test('完整链路：真实通过 + 浏览器缺失 → 保留版本证据 → 日报明确未完成，fake 发送只一次', async () => {
  const f = fixture(1, { unavailable: true })
  const result = await processJob({ db, reg: f.reg, secrets: {}, job: f.job })
  finishJob(db, f.job.id, result)
  assert.equal(result.outcome, 'passed')
  assert.deepEqual(result.detail.coverage.missed, ['e2e'])
  const evidence = JSON.parse(readFileSync(join(EVIDENCE_DIR, result.detail.evidenceFile)))
  assert.equal(evidence.executions.first.checkedOutSha, f.mergeSha)
  assert.equal(evidence.versions.baseSha, f.baseSha)
  assert.equal(evidence.executions.base, null, '未执行基线时不能伪称执行')
  assert.equal(evidence.versions.latestIsSame, null, '未查询主干时不能伪称已确认相同')
  assert.equal(evidence.runs.first.find((r) => r.id === 'browser').unavailable, true)
  assert.ok(evidence.runs.first.find((r) => r.id === 'unit').summary.lines.some((s) => /tests 1$/.test(s)))
  assert.equal(evidence.runs.first.find((r) => r.id === 'unit').exitCode, 0)
  await assert.rejects(verifyCheckoutSha(f.src, f.baseSha), /实际检出版本与请求不一致/)
  setMeta(db, 'heartbeat_at', new Date().toISOString())
  const secrets = { ARGUS_FEISHU_APP_ID: 'fixture', ARGUS_FEISHU_APP_SECRET: 'fixture', ARGUS_FEISHU_OPEN_ID: 'fixture' }
  const now = new Date(Date.now() + 1000)
  const sent = await sendDailyReport({ db, reg: f.reg, secrets, now })
  assert.equal(sent.status, 'sent')
  const outbox = getOutbox(db, `daily:${sent.day}`)
  const text = JSON.parse(outbox.payload_json).text
  assert.match(text, /未完成验证|不能确认全部适用检查完成/)
  assert.match(text, /浏览器|端到端/)
  assert.equal((await sendDailyReport({ db, reg: f.reg, secrets, now })).status, 'already_sent')
  assert.equal(fetchCalls.filter((c) => !c.url.includes('tenant_access_token')).length, 1)
  assert.equal(outbox.status, 'sent')
})

test('混合老失败：真实重跑通过 → 脱敏证据与 SQLite 保留 → 日报展示，不调用 issue', async () => {
  const f = fixture(835, { transient: true })
  const result = await processJob({ db, reg: f.reg, secrets: {}, job: f.job })
  finishJob(db, f.job.id, result)
  assert.equal(result.outcome, 'passed_known_failures')
  assert.equal(result.detail.knownFailureCount, 1)
  assert.equal(result.detail.transientFailureCount, 1)
  assert.deepEqual(result.detail.transientFailures, [{ key: 'unit::transient guard', type: 'assertion' }])
  const evidence = JSON.parse(readFileSync(join(EVIDENCE_DIR, result.detail.evidenceFile)))
  assert.deepEqual(evidence.runs.first.find((r) => r.id === 'unit').failures, ['old failure', 'transient guard'])
  assert.deepEqual(evidence.runs.rerun.find((r) => r.id === 'unit').failures, ['old failure'])
  assert.deepEqual(evidence.runs.base.find((r) => r.id === 'unit').failures, ['old failure'])
  assert.deepEqual(evidence.decision.knownOnlyStepIds, [])
  assert.deepEqual(evidence.decision.transientFailures, result.detail.transientFailures)
  const stored = JSON.parse(db.prepare('SELECT detail_json FROM jobs WHERE id = ?').get(f.job.id).detail_json)
  assert.deepEqual(stored.transientFailures, result.detail.transientFailures)
  assert.equal(stored.transientFailureCount, 1)
  setMeta(db, 'heartbeat_at', new Date().toISOString())
  const { text } = buildDailyReport({ db, reg: f.reg, from: new Date(Date.parse(f.job.created_at) - 1000).toISOString(), now: new Date(Date.now() + 1000), issues: { issues: [] } })
  assert.match(text, /重跑结果：1 项首次失败、重跑通过，原因尚未确认/)
  assert.match(text, /老失败：夹具项目 1 个/)
  assert.ok(!text.includes('transient guard'), '日报只展示数量')
  assert.ok(!JSON.parse(readFileSync(process.env.ARGUS_E2E_GH_STATE)).calls.some((s) => /\/issues(?:\/|\?)/.test(s)))
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM outbox WHERE dedupe_key = ?').get(`defect:${f.job.id}`).n, 0)
})

test('失败链路：真实回归三轮对照后，外部发布故障仍保留已完成证据', async () => {
  const f = fixture(2, { fail: true, failIssues: true })
  const result = await processJob({ db, reg: f.reg, secrets: {}, job: f.job })
  assert.equal(result.outcome, 'defect')
  assert.equal(result.detail.issuePending, true)
  finishJob(db, f.job.id, result)
  const evidence = JSON.parse(readFileSync(join(EVIDENCE_DIR, `job-${f.job.id}.json`)))
  assert.equal(evidence.decision.outcome, 'defect')
  assert.equal(evidence.executions.first.checkedOutSha, f.mergeSha)
  assert.equal(evidence.executions.rerun.checkedOutSha, f.mergeSha)
  assert.equal(evidence.executions.base.checkedOutSha, f.baseSha)
  assert.equal(evidence.versions.latestHeadSha, f.mergeSha)
  assert.equal(evidence.versions.latestIsSame, true)
  assert.equal(evidence.executions.latest, null)
  assert.equal(evidence.runs.first.find((r) => r.id === 'unit').exitCode, 1)
  assert.equal(evidence.runs.first.find((r) => r.id === 'unit').failureParseStatus, 'complete')
  assert.equal(evidence.runs.first.find((r) => r.id === 'unit').failureDetails[0].type, 'assertion')
  assert.equal(evidence.runs.base.find((r) => r.id === 'unit').exitCode, 0)
  assert.ok(evidence.decision.failureKeys.some((k) => k.includes('value remains positive')))
  const calls = JSON.parse(readFileSync(process.env.ARGUS_E2E_GH_STATE)).calls
  assert.ok(calls.some((s) => s.includes('/issues?')), '必须确实抵达 fake 发布边界')
  const { readdirSync } = await import('node:fs')
  assert.deepEqual(readdirSync(RUNS_DIR), [], '执行完成后清理临时树，证据仍在持久目录')
})

test('#843 超时形态：真实 Git→多轮执行→待核实，没有 issue 调用或 defect outbox', async () => {
  const f = fixture(843, { vitestTimeout: true })
  const result = await processJob({ db, reg: f.reg, secrets: {}, job: f.job })
  finishJob(db, f.job.id, result)
  assert.equal(result.outcome, 'needs_review')
  assert.match(result.detail.reasonZh, /内部超时/)
  assert.equal(result.detail.issuePending, undefined)
  assert.equal(result.detail.issueUrl, undefined)
  const evidence = JSON.parse(readFileSync(join(EVIDENCE_DIR, result.detail.evidenceFile)))
  for (const phase of ['first', 'rerun']) {
    const r = evidence.runs[phase].find((r) => r.id === 'unit')
    assert.equal(r.exitCode, 1)
    assert.equal(r.timedOut, false)
    assert.equal(r.failureParseStatus, 'complete')
    assert.deepEqual(r.failureDetails.find((r) => r.name === GUARD_843), { name: GUARD_843, type: 'test_timeout' })
  }
  assert.equal(evidence.runs.base.find((r) => r.id === 'unit').failures.length, 2)
  assert.equal(evidence.decision.outcome, 'needs_review')
  assert.equal(evidence.decision.knownFailureCount, 2)
  assert.deepEqual(evidence.decision.knownOnlyStepIds, [])
  assert.equal(evidence.decision.attributed, undefined)
  assert.equal(evidence.decision.failureKeys, undefined)
  assert.ok(result.detail.unverifiedFailures.some((r) => r.key === `unit::${GUARD_843}` && r.type === 'test_timeout'))
  assert.deepEqual(result.detail.results.find((r) => r.id === 'unit').failureDetails, evidence.runs.first.find((r) => r.id === 'unit').failureDetails)
  const verdict = toVerdict({ ...f.job, outcome: result.outcome }, result.detail)
  assert.equal(verdict.key, 'unsure')
  assert.match(verdict.reasonZh, /内部超时/)
  const calls = JSON.parse(readFileSync(process.env.ARGUS_E2E_GH_STATE)).calls
  assert.ok(calls.length > 0, '控制链确实经过 fake metadata 入口')
  assert.ok(!calls.some((s) => /\/issues(?:\/|\?)/.test(s)), '禁止 issue 查询、创建、评论及重开')
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM outbox WHERE dedupe_key = ?').get(`defect:${f.job.id}`).n, 0)
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM defects WHERE first_job_id = ?').get(f.job.id).n, 0)
})

test('真实 Node spec 内部超时同样待核实，不抵达 fake issue 发布入口', async () => {
  const f = fixture(844, { nodeTimeout: true })
  const result = await processJob({ db, reg: f.reg, secrets: {}, job: f.job })
  assert.equal(result.outcome, 'needs_review')
  const evidence = JSON.parse(readFileSync(join(EVIDENCE_DIR, result.detail.evidenceFile)))
  assert.equal(evidence.runs.first.find((r) => r.id === 'unit').failureDetails[0].type, 'test_timeout')
  assert.equal(evidence.runs.first.find((r) => r.id === 'unit').timedOut, false)
  assert.ok(!JSON.parse(readFileSync(process.env.ARGUS_E2E_GH_STATE)).calls.some((s) => /\/issues(?:\/|\?)/.test(s)))
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM outbox WHERE dedupe_key = ?').get(`defect:${f.job.id}`).n, 0)
})
