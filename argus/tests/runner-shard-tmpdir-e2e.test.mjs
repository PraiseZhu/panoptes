// 端到端：分片步骤的 step.env 覆盖 TMPDIR 时，各分片的 TMPDIR 仍落在这一步自己的 step-<id>/shard-i 下（运维对 #9 的建议）
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const root = mkdtempSync(join(tmpdir(), 'argus-shardtmp-'))
process.env.ARGUS_ROOT = root
process.env.ARGUS_UNIT_SHARDS = '2'
const { newRunDir, runSteps, cleanupRun } = await import('../src/runner.mjs')
const dirs = []
after(async () => { for (const d of dirs) await cleanupRun(null, d); rmSync(root, { recursive: true, force: true }) })

test('step.env.TMPDIR 覆盖不会把分片带出 step-<id> 目录', async () => {
  const runDir = newRunDir(1, 'shardtmp'); dirs.push(runDir)
  const src = join(runDir, 'src'); mkdirSync(src)
  const out = join(runDir, 'logs')
  const override = join(runDir, 'elsewhere')
  const code = "const fs=require('fs');const s=process.argv[process.argv.length-1].replace(/\\W/g,'_');fs.writeFileSync(require('path').join(process.env.OUT_DIR,'tmp'+s),process.env.TMPDIR)"
  const profile = { steps: [{ id: 'unit', kind: 'product', shard: { max: 2 }, env: { TMPDIR: override, OUT_DIR: out }, cmd: [process.execPath, '-e', code, '--'] }] }
  const res = await runSteps(profile, src, runDir)
  assert.equal(res[0].ok, true, res[0].tail)
  assert.equal(res[0].shards?.count ?? res[0].shards?.shards ?? 2, 2)
  const files = readdirSync(out).filter((f) => f.startsWith('tmp'))
  assert.equal(files.length, 2, `got ${files}`)
  const seen = files.map((f) => readFileSync(join(out, f), 'utf8')).sort()
  for (const [i, t] of seen.entries()) {
    assert.match(t, new RegExp(`/step-unit/shard-${i + 1}$`))
    assert.ok(!t.startsWith(override), `shard TMPDIR escaped to override: ${t}`)
  }
})
