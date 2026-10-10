#!/usr/bin/env node
// 测试用的假 gh：只实现 Argus 发布链路用到的几个 issue 接口，状态存在 FAKE_GH_STATE 指向的 JSON 里。
// state.faults 是按顺序消费的故障：{ match: 'METHOD 路径正则', mode: 'network' | 'lost' }
//   network：请求没到达，直接失败；lost：请求已生效但响应丢了（模拟 POST 结果不明）
import { readFileSync, writeFileSync } from 'node:fs'

const statePath = process.env.FAKE_GH_STATE
const state = JSON.parse(readFileSync(statePath, 'utf8'))
const args = process.argv.slice(2)
let method = 'GET'
let path = null
for (let i = 1; i < args.length; i++) {
  if (args[i] === '-X') { method = args[++i]; continue }
  if (args[i] === '-H' || args[i] === '--input') { i++; continue }
  if (!args[i].startsWith('-')) path = args[i]
}
const input = args.includes('--input') ? readFileSync(0, 'utf8') : null
const save = () => writeFileSync(statePath, JSON.stringify(state, null, 2))
const req = `${method} ${path}`
state.log = [...(state.log ?? []), req]

const faultIdx = (state.faults ?? []).findIndex((f) => new RegExp(f.match).test(req))
const fault = faultIdx >= 0 ? state.faults.splice(faultIdx, 1)[0] : null
if (fault?.mode === 'network') { save(); process.stderr.write('connection refused'); process.exit(1) }

const [pathOnly, query = ''] = path.split('?')
const q = Object.fromEntries(new URLSearchParams(query))
let out
let m
if (method === 'POST' && (m = pathOnly.match(/^repos\/[^/]+\/[^/]+\/issues$/))) {
  const body = JSON.parse(input)
  const number = (state.issues.length ? Math.max(...state.issues.map((i) => i.number)) : 0) + 1
  out = { number, html_url: `https://github.com/x/y/issues/${number}`, state: 'open', state_reason: null, title: body.title, body: body.body, labels: body.labels ?? [], user: { login: 'PraiseZhu' } }
  state.issues.push(out)
} else if (method === 'GET' && pathOnly.match(/^repos\/[^/]+\/[^/]+\/pulls$/)) {
  const page = Number(q.page ?? 1)
  out = (state.pulls ?? []).slice((page - 1) * 100, page * 100)
} else if (method === 'GET' && pathOnly.match(/^repos\/[^/]+\/[^/]+\/issues$/)) {
  const page = Number(q.page ?? 1)
  const pool = q.labels ? state.issues.filter((i) => (i.labels ?? ['pr-qa']).includes(q.labels)) : state.issues
  out = pool.slice((page - 1) * 100, page * 100)
} else if (method === 'GET' && (m = pathOnly.match(/issues\/(\d+)$/))) {
  out = state.issues.find((i) => i.number === Number(m[1]))
} else if (method === 'PATCH' && (m = pathOnly.match(/issues\/(\d+)$/))) {
  out = state.issues.find((i) => i.number === Number(m[1]))
  Object.assign(out, JSON.parse(input), { state_reason: null })
} else if (method === 'POST' && (m = pathOnly.match(/issues\/(\d+)\/comments$/))) {
  const c = { id: Date.now(), body: JSON.parse(input).body, html_url: `https://github.com/x/y/issues/${m[1]}#c` }
  ;(state.comments[m[1]] ??= []).push(c)
  out = c
} else if (method === 'GET' && (m = pathOnly.match(/issues\/(\d+)\/comments$/))) {
  out = state.comments[m[1]] ?? []
} else {
  save(); process.stderr.write(`fake gh 不支持 ${req}`); process.exit(2)
}
save()
if (fault?.mode === 'lost') { process.stderr.write('stream error: connection reset'); process.exit(1) }
process.stdout.write(JSON.stringify(out))
