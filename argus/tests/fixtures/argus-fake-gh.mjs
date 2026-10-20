#!/usr/bin/env node
// 端到端边界：只访问本测试的 JSON，不接触 GitHub。任何未声明调用直接失败。
import { readFileSync, writeFileSync, existsSync } from 'node:fs'
import { join } from 'node:path'

const file = process.env.ARGUS_E2E_GH_STATE
const state = JSON.parse(readFileSync(file, 'utf8'))
const args = process.argv.slice(2)
const route = args.find((a) => a.startsWith('repos/'))
state.calls.push(route)
writeFileSync(file, JSON.stringify(state))
if (!route || args.includes('-X')) throw new Error('E2E 禁止未声明的 GitHub 写请求')
let out
if (/\/pulls\/\d+\/files\?/.test(route)) out = state.files ?? [{ filename: 'value.mjs', additions: 1, deletions: 1, patch: '+value' }]
else if (/\/pulls\/\d+$/.test(route)) out = state.pr
else if (/\/contents\//.test(route)) {
  // state.contents：{ 'docs/pr/N/review.md': GitHub contents 接口的返回 }；没有 → 404
  const c = state.contents?.[route.replace(/^repos\/[^/]+\/[^/]+\/contents\//, '').split('?')[0]]
  if (!c) { process.stderr.write('gh: Not Found (HTTP 404)'); process.exit(1) }
  out = c
}
else if (/\/commits\//.test(route)) out = { parents: [{ sha: state.baseSha }], commit: { message: `fixture (#${state.pr.number})` } }
else if (/\/branches\//.test(route) || /\/git\/ref\//.test(route)) out = { commit: { sha: state.pr.merge_commit_sha }, object: { sha: state.pr.merge_commit_sha } }
else if (/\/issues\?/.test(route)) {
  if (state.failIssues) {
    if (!existsSync(join(process.env.ARGUS_ROOT, 'app/evidence', `job-${state.jobId}.json`))) throw new Error('外部请求发生前没有保存证据')
    process.stderr.write('HTTP 500 fixture issue lookup unavailable')
    process.exit(1)
  }
  out = []
} else throw new Error(`E2E 未声明路由：${route}`)
process.stdout.write(JSON.stringify(out))
