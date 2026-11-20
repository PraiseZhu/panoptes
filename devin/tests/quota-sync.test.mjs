// quota-sync.mjs tests (PraiseZhu/panoptes#311): seed sums every PR's quota file into the argus DB by (pool, kind);
// export writes only the calls made since seed. Counters are checked through argus/src/db.mjs.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const QUOTA = join(HERE, '../bin/quota-sync.mjs')
const { openDb, aiUsageToday, addAiUsage } = await import(join(HERE, '../../argus/src/db.mjs'))
const { quotaCounterKey } = await import(join(HERE, '../../argus/src/quota.mjs'))
const DAY = '2026-11-19'

function setup() {
  const qaHome = mkdtempSync(join(tmpdir(), 'quota-sync-'))
  const dir = join(qaHome, 'pi/out/devin/quota', DAY)
  mkdirSync(dir, { recursive: true })
  const put = (pr, usage) => writeFileSync(join(dir, `PR-${pr}.json`), JSON.stringify({ pr, day: DAY, usage }))
  put(2464, { production: { 'ai-test': { calls: 2, tokens: 100 }, jev: { calls: 1, tokens: 10 } } })
  put(2465, { production: { 'ai-test': { calls: 3, tokens: 30 }, 'ai-review': { calls: 1, tokens: 5 } } })
  put(2466, { production: { jev: { calls: 4, tokens: 40 } }, test: { 'ai-test': { calls: 1, tokens: 1 } } })
  return { qaHome, dir, dbPath: join(qaHome, 'argus-root/app/state/argus.sqlite') }
}
const quota = (qaHome, ...args) => {
  const r = spawnSync(process.execPath, [QUOTA, ...args, '--day', DAY, '--qa-home', qaHome], { encoding: 'utf8', env: { ...process.env, ARGUS_ROOT: '' } })
  return { status: r.status, last: JSON.parse(r.stdout.trim().split('\n').at(-1)), stderr: r.stderr }
}
const used = (db, pool, kind) => ({ ...aiUsageToday(db, quotaCounterKey(pool, kind, DAY)) })

test('seed: argus DB counters equal the per-(pool, kind) sum of the 3 PR files', () => {
  const { qaHome, dbPath } = setup()
  const r = quota(qaHome, 'seed')
  assert.equal(r.status, 0, r.stderr)
  assert.equal(r.last.files, 3)
  const db = openDb(dbPath)
  assert.deepEqual(used(db, 'production', 'ai-test'), { calls: 5, tokens: 130 })
  assert.deepEqual(used(db, 'production', 'jev'), { calls: 5, tokens: 50 })
  assert.deepEqual(used(db, 'production', 'ai-review'), { calls: 1, tokens: 5 })
  assert.deepEqual(used(db, 'test', 'ai-test'), { calls: 1, tokens: 1 })
  assert.deepEqual(used(db, 'production', 'pr-summary'), { calls: 0, tokens: 0 })
  db.close()
  assert.equal(quota(qaHome, 'seed').status, 0)
  const again = openDb(dbPath)
  assert.deepEqual(used(again, 'production', 'ai-test'), { calls: 5, tokens: 130 }, 'seeding twice must not double count')
  again.close()
  rmSync(qaHome, { recursive: true, force: true })
})

test('export --pr 2501: PR-2501.json records only the 2 calls made after seed', () => {
  const { qaHome, dir, dbPath } = setup()
  assert.equal(quota(qaHome, 'seed').status, 0)
  const db = openDb(dbPath)
  addAiUsage(db, quotaCounterKey('production', 'ai-test', DAY), 7)
  addAiUsage(db, quotaCounterKey('production', 'jev', DAY), 3)
  db.close()
  const r = quota(qaHome, 'export', '--pr', '2501')
  assert.equal(r.status, 0, r.stderr)
  const file = JSON.parse(readFileSync(join(dir, 'PR-2501.json'), 'utf8'))
  assert.deepEqual(file, { pr: 2501, day: DAY, usage: { production: { 'ai-test': { calls: 1, tokens: 7 }, jev: { calls: 1, tokens: 3 } } } })
  const calls = Object.values(file.usage).flatMap((k) => Object.values(k)).reduce((n, u) => n + u.calls, 0)
  assert.equal(calls, 2)
  assert.equal(quota(qaHome, 'export', '--pr', '2501').status, 0)
  assert.deepEqual(JSON.parse(readFileSync(join(dir, 'PR-2501.json'), 'utf8')), file, 'a second export without new calls adds nothing')
  rmSync(qaHome, { recursive: true, force: true })
})

test('export adds this session\'s calls to the PR\'s earlier tries of the day', () => {
  const { qaHome, dir, dbPath } = setup()
  assert.equal(quota(qaHome, 'seed').status, 0)
  const db = openDb(dbPath)
  addAiUsage(db, quotaCounterKey('production', 'ai-test', DAY), 1)
  db.close()
  assert.equal(quota(qaHome, 'export', '--pr', '2464').status, 0)
  const usage = JSON.parse(readFileSync(join(dir, 'PR-2464.json'), 'utf8')).usage
  assert.deepEqual(usage, { production: { 'ai-test': { calls: 3, tokens: 101 }, jev: { calls: 1, tokens: 10 } } })
  rmSync(qaHome, { recursive: true, force: true })
})

test('usage errors and export before seed fail visibly', () => {
  const qaHome = mkdtempSync(join(tmpdir(), 'quota-sync-'))
  assert.equal(quota(qaHome, 'export').status, 2)
  assert.equal(quota(qaHome, 'bogus').status, 2)
  const r = quota(qaHome, 'export', '--pr', '9')
  assert.equal(r.status, 1)
  assert.match(r.stderr, /no seed snapshot/)
  assert.equal(existsSync(join(qaHome, `pi/out/devin/quota/${DAY}/PR-9.json`)), false)
  rmSync(qaHome, { recursive: true, force: true })
})
