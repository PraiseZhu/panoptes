// scripts/bootstrap-box.sh：apt/dpkg 锁被别的进程占着时等待重试，而不是直接失败（假 apt-get，不碰真系统）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, chmodSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

const SCRIPT = resolve(dirname(fileURLToPath(import.meta.url)), '../../scripts/bootstrap-box.sh')

function run({ failTimes, wait = 5, msg = 'E: Could not get lock /var/lib/dpkg/lock-frontend. It is held by process 123 (apt-get)' }) {
  const dir = mkdtempSync(join(tmpdir(), 'argus-aptlock-'))
  const apt = join(dir, 'apt-get'), calls = join(dir, 'calls')
  writeFileSync(apt, `#!/usr/bin/env bash\necho "$*" >> "${calls}"\nn=$(wc -l < "${calls}")\nif (( n <= ${failTimes} )); then echo "${msg}" >&2; exit 100; fi\nexit 0\n`)
  chmodSync(apt, 0o755)
  const r = spawnSync('bash', ['-c', `PANOPTES_BOOTSTRAP_LIB=1 . "${SCRIPT}"; SUDO=""; APT_GET="${apt}"; apt_get install -y foo; echo "rc=$?"`], {
    encoding: 'utf8', env: { ...process.env, PANOPTES_APT_LOCK_WAIT: String(wait), PANOPTES_APT_LOCK_POLL: '0.2', PANOPTES_APT_LOCK_FILES: join(dir, 'nolock') } })
  return { out: r.stdout, err: r.stderr, calls: existsSync(calls) ? readFileSync(calls, 'utf8').trim().split('\n') : [] }
}

test('apt lock held → waits and retries, succeeds once the lock is released', () => {
  const r = run({ failTimes: 2 })
  assert.match(r.out, /rc=0/)
  assert.equal(r.calls.length, 3)
  assert.ok(r.calls.every((c) => /-o DPkg::Lock::Timeout=\d+ install -y foo/.test(c)), r.calls.join('\n'))
  assert.match(r.err, /lock held, retrying/)
})

test('apt lock held past PANOPTES_APT_LOCK_WAIT → gives up with apt-get exit code', () => {
  const r = run({ failTimes: 1000, wait: 1 })
  assert.match(r.out, /rc=100/)
  assert.ok(r.calls.length >= 2 && r.calls.length < 20, String(r.calls.length))
})

test('non-lock apt failure → no retry', () => {
  const r = run({ failTimes: 1, msg: 'E: Unable to locate package foo' })
  assert.match(r.out, /rc=100/); assert.equal(r.calls.length, 1)
})
