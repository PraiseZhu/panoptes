// inf1-22cebc32b9a494d2810b（category=check, stage=unit, 10-30 三次：PR #1449 #1424 #1447）
//
// 现象：mivo-canvas-plugin 的 scripts/ops/review-capacity-check/*.native.test.mjs 有 30 条权限类检查
// 在 PR 版本和基线版本上一起失败，nightly 按「also fails on the base revision」把它们全记成 pre-existing P3。
//
// 机制：这些检查按绝对路径逐级核验「属主是 root 或当前用户、且组/他人不可写」
//   - run.sh trusted_path（启动器）：/ 不满足 → 控制器启动路径不可信，拒绝加载模块
//   - workspace.mjs assertControllerDirectory：/ 不满足 → 父目录必须由控制器独占管理
// panoptes 用 bwrap 起被测检查（runner.mjs bwrapArgs：--ro-bind / /），非特权 bwrap 建的是一个只映射调用者
// 自己的 uid 的 user namespace（本次实测内部 uid_map 只有 "1000 1000 1"）。沙箱里没有 uid 0，宿主上 root 所有的
// 路径（/、/usr、/usr/bin/stat …）一律显示成溢出 uid 65534 —— 属主既不是 0 也不是 EUID，
// 所以「祖先必须由 root 或自己拥有」这条判据在沙箱里永远不成立，head 与 base 必然一起失败。
// 本机实测（同一份 bwrapArgs）：
//   / uid=65534 mode=755 · /usr uid=65534 mode=755 · <run>/home uid=1000 mode=755
//
// 缺的东西：system_health 的环境自检里没有「沙箱能不能表示 root 属主」这一项。nightly.mjs 的 env 检查只覆盖
// sandbox 模式、chromium、磁盘、umask（checkUmask），于是这类假失败只能落在每个 PR 的 findings 里，
// 既是噪声，也把同一批文件里的真实回归盖掉（同一个失败签名已经 pre-existing）。
//
// 正确处理（二选一，任一成立即视为修好）：
//   A. 沙箱里 / 对被测检查可信（属主是 root 或当前用户，组/他人不可写）——例如让沙箱拿到 root 映射；
//   B. 环境自检按沙箱内的真实属主记 env_problem：envSnapshot() 给出 path_trust（路径 → 属主/权限，至少含 /），
//      checkPathTrust(health) 把不可信项写进 system_health.env_problems（诊断信息要带路径和实际 uid/模式）。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-rootowner-'))
const { envSnapshot, checkPathTrust } = await import('../src/nightly.mjs')
const { bwrapArgs, sandboxMode } = await import('../src/runner.mjs')

// 用 panoptes 自己的沙箱参数（bwrapArgs）在沙箱里读 / 的真实属主：父进程看到的是宿主值，不能代表被测检查看到的值
function probeSandboxRoot() {
  if (sandboxMode({}) !== 'bwrap') return null
  const runDir = mkdtempSync(join(process.env.ARGUS_ROOT, 'probe-run-'))
  try {
    mkdirSync(join(runDir, 'home'), { recursive: true })
    const out = execFileSync('/usr/bin/bwrap', [...bwrapArgs(runDir, {}), '--', '/usr/bin/stat', '-c', '%u %a', '/'], { encoding: 'utf8', timeout: 20_000 }).trim()
    const [uid, mode] = out.split(/\s+/).map(Number)
    return { uid, mode: Number.isNaN(mode) ? null : parseInt(String(mode), 8) }
  } catch {
    return null
  } finally {
    rmSync(runDir, { recursive: true, force: true })
  }
}

const hasBwrap = sandboxMode({}) === 'bwrap'
const isTrusted = ({ uid, mode }) => (uid === 0 || uid === process.getuid()) && ((Number(mode) & 0o022) === 0)
const toNum = (v) => Number(v)
const toMode = (v) => (typeof v === 'string' ? parseInt(v.replace(/^0+/, '0'), 8) : Number(v))

test('沙箱把 root 所有的祖先显示成溢出 uid 时，环境自检必须记 env_problem（否则 mivo 的启动器/工作目录属主检查在 head 与 base 上一起失败）', { skip: hasBwrap ? false : '本机没有 bwrap：panoptes 不会跑被测检查，这条故障不存在' }, () => {
  const probed = probeSandboxRoot()
  assert.ok(probed, '用 panoptes 自己的 bwrapArgs 起沙箱失败，读不到沙箱内 / 的属主')
  // A：沙箱已让 / 可信（另一种正确修法）→ 这条不适用
  if (isTrusted(probed)) return

  const health = { environment: envSnapshot(), env_problems: [] }
  const entry = (health.environment.path_trust ?? []).find((e) => e.path === '/')
  assert.ok(entry, 'envSnapshot() 必须记录沙箱内 / 的属主与权限（path_trust），否则环境自检看不到这条故障')
  assert.equal(toNum(entry.uid), probed.uid)
  assert.equal(toMode(entry.mode) & 0o777, probed.mode & 0o777)

  // B：沙箱内 / 不可信 → 必须进 env_problems，而且要带得出诊断信息（路径 + 实际属主）
  checkPathTrust(health)
  const problems = health.env_problems.filter((p) => String(p.message ?? '').includes('/'))
  assert.ok(problems.length > 0, `沙箱内 / 的属主是 ${probed.uid}（既不是 0 也不是 EUID ${process.getuid()}），环境自检却没有任何 env_problem`)
  assert.match(problems.map((p) => p.message).join('\n'), new RegExp(`\\b${probed.uid}\\b`), 'env_problem 必须写出实际属主 uid，否则读报告的人看不出是沙箱把 root 显示成了 65534')
})

test('checkPathTrust：属主既不是 root 也不是当前用户 → env_problem', () => {
  const health = { environment: { path_trust: [{ path: '/', uid: 65534, mode: 0o755 }] }, env_problems: [] }
  checkPathTrust(health)
  assert.equal(health.env_problems.length, 1)
  assert.match(health.env_problems[0].message, /65534/)
})

test('checkPathTrust：组/他人可写的祖先同样不可信（0775/1777 在 root 下才算可信）', () => {
  const health = { environment: { path_trust: [{ path: '/workspace/mivo-qa', uid: process.getuid(), mode: 0o775 }] }, env_problems: [] }
  checkPathTrust(health)
  assert.equal(health.env_problems.length, 1)
  assert.match(health.env_problems[0].message, /775|组|world|group/i)
})

test('checkPathTrust：root 或当前用户所有、组/他人不可写时不报', () => {
  const health = {
    environment: { path_trust: [
      { path: '/', uid: 0, mode: 0o755 },
      { path: '/workspace/mivo-qa/argus-root/cache/runs/x/home', uid: process.getuid(), mode: 0o700 },
    ] },
    env_problems: [],
  }
  checkPathTrust(health)
  assert.deepEqual(health.env_problems, [])
})
