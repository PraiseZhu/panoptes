// 统一分级（Praise 2026-10-23）：本地（作者会话）、审查机（合并前）、测试（合并后 QA）三处的 P0–P3 以审查机的统一分级为准，
// 唯一来源是 PraiseZhu/Review-PR 的 mivo/config/severity.md。panoptes 不再手写一套 P0–P3 定义：
//  · config/severity.md 是那份文件按 sha256 钉住的只读副本（钉住的版本记在 config/severity.lock.json）；
//  · AI 代码审查的提示词直接嵌这份原文；报告只引用它（severity_standard），不复述定义；
//  · 副本和钉住的 sha256 对不上 → 读取失败，代码审查记 unavailable，不凭摘要继续；
//  · scripts/check-severity-pin.mjs 每天和 Review-PR main 比一次，不一致就在运维日志里报警，等人更新钉住值。
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { createHash } from 'node:crypto'
import { CODE_DIR } from './paths.mjs'

export const SEVERITY_FILE = () => process.env.ARGUS_SEVERITY_FILE ?? join(CODE_DIR, 'config', 'severity.md')
export const SEVERITY_LOCK_FILE = () => process.env.ARGUS_SEVERITY_LOCK ?? join(CODE_DIR, 'config', 'severity.lock.json')
export const sha256Hex = (b) => createHash('sha256').update(b).digest('hex')
// 与审查机 prompt.mjs 的 SEVERITY_VERSION 同一算法：文件原始内容 sha256 的前 12 位（盯梢裁决记录 std= 用它）
export const severityVersion = (sha) => String(sha ?? '').slice(0, 12)

export function readSeverityLock(file = SEVERITY_LOCK_FILE()) {
  const lock = JSON.parse(readFileSync(file, 'utf8'))
  for (const k of ['source_repo', 'path', 'ref', 'sha256']) if (!lock?.[k]) throw new Error(`severity lock ${file} has no ${k}`)
  return lock
}

let cached = null
export function loadSeverityStandard({ file = SEVERITY_FILE(), lockFile = SEVERITY_LOCK_FILE(), fresh = false } = {}) {
  if (cached && !fresh && cached.file === file && cached.lockFile === lockFile) return cached.std
  const lock = readSeverityLock(lockFile)
  const buf = readFileSync(file)
  const got = sha256Hex(buf)
  if (got !== lock.sha256) {
    throw new Error(`severity standard ${file}: sha256 ${got.slice(0, 12)} does not match the pinned ${String(lock.sha256).slice(0, 12)} `
      + `(${lock.source_repo} ${lock.path}@${String(lock.ref).slice(0, 12)}); refusing to grade with an unpinned standard`)
  }
  const std = { text: buf.toString('utf8'), sha256: got, version: severityVersion(got), source_repo: lock.source_repo, path: lock.path, ref: lock.ref, ref_source: lock.ref_source ?? null }
  cached = { file, lockFile, std }
  return std
}

// 报告里的引用（不含原文）。读不到时如实写 unavailable 和原因
export function severityStandardRef() {
  try {
    const s = loadSeverityStandard()
    return { status: 'pinned', source_repo: s.source_repo, path: s.path, ref: s.ref, sha256: s.sha256, version: s.version }
  } catch (err) { return { status: 'unavailable', error: String(err.message).slice(0, 300) } }
}

// 报告 severity_scale：只用 severity.md 表里的等级名称，不复述定义；定义看 severity_standard 指向的原文
export const SEVERITY_LEVEL_NAMES = Object.freeze({ P0: 'P0 紧急严重事故', P1: 'P1 严重缺陷', P2: 'P2 一般缺陷', P3: 'P3 改进建议（不报）' })

// 上游比对：pinned = 本地锁；upstream = Review-PR main（或指定 ref）上同一路径的原文。纯函数，取数由调用方注入
export function compareUpstream({ lock, upstreamText }) {
  if (upstreamText == null) return { status: 'error', pinned_sha256: lock.sha256, upstream_sha256: null }
  const up = sha256Hex(Buffer.from(String(upstreamText), 'utf8'))
  return { status: up === lock.sha256 ? 'same' : 'drift', pinned_sha256: lock.sha256, upstream_sha256: up, pinned_ref: lock.ref }
}
