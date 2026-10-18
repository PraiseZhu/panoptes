// 新代码覆盖率（审计 2026-10-18 S5）：vitest 加载不了的普通 JS 标「无法度量」不算 0%；
// node --test / 子进程 / 复制到临时目录运行的代码用 NODE_V8_COVERAGE 原始数据补上
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, cpSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { execFileSync, spawnSync } from 'node:child_process'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-cov8-'))
const { newCodeCoverage, readV8Dir, mapToRepo, mergeCoverageFiles, coverageGate } = await import('../src/coverage.mjs')
const { withV8Coverage } = await import('../src/runner.mjs')

const SRC = `export function used(x) {
  return x + 1
}
export function unused(x) {
  const y = x * 2
  return y
}
used(1)
`
function repo() {
  const root = mkdtempSync(join(tmpdir(), 'argus-cov8-repo-'))
  mkdirSync(join(root, 'scripts/ops/lib'), { recursive: true })
  writeFileSync(join(root, 'scripts/ops/lib/a.mjs'), SRC)
  writeFileSync(join(root, 'package.json'), '{"type":"module"}')
  execFileSync('git', ['-C', root, 'init', '-q']); execFileSync('git', ['-C', root, 'add', '.'])
  return root
}
const runNode = (file, cov, env = { ...process.env, NODE_V8_COVERAGE: cov }) => spawnSync(process.execPath, [file], { env, encoding: 'utf8' })

test('readV8Dir：源码树里直接跑的文件 → 可执行行 / 已覆盖行（没调用的函数体不算覆盖）', () => {
  const root = repo(); const cov = join(root, '.v8')
  assert.equal(runNode(join(root, 'scripts/ops/lib/a.mjs'), cov).status, 0)
  const files = readV8Dir(cov, root)
  assert.deepEqual(files['scripts/ops/lib/a.mjs'].lines, [1, 2, 4, 5, 6, 8])
  assert.deepEqual(files['scripts/ops/lib/a.mjs'].covered, [1, 2, 8])
  assert.equal(files['scripts/ops/lib/a.mjs'].source, 'v8')
})

test('复制到临时目录再运行：按路径后缀映射回源文件；跑代码的进程自己没有 NODE_V8_COVERAGE 就收不到数据', () => {
  const root = repo(); const cov = join(root, '.v8')
  const tmp = mkdtempSync(join(tmpdir(), 'argus-cov8-copy-'))
  cpSync(join(root, 'scripts/ops/lib'), join(tmp, 'lib'), { recursive: true }); writeFileSync(join(tmp, 'package.json'), '{"type":"module"}')
  assert.equal(runNode(join(tmp, 'lib/a.mjs'), cov).status, 0)
  assert.deepEqual(readV8Dir(cov, root)['scripts/ops/lib/a.mjs'].covered, [1, 2, 8])
  const cov2 = join(root, '.v8b')
  runNode(join(root, 'scripts/ops/lib/a.mjs'), cov2, { PATH: process.env.PATH, HOME: process.env.HOME })
  assert.deepEqual(readV8Dir(cov2, root), {}, '起进程的一方没带 NODE_V8_COVERAGE：收不到')
  assert.equal(mapToRepo('/tmp/x/a.mjs', root, ['a.mjs', 'b/a.mjs']), null, '只有文件名、无目录：不猜')
  assert.equal(mapToRepo('/tmp/q/lib/a.mjs', root, ['x/lib/a.mjs', 'y/lib/a.mjs']), null, '后缀不唯一：不猜')
})

test('newCodeCoverage：普通 JS 没数据 → not_measurable，不进分母；TS 没加载 → 仍按 0；V8 数据补上的文件正常计', () => {
  const added = {
    'cindyplugin/main.js': [{ line: 1, text: 'run()' }],
    'src/new.ts': [{ line: 1, text: 'export const a = f()' }],
    'src/ok.ts': [{ line: 3, text: 'return 1' }],
    'scripts/ops/lib/a.mjs': [{ line: 5, text: '  const y = x * 2' }, { line: 2, text: '  return x + 1' }],
  }
  const files = mergeCoverageFiles({ 'src/ok.ts': { lines: [3], covered: [3] } }, { 'scripts/ops/lib/a.mjs': { lines: [1, 2, 4, 5, 6, 8], covered: [1, 2, 8], source: 'v8' }, 'src/ok.ts': { lines: [3], covered: [] } })
  assert.deepEqual(files['src/ok.ts'].covered, [3], 'vitest 已有的文件不被 V8 覆盖')
  const nc = newCodeCoverage(added, files)
  assert.deepEqual(nc.not_measurable_files, ['cindyplugin/main.js'])
  assert.deepEqual(nc.unloaded_files, ['src/new.ts'])
  assert.equal(nc.executable_lines, 4); assert.equal(nc.covered_lines, 2); assert.equal(nc.pct, 50)
  assert.equal(nc.files.find((f) => f.file === 'scripts/ops/lib/a.mjs').source, 'v8')
  const only = newCodeCoverage({ 'cindyplugin/main.js': [{ line: 1, text: 'run()' }] }, {})
  assert.equal(only.pct, null)
  assert.equal(coverageGate({ coverage: { status: 'ok', total: {}, files: {} }, added: { 'cindyplugin/main.js': [{ line: 1, text: 'run()' }] } }).status, 'not-evaluated', '全是无法度量：不判 fail')
})

test('withV8Coverage：kind v8 只给逐文件数据；vitest 报告缺的文件补进来；原始目录读完删除', () => {
  const root = repo(); const cov = join(root, '.v8')
  runNode(join(root, 'scripts/ops/lib/a.mjs'), cov)
  const v = withV8Coverage(null, cov, root)
  assert.equal(v.kind, 'v8'); assert.ok(v.files['scripts/ops/lib/a.mjs'])
  assert.equal(existsSync(cov), false)
  runNode(join(root, 'scripts/ops/lib/a.mjs'), cov)
  const m = withV8Coverage({ status: 'ok', total: { lines: 1 }, files: { 'src/x.ts': { lines: [1], covered: [1] } } }, cov, root)
  assert.deepEqual(Object.keys(m.files).sort(), ['scripts/ops/lib/a.mjs', 'src/x.ts'])
  assert.equal(withV8Coverage({ status: 'unavailable', reason: 'x' }, join(root, 'none'), root).status, 'unavailable')
})

test('#1081 形状：测试只给子进程 PATH/HOME，并把代码复制进改了名的临时目录（/tmp/x/praisebuild/）→ 子进程覆盖率仍收得到并算进新代码覆盖率', () => {
  const root = repo(); const cov = join(root, '.v8')
  const tmp = mkdtempSync(join(tmpdir(), 'argus-cov8-rename-'))
  mkdirSync(join(tmp, 'praisebuild')); cpSync(join(root, 'scripts/ops/lib/a.mjs'), join(tmp, 'praisebuild/a.mjs')); writeFileSync(join(tmp, 'package.json'), '{"type":"module"}')
  writeFileSync(join(root, 'parent.mjs'), `import { spawnSync } from 'node:child_process'
process.exit(spawnSync('/bin/sh', ['-c', 'exec "$0" "$1"', process.execPath, ${JSON.stringify(join(tmp, 'praisebuild/a.mjs'))}], { env: { PATH: process.env.PATH, HOME: process.env.HOME } }).status)
`)
  // 起测试的进程带着 NODE_V8_COVERAGE（runner 的覆盖率轮次）：node 自己会把它传给子进程，即使 env 只给了 PATH/HOME
  assert.equal(runNode(join(root, 'parent.mjs'), cov).status, 0)
  const v8 = readV8Dir(cov, root)
  assert.deepEqual(v8['scripts/ops/lib/a.mjs'].covered, [1, 2, 8], '目录名变了：文件名在仓里唯一且长度一致 → 对回源文件')
  // vitest 主报告里已有这个文件（测试 import 过它，但函数体只在子进程里跑）：子进程跑到的行补进 covered，可执行行不变
  const merged = mergeCoverageFiles({ 'scripts/ops/lib/a.mjs': { lines: [2, 5, 6], covered: [] } }, v8)
  assert.deepEqual(merged['scripts/ops/lib/a.mjs'], { lines: [2, 5, 6], covered: [2] })
  // 长度不一致（同名不同文件）→ 不认
  writeFileSync(join(tmp, 'praisebuild/a.mjs'), SRC + '// different\n')
  const cov2 = join(root, '.v8c')
  assert.equal(runNode(join(root, 'parent.mjs'), cov2).status, 0)
  assert.equal(readV8Dir(cov2, root)['scripts/ops/lib/a.mjs'], undefined)
})
