// 测试脚本自身出错（failed_checks[].test_script_failure）按指纹归组，每组找最早出现的提交：
// 同一个 harness 失败跨多个 PR 出现时（#1384 合入后 #1433 #1456 … 都挂在同一处）只算一条共性问题，归到最早那个提交。
// 最早 = 组里是其他所有提交祖先的那个；分叉找不到时取 tested_at 最早的。isAncestor(a, b) 由调用方给（git merge-base --is-ancestor）。
import { spawnSync } from 'node:child_process'

// 新报告直接读 test_script_failure（产品步骤已跑时显式写 false）；旧报告没有这个字段：走同一个 isScriptFailure，
// 日志用 evidence.log_tail，改动文件只有报告里带了才用（没有就只按相对 / 仓库内路径判断）
export const isTestScriptFailure = (c, changedFiles = []) => Boolean(c?.test_script_failure ??
  isScriptFailure({ type: c?.failure_type, status: c?.status, test: c?.test, result: { tail: c?.evidence?.log_tail }, changedFiles }))

export function groupTestScriptFailures(reports, { isAncestor = () => false } = {}) {
  const groups = new Map()
  for (const r of reports ?? []) for (const p of r?.prs ?? []) for (const c of p.failed_checks ?? []) {
    if (!isTestScriptFailure(c, changedFileNames(p)) || !c.evidence?.tested_sha) continue
    const g = groups.get(c.fingerprint) ?? { fingerprint: c.fingerprint, check_id: c.check_id, test: c.test ?? null, seen: [] }
    if (!g.seen.some((x) => x.pr === p.number && x.sha === c.evidence.tested_sha)) g.seen.push({ pr: p.number, sha: c.evidence.tested_sha, tested_at: p.tested_at ?? null })
    groups.set(c.fingerprint, g)
  }
  return [...groups.values()].map((g) => {
    const byTime = [...g.seen].sort((a, b) => String(a.tested_at ?? '').localeCompare(String(b.tested_at ?? '')) || a.pr - b.pr)
    const first = byTime.find((x) => g.seen.every((y) => y.sha === x.sha || isAncestor(x.sha, y.sha))) ?? byTime[0]
    return { fingerprint: g.fingerprint, check_id: g.check_id, test: g.test, first_seen: { pr: first.pr, sha: first.sha }, prs: [...new Set(byTime.map((x) => x.pr))] }
  })
}

export const gitIsAncestor = (gitDir) => (a, b) => spawnSync('git', ['-C', gitDir, 'merge-base', '--is-ancestor', a, b], { timeout: 30_000 }).status === 0

// runner_error 只在「产品步骤一步都没开始」时才算测试脚本自身出错（验收 #166 round 1）：
// - browser-product-roundtrip「roundtrip did not complete」：产品旅程已经跑了（#1384），是产品失败
// - 缺模块（Cannot find module / ERR_MODULE_NOT_FOUND / Failed to load）：缺的是被测代码（相对路径、仓库内绝对路径）
//   或 PR diff 动过的文件 → PR 删 / 改名了被测模块，是产品失败；只有 node_modules / 裸包名且 diff 没碰才算脚本问题
const MISSING_MODULE = /(?:Cannot find (?:module|package)|Failed to load(?: url)?)\s+['"]?([^'"\s]+)['"]?/g

export function missingModules(tail) {
  return [...String(tail ?? '').replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '').matchAll(MISSING_MODULE)].map((m) => m[1])
}

const isProductModule = (spec, changedFiles) => {
  const s = spec.replace(/^file:\/\//, '').replace(/[?#].*$/, '')
  if ((changedFiles ?? []).some((f) => { const base = f.replace(/\.[cm]?[jt]sx?$/, ''); return s.endsWith(f) || s.endsWith(base) || base.endsWith(`/${s}`) || base === s }))
    return true
  if (s.includes('/node_modules/')) return false
  return s.startsWith('.') || s.startsWith('/')
}

export function isScriptFailure({ type, status, result, test, changedFiles = [] }) {
  if (type !== 'runner_error' || status !== 'unverified') return false
  // browser-product-roundtrip 只有「roundtrip did not complete」这一种 runner_error（runner.mjs checkFailureType），按检查名排除，旧报告没有 check_reasons 也适用
  if (test === 'browser-product-roundtrip') return false
  if (missingModules(result?.tail).some((m) => isProductModule(m, changedFiles))) return false
  return true
}

// PR 报告里的改动文件名（报告带 files[] 时）；没有就空
const changedFileNames = (p) => (Array.isArray(p?.files) ? p.files : []).map((f) => (typeof f === 'string' ? f : f?.filename)).filter(Boolean)
