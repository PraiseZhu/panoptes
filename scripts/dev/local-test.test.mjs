// node --test scripts/dev/local-test.test.mjs -- local-test.sh against fake Pi / Argus suites in a temp dir:
// failures are matched against the known list, unlisted ones fail the run, fixed entries are reported as stale,
// a hanging file is stopped at --timeout and the run goes on. Not part of CI (CI does not run local-test.sh).
import test from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync, execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(fileURLToPath(new URL('.', import.meta.url)), '../..')
const SCRIPT = join(ROOT, 'scripts/dev/local-test.sh')
const KNOWN = join(ROOT, 'scripts/dev/known-env-failures.json')
const py313 = (() => {
  for (const name of ['python3.13', 'python3']) {
    try {
      const p = execFileSync('bash', ['-c', `command -v ${name}`], { encoding: 'utf8' }).trim()
      if (p && spawnSync(p, ['-c', 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 13) else 1)']).status === 0) return p
    } catch {}
  }
  return null
})()
const needPy = { skip: py313 ? false : 'needs python >= 3.13' }

const PASS = 'import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): pass\nunittest.main()\n'
const FAIL2 = 'import unittest\nclass T(unittest.TestCase):\n    def test_a(self): self.fail("a")\n    def test_b(self): raise OSError("b")\n    def test_ok(self): pass\nunittest.main()\n'

// one fake repo: pi tests (unittest files run as scripts, like selftest.sh) and an Argus root whose run-tests.sh is node --test
function fixture({ pi = {}, argus = {}, entries = [] } = {}) {
  const d = mkdtempSync('/tmp/local-test-fixture-')
  mkdirSync(join(d, 'pi/test'), { recursive: true }); mkdirSync(join(d, 'pi/bin')); mkdirSync(join(d, 'argus/tests'), { recursive: true })
  for (const [f, src] of Object.entries(pi)) writeFileSync(join(d, 'pi/test', f), src)
  for (const [f, src] of Object.entries(argus)) writeFileSync(join(d, 'argus/tests', f), src)
  writeFileSync(join(d, 'argus/run-tests.sh'), 'exec node --test --test-reporter=spec "$@"\n')
  writeFileSync(join(d, 'known.json'), JSON.stringify({ version: 1, entries }))
  return d
}
function run(d, args = [], env = {}) {
  const r = spawnSync('bash', [SCRIPT, '--no-install', '--keep', ...args], { encoding: 'utf8', timeout: 120_000,
    env: { ...process.env, LOCAL_TEST_KNOWN: join(d, 'known.json'), LOCAL_TEST_PI_TESTS: join(d, 'pi/test'), LOCAL_TEST_PI_CWD: join(d, 'pi/bin'),
      LOCAL_TEST_ARGUS_ROOT: join(d, 'argus'), LOCAL_TEST_PLATFORM: 'darwin', ...(py313 ? { LOCAL_TEST_PYTHON: py313 } : {}), ...env } })
  const kept = /日志和 summary\.json 留在 (\S+)/.exec(r.stdout)?.[1]
  const summary = kept && existsSync(join(kept, 'summary.json')) ? JSON.parse(readFileSync(join(kept, 'summary.json'), 'utf8')) : null
  if (kept) rmSync(kept, { recursive: true, force: true })
  rmSync(d, { recursive: true, force: true })
  return { ...r, summary, all: r.stdout + r.stderr }
}
const entry = (id, extra = {}) => ({ id, platforms: ['darwin'], reason: 'test reason', tracking: '环境限制', ...extra })

test('SC2: a failure that is not on the list is an unexpected failure, exit 1', needPy, () => {
  const r = run(fixture({ pi: { 'test_x.py': FAIL2, 'test_y.py': PASS }, entries: [entry('pi/test_x.py::T.test_a')] }), ['--only', 'pi'])
  assert.equal(r.status, 1, r.all)
  assert.deepEqual(r.summary.expected, ['pi/test_x.py::T.test_a'])
  assert.deepEqual(r.summary.unexpected, ['pi/test_x.py::T.test_b'])   // a case entry does not cover other cases of its file
  assert.match(r.stdout, /意外失败 1\n  - pi\/test_x\.py::T\.test_b/)
})

test('SC1 shape: every failure on the list -> "意外失败 0", exit 0', needPy, () => {
  const r = run(fixture({ pi: { 'test_x.py': FAIL2, 'test_y.py': PASS }, entries: [entry('pi/test_x.py::T.test_a'), entry('pi/test_x.py::T.test_b')] }), ['--only', 'pi'])
  assert.equal(r.status, 0, r.all)
  assert.deepEqual(r.summary.expected.sort(), ['pi/test_x.py::T.test_a', 'pi/test_x.py::T.test_b'])
  assert.match(r.stdout, /意外失败 0$/m)
  assert.match(r.stdout, /已知环境失败 2 条/)
})

test('SC2: a file-level entry never hides a case failure of that file (unittest cases)', needPy, () => {
  const r = run(fixture({ pi: { 'test_x.py': FAIL2 }, entries: [entry('pi/test_x.py'), entry('pi/test_x.py::T.test_a')] }), ['--only', 'pi'])
  assert.equal(r.status, 1, r.all)
  assert.deepEqual(r.summary.unexpected, ['pi/test_x.py::T.test_b'])
  assert.deepEqual(r.summary.stale, ['pi/test_x.py'])   // the file-level entry matched nothing: reported, not used as a cover
})

// script-style file (no unittest): plain asserts at module level, stops at the first failing one
const SCRIPT_PY = (broken) => `import os\nx = 1\nassert x == ${broken === 'early' ? 2 : 1}, "unrelated check"\nassert os.path.exists("/no/such/proc"), "needs /proc"\n`
test('script-style file: the failure is "<module>:<line>" from the traceback; listed -> exit 0', needPy, () => {
  const r = run(fixture({ pi: { 'test_s.py': SCRIPT_PY() }, entries: [entry('pi/test_s.py::<module>:4')] }), ['--only', 'pi'])
  assert.equal(r.status, 0, r.all); assert.deepEqual(r.summary.expected, ['pi/test_s.py::<module>:4'])
})

test('SC2: script-style file -- another broken assert in a listed file is an unexpected failure, even with a file-level entry', needPy, () => {
  const r = run(fixture({ pi: { 'test_s.py': SCRIPT_PY('early') }, entries: [entry('pi/test_s.py'), entry('pi/test_s.py::<module>:4')] }), ['--only', 'pi'])
  assert.equal(r.status, 1, r.all)
  assert.deepEqual(r.summary.unexpected, ['pi/test_s.py::<module>:3'])
  assert.match(r.stdout, /意外失败 1\n  - pi\/test_s\.py::<module>:3/)
})

test('a file that fails without case lines or traceback is matched as the whole file', needPy, () => {
  const quiet = { 'test_q.py': 'import sys\nsys.exit(3)\n' }
  assert.deepEqual(run(fixture({ pi: quiet }), ['--only', 'pi']).summary.unexpected, ['pi/test_q.py'])
  assert.equal(run(fixture({ pi: quiet, entries: [entry('pi/test_q.py')] }), ['--only', 'pi']).status, 0)
})

test('SC3: a list entry that no longer fails is reported as stale and does not fail the run', needPy, () => {
  const r = run(fixture({ pi: { 'test_x.py': FAIL2 }, entries: [entry('pi/test_x.py::T.test_a'), entry('pi/test_x.py::T.test_b'),
    entry('pi/test_x.py::T.test_ok'), entry('pi/test_gone.py::T.test_z')] }), ['--only', 'pi'])
  assert.equal(r.status, 0, r.all)
  assert.deepEqual(r.summary.stale.sort(), ['pi/test_gone.py::T.test_z', 'pi/test_x.py::T.test_ok'])
  assert.match(r.stdout, /清单过期 2 条/)
  assert.match(r.stdout, /pi\/test_gone\.py::T\.test_z（测试文件已不存在）/)
  assert.match(r.stdout, /意外失败 0$/m)
})

test('SC3: entries of files filtered out of this run are "not run", not stale', needPy, () => {
  const r = run(fixture({ pi: { 'test_x.py': PASS, 'test_y.py': PASS }, entries: [entry('pi/test_y.py::T.test_q')] }), ['--only', 'pi', 'test_x'])
  assert.equal(r.status, 0, r.all)
  assert.deepEqual(r.summary.stale, []); assert.deepEqual(r.summary.notRun, ['pi/test_y.py::T.test_q'])
})

test('SC4: a file over --timeout is stopped, recorded as 超时, and the next file still runs', needPy, () => {
  const hang = 'import time\ntime.sleep(60)\n'
  const t0 = Date.now()
  const r = run(fixture({ pi: { 'test_a_hang.py': hang, 'test_b_after.py': FAIL2 } }), ['--only', 'pi', '--jobs', '1', '--timeout', '2'])
  assert.ok(Date.now() - t0 < 30_000, `took ${Date.now() - t0} ms`)
  assert.equal(r.status, 1, r.all)
  assert.equal(r.summary.timeout, 1)
  assert.deepEqual(r.summary.unexpectedTimeout, ['pi/test_a_hang.py'])
  assert.deepEqual(r.summary.unexpected.sort(), ['pi/test_b_after.py::T.test_a', 'pi/test_b_after.py::T.test_b'])   // ran after the hang
  assert.match(r.stdout, /超时  pi\/test_a_hang\.py/)
  assert.match(r.stdout, /超时 1 个/)
})

test('SC4: a listed timeout does not fail the run, and its case entries are "not run" rather than stale', needPy, () => {
  const r = run(fixture({ pi: { 'test_a_hang.py': 'import time\ntime.sleep(60)\n' }, entries: [entry('pi/test_a_hang.py'), entry('pi/test_a_hang.py::T.x')] }),
    ['--only', 'pi', '--timeout', '1'])
  assert.equal(r.status, 0, r.all)
  assert.deepEqual(r.summary.expected, ['pi/test_a_hang.py']); assert.deepEqual(r.summary.stale, [])
})

test('Argus: node:test failures are matched by test name; platform-specific entries only apply on their platform', () => {
  const src = "import test from 'node:test'\nimport assert from 'node:assert/strict'\ntest('good', () => {})\ntest('needs /proc', () => assert.equal(1, 2))\n"
  const ok = run(fixture({ argus: { 'a.test.mjs': src }, entries: [entry('argus/tests/a.test.mjs::needs /proc')] }), ['--only', 'argus'])
  assert.equal(ok.status, 0, ok.all); assert.deepEqual(ok.summary.expected, ['argus/tests/a.test.mjs::needs /proc'])
  const linux = run(fixture({ argus: { 'a.test.mjs': src }, entries: [entry('argus/tests/a.test.mjs::needs /proc')] }), ['--only', 'argus'], { LOCAL_TEST_PLATFORM: 'linux' })
  assert.equal(linux.status, 1, linux.all); assert.deepEqual(linux.summary.unexpected, ['argus/tests/a.test.mjs::needs /proc'])
})

test('setup errors exit 2: malformed list, bad options', () => {
  const bad = fixture({ argus: { 'a.test.mjs': "import test from 'node:test'\ntest('x', () => {})\n" } })
  writeFileSync(join(bad, 'known.json'), JSON.stringify({ entries: [{ id: 'nope', platforms: ['darwin'], reason: 'r', tracking: 't' }] }))
  const r = run(bad, ['--only', 'argus'])
  assert.equal(r.status, 2, r.all); assert.match(r.stderr, /entry 0: id must look like/)
  assert.equal(run(fixture(), ['--timeout', '0']).status, 2)
  assert.equal(run(fixture(), ['--only', 'mivo']).status, 2)
})

test('the checked-in list is well formed: unique ids, every file exists, darwin only, reason and tracking set', () => {
  const { entries } = JSON.parse(readFileSync(KNOWN, 'utf8'))
  assert.ok(entries.length > 0)
  assert.equal(new Set(entries.map((e) => e.id)).size, entries.length, 'duplicate ids')
  for (const e of entries) {
    const file = e.id.split('::')[0]
    const path = file.startsWith('pi/') ? join(ROOT, 'pi/test', file.slice(3)) : join(ROOT, file)
    assert.ok(existsSync(path), `${e.id}: ${path} missing`)
    assert.deepEqual(e.platforms, ['darwin'], e.id)
    assert.ok(e.reason && e.tracking, e.id)
  }
})
