#!/usr/bin/env node
// 黑盒接口探针：真起被测服务 → 等就绪 → 逐条发请求比对 → 关掉整个进程组。零依赖，由 runner 复制进测试目录执行。
// 用法：node probe-run.mjs <spec.json>
// 输出：每条请求一行「✔ 名字」或「✖ 名字」（✖ 下一行写期望与实际），跳过的写「- 名字（skipped: 原因）」；
//       旁边写 <spec>.report.json：{ status, checks:[{id,name,status,reason}], skipped:[{id,name,reason}], auth }
// 退出码 0 全部通过 / 1 有失败 / 2 服务起不来（没跑成）
//
// 请求字段：name, id?, method, path, headers?, body?(JSON) | rawBody?+contentType?, auth?, requires?, capture?, repeat?, sameAs?, expect, expectRepeat?
//  auth: 'none'（默认）| 'host' / 'host-b'（宿主 HMAC 交换：探针为本地服务现生成一次性密钥，签两个测试用户）|
//        'account' / 'account-b'（登录态 Bearer，两个不同用户）| 'bad-bearer'（伪造的 Bearer）
//  登录态来源（按顺序）：① spec.localOidc 且被测仓导出了 setCindyJwksLoader → 本机签发（探针现生成 RSA 密钥，
//  服务端经 tools/probe-local-auth-server.mjs 只拿到公钥），不用任何外部账号；② 外部测试账号（下面的环境变量，只有 account，没有 account-b）；
//  ③ 都没有 → requires:'account' 的请求跳过，reason 'no test account'
//  requires: 'account' —— 没有测试账号时跳过（reason 'no test account'），绝不算通过
//  capture: { var: 'jsonField' } —— 把响应字段存成变量，后续请求用 ${var}
//  repeat: N（幂等）—— 同一请求发 N 次，每次都要满足 expect（第 2 次起用 expectRepeat 覆盖），sameAs 列出各次必须相同的字段
// 测试账号（只经环境变量传入，绝不写进 spec / 日志 / 被测服务的环境）：
//  MIVO_QA_TEST_TOKEN（直接给 Bearer），或 MIVO_QA_TEST_USER + MIVO_QA_TEST_PASSWORD + MIVO_QA_TEST_TOKEN_URL
//  （OAuth password grant；可选 MIVO_QA_TEST_CLIENT_ID / MIVO_QA_TEST_SCOPE）
import { spawn } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:net'
import { createHmac, randomBytes, generateKeyPairSync, createSign } from 'node:crypto'
import { join } from 'node:path'

const READY_POLL_MS = 500
const KILL_GRACE_MS = 2000
const BODY_SHOWN = 300
const LOG_TAIL_LINES = 50
export const ACCOUNT_ENV = ['MIVO_QA_TEST_USER', 'MIVO_QA_TEST_PASSWORD', 'MIVO_QA_TEST_TOKEN', 'MIVO_QA_TEST_TOKEN_URL', 'MIVO_QA_TEST_CLIENT_ID', 'MIVO_QA_TEST_SCOPE']
export const NO_ACCOUNT = 'no test account'

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

const getPath = (obj, path) => String(path).split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj)

// 期望：status 精确相等 / json 逐键精确相等（键可写 a.b）/ error = json.error / nonEmptyArray 指定键是非空数组 /
// types { 键: 'string'|'number'|'boolean'|'array'|'object' } / header { 名: 值 }（值以 ~ 开头表示包含）
export function toMismatch(expect, status, body, headers = null) {
  const problems = []
  if (expect.status != null) {
    const ok = Array.isArray(expect.status) ? expect.status.includes(status) : status === expect.status
    if (!ok) problems.push(`状态码期望 ${Array.isArray(expect.status) ? expect.status.join('/') : expect.status}，实际 ${status}`)
  }
  let json = null
  if (expect.json || expect.nonEmptyArray || expect.error || expect.types) {
    try { json = JSON.parse(body) } catch { problems.push('期望返回 JSON，实际不是') }
  }
  if (json && expect.error && json.error !== expect.error) problems.push(`错误码期望 ${expect.error}，实际 ${JSON.stringify(json.error)}`)
  if (json && expect.json) {
    for (const [k, v] of Object.entries(expect.json)) if (JSON.stringify(getPath(json, k)) !== JSON.stringify(v)) problems.push(`字段 ${k} 期望 ${JSON.stringify(v)}，实际 ${JSON.stringify(getPath(json, k))}`)
  }
  if (json && expect.nonEmptyArray && !(Array.isArray(getPath(json, expect.nonEmptyArray)) && getPath(json, expect.nonEmptyArray).length > 0)) problems.push(`字段 ${expect.nonEmptyArray} 期望是非空数组`)
  if (json && expect.types) {
    for (const [k, t] of Object.entries(expect.types)) {
      const v = getPath(json, k)
      const actual = Array.isArray(v) ? 'array' : v === null ? 'null' : typeof v
      if (actual !== t) problems.push(`字段 ${k} 期望类型 ${t}，实际 ${actual}`)
    }
  }
  if (expect.header) {
    for (const [name, want] of Object.entries(expect.header)) {
      const got = headers?.get?.(name) ?? null
      const ok = String(want).startsWith('~') ? String(got ?? '').includes(String(want).slice(1)) : got === want
      if (!ok) problems.push(`响应头 ${name} 期望 ${want}，实际 ${got}`)
    }
  }
  return problems
}

// ${var} 替换；引用了没捕获到的变量 → 返回 null（该请求依赖的前置请求失败了）
export function fillVars(value, vars) {
  let missing = null
  const text = JSON.stringify(value ?? null).replace(/\$\{([A-Za-z0-9_]+)\}/g, (m, k) => {
    if (vars[k] == null) { missing = k; return m }
    return String(vars[k]).replace(/["\\]/g, (c) => `\\${c}`)
  })
  return missing ? { missing } : { value: JSON.parse(text) }
}

const slug = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60)
export const requestId = (req, i) => req.id ?? (slug(req.name) || `req-${i + 1}`)

// 测试账号：没有 → { status:'missing' }；token 直接给 → ok；用户名密码 + token 地址 → password grant
export async function resolveAccount(env = process.env, fetchImpl = fetch) {
  if (env.MIVO_QA_TEST_TOKEN) return { status: 'ok', via: 'token', token: env.MIVO_QA_TEST_TOKEN }
  if (!env.MIVO_QA_TEST_USER || !env.MIVO_QA_TEST_PASSWORD) return { status: 'missing', reason: NO_ACCOUNT }
  if (!env.MIVO_QA_TEST_TOKEN_URL) return { status: 'missing', reason: `${NO_ACCOUNT} (MIVO_QA_TEST_TOKEN_URL not set, cannot log in)` }
  try {
    const form = new URLSearchParams({ grant_type: 'password', username: env.MIVO_QA_TEST_USER, password: env.MIVO_QA_TEST_PASSWORD })
    if (env.MIVO_QA_TEST_CLIENT_ID) form.set('client_id', env.MIVO_QA_TEST_CLIENT_ID)
    if (env.MIVO_QA_TEST_SCOPE) form.set('scope', env.MIVO_QA_TEST_SCOPE)
    const r = await fetchImpl(env.MIVO_QA_TEST_TOKEN_URL, { method: 'POST', headers: { 'content-type': 'application/x-www-form-urlencoded', accept: 'application/json' }, body: form.toString(), signal: AbortSignal.timeout(20_000) })
    const data = await r.json().catch(() => null)
    const token = data?.access_token ?? data?.id_token ?? null
    if (!r.ok || !token) return { status: 'login-failed', reason: `test account login failed (HTTP ${r.status})` }
    return { status: 'ok', via: 'password-grant', token }
  } catch (err) {
    return { status: 'login-failed', reason: `test account login failed (${String(err.message).slice(0, 80)})` }
  }
}

// 本机签发：一次性 RSA 密钥 + 公钥 JWKS；token 的 iss / aud 取被测服务认可的值
export function createLocalIssuer({ issuer = 'https://auth.cindy.com.cn', audience = 'argus-qa:mivo-canvas', kid = `argus-${randomBytes(4).toString('hex')}` } = {}) {
  const { publicKey, privateKey } = generateKeyPairSync('rsa', { modulusLength: 2048 })
  const jwk = { ...publicKey.export({ format: 'jwk' }), kid, alg: 'RS256', use: 'sig' }
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString('base64url')
  const sign = (sub, { ttlSec = 3600 } = {}) => {
    const now = Math.floor(Date.now() / 1000)
    const input = `${b64({ alg: 'RS256', typ: 'JWT', kid })}.${b64({ iss: issuer, aud: audience, sub, iat: now, nbf: now - 5, exp: now + ttlSec })}`
    const s = createSign('RSA-SHA256'); s.update(input); s.end()
    return `${input}.${s.sign(privateKey).toString('base64url')}`
  }
  return { jwks: { keys: [jwk] }, sign }
}

export const localOidcSupported = (root = process.cwd()) => {
  try { return /export const setCindyJwksLoader\b/.test(readFileSync(join(root, 'server/lib/assetAuth.ts'), 'utf8')) } catch { return false }
}

function authHeaders(kind, ctx) {
  if (!kind || kind === 'none') return {}
  if (kind === 'bad-bearer') return { authorization: 'Bearer aaa.bbb.ccc' }
  if (kind === 'account') return ctx.account?.token ? { authorization: `Bearer ${ctx.account.token}` } : {}
  if (kind === 'account-b') return ctx.account?.tokenB ? { authorization: `Bearer ${ctx.account.tokenB}` } : {}
  if (kind === 'host' || kind === 'host-b') {
    const user = kind === 'host' ? 'argus-qa-a' : 'argus-qa-b'
    return { 'x-cindy-user-id': user, 'x-cindy-exchange': createHmac('sha256', ctx.hostSecret).update(user).digest('hex') }
  }
  throw new Error(`未知 auth：${kind}`)
}

async function send(base, req, ctx) {
  const headers = { ...authHeaders(req.auth, ctx), ...(req.headers ?? {}) }
  let body
  if (req.rawBody != null) { body = String(req.rawBody); if (req.contentType) headers['content-type'] = req.contentType }
  else if (req.body !== undefined) { body = JSON.stringify(req.body); headers['content-type'] = 'application/json' }
  try {
    const r = await fetch(base + req.path, { method: req.method ?? 'GET', headers, body, signal: AbortSignal.timeout(15_000) })
    return { status: r.status, headers: r.headers, body: await r.text() }
  } catch (err) {
    return { status: 0, headers: null, body: `请求失败：${err.message}` }
  }
}

// 跑完全部请求（服务已就绪），返回 checks / skipped。纯逻辑，测试可直接调
export async function runRequests(base, requests, ctx) {
  const vars = { ...(ctx.vars ?? {}) }
  const checks = []
  const skipped = []
  for (const [i, raw] of requests.entries()) {
    const id = requestId(raw, i)
    if ((raw.requires === 'account' && ctx.account?.status !== 'ok') || (raw.requires === 'account-b' && !ctx.account?.tokenB)) {
      skipped.push({ id, name: raw.name, reason: ctx.account?.status === 'ok' ? 'needs a second account (only available with local sign-in)' : (ctx.account?.reason ?? NO_ACCOUNT) })
      continue
    }
    const filled = fillVars({ path: raw.path, body: raw.body, headers: raw.headers, rawBody: raw.rawBody, expect: raw.expect, expectRepeat: raw.expectRepeat }, vars)
    if (filled.missing) { checks.push({ id, name: raw.name, status: 'fail', reason: `前置请求没有拿到 ${filled.missing}` }); continue }
    const req = { ...raw, ...filled.value }
    const times = Math.max(1, Number(raw.repeat ?? 1))
    const responses = []
    const problems = []
    for (let n = 0; n < times; n++) {
      const res = await send(ctx.base ?? base, req, ctx)
      responses.push(res)
      const exp = n > 0 && req.expectRepeat ? { ...req.expect, ...req.expectRepeat } : (req.expect ?? {})
      for (const p of toMismatch(exp, res.status, res.body, res.headers)) problems.push(times > 1 ? `第 ${n + 1} 次：${p}` : p)
    }
    if (times > 1 && raw.sameAs?.length) {
      const parsed = responses.map((r) => { try { return JSON.parse(r.body) } catch { return null } })
      for (const k of raw.sameAs) {
        const vals = parsed.map((p) => JSON.stringify(getPath(p, k)))
        if (new Set(vals).size !== 1) problems.push(`幂等：字段 ${k} 各次不同（${vals.join(' / ')}）`)
      }
    }
    const last = responses[responses.length - 1]
    if (raw.capture && !problems.length) {
      let json = null
      try { json = JSON.parse(responses[0].body) } catch { /* ignore */ }
      for (const [k, path] of Object.entries(raw.capture)) {
        const v = getPath(json, path)
        if (v == null) problems.push(`没拿到要记下的字段 ${path}`)
        else vars[k] = v
      }
    }
    checks.push(problems.length
      ? { id, name: raw.name, status: 'fail', reason: `${problems.join('；')}；响应开头：${String(last.body).slice(0, BODY_SHOWN).replace(/\s+/g, ' ')}` }
      : { id, name: raw.name, status: 'pass' })
  }
  return { checks, skipped }
}

export const reportPathFor = (specPath) => specPath.replace(/\.json$/, '') + '.report.json'

async function main() {
  const specPath = process.argv[2]
  const spec = JSON.parse(readFileSync(specPath, 'utf8'))
  const port = await freePort()
  // 被测服务拿不到测试账号的任何变量
  const env = { ...process.env, ...(spec.env ?? {}), [spec.portEnv]: String(port) }
  for (const k of ACCOUNT_ENV) delete env[k]
  const hostSecret = randomBytes(24).toString('hex')
  if (spec.hostExchangeEnv) env[spec.hostExchangeEnv] = hostSecret
  let account = { status: 'missing', reason: NO_ACCOUNT }
  let start = spec.start
  if (spec.localOidc && localOidcSupported()) {
    const iss = createLocalIssuer(spec.localOidc)
    env.ARGUS_LOCAL_JWKS = JSON.stringify(iss.jwks)
    start = spec.localOidc.start
    account = { status: 'ok', via: 'local-signed (setCindyJwksLoader seam, ephemeral RSA key)', token: iss.sign('argus-qa-a'), tokenB: iss.sign('argus-qa-b') }
  } else if (spec.requests.some((r) => r.requires === 'account' || r.auth === 'account')) {
    account = await resolveAccount()
  }
  const logs = []
  const child = spawn(start[0], start.slice(1), { cwd: process.cwd(), env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] })
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
  const { checks, skipped } = await runRequests(base, spec.requests, { account, hostSecret })
  await stopGroup(child)
  for (const c of checks) {
    console.log(`${c.status === 'pass' ? '✔' : '✖'} ${c.name}`)
    if (c.status !== 'pass') console.log(`  ${c.reason}`)
  }
  for (const s of skipped) console.log(`- ${s.name}（skipped: ${s.reason}）`)
  const status = checks.some((c) => c.status === 'fail') ? 'fail' : 'pass'
  try {
    writeFileSync(reportPathFor(specPath), JSON.stringify({ status, checks, skipped, auth: { account: account.status, account_via: account.via ?? null, host_exchange: Boolean(spec.hostExchangeEnv) } }, null, 2))
  } catch { /* 报告写不了不影响结论 */ }
  process.exit(status === 'fail' ? 1 : 0)
}

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split('/').pop())) main().catch((err) => { console.log(`探针自身出错：${err.message}`); process.exit(2) })
