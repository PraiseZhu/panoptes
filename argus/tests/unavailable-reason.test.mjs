// 11-07 17:41 起 box 上 reports/merged 里 e2e-browser 没跑成时 unavailable_reason 只有 "}"：
// e2e-browser 的 stdout 是一段 JSON 摘要，旧代码取输出最后一个非空行当原因，于是只剩 JSON 的右括号。
// 真正的原因在步骤报告（report.browser.json）里没跑成的那条检查上，或者在输出里那条报错（chromium 缺失、apt 的 E: 行）上。
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { toStepOutcome } from '../src/runner.mjs'

// PR-1785 h1 logs/e2e-browser.log 的 stdout 原文（exit 2）
const jsonTail = `{
  "reportPath": "/workspace/mivo-qa/argus-root/cache/runs/n1785-h1-mw6eo9u2/e2e-browser/report.browser.json",
  "status": "unavailable",
  "layer": "browser",
  "baseSha": "cc2fd4c6f618d7de1f825a1fefca272f455ae4ca",
  "headSha": "d9ad407f03423c22e0e7610b154c77ade2bc3de2",
  "candidateSnapshotHash": "8e8c7b0a7297a446e4981bf1c3918c38551a6350061f45e18ce723c50ed1a25a"
}
`
const browserStep = { id: 'e2e-browser', unavailableExitCodes: [2], failureReport: { format: 'checks', path: '${RUN}/e2e-browser/report.browser.json' } }
const run = (step, tail, runDir = '/tmp/none') => toStepOutcome(step, { id: step.id, kind: 'product', ok: false, exitCode: 2, tail }, runDir)

test('e2e-browser 没跑成：原因取报告里没跑成的检查，不是 JSON 摘要的 "}"', () => {
  const runDir = mkdtempSync(join(tmpdir(), 'argus-unavail-'))
  try {
    mkdirSync(join(runDir, 'e2e-browser'))
    writeFileSync(join(runDir, 'e2e-browser', 'report.browser.json'), JSON.stringify({ status: 'unavailable', checks: [
      { id: 'browser-host-probe', status: 'unavailable', reason: 'no trusted Chrome/Chromium executable on this platform; browser layer unavailable' },
      { id: 'browser-product-roundtrip', status: 'unavailable', reason: 'Chrome missing; product persist/export not executed' },
      { id: 'browser-not-cindy', status: 'pass', reason: 'ok' }] }))
    const r = run(browserStep, jsonTail, runDir)
    assert.equal(r.unavailable, true)
    assert.equal(r.unavailableReasonZh, 'no trusted Chrome/Chromium executable on this platform; browser layer unavailable')
  } finally { rmSync(runDir, { recursive: true, force: true }) }
})

test('报告缺失、输出只有 JSON：不写 "}"，退回固定说明', () => {
  const r = run(browserStep, jsonTail)
  assert.equal(r.unavailableReasonZh, '检查没能启动')
})

test('输出里有 chromium 缺失 / apt 失败的报错行：取那一行，不取末尾的标点', () => {
  const apt = run({ id: 'x', unavailableExitCodes: [2] }, 'Reading package lists...\nE: Unable to locate package chromium\nsetup done\n}\n')
  assert.equal(apt.unavailableReasonZh, 'E: Unable to locate package chromium')
  const chrome = run({ id: 'x', unavailableExitCodes: [2] }, 'starting layer\nError: chromium executable not found at /usr/bin/chromium\n{\n  "status": "unavailable"\n}\n')
  assert.equal(chrome.unavailableReasonZh, 'Error: chromium executable not found at 相关文件') // toPlainZh 照旧把路径换成「相关文件」
})

test('原因长度封顶 120 字符', () => {
  const r = run({ id: 'x', unavailableExitCodes: [2] }, `E: ${'chromium '.repeat(40)}\n}\n`)
  assert.ok(r.unavailableReasonZh.length <= 120, String(r.unavailableReasonZh.length))
  assert.match(r.unavailableReasonZh, /^E: chromium/)
})
