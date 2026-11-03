// 11-03 并发计划 §2.6：run-nightly.sh 退出时调用电脑运维的清理钩子，只 dryrun；touch $QA_HOME/qa-cleanup-hook-off 关闭
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, writeFileSync, chmodSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'

const SCRIPT = fileURLToPath(new URL('../../run-nightly.sh', import.meta.url))
const src = readFileSync(SCRIPT, 'utf8')
const fn = src.slice(src.indexOf('QA_CLEANUP_HOOK='), src.indexOf('# end qa_cleanup_hook'))

function setup () {
  const d = mkdtempSync(join(tmpdir(), 'qa-hook-'))
  const hook = join(d, 'hook.sh')
  writeFileSync(hook, `#!/bin/bash\necho "mode=$QA_HOOK_MODE dir=$1 pgid=$2" >>"${d}/calls"\n`); chmodSync(hook, 0o755)
  const run = () => execFileSync('bash', ['-c', `${fn}\nRUN_TMP=${d}/run; qa_cleanup_hook; echo rc=$?`],
    { encoding: 'utf8', env: { ...process.env, QA_HOME: d, PANOPTES_QA_CLEANUP_HOOK: hook, QA_HOOK_MODE: 'enforce' } })
  return { d, run }
}

test('exit trap calls the hook before RUN_TMP is removed', () => {
  const line = src.split('\n').filter(l => l.startsWith("EXTRA_EXIT_CLEANUP='slot_release_priority")).pop()
  assert.ok(line.indexOf('qa_cleanup_hook') > 0 && line.indexOf('qa_cleanup_hook') < line.indexOf('rm -rf'))
})

test('hook gets the run dir and this process group, always in dryrun even if the env says enforce', () => {
  const { d, run } = setup()
  assert.match(run(), /rc=0/)
  const call = readFileSync(join(d, 'calls'), 'utf8').trim()
  assert.match(call, new RegExp(`^mode=dryrun dir=${d}/run pgid=\\d+$`))
})

test('flag file off, or no hook installed: nothing is called and exit is not affected', () => {
  const { d, run } = setup()
  writeFileSync(join(d, 'qa-cleanup-hook-off'), '')
  assert.match(run(), /rc=0/)
  assert.equal(existsSync(join(d, 'calls')), false)
  const out = execFileSync('bash', ['-c', `${fn}\nRUN_TMP=/x; qa_cleanup_hook; echo rc=$?`],
    { encoding: 'utf8', env: { ...process.env, QA_HOME: d, PANOPTES_QA_CLEANUP_HOOK: join(d, 'missing.sh') } })
  assert.match(out, /rc=0/)
})
