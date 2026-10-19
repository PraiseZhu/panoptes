// scripts/bootstrap-box.sh：只有规范路径的 checkout 才安装共享 cron；worktree 运行跳过（cron 文件指向临时目录，不碰 /etc）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, readFileSync, mkdirSync, copyFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

const SCRIPTS = resolve(dirname(fileURLToPath(import.meta.url)), '../../scripts')

function run(canonicalIsSelf) {
  const dir = mkdtempSync(join(tmpdir(), 'argus-bootcanon-'))
  const wt = join(dir, 'wt'), canon = join(dir, 'canonical'), cron = join(dir, 'panoptes-catch-up')
  mkdirSync(join(wt, 'scripts'), { recursive: true }); mkdirSync(canon)
  copyFileSync(join(SCRIPTS, 'catch-up.cron'), join(wt, 'scripts/catch-up.cron'))
  writeFileSync(cron, 'ORIGINAL\n')
  const r = spawnSync('bash', ['-c', `PANOPTES_BOOTSTRAP_LIB=1 . "${SCRIPTS}/bootstrap-box.sh"; SUDO=""; install_catchup_timer; echo "rc=$?"`], {
    encoding: 'utf8',
    env: { ...process.env, PANOPTES_DIR: wt, PANOPTES_CANONICAL_DIR: canonicalIsSelf ? wt : canon, PANOPTES_CRON_FILE: cron,
      PANOPTES_BOOTSTRAP: '0', PANOPTES_CATCHUP_TIMER: '0' } })
  return { out: r.stdout, err: r.stderr, cron: readFileSync(cron, 'utf8'), wt }
}

test('worktree run (PANOPTES_DIR != canonical) leaves the shared cron untouched and logs the skip', () => {
  const r = run(false)
  assert.match(r.out, /rc=0/)
  assert.equal(r.cron, 'ORIGINAL\n')
  assert.match(r.err, /not the canonical checkout.*skipping/)
})

test('canonical checkout installs the cron (proves the injected target is what gets written)', () => {
  const r = run(true)
  assert.match(r.out, /rc=0/)
  assert.notEqual(r.cron, 'ORIGINAL\n')
  assert.ok(r.cron.includes(r.wt), r.cron)
})

test('--check from a worktree also logs the skip before checking (no exit before the log)', () => {
  const dir = mkdtempSync(join(tmpdir(), 'argus-bootcanon-'))
  const wt = join(dir, 'wt'), canon = join(dir, 'canonical'), cron = join(dir, 'panoptes-catch-up')
  mkdirSync(wt); mkdirSync(canon); writeFileSync(cron, 'ORIGINAL\n')
  const r = spawnSync('bash', [join(SCRIPTS, 'bootstrap-box.sh'), '--check'], {
    encoding: 'utf8',
    env: { ...process.env, PANOPTES_DIR: wt, PANOPTES_CANONICAL_DIR: canon, PANOPTES_CRON_FILE: cron, QA_HOME: dir,
      PANOPTES_BOOTSTRAP: '0', PANOPTES_CATCHUP_TIMER: '0' } })
  assert.ok(r.status === 0 || r.status === 2, `exit ${r.status}: ${r.stderr}`)
  assert.match(r.stderr, /not the canonical checkout.*skipping/)
  assert.equal(readFileSync(cron, 'utf8'), 'ORIGINAL\n')
})
