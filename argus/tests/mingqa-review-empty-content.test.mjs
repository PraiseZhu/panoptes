// 10-29 PR #1340：代码审查这条路连续两次（合并复核 10-28 23:11、catch-up 10-29 09:53）都只记下一句
//   bad_json: "model returned non-JSON or no usable object (0 chars)"
// 同一句把三种不同故障压成一件事：模型回了空内容（常见于推理把 max_completion_tokens=16000 用完）、
// 回包被截断、网关 200 但回包根本不是 JSON。报告里既没有 finish_reason 也无法判断该找网关还是找模型；
// 而且紧接着的重试和上一次逐个字节一样（同一份输入、同样的 token 上限、temperature 0 + 固定 seed），
// 注定以同样方式失败。AI 补测那条路（aitests.mjs parseModelJson，#966 2026-10-14）早就改成
// kind=empty/truncated + finish_reason 了，代码审查（mingqa.chat + pickReviewJson）一直没跟上。
// 这里钉住代码审查应当怎么报、以及「不可能成功的重试」不该再发。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

process.env.ARGUS_ROOT = mkdtempSync(join(tmpdir(), 'argus-review-empty-'))
const { chat, pickReviewJson } = await import('../src/mingqa.mjs')

const secrets = { ARGUS_LLM_BASE_URL: 'http://gw.invalid/v1', ARGUS_LLM_API_KEY: 'k' }
// json_object 请求要求非 system 消息里出现 json（util.jsonObjectBody），和 codeReview 的真实调用一致
const messages = [
  { role: 'system', content: 'You are the independent code reviewer. Return strict JSON only.' },
  { role: 'user', content: 'Diff: … \nRespond with a single json object in the format given by the system prompt.' },
]
// 走 codeReview 的调用方式（select: pickReviewJson）
const callReview = (fetchImpl, extra = {}) => chat({ secrets, messages, select: pickReviewJson, fetchImpl, netRetryDelayMs: 0, sleep: async () => {}, ...extra })
const reply = (body) => async () => ({ ok: true, status: 200, json: async () => body })

test('code-review chat：模型回空内容 → kind 标明是空内容，并保留 finish_reason', async () => {
  const res = await callReview(reply({ choices: [{ finish_reason: 'length', message: { content: '' } }], usage: { total_tokens: 16000 } }))
  assert.equal(res.ok, false)
  // 今天是 kind=bad_json（和「模型回了垃圾 JSON」混在一起）。空内容要能单独认出来
  assert.match(String(res.error.kind), /empty/i, `空内容回包的 kind 要能区分出来（现在 ${res.error.kind}）`)
  // finish_reason 是唯一能说明「是 token 用完了还是被拦了」的字段，今天被丢掉了
  assert.equal(res.error.finish_reason, 'length')
  assert.match(String(res.error.message), /finish_reason=length/)
  assert.match(String(res.error.message), /empty content|空内容/i, '错误里要说清是空内容，今天的 "(0 chars)" 看不出这一点')
})

test('code-review chat：finish_reason=length 的空内容重试不能是同一个请求（同 token 上限注定再失败）', async () => {
  const bodies = []
  const fetchImpl = async (_url, init) => { bodies.push(String(init.body)); return { ok: true, status: 200, json: async () => ({ choices: [{ finish_reason: 'length', message: { content: '' } }], usage: { total_tokens: 16000 } }) } }
  const res = await callReview(fetchImpl, { attempts: 2, maxMs: 60_000 })
  assert.equal(res.ok, false)
  // 要么不再盲目重试，要么重试换了参数（例如提高 max_completion_tokens / 降推理强度）
  if (bodies.length > 1) {
    assert.equal(new Set(bodies).size, bodies.length, `第 2 次请求和第 1 次一模一样（${bodies.length} 次），同样的输入加同样的 token 上限只会再失败一次`)
  }
})

test('code-review chat：网关 200 但回包不是 JSON → 说明是回包读不出来，不能报成「模型回了非 JSON」', async () => {
  const fetchImpl = async () => ({ ok: true, status: 200, json: async () => { throw new SyntaxError('Unexpected end of JSON input') } })
  const res = await callReview(fetchImpl)
  assert.equal(res.ok, false)
  // 今天是 "model returned non-JSON or no usable object (0 chars)"：模型可能根本没被调到，责任方记错了
  assert.match(String(res.error.message), /response|body|gateway|网关|回包/i, `错误里要指向网关回包本身（现在 "${res.error.message}"）`)
})
