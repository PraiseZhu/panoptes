// 回归：vitest 的「Failed Tests」段把同一个测试组里失败的用例连着列成多行 FAIL，随后只打一段共享诊断
// （同一步 --reporter=json 的报告里每条用例的 failureMessages 就是这一段）。createFailureParser 只在
// 最后一条 FAIL 行上记类型：下一条 FAIL 进来时先把上一条按默认 'unknown' 存下（exec.mjs:76-80），
// 共享诊断只升级 state.current（exec.mjs:116-123），于是组内前面的用例永远是 unknown。
// 后果：nightly 的基线对照 compareKey 只能给 'unknown'，该用例被写成
// 「在基线版本上也失败（base run failed; failure type not fingerprint-comparable）」
// （mivo #1424 / #1447 / #1449 的 batch-workspace.native.test.mjs「…：false」反复如此，日志里那段
// `Error: 父目录必须由控制器独占管理` 就是它的失败原因）。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createFailureParser } from '../src/exec.mjs'
import { compareKey } from '../src/decide.mjs'
import { classifyKey } from '../src/nightly.mjs'

const FILE = 'scripts/ops/review-capacity-check/batch-workspace.native.test.mjs'
const GROUP = `${FILE} > 批次权限元数据、不同UID的POSIX改名模型及空清理/失败保留`
const OTHER = `${FILE} > 未清批次保留完整路径进入最终失败，其他善后继续完成`
const ERROR = 'Error: 父目录必须由控制器独占管理'
const STACK = (line) => [` ❯ assertControllerDirectory scripts/ops/review-capacity-check/workspace.mjs:30:115`, ` ❯ ${FILE}:${line}:5`].join('\n')

// 真实 console 形态：两条 FAIL 行共用其后一段诊断，另一条用例有自己的诊断
const grouped = [' RUN  v3.2.4 /tmp/x', ` FAIL  ${GROUP}：false`, ` FAIL  ${GROUP}：true`, ERROR, STACK(20), '',
  ` FAIL  ${OTHER}`, ERROR, STACK(56), '', ' Test Files  1 failed (1)', '      Tests  3 failed (3)', '   Duration  1.2s', ''].join('\n')
// 对照：同样的失败，但每条 FAIL 行后面跟自己的诊断（超时/断言各自一段），解析器现在就认得
const perTest = [' RUN  v3.2.4 /tmp/x', ` FAIL  ${GROUP}：false`, ERROR, STACK(20), '', ` FAIL  ${GROUP}：true`, ERROR, STACK(20), '',
  ` FAIL  ${OTHER}`, ERROR, STACK(56), '', ' Test Files  1 failed (1)', '      Tests  3 failed (3)', '   Duration  1.2s', ''].join('\n')

const parse = (text) => { const p = createFailureParser(); p.push('stderr', text); return p.finish() }
const typesOf = (r) => Object.fromEntries(r.failureDetails.map((f) => [f.name, f.type]))
// 单轮结果：nightly 里失败键是 `步骤id::用例名`，compareKey 只看 failures/failureDetails/运行完整性
const asRun = (r) => [{ id: 'unit', ok: false, exitCode: 1, timeline: null, failures: r.failures, failureDetails: r.failureDetails, failureParseStatus: r.failureParseStatus }]

test('vitest 同组连写的 FAIL 行共用一段诊断：类型给组内每个用例，不止最后一条', () => {
  const run = parse(grouped)
  assert.equal(run.failureParseStatus, 'complete')
  assert.deepEqual(typesOf(run), { [`${GROUP}：false`]: 'test_error', [`${GROUP}：true`]: 'test_error', [OTHER]: 'test_error' },
    '组内第一条 FAIL 行拿到的是它自己的失败类型；现在被存成 unknown，只有最后一条（：true）记到诊断')
  // 对照：同样的失败只要每条 FAIL 行后面各自跟诊断，解析器本来就能全部认出来 → 差异只在「同组共享一段诊断」这个形态
  assert.deepEqual(typesOf(parse(perTest)), typesOf(run), '共享诊断与逐条诊断必须得到同一组类型')
  // 分组不能串味：后面独立成段的用例仍只用自己的诊断，不继承上一组
  assert.notEqual(typesOf(run)[OTHER], 'unknown')
})

test('同组两条 FAIL 的基线对照都判 pre-existing：「失败类型不可比」的兜底不再出现', () => {
  const run = parse(grouped)
  const first = asRun(run)
  assert.equal(compareKey(`unit::${GROUP}：false`, first, first), 'same', '同一段日志对同一段日志必须可比')
  const verdicts = ['false', 'true'].map((flag) => classifyKey(`unit::${GROUP}：${flag}`, { first, rerun: first, base: first, latest: null, latestIsSame: true }))
  assert.deepEqual(verdicts, [
    { status: 'pre-existing', why: 'also fails on the base revision' },
    { status: 'pre-existing', why: 'also fails on the base revision' },
  ], '同一个失败的两条 FAIL 行必须得到同一份归因；否则报告只能写「failure type not fingerprint-comparable」')
})
