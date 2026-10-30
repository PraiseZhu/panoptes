// 子进程执行：独立进程组、超时整组回收、完整输出落日志、只回传尾部
import { spawn } from 'node:child_process'
import { createWriteStream, mkdirSync } from 'node:fs'
import { dirname } from 'node:path'
import { StringDecoder } from 'node:string_decoder'

const TAIL_BYTES = 64 * 1024
const MAX_FAILURES = 1000
const active = new Set()

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
    if (state.current) for (const name of [state.current.name, ...state.current.shared]) save(name, state.current.type)
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
      // vitest 把同组失败用例连着列成多行 FAIL，随后只打一段共享诊断：还没读到任何诊断的上一条并入同一组，
      // 共享诊断的类型同时记给组内每条（否则前面的会被当成 unknown 存下）。
      const shared = state.current?.bytes === 0 ? [state.current.name, ...state.current.shared] : []
      flush(state)
      const reporter = /^\s*FAIL\s/.test(clean) ? 'vitest' : /^FAILED\s/.test(clean) ? 'pytest' : 'node'
      reporters.add(reporter)
      save(name)
      state.current = { name, type: 'unknown', bytes: 0, shared }
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
  for (const child of active) killGroup(child)
}

function killGroup(child, signal = 'SIGTERM') {
  try { process.kill(-child.pid, signal) } catch { /* 已退出 */ }
}

export function run(cmd, args, { cwd, env, timeoutMs = 10 * 60_000, logFile, input } = {}) {
  let budgetCut = false
  if (budgetEnd != null) {
    const left = budgetEnd - Date.now()
    if (left <= 0) return Promise.resolve({ ok: false, exitCode: null, timedOut: true, budgetExceeded: true, durationMs: 0, tail: 'PR time budget exhausted; not started', failures: [], failureDetails: [], failureParseStatus: 'incomplete' })
    if (left < timeoutMs) { timeoutMs = Math.max(1, Math.floor(left)); budgetCut = true }
  }
  return new Promise((resolve) => {
    const started = Date.now()
    let out = ''
    let timedOut = false
    let logStream = null
    if (logFile) {
      mkdirSync(dirname(logFile), { recursive: true })
      logStream = createWriteStream(logFile, { flags: 'a' })
      logStream.write(`$ ${[cmd, ...args].join(' ')}\n`)
    }
    let child
    try {
      child = spawn(cmd, args, { cwd, env, detached: true, stdio: [input == null ? 'ignore' : 'pipe', 'pipe', 'pipe'] })
    } catch (err) {
      resolve({ ok: false, exitCode: null, timedOut: false, durationMs: 0, tail: `启动失败：${err.message}` })
      return
    }
    active.add(child)
    const parser = createFailureParser()
    const onData = (stream, buf) => {
      logStream?.write(buf)
      const text = buf.toString('utf8')
      out += text
      if (out.length > TAIL_BYTES * 2) out = out.slice(out.length - TAIL_BYTES)
      parser.push(stream, buf)
    }
    child.stdout.on('data', (buf) => onData('stdout', buf))
    child.stderr.on('data', (buf) => onData('stderr', buf))
    if (input != null) child.stdin.end(input)
    timeoutMs = Math.max(1, Math.floor(Number(timeoutMs)) || 1)
    const timer = setTimeout(() => {
      timedOut = true
      killGroup(child, 'SIGTERM')
      setTimeout(() => killGroup(child, 'SIGKILL'), 10_000).unref()
    }, timeoutMs)
    let finished = false
    const finish = (exitCode, extra = '', signal = null) => {
      if (finished) return
      finished = true
      clearTimeout(timer)
      active.delete(child)
      // 进程组里可能还挂着孙进程（dev server、watcher），统一回收
      killGroup(child, 'SIGKILL')
      const durationMs = Date.now() - started
      const endedAt = new Date().toISOString()
      logStream?.end(`\n[exit ${exitCode}${signal ? ` ${signal}` : ''}${timedOut ? ' timeout' : ''} ${durationMs}ms]\n`)
      const tailText = (out.length > TAIL_BYTES ? out.slice(out.length - TAIL_BYTES) : out) + extra
      resolve({ ok: exitCode === 0 && !timedOut, exitCode, signal, endedAt, timedOut, ...(timedOut && budgetCut ? { budgetExceeded: true } : {}), durationMs, tail: tailText, ...parser.finish() })
    }
    child.on('error', (err) => finish(null, `\n进程错误：${err.message}`))
    child.on('close', (code, signal) => finish(code, '', signal ?? null))
  })
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
