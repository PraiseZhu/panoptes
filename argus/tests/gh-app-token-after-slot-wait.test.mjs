// 11-04 P0 (PR-1789 catch-up 401): run-nightly.sh must resolve the App token AFTER the QA slot wait, not only before it
// (installation tokens live 1 h; --wait-lock queues up to 4 h). Mock ghtoken + a held nightly.lock; no real token, no network.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const SCRIPT = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'run-nightly.sh')

test('run-nightly.sh re-resolves the App token once the slot wait is over', async () => {
  const root = mkdtempSync(join(tmpdir(), 'argus-tok-wait-'))
  const qa = join(root, 'qa'), st = join(root, 'pi'), argusRoot = join(qa, 'argus-root')
  mkdirSync(join(argusRoot, 'logs'), { recursive: true }); mkdirSync(st)
  writeFileSync(join(st, 'gh-app-enabled'), ''); writeFileSync(join(st, 'gh-app-nightly-enabled'), '')
  const nvm = join(root, 'nvm'); mkdirSync(nvm); writeFileSync(join(nvm, 'nvm.sh'), 'nvm() { :; }\n')
  const secrets = join(root, 'secrets.env'); writeFileSync(secrets, 'GITHUB_TOKEN=x\nQA_LLM_BASE_URL=http://127.0.0.1:9\nQA_LLM_API_KEY=x\n')
  const released = join(root, 'released'), calls = join(root, 'calls.log')
  const mock = join(root, 'ghtoken.py')
  writeFileSync(mock, `import os, sys\nopen(${JSON.stringify(calls)}, "a").write(("after" if os.path.exists(${JSON.stringify(released)}) else "before") + " " + sys.argv[2] + "\\n")\nprint("ghs_mock")\n`)
  // hold the slot for ~3 s, then mark the release
  const lock = join(argusRoot, 'nightly.lock')
  const holder = spawn('bash', ['-c', `exec 9>"${lock}"; flock 9; sleep 3; touch "${released}"; exec 9>&-`], { stdio: 'ignore' })
  await new Promise((r) => setTimeout(r, 500))
  const r = await new Promise((resolve) => {
    const c = spawn('bash', [SCRIPT, '--date', '2026-10-27', '--wait-lock', '30'], { env: { ...process.env, QA_HOME: qa, ARGUS_ROOT: argusRoot, PI_STATE: st,
      PANOPTES_GHTOKEN: mock, PANOPTES_NVM_DIR: nvm, QA_SECRETS_FILE: secrets, PANOPTES_BOOTSTRAP: '0', PANOPTES_TMP_SWEEP: '0', PANOPTES_TEST: '1',
      PANOPTES_GATE_DRYRUN: '1', PI_TEST_MEM_AVAIL_MB: '9000', PANOPTES_MEM_POLL_S: '1', PANOPTES_MEM_WAIT_S: '1' } })
    let err = ''; c.stderr.on('data', (d) => { err += d })
    c.on('exit', (status) => resolve({ status, err }))
  })
  holder.kill()
  assert.equal(r.status, 0, r.err)
  const log = existsSync(calls) ? readFileSync(calls, 'utf8') : ''
  assert.match(log, /^before xindong\/mivo-canvas-plugin$/m)
  assert.match(log, /^after xindong\/mivo-canvas-plugin$/m, `no token resolved after the slot wait:\n${log}`)
  assert.match(log, /^after PraiseZhu\/pr-qa-agent-deploy$/m)
})
