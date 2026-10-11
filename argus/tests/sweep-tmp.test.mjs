// scripts/sweep-tmp.sh：只删 ROOT 下 24 小时没动过、属于自己、没有活进程占用的 argus-* 目录
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, existsSync, utimesSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { spawn, execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const SWEEP = join(dirname(fileURLToPath(import.meta.url)), '../../scripts/sweep-tmp.sh')
const old = (p) => { const t = new Date(Date.now() - 48 * 3600_000); utimesSync(p, t, t) }

test('sweep removes old idle argus-* dirs only (keeps recent, in-use, non-argus)', async () => {
  const root = mkdtempSync(join(tmpdir(), 'sweep-root-'))
  const mk = (name, { aged = true, file = 'f' } = {}) => { const d = join(root, name); mkdirSync(d); writeFileSync(join(d, file), 'x'.repeat(1000)); if (aged) { old(join(d, file)); old(d) }; return d }
  const stale = mk('argus-stale-1'), recent = mk('argus-recent-1', { aged: false }), held = mk('argus-held-1'), other = mk('other-old')
  const mixed = mk('argus-mixed-1'); writeFileSync(join(mixed, 'new'), 'y'); old(mixed) // dir old, one file new → keep
  const sleeper = spawn('sleep', ['30'], { cwd: held, stdio: 'ignore' })
  await new Promise((r) => setTimeout(r, 200))
  try {
    const out = JSON.parse(execFileSync('bash', [SWEEP, '--root', root], { encoding: 'utf8' }).trim())
    assert.equal(out.removed, 1)
    assert.ok(out.freed_bytes >= 1000)
    assert.equal(out.skipped_in_use, 1)
    assert.equal(out.skipped_recent, 2)
    assert.equal(existsSync(stale), false)
    for (const d of [recent, held, other, mixed]) assert.ok(existsSync(d), d)
    // dry run removes nothing
    const dry = JSON.parse(execFileSync('bash', [SWEEP, '--root', root, '--dry-run', '--max-age-hours', '0'], { encoding: 'utf8' }).trim())
    assert.equal(dry.dry_run, true)
    assert.ok(existsSync(recent))
  } finally { sleeper.kill() }
})
