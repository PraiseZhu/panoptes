// 沙箱里 host root 没有映射：非特权 bwrap 只映射调用者自己的 uid，/、/usr、/usr/bin 在沙箱内的属主变成
// overflowuid(65534)。目标仓里按属主把关的安全断言（mivo review-capacity-check：「父目录必须由控制器独占管理」
// 「控制器启动路径不可信，拒绝加载模块」）在沙箱内必然假失败；基线版本同样在沙箱里跑，于是逐个记成
// pre-existing「also fails on the base revision」（10-30 六次，workspace-creation.native.test.mjs > tmp-chown）。
// panoptes 必须自己量出沙箱视角的 root 属主并在 system_health.env_problems 里点名，而不是让日报只说「基线也失败」。
import test from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, rmSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, join } from 'node:path'
import { bwrapArgs, sandboxMode, sandboxRootUid } from '../src/runner.mjs'
import { checkUidMapping, envSnapshot } from '../src/nightly.mjs'

const BWRAP = process.env.ARGUS_BWRAP_BIN ?? '/usr/bin/bwrap'
const skip = process.platform !== 'linux' || !existsSync(BWRAP) || sandboxMode({}) !== 'bwrap'
  ? 'needs the bwrap sandbox (Linux with /usr/bin/bwrap)'
  : false

const inSandbox = (runDir, argv) => {
  const r = spawnSync(BWRAP, [...bwrapArgs(runDir, { chdir: runDir }), '--', ...argv], { encoding: 'utf8', timeout: 30_000 })
  assert.equal(r.error, undefined)
  return r
}

// run 目录形状：步骤在 <run>/src 里跑；bwrapArgs 另外会建 /tmp/argus-<id> 短临时目录，一并清掉
function withRunDir(fn) {
  const runDir = mkdtempSync(join(tmpdir(), 'argus-root-uid-'))
  mkdirSync(join(runDir, 'src'))
  try { return fn(runDir) } finally {
    rmSync(runDir, { recursive: true, force: true })
    rmSync(join(tmpdir(), `argus-${basename(runDir)}`), { recursive: true, force: true })
  }
}

test('sandboxRootUid：量的是沙箱里 / 的属主，和「/ 必须由 uid 0 独占」的真实断言结论一致', { skip }, () => {
  withRunDir((runDir) => {
    const uid = sandboxRootUid({ runDir })
    assert.equal(Number.isInteger(uid), true, `sandboxRootUid 要在沙箱里真的跑出数字，实际 ${uid}`)
    const view = inSandbox(runDir, ['stat', '-c', '%u', '/'])
    assert.equal(view.status, 0, view.stderr)
    assert.equal(uid, Number(view.stdout.trim()), 'sandboxRootUid 必须是沙箱里 / 的属主，不是宿主进程的视图')
    // 目标仓的断言就是这一条：/ 必须由 uid 0 独占。沙箱里 uid 不是 0 时这类用例必然失败，探针必须提前量出来
    const assertRoot = inSandbox(runDir, [process.execPath, '-e', 'process.exit(require("node:fs").statSync("/").uid === 0 ? 0 : 1)'])
    assert.equal(assertRoot.status === 0, uid === 0, `沙箱 root 属主是 ${uid}：root 属主断言的成败必须与探针一致`)
  })
})

test('checkUidMapping：沙箱把 root 变成 65534 时记 env_problem，点名 pre-existing 误判', () => {
  const h = { environment: { sandbox: 'bwrap', root_uid: 0, sandbox_root_uid: 65534 }, env_problems: [] }
  checkUidMapping(h)
  assert.equal(h.env_problems.length, 1)
  assert.match(h.env_problems[0].message, /uid 65534/)
  assert.match(h.env_problems[0].message, /root-owned/)
  assert.match(h.env_problems[0].message, /pre-existing/)
})

test('checkUidMapping：root 映射正常、探针拿不到结果、没用沙箱时都不记', () => {
  for (const env of [
    { sandbox: 'bwrap', root_uid: 0, sandbox_root_uid: 0 },
    { sandbox: 'bwrap', root_uid: 0, sandbox_root_uid: null },
    { sandbox: 'env-only' },
  ]) {
    const h = { environment: env, env_problems: [] }
    checkUidMapping(h)
    assert.deepEqual(h.env_problems, [], JSON.stringify(env))
  }
})

test('envSnapshot 带上 root_uid 与 sandbox_root_uid，日报才能解释沙箱造成的假失败', () => {
  const env = envSnapshot()
  assert.equal(env.root_uid, statSync('/').uid)
  assert.equal('sandbox_root_uid' in env, true)
})
