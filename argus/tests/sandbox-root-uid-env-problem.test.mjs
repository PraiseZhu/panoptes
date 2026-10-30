// 沙箱里 host root 没有映射：非特权 bwrap 只映射调用者自己的 uid，/ 与 /usr 的属主在沙箱内变成未映射的
// overflowuid（65534）。目标仓里按属主把门的断言因此在沙箱内必然失败 —— mivo
// scripts/ops/review-capacity-check/workspace.mjs:28-31「父目录必须由控制器独占管理」，被 openDirectory 从 /
// 开始逐级 fstat，于是 workspace-creation.native.test.mjs / batch-workspace.native.test.mjs 的一整片用例失败
// （10-30 的 unit 日志：这条 Error 出现在 9 个失败块里，[1][2][3][4][5][6] 直接打出来；workspace-creation.native.test.mjs
// 那 15 条（准备失败保留…：{replace,link,nonempty}、tmp-*、handoff、second-stat、目录创建后异常清理…）是同一个 throw：
// probe 的子进程在 openPrivateDirectory 里一打开 / 就 throw，注入和建目录都没进行，于是 failed=true、targetExists=false）。
// 基线版本在同一个沙箱里跑，同样失败，于是 panoptes 逐条记成 pre-existing
// 「单元测试「…」在基线版本上也失败」(PR #1351/#1444/#1450/#1454，10-30 七次)，日报里看不出真正原因是 QA 沙箱
// 的 uid 映射。这和 umask 002 造成 RUNNER_OWNER_MISMATCH 假失败是同一类问题，做法对齐 checkUmask：
// panoptes 自己进沙箱量出 / 的属主，在 system_health.env_problems 里点名，而不是只说「基线也失败」。
//
// 判据不能是「宿主 / 的属主 vs 沙箱 / 的属主」：这台 QA 机自己就在 userns 里（宿主视角 / 也是 65534），
// 两边一比相等就漏了。能区分的是「沙箱里那条按属主把门的断言能不能过」，也就是 / 的属主是否属于
// {0, 沙箱内进程 uid}，所以探针要把沙箱内的进程 uid 一起量回来。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdirSync, mkdtempSync, rmSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, join } from 'node:path'
import { spawnSync } from 'node:child_process'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-sandbox-uid-'))
const { bwrapArgs, sandboxMode } = await import('../src/runner.mjs')
const { checkUidMapping, envSnapshot } = await import('../src/nightly.mjs')

const BWRAP = process.env.ARGUS_BWRAP_BIN ?? '/usr/bin/bwrap'
const skip = process.platform !== 'linux' || !existsSync(BWRAP) || sandboxMode({}) !== 'bwrap'
  ? 'needs the bwrap sandbox (Linux with /usr/bin/bwrap)'
  : false

// 目标仓 assertControllerDirectory 的判据：目录属主必须是 0 或进程 uid
const CONTROLLER_JUDGEMENT = 'const fs = require("node:fs"); process.exit([0, process.getuid()].includes(fs.statSync(process.argv[1]).uid) ? 0 : 1)'

// 步骤在 <run>/src 里跑；bwrapArgs 另外建 /tmp/argus-<id> 短临时目录，一并清掉
function inSandbox(fn) {
  const runDir = mkdtempSync(join(tmpdir(), 'argus-sandbox-uid-run-'))
  mkdirSync(join(runDir, 'src'), { recursive: true })
  try {
    return fn((argv) => {
      const r = spawnSync(BWRAP, [...bwrapArgs(runDir, { chdir: runDir }), '--', ...argv], { encoding: 'utf8', timeout: 30_000 })
      assert.equal(r.error, undefined, String(r.error))
      return r
    })
  } finally {
    rmSync(runDir, { recursive: true, force: true })
    rmSync(join(tmpdir(), `argus-${basename(runDir)}`), { recursive: true, force: true })
  }
}

// 沙箱视角：进程 uid 与 / 的属主
function sandboxUidView() {
  return inSandbox((sandbox) => {
    const r = sandbox(['sh', '-c', 'echo "$(id -u) $(stat -c %u /)"'])
    assert.equal(r.status, 0, r.stderr)
    const [uid, rootUid] = r.stdout.trim().split(/\s+/).map(Number)
    return { uid, rootUid }
  })
}

const sandboxMapsRoot = (view) => [0, view.uid].includes(view.rootUid)

test('沙箱里 / 的属主不是 0 也不是进程 uid：按属主把门的断言在沙箱内必然失败', { skip }, (t) => {
  const view = sandboxUidView()
  if (sandboxMapsRoot(view)) return t.skip('这一台的沙箱里 / 的属主是 0 或进程 uid，这类失败在这里复现不出来')
  // 未映射的属主读成 overflowuid，宿主 / 归谁都不影响沙箱内的结论
  assert.notEqual(view.rootUid, 0)
  assert.notEqual(view.rootUid, view.uid)
  // 复刻目标仓判据真跑一次：沙箱内必须判失败（日志里那条「父目录必须由控制器独占管理」就是它）
  const judged = inSandbox((sandbox) => sandbox([process.execPath, '-e', CONTROLLER_JUDGEMENT, '/']))
  assert.notEqual(judged.status, 0, `沙箱内 / 的属主是 ${view.rootUid}：按属主把门的断言却判通过了？`)
  // 同一份判据对着沙箱里归进程自己的目录（tmpfs 根）要判通过，证明判失败的是属主而不是判据本身
  const ownDir = inSandbox((sandbox) => sandbox([process.execPath, '-e', CONTROLLER_JUDGEMENT, '/tmp']))
  assert.equal(ownDir.status, 0, '同一份判据对着归进程自己的目录要判通过')
  // 这台宿主自己就在 userns 里（/ 也不是 0 也不是调用者 uid）：宿主视角和沙箱视角一样，
  // 拿两边比属主区分不出来，只能问「沙箱里这条断言能不能过」
  const hostRootUid = statSync('/').uid
  if (![0, process.getuid()].includes(hostRootUid)) assert.equal(hostRootUid, view.rootUid)
})

test('checkUidMapping：沙箱 root 属主不可用时记 env_problem，点名「基线也失败」的误判', { skip }, (t) => {
  const view = sandboxUidView()
  if (sandboxMapsRoot(view)) return t.skip('这一台的沙箱里 / 的属主是 0 或进程 uid，不需要这条提醒')
  assert.equal(typeof checkUidMapping, 'function', 'nightly.mjs 要导出 checkUidMapping(health)（做法对齐 checkUmask：由它把沙箱 uid 映射记进 env_problems）')
  const health = { environment: envSnapshot(), env_problems: [] }
  checkUidMapping(health)
  assert.ok(health.env_problems.length >= 1, `沙箱里 / 的属主 ${view.rootUid} 既不是 0 也不是进程 uid ${view.uid}，按属主把门的断言必然失败；checkUidMapping 必须记 env_problem，否则日报只会说「基线也失败」`)
  const messages = health.env_problems.map((p) => p.message).join('\n')
  // 点名沙箱里 / 的真实属主：读者要能把它和 unit 日志里那条「父目录必须由控制器独占管理」对上
  assert.match(messages, new RegExp(`(?<![0-9])${view.rootUid}(?![0-9])`), `env_problem 要点名沙箱里 / 的属主 ${view.rootUid}（沙箱内进程 uid ${view.uid}）；实际：${messages}`)
  // 说明后果：这类失败在基线版本上也一样，所以被记成 pre-existing / 在基线版本上也失败，不是这个 PR 的问题
  assert.match(messages, /(base revision|pre-existing|基线)/, `env_problem 要说明这些失败在基线版本上也一样，是沙箱造成的误判；实际：${messages}`)
})

test('checkUidMapping：没有沙箱、没有环境快照时都不报，也不抛', () => {
  assert.equal(typeof checkUidMapping, 'function', 'nightly.mjs 要导出 checkUidMapping(health)')
  for (const health of [{ environment: { sandbox: 'env-only' }, env_problems: [] }, { env_problems: [] }]) {
    checkUidMapping(health)
    assert.deepEqual(health.env_problems, [], JSON.stringify(health.environment))
  }
})
