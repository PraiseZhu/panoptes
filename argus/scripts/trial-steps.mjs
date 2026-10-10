#!/usr/bin/env node
// 在 Mini 上用真实版本试跑指定步骤：node scripts/trial-steps.mjs <profile名> <sha> [--only a,b]
// 走和正式任务一样的检出、沙箱、清理；不入账、不调 AI、不发 issue、不发飞书。setup 步骤总会执行。
import { loadProfile, loadRegistry } from '../src/config.mjs'
import { executeOnSha } from '../src/runner.mjs'

const [name, sha, ...rest] = process.argv.slice(2)
if (!name || !sha) { console.error('用法：node scripts/trial-steps.mjs <profile名> <sha> [--only a,b]'); process.exit(2) }
const only = rest.includes('--only') ? rest[rest.indexOf('--only') + 1].split(',') : null
const reg = loadRegistry()
const project = reg.projects.find((p) => p.profile === name)
if (!project) { console.error(`registry 里没有 profile=${name} 的项目`); process.exit(2) }
const profile = loadProfile(name)
const t0 = Date.now()
const results = await executeOnSha({ repo: project.repo, jobId: 0, label: `trial-${Date.now()}`, sha, profile, stepIds: only })
for (const r of results) {
  const status = r.unavailable ? 'unavailable' : r.ok ? 'pass' : 'fail'
  console.log(JSON.stringify({ id: r.id, status, durationSec: Math.round(r.durationMs / 1000), failures: (r.failures ?? []).slice(0, 10), reason: r.unavailableReasonZh ?? undefined, sandboxed: profile.sandbox !== false }))
  if (status === 'fail') console.log(String(r.tail ?? '').split('\n').slice(-15).join('\n'))
}
console.log(`总耗时 ${Math.round((Date.now() - t0) / 1000)} 秒`)
