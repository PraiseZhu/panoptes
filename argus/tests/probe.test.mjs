// 接口探针与步骤扩展：真起服务逐条比对；服务起不来记没跑成；不留子进程；前提不满足不跑；基线提交变量
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync, execFileSync } from 'node:child_process'
import { mkdtempSync, writeFileSync, readFileSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
process.env.ARGUS_ROOT = mkdtempSync(join(tmpdir(), 'argus-probe-'))
const { toMismatch } = await import('../src/probe-run.mjs')
const { runSteps, newRunDir } = await import('../src/runner.mjs')

const PROBE = join(here, '..', 'src', 'probe-run.mjs')
const FAKE = join(here, 'fixtures', 'probe-server.mjs')
const requests = [
  { name: '健康检查', path: '/healthz', expect: { status: 200, json: { ok: true } } },
  { name: '列表非空', path: '/items', expect: { status: 200, nonEmptyArray: 'items' } },
  { name: '未登录被拒', path: '/secret', expect: { status: 401 } },
]
function runProbe(mode, extra = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'argus-p-'))
  const pidFile = join(dir, 'pid')
  const spec = { start: ['node', FAKE], portEnv: 'PORT', env: { FAKE_MODE: mode, FAKE_PID_FILE: pidFile }, ready: { path: '/healthz', timeoutSec: 3 }, requests, ...extra }
  writeFileSync(join(dir, 'spec.json'), JSON.stringify(spec))
  const r = spawnSync(process.execPath, [PROBE, join(dir, 'spec.json')], { cwd: dir, encoding: 'utf8', timeout: 30_000 })
  let pid = null
  try { pid = Number(readFileSync(pidFile, 'utf8')) } catch { /* 没起来 */ }
  return { ...r, pid }
}
const gone = (pid) => { try { process.kill(pid, 0); return false } catch (e) { return e.code === 'ESRCH' } }

test('期望比对：状态码、JSON 字段、非空数组', () => {
  assert.deepEqual(toMismatch({ status: 200, json: { ok: true } }, 200, '{"ok":true}'), [])
  assert.match(toMismatch({ status: 401 }, 200, 'x')[0], /期望 401，实际 200/)
  assert.match(toMismatch({ json: { ok: true } }, 200, 'nope')[0], /不是/)
  assert.match(toMismatch({ nonEmptyArray: 'items' }, 200, '{"items":[]}')[0], /非空数组/)
})

test('全部通过退出 0；结束后服务进程不在了', () => {
  const r = runProbe('ok')
  assert.equal(r.status, 0, r.stdout)
  assert.equal(r.stdout.match(/✔/g).length, 3)
  assert.ok(r.pid && gone(r.pid), '探针结束后假服务必须被关掉')
})

test('返回不符时逐条列出 ✖ 和期望实际，退出 1', () => {
  const r = runProbe('wrong')
  assert.equal(r.status, 1)
  assert.match(r.stdout, /✖ 列表非空\n.*非空数组/)
  assert.match(r.stdout, /✖ 未登录被拒\n.*期望 401，实际 200/)
  assert.ok(r.stdout.includes('✔ 健康检查'))
  assert.ok(gone(r.pid))
})

test('服务一直不就绪：退出 2（没跑成），并关掉服务', () => {
  const r = runProbe('never-ready')
  assert.equal(r.status, 2)
  assert.match(r.stdout, /没有就绪/)
  assert.ok(gone(r.pid))
})

test('runSteps：前提不满足的步骤不跑、记没跑成；探针步骤自动准备；${BASE_SHA} 是被测提交的父提交', async () => {
  const runDir = newRunDir(1, 'steps')
  const src = join(runDir, 'src')
  mkdirSync(src, { recursive: true })
  const git = (...a) => execFileSync('git', ['-C', src, '-c', 'user.name=t', '-c', 'user.email=t@t', ...a], { encoding: 'utf8' }).trim()
  git('init', '-q')
  writeFileSync(join(src, 'a.txt'), '1'); git('add', '.'); git('commit', '-qm', 'one')
  const parent = git('rev-parse', 'HEAD')
  writeFileSync(join(src, 'a.txt'), '2'); git('commit', '-qam', 'two')
  const profile = {
    sandbox: false,
    steps: [
      { id: 'install', kind: 'setup', nameZh: '准备', cmd: ['node', '-e', ''] },
      { id: 'base', kind: 'product', nameZh: '基线', cmd: ['node', '-e', 'console.log("base=" + process.argv[1])', '${BASE_SHA}'] },
      { id: 'needs', kind: 'product', nameZh: '要新脚本', cmd: ['node', '-e', 'process.exit(1)'], requires: [{ file: 'a.txt', contains: 'NEW_FLAG', reasonZh: '脚本还不支持' }] },
      { id: 'probe', kind: 'product', nameZh: '接口', unavailableExitCodes: [2], cmd: ['node', '${RUN}/tools/probe-run.mjs', '${RUN}/probe-probe.json'], probe: { start: ['node', FAKE], portEnv: 'PORT', env: { FAKE_MODE: 'ok' }, ready: { path: '/healthz', timeoutSec: 5 }, requests } },
      { id: 'envstep', kind: 'product', nameZh: '步骤环境', env: { STEP_ONLY: 'yes' }, cmd: ['node', '-e', 'process.exit(process.env.STEP_ONLY === "yes" ? 0 : 1)'] },
    ],
  }
  const results = await runSteps(profile, src, runDir)
  const by = Object.fromEntries(results.map((r) => [r.id, r]))
  assert.ok(by.base.tail.includes(`base=${parent}`), by.base.tail)
  assert.equal(by.needs.unavailable, true)
  assert.equal(by.needs.unavailableReasonZh, '脚本还不支持')
  assert.equal(by.probe.ok, true, by.probe.tail)
  assert.equal(by.envstep.ok, true)
})

test('探测期望 absent：键必须不存在（跨用户列表里不能出现别人的键）', () => {
  const body = JSON.stringify({ entries: { mine: { value: 1 } } })
  assert.deepEqual(toMismatch({ status: 200, absent: ['entries.argus-qa-key'] }, 200, body), [])
  const leaked = JSON.stringify({ entries: { 'argus-qa-key': { value: { a: 1 } } } })
  assert.match(toMismatch({ status: 200, absent: ['entries.argus-qa-key'] }, 200, leaked)[0], /entries\.argus-qa-key 期望不存在/)
  assert.match(toMismatch({ absent: ['x'] }, 200, 'not json')[0], /期望返回 JSON/)
})

test('api-probe：登录态 GET /api/user-state（account 看得到自己的键，account-b 看不到）', async () => {
  const { loadProfile } = await import('../src/config.mjs')
  const p = loadProfile('mivo-canvas-plugin')
  const all = p.steps.find((s) => s.id === 'api-probe').probe.requests
  const ids = all.map((r) => r.id)
  const mine = all.find((r) => r.id === 'user-state-list'), other = all.find((r) => r.id === 'user-state-list-cross-user')
  assert.ok(mine && other, 'both list probes exist')
  assert.equal(mine.method, 'GET'); assert.equal(mine.path, '/api/user-state'); assert.equal(mine.auth, 'account')
  assert.deepEqual(mine.expect.json['entries.argus-qa-key.value'], { a: 1 })
  assert.equal(other.auth, 'account-b'); assert.equal(other.requires, 'account-b'); assert.deepEqual(other.expect.absent, ['entries.argus-qa-key'])
  // 顺序：先写入、再列出（两个用户）、再删除
  assert.ok(ids.indexOf('user-state-put') < ids.indexOf('user-state-list') && ids.indexOf('user-state-list-cross-user') < ids.indexOf('user-state-delete'))
})

test('api-probe：按画布列出已挂资产先写引用该资产的节点（mivo #434 起引用以节点 payload 为准）', async () => {
  const { loadProfile } = await import('../src/config.mjs')
  const all = loadProfile('mivo-canvas-plugin').steps.find((s) => s.id === 'api-probe').probe.requests
  const at = (id) => { const i = all.findIndex((r) => r.id === id); assert.ok(i >= 0, id); return i }
  const create = all[at('assets-ref-node-create')], clear = all[at('assets-ref-node-clear')]
  assert.equal(create.method, 'POST'); assert.equal(create.path, '/api/canvas/argus-qa-c1/nodes/argus-qa-n2')
  assert.equal(create.body.payload.asset.url, 'mivo-sasset:${accAsset}')
  assert.deepEqual(all[at('assets-attach-ref-node')].expect.json, { kind: 'already-attached' })
  assert.deepEqual(all[at('assets-list-attached')].expect.json, { assetIds: ['${accAsset}'] })
  assert.equal(clear.path, create.path); assert.equal(clear.headers['if-match'], '1'); assert.equal(clear.body.payload.asset, undefined)
  assert.deepEqual(all[at('assets-list-detached')].expect.json, { assetIds: [] })
  // 顺序：上传 → 写引用节点 → 列出含该资产 → 去掉引用 → 列出为空
  const order = ['assets-upload-account', 'assets-ref-node-create', 'assets-attach-ref-node', 'assets-list-attached', 'assets-detach', 'assets-ref-node-clear', 'assets-list-detached'].map(at)
  assert.deepEqual([...order].sort((a, b) => a - b), order)
})
