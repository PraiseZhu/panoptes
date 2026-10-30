import { readFileSync, existsSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { CODE_DIR, SECRETS_FILE } from './paths.mjs'
import { DIMENSION_KEYS } from './matrix.mjs'

const REGISTRY_PATH = process.env.ARGUS_REGISTRY ?? join(CODE_DIR, 'config/registry.json')
const PROFILES_DIR = process.env.ARGUS_PROFILES_DIR ?? join(CODE_DIR, 'config/profiles')

const STEP_KINDS = new Set(['setup', 'product', 'quality'])

// 目标分支匹配这些 glob（* 可跨 /）的 PR 不测，只写 skipped（reason non-main-base）报告；registry.skipBaseBranches 可覆盖；设成 [] 就关闭跳过（所有目标分支都测）
export const DEFAULT_SKIP_BASE_BRANCHES = ['probe-*']
export function matchesSkipBase(baseRef, patterns = DEFAULT_SKIP_BASE_BRANCHES) {
  if (typeof baseRef !== 'string' || !baseRef) return false
  return patterns.some((g) => new RegExp('^' + g.split('*').map((x) => x.replace(/[.+?^${}()|[\]\\]/g, '\\$&')).join('.*') + '$').test(baseRef))
}

export function loadRegistry() {
  const reg = JSON.parse(readFileSync(REGISTRY_PATH, 'utf8'))
  if (!reg.owner?.login || !Number.isInteger(reg.owner?.id)) throw new Error(`registry.owner 需要 login 和数字 id，实际 ${JSON.stringify(reg.owner)}`)
  if (!reg.issueRepo) throw new Error('registry.issueRepo 缺失')
  if (Number.isNaN(Date.parse(reg.enabledSince))) throw new Error(`registry.enabledSince 不是合法时间：${reg.enabledSince}`)
  reg.skipBaseBranches ??= DEFAULT_SKIP_BASE_BRANCHES
  if (!Array.isArray(reg.skipBaseBranches) || reg.skipBaseBranches.some((g) => typeof g !== 'string' || !g)) throw new Error(`registry.skipBaseBranches 需要非空字符串的 glob 列表，实际 ${JSON.stringify(reg.skipBaseBranches)}`)
  for (const p of reg.projects ?? []) {
    p.skipBaseBranches = reg.skipBaseBranches
    if (!/^[\w.-]+\/[\w.-]+$/.test(p.repo)) throw new Error(`项目 repo 格式错误：${p.repo}`)
    if (p.profile) p.profileData = loadProfile(p.profile)
  }
  return reg
}

export function loadProfile(name) {
  const path = join(PROFILES_DIR, `${name}.json`)
  const prof = applyPlatformOverrides(JSON.parse(readFileSync(path, 'utf8')))
  if (!Array.isArray(prof.steps) || prof.steps.length === 0) throw new Error(`profile ${name} 没有 steps`)
  for (const s of prof.steps) {
    validateTestReport(s.testReport)
    // 探针步骤的命令固定：跑测试目录里的探针脚本
    if (s.probe) {
      if (s.cmd) throw new Error(`profile ${name} 的探针步骤 ${s.id} 不能再写 cmd`)
      s.cmd = ['node', '${RUN}/tools/probe-run.mjs', `\${RUN}/probe-${s.id}.json`]
    }
    if (!s.id || !s.nameZh || !STEP_KINDS.has(s.kind) || !Array.isArray(s.cmd) || s.cmd.length === 0) {
      throw new Error(`profile ${name} 的 step 字段不完整：${JSON.stringify(s)}`)
    }
  }
  if (!prof.steps.some((s) => s.kind === 'setup')) throw new Error(`profile ${name} 缺少 setup 步骤`)
  validateDimensions(name, prof)
  return prof
}

// profile.platforms.<process.platform>：按平台覆盖。env / sandboxReadOnly 合并，steps 按 id 浅合并（值为 null 则删掉该步）。
// 例如 Linux 上把 Mac mini 的 Ego Lite 换成 /usr/bin/google-chrome。
export function applyPlatformOverrides(prof, platform = process.platform) {
  const o = prof.platforms?.[platform]
  if (!o) return prof
  const out = { ...prof, env: { ...(prof.env ?? {}), ...(o.env ?? {}) } }
  if (o.checkoutMode) out.checkoutMode = o.checkoutMode
  if (o.sandboxReadOnly) out.sandboxReadOnly = [...(prof.sandboxReadOnly ?? []), ...o.sandboxReadOnly]
  if (o.steps) {
    out.steps = prof.steps.flatMap((s) => {
      if (!(s.id in o.steps)) return [s]
      const patch = o.steps[s.id]
      if (patch === null) return []
      return [{ ...s, ...patch, env: patch.env === undefined ? s.env : { ...(s.env ?? {}), ...patch.env } }]
    })
    for (const extra of o.extraSteps ?? []) out.steps.push(extra)
  } else if (o.extraSteps) out.steps = [...prof.steps, ...o.extraSteps]
  return out
}

export function validateTestReport(report) {
  if (report === undefined) return
  if (!report || !['vitest-json', 'node-events', 'junit-xml'].includes(report.format)
    || typeof report.path !== 'string' || !report.path.startsWith('${RUN}/')
    || report.path.includes('\\') || report.path.slice(7).includes('${')
    || report.path.split('/').some((part) => ['.', '..', ''].includes(part))) {
    throw new Error('testReport 必须指定受支持的格式和本轮 ${RUN}/ 下的文件路径')
  }
}

// covers / notApplicable / pathHints 里的维度必须属于 ming-qa 的 29 项；同一维度不能既声明不适用又被某步覆盖
export function validateDimensions(name, prof) {
  const bad = (k) => !DIMENSION_KEYS.has(k)
  const na = Object.keys(prof.notApplicable ?? {})
  for (const k of na) if (bad(k)) throw new Error(`profile ${name} 的 notApplicable 有不认识的维度：${k}`)
  for (const [glob, keys] of Object.entries(prof.pathHints ?? {})) {
    for (const k of keys) if (bad(k)) throw new Error(`profile ${name} 的 pathHints「${glob}」有不认识的维度：${k}`)
  }
  for (const s of prof.steps) {
    for (const k of s.covers ?? []) {
      if (bad(k)) throw new Error(`profile ${name} 的步骤 ${s.id} covers 有不认识的维度：${k}`)
      if (na.includes(k)) throw new Error(`profile ${name} 的维度 ${k} 既声明不适用，又被步骤 ${s.id} 覆盖`)
    }
    if (s.optional && !(s.covers ?? []).length) throw new Error(`profile ${name} 的可选步骤 ${s.id} 必须写 covers，否则分诊没法决定跑不跑`)
  }
}

// secrets.env：KEY=VALUE，每行一个；文件权限必须是 0600
export function loadSecrets(path = SECRETS_FILE) {
  if (!existsSync(path)) return {}
  const mode = statSync(path).mode & 0o777
  if (mode & 0o077) throw new Error(`secrets 文件权限过宽：${mode.toString(8)}，期望 600`)
  const out = {}
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/)
    if (m) out[m[1]] = m[2].replace(/^["']|["']$/g, '')
  }
  return withSecretAliases(out)
}

// Linux 夜间 QA 的凭证文件用 QA_LLM_*（与 ming-qa 共用）；映射到 Argus 内部名，已有的 ARGUS_* 优先
const SECRET_ALIASES = { ARGUS_LLM_BASE_URL: 'QA_LLM_BASE_URL', ARGUS_LLM_API_KEY: 'QA_LLM_API_KEY' }
export function withSecretAliases(out) {
  for (const [argusKey, alias] of Object.entries(SECRET_ALIASES)) if (!out[argusKey] && out[alias]) out[argusKey] = out[alias]
  return out
}
