#!/usr/bin/env node
// 黑盒接口探针：真起被测服务 → 等就绪 → 逐条发请求比对 → 关掉整个进程组。零依赖，由 runner 复制进测试目录执行。
// 用法：node probe-run.mjs <spec.json>
// 输出：每条请求一行「✔ 名字」或「✖ 名字」（✖ 下一行写期望与实际）；退出码 0 全部通过 / 1 有失败 / 2 服务起不来（没跑成）
import { spawn } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { createServer } from 'node:net'

const READY_POLL_MS = 500
const KILL_GRACE_MS = 2000
const BODY_SHOWN = 300
const LOG_TAIL_LINES = 50

const freePort = () => new Promise((resolve, reject) => {
  const srv = createServer()
  srv.unref()
  srv.on('error', reject)
  srv.listen(0, '127.0.0.1', () => { const { port } = srv.address(); srv.close(() => resolve(port)) })
})

function stopGroup(child) {
  return new Promise((resolve) => {
    if (child.exitCode != null || child.signalCode != null) return resolve()
    child.once('exit', () => resolve())
    try { process.kill(-child.pid, 'SIGTERM') } catch { /* 已退出 */ }
    setTimeout(() => { try { process.kill(-child.pid, 'SIGKILL') } catch { /* 已退出 */ } resolve() }, KILL_GRACE_MS).unref()
  })
}

// 期望只支持三种：status 精确相等 / json 逐键精确相等 / nonEmptyArray 指定键是非空数组
export function toMismatch(expect, status, body) {
  const problems = []
  if (expect.status != null && status !== expect.status) problems.push(`状态码期望 ${expect.status}，实际 ${status}`)
  let json = null
  if (expect.json || expect.nonEmptyArray) {
    try { json = JSON.parse(body) } catch { problems.push('期望返回 JSON，实际不是') }
  }
  if (json && expect.json) {
    for (const [k, v] of Object.entries(expect.json)) if (JSON.stringify(json[k]) !== JSON.stringify(v)) problems.push(`字段 ${k} 期望 ${JSON.stringify(v)}，实际 ${JSON.stringify(json[k])}`)
  }
  if (json && expect.nonEmptyArray && !(Array.isArray(json[expect.nonEmptyArray]) && json[expect.nonEmptyArray].length > 0)) problems.push(`字段 ${expect.nonEmptyArray} 期望是非空数组`)
  return problems
}

async function main() {
  const spec = JSON.parse(readFileSync(process.argv[2], 'utf8'))
  const port = await freePort()
  const env = { ...process.env, ...(spec.env ?? {}), [spec.portEnv]: String(port) }
  const logs = []
  const child = spawn(spec.start[0], spec.start.slice(1), { cwd: process.cwd(), env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] })
  for (const s of [child.stdout, child.stderr]) s.on('data', (b) => { logs.push(...String(b).split('\n')); if (logs.length > 500) logs.splice(0, logs.length - 500) })
  const base = `http://127.0.0.1:${port}`
  const deadline = Date.now() + (spec.ready?.timeoutSec ?? 30) * 1000
  let ready = false
  while (Date.now() < deadline && child.exitCode == null) {
    try {
      const r = await fetch(base + (spec.ready?.path ?? '/'), { signal: AbortSignal.timeout(2000) })
      if (r.status >= 200 && r.status < 300) { ready = true; break }
    } catch { /* 还没起来 */ }
    await new Promise((r) => setTimeout(r, READY_POLL_MS))
  }
  if (!ready) {
    console.log(logs.filter(Boolean).slice(-LOG_TAIL_LINES).join('\n'))
    console.log(child.exitCode != null ? `服务启动后退出了（退出码 ${child.exitCode}）` : '服务在规定时间内没有就绪')
    await stopGroup(child)
    process.exit(2)
  }
  let failed = 0
  for (const req of spec.requests) {
    let status = 0
    let body = ''
    try {
      const r = await fetch(base + req.path, {
        method: req.method ?? 'GET',
        headers: req.body ? { 'content-type': 'application/json' } : {},
        body: req.body ? JSON.stringify(req.body) : undefined,
        signal: AbortSignal.timeout(15_000),
      })
      status = r.status
      body = await r.text()
    } catch (err) {
      body = `请求失败：${err.message}`
    }
    const problems = toMismatch(req.expect ?? {}, status, body)
    if (problems.length === 0) { console.log(`✔ ${req.name}`); continue }
    failed++
    console.log(`✖ ${req.name}`)
    console.log(`  ${problems.join('；')}；响应开头：${body.slice(0, BODY_SHOWN).replace(/\s+/g, ' ')}`)
  }
  await stopGroup(child)
  process.exit(failed ? 1 : 0)
}

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split('/').pop())) main().catch((err) => { console.log(`探针自身出错：${err.message}`); process.exit(2) })
