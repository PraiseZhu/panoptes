// 10-19：约 45% 的 LLM 调用在原地 3 次（2 s/4 s）后仍 UND_ERR_SOCKET / fetch failed。
// 原地 5 次指数退避 + 外层等待重试；等待可注入（这里不真等），且不越过 PR 时间预算。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-netretry-'))
const { fetchRetryingNetwork } = await import('../src/util.mjs')
const { setBudget } = await import('../src/exec.mjs')
const { chat, REVIEW_MAX_MS } = await import('../src/mingqa.mjs')
const { analyzePr, PR_SUMMARY_MAX_MS } = await import('../src/ai.mjs')
const { runAiTests } = await import('../src/nightly.mjs')

const secrets = { ARGUS_LLM_BASE_URL: 'http://gw.invalid/v1', ARGUS_LLM_API_KEY: 'k' }
const netErr = () => Object.assign(new TypeError('fetch failed'), { cause: { code: 'UND_ERR_SOCKET', message: 'other side closed' } })
const reply = (content) => ({ ok: true, status: 200, json: async () => ({ choices: [{ message: { content }, finish_reason: 'stop' }], usage: { total_tokens: 5 } }) })
// 前 n 次断连，之后成功；fail = Infinity 表示一直断
const flaky = (n, content) => { const f = async () => { if (++f.calls <= n) throw netErr(); return reply(content) }; f.calls = 0; return f }
const recorder = () => { const s = async (ms) => { s.waits.push(ms) }; s.waits = []; return s }

test('fetchRetryingNetwork：默认 5 次，退避约 2/4/8/16 s（±25% 抖动），前 4 次断连第 5 次成功', async () => {
  const f = flaky(4, '{}'); const sleep = recorder()
  const resp = await fetchRetryingNetwork(f, 'u', () => ({}), { sleep })
  assert.equal(resp.ok, true); assert.equal(f.calls, 5)
  assert.equal(sleep.waits.length, 4)
  sleep.waits.forEach((ms, i) => { const base = 2000 * 2 ** i; assert.ok(ms >= base * 0.75 && ms <= base * 1.25, `wait ${i}: ${ms}`) })
})

test('fetchRetryingNetwork：一直断连 → 5 次后抛出最后的网络错误', async () => {
  const f = flaky(Infinity); const sleep = recorder()
  await assert.rejects(fetchRetryingNetwork(f, 'u', () => ({}), { sleep }), /fetch failed/)
  assert.equal(f.calls, 5); assert.equal(sleep.waits.length, 4)
})

test('fetchRetryingNetwork：等待不越过 deadline / PR 时间预算，等不起就直接失败', async () => {
  const f = flaky(Infinity); const sleep = recorder()
  await assert.rejects(fetchRetryingNetwork(f, 'u', () => ({}), { sleep, deadline: Date.now() + 1000 }), /fetch failed/)
  assert.equal(f.calls, 1); assert.equal(sleep.waits.length, 0)
  setBudget(Date.now() + 1000)
  try {
    const g = flaky(Infinity)
    await assert.rejects(fetchRetryingNetwork(g, 'u', () => ({}), { sleep }), /fetch failed/)
    assert.equal(g.calls, 1)
  } finally { setBudget(null) }
})

test('代码审查 chat：前 7 次断连（第 1 轮 5 次 + 第 2 轮 2 次）后成功；外层等约 20 s 再试', async () => {
  const f = flaky(7, '{"status":"passed","findings":[]}'); const sleep = recorder()
  const res = await chat({ secrets, messages: [{ role: 'user', content: 'json' }], fetchImpl: f, netRetryDelayMs: 0, sleep })
  assert.equal(res.ok, true); assert.equal(f.calls, 8)
  assert.ok(sleep.waits.includes(20_000))
})

test('代码审查 chat：一直断连 → 3 轮 × 5 次后 ok=false、kind=network，外层等 20 s、40 s', async () => {
  const f = flaky(Infinity); const sleep = recorder()
  const res = await chat({ secrets, messages: [{ role: 'user', content: 'json' }], fetchImpl: f, netRetryDelayMs: 0, sleep })
  assert.equal(res.ok, false); assert.equal(res.error.kind, 'network'); assert.match(res.error.message, /UND_ERR_SOCKET/)
  assert.equal(f.calls, 15)
  assert.deepEqual(sleep.waits.filter((ms) => ms >= 20_000), [20_000, 40_000])
})

test('代码审查 chat：连续超时 2 次就停，不走第 3 轮（第 3 轮只给网络错误），超时后也不等 20 s', async () => {
  let calls = 0; const sleep = recorder()
  const fetchImpl = async () => { calls++; throw Object.assign(new Error('The operation was aborted due to timeout'), { name: 'TimeoutError' }) }
  const res = await chat({ secrets, messages: [{ role: 'user', content: 'json' }], fetchImpl, netRetryDelayMs: 0, sleep })
  assert.equal(res.ok, false); assert.equal(calls, 2)
  assert.deepEqual(sleep.waits, [])
})

// 假时钟：sleep 和每次请求都推进 clock.t；默认上限 / PR 时间预算都按 now() 算，请求本身花的时间也算进去
const fakeClock = (requestMs) => {
  const c = { t: Date.now() }
  c.now = () => c.t
  c.sleep = async (ms) => { c.t += ms }
  c.fetch = async () => { c.fetch.calls++; c.t += requestMs; throw netErr() }
  c.fetch.calls = 0
  return c
}

test('代码审查 chat：只有默认上限 20 分钟——每次请求 5 分钟后断连，第 4 次结束时到 20 分钟就停', async () => {
  assert.equal(REVIEW_MAX_MS, 20 * 60_000)
  const c = fakeClock(5 * 60_000); const t0 = c.t
  const res = await chat({ secrets, messages: [{ role: 'user', content: 'json' }], fetchImpl: c.fetch, netRetryDelayMs: 0, sleep: c.sleep, now: c.now })
  assert.equal(res.ok, false); assert.equal(res.error.kind, 'network')
  assert.equal(c.fetch.calls, 4) // 没有上限时是 15 次
  assert.ok(c.t - t0 <= REVIEW_MAX_MS)
})

test('代码审查 chat：外部 PR 预算（3 分钟）比默认上限紧——按预算停', async () => {
  const c = fakeClock(60_000); const t0 = c.t
  setBudget(t0 + 3 * 60_000)
  try {
    const res = await chat({ secrets, messages: [{ role: 'user', content: 'json' }], fetchImpl: c.fetch, netRetryDelayMs: 0, sleep: c.sleep, now: c.now })
    assert.equal(res.ok, false); assert.equal(res.error.kind, 'network')
    assert.equal(c.fetch.calls, 3); assert.ok(c.t - t0 <= 3 * 60_000)
  } finally { setBudget(null) }
})

test('代码审查 chat：外部 PR 预算（60 分钟）比默认上限松——按 20 分钟停', async () => {
  const c = fakeClock(5 * 60_000); const t0 = c.t
  setBudget(t0 + 60 * 60_000)
  try {
    const res = await chat({ secrets, messages: [{ role: 'user', content: 'json' }], fetchImpl: c.fetch, netRetryDelayMs: 0, sleep: c.sleep, now: c.now })
    assert.equal(res.ok, false); assert.equal(c.fetch.calls, 4); assert.ok(c.t - t0 <= REVIEW_MAX_MS)
  } finally { setBudget(null) }
})

const prArgs = { db: null, secrets, riskClass: 'B', pr: { title: 't', body: '' }, files: [], profile: {}, results: [], outcome: 'ok', retryDelayMs: 0, quota: { check: () => ({ ok: true }), consume: () => {} } }

test('PR 摘要：原地 5 次都断连时走外层重试（等约 20 s），第 2 轮成功', async () => {
  const f = flaky(6, '{"summaryZh":"好"}'); const sleep = recorder()
  const r = await analyzePr({ ...prArgs, fetchImpl: f, sleep })
  assert.equal(r.status, 'ok'); assert.equal(r.summaryZh, '好'); assert.equal(f.calls, 7)
  assert.deepEqual(sleep.waits.filter((ms) => ms >= 20_000), [20_000])
})

test('PR 摘要：一直断连 → 3 轮 × 5 次后 status=error，原因写明网关连不上', async () => {
  const f = flaky(Infinity); const sleep = recorder()
  const r = await analyzePr({ ...prArgs, fetchImpl: f, sleep })
  assert.equal(r.status, 'error'); assert.match(r.reasonZh, /模型网关连不上/); assert.match(r.detail, /fetch failed/)
  assert.equal(f.calls, 15)
  assert.deepEqual(sleep.waits.filter((ms) => ms >= 20_000), [20_000, 40_000])
})

test('PR 摘要：只有默认上限 5 分钟——每次请求 2 分钟后断连，第 3 次结束时超过就停，status=error', async () => {
  assert.equal(PR_SUMMARY_MAX_MS, 5 * 60_000)
  const c = fakeClock(2 * 60_000); const t0 = c.t
  const r = await analyzePr({ ...prArgs, fetchImpl: c.fetch, sleep: c.sleep, now: c.now })
  assert.equal(r.status, 'error'); assert.match(r.reasonZh, /模型网关连不上/)
  assert.equal(c.fetch.calls, 3) // 没有上限时是 15 次
  assert.ok(c.t - t0 <= PR_SUMMARY_MAX_MS + 2 * 60_000) // 最后一次请求在上限内发出
})

test('PR 摘要：外部 PR 预算（90 s）比默认上限紧——按预算停', async () => {
  const c = fakeClock(60_000); const t0 = c.t
  setBudget(t0 + 90_000)
  try {
    const r = await analyzePr({ ...prArgs, fetchImpl: c.fetch, sleep: c.sleep, now: c.now })
    assert.equal(r.status, 'error'); assert.equal(c.fetch.calls, 2)
  } finally { setBudget(null) }
})

test('PR 摘要：外部 PR 预算（60 分钟）比默认上限松——按 5 分钟停', async () => {
  const c = fakeClock(2 * 60_000); const t0 = c.t
  setBudget(t0 + 60 * 60_000)
  try {
    const r = await analyzePr({ ...prArgs, fetchImpl: c.fetch, sleep: c.sleep, now: c.now })
    assert.equal(r.status, 'error'); assert.equal(c.fetch.calls, 3)
  } finally { setBudget(null) }
})

test('PR 摘要：网关 5xx 重试前也看剩余时间，超了就按网关错误返回', async () => {
  const c = fakeClock(0); let calls = 0
  const fetchImpl = async () => { calls++; c.t += 3 * 60_000; return { ok: false, status: 502, json: async () => ({ error: { message: 'bad gateway' } }) } }
  const r = await analyzePr({ ...prArgs, retryDelayMs: 1000, fetchImpl, sleep: c.sleep, now: c.now })
  assert.equal(r.status, 'error'); assert.match(r.reasonZh, /502/)
  assert.equal(calls, 2) // 0→3 min, 3→6 min；第 3 次前已超 5 分钟
})

// S 档（只改测试文件 → maxCalls=1）：模型回空测试列表，不会去执行步骤
const aiArgs = () => ({
  out: { triage: { status: 'ok', applicable: ['boundary'] } }, repo: 'o/r', pr: { title: 't', body: '' }, files: [{ filename: 'tests/a.test.mjs', status: 'modified', additions: 1, deletions: 0 }],
  profile: { aiTests: { frameworkZh: 'node:test', pathGlobs: ['tests/**'], run: 'x' }, steps: [] }, riskClass: 'B', sha: 'abc', common: {}, db: null, secrets,
  health: { llm_errors: [] }, number: 1, results: [],
})

test('夜间 AI 补测（S 档 maxCalls=1）：第 1 轮原地 5 次都断连 → 不计入 maxCalls，等约 30 s 补 1 轮并成功', async () => {
  const f = flaky(5, '{"tests":[]}'); const sleep = recorder(); const a = aiArgs()
  await runAiTests({ ...a, llm: { fetchImpl: f, netRetryDelayMs: 0, sleep } })
  const ai = a.out.ai_tests
  assert.equal(a.out.ai_test_tier.tier, 'S'); assert.equal(ai.caps.max_model_calls, 1)
  assert.equal(f.calls, 6); assert.equal(ai.model_calls, 1)
  assert.deepEqual(ai.rounds.map((r) => r.status), ['error', 'empty'])
  assert.ok(sleep.waits.includes(30_000))
})

test('夜间 AI 补测：一直断连 → 只补 1 轮，两轮都记 error，模型调用不计数，llm_errors 有记录', async () => {
  const f = flaky(Infinity); const sleep = recorder(); const a = aiArgs()
  await runAiTests({ ...a, llm: { fetchImpl: f, netRetryDelayMs: 0, sleep } })
  const ai = a.out.ai_tests
  assert.equal(f.calls, 10); assert.equal(ai.model_calls, 0)
  assert.deepEqual(ai.rounds.map((r) => r.status), ['error', 'error'])
  assert.deepEqual(sleep.waits.filter((ms) => ms === 30_000), [30_000])
  assert.equal(a.health.llm_errors.length, 2); assert.match(a.health.llm_errors[0].message, /fetch failed/)
})
