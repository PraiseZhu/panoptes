// scripts/bootstrap-box.sh 装出来的 QA 环境也得自己固定 umask。
// 它装的 /etc/cron.d/panoptes-catch-up 由 cron 启动，cron 继承盒子的 umask（本机 0002），于是 run-nightly.sh 记下的
// inherited umask 是 0002 → system_health 每次都记一条 env_problem，报告一律 degraded（PR #1377 / #1380：
// "inherited umask was 0002 (expected 0022) … the QA environment should be fixed"）。
// 这里只验装出去的 cron 任务：以 umask 002 启动时，任务里的 run-nightly.sh 拿到的必须是 0022，且下游 checkUmask 不再记 env_problem。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, cpSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

const SCRIPTS = resolve(dirname(fileURLToPath(import.meta.url)), '../../scripts')
const NIGHTLY = new URL('../src/nightly.mjs', import.meta.url).href

// 临时的「规范 checkout」：真实的 scripts/（fix 可能加启动脚本）+ 一个 run-nightly.sh 桩，
// 桩只做 run-nightly.sh 开头那件事——记下 cron 递给它的 umask 和参数。
function installCatchupCron() {
  const dir = mkdtempSync(join(tmpdir(), 'argus-bootumask-'))
  const wt = join(dir, 'panoptes'), cron = join(dir, 'panoptes-catch-up'), qaHome = join(dir, 'qa')
  const stubLog = join(dir, 'run-nightly-stub.log')
  cpSync(SCRIPTS, join(wt, 'scripts'), { recursive: true })
  writeFileSync(join(wt, 'run-nightly.sh'),
    '#!/bin/bash\n# stands in for run-nightly.sh: record the umask cron handed it\n{ printf \'umask=%s\\n\' "$(umask)"; printf \'args=%s\\n\' "$*"; } > "$PANOPTES_TEST_STUB_LOG"\n',
    { mode: 0o755 })
  mkdirSync(join(qaHome, 'argus-root/logs'), { recursive: true })
  writeFileSync(cron, 'ORIGINAL\n')
  const r = spawnSync('bash', ['-c', `PANOPTES_BOOTSTRAP_LIB=1 . "${SCRIPTS}/bootstrap-box.sh"; SUDO=""; install_catchup_timer; echo "rc=$?"`], {
    encoding: 'utf8',
    env: { ...process.env, PANOPTES_DIR: wt, PANOPTES_CANONICAL_DIR: wt, PANOPTES_CRON_FILE: cron, QA_HOME: qaHome,
      PANOPTES_TOOLS: join(dir, 'tools'), PANOPTES_BOOTSTRAP: '0', PANOPTES_CATCHUP_TIMER: '0' } })
  return { dir, wt, cron, qaHome, stubLog, install: r, text: existsSync(cron) ? readFileSync(cron, 'utf8') : '' }
}

// cron 起任务的方式：SHELL -c '<command>'（cron.d 里 SHELL=/bin/bash），umask 来自 cron 守护进程
function runInstalledJob(job, daemonUmask) {
  const line = job.text.split('\n').find((l) => l.trim() && !l.trimStart().startsWith('#') && !/^[A-Za-z_][A-Za-z0-9_]*=/.test(l.trim()))
  assert.ok(line, `${job.cron}: no cron job line in ${JSON.stringify(job.text)}`)
  const cmd = /^(?:\S+\s+){6}([\s\S]*)$/.exec(line)
  assert.ok(cmd, `${job.cron}: unexpected cron line ${JSON.stringify(line)}`)
  const r = spawnSync('bash', ['-c', `umask ${daemonUmask}; exec bash -c "$1"`, '_', cmd[1]], {
    encoding: 'utf8',
    env: { PATH: process.env.PATH, HOME: job.qaHome, SHELL: '/bin/bash', PANOPTES_TEST_STUB_LOG: job.stubLog } })
  const log = existsSync(job.stubLog) ? readFileSync(job.stubLog, 'utf8') : null
  return { ...r, log, umask: log && /umask=(\d+)/.exec(log)?.[1], args: log && /args=([\s\S]*)/.exec(log)?.[1].trim() }
}

test('bootstrap installs the catch-up cron so run-nightly.sh starts under umask 0022 even when cron runs with umask 002', () => {
  const job = installCatchupCron()
  assert.match(job.install.stdout, /rc=0/, job.install.stderr)
  assert.notEqual(job.text, 'ORIGINAL\n', 'install_catchup_timer did not write the cron file')
  const ran = runInstalledJob(job, '002')
  assert.ok(ran.log != null, `run-nightly.sh never ran; status ${ran.status} stderr ${ran.stderr}`)
  // 任务本身不能被固定 umask 改掉：还是那个 catch-up 调用
  assert.match(ran.args ?? '', /--catch-up\b/)
  assert.match(ran.args ?? '', /--trigger catch-up\b/)
  assert.equal(ran.umask, '0022', `run-nightly.sh inherited umask ${ran.umask} from cron; the QA environment bootstrap installs must pin 0022 (got ${ran.umask})`)
})

test('the inherited umask of the installed job does not add an env_problem to system_health', async () => {
  const job = installCatchupCron()
  const ran = runInstalledJob(job, '002')
  assert.ok(ran.umask, `run-nightly.sh never ran; status ${ran.status} stderr ${ran.stderr}`)
  const { checkUmask } = await import(NIGHTLY)
  const health = { environment: { umask: '0022', umask_inherited: ran.umask }, env_problems: [] }
  checkUmask(health)
  assert.deepEqual(health.env_problems, [], `inherited umask ${ran.umask} still degrades every report (PR #1377/#1380)`)
})
