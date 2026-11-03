#!/usr/bin/env node
// 用法：node scripts/test-script-failures.mjs [--git <镜像目录>] <reports 目录>…（只读）。
// 测试脚本自身出错（runner_error，产品步骤没跑）按指纹归组，列出最早出现的提交和涉及的 PR。
// 例：node scripts/test-script-failures.mjs --git /workspace/mivo-qa/argus-root/cache/mirrors/xindong__mivo-canvas-plugin.git /workspace/mivo-qa/reports/catch-up
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { groupTestScriptFailures, gitIsAncestor } from '../src/test-script-failures.mjs'
const args = process.argv.slice(2)
const gi = args.indexOf('--git')
const git = gi >= 0 ? args.splice(gi, 2)[1] : null
const reports = []
for (const dir of args) for (const f of readdirSync(dir)) if (/^PR-\d+\.json$/.test(f)) { try { reports.push(JSON.parse(readFileSync(join(dir, f), 'utf8'))) } catch { /* skip */ } }
console.log(JSON.stringify(groupTestScriptFailures(reports, git ? { isAncestor: gitIsAncestor(git) } : {}), null, 1))
