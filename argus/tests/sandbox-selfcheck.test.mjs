// QA #1456 回归保护：旧沙箱 `--ro-bind / /` 让 / 在 user namespace 里显示成 65534，产品的祖先目录校验于是误报（#121 已修）。
// 1) bwrapArgs 的根必须是新 tmpfs（不是 ro-bind 宿主 /），挂完再 --remount-ro /；
// 2) sandboxSelfCheck：沙箱里 / 的属主 uid 不等于本次运行 uid → 抛「沙箱自检失败」（QA 环境问题），开关文件存在就跳过。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-selfcheck-root-'))
const { bwrapArgs, sandboxMode, sandboxSelfCheck, sandboxSelfcheckOffFile, SANDBOX_SELFCHECK_ZH } = await import('../src/runner.mjs')

const pairs = (args, flag) => args.flatMap((a, i) => (a === flag ? [[args[i + 1], args[i + 2]]] : []))

test('bwrapArgs：根是 --tmpfs /（在所有挂载之前），没有 --ro-bind / /，最后 --remount-ro /', () => {
  const runDir = mkdtempSync(join(tmpdir(), 'argus-selfcheck-run-'))
  try {
    const args = bwrapArgs(runDir)
    const tmpfsRoot = args.findIndex((a, i) => a === '--tmpfs' && args[i + 1] === '/')
    assert.ok(tmpfsRoot >= 0, 'missing --tmpfs /')
    const firstMount = args.findIndex((a) => ['--ro-bind', '--bind', '--symlink', '--dev', '--proc'].includes(a))
    assert.ok(tmpfsRoot < firstMount, '--tmpfs / must come before every other mount')
    assert.deepEqual(pairs(args, '--ro-bind').filter(([src, dst]) => src === '/' || dst === '/'), [], 'no --ro-bind of / (QA #1456)')
    assert.deepEqual(pairs(args, '--bind').filter(([src, dst]) => src === '/' || dst === '/'), [], 'no --bind of /')
    const remount = args.findIndex((a, i) => a === '--remount-ro' && args[i + 1] === '/')
    assert.ok(remount > firstMount, 'missing --remount-ro / after the mounts')
  } finally { rmSync(runDir, { recursive: true, force: true }) }
})

function withQaHome(fn) {
  const home = mkdtempSync(join(tmpdir(), 'argus-selfcheck-qahome-'))
  return Promise.resolve(fn({ ...process.env, QA_HOME: home }, home)).finally(() => rmSync(home, { recursive: true, force: true }))
}

test('sandboxSelfCheck：/ 的属主是 65534 → 抛沙箱自检失败（不是产品问题）', () => withQaHome(async (env) => {
  const runDir = mkdtempSync(join(tmpdir(), 'argus-selfcheck-run-'))
  try {
    let calledWith = null
    const exec = async (cmd, args) => { calledWith = args; return { ok: true, exitCode: 0, tail: '65534\n' } }
    await assert.rejects(sandboxSelfCheck(runDir, {}, { env, exec, mode: 'bwrap', uid: 1000 }), (err) => err.message.startsWith(SANDBOX_SELFCHECK_ZH) && /65534/.test(err.message) && /1000/.test(err.message))
    assert.deepEqual(calledWith.slice(-5), ['--', 'stat', '-c', '%u', '/'])
  } finally { rmSync(runDir, { recursive: true, force: true }) }
}))

test('sandboxSelfCheck：/ 的属主等于运行 uid → 通过；沙箱跑不起来 → 也算沙箱问题', () => withQaHome(async (env) => {
  const runDir = mkdtempSync(join(tmpdir(), 'argus-selfcheck-run-'))
  try {
    assert.deepEqual(await sandboxSelfCheck(runDir, {}, { env, exec: async () => ({ ok: true, exitCode: 0, tail: '1000\n' }), mode: 'bwrap', uid: 1000 }), { ok: true, uid: '1000' })
    await assert.rejects(sandboxSelfCheck(runDir, {}, { env, exec: async () => ({ ok: false, exitCode: 1, tail: 'bwrap: setting up uid map: Permission denied' }), mode: 'bwrap', uid: 1000 }), (err) => err.message.startsWith(SANDBOX_SELFCHECK_ZH))
  } finally { rmSync(runDir, { recursive: true, force: true }) }
}))

test('sandboxSelfCheck：开关文件 $QA_HOME/sandbox-selfcheck-off 存在 → 跳过（每次都重新读）；非 bwrap 模式不检查', () => withQaHome(async (env, home) => {
  const runDir = mkdtempSync(join(tmpdir(), 'argus-selfcheck-run-'))
  try {
    assert.equal(sandboxSelfcheckOffFile(env), join(home, 'sandbox-selfcheck-off'))
    assert.equal(sandboxSelfcheckOffFile({}), '/workspace/mivo-qa/sandbox-selfcheck-off')
    const bad = async () => ({ ok: true, exitCode: 0, tail: '65534\n' })
    writeFileSync(sandboxSelfcheckOffFile(env), '')
    assert.deepEqual(await sandboxSelfCheck(runDir, {}, { env, exec: bad, mode: 'bwrap', uid: 1000 }), { skipped: 'off-switch' })
    rmSync(sandboxSelfcheckOffFile(env))
    await assert.rejects(sandboxSelfCheck(runDir, {}, { env, exec: bad, mode: 'bwrap', uid: 1000 }))
    let called = false
    assert.deepEqual(await sandboxSelfCheck(runDir, {}, { env, exec: async () => { called = true; return {} }, mode: 'env-only', uid: 1000 }), { skipped: 'env-only' })
    assert.equal(called, false)
  } finally { rmSync(runDir, { recursive: true, force: true }) }
}))

test('sandboxSelfCheck：真实 bwrap 沙箱里 / 的属主就是运行 uid', { skip: process.platform !== 'linux' || sandboxMode() !== 'bwrap' }, () => withQaHome(async (env) => {
  const runDir = mkdtempSync(join(tmpdir(), 'argus-selfcheck-run-'))
  try {
    assert.deepEqual(await sandboxSelfCheck(runDir, {}, { env }), { ok: true, uid: String(process.getuid()) })
  } finally { rmSync(runDir, { recursive: true, force: true }) }
}))
