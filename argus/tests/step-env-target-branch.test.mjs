// 产品步骤的目标分支身份（inf1-2fc423a146a9666ef7a7，2026-11-16 三次运行：#2151 #2430 #2438）。
//
// 签名：单元测试「scripts/ci/real-timer-guard.test.mjs > 真实定时器守卫:仓库实扫 > 存量与基线一一对应,
// 且基线不超出目标分支基线」在 PR 版本和基线版本上一模一样地失败。panoptes 于是记 pre-existing（base_failed /
// 「also fails on the base revision」），pi/bin/infrafix.py 的 check 规则（pre_existing=true 且非 flaky）
// 每 7 天照这个签名开一张「在基线版本上也失败」的工单。
//
// 为什么是 panoptes 的机器问题：那个用例要读「目标分支」的基线，目标分支来自 CI 环境变量。
//   mivo scripts/ci/guard-target.mjs（d7caa444c6）：
//     guardTargetBranch = MIVO_GUARD_BASE_REF ?? GITHUB_BASE_REF ?? (GITHUB_REF_NAME 是 main/release/* 时用它) ?? 'main'
//     guardBaseRef      = MIVO_GUARD_BASE_SHA ?? 'refs/remotes/origin/' + guardTargetBranch
//   argus/src/runner.mjs:146 stepEnv() 只给被测进程 CI=1（第 155 行），不给 PR 的目标分支 —— 于是守卫退回
//   refs/remotes/origin/main。这三个 PR 的 base_ref 是 integration/3d：
//     integration/3d 的 scripts/ci/real-timer-baseline.json 给
//     cindyplugin/src/__tests__/placedArtifactAccounting.test.ts 登记了 600ms（该文件第 186 行确实是真实等待）；
//     main 的基线没有这一条，main 上那份文件也只有 5ms（低于 10ms 门槛）。
//   对照 main 时这 600ms 就成了「目标分支的代码与基线都解释不了」→ 报错原文与报告里的 log_tail 完全一致。
//   同一份代码在 integration/3d 自己的 CI 里是通过的；基线版本（上一跳的合并提交）也一样带着这条基线，
//   所以两轮一起红 —— 不是 PR 的问题，也不是目标分支坏了，是 panoptes 让被测代码看错了目标分支。
//
// 修法方向：把 PR 的目标分支（nightly 的 pr.base.ref，argus/src/nightly.mjs:255）交给步骤环境，
// 让产品用例看到的 CI 身份和 GitHub Actions 同形（GITHUB_BASE_REF / MIVO_GUARD_BASE_REF）。
// 这里按产品用例的走法把链走一遍：临时 git 仓库里同时有 origin/main 与 origin/integration/3d，
// 用 runSteps 跑一个复刻的守卫步骤，断言它对照的是 PR 的目标分支，而不是 main。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync, spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'

const root = mkdtempSync(join(tmpdir(), 'argus-targetbranch-'))
process.env.ARGUS_ROOT = root
const { newRunDir, runSteps, cleanupRun } = await import('../src/runner.mjs')
const dirs = []
after(async () => { for (const d of dirs) await cleanupRun(null, d); rmSync(root, { recursive: true, force: true }) })

const FILE = 'cindyplugin/src/__tests__/placedArtifactAccounting.test.ts'
const BASELINE = 'scripts/ci/real-timer-baseline.json'
const GUARD = 'scripts/ci/target-branch-guard.mjs'
const TARGET_BRANCH = 'integration/3d'

// 复刻 mivo 的守卫：读目标分支的基线 + 用当前规则扫目标分支上同一份代码，解释不了「基线新增」就判红
// （scripts/ci/real-timer-guard.mjs 的 compareWithBaseline，d7caa444c6 里在 1831-1850 行）。
const GUARD_SOURCE = `
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'

const BASELINE = ${JSON.stringify(BASELINE)}
const git = (...args) => execFileSync('git', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()

// mivo scripts/ci/guard-target.mjs：guardTargetBranch / guardBaseRef，与仓库里的实现同形
const targetBranch = (env) => {
  const explicit = [env.MIVO_GUARD_BASE_REF, env.GITHUB_BASE_REF].map((value) => value && value.trim()).find(Boolean)
  if (explicit) return explicit
  const pushed = env.GITHUB_REF_NAME && env.GITHUB_REF_NAME.trim()
  return pushed === 'main' || /^release\\/[0-9]+\\.[0-9]+$/.test(pushed || '') ? pushed : 'main'
}
const targetRef = (env) => (env.MIVO_GUARD_BASE_SHA || '').trim() || 'refs/remotes/origin/' + targetBranch(env)

const target = targetRef(process.env)
const show = (spec) => { try { return git('show', spec) } catch { return '' } }
const delaysOf = (source) => [...String(source).matchAll(/(?:setTimeout|setInterval)\\s*\\([^,)]*,\\s*(\\d+)/g)]
  .map((m) => Number(m[1])).filter((delay) => Number.isFinite(delay) && delay >= 10)
const toUnmatched = (list, reference) => {
  const left = new Map()
  for (const item of reference) left.set(item, (left.get(item) ?? 0) + 1)
  return list.filter((item) => { const n = left.get(item) ?? 0; if (n <= 0) return true; left.set(item, n - 1); return false })
}

let targetCommit
try { targetCommit = git('rev-parse', '--verify', target + '^{commit}') } catch {
  console.error('[FAIL] 无法读取目标分支真实定时器基线: ' + target + ' 不可解析')
  process.exit(1)
}
console.log('守卫对照的目标分支: ' + target)
let current
try { current = JSON.parse(readFileSync(BASELINE, 'utf8')).files ?? {} } catch { current = {} }
const targetFiles = JSON.parse(show(targetCommit + ':' + BASELINE) || '{"files":{}}').files ?? {}
const failures = []
for (const [file, delays] of Object.entries(current)) {
  // 目标分支上同一份代码用当前规则扫得出来的存量，和它的基线一起才是「允许登记」的上限
  const explained = [...(targetFiles[file] ?? []), ...delaysOf(show(targetCommit + ':' + file))]
  const added = toUnmatched(delays, explained)
  if (added.length) failures.push('[FAIL] 基线不得新增: ' + file + ' 新登记延迟 ' + added.join(',') + 'ms,目标分支的代码与基线都解释不了(只允许减少)')
}
if (failures.length) { console.error(failures.join('\\n')); process.exit(1) }
`

// 临时仓库：main 是「没有这处 600ms」的旧状态，integration/3d 是 PR 真正要落的分支（基线登记了这处存量）
function makeRepo() {
  const src = join(root, 'repo')
  mkdirSync(src, { recursive: true })
  const git = (...args) => execFileSync('git', ['-C', src, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', ...args],
    { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()
  const put = (path, content) => { mkdirSync(dirname(join(src, path)), { recursive: true }); writeFileSync(join(src, path), content) }
  const commit = (message) => { git('add', '.'); git('commit', '-q', '-m', message); return git('rev-parse', 'HEAD') }
  git('init', '-q', '-b', 'main')
  put(FILE, 'await new Promise((resolve) => setTimeout(resolve, 5))\n')
  put(BASELINE, JSON.stringify({ files: {} }, null, 2))
  put('README.md', 'fixture\n')
  const mainSha = commit('main: 只有 5ms，低于基线门槛')
  put(FILE, 'await new Promise((resolve) => setTimeout(resolve, 600))\n')
  put(BASELINE, JSON.stringify({ files: { [FILE]: [600] } }, null, 2))
  const targetSha = commit(`${TARGET_BRANCH}: 600ms 真实等待 + 基线登记`)
  put(GUARD, GUARD_SOURCE)
  // 生产里 src 是裸镜像的 worktree，refs/remotes/origin/* 由 ensureMirror 的 fetch 写进去（runner.mjs:80）
  git('update-ref', 'refs/remotes/origin/main', mainSha)
  git('update-ref', `refs/remotes/origin/${TARGET_BRANCH}`, targetSha)
  return { src, mainSha, targetSha }
}
const repo = makeRepo()

const stepFor = (cmd) => ({ id: 'unit', kind: 'product', nameZh: '单元测试', cache: false, cmd })

// 夹具自检：同一份代码，目标分支对了就通过，退回 main 就复现报告里的那句报错。
// 这一条在修复前后都成立 —— 它证明「差别只在目标分支这一个变量」，下面的失败不是夹具编出来的。
test('复刻夹具自检：目标分支是 integration/3d 时通过，退回 main 就复现 #2151 #2430 #2438 的报错原文', () => {
  const run = (env) => spawnSync(process.execPath, [join(repo.src, GUARD)], {
    cwd: repo.src, encoding: 'utf8', env: { PATH: process.env.PATH, CI: '1', ...env },
  })
  const withTarget = run({ GITHUB_BASE_REF: TARGET_BRANCH })
  assert.equal(withTarget.status, 0, `目标分支 ${TARGET_BRANCH} 下同一份代码必须通过（夹具与 integration/3d 的真实状态不符）：${withTarget.stderr || withTarget.stdout}`)
  const withoutTarget = run({})
  assert.equal(withoutTarget.status, 1, `没有 CI 目标分支时守卫按 mivo 的默认值对照 origin/main，必须判红：${withoutTarget.stdout}`)
  assert.match(withoutTarget.stderr, new RegExp(`基线不得新增: ${FILE.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} 新登记延迟 600ms`),
    '要先复现报告里的失败形态，才谈得上修 panoptes')
  assert.match(withoutTarget.stdout, /refs\/remotes\/origin\/main/, '缺目标分支时 mivo 的 guardBaseRef 退回 origin/main')
})

// 回归：产品步骤的进程环境必须让仓库的「相对目标分支」守卫看到 PR 的目标分支。
// panoptes 拿得到它（pr.base.ref → argus/src/nightly.mjs:255），交给步骤的通道是 execution
// ——那里已经带着 requestedSha / checkedOutSha / diffBase（runner.mjs:777）。
// 基线轮（label 'b'，runner 的 executeOnSha 对同一个 execution 跑 base_sha）走的是同一条通道：
// 目标分支对了，基线版本也会通过，报告就不会再写 pre-existing /「also fails on the base revision」。
test('产品步骤拿到 PR 的目标分支：真实定时器守卫不能退回 origin/main（inf1-2fc423a146a9666ef7a7）', async () => {
  const runDir = newRunDir(1, 'targetbranch'); dirs.push(runDir)
  const profile = { sandbox: false, steps: [stepFor([process.execPath, GUARD])] }
  const [res] = await runSteps(profile, repo.src, runDir, {
    execution: { jobId: 'fixture-targetbranch', runLabel: 'h1', requestedSha: repo.targetSha, checkedOutSha: repo.targetSha, diffBase: repo.mainSha, baseRef: TARGET_BRANCH },
  })
  assert.ok(res, '步骤要有结果')
  assert.equal(res.ok, true,
    '产品步骤里没有任何目标分支身份（runner.mjs:146 stepEnv 只给 CI=1，没有 GITHUB_BASE_REF / MIVO_GUARD_BASE_REF），' +
    '仓库的守卫于是按 mivo scripts/ci/guard-target.mjs 的默认值对照 refs/remotes/origin/main：' +
    `${FILE} 的 600ms 在 main 上既没有基线也没有代码，报「基线不得新增…目标分支的代码与基线都解释不了」。` +
    'PR 版本和基线版本因此一模一样地失败，报告写 pre-existing（base_failed），infrafix 每 7 天开一张' +
    '「在基线版本上也失败」的工单。步骤输出：\n' + String(res.tail ?? '').slice(-1500))
})
