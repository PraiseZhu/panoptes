// 在隔离 run 目录里检出指定版本并执行项目 profile 的步骤
import { mkdirSync, writeFileSync, rmSync, existsSync, readdirSync, readFileSync, statSync, lstatSync, realpathSync, cpSync } from 'node:fs'
import { join, dirname, basename, resolve, relative, isAbsolute } from 'node:path'
import { homedir, tmpdir } from 'node:os'
import { run, isStopping, overBudget } from './exec.mjs'
import { RUNS_DIR, MIRRORS_DIR, NPM_CACHE_DIR, UV_CACHE_DIR, UV_PYTHON_DIR, CACHE_DIR, CODE_DIR } from './paths.mjs'
import { log, sleep, toPlainZh } from './util.mjs'
import { gitTokenFor } from './github.mjs'
import { fileURLToPath } from 'node:url'
import { validateTestReport } from './config.mjs'
import { normalizeTestOutcomes } from './test-outcomes.mjs'
import { vitestCoverageArgs, readCoverageDir } from './coverage.mjs'

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
const baseEnv = () => ({
  PATH: process.env.PATH,
  HOME: process.env.HOME,
  LANG: 'en_US.UTF-8',
  GIT_TERMINAL_PROMPT: '0',
  // 大文件（LFS）默认不在检出时下载，需要的项目在 profile 里声明 lfs: true 后单独拉取
  GIT_LFS_SKIP_SMUDGE: '1',
})

function mirrorPath(repo) {
  return join(MIRRORS_DIR, `${repo.replace('/', '__')}.git`)
}

// 本地裸镜像：只取分支，不取 refs/pull；每次任务前 fetch。
// 网络抖动时重试 3 次；仍失败但镜像里已有需要的版本就照常测，否则报错交给上层重排队
export async function ensureMirror(repo, neededSha = null, { extraRefspecs = [] } = {}) {
  const dir = mirrorPath(repo)
  const url = `https://github.com/${repo}.git`
  const auth = await gitAuth(repo)
  const env = { ...baseEnv(), ...auth.env }
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
    if (res.ok || isStopping()) break
    log('info', 'mirror fetch retry', { repo, attempt })
    await sleep(attempt * 15_000)
  }
  if (!res.ok) {
    const have = neededSha && (await run('git', ['-C', dir, 'cat-file', '-e', `${neededSha}^{commit}`], { env: baseEnv(), timeoutMs: 30_000 })).ok
    if (!have) throw new Error(`更新代码仓失败：${res.tail.slice(-500)}`)
    log('info', 'mirror fetch failed, using cached commit', { repo })
  }
  return dir
}

// macOS 的 unix socket 路径上限约 104 字符，run 目录太深会让测试里的本地 socket 起不来；
// 临时目录改放短路径 /private/tmp/argus-<id>，随 run 一起清理
const SHORT_TMP_ROOT = process.platform === 'darwin' ? '/private/tmp' : tmpdir()
const shortTmpOf = (runDir) => join(SHORT_TMP_ROOT, `argus-${runDir.split('/').pop()}`)

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

function stepEnv(runDir, profile) {
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
// 根文件系统只读挂载；/home、/root、/workspace、/tmp、/mnt、/media 换成空 tmpfs（读不到 gh 登录、
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
  const args = ['--die-with-parent', '--new-session', '--unshare-pid', '--unshare-ipc', '--unshare-uts', '--unshare-cgroup-try',
    '--ro-bind', '/', '/', '--dev', '/dev', '--proc', '/proc', '--tmpfs', '/tmp']
  for (const d of HIDDEN_ROOTS) if (existsSync(d)) args.push('--tmpfs', d)
  const ro = [NODE_PREFIX, ...extraRo].filter((p) => p && existsSync(p))
  for (const p of ro) args.push('--ro-bind', p, p)
  for (const p of [CACHE_DIR, runDir, shortTmpOf(runDir), ...extraRw]) { mkdirSync(p, { recursive: true }); args.push('--bind', p, p) }
  if (chdir) args.push('--chdir', chdir)
  return args
}

function sandboxed(argv, runDir, profile) {
  const mode = sandboxMode(profile)
  if (mode === 'disabled-by-profile' || mode === 'env-only') return argv
  if (mode === 'missing') throw new Error(process.platform === 'darwin' ? '本机没有 sandbox-exec，拒绝在无隔离的情况下跑测试' : '本机没有 bubblewrap（bwrap），拒绝在无隔离的情况下跑测试；装 bubblewrap 或显式设置 ARGUS_ALLOW_UNSANDBOXED=1')
  if (mode === 'bwrap') {
    const extraRo = [process.env.PLAYWRIGHT_BROWSERS_PATH, join(CODE_DIR, 'node_modules'), ...(profile.sandboxReadOnly ?? [])]
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

export async function runSteps(profile, src, runDir, { stepIds = null, execution = null } = {}) {
  const env = stepEnv(runDir, profile)
  const needsBase = profile.steps.some((s) => JSON.stringify(s).includes('${BASE_SHA}'))
  const vars = needsBase ? { BASE_SHA: await toParentSha(src) } : {}
  const results = []
  for (const step of profile.steps) {
    if (isStopping() || overBudget()) break
    if (stepIds && step.kind !== 'setup' && !stepIds.includes(step.id)) continue
    const stepVarsForReport = prepareTestReport(step, runDir)
    const stepSubstitutions = { ...vars, ...stepVarsForReport }
    const identity = { ...(execution ?? {}), commandTemplate: [...step.cmd], covers: [...(step.covers ?? [])], aiGenerated: Boolean(step.aiGenerated) }
    const unmet = toUnmetRequirement(step, src)
    if (unmet) {
      results.push({ ...identity, id: step.id, nameZh: step.nameZh, kind: step.kind, cmdText: step.cmd.join(' '), ok: false, unavailable: true, unavailableReasonZh: unmet, exitCode: null, timedOut: false, durationMs: 0, tail: '', failures: [] })
      log('info', 'step unavailable', { ...execution, step: step.id, reason: unmet })
      continue
    }
    if (step.probe || step.aiTest || step.tool) prepareTool(step, runDir, vars)
    const logFile = join(runDir, 'logs', `${step.id}.log`)
    const covDir = coverageDirFor(step, runDir, execution)
    const cmd = step.cmd.map((a) => subst(a, runDir, stepSubstitutions))
    if (covDir) { rmSync(covDir, { recursive: true, force: true }); mkdirSync(dirname(covDir), { recursive: true }); cmd.push(...vitestCoverageArgs(covDir)) }
    const argv = sandboxed(cmd, runDir, profile)
    const stepVars = Object.fromEntries(Object.entries(step.env ?? {}).map(([k, v]) => [k, subst(v, runDir, vars)]))
    // step.envUnset：这一步不要继承的公共变量（例如单测要求 MIVO_ASSET_STORE_DIR 未设置）
    const stepEnvBase = { ...env }
    for (const k of step.envUnset ?? []) delete stepEnvBase[k]
    const watch = watchDesktop(step.silentCheck)
    const res = await run(argv[0], argv.slice(1), { cwd: src, env: { ...stepEnvBase, ...stepVars }, timeoutMs: (step.timeoutMin ?? 20) * 60_000, logFile })
    const disturbed = await watch.stop()
    if (disturbed) log('error', 'step disturbed desktop', { step: step.id })
    const result = { ...identity, id: step.id, nameZh: step.nameZh, kind: step.kind, cmdText: step.cmd.join(' '), ok: res.ok, exitCode: res.exitCode, timedOut: res.timedOut, durationMs: res.durationMs, tail: res.tail, failures: res.ok ? [] : res.failures, failureDetails: res.ok ? [] : res.failureDetails, failureParseStatus: res.failureParseStatus }
    toStepOutcome(step, result, runDir)
    Object.assign(result, readTestReport(step, result, src, runDir, stepVarsForReport, execution))
    if (disturbed) result.disturbedDesktop = true
    if (covDir) result.coverage = res.timedOut ? { status: 'unavailable', reason: 'unit step timed out' } : readCoverageDir(covDir, src)
    results.push(result)
    log('info', 'step done', { ...execution, step: step.id, ok: result.ok, unavailable: Boolean(result.unavailable), exitCode: res.exitCode, ms: res.durationMs })
    if (step.kind === 'setup' && !res.ok) break
  }
  return results
}

// 覆盖率只在被测版本的首轮（h1 / 安装失败重试的 h1r）开启；重跑、基线、最新主干不需要，也不该变慢。
// ARGUS_COVERAGE=0 全局关闭。目录必须落在 run 目录内。
export function coverageDirFor(step, runDir, execution) {
  if (!step.coverage || process.env.ARGUS_COVERAGE === '0') return null
  const labels = step.coverage.labels ?? ['h1', 'h1r']
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
export async function executeOnSha({ repo, jobId, label, sha, profile, stepIds, extraRefspecs = [], keepTo = null }) {
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
    return await runSteps(profile, src, runDir, { stepIds, execution: { jobId, runLabel: label, requestedSha: sha, checkedOutSha } })
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
