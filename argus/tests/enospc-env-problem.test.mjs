// inf1-c1bdc506ab8964e60e0f 回归保护（11-05 03:04 北京时间，PR #1789）：
// 报告 reports/catch-up/PR-1789.json 的 system_health 是
//   { status: "failed", qa_tool_failures: [{ pr: 1789, stage: "pipeline",
//     message: "ENOSPC: no space left on device, mkdir '/tmp/argus-run-S1rCD9/argus-n1789-h1-mw2hjrvw/step-e2e-browser'" }],
//     env_problems: [], environment: { disk_free_gb: 52 } }
// 同一轮 logs/nightly-20261105T030402.log 里留证据也失败在同一件事上：
//   keep evidence failed: ENOSPC, mkdir '/workspace/mivo-qa/argus-root/app/evidence/nightly/2026-11-05/adf3ae28/pr-1789/h1'
// 两处都是「建目录」被操作系统拒绝，而字节还剩 52 GB —— 这台箱子是 inode 用满（11-05 02:42 的 #1729 是同一件事，
// 那次只差在步骤名 step-plugin，所以被当成另一条签名）。
//
// 已经修好的一半：c98d974（inf1-017aa4a1）让 envSnapshot 记 inode、checkDiskInodes 把「inode 用满」记进
// env_problems（src/nightly.mjs:1204 / 1223），原因是看得见了。
// 还坏的一半（本文件钉的）：这个错误仍然走 processTargets 的通用 catch，被记成 QA 工具故障
// `qa_tool_failures[{ stage: 'pipeline' }]`（src/nightly.mjs:1680），于是 verdict=qa-error、severity=untested、
// system_health.status=failed、complete=false（src/nightly.mjs:1709 / 1721）。后果是：
//   · 「箱子建不出目录」被算成 panoptes 自己的代码故障（infra 队列里就是要按 panoptes 代码缺陷开工单的那一类）；
//   · 消息里带着失败那一步的临时目录名，换个步骤就是一条新签名（#1729 的 step-plugin → #1789 的 step-e2e-browser），
//     同一台箱子的一次 inode 耗尽会持续产出「panoptes 机制有 bug」的工单；
//   · 证据文件里 env_problems 是空的，报告没说清箱子该清什么。
//
// 正确收场在报告 schema 里已有先例：环境问题记 env_problems（docs/report-schema.md:699），没测到的运行按
// infra_failed → verdict qa-env-problem（src/nightly.mjs:1184、docs/report-schema.md:161 / 200 —— 内存低水位
// 走的就是这条路，src/nightly.mjs:1946）。ENOSPC / EDQUOT 是操作系统拒绝，不是 QA 工具自己出错。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const root = mkdtempSync(join(tmpdir(), 'argus-enospc-'))
process.env.ARGUS_ROOT = root
after(() => rmSync(root, { recursive: true, force: true }))

const N = await import('../src/nightly.mjs')

// mkdirSync 真的失败时抛出来的形状（node fs：message + code / errno / syscall / path）
const STEP_TMP_1789 = '/tmp/argus-run-S1rCD9/argus-n1789-h1-mw2hjrvw/step-e2e-browser'
const STEP_TMP_1729 = '/tmp/argus-run-M95tCP/argus-n1729-h1-mw2grl7o/step-plugin'
function enospcError(path = STEP_TMP_1789) {
  return Object.assign(new Error(`ENOSPC: no space left on device, mkdir '${path}'`), { code: 'ENOSPC', errno: -28, syscall: 'mkdir', path })
}
const health = () => ({ status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], notes: [] })

async function runFailing(targets) {
  const h = health()
  const errors = new Map(targets.map((t) => [t.number, t.error]))
  const { prs } = await N.processTargets(targets.map((t) => ({ number: t.number, reason: 'merged' })), {
    health: h,
    processOne: async (t) => { throw errors.get(t.number) },
  })
  return { prs, health: h, finalized: N.finalizeHealth(h, prs) }
}
const envText = (h) => h.env_problems.map((e) => String(e.message ?? '')).join(' | ')

test('建不出步骤临时目录（ENOSPC）是箱子的问题：记 env_problems，不记 pipeline 工具故障', async () => {
  const { prs, health: h, finalized } = await runFailing([{ number: 1789, error: enospcError() }])
  const pr = prs[0]
  assert.ok(h.env_problems.length > 0,
    `操作系统拒绝建目录是环境问题，报告要有一条 env_problems（证据文件里是 []）：${JSON.stringify({ env_problems: h.env_problems, qa_tool_failures: h.qa_tool_failures })}`)
  const said = envText(h)
  assert.match(said, /enospc|no space|inode|disk/i, `env_problems 要说清是空间 / inode 耗尽：${said}`)
  assert.ok(said.includes('step-e2e-browser'), `要说清是哪个目录建不出来（消息里本来就有这个路径）：${said}`)
  assert.ok(!h.qa_tool_failures.some((f) => f.stage === 'pipeline'),
    `来源是操作系统、不是 panoptes 代码：不能再进 stage=pipeline（那条通道开的是 panoptes 机制工单）：${JSON.stringify(h.qa_tool_failures)}`)
  assert.equal(pr.verdict, 'qa-env-problem',
    `没测到的运行遇到环境故障按 qa-env-problem 收场（docs/report-schema.md:161 / 200），不能是 qa-error：${JSON.stringify({ verdict: pr.verdict, error: pr.error })}`)
  assert.equal(pr.tested, false, '这个 PR 确实没测到，tested 不能变 true')
  assert.deepEqual(pr.findings, [], '箱子满了不是作者的发现')
  assert.notEqual(finalized.status, 'failed',
    `整轮不该被记成 failed（failed 的含义是 panoptes 自己坏了，src/nightly.mjs:1709）：${JSON.stringify({ status: finalized.status, qaf: h.qa_tool_failures })}`)
  assert.equal(finalized.status, 'degraded', `环境问题让整轮 degraded：${finalized.status}`)
})

test('同一个「箱子建不出目录」换 PR / 换步骤名，还是环境问题（不是一条步骤一条代码故障）', async () => {
  const { prs, health: h, finalized } = await runFailing([
    { number: 1789, error: enospcError(STEP_TMP_1789) },
    { number: 1729, error: enospcError(STEP_TMP_1729) },
  ])
  assert.deepEqual(prs.map((p) => p.verdict), ['qa-env-problem', 'qa-env-problem'],
    `两个步骤名是同一台箱子的同一次耗尽（#1729 step-plugin / #1789 step-e2e-browser），判定要一样：${JSON.stringify(prs.map((p) => [p.number, p.verdict]))}`)
  assert.ok(!h.qa_tool_failures.some((f) => f.stage === 'pipeline'), `两个都不能进 stage=pipeline：${JSON.stringify(h.qa_tool_failures)}`)
  const said = envText(h)
  for (const step of ['step-plugin', 'step-e2e-browser']) assert.ok(said.includes(step), `两个失败的目录都要在 env_problems 里点名（${step}）：${said}`)
  assert.equal(finalized.status, 'degraded', `整轮 degraded，不是 failed：${finalized.status}`)
})

test('错误被包装过、只剩消息（没有 errno / code）时也认得出来', async () => {
  // 上层 redact / 重抛之后操作用的只有这条消息（报告里存的就是 message），所以按消息也要认
  const { prs, health: h } = await runFailing([{ number: 1790, error: new Error(`ENOSPC: no space left on device, mkdir '${STEP_TMP_1789}'`) }])
  assert.equal(prs[0].verdict, 'qa-env-problem', `只有消息时也要按环境问题收场：${JSON.stringify({ verdict: prs[0].verdict, qaf: h.qa_tool_failures })}`)
  assert.ok(!h.qa_tool_failures.some((f) => f.stage === 'pipeline'), `只有消息时也不能进 stage=pipeline：${JSON.stringify(h.qa_tool_failures)}`)
  assert.match(envText(h), /enospc|no space/i, `env_problems 要说清原因：${envText(h)}`)
})

test('真正的 pipeline 崩溃仍然记 qa-error / stage=pipeline（别把这条修成万用挡板）', async () => {
  const { prs, health: h, finalized } = await runFailing([{ number: 1791, error: new Error('boom') }])
  assert.equal(prs[0].verdict, 'qa-error', `不是环境故障的崩溃照旧是 qa-error：${JSON.stringify({ verdict: prs[0].verdict, env: h.env_problems })}`)
  assert.deepEqual(h.qa_tool_failures.map((f) => [f.pr, f.stage]), [[1791, 'pipeline']], `照旧进 stage=pipeline：${JSON.stringify(h.qa_tool_failures)}`)
  assert.equal(finalized.status, 'failed', `照旧整轮 failed：${finalized.status}`)
})
