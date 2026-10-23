// GitHub 访问：个人仓走 GitHub App（Argus QA PraiseZhu，最小权限：读代码 / 读 PR / 写 issue），
// App 没装到的仓（如 xindong 组织的 Mivo）仍走 Mini 上已登录的 gh
import { execFile } from 'node:child_process'
import { readFileSync, existsSync } from 'node:fs'
import { createSign } from 'node:crypto'
import { join } from 'node:path'
import { loadSecrets } from './config.mjs'
import { APP_DIR } from './paths.mjs'

const GH = process.env.ARGUS_GH_BIN ?? 'gh'
const RETRY_MS = Number(process.env.ARGUS_GH_RETRY_MS ?? 10_000)
const API = process.env.ARGUS_GH_API ?? 'https://api.github.com'

function gh(args, { input, timeoutMs = 60_000 } = {}) {
  return new Promise((resolve, reject) => {
    const child = execFile(GH, args, { maxBuffer: 64 * 1024 * 1024, timeout: timeoutMs, env: process.env }, (err, stdout, stderr) => {
      if (err) {
        const e = new Error(`gh ${args.slice(0, 4).join(' ')} 失败：${String(stderr || err.message).slice(0, 600)}`)
        e.stderr = String(stderr ?? '')
        e.httpStatus = Number(/HTTP (\d{3})/.exec(e.stderr)?.[1] ?? 0) || null
        reject(e)
        return
      }
      resolve(stdout)
    })
    if (input != null) child.stdin.end(input)
  })
}

// app identity

function appConfig() {
  const s = loadSecrets()
  if (!s.ARGUS_GH_APP_ID || !s.ARGUS_GH_APP_INSTALLATION_ID) return null
  const keyFile = s.ARGUS_GH_APP_KEY_FILE || join(APP_DIR, 'github-app.pem')
  if (!existsSync(keyFile)) return null
  return { appId: s.ARGUS_GH_APP_ID, installationId: s.ARGUS_GH_APP_INSTALLATION_ID, slug: s.ARGUS_GH_APP_SLUG || null, keyFile }
}

function appJwt(cfg) {
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString('base64url')
  const now = Math.floor(Date.now() / 1000)
  const unsigned = `${b64({ alg: 'RS256', typ: 'JWT' })}.${b64({ iat: now - 60, exp: now + 540, iss: String(cfg.appId) })}`
  return `${unsigned}.${createSign('RSA-SHA256').update(unsigned).sign(readFileSync(cfg.keyFile, 'utf8'), 'base64url')}`
}

async function httpJson(method, url, { token, scheme = 'token', body } = {}) {
  let resp
  try {
    resp = await fetch(url, {
      method,
      headers: { accept: 'application/vnd.github+json', authorization: `${scheme} ${token}`, 'x-github-api-version': '2022-11-28', ...(body ? { 'content-type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(60_000),
    })
  } catch (err) {
    throw Object.assign(new Error(`GitHub ${method} ${url.replace(API, '')} 网络失败：${err.message}`), { httpStatus: null })
  }
  const text = await resp.text()
  if (!resp.ok) throw Object.assign(new Error(`GitHub ${method} ${url.replace(API, '')} 返回 ${resp.status}：${text.slice(0, 300)}`), { httpStatus: resp.status })
  return text.trim() ? JSON.parse(text) : null
}

// 安装令牌 1 小时有效：提前 5 分钟换新；同时记下 App 能访问的仓
let appState = null
async function appSession() {
  const cfg = appConfig()
  if (!cfg) return null
  if (appState && appState.expiresAt - Date.now() > 5 * 60_000) return appState
  const tok = await httpJson('POST', `${API}/app/installations/${cfg.installationId}/access_tokens`, { token: appJwt(cfg), scheme: 'Bearer' })
  const repos = new Set()
  for (let page = 1; page <= 10; page++) {
    const r = await httpJson('GET', `${API}/installation/repositories?per_page=100&page=${page}`, { token: tok.token })
    for (const x of r.repositories) repos.add(x.full_name.toLowerCase())
    if (r.repositories.length < 100) break
  }
  let slug = cfg.slug
  if (!slug) slug = (await httpJson('GET', `${API}/app`, { token: appJwt(cfg), scheme: 'Bearer' })).slug
  appState = { token: tok.token, expiresAt: Date.parse(tok.expires_at), repos, botLogin: `${slug}[bot]` }
  return appState
}

const repoOf = (path) => /^repos\/([^/]+\/[^/?]+)/.exec(path)?.[1]?.toLowerCase() ?? null

// 这个仓由谁访问：App 覆盖的返回 App 会话，否则 null（走 gh）
export async function appFor(repo) {
  const s = await appSession()
  return s && repo && s.repos.has(repo.toLowerCase()) ? s : null
}

// issue 由谁创建（回查「结果不明」的发布时按创建者过滤）
export async function publisherLogin(repo, fallbackLogin) {
  return (await appFor(repo))?.botLogin ?? fallbackLogin
}

// git 取码凭证：App 覆盖的仓用安装令牌，否则 null（调用方用 gh 的凭证助手）
export async function gitTokenFor(repo) {
  return (await appFor(repo))?.token ?? null
}

// 只读请求遇到网络错误或 5xx 时重试；4xx 是确定性错误，直接抛
async function withRetry(fn, attempts = 3) {
  for (let i = 1; ; i++) {
    try {
      return await fn()
    } catch (err) {
      const transient = !err.httpStatus || err.httpStatus >= 500
      if (!transient || i >= attempts) throw err
      await new Promise((r) => setTimeout(r, i * RETRY_MS))
    }
  }
}

async function apiGet(path) {
  return withRetry(async () => {
    const app = await appFor(repoOf(path))
    if (app) return httpJson('GET', `${API}/${path}`, { token: app.token })
    const out = await gh(['api', '-H', 'Accept: application/vnd.github+json', path])
    return out.trim() ? JSON.parse(out) : null
  })
}

// 写请求不自动重试：结果不明由发布器的回查逻辑处理
async function apiSend(method, path, body) {
  const app = await appFor(repoOf(path))
  if (app) return httpJson(method, `${API}/${path}`, { token: app.token, body })
  const out = await gh(['api', '-X', method, '-H', 'Accept: application/vnd.github+json', path, '--input', '-'], { input: JSON.stringify(body) })
  return out.trim() ? JSON.parse(out) : null
}

// 按更新时间倒序翻页，直到更新时间早于 since；只返回 since 之后合并、作者 id 匹配的 PR。
// 翻到上限还没到 since 就报错：宁可这轮不推进水位，也不能把没扫到的当成没有
export async function listMergedByAuthor(repo, authorId, sinceIso, { maxPages = 30 } = {}) {
  const since = Date.parse(sinceIso)
  const found = []
  for (let page = 1; page <= maxPages; page++) {
    const rows = await apiGet(`repos/${repo}/pulls?state=closed&sort=updated&direction=desc&per_page=100&page=${page}`)
    if (!Array.isArray(rows)) throw new Error(`读取 ${repo} 的 PR 列表返回异常`)
    if (rows.length === 0) return found
    for (const pr of rows) {
      if (!pr.merged_at || !pr.merge_commit_sha) continue
      if (pr.user?.id !== authorId) continue
      if (Date.parse(pr.merged_at) < since) continue
      found.push(pr)
    }
    if (Date.parse(rows[rows.length - 1].updated_at) < since || rows.length < 100) return found
  }
  throw new Error(`翻了 ${maxPages} 页仍没扫到 ${sinceIso}，这轮不算扫完`)
}

export const getPull = (repo, n) => apiGet(`repos/${repo}/pulls/${n}`)
export const getCommit = (repo, sha) => apiGet(`repos/${repo}/commits/${sha}`)
export const getBranchHead = async (repo, branch) => (await apiGet(`repos/${repo}/branches/${encodeURIComponent(branch)}`))?.commit?.sha ?? null

export async function listPullFiles(repo, n, { maxPages = 30 } = {}) {
  const files = []
  for (let page = 1; page <= maxPages; page++) {
    const rows = await apiGet(`repos/${repo}/pulls/${n}/files?per_page=100&page=${page}`)
    if (!Array.isArray(rows) || rows.length === 0) break
    files.push(...rows)
    if (rows.length < 100) break
  }
  return files
}

// PR 讨论：review comments（行内评论 + 回复，带 in_reply_to_id）与提交列表（找回帖时刻的 head）。只读
async function pagedGet(path, { maxPages = 10 } = {}) {
  const out = []
  for (let page = 1; page <= maxPages; page++) {
    const rows = await apiGet(`${path}${path.includes('?') ? '&' : '?'}per_page=100&page=${page}`)
    if (!Array.isArray(rows) || rows.length === 0) break
    out.push(...rows)
    if (rows.length < 100) break
  }
  return out
}
export const listPullReviewComments = (repo, n) => pagedGet(`repos/${repo}/pulls/${n}/comments`)
export const listPullCommits = (repo, n) => pagedGet(`repos/${repo}/pulls/${n}/commits`, { maxPages: 3 })
export const listTeamMembers = async (org, team) => (await pagedGet(`orgs/${org}/teams/${team}/members`, { maxPages: 3 })).map((m) => m.login)

// 给 JS/TS 改动文件补上改动前后的完整内容（f.before / f.after），供「只改注释」判断用。
// 前 = merge base，后 = PR head（和 files 接口的 patch 同一对版本）。任何一步失败就不补，判断时按真实改动算
const fileAt = async (repo, path, ref) => {
  const r = await apiGet(`repos/${repo}/contents/${path.split('/').map(encodeURIComponent).join('/')}?ref=${ref}`)
  return r?.encoding === 'base64' ? Buffer.from(r.content, 'base64').toString('utf8') : null
}
// PR 交接文件 docs/pr/<number>/review.md、qa.md（ref = 合并提交）。404 → null；不是普通文件（symlink、目录、子模块，
// 或 contents 接口跟随 symlink 返回了别的路径）→ null，并记一条 env_problems。返回 { review, qa }（各为文本或 null）
export async function getHandoff(repo, number, sha, { health = null, get = apiGet } = {}) {
  const out = {}
  for (const kind of ['review', 'qa']) {
    const path = `docs/pr/${Number(number)}/${kind}.md`
    let r
    try { r = await get(`repos/${repo}/contents/${path}?ref=${sha}`) } catch (err) {
      if (err.httpStatus === 404) { out[kind] = null; continue }
      throw err
    }
    if (r?.type !== 'file' || r.path !== path || r.encoding !== 'base64') {
      health?.env_problems.push({ pr: Number(number), message: `handoff ${path} is not a regular file (type ${Array.isArray(r) ? 'dir' : r?.type ?? 'unknown'}${r?.path && r.path !== path ? `, resolves to ${r.path}` : ''}); ignored` })
      out[kind] = null
      continue
    }
    out[kind] = Buffer.from(r.content, 'base64').toString('utf8')
  }
  return out
}
export async function addFullText(repo, pr, files, { maxFiles = 50 } = {}) {
  const js = files.filter((f) => /\.[cm]?[jt]sx?$/i.test(f.filename))
  if (!js.length || js.length > maxFiles) return files
  try {
    const base = (await apiGet(`repos/${repo}/compare/${pr.base.sha}...${pr.head.sha}`))?.merge_base_commit?.sha
    if (!base) return files
    for (const f of js) {
      const before = f.status === 'added' ? '' : await fileAt(repo, f.previous_filename ?? f.filename, base)
      const after = f.status === 'removed' ? '' : await fileAt(repo, f.filename, pr.head.sha)
      if (typeof before === 'string' && typeof after === 'string') Object.assign(f, { before, after })
    }
  } catch {}
  return files
}

export const createIssue = (repo, { title, body, labels }) => apiSend('POST', `repos/${repo}/issues`, { title, body, labels })
export const commentIssue = (repo, number, body) => apiSend('POST', `repos/${repo}/issues/${number}/comments`, { body })
export const getIssue = (repo, number) => apiGet(`repos/${repo}/issues/${number}`)

// POST 结果不明时回查：只认本账号创建、正文带同一 publicationId 的 issue（列表接口，不用 Search）。
// 翻完所有页才算「没找到」；读取失败直接抛，调用方不得因此补发
export async function findIssueByMarker(repo, creator, marker, sinceIso, { maxPages = 20 } = {}) {
  for (let page = 1; page <= maxPages; page++) {
    const rows = await apiGet(`repos/${repo}/issues?state=all&creator=${encodeURIComponent(creator)}&since=${encodeURIComponent(sinceIso)}&per_page=100&page=${page}`)
    const hit = (rows ?? []).find((i) => !i.pull_request && String(i.body ?? '').includes(marker))
    if (hit) return hit
    if (!Array.isArray(rows) || rows.length < 100) return null
  }
  throw new Error(`回查 issue 超过 ${maxPages} 页仍未翻完，不能断定没发出去`)
}

export async function findCommentByMarker(repo, number, marker, { maxPages = 10 } = {}) {
  for (let page = 1; page <= maxPages; page++) {
    const rows = await apiGet(`repos/${repo}/issues/${number}/comments?per_page=100&page=${page}`)
    const hit = (rows ?? []).find((c) => String(c.body ?? '').includes(marker))
    if (hit) return hit
    if (!Array.isArray(rows) || rows.length < 100) return null
  }
  throw new Error(`回查评论超过 ${maxPages} 页仍未翻完`)
}

// 跨实例去重：按指纹标记在目标仓的 pr-qa issue 里找（含已关闭），返回最新的一条
export async function findIssueByFingerprint(repo, fingerprint, { maxPages = 10 } = {}) {
  const marker = `argus-fingerprint: ${fingerprint}`
  for (let page = 1; page <= maxPages; page++) {
    const rows = await apiGet(`repos/${repo}/issues?state=all&labels=pr-qa&sort=created&direction=desc&per_page=100&page=${page}`)
    const hit = (rows ?? []).find((i) => !i.pull_request && String(i.body ?? '').includes(marker))
    if (hit) return hit
    if (!Array.isArray(rows) || rows.length < 100) return null
  }
  throw new Error(`按指纹回查超过 ${maxPages} 页仍未翻完`)
}

// 同上，但返回全部命中（开放 + 已关闭），供「指纹 + 合并提交」去重判定
export async function findIssuesByMarkerLabel(repo, label, marker, { maxPages = 10 } = {}) {
  const hits = []
  for (let page = 1; page <= maxPages; page++) {
    const rows = await apiGet(`repos/${repo}/issues?state=all&labels=${encodeURIComponent(label)}&sort=created&direction=desc&per_page=100&page=${page}`)
    hits.push(...(rows ?? []).filter((i) => !i.pull_request && String(i.body ?? '').includes(marker)))
    if (!Array.isArray(rows) || rows.length < 100) return hits
  }
  throw new Error(`按指纹回查超过 ${maxPages} 页仍未翻完`)
}

// 日报用：目标仓里全部自动 QA issue（含已关闭），用来给出实时状态
export async function listQaIssues(repo, { maxPages = 5 } = {}) {
  const all = []
  for (let page = 1; page <= maxPages; page++) {
    const rows = await apiGet(`repos/${repo}/issues?state=all&labels=pr-qa&sort=created&direction=desc&per_page=100&page=${page}`)
    all.push(...(rows ?? []).filter((i) => !i.pull_request))
    if (!Array.isArray(rows) || rows.length < 100) return all
  }
  throw new Error(`读取 issue 列表超过 ${maxPages} 页仍未翻完`)
}

export const reopenIssue = (repo, number) => apiSend('PATCH', `repos/${repo}/issues/${number}`, { state: 'open' })

export const currentUser = () => apiGet('user')

// 夜间 QA 的 PR 发现：上海时区某一天 [day 00:00, 次日 00:00)，不限作者。
// 收两类：① 当天合并的 PR；② 当前仍开着、且当天有过更新的 PR。
// 按 updated_at 倒序翻页到早于当天为止；开放 PR 若 updated_at 已晚于当天（之后又被更新），
// 再看 timeline 里是否有落在当天的事件（提交、评论、评审、标签等），没有就不算。
export function shanghaiDayWindow(day) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) throw new Error(`日期格式应为 YYYY-MM-DD：${day}`)
  const start = Date.parse(`${day}T00:00:00+08:00`)
  return { day, startMs: start, endMs: start + 86400_000, startIso: new Date(start).toISOString(), endIso: new Date(start + 86400_000).toISOString() }
}

const inWindow = (iso, w) => { const t = Date.parse(iso ?? ''); return Number.isFinite(t) && t >= w.startMs && t < w.endMs }

async function hadActivityIn(repo, n, w, { maxPages = 10 } = {}) {
  for (let page = 1; page <= maxPages; page++) {
    const rows = await apiGet(`repos/${repo}/issues/${n}/timeline?per_page=100&page=${page}`)
    if (!Array.isArray(rows) || rows.length === 0) return false
    for (const e of rows) {
      const stamps = [e.created_at, e.submitted_at, e.updated_at, e.author?.date, e.committer?.date]
      if (stamps.some((t) => inWindow(t, w))) return true
    }
    if (rows.length < 100) return false
  }
  return false
}

export async function listNightlyPrs(repo, day, { maxPages = 30 } = {}) {
  const w = shanghaiDayWindow(day)
  const found = []
  for (let page = 1; page <= maxPages; page++) {
    const rows = await apiGet(`repos/${repo}/pulls?state=all&sort=updated&direction=desc&per_page=100&page=${page}`)
    if (!Array.isArray(rows)) throw new Error(`读取 ${repo} 的 PR 列表返回异常`)
    for (const pr of rows) {
      if (pr.merged_at && inWindow(pr.merged_at, w)) { found.push({ pr, reason: 'merged' }); continue }
      if (pr.state !== 'open') continue
      if (inWindow(pr.updated_at, w)) { found.push({ pr, reason: 'open-updated' }); continue }
      if (Date.parse(pr.updated_at) >= w.endMs && Date.parse(pr.created_at) < w.endMs && await hadActivityIn(repo, pr.number, w)) found.push({ pr, reason: 'open-updated' })
    }
    if (rows.length < 100 || Date.parse(rows[rows.length - 1].updated_at) < w.startMs) return { window: w, prs: found }
  }
  throw new Error(`翻了 ${maxPages} 页仍没扫到 ${w.startIso}，PR 发现不完整`)
}

// 夜间 QA 去重：在 issue 仓里按 label + 正文指纹标记查（含已关闭），只读
export async function findIssueByMarkerLabel(repo, label, marker, { maxPages = 10 } = {}) {
  for (let page = 1; page <= maxPages; page++) {
    const rows = await apiGet(`repos/${repo}/issues?state=all&labels=${encodeURIComponent(label)}&sort=created&direction=desc&per_page=100&page=${page}`)
    const hit = (rows ?? []).find((i) => !i.pull_request && String(i.body ?? '').includes(marker))
    if (hit) return hit
    if (!Array.isArray(rows) || rows.length < 100) return null
  }
  throw new Error(`按指纹回查超过 ${maxPages} 页仍未翻完`)
}
