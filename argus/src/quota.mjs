// 模型调用额度（每天，上海日期）：按调用种类分开计数，生产与测试两个池子互不占用。
//   种类：ai-test（AI 补测生成）· jev（Jev 维度分诊）· ai-review（ming-qa 代码审查）· pr-summary（人话摘要）
//   池子：production（event 合并即测、catch-up 补测）· test（--trigger manual 手动跑 / 演练 / 验收重跑，或 --quota-pool test）
// 上限可配：环境变量 ARGUS_QUOTA_<KIND>（生产）/ ARGUS_QUOTA_TEST_<KIND>（测试池），或 JSON 文件
// ARGUS_QUOTA_FILE（默认 $ARGUS_ROOT/quota.json：{ "production": { "ai-test": 40, … }, "test": { … } }）；环境变量优先。
// 计数存在 sqlite ai_usage 表（day 列 = quota:<pool>:<kind>:<YYYY-MM-DD>），一次运行一把锁，先查后记即可。
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { aiUsageToday, addAiUsage } from './db.mjs'
import { toShanghaiDay } from './util.mjs'

export const QUOTA_KINDS = ['ai-test', 'jev', 'ai-review', 'pr-summary']
export const QUOTA_POOLS = ['production', 'test']
export const DEFAULT_CAPS = { 'ai-test': 40, jev: 150, 'ai-review': 70, 'pr-summary': 40 }
// 旧的单一上限变量仍然认（只作用于生产池的对应种类）
const LEGACY_ENV = { 'ai-test': 'ARGUS_AITEST_DAILY_CAP', 'pr-summary': 'ARGUS_AI_DAILY_CAP' }
export const CAPPED_ZH = '额度用尽未补测'
const envName = (kind, pool) => `ARGUS_QUOTA_${pool === 'test' ? 'TEST_' : ''}${kind.replace(/-/g, '_').toUpperCase()}`

export function resolvePool({ quotaPool = null, trigger = 'manual' } = {}) {
  if (quotaPool != null && quotaPool !== '') {
    if (!QUOTA_POOLS.includes(quotaPool)) throw new Error(`--quota-pool must be ${QUOTA_POOLS.join('|')}: ${quotaPool}`)
    return quotaPool
  }
  return trigger === 'manual' ? 'test' : 'production'
}

function readQuotaFile(env) {
  const file = env.ARGUS_QUOTA_FILE ?? (env.ARGUS_ROOT ? join(env.ARGUS_ROOT, 'quota.json') : null)
  if (!file || !existsSync(file)) return { file: null, data: {} }
  try { return { file, data: JSON.parse(readFileSync(file, 'utf8')) } } catch { return { file, data: {}, error: 'unreadable' } }
}

export function quotaCaps(pool, env = process.env) {
  const { file, data } = readQuotaFile(env)
  const caps = {}, source = {}
  for (const k of QUOTA_KINDS) {
    const fromEnv = env[envName(k, pool)] ?? (pool === 'production' && LEGACY_ENV[k] ? env[LEGACY_ENV[k]] : undefined)
    const fromFile = data?.[pool]?.[k]
    const v = fromEnv != null && fromEnv !== '' ? Number(fromEnv) : fromFile != null ? Number(fromFile) : DEFAULT_CAPS[k]
    caps[k] = Number.isFinite(v) && v >= 0 ? Math.floor(v) : DEFAULT_CAPS[k]
    source[k] = fromEnv != null && fromEnv !== '' ? 'env' : fromFile != null ? `file ${file}` : 'default'
  }
  return { caps, source }
}

export const quotaCounterKey = (pool, kind, day) => `quota:${pool}:${kind}:${day}`

// 旧计数键：gen:<日>（旧 AI 补测）、jev:<日>、裸 <日>（旧 pr-summary）。夜间流程只读 quota:<池>:<种类>:<日>；
// 只有 Mac 上的 control.mjs（不传 quota 时）还读当天的旧键，它用自己那台机器的库。prune 前先备份（cli prune-quota-keys）
export const LEGACY_QUOTA_KEY = /^(?:gen:|jev:)?\d{4}-\d{2}-\d{2}$/
export function pruneLegacyQuotaKeys(db, { dryRun = false } = {}) {
  const rows = db.prepare('SELECT day, calls, tokens FROM ai_usage ORDER BY day').all().filter((r) => LEGACY_QUOTA_KEY.test(r.day))
  if (!dryRun) { const del = db.prepare('DELETE FROM ai_usage WHERE day = ?'); for (const r of rows) del.run(r.day) }
  return rows.map((r) => ({ key: r.day, calls: r.calls, tokens: r.tokens }))
}

// 一次运行用的额度对象。check(kind) 不扣；consume(kind, tokens) 记一次调用；markCapped 记下谁因为额度没做
export function createQuota({ db, pool = 'production', env = process.env, day = () => toShanghaiDay() } = {}) {
  const { caps, source } = quotaCaps(pool, env)
  const run = Object.fromEntries(QUOTA_KINDS.map((k) => [k, 0]))
  const capped = []
  const used = (kind) => (db ? aiUsageToday(db, quotaCounterKey(pool, kind, day())).calls : run[kind])
  return {
    pool, caps,
    check(kind) {
      const u = used(kind)
      return { ok: u < caps[kind], kind, pool, cap: caps[kind], used: u, day: day() }
    },
    consume(kind, tokens = 0) {
      run[kind]++
      if (db) addAiUsage(db, quotaCounterKey(pool, kind, day()), Number(tokens) || 0)
    },
    markCapped(kind, { pr = null, dimensions = [] } = {}) {
      const c = this.check(kind)
      const entry = { pr, kind, pool, cap: c.cap, used: c.used, day: c.day, dimensions, message: `${CAPPED_ZH}: ${kind} daily cap reached (${pool} pool ${c.used}/${c.cap}, ${c.day} Asia/Shanghai)` }
      capped.push(entry)
      return entry
    },
    summary() {
      return { pool, day: day(), caps, caps_source: source, used_by_run: { ...run }, used_today: Object.fromEntries(QUOTA_KINDS.map((k) => [k, used(k)])), capped: capped.map((c) => ({ ...c })) }
    },
  }
}

// 没有数据库时（单元测试 / 旧调用方）用的内存版
export const memoryQuota = (opts = {}) => createQuota({ ...opts, db: null })
