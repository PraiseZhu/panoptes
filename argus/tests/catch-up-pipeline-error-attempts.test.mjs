// 「补测放弃：catch-up gave up after 3 attempts (last: exit 3; missing)」——10-29 22:07 / 23:22 / 23:40（北京）三轮补测里，
// #1411 / #1413 / #1419 / #1422 四条已合并 PR 全部被放弃：reports/catch-up/state.json 里四个条目都是 attempts=3、
// last_reason="exit 3; missing"、gave_up_at=2026-10-30T00:23:*；三份 run-20261029T232159 / -233353 / -20261030T002334.json
// 里这四条 PR 一共 12 次子运行，每次都是 exit 3，而且 merged/PR-N.json 一直没出现（remaining_gap="missing"）。
//
// 现场：catch-up 的子运行只测一个 PR（run-nightly.sh --pr N），它的退出码 3 = 「pipeline 失败 / QA 环境坏」
// （run-nightly.sh 的 Exit codes 注释；cli.mjs:78 只在 report.system_health.status==="failed" 时给 3，而 --pr 运行里这个状态
// 只来自 qa-error，nightly.mjs:1611/1640）。也就是说这 12 次一个测试都没跑：
// reports/catch-up/PR-1411.json 的 prs[0] = { verdict: "qa-error", tested: false,
// error: "检出版本失败：fatal: unable to read tree (3b416d02…)" }、system_health.status = "failed"。
// 同一个工具故障（合并 commit 不在本地镜像里）在三条 PR 上重复出现，跟这四条 PR 本身能不能测无关 ——
// 真正的修法在 runner.mjs:160 的 checkout（#106 / 47e434c，10-29 23:38 合并）。
//
// 缺陷在 argus/src/catchup.mjs:147-159：catch-up 只看「子运行退出码 + merged 文件还在不在」，把 exit 3
// （自测系统自己出错、一个测试都没跑）当成一次正常尝试累计，于是 66 分钟内的三轮补测就把四条 PR 的 3 次预算烧光，
// state.json 永久写下 gave_up_at，之后 catchup.mjs:130 永远跳过它们 —— 装备修好也补不到：现在
// /workspace/mivo-qa/reports/merged/PR-1411.json、PR-1413.json、PR-1419.json、PR-1422.json 仍然缺失。
// 同一个文件里「内存不足没开测」（exit 76，catchup.mjs:148-152）已经写明「撤回这次计数，不放弃」，等锁超时（exit 75）
// 也是「停下，下次再试」；exit 3 是同一类「一个测试都没跑」，同样不该烧掉 PR 的补测预算。
//
// 因此这里的断言是：子运行反复以 exit 3 收场（PR 没测到）时，catch-up 不得累计尝试、不得放弃；工具恢复后照常补上。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { catchUp, statePath, recentGiveUps } from '../src/catchup.mjs'

// 事件里那四条已合并 PR（merge_sha 取自 reports/catch-up/state.json 原文）
const MERGED = [
  { number: 1411, merge_sha: '3b416d02ddf83e3e09c855f1368cc56d5ab20324' },
  { number: 1413, merge_sha: 'aafb8305c7ffcf8a0192e1c45f78aac757302277' },
  { number: 1419, merge_sha: '86df970e46f974f0d62b5f9dccc61285bdd83f0c' },
  { number: 1422, merge_sha: '5965c64e449e46e03b35608c3605cfa039cd041d' },
]
const PRS = MERGED.map((p) => ({ ...p, merged_at: '2026-10-29T13:42:26Z', day: '2026-10-29' }))
const NOW = new Date('2026-10-30T00:23:34+08:00')
const QA_ERROR = `检出版本失败：fatal: unable to read tree (${MERGED[0].merge_sha})`

function fixture () {
  const reportsDir = mkdtempSync(join(tmpdir(), 'argus-cu-pipe-'))
  mkdirSync(join(reportsDir, 'merged'), { recursive: true })
  return reportsDir
}
const stateOf = (reportsDir) => JSON.parse(readFileSync(statePath(reportsDir), 'utf8')).prs
const writeMerged = (reportsDir, pr) => writeFileSync(join(reportsDir, 'merged', `PR-${pr.number}.json`),
  JSON.stringify({ merged_sha: pr.merge_sha, prs: [{ number: pr.number, verdict: 'clean', tested_sha: pr.merge_sha }] }))
// QA 工具故障的子运行：和 10-29 那四份 reports/catch-up/PR-14xx.json 同形 —— 只写补测自己的报告，不写 merged/PR-N.json
function pipelineErrorChild (reportsDir) {
  return (pr) => {
    mkdirSync(join(reportsDir, 'catch-up'), { recursive: true })
    writeFileSync(join(reportsDir, 'catch-up', `PR-${pr.number}.json`), JSON.stringify({
      schema_version: '1.0',
      repo: 'xindong/mivo-canvas-plugin',
      run: { id: `run-${pr.number}`, trigger: 'event' },
      prs: [{ number: pr.number, verdict: 'qa-error', severity: 'untested', tested: false, error: QA_ERROR }],
      system_health: { status: 'failed', qa_tool_failures: [{ pr: pr.number, stage: 'pipeline', message: QA_ERROR }] },
    }))
    return 3
  }
}
const run = (reportsDir, runOne) => catchUp({ repo: 'xindong/mivo-canvas-plugin', now: NOW, reportsDir, list: () => PRS, runOne, active: () => new Set() })

test('补测：子运行反复 exit 3（QA 工具故障，PR 一个测试都没跑）不累计尝试、不放弃，工具恢复后照常补上', () => {
  const reportsDir = fixture()
  const child = pipelineErrorChild(reportsDir)
  const tried = []
  // 10-29 22:07 / 23:22 / 23:40 三轮：每轮四条 PR 都是 exit 3 + merged 仍然缺失
  for (let round = 1; round <= 3; round++) {
    const r = run(reportsDir, (pr) => { tried.push(pr.number); return child(pr) })
    assert.equal(r.status, 'ran')
    assert.deepEqual(r.runs.map((x) => x.exit_code), [3, 3, 3, 3], `第 ${round} 轮：四条 PR 都是 QA 工具故障（exit 3）`)
    assert.deepEqual(r.system_health.gave_up, [], `第 ${round} 轮：一个测试都没跑（exit 3），不该记为放弃`)
    assert.deepEqual(r.system_health.qa_tool_failures, [], `第 ${round} 轮：exit 3 不该写成 stage=catch-up 的放弃记录`)
  }
  assert.deepEqual(tried, [1411, 1413, 1419, 1422, 1411, 1413, 1419, 1422, 1411, 1413, 1419, 1422])
  for (const [key, st] of Object.entries(stateOf(reportsDir))) {
    assert.equal(st.gave_up_at, undefined, `PR #${key}：QA 工具故障不许留下永久放弃标记（之后再也补不到）`)
  }
  assert.deepEqual(recentGiveUps({ reportsDir, now: NOW.getTime() }), [], '日报 system_health 不该带出这类放弃')
  // 工具故障修好（#106）之后：同一批 PR 仍然会被补测，并且这次补上，state 清空
  const fixed = run(reportsDir, (pr) => { writeMerged(reportsDir, pr); return 0 })
  assert.equal(fixed.status, 'ran', 'exit 3 之后 catch-up 必须还会再试这些缺口（不能永久跳过）')
  assert.deepEqual(fixed.runs.map((x) => [x.number, x.filled]), [[1411, true], [1413, true], [1419, true], [1422, true]])
  assert.deepEqual(stateOf(reportsDir), {}, '补上之后不再留状态')
})

test('补测：子运行跑完（exit 0）却仍没有可用结论时照旧 3 次放弃——放弃机制不能被这次修复清掉', () => {
  const reportsDir = fixture()
  let r
  for (let round = 1; round <= 3; round++) r = run(reportsDir, () => 0) // 跑完但没写 merged（例如 not-tested）
  assert.equal(r.system_health.gave_up.length, PRS.length)
  assert.deepEqual(r.system_health.gave_up.map((g) => [g.pr, g.attempts, g.stage]), [[1411, 3, 'catch-up'], [1413, 3, 'catch-up'], [1419, 3, 'catch-up'], [1422, 3, 'catch-up']])
  assert.equal(r.system_health.status, 'degraded')
  // 放弃之后不再反复试（这一条以前就有，别为了消掉 exit 3 的签名把它一起去掉）
  const next = run(reportsDir, () => { throw new Error('放弃的 PR 不该再被调用') })
  assert.equal(next.status, 'nothing-to-do')
  assert.equal(recentGiveUps({ reportsDir, now: NOW.getTime() }).length, PRS.length)
})
