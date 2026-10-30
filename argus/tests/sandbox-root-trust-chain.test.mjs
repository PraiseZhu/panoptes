// 沙箱的根 `/` 必须由 root 或沙箱用户自己拥有：mivo 的 scripts/ops/review-capacity-check 从 `/` 起
// 逐层校验「控制器父目录」（workspace.mjs 的 assertControllerDirectory：uid ∈ {0, 当前用户} 且不带组/其他写位；
// run.sh 的 trusted_path 同理）。真 CI 上 `/` 属主是 0，这条链成立。
//
// panoptes 原来用 `--ro-bind / /` 建沙箱：bwrap 的非特权 user namespace 里宿主 root 没有映射，
// 沙箱内 stat('/') 读成 65534(nobody)，而 EUID 是 1000 → 链在 `/` 处就被拒：
//   `父目录必须由控制器独占管理` / `控制器启动路径不可信，拒绝加载模块`
// 合并版本轮次和基线轮次跑在同一个沙箱里，两边一起失败，于是 nightly 只能记成
//   `also fails on the base revision (base run failed; failure type not fingerprint-comparable…)`
// 每次 PR 30 条 P3，5 次运行反复出现（PR-1449/1450/1454：workspace-creation.native.test.mjs >
// 「目录创建后异常清理原空目录并允许重试：mode」在基线版本上也失败）。
// 这两条用例要的是「被测仓库自己的信任链断言在 panoptes 沙箱里和真 CI 一样成立」，不是放宽被测仓的断言。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'

const root = mkdtempSync(join(tmpdir(), 'argus-sandbox-root-'))
process.env.ARGUS_ROOT = root
const { bwrapArgs, newRunDir, runSteps, sandboxMode } = await import('../src/runner.mjs')

after(() => { rmSync(root, { recursive: true, force: true }) })

const BWRAP = process.env.ARGUS_BWRAP_BIN ?? '/usr/bin/bwrap'
// 只在这台机器真能用 bwrap 建沙箱时才测；macOS（sandbox-exec）与没装 bubblewrap 的机器跳过
const skip = process.platform !== 'linux' ? 'linux only (bwrap sandbox)'
  : !existsSync(BWRAP) ? 'bubblewrap not installed'
    : sandboxMode({}) !== 'bwrap' ? `sandbox mode is ${sandboxMode({})}`
      : false

// 被测仓那类信任链检查：从 cwd 一路 stat 到 `/`，每一层 uid 必须是 0 或当前用户，且不带组/其他写位
const WALK = `import { statSync } from 'node:fs'
import { dirname } from 'node:path'
const owner = process.getuid()
const bad = []
for (let p = process.cwd(); ; p = dirname(p)) {
  const st = statSync(p)
  const uid = st.uid, mode = st.mode & 0o777
  if (![0, owner].includes(uid) || ((mode & 0o022) && !(uid === 0 && (st.mode & 0o1000)))) bad.push({ path: p, uid, mode: mode.toString(8) })
  if (p === '/') break
}
`
// 只报告（对照用）
const PROBE = `${WALK}console.log(JSON.stringify({ euid: owner, cwd: process.cwd(), bad }))\n`
// 断言失败就退出 1，和 workspace.mjs 的报错一致
const GUARD = `${WALK}if (bad.length) {
  process.stderr.write('父目录必须由控制器独占管理\\n' + bad.map((l) => \`\${l.path} uid=\${l.uid} mode=\${l.mode}\`).join('\\n') + '\\n')
  process.exitCode = 1
}
`
const writeTool = (runDir, name, body) => {
  mkdirSync(join(runDir, 'tools'), { recursive: true })
  const file = join(runDir, 'tools', name)
  writeFileSync(file, body)
  return file
}

test('沙箱根 / 的属主是 root 或沙箱用户（review-capacity-check 的父目录信任链要求）', { skip }, () => {
  const runDir = newRunDir('n1', 'h1')
  mkdirSync(join(runDir, 'src'), { recursive: true })
  const probe = writeTool(runDir, 'trust-chain-probe.mjs', PROBE)
  // 与 runner.sandboxed() 用同一份 bwrap 参数（extraRo 里带上 node 前缀，步骤才跑得起来）
  const args = bwrapArgs(runDir, { extraRo: [dirname(dirname(process.execPath))], chdir: join(runDir, 'src') })
  const { euid, bad } = JSON.parse(execFileSync(BWRAP, [...args, '--', process.execPath, probe], { encoding: 'utf8' }).trim().split('\n').pop())
  assert.deepEqual(bad, [], `沙箱里这些层的属主/权限和真 CI 不一致，被测仓的父目录信任链会在 / 处被拒（合并前版本一样失败）：${JSON.stringify(bad)}，euid=${euid}`)
})

test('单元测试步骤在沙箱里能通过父目录信任链（基线轮次和合并轮次同一套沙箱）', { skip }, async () => {
  const src = join(root, 'checkout')
  mkdirSync(join(src, 'scripts', 'ops', 'review-capacity-check'), { recursive: true })
  // 步骤命令就是被测仓那类断言：链不成立就 exit 1
  const profile = { steps: [{ id: 'unit', kind: 'product', nameZh: '单元测试', covers: ['unit'],
    cmd: [process.execPath, '${RUN}/tools/trust-chain.mjs'] }] }
  for (const label of ['h1', 'b']) {
    const runDir = newRunDir('n1', label)
    mkdirSync(join(runDir, 'src'), { recursive: true }) // 沙箱的 --chdir 是 runDir/src（检出目录）
    writeTool(runDir, 'trust-chain.mjs', GUARD)
    const [res] = await runSteps(profile, src, runDir, { stepIds: ['unit'] })
    assert.equal(res?.ok, true, `${label} 轮次：沙箱里父目录信任链没通过，基线对照也会一起失败\n${String(res?.tail ?? '').slice(-800)}`)
  }
})
