#!/usr/bin/env node
// 用真实 PR 试跑 AI 补测：node scripts/trial-aitests.mjs <owner/repo> <pr号>
// 真分诊、真生成、真沙箱执行；用量记在临时账本里，不写正式账本、不发 issue、不发飞书。
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { loadRegistry, loadSecrets } from '../src/config.mjs'
import { openDb } from '../src/db.mjs'
import { getPull, listPullFiles } from '../src/github.mjs'
import { classifyChange } from '../src/classify.mjs'
import { triagePr } from '../src/control.mjs'
import { toStepPlan, toDimensionLabel } from '../src/matrix.mjs'
import { generateTargetedTests, toGapDimensions, toAiTestSteps } from '../src/aitests.mjs'
import { executeOnSha } from '../src/runner.mjs'

const [repo, prArg] = process.argv.slice(2)
const reg = loadRegistry()
const project = reg.projects.find((p) => p.repo === repo)
if (!project?.profileData) { console.error(`${repo} 没有接入测试`); process.exit(2) }
const profile = { ...project.profileData, displayName: project.displayName }
const secrets = loadSecrets()
const db = openDb(join(mkdtempSync(join(tmpdir(), 'argus-trial-')), 'trial.sqlite'))
const pr = await getPull(repo, Number(prArg))
const files = await listPullFiles(repo, Number(prArg))
const { riskClass } = classifyChange(files, profile)
const triage = await triagePr({ db, secrets, pr, files, profile })
const plan = toStepPlan({ profile, riskClass, triage })
const gaps = toGapDimensions({ profile, triage, stepIds: plan.stepIds })
console.log(`分级 ${riskClass}；缺口 ${gaps.map(toDimensionLabel).join('、') || '无'}`)
const gen = await generateTargetedTests({ db, secrets, riskClass, pr, files, profile, gaps })
console.log(`生成：${gen.status}${gen.reasonZh ? `（${gen.reasonZh}）` : ''}，${gen.tests.length} 个`)
for (const t of gen.tests) console.log(`  - [${t.kind}] ${t.purposeZh} → ${t.filePath}（覆盖 ${t.covers.map(toDimensionLabel).join('、')}）`)
if (!gen.tests.length) process.exit(0)
const aiSteps = toAiTestSteps(gen.tests, profile)
const full = { ...profile, steps: [...profile.steps, ...aiSteps] }
const results = await executeOnSha({ repo, jobId: 0, label: `trial-ai-${Date.now()}`, sha: pr.merge_commit_sha, profile: full, stepIds: aiSteps.map((s) => s.id) })
for (const r of results.filter((x) => x.id.startsWith('ai-test'))) {
  console.log(JSON.stringify({ id: r.id, status: r.unavailable ? 'invalid' : r.ok ? 'pass' : 'fail', reason: r.unavailableReasonZh }))
  console.log(String(r.tail ?? '').split('\n').slice(-6).join('\n'))
}
