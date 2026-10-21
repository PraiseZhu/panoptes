#!/usr/bin/env node
// PR 作者 -> 交接仓 的唯一来源：report/handoff-routes.json（$MIVO_QA_HANDOFF_ROUTES 可覆盖路径）。
// 用法（只读，不发任何东西）：node report/handoff-routes.mjs <PR号|作者login>...
import { readFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const FILE = process.env.MIVO_QA_HANDOFF_ROUTES || fileURLToPath(new URL('./handoff-routes.json', import.meta.url))
export function loadRoutes(file = FILE) { return JSON.parse(readFileSync(file, 'utf8')) }
export function routeFor(login, cfg = loadRoutes()) {
  const r = cfg.routes.find(x => x.authors.includes(login))
  return r ? { repo: r.repo, owner: r.owner, cc: r.cc?.[login] || [] } : null
}
export const routedRepos = (cfg = loadRoutes()) => cfg.routes.map(r => r.repo)
export function overdueCc(cfg = loadRoutes()) {
  const out = {}
  for (const r of cfg.routes) for (const [k, v] of Object.entries(r.cc || {})) out[k] = v
  return out
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const cfg = loadRoutes()
  for (const a of process.argv.slice(2)) {
    let login = a, pr = null
    if (/^\d+$/.test(a)) { pr = Number(a); login = execFileSync('gh', ['pr', 'view', a, '-R', cfg.target_repo, '--json', 'author', '-q', '.author.login'], { encoding: 'utf8' }).trim() }
    console.log(JSON.stringify({ pr, author: login, route: routeFor(login, cfg) }))
  }
}
