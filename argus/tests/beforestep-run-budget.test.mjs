// 回归：等覆盖率轮次（beforeStep）不能吃掉这一步自己的运行预算。
// 2026-11-16 22:01 PR #2418：bench（afterCoverage，timeoutMin 15）先等并行的覆盖率轮次，等了 15 min 就被判
// "check timed out after 15 min"（log_tail: beforeStep time budget exhausted，duration_ms 900002，exit_code null），
// 而 bench 一行命令都没跑；随后 break 掉整个步骤循环，排在 bench 之后的 node-test / pytest / lint / … 全部
// "not run"，nightly 还把这条超时写进 system_health.qa_tool_failures（stage: bench）→ infrafix 给 panoptes 自己开票。
import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const root = mkdtempSync(join(tmpdir(), 'argus-beforestep-'))
process.env.ARGUS_ROOT = root
const { newRunDir, runSteps, cleanupRun } = await import('../src/runner.mjs')
const { loadProfile } = await import('../src/config.mjs')
const dirs = []
after(async () => { for (const d of dirs) await cleanupRun(null, d); rmSync(root, { recursive: true, force: true }) })

const ok = [process.execPath, '-e', '0']
// 覆盖率轮次（单测 + --coverage）比 bench 自己的预算慢：真实报告里是 15 min 的 bench 等一个十几分钟的覆盖率轮次
const COVERAGE_MS = 400
const BENCH_BUDGET_MS = 150

// 与 mivo profile 同形：bench 排在单测之后、afterCoverage，只是把 15 min 缩成毫秒
function profile({ tail = [] } = {}) {
  return { steps: [
    { id: 'unit', kind: 'product', cmd: ok, timeoutMs: 60_000 },
    { id: 'bench', kind: 'product', trialOnly: true, afterCoverage: true, cmd: ok, timeoutMs: BENCH_BUDGET_MS },
    ...tail,
  ] }
}
const gated = (ms = COVERAGE_MS) => {
  const gate = new Promise((r) => setTimeout(r, ms))
  return (step) => (step.afterCoverage ? gate : null)
}

test('mivo profile：bench 是 afterCoverage 的试运行检查（回归场景来自这条真实配置）', () => {
  const bench = loadProfile('mivo-canvas-plugin').steps.find((s) => s.id === 'bench')
  assert.equal(bench.afterCoverage, true)
  assert.equal(bench.trialOnly, true)
})

test('bench 等覆盖率轮次超过它自己的预算后才开跑：仍跑满自己的预算并给出真实结果，不是 timeout', async () => {
  const runDir = newRunDir(1, 'budget'); dirs.push(runDir)
  const src = join(runDir, 'src'); mkdirSync(src)
  const [unit, bench] = await runSteps(profile(), src, runDir, { beforeStep: gated() })
  assert.equal(unit.status, 'pass')
  assert.equal(bench.timedOut, false, `bench 还没开跑就被判超时（${bench.tail}）：nightly 会把它写成 "check timed out"，进 qa_tool_failures`)
  assert.equal(bench.status, 'pass', `等了 ${COVERAGE_MS}ms 覆盖率轮次之后 bench 仍应拿到 ${BENCH_BUDGET_MS}ms 自己的预算`)
  assert.notEqual(bench.exitCode, null, 'bench 的命令真的跑过（exit_code 不是 null）')
})

test('bench 被 beforeStep 挡住时不结束整轮的步骤循环：排在它之后的检查照跑，不会静默变成 "not run"', async () => {
  const runDir = newRunDir(1, 'tail'); dirs.push(runDir)
  const src = join(runDir, 'src'); mkdirSync(src)
  const results = await runSteps(profile({ tail: [{ id: 'lint', kind: 'quality', cmd: ok, timeoutMs: 60_000 }] }), src, runDir, { beforeStep: gated() })
  assert.deepEqual(results.map((r) => r.id), ['unit', 'bench', 'lint'])
  assert.equal(results.find((r) => r.id === 'lint').ok, true)
})

test('这一步自己的命令跑超预算仍然算 timeout：预算是运行预算，不是取消超时', async () => {
  const runDir = newRunDir(1, 'hang'); dirs.push(runDir)
  const src = join(runDir, 'src'); mkdirSync(src)
  const hanging = { steps: [{ id: 'bench', kind: 'product', cmd: [process.execPath, '-e', 'setTimeout(() => {}, 5000)'], timeoutMs: 150 }] }
  const [bench] = await runSteps(hanging, src, runDir, { beforeStep: gated(0) })
  assert.equal(bench.timedOut, true)
  assert.equal(bench.status, 'timeout')
})
