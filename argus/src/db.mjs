// 单写者 SQLite 账本：任务、发布 outbox、缺陷绑定、AI 用量、元数据
import { DatabaseSync } from 'node:sqlite'
import { mkdirSync } from 'node:fs'
import { dirname } from 'node:path'
import { DB_PATH } from './paths.mjs'

const SCHEMA_VERSION = 2

const SCHEMA = `
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_key TEXT NOT NULL UNIQUE,
  repo TEXT NOT NULL,
  pr_number INTEGER NOT NULL,
  pr_node_id TEXT NOT NULL,
  pr_title TEXT NOT NULL,
  pr_url TEXT NOT NULL,
  author_login TEXT NOT NULL,
  base_ref TEXT NOT NULL,
  merged_at TEXT NOT NULL,
  merge_sha TEXT NOT NULL,
  status TEXT NOT NULL,          -- queued | running | done
  outcome TEXT,                  -- 见 decide.mjs 的 OUTCOMES
  risk_class TEXT,               -- A | B | C
  detail_json TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, merged_at);
CREATE INDEX IF NOT EXISTS jobs_finished ON jobs(finished_at);
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,            -- issue | issue_comment | feishu
  dedupe_key TEXT NOT NULL UNIQUE,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL,          -- pending | sent | unknown | failed
  result_json TEXT,
  tries INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  claimed_at TEXT
);
CREATE TABLE IF NOT EXISTS defects (
  fingerprint TEXT PRIMARY KEY,
  issue_repo TEXT NOT NULL,
  issue_number INTEGER NOT NULL,
  issue_url TEXT NOT NULL,
  first_job_id INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_usage (
  day TEXT PRIMARY KEY,
  calls INTEGER NOT NULL DEFAULT 0,
  tokens INTEGER NOT NULL DEFAULT 0
);
`

export function openDb(path = DB_PATH) {
  mkdirSync(dirname(path), { recursive: true })
  const db = new DatabaseSync(path)
  db.exec('PRAGMA journal_mode=WAL; PRAGMA busy_timeout=10000; PRAGMA foreign_keys=ON;')
  db.exec(SCHEMA)
  const v = getMeta(db, 'schema_version')
  if (v && Number(v) === 1) migrateToV2(db)
  else if (v && Number(v) !== SCHEMA_VERSION) throw new Error(`数据库 schema 版本不兼容：期望 ${SCHEMA_VERSION}，实际 ${v}`)
  if (!getMeta(db, 'schema_version')) setMeta(db, 'schema_version', String(SCHEMA_VERSION))
  return db
}

// v2：outbox 增加跨进程认领列，保证同一条消息同时只有一个进程在发
function migrateToV2(db) {
  const cols = db.prepare('PRAGMA table_info(outbox)').all().map((c) => c.name)
  db.exec('BEGIN')
  if (!cols.includes('claimed_at')) db.exec('ALTER TABLE outbox ADD COLUMN claimed_at TEXT')
  setMeta(db, 'schema_version', '2')
  db.exec('COMMIT')
}

const now = () => new Date().toISOString()

export function getMeta(db, key) {
  return db.prepare('SELECT value FROM meta WHERE key = ?').get(key)?.value ?? null
}

export function setMeta(db, key, value) {
  db.prepare('INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value').run(key, String(value))
}

// 业务唯一键：同一 PR 同一合并版本只会有一个任务
export function jobKey(repo, prNodeId, mergeSha) {
  return `github.com|${repo}|${prNodeId}|${mergeSha}`
}

export function insertJob(db, j) {
  const ts = now()
  const res = db.prepare(`INSERT OR IGNORE INTO jobs
    (job_key, repo, pr_number, pr_node_id, pr_title, pr_url, author_login, base_ref, merged_at, merge_sha, status, outcome, detail_json, created_at, updated_at, finished_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`).run(
    jobKey(j.repo, j.prNodeId, j.mergeSha), j.repo, j.prNumber, j.prNodeId, j.prTitle, j.prUrl, j.authorLogin, j.baseRef,
    j.mergedAt, j.mergeSha, j.status ?? 'queued', j.outcome ?? null, j.detail ? JSON.stringify(j.detail) : null, ts, ts,
    j.status === 'done' ? ts : null,
  )
  return res.changes > 0
}

export function nextQueuedJob(db) {
  return db.prepare("SELECT * FROM jobs WHERE status = 'queued' ORDER BY merged_at ASC, id ASC LIMIT 1").get() ?? null
}

export function markRunning(db, id) {
  db.prepare("UPDATE jobs SET status = 'running', attempts = attempts + 1, updated_at = ? WHERE id = ?").run(now(), id)
}

export function finishJob(db, id, { outcome, riskClass, detail }) {
  const ts = now()
  db.prepare("UPDATE jobs SET status = 'done', outcome = ?, risk_class = ?, detail_json = ?, updated_at = ?, finished_at = ? WHERE id = ?")
    .run(outcome, riskClass ?? null, JSON.stringify(detail ?? {}), ts, ts, id)
}

export function patchJobDetail(db, id, patch) {
  const row = db.prepare('SELECT detail_json FROM jobs WHERE id = ?').get(id)
  if (!row) return
  const detail = { ...(row.detail_json ? JSON.parse(row.detail_json) : {}), ...patch }
  db.prepare('UPDATE jobs SET detail_json = ?, updated_at = ? WHERE id = ?').run(JSON.stringify(detail), now(), id)
}

// refundAttempt：服务正常停止（升级、重启）打断的任务不算一次尝试，否则多次部署会把它误判成「环境问题」
export function requeueJob(db, id, { refundAttempt = false } = {}) {
  const refund = refundAttempt ? ', attempts = MAX(attempts - 1, 0)' : ''
  db.prepare(`UPDATE jobs SET status = 'queued'${refund}, updated_at = ? WHERE id = ?`).run(now(), id)
}

// 重启恢复：上次进程死在 running 的任务回到队列；超过 3 次记基础设施失败
export function recoverRunning(db) {
  const rows = db.prepare("SELECT id, attempts FROM jobs WHERE status = 'running'").all()
  for (const r of rows) {
    if (r.attempts >= 3) finishJob(db, r.id, { outcome: 'infra_failed', detail: { reasonZh: '测试进程连续 3 次中途退出，已停止重试' } })
    else requeueJob(db, r.id)
  }
  return rows.length
}

export function jobsFinishedBetween(db, fromIso, toIso) {
  return db.prepare('SELECT * FROM jobs WHERE status = ? AND finished_at > ? AND finished_at <= ? ORDER BY repo, pr_number').all('done', fromIso, toIso)
}

export function countByStatus(db) {
  return Object.fromEntries(db.prepare('SELECT status, COUNT(*) AS n FROM jobs GROUP BY status').all().map((r) => [r.status, r.n]))
}

// outbox

export function enqueueOutbox(db, kind, dedupeKey, payload) {
  const ts = now()
  db.prepare('INSERT OR IGNORE INTO outbox(kind, dedupe_key, payload_json, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)')
    .run(kind, dedupeKey, JSON.stringify(payload), 'pending', ts, ts)
  return db.prepare('SELECT * FROM outbox WHERE dedupe_key = ?').get(dedupeKey)
}

export function updateOutbox(db, id, status, result) {
  db.prepare('UPDATE outbox SET status = ?, result_json = ?, tries = tries + 1, updated_at = ? WHERE id = ?')
    .run(status, JSON.stringify(result ?? null), now(), id)
}

export function pendingOutbox(db, kind) {
  return db.prepare("SELECT * FROM outbox WHERE kind = ? AND status IN ('pending', 'unknown') ORDER BY id").all(kind)
}

// 原子认领：只有认领成功的进程才能发送；超过 staleMs 的旧认领视为发送进程已死
export function claimOutbox(db, id, staleMs = 10 * 60_000) {
  const ts = now()
  const staleBefore = new Date(Date.now() - staleMs).toISOString()
  const res = db.prepare("UPDATE outbox SET claimed_at = ? WHERE id = ? AND status IN ('pending', 'unknown', 'failed') AND (claimed_at IS NULL OR claimed_at < ?)").run(ts, id, staleBefore)
  return res.changes > 0
}

export function releaseOutbox(db, id) {
  db.prepare('UPDATE outbox SET claimed_at = NULL WHERE id = ?').run(id)
}

export function getOutbox(db, dedupeKey) {
  return db.prepare('SELECT * FROM outbox WHERE dedupe_key = ?').get(dedupeKey) ?? null
}

// defects

export function getDefect(db, fingerprint) {
  return db.prepare('SELECT * FROM defects WHERE fingerprint = ?').get(fingerprint) ?? null
}

export function saveDefect(db, d) {
  // 同一指纹换绑到新 issue 时要覆盖，否则之后的观察会一直指向旧 issue
  db.prepare(`INSERT INTO defects(fingerprint, issue_repo, issue_number, issue_url, first_job_id, created_at) VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(fingerprint) DO UPDATE SET issue_repo = excluded.issue_repo, issue_number = excluded.issue_number, issue_url = excluded.issue_url`)
    .run(d.fingerprint, d.issueRepo, d.issueNumber, d.issueUrl, d.jobId, now())
}

// ai budget

export function aiUsageToday(db, day) {
  return db.prepare('SELECT calls, tokens FROM ai_usage WHERE day = ?').get(day) ?? { calls: 0, tokens: 0 }
}

export function addAiUsage(db, day, tokens) {
  db.prepare('INSERT INTO ai_usage(day, calls, tokens) VALUES (?, 1, ?) ON CONFLICT(day) DO UPDATE SET calls = calls + 1, tokens = tokens + excluded.tokens')
    .run(day, tokens)
}
