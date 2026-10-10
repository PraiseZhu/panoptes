import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createFailureParser, run } from '../src/exec.mjs'

const name = 'src/a.test.ts > contract > 全部入口已声明意图'
const vitest = (reason, testName = name) => ` RUN v3.2.4\n FAIL ${testName}\n${reason}\n Tests 1 failed | 9 passed (10)\n Duration 1.2s\n`
const parse = (text) => { const p = createFailureParser(); p.push('stderr', text); return p.finish() }

test('Vitest 类型来自诊断：内部超时、断言、正文异常和未知，名字原样保留', () => {
  for (const [reason, type] of [
    ['Error: Test timed out in 5000ms.', 'test_timeout'],
    ['Error: Hook timed out in 5000ms.', 'test_timeout'],
    ['AssertionError: expected 20 to be less than 10', 'assertion'],
    ['TypeError: value is not a function', 'test_error'],
    ['reason cannot be identified', 'unknown'],
  ]) {
    const r = parse(vitest(reason))
    assert.equal(r.failureParseStatus, 'complete')
    assert.deepEqual(r.failureDetails, [{ name, type }])
    assert.deepEqual(r.failures, [name])
  }
  assert.equal(parse(vitest('ordinary log Error: Test timed out in 5ms', 'src/a.test.ts > timeout threshold')).failureDetails[0].type, 'unknown')
  assert.equal(parse(vitest('  42| throw new Error("Test timed out in 5ms")')).failureDetails[0].type, 'unknown')
})

test('UTF-8、ANSI、CRLF、chunk 拆分、无最终换行不改变类型；stdout/stderr 半行不拼接', () => {
  const text = Buffer.from(vitest('Error: Test timed out in 5000ms.').replace(' FAIL', ' \x1b[31mFAIL').replace(name, `${name}\x1b[0m`).replaceAll('\n', '\r\n').trimEnd())
  const p = createFailureParser()
  for (const byte of text) p.push('stderr', Buffer.from([byte]))
  p.push('stdout', 'FAIL src/unrelated.test.ts > split')
  p.push('stderr', '\nignored\n')
  p.push('stdout', ' title\nunknown\n')
  const r = p.finish()
  assert.equal(r.failureParseStatus, 'incomplete', '终态只有一个失败，额外未完整诊断必须不完整')
  assert.deepEqual(r.failures, [name, 'src/unrelated.test.ts > split title'].sort())
  assert.equal(r.failureDetails.find((f) => f.name === name).type, 'test_timeout')
})

test('重复摘要可以补齐；两个明确类型冲突永久 unknown，不被后来的输出覆盖', () => {
  const p = createFailureParser()
  p.push('stdout', `FAIL ${name}\n`)
  p.push('stderr', vitest('AssertionError: expected []'))
  assert.equal(p.finish().failureDetails[0].type, 'assertion')
  const conflict = parse(`FAIL ${name}\nAssertionError: expected []\nFAIL ${name}\nError: Test timed out in 5000ms.\n${vitest('AssertionError: expected []')}`)
  assert.deepEqual(conflict.failureDetails, [{ name, type: 'unknown' }])
})

test('pytest 摘要保留名字并核对终态；收集错误不可确认', () => {
  const r = parse('FAILED tests/a.py::test_contract - AssertionError: expected 1\nFAILED tests/a.py::test_other - TypeError: missing\n2 failed, 8 passed in 0.50s\n')
  assert.equal(r.failureParseStatus, 'complete')
  assert.deepEqual(r.failureDetails.map((f) => f.type), ['assertion', 'test_error'])
  assert.equal(parse('ERROR collecting tests/a.py\n1 error in 0.12s\n').failureParseStatus, 'incomplete')
  assert.equal(parse('FAILED tests/a.py::test_contract - Failed: Timeout (>5.0s) from pytest-timeout.\n1 failed in 5.10s\n').failureDetails[0].type, 'test_timeout')
})

test('失败上限、长行、长诊断、缺终态、计数不符和未处理异常都不能假称完整', () => {
  assert.equal(parse(`FAIL ${name}\nAssertionError: x\n`).failureParseStatus, 'incomplete')
  assert.equal(parse(vitest('AssertionError: x').replace('1 failed', '2 failed')).failureParseStatus, 'incomplete')
  assert.equal(parse(`${'x'.repeat(65537)}\n${vitest('AssertionError: x')}`).failureParseStatus, 'incomplete')
  assert.equal(parse(`FAIL ${name}\n${'detail\n'.repeat(12000)} Tests 1 failed\n`).failureParseStatus, 'incomplete')
  assert.equal(parse(`${vitest('AssertionError: x')}Unhandled Rejection\nError: process exploded\n`).failureParseStatus, 'incomplete')
  assert.equal(parse('Test Files 1 failed\nTests no tests\nDuration 0.1s\n').failureParseStatus, 'incomplete')
  assert.equal(parse('ℹ tests 2\nℹ pass 1\nℹ fail 0\nℹ cancelled 0\nℹ skipped 0\nℹ todo 0\nℹ duration_ms 10\n').failureParseStatus, 'incomplete')
  const overflow = parse(Array.from({ length: 1001 }, (_, i) => `FAIL src/a.test.ts > ${i}\nAssertionError: x\n`).join('') + 'Tests 1001 failed\n')
  assert.equal(overflow.failures.length, 1000)
  assert.equal(overflow.failureParseStatus, 'incomplete')
  assert.equal(parse('build failed\n').failureParseStatus, 'not_detected')
})

test('真实 Node spec 子进程的断言与内部超时都有类型；外层未被超时回收', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'argus-types-'))
  try {
    const file = join(dir, 'behavior.test.mjs')
    writeFileSync(file, "import { test } from 'node:test'; import assert from 'node:assert/strict'; test('assertion sample', () => assert.equal(1, 2)); test('timeout sample', { timeout: 20 }, async () => new Promise(resolve => setTimeout(resolve, 200))); test('import sample', async () => import('./missing.mjs'));\n")
    const r = await run(process.execPath, ['--test', '--test-reporter=spec', file], { cwd: dir, env: { PATH: process.env.PATH }, timeoutMs: 5000 })
    assert.equal(r.exitCode, 1)
    assert.equal(r.timedOut, false)
    assert.equal(r.failureParseStatus, 'complete', r.tail)
    assert.deepEqual(r.failureDetails, [{ name: 'assertion sample', type: 'assertion' }, { name: 'import sample', type: 'runner_error' }, { name: 'timeout sample', type: 'test_timeout' }])
  } finally { rmSync(dir, { recursive: true, force: true }) }
})

test('完整流式解析不依赖 tail 保留前面的失败诊断', async () => {
  const output = vitest('Error: Test timed out in 5000ms.') + 'irrelevant output\n'.repeat(7000)
  const r = await run(process.execPath, ['-e', 'process.stdout.write(process.argv[1]); process.exitCode=1', output], { env: { PATH: process.env.PATH } })
  assert.equal(r.timedOut, false)
  assert.ok(!r.tail.includes('Test timed out'))
  assert.deepEqual(r.failureDetails, [{ name, type: 'test_timeout' }])
})
