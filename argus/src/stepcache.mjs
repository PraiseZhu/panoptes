// 步骤结果复用（burst 容量）：同一步骤的相关输入没变 → 直接沿用上次的通过结果，不重跑。
// 键 = sha256(步骤定义 + 用到的替换变量（如 DIFF_BASE_SHA）+ 轮次类别（verdict / cov）+ Node 主版本 +
//            每个输入路径在被测提交里的 git tree / blob 哈希 + package-lock.json 的 blob 哈希)。
// 输入路径：step.inputs（例如 api-inproc 只看 server/ shared/ src/ 和配置）；没写就是整棵树（HEAD^{tree}）。
// 只存「通过、没超时、没有 unavailable、测试报告完整」的结果；失败永远重跑（要走重跑 / 基线三重确认）。
// step.cache === false（bench 这类计时测试、AI 补测）不复用。ARGUS_STEP_CACHE=0 关闭（--no-reuse 也会关）。
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync, renameSync } from 'node:fs'
import { join } from 'node:path'
import { CACHE_DIR, CODE_DIR } from './paths.mjs'

// 带 argus 自带工具的步骤（接口探测、旅程脚本）：工具代码变了也要重跑 → 键里带 panoptes 提交
let codeVersion = null
const argusCodeVersion = () => (codeVersion ??= (spawnSync('git', ['-C', CODE_DIR, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).stdout || 'unknown').trim())

export const STEP_CACHE_DIR = () => process.env.ARGUS_STEP_CACHE_DIR ?? join(CACHE_DIR, 'step-results')
export const REUSE_LABELS = new Set(['h1', 'h1r', 'cov', 'l'])
export const stepCacheEnabled = (env = process.env) => env.ARGUS_STEP_CACHE !== '0'
const sha256 = (s) => createHash('sha256').update(s).digest('hex')

export function treeHashes(src, paths, { git = (args) => spawnSync('git', ['-C', src, ...args], { encoding: 'utf8' }) } = {}) {
  const out = {}
  for (const p of paths) {
    const r = git(['rev-parse', '--verify', '--quiet', p === '.' ? 'HEAD^{tree}' : `HEAD:${p}`])
    out[p] = r.status === 0 ? String(r.stdout).trim() : 'absent'
  }
  return out
}

export function stepCacheKey({ step, label, vars = {}, src, nodeMajor = process.versions.node.split('.')[0], hashes = null }) {
  if (step.cache === false || step.aiGenerated || step.aiTest || step.kind === 'setup') return null
  if (!REUSE_LABELS.has(label)) return null
  const inputs = [...new Set([...(step.inputs ?? ['.']), 'package-lock.json'])]
  const h = hashes ?? treeHashes(src, inputs)
  const def = JSON.stringify({ id: step.id, cmd: step.cmd, env: step.env ?? null, probe: step.probe ?? null, tool: step.tool ?? null, testReport: step.testReport ?? null, shard: Boolean(step.shard) })
  const used = Object.fromEntries(Object.entries(vars).filter(([k]) => def.includes(`\${${k}}`)))
  const cls = label === 'cov' ? 'cov' : 'verdict'
  const tools = step.probe || step.tool ? argusCodeVersion() : null
  return { key: sha256(JSON.stringify({ v: 1, def, used, cls, nodeMajor, h, tools })), inputs: h, cls }
}

// 判定轮次：只存通过的完整结果。覆盖率轮次（cov）：存覆盖率数据完整的结果（插桩下个别用例失败不影响，它本来就不参与判定）
const cacheable = (r, cls = 'verdict') => Boolean(r) && !r.timedOut && !r.unavailable && !r.notApplicable && !r.disturbedDesktop
  && (cls === 'cov' ? r.coverage?.status === 'ok' : r.ok && r.testOutcomeStatus !== 'incomplete' && (r.coverage == null || r.coverage.status === 'ok'))

const KEEP = ['id', 'nameZh', 'kind', 'cmdText', 'ok', 'exitCode', 'timedOut', 'durationMs', 'failures', 'failureDetails', 'failureParseStatus', 'testOutcomes', 'testOutcomeStatus', 'testOutcomeReason', 'probeReport', 'coverage', 'shards', 'covers', 'commandTemplate']
export function storeStepResult(k, result, source, { dir = STEP_CACHE_DIR() } = {}) {
  if (!k || !cacheable(result, k.cls)) return false
  mkdirSync(dir, { recursive: true })
  const doc = { key: k.key, inputs: k.inputs, stored_at: new Date().toISOString(), source, result: Object.fromEntries(KEEP.filter((f) => result[f] !== undefined).map((f) => [f, result[f]])) }
  const file = join(dir, `${k.key}.json`)
  writeFileSync(`${file}.tmp-${process.pid}`, JSON.stringify(doc))
  renameSync(`${file}.tmp-${process.pid}`, file)
  return true
}

export function lookupStepResult(k, { dir = STEP_CACHE_DIR(), maxAgeDays = Number(process.env.ARGUS_STEP_CACHE_DAYS ?? 14) } = {}) {
  if (!k) return null
  const file = join(dir, `${k.key}.json`)
  if (!existsSync(file)) return null
  try {
    const doc = JSON.parse(readFileSync(file, 'utf8'))
    if (doc.key !== k.key || !cacheable(doc.result, k.cls)) return null
    if (Date.now() - Date.parse(doc.stored_at) > maxAgeDays * 86400_000) return null
    return doc
  } catch { return null }
}

// 复用的结果：与真跑的结果同形，durationMs 记 0（这次没花时间），原耗时与来源放 reusedFrom
export function reusedResult(doc, identity) {
  return { ...identity, ...doc.result, durationMs: 0, reused: true,
    reusedFrom: { ...doc.source, stored_at: doc.stored_at, key: doc.key.slice(0, 16), original_duration_ms: doc.result.durationMs ?? null } }
}
