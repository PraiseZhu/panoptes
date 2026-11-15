// 子进程执行：独立进程组、超时整组回收、完整输出落日志、只回传尾部
import { spawn } from 'node:child_process'
import { createWriteStream, mkdirSync } from 'node:fs'
import { dirname, resolve as resolvePath } from 'node:path'
import { fileURLToPath } from 'node:url'
import { StringDecoder } from 'node:string_decoder'

const TAIL_BYTES = 64 * 1024
const MAX_FAILURES = 1000
const active = new Set()
const signalHandlers = new Map()
let shutdownSignal = null

function installSignalCleanup() {
  for (const signal of ['SIGTERM', 'SIGINT']) {
    // Applications such as nightly already own their shutdown protocol.
    if (process.listenerCount(signal) || signalHandlers.has(signal)) continue
    const handler = () => { shutdownSignal = signal; killAllActive() }
    signalHandlers.set(signal, handler)
    process.once(signal, handler)
  }
}
function releaseSignalCleanup() {
  if (active.size) return
  for (const [signal, handler] of signalHandlers) process.removeListener(signal, handler)
  signalHandlers.clear()
  if (shutdownSignal) setImmediate(() => process.exit(shutdownSignal === 'SIGINT' ? 130 : 143))
}
process.once('exit', () => { for (const stop of active) stop.kill() })

// 从测试输出里认出具体失败用例（vitest / pytest / node:test），用于用例级对比
const ANSI = /\x1b\[[0-9;?]*[A-Za-z]/g
const FAILURE_PATTERNS = [
  /^\s*FAIL\s+(\S+\.(?:test|spec)\.[cm]?[jt]sx?\s+>.+)$/,
  /^FAILED\s+(\S+::\S+)/,
  /^\s*✖\s+(.+)$/,
]

export function toFailureName(line) {
  const clean = line.replace(ANSI, '').replace(/\r$/, '')
  for (const re of FAILURE_PATTERNS) {
    const m = re.exec(clean)
    if (!m) continue
    const name = m[1].replace(/\s+-\s.*$/, '').replace(/\s*\(?\d+(\.\d+)?\s*m?s\)?\s*$/, '').trim()
    return name && !name.endsWith(':') ? name : null
  }
  return null
}

// 类型只从 reporter 的诊断块读取，不从测试标题、源码摘录或普通日志猜测。
function diagnosticType(line) {
  if (/^\s*(?:Error:\s*(?:Test|Hook) timed out in \d+|failureType:\s*['"]testTimeoutFailure['"]|['"]test timed out after \d+(?:\.\d+)?m?s['"]|[Ee]rror:.*test timed out after \d+|[Ee]rror:.*Timeout >\d+(?:\.\d+)?s|(?:E\s+)?Failed: Timeout \(?>[\d.]+s\)? from pytest-timeout)/.test(line)) return 'test_timeout'
  if (/^\s*\[?Error\s*\[(?:ERR_MODULE_NOT_FOUND|MODULE_NOT_FOUND|ERR_UNKNOWN_FILE_EXTENSION|ERR_UNSUPPORTED_DIR_IMPORT)\]:/.test(line)) return 'runner_error'
  if (/^\s*(?:failureType:\s*['"](?:cancelledByParent|hookFailed)['"]|(?:Error: )?(?:Test suite failed to run|Failed to load|Cannot find module|Cannot find package))/.test(line)) return 'runner_error'
  if (/^\s*(?:E\s+)?(?:AssertionError\b|assert\s|(?:\[?Error|AssertionError)\s*\[ERR_ASSERTION\])/.test(line)) return 'assertion'
  if (/^\s*(?:E\s+)?\[?(?:\w*Error)(?:\s*\[[\w_]+\])?:/.test(line) && !/\[ERR_TEST_FAILURE\]/.test(line)) return 'test_error'
  return null
}

export function createFailureParser() {
  const streams = Object.fromEntries(['stdout', 'stderr'].map((id) => [id, { decoder: new StringDecoder('utf8'), partial: '', current: null, dropping: false }]))
  const records = new Map()
  const reporters = new Set()
  const terminals = new Map()
  const nodeCounts = {}
  let vitestFilesFailed = 0
  let incomplete = false
  const rank = { unknown: 0, test_error: 1, assertion: 2, runner_error: 3, test_timeout: 4 }
  const save = (name, type = 'unknown') => {
    if (!records.has(name)) {
      if (records.size >= MAX_FAILURES) { incomplete = true; return }
      records.set(name, { name, type, conflict: false })
      return
    }
    const record = records.get(name)
    if (type === 'unknown' || record.conflict) return
    if (record.type !== 'unknown' && record.type !== type) { record.type = 'unknown'; record.conflict = true }
    else record.type = type
  }
  const flush = (state) => {
    if (state.current) for (const name of state.current.names) save(name, state.current.type)
    state.current = null
  }
  const terminal = (reporter, failed) => {
    reporters.add(reporter)
    if (terminals.has(reporter) && terminals.get(reporter) !== failed) incomplete = true
    terminals.set(reporter, failed)
  }
  const line = (state, raw) => {
    const clean = raw.replace(ANSI, '').replace(/\r$/, '')
    if (/Unhandled (?:Error|Rejection|Exception)|Errors during collection|^ERROR collecting\b/.test(clean)) {
      flush(state); incomplete = true; reporters.add('runner'); return
    }
    const name = toFailureName(clean)
    if (name) {
      const reporter = /^\s*FAIL\s/.test(clean) ? 'vitest' : /^FAILED\s/.test(clean) ? 'pytest' : 'node'
      reporters.add(reporter)
      // vitest 把同一行断言的多个 it.each 用例合成一个诊断块：块内连打 N 个失败头，再给一份共同诊断。
      // 头部之后还没读到诊断正文时，名字并进同一块，块内每个用例都拿到这份诊断的类型。
      if (state.current && state.current.reporter === reporter && state.current.bytes === 0) state.current.names.push(name)
      else {
        flush(state)
        state.current = { names: [name], reporter, type: 'unknown', bytes: 0 }
      }
      save(name)
      if (reporter === 'pytest') {
        const reason = clean.match(/\s+-\s+(.+)$/)?.[1]
        if (reason) state.current.type = diagnosticType(reason) ?? 'unknown'
        flush(state)
      }
      return
    }
    if (/^\s*(?:RUN|RERUN)\s+v\d|^\s*Test Files\s/.test(clean)) reporters.add('vitest')
    const filesFailed = clean.match(/^\s*Test Files\s+(\d+) failed/)
    if (filesFailed) vitestFilesFailed = Number(filesFailed[1])
    if (/^=+\s*(?:test session starts|FAILURES|ERRORS)/.test(clean)) reporters.add('pytest')
    const vitest = clean.match(/^\s*Tests\s+(.+)$/)
    if (vitest) {
      flush(state)
      if (!/\d+ (?:failed|passed|skipped|todo)\b/.test(vitest[1])) incomplete = true
      terminal('vitest', Number(vitest[1].match(/(\d+) failed/)?.[1] ?? 0))
      return
    }
    const pytest = clean.match(/^(?:=+\s*)?((?:\d+ (?:failed|passed|skipped|xfailed|xpassed|deselected|error[s]?)\b[^\n]*))\s+in\s+[\d.]+s(?:\s|=|$)/)
    if (pytest) {
      flush(state)
      if (/\d+ errors?\b/.test(pytest[1])) incomplete = true
      terminal('pytest', Number(pytest[1].match(/(\d+) failed/)?.[1] ?? 0))
      return
    }
    const node = clean.match(/^\s*[ℹ#]\s+(tests|pass|fail|cancelled|skipped|todo)\s+(\d+)\s*$/)
    if (node) {
      flush(state); reporters.add('node'); nodeCounts[node[1]] = Number(node[2]); return
    }
    if (/^\s*[ℹ#]\s+duration_ms\s+[\d.]+/.test(clean)) {
      flush(state)
      if (['tests', 'pass', 'fail', 'cancelled', 'skipped', 'todo'].some((key) => !(key in nodeCounts)) || nodeCounts.tests !== nodeCounts.pass + nodeCounts.fail + nodeCounts.cancelled + nodeCounts.skipped + nodeCounts.todo) incomplete = true
      terminal('node', (nodeCounts.fail ?? 0) + (nodeCounts.cancelled ?? 0))
      return
    }
    if (state.current) {
      state.current.bytes += Buffer.byteLength(clean)
      if (state.current.bytes > TAIL_BYTES) { incomplete = true; flush(state); return }
      // pytest summary has its own inline reason; other reporters delimit diagnostic blocks.
      if (/^\s*(?:✔|✓|❯|⎯{3,}|Test Files\b|Start at\b|Duration\b)/.test(clean)) { flush(state); return }
      const type = diagnosticType(clean)
      if (type && rank[type] > rank[state.current.type]) state.current.type = type
    }
  }
  const consume = (state, text) => {
    const parts = text.split('\n')
    for (let i = 0; i < parts.length; i++) {
      const ended = i < parts.length - 1
      if (!state.dropping) {
        state.partial += parts[i]
        if (Buffer.byteLength(state.partial) > TAIL_BYTES) { incomplete = true; state.partial = ''; state.dropping = true; flush(state) }
        else if (ended) { line(state, state.partial); state.partial = '' }
      }
      if (ended) state.dropping = false
    }
  }
  return {
    push(stream, chunk) {
      const state = streams[stream]
      if (!state) throw new Error(`未知输出流：${stream}`)
      consume(state, state.decoder.write(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)))
    },
    finish() {
      for (const state of Object.values(streams)) {
        consume(state, state.decoder.end())
        if (state.partial) line(state, state.partial)
        state.partial = ''; flush(state)
      }
      const failureDetails = [...records.keys()].sort().map((name) => ({ name, type: records.get(name).type }))
      if (nodeCounts.cancelled > failureDetails.filter((r) => r.type === 'test_timeout').length) incomplete = true
      if (vitestFilesFailed > 0 && terminals.get('vitest') === 0) incomplete = true
      const complete = reporters.size === 1 && terminals.size === 1 && [...terminals.values()][0] === records.size
      return { failures: failureDetails.map((r) => r.name), failureDetails, failureParseStatus: incomplete || (reporters.size && !complete) ? 'incomplete' : complete ? 'complete' : 'not_detected' }
    },
  }
}

let stopping = false
export const isStopping = () => stopping

// 单个 PR 的时间预算（nightly 的 --pr-timeout / --deadline）：设置后每个子进程的超时不超过剩余预算，
// 预算用完后新进程立即以超时返回，runner 不再开始新步骤。null = 不限。
let budgetEnd = null
export function setBudget(endMs) { budgetEnd = Number.isFinite(endMs) ? endMs : null }
export const overBudget = () => budgetEnd != null && Date.now() >= budgetEnd
export const budgetEndMs = () => budgetEnd

export function killAllActive() {
  stopping = true
  const stops = [...active]
  for (const stop of stops) stop()
  // nightly calls process.exit immediately after this synchronous API. Wait here
  // (at most the configured 2s grace), so independent children can finish TERM
  // cleanup before the exit hook force-kills remaining groups. A returned Promise
  // would be ignored by that caller; do not patch process.exit or its handlers.
  const end = Math.max(Date.now(), ...stops.map((stop) => stop.killAtMs ?? 0))
  const remaining = end - Date.now()
  if (remaining > 0) Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, remaining)
  for (const stop of stops) stop.kill()
}

function killGroup(child, signal = 'SIGTERM') {
  try { process.kill(-child.pid, signal) } catch { /* 已退出 */ }
}

export function run(cmd, args, { cwd, env, timeoutMs = Number(process.env.ARGUS_SUBPROCESS_TIMEOUT_MS ?? 600_000), killGraceMs = 1000, logFile, input, signal: abortSignal, inheritOutput = false, testSupervisor = false } = {}) {
  timeoutMs = Math.max(1, Math.floor(Number(timeoutMs)) || 1)
  killGraceMs = Math.min(2000, Math.max(0, Math.floor(Number(killGraceMs)) || 0))
  let budgetCut = false
  if (budgetEnd != null) {
    const left = budgetEnd - Date.now()
    if (left <= 0) return Promise.resolve({ ok: false, status: 'timeout', exitCode: null, signal: null, endedAt: new Date().toISOString(), timedOut: true, budgetExceeded: true, durationMs: 0, tail: 'PR time budget exhausted; not started', failures: [], failureDetails: [], failureParseStatus: 'incomplete' })
    if (left < timeoutMs) { timeoutMs = Math.max(1, Math.floor(left)); budgetCut = true }
  }
  return new Promise((resolve) => {
    const started = Date.now()
    let out = ''
    let timedOut = false
    let testTimedOut = false
    let logStream = null
    if (logFile) {
      mkdirSync(dirname(logFile), { recursive: true })
      logStream = createWriteStream(logFile, { flags: 'a' })
      logStream.write(`$ ${[cmd, ...args].join(' ')}\n`)
    }
    let child
    try {
      child = spawn(cmd, args, { cwd, env, detached: true, stdio: [input == null ? 'ignore' : 'pipe', 'pipe', 'pipe', ...(testSupervisor ? ['ipc'] : [])] })
    } catch (err) {
      logStream?.end()
      resolve({ ok: false, status: 'runner_error', exitCode: null, signal: null, timedOut: false, durationMs: 0, tail: `启动失败：${err.message}`, failures: [], failureDetails: [], failureParseStatus: 'incomplete' })
      return
    }
    let killTimer
    let finished = false
    let spawnError = false
    const terminate = () => {
      if (finished || killTimer) return
      terminate.killAtMs = Date.now() + killGraceMs
      killGroup(child, 'SIGTERM')
      killTimer = setTimeout(() => {
        killGroup(child, 'SIGKILL')
        // Allow exit/close to report the actual signal before the final pipe fallback.
        killTimer = setTimeout(() => {
          child.stdout.destroy(); child.stderr.destroy()
          finish(child.exitCode, '', child.signalCode)
        }, 200)
      }, killGraceMs)
    }
    terminate.kill = () => killGroup(child, 'SIGKILL')
    active.add(terminate)
    installSignalCleanup()
    if (testSupervisor) child.on('message', (message) => {
      if (message?.type !== 'argus:test-timeout') return
      timedOut = true
      testTimedOut = true
      terminate()
    })
    const parser = createFailureParser()
    const onData = (stream, buf) => {
      logStream?.write(buf)
      if (inheritOutput) process[stream].write(buf)
      const text = buf.toString('utf8')
      out += text
      if (out.length > TAIL_BYTES * 2) out = out.slice(out.length - TAIL_BYTES)
      parser.push(stream, buf)
    }
    child.stdout.on('data', (buf) => onData('stdout', buf))
    child.stderr.on('data', (buf) => onData('stderr', buf))
    if (input != null) { child.stdin.on('error', () => {}); child.stdin.end(input) }
    const timer = setTimeout(() => {
      timedOut = true
      terminate()
    }, timeoutMs)
    const finish = (exitCode, extra = '', signal = null) => {
      if (finished) return
      finished = true
      clearTimeout(timer)
      clearTimeout(killTimer)
      abortSignal?.removeEventListener('abort', terminate)
      active.delete(terminate)
      releaseSignalCleanup()
      // 进程组里可能还挂着孙进程（dev server、watcher），统一回收
      killGroup(child, 'SIGKILL')
      const durationMs = Date.now() - started
      const endedAt = new Date().toISOString()
      logStream?.end(`\n[exit ${exitCode}${signal ? ` ${signal}` : ''}${timedOut ? ' timeout' : ''} ${durationMs}ms]\n`)
      const tailText = (out.length > TAIL_BYTES ? out.slice(out.length - TAIL_BYTES) : out) + extra
      const ok = exitCode === 0 && !timedOut && !abortSignal?.aborted
      const parsed = parser.finish()
      const status = timedOut || parsed.failureDetails.some((f) => f.type === 'test_timeout') ? 'timeout'
        : spawnError || signal || abortSignal?.aborted || parsed.failureDetails.some((f) => f.type === 'runner_error') ? 'runner_error' : ok ? 'pass' : 'product_fail'
      resolve({ ok, status, exitCode, signal, endedAt, timedOut, ...(testTimedOut ? { testTimedOut: true } : {}), ...(timedOut && budgetCut ? { budgetExceeded: true } : {}), durationMs, tail: tailText, ...parsed })
    }
    child.on('error', (err) => { spawnError = true; finish(null, `\n进程错误：${err.message}`) })
    child.on('exit', () => killGroup(child, 'SIGKILL'))
    child.on('close', (code, signal) => finish(code, '', signal ?? null))
    abortSignal?.addEventListener('abort', terminate, { once: true })
    if (abortSignal?.aborted) terminate()
  })
}

// Node cancels a timed-out test but can leave its handles alive. Report that event
// immediately over IPC so the suite supervisor can reap the whole process group.
export default async function* testBudgetReporter(source) {
  for await (const event of source) {
    if (event.type === 'test:fail' && event.data?.details?.error?.failureType === 'testTimeoutFailure') {
      process.send?.({ type: 'argus:test-timeout' })
    }
    yield ''
  }
}

// Standalone suite supervisor, also used by run-tests.sh. Arguments remain Node test arguments.
if (process.argv[1] && resolvePath(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const suiteMs = Number(process.env.ARGUS_TEST_SUITE_TIMEOUT_MS ?? 600_000)
  const testMs = Number(process.env.ARGUS_TEST_TIMEOUT_MS ?? 120_000)
  if (![suiteMs, testMs].every((n) => Number.isSafeInteger(n) && n > 0)) throw new Error('test budgets must be positive integer milliseconds')
  for (const sig of ['SIGTERM', 'SIGINT']) process.once(sig, killAllActive)
  const idleFixture = new URL('../tests/bench-env-timeout.test.mjs', import.meta.url).href
  const args = process.argv.slice(2)
  const reporters = args.filter((arg) => /^--test-reporter(?:=|$)/.test(arg)).length
  const destinations = args.filter((arg) => /^--test-reporter-destination(?:=|$)/.test(arg)).length
  const output = reporters === 0 ? ['--test-reporter=spec', '--test-reporter-destination=stdout']
    : reporters === 1 && destinations === 0 ? ['--test-reporter-destination=stdout'] : []
  const result = await run(process.execPath, ['--disable-warning=ExperimentalWarning', '--import', idleFixture, '--test', `--test-timeout=${testMs}`,
    '--test-reporter', fileURLToPath(import.meta.url), '--test-reporter-destination=stdout', ...output, ...args], {
    timeoutMs: suiteMs, inheritOutput: true, testSupervisor: true,
  })
  if (result.status === 'timeout') console.error(JSON.stringify({ status: 'timeout', budget_ms: result.testTimedOut || !result.timedOut ? testMs : suiteMs }))
  process.exitCode = result.status === 'timeout' ? 124 : result.ok ? 0 : result.exitCode || 1
}

// 读 JSON 输出的便捷封装
export async function runJson(cmd, args, opts) {
  const res = await run(cmd, args, opts)
  if (!res.ok) throw new Error(`${cmd} ${args.slice(0, 3).join(' ')} 失败（退出码 ${res.exitCode}${res.timedOut ? '，超时' : ''}）：${res.tail.slice(-800)}`)
  try {
    return JSON.parse(res.tail)
  } catch {
    throw new Error(`${cmd} 输出不是合法 JSON（前 200 字符）：${res.tail.slice(0, 200)}`)
  }
}
