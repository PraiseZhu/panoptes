// 在隔离 run 目录里检出指定版本并执行项目 profile 的步骤
import { mkdirSync, writeFileSync, rmSync, existsSync, readdirSync, readFileSync, readlinkSync, statSync, lstatSync, realpathSync, cpSync } from 'node:fs'
import { join, dirname, basename, resolve, relative, isAbsolute } from 'node:path'
import { homedir, tmpdir } from 'node:os'
import { run, isStopping, overBudget } from './exec.mjs'
import { RUNS_DIR, MIRRORS_DIR, NPM_CACHE_DIR, UV_CACHE_DIR, UV_PYTHON_DIR, CACHE_DIR, CODE_DIR } from './paths.mjs'
import { log, toPlainZh, waitWithinBudget } from './util.mjs'
import { machineLoad } from './memory.mjs'
import { gitTokenFor } from './github.mjs'
import { fileURLToPath } from 'node:url'
import { validateTestReport } from './config.mjs'
import { normalizeTestOutcomes } from './test-outcomes.mjs'
import { vitestCoverageArgs, readCoverageDir, readV8Dir, mergeCoverageFiles } from './coverage.mjs'
import { stepCacheEnabled, stepCacheKey, lookupStepResult, storeStepResult, reusedResult } from './stepcache.mjs'
import { shardPlan, reserveShards, shardArgs, mergeVitestJson, mergeCoverageFinal, coverageSummaryOf, readJsonOrNull } from './shard.mjs'

// 文件权限掩码固定为 022：QA 曾在 umask 002 下跑，测试夹具目录变成 0775，触发 RUNNER_OWNER_MISMATCH 假失败（mivo #1385）。
// 模块加载时就固定，之后 spawn 的所有子进程都继承 022；固定前继承到的值留给 system_health 记录
export const PINNED_UMASK = 0o022
export const fmtUmask = (n) => (n == null ? null : n.toString(8).padStart(4, '0'))
// 读本进程当前真实的 umask（/proc/self/status 的 Umask 行），读不到返回 null
export function currentUmask() {
  try { const m = /^Umask:\s*([0-7]+)/m.exec(readFileSync('/proc/self/status', 'utf8')); return m ? m[1].padStart(4, '0') : null } catch { return null }
}
export function pinUmask() {
  const prev = process.umask(PINNED_UMASK)
  return fmtUmask(prev)
}
export const INHERITED_UMASK = pinUmask()

const PROBE_SCRIPT = fileURLToPath(new URL('./probe-run.mjs', import.meta.url))
const AI_TEST_SCRIPT = fileURLToPath(new URL('./ai-test-run.mjs', import.meta.url))
const NODE_TEST_REPORTER = fileURLToPath(new URL('./node-test-reporter.mjs', import.meta.url))

const GH_HELPER = ['-c', 'credential.helper=', '-c', `credential.helper=!${process.env.ARGUS_GH_BIN ?? 'gh'} auth git-credential`]
// App 覆盖的仓用安装令牌（经环境变量传给凭证助手，不出现在命令行里）；其余仓用 gh 登录
const TOKEN_HELPER = ['-c', 'credential.helper=', '-c', 'credential.helper=!f() { echo username=x-access-token; echo "password=$ARGUS_GIT_TOKEN"; }; f']

async function gitAuth(repo) {
  const token = await gitTokenFor(repo).catch(() => null)
  return token ? { args: TOKEN_HELPER, env: { ARGUS_GIT_TOKEN: token } } : { args: GH_HELPER, env: {} }
}
const MANIFEST = 'argus-run.json'

// git 取码要用真实 HOME 找 gh 登录；测试步骤在 stepEnv 里把 HOME 换成 run 目录
export const testTz = () => process.env.PANOPTES_TEST_TZ || 'Asia/Shanghai'
export const baseEnv = () => ({
  PATH: process.env.PATH,
  HOME: process.env.HOME,
  LANG: 'en_US.UTF-8',
  // 测试按上海时区写的断言：子进程硬性固定 TZ=Asia/Shanghai，不继承父进程/本机时区（本机是 America/Los_Angeles）。
  // 只有显式 PANOPTES_TEST_TZ 能改（排查用）；profile.env 里的 TZ 仍可按项目覆盖
  TZ: testTz(),
  GIT_TERMINAL_PROMPT: '0',
  // 大文件（LFS）默认不在检出时下载，需要的项目在 profile 里声明 lfs: true 后单独拉取
  GIT_LFS_SKIP_SMUDGE: '1',
})

export function mirrorPath(repo) {
  return join(MIRRORS_DIR, `${repo.replace('/', '__')}.git`)
}

// 本地裸镜像：只取分支，不取 refs/pull；每次任务前 fetch。
// 网络抖动时重试 3 次；仍失败但镜像里已有需要的版本就照常测，否则报错交给上层重排队
export async function ensureMirror(repo, neededSha = null, { extraRefspecs = [] } = {}) {
  const dir = mirrorPath(repo)
  const url = `https://github.com/${repo}.git`
  const auth = await gitAuth(repo)
  const env = { ...baseEnv(), ...auth.env }
  const hasNeeded = async () => Boolean(neededSha) && (await run('git', ['-C', dir, 'cat-file', '-e', `${neededSha}^{commit}`], { env: baseEnv(), timeoutMs: 30_000 })).ok
  if (!existsSync(join(dir, 'HEAD'))) {
    mkdirSync(MIRRORS_DIR, { recursive: true })
    // 不用 partial clone：worktree 检出时的按需取对象不会带上凭证
    const res = await run('git', [...auth.args, 'clone', '--bare', url, dir], { env, timeoutMs: 30 * 60_000 })
    if (!res.ok) throw new Error(`拉取代码仓失败：${res.tail.slice(-500)}`)
  }
  // 同时写 refs/remotes/origin/*：项目测试里常有「相对 origin/main 的 merge-base」比较，缺这组引用会误报失败
  let res
  for (let attempt = 1; attempt <= 3; attempt++) {
    res = await run('git', [...auth.args, '-C', dir, 'fetch', '--prune', 'origin', '+refs/heads/*:refs/heads/*', '+refs/heads/*:refs/remotes/origin/*', ...extraRefspecs], { env, timeoutMs: 20 * 60_000 })
    if (res.ok || isStopping() || overBudget()) break
    log('info', 'mirror fetch retry', { repo, attempt })
    // 预算用完（run() 一开就返回 budgetExceeded，没起进程）不是网络抖动：等下去也不可能成功，
    // 每次等待也不越过 PR 时间预算（#1166：45 min 预算跑成 46 min 32 s，正是这 15+30+45 s）
    if (!await waitWithinBudget(attempt * 15_000)) break
  }
  if (!res.ok) {
    if (!await hasNeeded()) throw new Error(`更新代码仓失败：${res.tail.slice(-500)}`)
    log('info', 'mirror fetch failed, using cached commit', { repo })
  }
  // 分支 fetch 只取 refs/heads/*（开放 PR 另加 refs/pull/N/head）：需要的版本不挂在任何分支上时
  // （合并后 main 被回退、探针 PR 的一次性基线分支合并后删掉）镜像里仍然没有它，但 GitHub 保留
  // refs/pull/N/{head,merge} 并允许按可达版本取对象，所以再按这个版本本身向远端要一次；远端确实没有时
  // 照旧由 checkout 报「镜像里缺这个版本」。只认完整 sha：名字/选项形式不当请求发给远端
  if (/^[a-f0-9]{40,64}$/i.test(neededSha ?? '') && !isStopping() && !overBudget() && !await hasNeeded()) {
    const bySha = await run('git', [...auth.args, '-C', dir, 'fetch', 'origin', neededSha], { env, timeoutMs: 20 * 60_000 })
    if (bySha.ok) log('info', 'mirror fetched version not on any branch', { repo, sha: neededSha })
    else log('info', 'mirror fetch by version failed', { repo, sha: neededSha })
  }
  return dir
}

// macOS 的 unix socket 路径上限约 104 字符，run 目录太深会让测试里的本地 socket 起不来；
// 临时目录改放短路径 /private/tmp/argus-<id>，随 run 一起清理
const SHORT_TMP_ROOT = process.platform === 'darwin' ? '/private/tmp' : tmpdir()
const shortTmpOf = (runDir) => join(SHORT_TMP_ROOT, `argus-${runDir.split('/').pop()}`)
// 每步独立的 TMPDIR（在 run 的短临时目录下，沙箱已绑定）：前一步在 $TMPDIR 留下的 node_modules 等文件
// 会变成后一步测试临时目录的「祖先」，例如 org-market-company-no-dependencies 断言祖先无 node_modules，
// 只在本机前序步骤真跑（没被 step cache 复用）时失败，GitHub CI 每个 job 全新 /tmp 不会。
export function stepTmpDir(baseTmp, stepId) {
  const dir = join(baseTmp, `step-${String(stepId).replace(/[^A-Za-z0-9_.-]/g, '_')}`)
  rmSync(dir, { recursive: true, force: true })
  mkdirSync(dir, { recursive: true })
  return dir
}

// 两个提交的 merge-base（开放 PR 用它当「改动前」的对照版本）
export async function mergeBase(mirror, a, b) {
  const res = await run('git', ['-C', mirror, 'merge-base', a, b], { env: baseEnv(), timeoutMs: 60_000 })
  const sha = res.ok ? res.tail.trim().split('\n').pop() : ''
  return /^[a-f0-9]{40}$/.test(sha) ? sha : null
}

export function newRunDir(jobId, label) {
  const id = `${jobId}-${label}-${Date.now().toString(36)}`
  const dir = join(RUNS_DIR, id)
  mkdirSync(join(dir, 'home'), { recursive: true })
  mkdirSync(shortTmpOf(dir), { recursive: true })
  mkdirSync(join(dir, 'assets'), { recursive: true })
  mkdirSync(join(dir, 'logs'), { recursive: true })
  writeFileSync(join(dir, MANIFEST), JSON.stringify({ owner: 'argus', jobId, label, pid: process.pid, createdAt: new Date().toISOString() }))
  return dir
}

// 测试进程只拿到最小环境：没有 GitHub / 模型 / 飞书凭证，HOME 与临时目录都指到 run 目录
// profile 里可用 ${RUN} / ${SRC} 指代本次 run 目录与源码目录
const subst = (value, runDir, vars = {}) => {
  let out = String(value).replaceAll('${RUN}', runDir).replaceAll('${SRC}', join(runDir, 'src')).replaceAll('${CACHE}', CACHE_DIR).replaceAll('${ARGUS_CODE}', CODE_DIR)
  for (const [k, v] of Object.entries(vars)) out = out.replaceAll(`\${${k}}`, v)
  return out
}

export function stepEnv(runDir, profile) {
  const extra = Object.fromEntries(Object.entries(profile.env ?? {}).map(([k, v]) => [k, subst(v, runDir)]))
  const prepend = (profile.pathPrepend ?? []).map((p) => subst(p, runDir))
  return {
    ...baseEnv(),
    // /usr/sbin 里有 ioreg 等系统命令，launchd 默认 PATH 不含它
    PATH: [...prepend, process.env.PATH, '/usr/sbin', '/sbin'].join(':'),
    HOME: join(runDir, 'home'),
    TMPDIR: shortTmpOf(runDir),
    CI: '1',
    npm_config_cache: NPM_CACHE_DIR,
    npm_config_update_notifier: 'false',
    npm_config_fund: 'false',
    npm_config_audit: 'false',
    UV_CACHE_DIR,
    UV_PYTHON_INSTALL_DIR: UV_PYTHON_DIR,
    UV_LINK_MODE: 'clone',
    MIVO_ASSET_STORE_DIR: join(runDir, 'assets'),
    ...(process.env.PLAYWRIGHT_BROWSERS_PATH ? { PLAYWRIGHT_BROWSERS_PATH: process.env.PLAYWRIGHT_BROWSERS_PATH } : {}),
    ...extra,
  }
}

// mode 'worktree'（默认）：裸镜像的链接 worktree；
// mode 'clone'：从镜像 `git clone --shared` 出一个独立仓（对象经 alternates 共享，几乎不占空间）。
// 有的项目测试要求「主 checkout 根目录有 .git」（Mivo 的 bug-doctor intake 测试），链接 worktree 的主仓是裸仓，会误报失败。
export async function checkout(mirror, sha, runDir, { repo, lfs = false, mode = 'worktree' } = {}) {
  const src = join(runDir, 'src')
  // 请求的版本可能已经不在镜像里（合并后 main 被回退、合并分支合并后删掉）：先确认并点名这件事，
  // 否则 git 会把「整个 commit 缺失」说成 clone 模式下的 unable to read tree（像镜像 tree 损坏）或
  // worktree 模式下的 invalid reference，运维看不出该去查「这个版本没挂在任何分支上」
  const present = await run('git', ['-C', mirror, 'cat-file', '-e', `${sha}^{commit}`], { env: baseEnv(), timeoutMs: 60_000 })
  if (!present.ok && !present.budgetExceeded) throw new Error(`镜像里缺这个版本：${sha}（本地镜像 ${mirror} 没有这个 commit，可能合并后 main 被回退或合并分支已删除）`)
  if (mode === 'clone') {
    const c = await run('git', ['clone', '--quiet', '--shared', '--no-checkout', mirror, src], { env: baseEnv(), timeoutMs: 10 * 60_000 })
    if (!c.ok) throw new Error(`克隆镜像失败：${c.tail.slice(-500)}`)
    // origin 指回 GitHub 地址（和 worktree 模式下镜像的 origin 一致；有的脚本按 remote URL 核对仓库身份）
    if (repo) await run('git', ['-C', src, 'remote', 'set-url', 'origin', `https://github.com/${repo}.git`], { env: baseEnv(), timeoutMs: 60_000 })
    const co = await run('git', ['-C', src, '-c', 'advice.detachedHead=false', 'checkout', '--quiet', '--detach', sha], { env: baseEnv(), timeoutMs: 10 * 60_000 })
    if (!co.ok) throw new Error(`检出版本失败：${co.tail.slice(-500)}`)
  } else {
    const res = await run('git', ['-C', mirror, 'worktree', 'add', '--force', '--detach', src, sha], { env: baseEnv(), timeoutMs: 10 * 60_000 })
    if (!res.ok) throw new Error(`检出版本失败：${res.tail.slice(-500)}`)
  }
  if (lfs) {
    const auth = await gitAuth(repo)
    const pull = await run('git', [...auth.args, '-C', src, 'lfs', 'pull', 'origin'], { env: { ...baseEnv(), ...auth.env, GIT_LFS_SKIP_SMUDGE: '0' }, timeoutMs: 20 * 60_000 })
    if (!pull.ok) throw new Error(`拉取大文件（LFS）失败：${pull.tail.slice(-500)}`)
  }
  return src
}

// 文件隔离（Jev sandbox_exec_now 0.99）：测试和依赖安装脚本只能读写本次 run 目录与 Argus 缓存，
// 读不到家目录里的其它东西（gh 登录、凭证、Cindy 数据）和外接卷（真实知识库）。网络暂不限制。
const SANDBOX_EXEC = '/usr/bin/sandbox-exec'
const sbString = (p) => JSON.stringify(p)

export function sandboxProfile(runDir, home = homedir()) {
  const allowed = [runDir, CACHE_DIR, shortTmpOf(runDir)]
  // 访问允许目录时要能读取沿途父目录的元信息
  const parents = new Set()
  for (const dir of allowed) {
    for (let p = dirname(dir); p.startsWith(home) && p.length >= home.length; p = dirname(p)) {
      parents.add(p)
      if (p === home) break
    }
  }
  return [
    '(version 1)',
    '(allow default)',
    `(deny file-read* (subpath ${sbString(home)}))`,
    `(allow file-read* ${allowed.map((d) => `(subpath ${sbString(d)})`).join(' ')} ${[...parents].map((p) => `(literal ${sbString(p)})`).join(' ')})`,
    `(deny file-write* (subpath ${sbString(home)}))`,
    `(allow file-write* ${allowed.map((d) => `(subpath ${sbString(d)})`).join(' ')})`,
    '(deny file-read* (subpath "/Volumes"))',
    '',
  ].join('\n')
}

// Linux：用 bubblewrap（bwrap，非特权 user namespace）做等价的文件隔离。
// 根目录是本次 run 自己造的 tmpfs，宿主 / 的顶层逐项只读挂回（理由见 bwrapArgs）；
// /home、/root、/workspace、/tmp、/mnt、/media 换成空 tmpfs（读不到 gh 登录、
// secrets.env、其它 agent 的数据），再把本次 run 目录、Argus 缓存、短临时目录读写挂回，
// Node 安装目录（nvm 在 ~ 下）和 Playwright 浏览器目录只读挂回。新的 pid/ipc/uts 命名空间，父进程退出即回收。
// 网络和 macOS 版一样暂不限制（npm ci 需要）。
// 没有 bwrap 时拒绝执行；只有显式 ARGUS_ALLOW_UNSANDBOXED=1 才退回「仅最小环境变量 + 独立 HOME/TMPDIR」。
const BWRAP = process.env.ARGUS_BWRAP_BIN ?? '/usr/bin/bwrap'
const NODE_PREFIX = dirname(dirname(process.execPath))
const HIDDEN_ROOTS = ['/home', '/root', '/workspace', '/mnt', '/media', '/srv', '/cursor', '/exec-daemon', '/opt/sand', '/opt/sand-managed']

export function sandboxMode(profile = {}) {
  if (profile.sandbox === false) return 'disabled-by-profile'
  if (process.platform === 'darwin') return existsSync(SANDBOX_EXEC) ? 'sandbox-exec' : 'missing'
  if (process.platform === 'linux') {
    if (process.env.ARGUS_SANDBOX !== 'none' && existsSync(BWRAP)) return 'bwrap'
    return process.env.ARGUS_ALLOW_UNSANDBOXED === '1' ? 'env-only' : 'missing'
  }
  return 'missing'
}

export function bwrapArgs(runDir, { extraRo = [], extraRw = [], chdir = null } = {}) {
  const args = ['--die-with-parent', '--new-session', '--unshare-pid', '--unshare-ipc', '--unshare-uts', '--unshare-cgroup-try']
  // 沙箱里的 / 必须和控制器看到的 / 同一个属主，否则目标仓里「祖先必须由 root 或控制器自己拥有」的断言
  // （mivo run.sh trusted_path、scripts/ops/review-capacity-check/workspace.mjs assertControllerDirectory）在沙箱里恒假：
  // 非特权 bwrap 的 user namespace 只映射控制器自己的 uid，借宿主的 / 时 root 所有的那层就变成溢出 uid 65534，
  // 从第一层开始失败，PR 版本和基线版本一起红，nightly 只能记成「also fails on the base revision」
  // （#1449/#1450/#1454 各 30 条）。宿主 / 对控制器可信时另造根：tmpfs 根归控制器、沙箱 uid 0 就是控制器自己
  // （映射到控制器的真实 uid，拿不到额外权限），宿主 / 的顶层逐项挂回，可见范围不变。
  // 宿主 / 本身不可信（容器把 root 压缩成 nobody）时保持直接挂宿主的 /：沙箱里看到的和控制器看到的仍然一致，
  // 由 nightly 的 checkRootOwnership 记 env_problem，不在这里掩饰。
  const hostRoot = (() => { try { return statSync('/') } catch { return null } })()
  if (hostRoot && [0, process.getuid()].includes(hostRoot.uid) && !(hostRoot.mode & 0o022)) {
    args.push('--unshare-user', '--uid', '0', '--gid', '0', '--tmpfs', '/')
    for (const entry of readdirSync('/')) {
      const p = `/${entry}`
      if (p === '/dev' || p === '/proc' || p === '/tmp' || HIDDEN_ROOTS.includes(p)) continue
      // usrmerge 的顶层链接（/bin、/lib…）原样复制：只读挂载会把链接摊成目录，悬空链接还会让 bwrap 起不来
      if (lstatSync(p).isSymbolicLink()) args.push('--symlink', readlinkSync(p), p)
      else args.push('--ro-bind-try', p, p)
    }
  } else args.push('--ro-bind', '/', '/')
  args.push('--dev', '/dev', '--proc', '/proc', '--tmpfs', '/tmp')
  for (const d of HIDDEN_ROOTS) if (existsSync(d)) args.push('--tmpfs', d)
  const ro = [NODE_PREFIX, ...extraRo].filter((p) => p && existsSync(p))
  for (const p of ro) args.push('--ro-bind', p, p)
  for (const p of [CACHE_DIR, runDir, shortTmpOf(runDir), ...extraRw]) { mkdirSync(p, { recursive: true }); args.push('--bind', p, p) }
  if (chdir) args.push('--chdir', chdir)
  args.push('--remount-ro', '/')
  return args
}

function sandboxed(argv, runDir, profile, stepRo = []) {
  const mode = sandboxMode(profile)
  if (mode === 'disabled-by-profile' || mode === 'env-only') return argv
  if (mode === 'missing') throw new Error(process.platform === 'darwin' ? '本机没有 sandbox-exec，拒绝在无隔离的情况下跑测试' : '本机没有 bubblewrap（bwrap），拒绝在无隔离的情况下跑测试；装 bubblewrap 或显式设置 ARGUS_ALLOW_UNSANDBOXED=1')
  if (mode === 'bwrap') {
    const extraRo = [process.env.PLAYWRIGHT_BROWSERS_PATH, join(CODE_DIR, 'node_modules'), ...(profile.sandboxReadOnly ?? []), ...stepRo]
    // ARGUS_SANDBOX_EXTRA_RW：冒号分隔的额外读写目录（测试夹具用；生产不设）
    const extraRw = (process.env.ARGUS_SANDBOX_EXTRA_RW ?? '').split(':').filter(Boolean)
    return [BWRAP, ...bwrapArgs(runDir, { extraRo, extraRw, chdir: join(runDir, 'src') }), '--', ...argv]
  }
  const sb = join(runDir, 'sandbox.sb')
  if (!existsSync(sb)) writeFileSync(sb, sandboxProfile(runDir))
  return [SANDBOX_EXEC, '-f', sb, ...argv]
}

// stepIds 为空表示全部步骤；setup 步骤总会执行
// 静默检查：要求不打扰桌面的步骤（例如浏览器），运行期间每秒看一次有没有弹出系统密码框
const PROMPT_PROCESS = 'SecurityAgent'
function watchDesktop(enabled) {
  if (!enabled || process.platform !== 'darwin') return { stop: async () => false }
  let seen = false
  let busy = false
  const timer = setInterval(async () => {
    if (seen || busy) return
    busy = true
    const r = await run('/usr/bin/pgrep', ['-x', PROMPT_PROCESS], { env: baseEnv(), timeoutMs: 5000 })
    if (r.ok) seen = true
    busy = false
  }, 1000)
  return { stop: async () => { clearInterval(timer); return seen } }
}

// 被测版本的第一个父提交：有的项目脚本要求显式给出对照基线（例如 Mivo 浏览器旅程的 --base）
async function toParentSha(src) {
  const res = await run('git', ['-C', src, 'rev-parse', 'HEAD^'], { env: baseEnv(), timeoutMs: 60_000 })
  return res.ok ? String(res.tail).trim().split('\n').pop() : ''
}

// 适用条件（appliesIf）：被测版本根本没有这项检查的对象时（例如网页入口 index.html 已下线、扫描脚本还没合入），
// 这一步「不适用」—— 不是没跑成，也不算通过：nightly 把它记进 skipped_steps，不进 checks
export function toNotApplicable(step, src) {
  for (const r of step.appliesIf ?? []) {
    if (!existsSync(join(src, r.file))) return r.reasonZh ?? `not applicable: this revision has no ${r.file}`
  }
  return null
}

// 前提条件：被测版本的脚本还不支持时（例如还没合入对 Ego Lite 的支持），这一步记没跑成，不退回到别的浏览器
function toUnmetRequirement(step, src) {
  for (const r of step.requires ?? []) {
    let text = ''
    try { text = readFileSync(join(src, r.file), 'utf8') } catch { return r.reasonZh ?? `缺少 ${r.file}` }
    if (r.contains && !text.includes(r.contains)) return r.reasonZh ?? `${r.file} 还不支持这项检查`
  }
  return null
}

// 探针和 AI 补测步骤：把 spec 和执行脚本放进测试目录（沙箱读不到 Argus 自己的代码目录）
function prepareTool(step, runDir, vars) {
  const tools = join(runDir, 'tools')
  mkdirSync(tools, { recursive: true })
  if (step.probe) {
    writeFileSync(join(tools, 'probe-run.mjs'), readFileSync(PROBE_SCRIPT))
    if (step.probe.localOidc) writeFileSync(join(tools, 'probe-local-auth-server.mjs'), readFileSync(fileURLToPath(new URL('./tools/probe-local-auth-server.mjs', import.meta.url))))
    writeFileSync(join(runDir, `probe-${step.id}.json`), subst(JSON.stringify(step.probe), runDir, vars))
  }
  if (step.tool) {
    if (!/^[a-z0-9-]+$/.test(step.tool)) throw new Error(`非法工具名：${step.tool}`)
    writeFileSync(join(tools, `${step.tool}.mjs`), readFileSync(fileURLToPath(new URL(`./tools/${step.tool}.mjs`, import.meta.url))))
  }
  if (step.aiTest) {
    writeFileSync(join(tools, 'ai-test-run.mjs'), readFileSync(AI_TEST_SCRIPT))
    // 补测代码原样写入，不做变量替换
    writeFileSync(join(runDir, `${step.id}.json`), JSON.stringify(step.aiTest))
  }
}

function prepareTestReport(step, runDir) {
  validateTestReport(step.testReport)
  if (!step.testReport) return {}
  const file = resolve(subst(step.testReport.path, runDir))
  const rel = relative(resolve(runDir), file)
  if (!rel || rel.startsWith('..') || isAbsolute(rel)) throw new Error('testReport path escapes run directory')
  // 新建文件前核对每层父目录，不能通过符号链接读取旧报告或覆盖别处的文件。
  for (let dir = dirname(file); dir !== resolve(runDir); dir = dirname(dir)) {
    let meta
    try { meta = lstatSync(dir) } catch (error) { if (error.code !== 'ENOENT') throw error }
    if (meta?.isSymbolicLink()) throw new Error('testReport parent is a symlink')
  }
  mkdirSync(dirname(file), { recursive: true })
  if (existsSync(file) && !lstatSync(file).isFile()) throw new Error('testReport is not a regular file')
  rmSync(file, { force: true })
  const vars = { TEST_REPORT: file }
  if (step.testReport.format === 'node-events') {
    const tools = join(runDir, 'tools')
    mkdirSync(tools, { recursive: true })
    vars.NODE_TEST_REPORTER = join(tools, 'node-test-reporter.mjs')
    writeFileSync(vars.NODE_TEST_REPORTER, readFileSync(NODE_TEST_REPORTER))
  }
  return vars
}

function readTestReport(step, res, src, runDir, vars, execution) {
  if (!step.testReport) return { testOutcomeStatus: 'unavailable' }
  try {
    const file = vars.TEST_REPORT
    const meta = lstatSync(file)
    const rel = relative(realpathSync(runDir), realpathSync(file))
    if (!rel || rel.startsWith('..') || isAbsolute(rel)) throw new Error('report escaped run directory')
    if (!meta.isFile() || meta.size > 32 * 1024 * 1024 || res.timedOut || res.exitCode == null) throw new Error('incomplete')
    const outcomes = normalizeTestOutcomes({ format: step.testReport.format, raw: readFileSync(file, 'utf8'),
      sourceRoot: realpathSync(src), runId: basename(runDir), stepId: step.id, checkedOutSha: execution?.checkedOutSha ?? null })
    const reportedFailures = new Set((outcomes.testOutcomes ?? []).filter((t) => t.status === 'failed').map((t) => t.failureName))
    const parsedFailures = new Set(res.failures ?? [])
    if (reportedFailures.size !== parsedFailures.size || [...reportedFailures].some((name) => !parsedFailures.has(name))
      || res.failureParseStatus === 'incomplete' || (res.exitCode !== 0 && !reportedFailures.size)
      || (res.exitCode === 0 && reportedFailures.size)) throw new Error('report/result mismatch')
    return outcomes
  } catch {
    return { testOutcomes: [], testOutcomeStatus: 'incomplete', testOutcomeReason: 'test-report-unavailable-or-inconsistent' }
  }
}

// step.passEnv：只给这一步、从 nightly 自己的环境（secrets.env 经 run-nightly.sh 导出）透传的变量名，
// 例如接口探针的测试账号。值不进命令行、不进 spec 文件、不进日志；没有设置的不传。
export function passEnvFor(step, env = process.env) {
  const out = {}
  for (const k of step.passEnv ?? []) if (/^[A-Z0-9_]+$/.test(k) && env[k]) out[k] = env[k]
  return out
}

// step.binary：步骤需要的宿主程序（例如 gitleaks）。按 env 变量 → 候选路径 → PATH 查找；找到就以 ${var} 代入命令，
// 并只读挂进沙箱（单个文件）；找不到这一步记没跑成。
export function resolveStepBinary(step, env = process.env) {
  const b = step.binary
  if (!b) return { vars: {}, ro: [], unmet: null }
  const fromPath = (name) => (env.PATH ?? '').split(':').filter(Boolean).map((d) => join(d, name)).find((p) => existsSync(p))
  const found = [env[b.env], ...(b.candidates ?? []), ...(b.names ?? []).map(fromPath)].filter(Boolean).find((p) => isAbsolute(p) && existsSync(p))
  if (!found) return { vars: {}, ro: [], unmet: b.reasonZh ?? `本机没有 ${b.names?.[0] ?? b.var}` }
  const real = realpathSync(found)
  return { vars: { [b.var]: real }, ro: [real], unmet: null }
}

function readProbeReport(specPath) {
  try {
    const r = JSON.parse(readFileSync(specPath.replace(/\.json$/, '') + '.report.json', 'utf8'))
    return { status: r.status, checks: Array.isArray(r.checks) ? r.checks : [], skipped: Array.isArray(r.skipped) ? r.skipped : [], auth: r.auth ?? null }
  } catch { return null }
}

// beforeStep(step)：每步开跑前等它返回的 promise（nightly 用来让 afterCoverage 的步骤等覆盖率轮次的单测分片跑完）
export async function runSteps(profile, src, runDir, { stepIds = null, execution = null, beforeStep = null } = {}) {
  const env = stepEnv(runDir, profile)
  const needsBase = profile.steps.some((s) => /\$\{(?:BASE_SHA|DIFF_BASE_SHA)\}/.test(JSON.stringify(s)))
  const parent = needsBase ? await toParentSha(src) : ''
  // BASE_SHA：被测提交的第一个父提交；DIFF_BASE_SHA：本 PR 改动的起点（nightly 传入的 base_sha：merged → 第一个父提交，
  // open → 与目标分支的 merge-base），没有时退回第一个父提交（对照 / 最新主干轮次）
  const vars = needsBase ? { BASE_SHA: parent, DIFF_BASE_SHA: execution?.diffBase || parent } : {}
  const results = []
  for (const step of profile.steps) {
    if (isStopping() || overBudget()) break
    if (stepIds && step.kind !== 'setup' && !stepIds.includes(step.id)) continue
    if (beforeStep) await beforeStep(step)
    const stepVarsForReport = prepareTestReport(step, runDir)
    const stepSubstitutions = { ...vars, ...stepVarsForReport }
    const identity = { ...(execution ?? {}), commandTemplate: [...step.cmd], covers: [...(step.covers ?? [])], aiGenerated: Boolean(step.aiGenerated) }
    const na = toNotApplicable(step, src)
    if (na) {
      results.push({ ...identity, id: step.id, nameZh: step.nameZh, kind: step.kind, cmdText: step.cmd.join(' '), ok: true, notApplicable: true, notApplicableReason: na, unavailable: false, exitCode: null, timedOut: false, durationMs: 0, tail: '', failures: [] })
      log('info', 'step not applicable', { ...execution, step: step.id, reason: na })
      continue
    }
    const bin = resolveStepBinary(step)
    const unmet = toUnmetRequirement(step, src) ?? bin.unmet
    if (unmet) {
      results.push({ ...identity, id: step.id, nameZh: step.nameZh, kind: step.kind, cmdText: step.cmd.join(' '), ok: false, unavailable: true, unavailableReasonZh: unmet, exitCode: null, timedOut: false, durationMs: 0, tail: '', failures: [] })
      log('info', 'step unavailable', { ...execution, step: step.id, reason: unmet })
      continue
    }
    // 相关输入（tree 哈希 + lockfile）和上次通过时完全一样 → 复用那次的结果（reused + 来源），不重跑
    let ck = null
    try { ck = stepCacheEnabled() ? stepCacheKey({ step, label: execution?.runLabel, vars: stepSubstitutions, src }) : null } catch { ck = null }
    const hit = lookupStepResult(ck)
    if (hit) {
      results.push(reusedResult(hit, identity))
      log('info', 'step reused', { ...execution, step: step.id, from: hit.source })
      continue
    }
    if (step.probe || step.aiTest || step.tool) prepareTool(step, runDir, vars)
    const logFile = join(runDir, 'logs', `${step.id}.log`)
    const covDir = coverageDirFor(step, runDir, execution)
    const cmd = step.cmd.map((a) => subst(a, runDir, { ...stepSubstitutions, ...bin.vars }))
    // 覆盖率轮次同时设 NODE_V8_COVERAGE：收 node --test 步骤和测试起的子进程的原始 V8 数据（vitest 主报告里没有的文件用它补）。
    // coverage.kind 'v8'（node --test 等非 vitest 步骤）只收 V8 数据，不加 vitest 参数
    const v8Dir = covDir ? `${covDir}-v8` : null
    if (covDir) {
      rmSync(covDir, { recursive: true, force: true }); rmSync(v8Dir, { recursive: true, force: true })
      mkdirSync(dirname(covDir), { recursive: true }); mkdirSync(v8Dir, { recursive: true })
      if (step.coverage.kind !== 'v8') cmd.push(...vitestCoverageArgs(covDir))
    }
    const argv = sandboxed(cmd, runDir, profile, bin.ro)
    const stepVars = { ...passEnvFor(step), ...Object.fromEntries(Object.entries(step.env ?? {}).map(([k, v]) => [k, subst(v, runDir, vars)])), ...(v8Dir ? { NODE_V8_COVERAGE: v8Dir } : {}) }
    // step.envUnset：这一步不要继承的公共变量（例如单测要求 MIVO_ASSET_STORE_DIR 未设置）
    const stepEnvBase = { ...env, TMPDIR: stepTmpDir(env.TMPDIR ?? tmpdir(), step.id) }
    const stepTmp = stepEnvBase.TMPDIR
    for (const k of step.envUnset ?? []) delete stepEnvBase[k]
    const watch = watchDesktop(step.silentCheck)
    const sp = step.shard ? shardPlan(step) : null
    const res = sp && sp.shards > 1
      ? await runSharded({ step, plan: sp, cmd, covDir, reportFile: stepVarsForReport.TEST_REPORT ?? null, runDir, src, profile, ro: bin.ro, env: { ...stepEnvBase, ...stepVars }, logFile, tmpBase: stepTmp })
      : await run(argv[0], argv.slice(1), { cwd: src, env: { ...stepEnvBase, ...stepVars }, timeoutMs: (step.timeoutMin ?? 20) * 60_000, logFile })
    if (sp && !res.shards) res.shards = { count: 1, reason: sp.reason }
    const disturbed = await watch.stop()
    if (disturbed) log('error', 'step disturbed desktop', { step: step.id })
    const result = { ...identity, id: step.id, nameZh: step.nameZh, kind: step.kind, cmdText: step.cmd.join(' '), ok: res.ok, exitCode: res.exitCode, signal: res.signal ?? null, endedAt: res.endedAt ?? null, timedOut: res.timedOut, durationMs: res.durationMs, tail: res.tail, failures: res.ok ? [] : res.failures, failureDetails: res.ok ? [] : res.failureDetails, failureParseStatus: res.failureParseStatus, ...(res.shards ? { shards: res.shards } : {}) }
    if (step.trialOnly) result.machine = machineLoad()
    toStepOutcome(step, result, runDir)
    Object.assign(result, readTestReport(step, result, src, runDir, stepVarsForReport, execution))
    if (disturbed) result.disturbedDesktop = true
    if (step.probe) { const pr = readProbeReport(join(runDir, `probe-${step.id}.json`)); if (pr) result.probeReport = pr }
    if (covDir) result.coverage = res.timedOut ? { status: 'unavailable', reason: 'unit step timed out' } : withV8Coverage(step.coverage.kind === 'v8' ? null : readCoverageDir(covDir, src), v8Dir, src)
    try { storeStepResult(ck, result, { job: execution?.jobId ?? null, label: execution?.runLabel ?? null, sha: execution?.checkedOutSha ?? execution?.requestedSha ?? null, run_dir: basename(runDir) }) } catch (err) { log('warn', 'step cache store failed', { step: step.id, error: err.message }) }
    results.push(result)
    log('info', 'step done', { ...execution, step: step.id, ok: result.ok, unavailable: Boolean(result.unavailable), exitCode: res.exitCode, ms: res.durationMs })
    if (step.kind === 'setup' && !res.ok) break
  }
  return results
}

// 分片执行（step.shard）：N 个 `--shard=i/N` 进程并行，各写自己的 JSON 报告 / 覆盖率目录 / 日志，跑完合并成
// 和不分片时同名的文件（reports/<step>.json、coverage/<step>/coverage-{final,summary}.json、logs/<step>.log），
// 结果对象与 run() 同形，外加 shards { count, reason, per_shard[] }。
// 分片 TMPDIR 建在这一步自己的 TMPDIR（stepEnvBase.TMPDIR）下：step.env / passEnv 里的 TMPDIR 不能把分片带出步骤隔离目录
export function shardTmpDir(tmpBase, i) {
  const dir = join(tmpBase ?? tmpdir(), `shard-${i}`)
  mkdirSync(dir, { recursive: true })
  return dir
}

export async function runSharded({ step, plan, cmd, covDir, reportFile, runDir, src, profile, ro = [], env, logFile, tmpBase = null }) {
  const n = plan.shards
  const release = reserveShards(n * (plan.per_shard_gb ?? 1.5))
  const t0 = Date.now()
  try {
    const jobs = []
    for (let i = 1; i <= n; i++) {
      const shardReport = reportFile ? reportFile.replace(/\.json$/, '') + `.shard-${i}.json` : null
      const shardCov = covDir ? join(covDir, `shard-${i}`) : null
      let c = cmd.map((a) => (reportFile && a.includes(reportFile) ? a.split(reportFile).join(shardReport) : a))
      if (covDir) c = c.map((a) => (a === `--coverage.reportsDirectory=${covDir}` ? `--coverage.reportsDirectory=${shardCov}` : a))
      c = shardArgs(c, i, n)
      if (shardCov) mkdirSync(shardCov, { recursive: true })
      // 每片自己的 TMPDIR，避免测试用固定临时文件名时互相踩
      const tmp = shardTmpDir(tmpBase ?? env.TMPDIR, i)
      const argv = sandboxed(c, runDir, profile, ro)
      const shardLog = logFile.replace(/\.log$/, '') + `.shard-${i}.log`
      jobs.push(run(argv[0], argv.slice(1), { cwd: src, env: { ...env, TMPDIR: tmp }, timeoutMs: (step.timeoutMin ?? 20) * 60_000, logFile: shardLog })
        .then((r) => ({ i, r, shardReport, shardCov, shardLog })))
    }
    const done = await Promise.all(jobs)
    // 合并 JSON 报告
    if (reportFile) {
      const parts = done.map((d) => readJsonOrNull(d.shardReport))
      if (parts.every(Boolean)) writeFileSync(reportFile, JSON.stringify(mergeVitestJson(parts)))
      else rmSync(reportFile, { force: true }) // 缺一片 → 没有合并报告（判定按 incomplete 处理，不猜）
    }
    // 合并覆盖率
    if (covDir) {
      const finals = done.map((d) => readJsonOrNull(join(d.shardCov, 'coverage-final.json')))
      if (finals.every(Boolean)) {
        const merged = mergeCoverageFinal(finals)
        writeFileSync(join(covDir, 'coverage-final.json'), JSON.stringify(merged))
        writeFileSync(join(covDir, 'coverage-summary.json'), JSON.stringify(coverageSummaryOf(merged)))
      }
      for (const d of done) rmSync(d.shardCov, { recursive: true, force: true })
    }
    // 合并日志（证据里的 logs/<step>.log 仍是一份完整日志）
    const header = `[argus] ${step.id}: ${n} shards in parallel (${plan.reason})\n`
    writeFileSync(logFile, header + done.map((d) => { let t = ''; try { t = readFileSync(d.shardLog, 'utf8') } catch { /* 没有日志 */ } return `\n===== shard ${d.i}/${n} exit=${d.r.exitCode}${d.r.signal ? ` ${d.r.signal}` : ''} ${Math.round(d.r.durationMs / 1000)}s =====\n${t}` }).join(''))
    const rs = done.map((d) => d.r)
    const failed = rs.filter((r) => !r.ok)
    const statuses = rs.map((r) => r.failureParseStatus)
    const parse = failed.length
      ? (failed.every((r) => r.failureParseStatus === 'complete') ? 'complete' : 'incomplete')
      : (statuses.some((x) => x === 'incomplete') ? 'incomplete' : statuses.every((x) => x === 'complete') ? 'complete' : statuses[0])
    const exitCode = failed.length ? (failed.find((r) => r.exitCode != null && r.exitCode !== 0)?.exitCode ?? null) : 0
    return {
      ok: failed.length === 0, exitCode, signal: failed.find((r) => r.signal)?.signal ?? null, endedAt: rs.map((r) => r.endedAt).filter(Boolean).sort().at(-1) ?? null, timedOut: rs.some((r) => r.timedOut), durationMs: Date.now() - t0,
      tail: (failed.length ? failed : rs.slice(-1)).map((r) => r.tail ?? '').join('\n').slice(-20000),
      failures: [...new Set(failed.flatMap((r) => r.failures ?? []))].sort(),
      failureDetails: failed.flatMap((r) => r.failureDetails ?? []),
      failureParseStatus: parse,
      shards: { count: n, reason: plan.reason, mem_available_gb: plan.mem_available_gb ?? null, budget_gb: plan.budget_gb ?? null, per_shard_gb: plan.per_shard_gb ?? null,
        per_shard: done.map((d) => ({ shard: d.i, exit_code: d.r.exitCode, signal: d.r.signal ?? null, ended_at: d.r.endedAt ?? null, timed_out: Boolean(d.r.timedOut), duration_ms: d.r.durationMs })) },
    }
  } finally { release() }
}

// 覆盖率只在专门的覆盖率轮次（label 'cov'，与首轮并行、独立检出）开启：v8 插桩会让少数测试改变行为
// （实测 main 上有 6 个用例只在 --coverage 下失败），所以判定用的首轮 / 重跑 / 基线都不带覆盖率。
// ARGUS_COVERAGE=0 全局关闭。目录必须落在 run 目录内。
// vitest 报告 + 同一步的 V8 原始数据；原始目录读完即删（可能很大）。kind 'v8' 的步骤没有 vitest 报告，只有逐文件数据
export function withV8Coverage(vitest, v8Dir, src) {
  let v8 = {}
  try { v8 = readV8Dir(v8Dir, src) } catch { v8 = {} }
  rmSync(v8Dir, { recursive: true, force: true })
  if (!vitest) return { status: 'ok', kind: 'v8', total: null, files: v8 }
  if (vitest.status !== 'ok' || !vitest.files) return vitest
  return { ...vitest, files: mergeCoverageFiles(vitest.files, v8) }
}

export function coverageDirFor(step, runDir, execution) {
  if (!step.coverage || process.env.ARGUS_COVERAGE === '0') return null
  const labels = step.coverage.labels ?? ['cov']
  if (!labels.includes(execution?.runLabel)) return null
  const dir = resolve(subst(step.coverage.dir ?? `\${RUN}/coverage/${step.id}`, runDir))
  const rel = relative(resolve(runDir), dir)
  if (!rel || rel.startsWith('..') || isAbsolute(rel)) throw new Error('coverage dir escapes run directory')
  return dir
}

// 步骤约定的「没跑成」退出码（例如浏览器或服务起不来）不算失败也不算通过；报告文件里的失败项覆盖从输出认出的用例名
export function toStepOutcome(step, result, runDir) {
  if (!result.ok && !result.timedOut && (step.unavailableExitCodes ?? []).includes(result.exitCode)) {
    const lastLine = String(result.tail ?? '').split('\n').map((l) => l.trim()).filter(Boolean).pop() ?? ''
    Object.assign(result, { unavailable: true, unavailableReasonZh: toPlainZh(lastLine).slice(0, 120) || '检查没能启动', failures: [], failureDetails: [], failureParseStatus: 'incomplete' })
    return result
  }
  if (step.failureReport && !result.ok) {
    Object.assign(result, readFailureReport(step.failureReport, runDir))
  } else if (!result.ok && expectsTestReporter(step) && result.failureParseStatus !== 'complete') {
    result.failureParseStatus = 'incomplete'
  }
  return result
}

function expectsTestReporter(step) {
  if (step.failureReport || step.aiGenerated || step.aiTest) return true
  const argv = step.cmd ?? []
  if (argv.some((arg) => /^(?:--test(?:=|$)|pytest$|vitest$)/.test(arg) || ['pytest', 'vitest', 'run-vitest.mjs'].includes(basename(arg)))) return true
  return ['npm', 'pnpm', 'yarn', 'bun'].includes(basename(argv[0] ?? '')) && argv.slice(1).some((arg) => /^test(?::|$)/.test(arg))
}

function checkFailureType(check) {
  const reason = typeof check.reason === 'string' ? check.reason : ''
  if (check.id === 'browser-host-probe' && reason.startsWith('CDP timeout ')) return 'test_timeout'
  if (check.id === 'browser-product-roundtrip' && reason === 'product persist/export roundtrip did not complete') return 'runner_error'
  if (check.id === 'candidate-snapshot-stable' && reason === 'candidate files changed during layer run') return 'runner_error'
  if (check.id === 'browser-product-roundtrip' && reason.startsWith('product journey failed persisted=')) return 'assertion'
  // Argus 自带的 Playwright 冒烟：明确的断言失败写成 `assert: ...`
  if (check.id.startsWith('pw-') && reason.startsWith('assert:')) return 'assertion'
  return 'unknown'
}

function readFailureReport(spec, runDir) {
  const path = subst(spec.path, runDir)
  const missing = { failures: [], failureDetails: [], failureParseStatus: 'incomplete' }
  try {
    if (spec.format !== 'checks' || statSync(path).size > 1024 * 1024) return missing
    const report = JSON.parse(readFileSync(path, 'utf8'))
    if (!Array.isArray(report.checks)) return missing
    const ids = new Set()
    let incomplete = false
    const failureDetails = []
    for (const check of report.checks) {
      if (!check || typeof check.id !== 'string' || !check.id.trim() || ids.has(check.id) || !['pass', 'fail', 'unavailable'].includes(check.status)) { incomplete = true; continue }
      ids.add(check.id)
      if (check.status === 'unavailable') incomplete = true
      if (check.status !== 'fail') continue
      if (failureDetails.length >= 1000) { incomplete = true; continue }
      const type = checkFailureType(check)
      failureDetails.push({ name: check.id, type })
      if (check.id === 'candidate-snapshot-stable' && type === 'runner_error') incomplete = true
    }
    failureDetails.sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0)
    const aggregate = report.checks.some((c) => c?.status === 'fail') ? 'fail' : report.checks.length && report.checks.every((c) => c?.status === 'pass') ? 'pass' : 'unavailable'
    if (report.status != null && report.status !== aggregate) incomplete = true
    return { failures: failureDetails.map((c) => c.name), failureDetails, failureParseStatus: incomplete || !failureDetails.length ? 'incomplete' : 'complete' }
  } catch { return missing /* 报告缺失或坏掉，不能退回整步确认 */ }
}

export async function cleanupRun(mirror, runDir) {
  const src = join(runDir, 'src')
  if (mirror && existsSync(src)) await run('git', ['-C', mirror, 'worktree', 'remove', '--force', src], { env: baseEnv(), timeoutMs: 5 * 60_000 })
  rmSync(runDir, { recursive: true, force: true })
  rmSync(shortTmpOf(runDir), { recursive: true, force: true })
  if (mirror) await run('git', ['-C', mirror, 'worktree', 'prune'], { env: baseEnv(), timeoutMs: 60_000 })
}

// 一次完整执行：检出 → 跑步骤 → 清理；清理失败只记日志，由 janitor 兜底
export async function executeOnSha({ repo, jobId, label, sha, profile, stepIds, extraRefspecs = [], keepTo = null, diffBase = null, beforeStep = null }) {
  const mirror = await ensureMirror(repo, sha, { extraRefspecs })
  const runDir = newRunDir(jobId, label)
  try {
    if (keepTo) {
      // 清理 run 目录前把日志和检查报告留作证据（不含源码与依赖）
      const src = join(runDir, 'src')
      try { return await runCheckout() } finally { keepEvidence(runDir, join(keepTo, label), src) }
    }
    return await runCheckout()
  } finally {
    try { await cleanupRun(mirror, runDir) } catch (err) { log('error', 'cleanup failed', { runDir, err: err.message }) }
  }
  async function runCheckout() {
    const src = await checkout(mirror, sha, runDir, { repo, lfs: Boolean(profile.lfs), mode: profile.checkoutMode ?? 'worktree' })
    const checkedOutSha = await verifyCheckoutSha(src, sha)
    return await runSteps(profile, src, runDir, { stepIds, beforeStep, execution: { jobId, runLabel: label, requestedSha: sha, checkedOutSha, diffBase } })
  }
}

const KEEP_MAX_BYTES = 20 * 1024 * 1024
function keepEvidence(runDir, dest, src) {
  try {
    mkdirSync(dest, { recursive: true })
    for (const name of readdirSync(runDir)) {
      if (['src', 'home', 'assets', 'tools', MANIFEST].includes(name)) continue
      cpSync(join(runDir, name), join(dest, name), { recursive: true, filter: (from) => { try { const st = lstatSync(from); return !st.isSymbolicLink() && (st.isDirectory() || st.size <= KEEP_MAX_BYTES) } catch { return false } } })
    }
  } catch (err) { log('error', 'keep evidence failed', { runDir, err: err.message }) }
}

// 不把“请求检出某版本”当成“实际测了该版本”：在任何测试执行前验证 worktree HEAD。
export async function verifyCheckoutSha(src, requestedSha) {
  const res = await run('git', ['-C', src, 'rev-parse', 'HEAD'], { env: baseEnv(), timeoutMs: 60_000 })
  const actual = res.ok ? res.tail.trim() : ''
  if (!/^[a-f0-9]{40,64}$/i.test(actual) || actual.toLowerCase() !== String(requestedSha).toLowerCase()) {
    throw new Error(`实际检出版本与请求不一致：请求 ${requestedSha}，实际 ${actual || '无法读取'}`)
  }
  return actual
}

function isAlive(pid) {
  try { process.kill(pid, 0); return true } catch { return false }
}

// janitor：只删带本系统 manifest、且创建它的进程已不在、或本进程已不再使用的 run 目录
export async function janitor({ activeRunDirs = new Set(), maxAgeMs = 2 * 3600_000 } = {}) {
  if (!existsSync(RUNS_DIR)) return { removed: 0, skipped: 0 }
  let removed = 0
  let skipped = 0
  for (const name of readdirSync(RUNS_DIR)) {
    const dir = join(RUNS_DIR, name)
    const manifestPath = join(dir, MANIFEST)
    if (!existsSync(manifestPath)) { skipped++; continue }
    let manifest
    try { manifest = JSON.parse(readFileSync(manifestPath, 'utf8')) } catch { skipped++; continue }
    if (manifest.owner !== 'argus' || activeRunDirs.has(dir)) { skipped++; continue }
    if (Date.now() - statSync(dir).mtimeMs < maxAgeMs && manifest.pid === process.pid) { skipped++; continue }
    if (manifest.pid !== process.pid && isAlive(manifest.pid)) { skipped++; continue }
    rmSync(dir, { recursive: true, force: true })
    rmSync(shortTmpOf(dir), { recursive: true, force: true })
    removed++
  }
  if (existsSync(MIRRORS_DIR)) {
    for (const m of readdirSync(MIRRORS_DIR)) await run('git', ['-C', join(MIRRORS_DIR, m), 'worktree', 'prune'], { env: baseEnv(), timeoutMs: 60_000 })
  }
  return { removed, skipped }
}
