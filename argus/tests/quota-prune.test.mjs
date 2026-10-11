// 旧额度计数键清理：只删 gen:* / jev:* / 裸日期，quota:* 和其他键保留；--dry-run 不删
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const root = mkdtempSync(join(tmpdir(), 'argus-quota-prune-'))
process.env.ARGUS_ROOT ??= root
const { openDb, addAiUsage } = await import('../src/db.mjs')
const { pruneLegacyQuotaKeys } = await import('../src/quota.mjs')

test('prune removes only legacy counter keys', () => {
  const db = openDb(join(root, 'p.sqlite'))
  for (const k of ['2026-10-11', 'gen:2026-10-11', 'jev:2026-10-10', 'quota:test:ai-review:2026-10-11', 'quota:production:jev:2026-10-11', 'other']) addAiUsage(db, k, 5)
  const dry = pruneLegacyQuotaKeys(db, { dryRun: true })
  assert.deepEqual(dry.map((r) => r.key), ['2026-10-11', 'gen:2026-10-11', 'jev:2026-10-10'])
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM ai_usage').get().n, 6, 'dry run deletes nothing')
  const removed = pruneLegacyQuotaKeys(db)
  assert.equal(removed.length, 3)
  assert.deepEqual(db.prepare('SELECT day FROM ai_usage ORDER BY day').all().map((r) => r.day), ['other', 'quota:production:jev:2026-10-11', 'quota:test:ai-review:2026-10-11'])
})
