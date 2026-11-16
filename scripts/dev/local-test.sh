#!/usr/bin/env bash
# Local test entry (made for Mac; works on Linux too). Runs the Pi tests (pi/test/test_*.py) and the Argus tests
# (argus/tests/*.test.mjs) one file at a time, then sorts every failure into one of three groups:
#   known env failure  -- matches an entry of scripts/dev/known-env-failures.json for this platform
#   unexpected failure -- everything else (exit code 1)
#   stale list entry   -- a list entry whose test ran and no longer fails (reported, never fails the run)
# Fixed environment, so results do not depend on the shell it runs from: python3 = python3.13, an env -i allowlist
# (no tokens), TMPDIR without spaces, each file in its own scratch dir and under its own timeout.
# Linux CI does not run this script; CI commands and results are unchanged. Docs: docs/dev/local-test-on-mac.md
#
# usage: scripts/dev/local-test.sh [--only pi|argus] [--jobs N] [--timeout SEC] [--no-install] [--keep] [FILTER...]
#   FILTER  only run test files whose path contains one of the strings (e.g. boxhealth, sandbox)
# exit: 0 no unexpected failure and no timeout / 1 an unexpected failure or any timeout (no list entry covers a timeout) / 2 setup error
# Test hooks (scripts/dev/local-test.test.mjs): LOCAL_TEST_KNOWN, LOCAL_TEST_PI_TESTS, LOCAL_TEST_PI_CWD,
# LOCAL_TEST_ARGUS_ROOT, LOCAL_TEST_PYTHON, LOCAL_TEST_PLATFORM.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
ONLY="" JOBS=4 TIMEOUT=300 INSTALL=1 KEEP=0 FILTERS=()
die() { echo "local-test: $*" >&2; exit 2; }
while (( $# )); do
  case "$1" in
    --only) ONLY=${2:-}; shift 2 || die "--only needs pi or argus" ;;
    --jobs) JOBS=${2:-}; shift 2 || die "--jobs needs a number" ;;
    --timeout) TIMEOUT=${2:-}; shift 2 || die "--timeout needs seconds" ;;
    --no-install) INSTALL=0; shift ;;
    --keep) KEEP=1; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    -*) die "unknown option $1 (see --help)" ;;
    *) FILTERS+=("$1"); shift ;;
  esac
done
case "$ONLY" in ""|pi|argus) ;; *) die "--only must be pi or argus, got '$ONLY'" ;; esac
[[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || die "--jobs must be a positive integer, got '$JOBS'"
[[ "$TIMEOUT" =~ ^[1-9][0-9]*$ ]] || die "--timeout must be a positive integer (seconds), got '$TIMEOUT'"
command -v node >/dev/null || die "node not found (Argus needs node >= 24)"

PY=${LOCAL_TEST_PYTHON:-$(command -v python3.13 || true)}
[ -n "$PY" ] && [ -x "$PY" ] || die "python3.13 not found. Install it (brew install python@3.13, or pyenv install 3.13) -- the system python3 (3.9) cannot parse the Pi code"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 13) else 1)' 2>/dev/null || die "$PY is not Python >= 3.13 ($("$PY" --version 2>&1))"

# no spaces: an inherited TMPDIR like ".../Application Support/..." breaks fixtures that split paths on whitespace
# physical path: on macOS /tmp is a link to /private/tmp, and tests compare realpath() results with $TMPDIR
if ! RUN=$(mktemp -d /tmp/panoptes-localtest.XXXXXX) || ! RUN=$(cd "$RUN" && pwd -P); then die "cannot create a scratch dir under /tmp"; fi
case "$RUN" in *" "*) die "scratch dir has a space: $RUN" ;; esac
mkdir -p "$RUN/bin" "$RUN/logs" "$RUN/files"
ln -s "$PY" "$RUN/bin/python3"; ln -s "$PY" "$RUN/bin/python"

PI_TESTS=${LOCAL_TEST_PI_TESTS:-$ROOT/pi/test}
PI_CWD=${LOCAL_TEST_PI_CWD:-$ROOT/pi/bin}
ARGUS=${LOCAL_TEST_ARGUS_ROOT:-$ROOT/argus}
KNOWN=${LOCAL_TEST_KNOWN:-$ROOT/scripts/dev/known-env-failures.json}
[ -f "$KNOWN" ] || die "known failure list not found: $KNOWN"

# Argus needs its node_modules (typescript, playwright); CI does `npm ci` too. No browser download, as in CI.
if [ "$ONLY" != pi ] && [ "$INSTALL" = 1 ] && [ -f "$ARGUS/package-lock.json" ] && [ ! -d "$ARGUS/node_modules/typescript" ]; then
  echo "local-test: argus/node_modules missing, running npm ci (log: $RUN/logs/npm-ci.log)"
  (cd "$ARGUS" && PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci --no-audit --no-fund >"$RUN/logs/npm-ci.log" 2>&1) \
    || echo "local-test: npm ci failed (exit $?), Argus tests will run without it -- see $RUN/logs/npm-ci.log"
fi
# Same fallback as CI's "Sandbox (bubblewrap)" step: without a working bwrap the suite runs in the explicit unsandboxed mode.
SANDBOX_ENV=()
if ! command -v bwrap >/dev/null || ! bwrap --ro-bind / / --dev /dev --proc /proc --unshare-pid true 2>/dev/null; then
  SANDBOX_ENV=("ARGUS_SANDBOX=none" "ARGUS_ALLOW_UNSANDBOXED=1")
fi

# Allowlisted environment for every test process (as pi/bin/selftest.sh: tests never see our tokens).
CLEAN_ENV=("PATH=$RUN/bin:$PATH" "HOME=$HOME" "USER=${USER:-$(id -un)}" "LOGNAME=${LOGNAME:-$(id -un)}"
           "LANG=C.UTF-8" "LC_ALL=C.UTF-8" "TZ=Asia/Shanghai" "TMPDIR=$RUN/files" "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1"
           ${SANDBOX_ENV[@]+"${SANDBOX_ENV[@]}"})

export LT_RUN=$RUN LT_ROOT=$ROOT LT_ONLY=$ONLY LT_JOBS=$JOBS LT_TIMEOUT=$TIMEOUT LT_KNOWN=$KNOWN
export LT_PI_TESTS=$PI_TESTS LT_PI_CWD=$PI_CWD LT_ARGUS=$ARGUS LT_PLATFORM=${LOCAL_TEST_PLATFORM:-}
export LT_CLEAN_ENV LT_FILTERS
LT_CLEAN_ENV=$(printf '%s\0' "${CLEAN_ENV[@]}" | base64 | tr -d '\n')
LT_FILTERS=$(printf '%s\n' ${FILTERS[@]+"${FILTERS[@]}"})

node --input-type=module - <<'JS'
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { basename, join, relative } from 'node:path'

const E = process.env
const platform = E.LT_PLATFORM || process.platform
const timeoutMs = Number(E.LT_TIMEOUT) * 1000
const jobs = Number(E.LT_JOBS)
const filters = (E.LT_FILTERS || '').split('\n').filter(Boolean)
const cleanEnv = Object.fromEntries(Buffer.from(E.LT_CLEAN_ENV, 'base64').toString('utf8').split('\0').filter(Boolean)
  .map((kv) => [kv.slice(0, kv.indexOf('=')), kv.slice(kv.indexOf('=') + 1)]))
const fail = (msg) => { console.error(`local-test: ${msg}`); process.exit(2) }

// known list: { version: 1, entries: [{ id, platforms, reason, tracking }] }; id is "<suite file>" or "<suite file>::<case>"
let known
try { known = JSON.parse(readFileSync(E.LT_KNOWN, 'utf8')) } catch (e) { fail(`cannot read ${E.LT_KNOWN}: ${e.message}`) }
if (!Array.isArray(known?.entries)) fail(`${E.LT_KNOWN}: expected { "entries": [...] }`)
for (const [i, k] of known.entries.entries()) {
  if (typeof k?.id !== 'string' || !/^(pi|argus)\/[^:]+(::.+)?$/.test(k.id)) fail(`${E.LT_KNOWN} entry ${i}: id must look like pi/test_x.py[::Case] or argus/tests/x.test.mjs[::name], got ${JSON.stringify(k?.id)}`)
  if (!Array.isArray(k.platforms) || !k.platforms.length) fail(`${E.LT_KNOWN} entry ${i} (${k.id}): platforms must be a non-empty list, e.g. ["darwin"]`)
  if (!k.reason || !k.tracking) fail(`${E.LT_KNOWN} entry ${i} (${k.id}): reason and tracking are required`)
}
const entries = known.entries.filter((k) => k.platforms.includes(platform))

// suites: one process per file
const pick = (id) => !filters.length || filters.some((f) => id.includes(f))
const list = (dir, re) => (existsSync(dir) ? readdirSync(dir).filter((f) => re.test(f)).sort() : [])
const suites = []
if (E.LT_ONLY !== 'argus') suites.push({ name: 'pi', files: list(E.LT_PI_TESTS, /^test_.*\.py$/).map((f) => ({
  id: `pi/${f}`, cwd: E.LT_PI_CWD, cmd: 'python3', args: [join(E.LT_PI_TESTS, f)], parse: piCases })) })
if (E.LT_ONLY !== 'pi') suites.push({ name: 'argus', files: list(join(E.LT_ARGUS, 'tests'), /\.test\.mjs$/).map((f) => ({
  id: `argus/tests/${f}`, cwd: E.LT_ARGUS, cmd: 'bash', args: ['run-tests.sh', `tests/${f}`], parse: argusCases })) })
const all = suites.flatMap((s) => s.files)
const files = all.filter((f) => pick(f.id))
if (!files.length) fail(`no test files matched (filters: ${filters.join(' ') || 'none'})`)

// unittest: "FAIL: test_x (__main__.Cls.test_x)" / "ERROR: setUpClass (__main__.Cls)" -> "Cls.test_x" / "Cls.setUpClass";
// a case of another module run from inside this file keeps its module ("test_other.Cls.test_y")
function piCases(out, file) {
  const stem = basename(file.id, '.py'), cases = new Set()
  for (const m of out.matchAll(/^(?:FAIL|ERROR): (\S+) \(([^)\s]+)\)/gm)) {
    const own = [`__main__.`, `${stem}.`].find((p) => m[2].startsWith(p))
    const where = own ? m[2].slice(own.length) : m[2]
    cases.add(where === m[1] || where.endsWith(`.${m[1]}`) ? where : `${where}.${m[1]}`)
  }
  if (cases.size) return [...cases]
  // script-style file (plain asserts) or a crash before unittest runs: the failure is where the last traceback leaves this
  // file, "<function>:<line>" (e.g. "<module>:53"). A different broken assert gets a different id, so it is never covered
  // by the entry of a known one; only a failure without any traceback stays file-level.
  const tb = out.lastIndexOf('Traceback (most recent call last):')
  if (tb < 0) return []
  const frames = [...out.slice(tb).matchAll(/^ {2}File "([^"]+)", line (\d+), in (.+)$/gm)].filter((m) => basename(m[1]) === basename(file.id))
  const last = frames.at(-1)
  return last ? [`${last[3].trim()}:${last[2]}`] : []
}
// node:test spec reporter: "✖ name (12.3ms)"; the "failing tests:" header is not a test
function argusCases(out) {
  const cases = new Set()
  for (const line of out.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '').split('\n')) {
    const m = /^\s*✖\s+(.+?)\s*(?:\(\d+(?:\.\d+)?m?s\))?\s*$/.exec(line)
    if (m && m[1] !== 'failing tests:') cases.add(m[1])
  }
  return [...cases]
}

function runFile(file, slot) {
  return new Promise((resolve) => {
    const scratch = join(E.LT_RUN, 'files', `${slot}`)
    rmSync(scratch, { recursive: true, force: true })
    // selftest.sh shares one PI_STATE across all files, so an earlier test has always created it; a fresh dir per file must too
    for (const d of ['pi-state', 'pi-work']) mkdirSync(join(scratch, d), { recursive: true })
    // the Pi state paths of pi/bin/selftest.sh, one set per file so files can run side by side
    const env = { ...cleanEnv, TMPDIR: scratch, PI_WORK: `${scratch}/pi-work`, PI_WORK_ROOT: `${scratch}/pi-work`, PI_STATE: `${scratch}/pi-state`,
      PI_ARGUS_ROOT: `${scratch}/argus`, PI_GAPS_FILE: `${scratch}/pi-state/gaps-open.json`, PRAISE_QA_KEY: `${scratch}/no-app-key`,
      PRAISE_QA_CACHE: `${scratch}/praise-qa-cache`, PYTHONPYCACHEPREFIX: `${scratch}/pycache`, PYTHONDONTWRITEBYTECODE: '1' }
    const started = Date.now(), chunks = []
    const child = spawn(file.cmd, file.args, { cwd: file.cwd, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] })
    child.stdout.on('data', (c) => chunks.push(c)); child.stderr.on('data', (c) => chunks.push(c))
    let timedOut = false, killer
    const timer = setTimeout(() => {
      timedOut = true
      try { process.kill(-child.pid, 'SIGTERM') } catch {}
      killer = setTimeout(() => { try { process.kill(-child.pid, 'SIGKILL') } catch {} }, 5000)
    }, timeoutMs)
    const done = (code, signal, error) => {
      clearTimeout(timer)
      // the group may still hold pipes open after a timeout; make sure nothing of this file survives
      if (timedOut) { try { process.kill(-child.pid, 'SIGKILL') } catch {} }
      clearTimeout(killer)
      const out = Buffer.concat(chunks).toString('utf8') + (error ? `\n[local-test] spawn error: ${error.message}\n` : '')
      const log = join(E.LT_RUN, 'logs', `${file.id.replaceAll('/', '__')}.log`)
      writeFileSync(log, out)
      rmSync(scratch, { recursive: true, force: true })
      const status = timedOut ? 'timeout' : code === 0 && !error ? 'pass' : 'fail'
      resolve({ ...file, status, code, signal, ms: Date.now() - started, log, cases: status === 'fail' ? file.parse(out, file) : [] })
    }
    child.on('error', (e) => done(null, null, e))
    child.on('close', (code, signal) => done(code, signal))
  })
}

const results = []
let next = 0
const progress = (r) => { if (r.status !== 'pass') console.log(`  ${r.status === 'timeout' ? '超时' : '失败'}  ${r.id}  (${(r.ms / 1000).toFixed(1)}s)`) }
console.log(`local-test: ${files.length} 个测试文件，并发 ${jobs}，单文件超时 ${E.LT_TIMEOUT}s，平台 ${platform}，日志 ${E.LT_RUN}/logs`)
await Promise.all(Array.from({ length: Math.min(jobs, files.length) }, async (_, slot) => {
  while (next < files.length) { const f = files[next++]; const r = await runFile(f, slot); results.push(r); progress(r) }
}))

// match: a failing file yields its failing cases, or the file itself when no case could be read
const failures = results.flatMap((r) => r.status === 'timeout' ? [{ id: r.id, file: r.id, kind: 'timeout', log: r.log }]
  : r.status === 'fail' ? (r.cases.length ? r.cases : [null]).map((c) => ({ id: c ? `${r.id}::${c}` : r.id, file: r.id, kind: 'fail', log: r.log })) : [])
// exact match only: a file-level entry matches a file-level failure (no case / traceback could be read), never a case of
// that file -- otherwise a new failure in a listed file would be hidden. A timeout is always unexpected: no entry covers it.
const covers = (k, f) => f.kind === 'fail' && k.id === f.id
const used = new Set()
for (const f of failures) { f.entry = entries.find((k) => covers(k, f)); if (f.entry) used.add(f.entry) }
const ran = new Map(results.map((r) => [r.id, r]))
const ranSuites = new Set(suites.map((s) => s.name))
const stale = [], notRun = []
for (const k of entries) {
  if (used.has(k)) continue
  const file = k.id.split('::')[0], r = ran.get(file)
  if (r && r.status !== 'timeout') stale.push(k)                         // ran to the end and this entry did not fail
  else if (!r && ranSuites.has(file.split('/')[0]) && !all.some((f) => f.id === file)) stale.push({ ...k, gone: true })
  else if (!r || r.status === 'timeout') notRun.push(k)
}
const expected = failures.filter((f) => f.entry), unexpected = failures.filter((f) => !f.entry)
const unexpectedFail = unexpected.filter((f) => f.kind === 'fail'), unexpectedTimeout = unexpected.filter((f) => f.kind === 'timeout')
const count = (s) => results.filter((r) => r.status === s).length

const out = []
out.push('', `== 本地测试汇总（${platform}） ==`)
out.push(`测试文件 ${results.length} 个：通过 ${count('pass')}，失败 ${count('fail')}，超时 ${count('timeout')}`)
out.push(`已知环境失败 ${expected.length} 条（在清单里，不算失败）`)
for (const f of expected) out.push(`  - ${f.id}：${f.entry.reason}［${f.entry.tracking}］`)
out.push(`超时 ${count('timeout')} 个（单文件超过 ${E.LT_TIMEOUT}s，已停掉并接着跑下一个；超时一律算意外，清单不覆盖）`)
for (const f of failures.filter((x) => x.kind === 'timeout')) out.push(`  - ${f.id}  日志 ${f.log}`)
out.push(`清单过期 ${stale.length} 条（已不再失败；提醒，不算失败，请从清单删掉）`)
for (const k of stale) out.push(`  - ${k.id}${k.gone ? '（测试文件已不存在）' : ''}［${k.tracking}］`)
if (notRun.length) out.push(`清单条目本轮没跑到 ${notRun.length} 条（被筛掉、或所在文件超时）`)
out.push(`意外失败 ${unexpectedFail.length}`)
for (const f of unexpectedFail) out.push(`  - ${f.id}  日志 ${f.log}`)
if (unexpectedTimeout.length) out.push(`意外超时 ${unexpectedTimeout.length}`)
console.log(out.join('\n'))
writeFileSync(join(E.LT_RUN, 'summary.json'), JSON.stringify({ platform, files: results.length, pass: count('pass'), fail: count('fail'),
  timeout: count('timeout'), expected: expected.map((f) => f.id), unexpected: unexpectedFail.map((f) => f.id),
  unexpectedTimeout: unexpectedTimeout.map((f) => f.id), stale: stale.map((k) => k.id), notRun: notRun.map((k) => k.id) }, null, 2))
process.exit(unexpected.length ? 1 : 0)
JS
rc=$?
if [ "$rc" = 0 ] && [ "$KEEP" = 0 ]; then rm -rf "$RUN"; else echo "local-test: 日志和 summary.json 留在 $RUN"; fi
exit "$rc"
