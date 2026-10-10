#!/usr/bin/env node
// 用真实 PR 试跑分诊：node scripts/trial-matrix.mjs <owner/repo> <pr号> [--twice]
// 只读：不写账本、不记用量、不跑测试、不发 issue、不发飞书。--twice 连跑两次，列出判定不一致的维度。
import { loadRegistry, loadSecrets } from '../src/config.mjs'
import { getPull, listPullFiles } from '../src/github.mjs'
import { classifyChange } from '../src/classify.mjs'
import { evaluateJev } from '../src/jev.mjs'
import { DIMENSIONS, toMatrixQuestions, toMatrixState, toTriage, toStepPlan, toDimensionLabel } from '../src/matrix.mjs'

const [repo, prArg, ...rest] = process.argv.slice(2)
if (!repo || !prArg) { console.error('用法：node scripts/trial-matrix.mjs <owner/repo> <pr号> [--twice]'); process.exit(2) }
const reg = loadRegistry()
const project = reg.projects.find((p) => p.repo === repo)
if (!project?.profileData) { console.error(`${repo} 没有接入测试`); process.exit(2) }
const profile = project.profileData
const secrets = loadSecrets()
const pr = await getPull(repo, Number(prArg))
const files = await listPullFiles(repo, Number(prArg))
const { riskClass } = classifyChange(files, profile)

async function once() {
  const jev = await evaluateJev({ state: toMatrixState({ pr, files, profile }), questions: toMatrixQuestions(profile), secrets })
  return toTriage({ profile, files, jev })
}
const a = await once()
console.log(`PR ${repo}#${prArg}「${pr.title}」 分级 ${riskClass}，分诊 ${a.status}${a.reasonZh ? `（${a.reasonZh}）` : ''} 模型 ${a.model ?? '-'}`)
for (const r of a.rows) console.log(`${toDimensionLabel(r.key).padEnd(12, '　')}\t${r.verdict}\t${r.source}\t${r.p != null ? r.p.toFixed(2) : ''}`)
const plan = toStepPlan({ profile, riskClass, triage: a })
console.log(`会跑：${plan.stepIds.join(', ')}`)
for (const s of plan.skipped) console.log(`跳过：${s.nameZh}（${s.reasonZh}）`)
if (rest.includes('--twice')) {
  const b = await once()
  const diff = DIMENSIONS.filter((d) => a.rows.find((r) => r.key === d.key).verdict !== b.rows.find((r) => r.key === d.key).verdict)
  console.log(`第二次判定不一致：${diff.length} 项${diff.length ? `（${diff.map((d) => d.labelZh).join('、')}）` : ''}`)
}
