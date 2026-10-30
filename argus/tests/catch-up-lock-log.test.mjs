// --catch-up：拿不到 catch-up.lock 时 exit 0，但写一行 "[catch-up] … skipped"（以前静默退出，跳过的一轮看不出来）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'

const SCRIPT = fileURLToPath(new URL('../../run-nightly.sh', import.meta.url))

test('catch-up lock busy → one log line, exit 0, node never started', () => {
  const qa = mkdtempSync(join(tmpdir(), 'argus-culock-'))
  mkdirSync(join(qa, 'argus-root'), { recursive: true })
  const secrets = join(qa, 's.env')
  writeFileSync(secrets, 'GITHUB_TOKEN=x\nQA_LLM_BASE_URL=http://127.0.0.1:9\nQA_LLM_API_KEY=x\n')
  const lock = join(qa, 'argus-root', 'catch-up.lock')
  // 假 nvm（同 qa-slot.test.mjs）：CI 上没有 nvm，脚本只需 node >= 24 在 PATH 里
  const nvm = join(qa, 'nvm'); mkdirSync(nvm); writeFileSync(join(nvm, 'nvm.sh'), 'nvm() { :; }\n')
  const env = { ...process.env, PANOPTES_NVM_DIR: nvm, QA_HOME: qa, QA_SECRETS_FILE: secrets, PANOPTES_BOOTSTRAP: '0', PANOPTES_TMP_SWEEP: '0', PANOPTES_SEVERITY_PIN_CHECK: '0',
    // even Beijing hour: the odd-hour memory pre-check (run-nightly.sh, 10-30) must not depend on the runner's clock / memory
    PANOPTES_FAKE_BJ_HOUR: '12' }
  // flock 持锁再跑脚本：脚本内 flock -n 8 必然失败
  const r = spawnSync('flock', [lock, SCRIPT, '--catch-up', '--trigger', 'catch-up'], { env, encoding: 'utf8', timeout: 60_000 })
  assert.equal(r.status, 0, r.stderr)
  const lines = r.stdout.split('\n').filter((l) => l.startsWith('[catch-up]'))
  assert.equal(lines.length, 1, r.stdout)
  assert.match(lines[0], /^\[catch-up\] \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+08:00 trigger=catch-up: catch-up\.lock held by another catch-up; skipped$/)
})
