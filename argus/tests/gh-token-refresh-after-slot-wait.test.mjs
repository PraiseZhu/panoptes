// 11-05 04:12（北京）补测 PR #1789：stage=discovery 的
//   gh api -H Accept: application/vnd.github+json repos/xindong/mivo-canvas-plugin/pulls?state=all&sort=updated&direction=desc&per_page=100&page=1
//   → gh: Bad credentials (HTTP 401)
// （证据 reports/catch-up/PR-1789.json system_health.qa_tool_failures[0]；同一轮 pipeline 里 getPull 也是同一条 401）
//
// 现场（read-only 复读：argus-root/logs/catch-up-20261105T030705.log + control.log + nightly-20261105T041232.log）：
// 补测父进程 03:07:05 起，子运行 run-nightly.sh --pr 1789 03:07:06 起，先
//   run-nightly.sh:289  gh_app_env …            ← 在这里铸 App 安装令牌（scripts/gh-app-env.sh:44 导出 GH_TOKEN/PRAISE_QA_GH_TOKEN）
// 然后才在 run-nightly.sh:357-364 排队等 QA 槽："slot wait start" → "slot acquired … waited_s 3926"（3926 s = 65 分 26 秒）。
// 安装令牌活 1 小时（pi/bin/ghtoken.py:20 MARGIN_S 只决定「剩余不足 10 分钟就重铸」），所以 04:12:33 discovery 第一次 gh 调用时，
// 手上那个令牌已经过期约 5 分半 → 401。同一个 PR 30 秒后由 on-merge 的 --priority 新进程重跑（control.log 20:13:01Z）就一切正常，
// 因为新进程重新铸了一次 —— 也就是说这一轮什么都没测成，纯粹是「等锁期间凭据过期」。
//
// 缺陷：凭据是在等锁**之前**铸的，等完锁之后没有任何重铸/刷新点；run-nightly.sh 全脚本只有一处 gh_app_env（第 289 行），
// 而等锁（--wait-lock 默认 14400 s = 4 h）在它之后。等锁时间超过令牌剩余寿命，后面的 GitHub 工作就必然拿着过期令牌起跑，
// 然后被 argus/src/nightly.mjs:1782 当成 stage=discovery 的 QA 工具故障记进报告（这张 infrafix ticket 的来源）。
// 正确行为（这里断言的）：开始做 GitHub 工作的那个进程（node src/cli.mjs nightly）拿到的 GH_TOKEN 必须是在等锁之后铸的。
// 修法不限：等锁后重跑一次 gh_app_env（App-only，见第二个用例：不能把 PAT 带回来）、把铸令牌挪到等锁之后、或让等待期间就刷新。
//
// 这一整套都在一次性的假盒子里跑：临时 QA_HOME / PI_STATE / 假 nvm / 假 ghtoken（PANOPTES_GHTOKEN）/ 假 node（只记录 argv 与环境，
// 不跑任何 QA 步骤）/ 假 meminfo；不停机、不联网、不碰 /workspace/mivo-qa、不碰真 cron —— 与 tests/catch-up-lock-log.test.mjs 同一套做法。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync, chmodSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')   // panoptes checkout（run-nightly.sh / scripts/ / argus/）
const SCRIPT = join(ROOT, 'run-nightly.sh')
const WAIT_S = 3          // 假盒子里持锁 3 s：等锁瞬时完成的话这个用例就失去意义（下面有 waited_s 前置断言）

const root = mkdtempSync(join(tmpdir(), 'argus-token-wait-'))
after(() => rmSync(root, { recursive: true, force: true }))

const paths = {
  qa: join(root, 'qa'), pi: join(root, 'pi'), bin: join(root, 'bin'), nvm: join(root, 'nvm'), fake: join(root, 'fake'),
  mintLog: join(root, 'mints.jsonl'), nodeLog: join(root, 'node.jsonl'),
}

function fakeBox() {
  for (const d of [join(paths.qa, 'argus-root', 'logs'), paths.pi, paths.bin, paths.nvm, paths.fake, join(root, 'tmp')]) mkdirSync(d, { recursive: true })
  // G4 的 App-only 开关：两个 flag 都在 → run-nightly.sh 只走安装令牌，并且把 PAT 变体（ghp_/github_pat_…）从环境里清掉
  for (const f of ['gh-app-enabled', 'gh-app-nightly-enabled']) writeFileSync(join(paths.pi, f), '')
  writeFileSync(join(paths.qa, 'secrets.env'), 'GITHUB_TOKEN=ghp_notarealtoken\nQA_LLM_BASE_URL=http://127.0.0.1:9\nQA_LLM_API_KEY=x\n')
  // 内存门：假 meminfo + PI_TEST_MEM_AVAIL_MB 让 slot_mem_gate 直接判 normal（不等待、不 skip），与机器真实内存无关
  writeFileSync(join(paths.qa, 'meminfo'), 'MemAvailable: 9000000 kB\nSwapFree: 6000000 kB\nSwapTotal: 8000000 kB\n')
  // 假 nvm：脚本只需要一个 node >= 24 的答案，假 node 会替它答
  writeFileSync(join(paths.nvm, 'nvm.sh'), 'nvm() { :; }\n')
  // 假 ghtoken：每次被调用记一行（时间 + 第几个令牌 + 目标仓）并给出一个可区分的 ghs_ 令牌；真令牌/真 App key 全程不参与
  writeFileSync(join(paths.fake, 'ghtoken.py'), `#!/usr/bin/env python3
import json, os, sys, time
log = os.environ["ARGUS_TEST_MINT_LOG"]
n = (sum(1 for _ in open(log)) if os.path.exists(log) else 0) + 1
tok = "ghs_" + str(n) + "x" * 35
with open(log, "a") as f:
    f.write(json.dumps({"n": n, "token": tok, "at": time.time(), "repo": sys.argv[2] if len(sys.argv) > 2 else None}) + "\\n")
print(tok)
`)
  // 假 node：Node 24 探测答 0；真正那条 `src/cli.mjs nightly` 只记录「起跑时刻 + argv + 关键环境变量」，不做任何 QA 工作
  writeFileSync(join(paths.bin, 'node'), `#!/usr/bin/env python3
import json, os, sys, time
argv = sys.argv[1:]
if "-e" in argv:
    sys.exit(0)
leaks = sorted(k for k, v in os.environ.items() if str(v).startswith(("ghp_", "github_pat_", "gho_", "ghu_")))
with open(os.environ["ARGUS_TEST_NODE_LOG"], "a") as f:
    f.write(json.dumps({"at": time.time(), "argv": argv,
                        "GH_TOKEN": os.environ.get("GH_TOKEN"), "PRAISE_QA_GH_TOKEN": os.environ.get("PRAISE_QA_GH_TOKEN"),
                        "GITHUB_TOKEN": os.environ.get("GITHUB_TOKEN"), "pat_like_env": leaks}) + "\\n")
`)
  for (const f of [join(paths.bin, 'node'), join(paths.fake, 'ghtoken.py')]) chmodSync(f, 0o755)
}

// 等锁：另起一个进程持住 nightly.lock WAIT_S 秒（拿到锁后先写 ready 标记，测试等它出现再起 run-nightly.sh）；
// run-nightly.sh 在这段时间里排队（--wait-lock），等完才 exec node
function runNightly({ holdLockS = WAIT_S } = {}) {
  const lock = join(paths.qa, 'argus-root', 'nightly.lock')
  const ready = join(root, 'lock-held')
  const holder = spawn('bash', ['-c', 'exec 9>"$LOCK" || exit 1; flock 9 || exit 1; : > "$READY"; sleep "$HOLD"'], {
    stdio: 'ignore', env: { ...process.env, LOCK: lock, READY: ready, HOLD: String(holdLockS) },
  })
  const started = Date.now()
  while (!existsSync(ready) && Date.now() - started < 10_000) spawnSync('sleep', ['0.05'])
  const r = spawnSync('bash', [SCRIPT, '--trigger', 'manual', '--wait-lock', '120'], {
    encoding: 'utf8',
    timeout: 120_000,
    env: {
      PATH: `${paths.bin}:${process.env.PATH}`,     // 假 node 在最前：脚本不再拉起真的 argus
      HOME: root, TMPDIR: join(root, 'tmp'),
      QA_HOME: paths.qa, QA_SECRETS_FILE: join(paths.qa, 'secrets.env'), PI_STATE: paths.pi,
      PANOPTES_DIR: ROOT, PANOPTES_NVM_DIR: paths.nvm,
      PANOPTES_BOOTSTRAP: '0', PANOPTES_TMP_SWEEP: '0', PANOPTES_SEVERITY_PIN_CHECK: '0', PANOPTES_QA_CLEANUP_HOOK: join(root, 'no-hook'),
      PANOPTES_MEMINFO: join(paths.qa, 'meminfo'), PANOPTES_TEST: '1', PI_TEST_MEM_AVAIL_MB: '9000',
      PANOPTES_GHTOKEN: join(paths.fake, 'ghtoken.py'), ARGUS_TEST_MINT_LOG: paths.mintLog, ARGUS_TEST_NODE_LOG: paths.nodeLog,
    },
  })
  holder.kill()
  return r
}

const readJsonl = (f) => (existsSync(f) ? readFileSync(f, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l)) : [])
const isNightlyRun = (r) => r.argv.some((a) => a.endsWith('cli.mjs')) && r.argv.includes('nightly')

let cached = null
function scenario() {
  if (cached) return cached
  fakeBox()
  const r = runNightly()
  const control = readJsonl(join(paths.qa, 'argus-root', 'logs', 'control.log'))
  const acquired = control.find((e) => e.msg === 'run-nightly slot acquired') ?? null
  cached = { r, control, acquired, mints: readJsonl(paths.mintLog), nodes: readJsonl(paths.nodeLog) }
  return cached
}

test('等 QA 槽超过安装令牌寿命后，做 GitHub 工作的 nightly 进程不能拿等锁前铸的令牌起跑（PR-1789 的 401 Bad credentials）', () => {
  const { r, acquired, mints, nodes } = scenario()
  // 前置：这轮必须真的排过队、而且真的把 nightly 拉起来了，否则这个用例什么也没证明
  assert.ok(acquired, `前提不成立：run-nightly.sh 没走到 QA 槽（退出码 ${r.status}）：${(r.stderr ?? '').slice(0, 400)}`)
  assert.ok(Number(acquired.waited_s) >= 2, `前提不成立：这轮没有真的等锁（waited_s=${acquired.waited_s}），复现不出「等锁跨过令牌寿命」：${JSON.stringify(acquired)}`)
  const nightly = nodes.find(isNightlyRun)
  assert.ok(nightly, `前提不成立：run-nightly.sh 没把真正的 nightly 交给 node（只看到 ${JSON.stringify(nodes)}）`)
  assert.ok(mints.length > 0, '前提不成立：这轮一个安装令牌都没铸')
  const used = mints.filter((m) => m.token === nightly.GH_TOKEN)
  assert.equal(used.length, 1, `nightly 手上的 GH_TOKEN 应该是这轮铸出来的安装令牌之一；实际 ${nightly.GH_TOKEN}`)
  const acquiredAt = Date.parse(acquired.t) / 1000
  assert.ok(nightly.at >= acquiredAt, `前提不成立：nightly 进程起跑于槽位到手之前（${nightly.at} < ${acquiredAt}）`)
  // 正题：Discovery 用 GH_TOKEN（argus/src/github.mjs ghEnvFor → gh api …）——
  // 等锁前铸的令牌在锁一等到就可能已经过期（安装令牌活 1 小时，这次等了 3926 s），第一个 gh 调用就是 401。
  assert.ok(used[0].at >= acquiredAt,
    `nightly 拿到的是等锁**之前**铸的令牌（铸于 ${new Date(used[0].at * 1000).toISOString()}，槽位到手 ${acquired.t}，等了 ${acquired.waited_s}s）：` +
    `安装令牌只活 1 小时，等锁（--wait-lock 默认 4 h）跨过寿命后 discovery 的第一个 gh 调用就是 gh: Bad credentials (HTTP 401)（PR-1789），` +
    `整轮什么都没测成。等锁结束后要按当前凭据重新铸一次（例如再跑一次 App-only 的 gh_app_env），让 GitHub 工作从有效令牌开始`)
})

test('等锁后的刷新不能把 PAT 带回来：nightly 环境里只有 Praise QA App 安装令牌（G4 App-only 约定）', () => {
  const { nodes } = scenario()
  const nightly = nodes.find(isNightlyRun)
  assert.ok(nightly, '前提不成立：没看到 nightly 的启动记录')
  assert.match(String(nightly.GH_TOKEN), /^ghs_/, `nightly 的 GH_TOKEN 必须是安装令牌（ghs_），实际 ${nightly.GH_TOKEN}`)
  assert.equal(nightly.PRAISE_QA_GH_TOKEN, nightly.GH_TOKEN, 'GH_TOKEN 与 PRAISE_QA_GH_TOKEN 必须是同一个安装令牌（argus ghEnvFor 按仓取它）')
  assert.equal(nightly.GITHUB_TOKEN, null, `secrets 里的 PAT 不能留在 nightly 的环境里（scripts/gh-app-env.sh 的 App-only 约定）：${nightly.GITHUB_TOKEN}`)
  assert.deepEqual(nightly.pat_like_env, [], `nightly 环境里不能有任何 PAT 变体（ghp_/github_pat_/gho_/ghu_）：${JSON.stringify(nightly.pat_like_env)}`)
})
