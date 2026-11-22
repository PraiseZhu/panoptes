#!/usr/bin/env node
// quota-sync.mjs — share one Asia/Shanghai day's model-call quota between Devin QA sessions (PraiseZhu/panoptes#311).
//
//   quota-sync.mjs seed   [--day D] [--qa-home DIR]          before a run: raise the argus DB counters to the sum of every
//                                                           pi/out/devin/quota/D/PR-*.json, then remember the counters
//   quota-sync.mjs export --pr N [--day D] [--qa-home DIR]   after a run: add the calls made since seed/last export to
//                                                           pi/out/devin/quota/D/PR-N.json (state-sync.sh push qa ships it)
//
// D defaults to today in Asia/Shanghai, DIR to $QA_HOME or /workspace/mivo-qa. The DB is $DIR/argus-root/app/state/argus.sqlite
// (ARGUS_ROOT=$DIR/argus-root, as run-nightly.sh sets it); counters are read and written only through argus/src/db.mjs.
// PR-N.json: {"pr":N,"day":D,"usage":{"<pool>":{"<kind>":{"calls":c,"tokens":t}}}}. The seed snapshot stays local in
// $DIR/argus-root/devin-quota/D.json. Exit 0 ok · 1 error · 2 usage. Last stdout line: one JSON object.
import { readFileSync, writeFileSync, mkdirSync, readdirSync, existsSync, renameSync } from 'node:fs'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ARGUS_SRC = join(dirname(fileURLToPath(import.meta.url)), '../../argus/src')
const USAGE = 'usage: quota-sync.mjs seed|export [--pr N] [--day YYYY-MM-DD] [--qa-home DIR]'

function toArgs(argv) {
  const [cmd, ...rest] = argv
  const opts = { cmd, pr: null, day: null, qaHome: process.env.QA_HOME || '/workspace/mivo-qa' }
  for (let i = 0; i < rest.length; i += 2) {
    const [flag, value] = [rest[i], rest[i + 1]]
    if (value === undefined) throw new Error(`${flag} needs a value`)
    if (flag === '--pr') opts.pr = value
    else if (flag === '--day') opts.day = value
    else if (flag === '--qa-home') opts.qaHome = value
    else throw new Error(`unknown option ${flag}`)
  }
  if (!['seed', 'export'].includes(cmd)) throw new Error(`unknown command ${cmd ?? '(none)'}`)
  if (opts.pr !== null && !/^[1-9][0-9]*$/.test(opts.pr)) throw new Error(`--pr must be a PR number, got ${opts.pr}`)
  if (cmd === 'export' && opts.pr === null) throw new Error('export needs --pr N')
  opts.day ??= new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(new Date())
  if (!/^\d{4}-\d{2}-\d{2}$/.test(opts.day)) throw new Error(`--day must be YYYY-MM-DD, got ${opts.day}`)
  opts.qaHome = resolve(opts.qaHome)
  return opts
}

function writeJsonAtomic(path, value) {
  mkdirSync(dirname(path), { recursive: true })
  const tmp = `${path}.tmp-${process.pid}`
  writeFileSync(tmp, JSON.stringify(value, null, 2) + '\n')
  renameSync(tmp, path)
}

function addUsage(total, usage) {
  for (const [pool, kinds] of Object.entries(usage ?? {})) {
    for (const [kind, { calls = 0, tokens = 0 } = {}] of Object.entries(kinds ?? {})) {
      total[pool] ??= {}
      total[pool][kind] ??= { calls: 0, tokens: 0 }
      total[pool][kind].calls += Number(calls) || 0
      total[pool][kind].tokens += Number(tokens) || 0
    }
  }
  return total
}

async function main() {
  let opts
  try { opts = toArgs(process.argv.slice(2)) } catch (e) {
    console.error(`quota-sync: ${e.message}\n${USAGE}`)
    console.log(JSON.stringify({ ok: false, error: e.message }))
    return 2
  }
  process.env.ARGUS_ROOT = join(opts.qaHome, 'argus-root')
  const { openDb, aiUsageToday, addAiUsage } = await import(join(ARGUS_SRC, 'db.mjs'))
  const { QUOTA_POOLS, QUOTA_KINDS, quotaCounterKey } = await import(join(ARGUS_SRC, 'quota.mjs'))
  const { DB_PATH } = await import(join(ARGUS_SRC, 'paths.mjs'))
  const quotaDir = join(opts.qaHome, 'pi/out/devin/quota', opts.day)
  const snapPath = join(process.env.ARGUS_ROOT, 'devin-quota', `${opts.day}.json`)
  const db = openDb(DB_PATH)
  const snapshot = () => {
    const out = {}
    for (const pool of QUOTA_POOLS) for (const kind of QUOTA_KINDS) {
      const { calls, tokens } = aiUsageToday(db, quotaCounterKey(pool, kind, opts.day))
      if (calls || tokens) addUsage(out, { [pool]: { [kind]: { calls, tokens } } })
    }
    return out
  }

  if (opts.cmd === 'seed') {
    const files = existsSync(quotaDir) ? readdirSync(quotaDir).filter((f) => /^PR-[0-9]+\.json$/.test(f)).sort() : []
    const total = {}
    for (const f of files) addUsage(total, JSON.parse(readFileSync(join(quotaDir, f), 'utf8')).usage)
    for (const [pool, kinds] of Object.entries(total)) {
      for (const [kind, want] of Object.entries(kinds)) {
        if (!QUOTA_POOLS.includes(pool) || !QUOTA_KINDS.includes(kind)) throw new Error(`unknown quota counter ${pool}/${kind}`)
        const key = quotaCounterKey(pool, kind, opts.day)
        const have = aiUsageToday(db, key)
        for (let i = have.calls; i < want.calls; i++) addAiUsage(db, key, i === have.calls ? Math.max(0, want.tokens - have.tokens) : 0)
      }
    }
    const used = snapshot()
    writeJsonAtomic(snapPath, used)
    console.log(JSON.stringify({ ok: true, cmd: 'seed', day: opts.day, files: files.length, used }))
    return 0
  }

  if (!existsSync(snapPath)) throw new Error(`no seed snapshot ${snapPath}: run "quota-sync.mjs seed --day ${opts.day}" first`)
  const before = JSON.parse(readFileSync(snapPath, 'utf8'))
  const now = snapshot()
  const delta = {}
  for (const [pool, kinds] of Object.entries(now)) {
    for (const [kind, n] of Object.entries(kinds)) {
      const b = before[pool]?.[kind] ?? { calls: 0, tokens: 0 }
      if (n.calls > b.calls) addUsage(delta, { [pool]: { [kind]: { calls: n.calls - b.calls, tokens: Math.max(0, n.tokens - b.tokens) } } })
    }
  }
  const prPath = join(quotaDir, `PR-${opts.pr}.json`)
  const own = existsSync(prPath) ? JSON.parse(readFileSync(prPath, 'utf8')).usage : {}
  const usage = addUsage(addUsage({}, own), delta)
  writeJsonAtomic(prPath, { pr: Number(opts.pr), day: opts.day, usage })
  writeJsonAtomic(snapPath, now)
  console.log(JSON.stringify({ ok: true, cmd: 'export', day: opts.day, pr: Number(opts.pr), file: prPath, added: delta }))
  return 0
}

main().then((code) => { process.exitCode = code }, (e) => {
  console.error(`quota-sync: ${e.message}`)
  console.log(JSON.stringify({ ok: false, error: e.message }))
  process.exitCode = 1
})
