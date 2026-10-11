// 步骤结果复用（burst 容量）：同一步骤的相关输入没变 → 直接沿用上次的通过结果，不重跑。
// 键 = sha256(步骤定义 + 用到的替换变量（如 DIFF_BASE_SHA）+ 轮次类别（verdict / cov）+ Node 主版本 +
//            浏览器版本（系统 Chromium `--version`、panoptes 的 Playwright 版本及其 Chromium 版本 / revision）+
//            每个输入路径在被测提交里的 git tree / blob 哈希 + package-lock.json 的 blob 哈希)。
// 浏览器升级后旅程 / 冒烟的结果可能不同，所以浏览器版本变了一律不复用（对所有步骤都生效，保守）。
// 输入路径：step.inputs（例如 api-inproc 只看 server/ shared/ src/ 和配置）；没写就是整棵树（HEAD^{tree}）。
// 只存「通过、没超时、没有 unavailable、测试报告完整」的结果；失败永远重跑（要走重跑 / 基线三重确认）。
// step.cache === false（bench 这类计时测试、AI 补测）不复用。ARGUS_STEP_CACHE=0 / --no-step-cache 关闭。
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync, renameSync } from 'node:fs'
import { join } from 'node:path'
import { CACHE_DIR, CODE_DIR } from './paths.mjs'

// 带 argus 自带工具的步骤（接口探测、旅程脚本）：工具代码变了也要重跑 → 键里带 panoptes 提交
let codeVersion = null
const argusCodeVersion = () => (codeVersion ??= (spawnSync('git', ['-C', CODE_DIR, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).stdout || 'unknown').trim())

// 浏览器版本（每个进程算一次）：与 nightly 的 system_health.environment 用同一个 Chromium 启动器
export const linuxChromium = () => (existsSync('/usr/local/bin/chromium') ? '/usr/local/bin/chromium' : '/usr/bin/chromium')
let browserCache = null
export function browserVersions({ chromiumBin = process.env.ARGUS_CHROMIUM_BIN ?? linuxChromium(), codeDir = CODE_DIR } = {}) {
  const chromium = (() => { const r = spawnSync(chromiumBin, ['--version'], { encoding: 'utf8', timeout: 15_000 }); return r.status === 0 ? String(r.stdout).trim().split('\n')[0] : 'unavailable' })()
  const json = (p) => { try { return JSON.parse(readFileSync(join(codeDir, p), 'utf8')) } catch { return null } }
  const pw = json('node_modules/playwright/package.json')?.version ?? 'absent'
  const b = (json('node_modules/playwright-core/browsers.json')?.browsers ?? []).find((x) => x.name === 'chromium')
  return { chromium, playwright: pw, playwright_chromium: b ? `${b.browserVersion} (r${b.revision})` : 'absent' }
}
export const currentBrowserVersions = () => (browserCache ??= browserVersions())

export const STEP_CACHE_DIR = () => process.env.ARGUS_STEP_CACHE_DIR ?? join(CACHE_DIR, 'step-results')
export const REUSE_LABELS = new Set(['h1', 'h1r', 'cov', 'l'])
export const stepCacheEnabled = (env = process.env) => env.ARGUS_STEP_CACHE !== '0'
const sha256 = (s) => createHash('sha256').update(s).digest('hex')
// 每次运行都不同的位置变量（运行目录、报告文件路径、复制进来的工具路径）不进键：它们只决定结果写到哪，不影响结果本身。
// 否则带 \${TEST_REPORT} / \${RUN} 的步骤（单测）永远命中不了。
export const PER_RUN_VARS = new Set(['RUN', 'TEST_REPORT', 'NODE_TEST_REPORTER'])
const isPerRunVar = (k, v) => PER_RUN_VARS.has(k) || (typeof v === 'string' && v.startsWith('/'))

export function treeHashes(src, paths, { git = (args) => spawnSync('git', ['-C', src, ...args], { encoding: 'utf8' }) } = {}) {
  const out = {}
  for (const p of paths) {
    const r = git(['rev-parse', '--verify', '--quiet', p === '.' ? 'HEAD^{tree}' : `HEAD:${p}`])
    out[p] = r.status === 0 ? String(r.stdout).trim() : 'absent'
  }
  return out
}

export function stepCacheKey({ step, label, vars = {}, src, nodeMajor = process.versions.node.split('.')[0], hashes = null, browser = null }) {
  if (step.cache === false || step.aiGenerated || step.aiTest || step.kind === 'setup') return null
  if (!REUSE_LABELS.has(label)) return null
  const inputs = [...new Set([...(step.inputs ?? ['.']), 'package-lock.json'])]
  const h = hashes ?? treeHashes(src, inputs)
  const def = JSON.stringify({ id: step.id, cmd: step.cmd, env: step.env ?? null, probe: step.probe ?? null, tool: step.tool ?? null, testReport: step.testReport ?? null, shard: Boolean(step.shard) })
  const used = Object.fromEntries(Object.entries(vars).filter(([k, v]) => !isPerRunVar(k, v) && def.includes(`\${${k}}`)))
  const cls = label === 'cov' ? 'cov' : 'verdict'
  const tools = step.probe || step.tool ? argusCodeVersion() : null
  const br = browser ?? currentBrowserVersions()
  return { key: sha256(JSON.stringify({ v: 2, def, used, cls, nodeMajor, h, tools, browser: br })), inputs: h, cls, browser: br }
}

// 判定轮次：只存通过的完整结果。覆盖率轮次（cov）：存覆盖率数据完整的结果（插桩下个别用例失败不影响，它本来就不参与判定）
const cacheable = (r, cls = 'verdict') => Boolean(r) && !r.timedOut && !r.unavailable && !r.notApplicable && !r.disturbedDesktop
  && (cls === 'cov' ? r.coverage?.status === 'ok' : r.ok && r.testOutcomeStatus !== 'incomplete' && (r.coverage == null || r.coverage.status === 'ok'))

const KEEP = ['id', 'nameZh', 'kind', 'cmdText', 'ok', 'exitCode', 'timedOut', 'durationMs', 'failures', 'failureDetails', 'failureParseStatus', 'testOutcomes', 'testOutcomeStatus', 'testOutcomeReason', 'probeReport', 'coverage', 'shards', 'covers', 'commandTemplate']
export function storeStepResult(k, result, source, { dir = STEP_CACHE_DIR() } = {}) {
  if (!k || !cacheable(result, k.cls)) return false
  mkdirSync(dir, { recursive: true })
  const doc = { key: k.key, inputs: k.inputs, browser: k.browser ?? null, stored_at: new Date().toISOString(), source, result: Object.fromEntries(KEEP.filter((f) => result[f] !== undefined).map((f) => [f, result[f]])) }
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
    reusedFrom: { ...doc.source, stored_at: doc.stored_at, key: doc.key.slice(0, 16), original_duration_ms: doc.result.durationMs ?? null, ...(doc.browser ? { browser: doc.browser } : {}) } }
}
