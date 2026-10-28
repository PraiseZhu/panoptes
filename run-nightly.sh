#!/usr/bin/env bash
# panoptes — Mivo QA nightly entrypoint (Linux).
#
#   run-nightly.sh --catch-up [--wait-lock SECONDS] [--trigger catch-up|manual] [--create-issues]
#   run-nightly.sh [--trigger event|catch-up|manual] [--date YYYY-MM-DD] [--pr N ...] [--limit N]
#                  [--no-review] [--no-ai-tests] [--skip-step ID ...]
#                  [--out PATH] [--no-reuse] [--wait-lock [SECONDS]]
#                  [--pr-timeout MIN] [--deadline HH:MM | --burst-cutoff [HH:MM]] [--write-merged | --no-write-merged] [--write-open]
#                  [--no-issues | --create-issues] [--quota-pool test|production] [--fresh-review]
#
#   --catch-up    pure-script gap filler (cron-safe, quiet when there is nothing to do): list PRs merged
#                 yesterday + today (Asia/Shanghai) via gh, compare with $QA_HOME/reports/merged/PR-N.json
#                 (gap = missing, or tested_sha != merge commit), then test each gap oldest-first with
#                 --pr N --date <merge day> --write-merged --wait-lock S --no-issues --out reports/catch-up/PR-N.json.
#                 A lock timeout (exit 75) stops and retries on the next invocation; after 3 attempts a PR is
#                 given up and recorded in system_health (reports/catch-up/run-*.json and the next daily report).
#                 Only one catch-up at a time (catch-up.lock; a second one exits 0 with one '[catch-up] … skipped' line).
#                 With --create-issues (and ARGUS_ALLOW_ISSUES=1 exported) the children get --create-issues instead.
#                 Installed by scripts/bootstrap-box.sh as a cron job every 2 hours (scripts/catch-up.cron).
#   --trigger     who started this run, recorded as run.trigger: event (on-merge trigger) | catch-up | manual.
#                 Default: manual; --catch-up defaults to catch-up (its children get --trigger catch-up).
#   --date        Asia/Shanghai calendar day to report on (default: yesterday in Shanghai)
#   --pr N        test only these PRs (repeatable); otherwise discovery picks
#                 open PRs updated that day + PRs merged that day (all authors)
#   --limit N     test at most N discovered PRs
#   --no-review   skip the ming-qa LLM code review
#   --no-ai-tests skip AI-generated tests for uncovered applicable dimensions (env ARGUS_AITESTS=0 does the same;
#                 caps per PR: ARGUS_AITEST_MAX_CALLS (2), ARGUS_AITEST_MAX_MIN (25), ARGUS_AITEST_MAX_TESTS per call (3))
#   --skip-step ID  skip a profile step (repeatable)
#   --out PATH    write the report here instead of $QA_HOME/reports/YYYY-MM-DD.json (refuses to target a
#                 daily report file). Relative: reports/… is under $QA_HOME, anything else under the calling directory. On-merge trigger: --pr N --out $QA_HOME/reports/merged/PR-N.json
#   --no-reuse    re-test merged PRs even if reports/merged/PR-N.json exists for the same merge commit
#   --no-step-cache  run every step even if a passing result for identical inputs (tree hashes + lockfile) is cached
#   --priority    fresh-merge run (mergewatch): jumps ahead of the sequential queue and takes the single QA slot as
#                 soon as it is free, or while the slot holder is parked between PRs (scripts/qa-slot.sh)
#   --wait-lock [SECONDS]  queue behind a running instance (default 14400 = 4h) instead of exit 75
#   --pr-timeout MIN  per-PR time budget; a PR that exceeds it is stopped and reported as not-tested (P2)
#                 (env ARGUS_PR_TIMEOUT_MIN; default: no limit)
#   --burst-cutoff [HH:MM]  overnight burst cutoff (default 08:30 Asia/Shanghai): PRs still untested then are reported
#                 not-tested (not_tested_code burst-cutoff); the catch-up timer tests them later (env ARGUS_BURST_CUTOFF)
#   --deadline HH:MM  Asia/Shanghai wall-clock deadline (or ISO time); PRs not started by then are
#                 reported as not-tested (P2) and a running PR is stopped (env ARGUS_DEADLINE)
#   --write-merged / --no-write-merged  also write each merged PR that was actually tested to
#                 $QA_HOME/reports/merged/PR-N.json (same format as on-merge). Default: on without --pr, off with --pr
#   --write-open  also write every open PR of the run (tested or not, with reasons) to
#                 $QA_HOME/reports/open/PR-N.json (same per-PR format). Default: off
#   --quota-pool P  daily model-call quota pool: production | test. Default: --trigger manual → test (manual runs,
#                 drills, acceptance reruns never consume production quota), event / catch-up → production.
#                 Caps per kind and pool: ARGUS_QUOTA_{AI_TEST,JEV,AI_REVIEW,PR_SUMMARY} (50/150/70/40) and
#                 ARGUS_QUOTA_TEST_* for the test pool, or $ARGUS_ROOT/quota.json (env ARGUS_QUOTA_FILE)
#   --fresh-review  do not reuse a cached code-review verdict (commit + diff + prompt + model); review afresh
#   --no-issues   default behaviour; accepted for explicitness
#   --create-issues  OFF by default. Only acts when ARGUS_ALLOW_ISSUES=1 is also set.
#   -h, --help    print this help
#
# Environment (all optional):
#   QA_HOME            data dir: argus-root/, reports/, cache/ms-playwright, .venv   (default /workspace/mivo-qa)
#   QA_SECRETS_FILE    secrets env file, see secrets.env.example        (default /home/box/.config/mivo-qa/secrets.env)
#   PANOPTES_DIR       code dir (argus/, ming-qa/)                       (default: this script's directory)
#   PANOPTES_NVM_DIR   nvm with Node 24                                  (default /workspace/_tools/nvm)
#   PANOPTES_BOOTSTRAP=0  do not auto-install missing dependencies (the self-check still runs and is reported)
#
# Self-check: every run first runs scripts/bootstrap-box.sh --check; anything missing (Node 24, argus npm deps,
# chromium + launcher, Playwright browsers / deps, bubblewrap, ming-qa venv) is installed by scripts/bootstrap-box.sh
# (log: $QA_HOME/argus-root/logs/bootstrap-*.log). What was missing / installed / still missing goes into the report's
# system_health (qa_tool_failures stage "bootstrap", env_problems). If Node 24 itself is still missing, a minimal
# report with system_health.status "failed" is written (never over an existing report) and the exit code is 3.
#   ARGUS_LLM_MODEL    review/summary model                              (default openai/gpt-6-luna)
#
# Output: $QA_HOME/reports/YYYY-MM-DD.json  (logs: $QA_HOME/argus-root/logs)
# Exit codes: 0 ok/degraded, 3 pipeline failed or QA environment broken (see system_health), 1 usage/config error,
#             75 another run active / lock wait timed out, 76 skipped for low memory (memory gate below: still < 8 GB after
#             30 min and below the 5 GB / 4 GB swap degraded floor; nothing ran, catch-up retries it without counting an attempt).
set -euo pipefail

PANOPTES_DIR="${PANOPTES_DIR:-$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)}"
QA_HOME="${QA_HOME:-/workspace/mivo-qa}"
SECRETS="${QA_SECRETS_FILE:-/home/box/.config/mivo-qa/secrets.env}"

# Launch / exit lines in control.log (before any lock, on every exit path) so an interrupted or queued run is visible.
mkdir -p "$QA_HOME/argus-root/logs" 2>/dev/null || true
CONTROL_LOG="$QA_HOME/argus-root/logs/control.log"
ctl() { printf '{"t":"%s","level":"info","msg":"%s","pid":%d,%s}\n' "$(date -u +%Y-%m-%dT%H:%M:%S.000Z)" "$1" "$$" "$2" >>"$CONTROL_LOG" 2>/dev/null || true; }
LAUNCH_ARGS="$(printf '%s ' "$@" | sed 's/[\\"]/\\&/g')"
ctl "run-nightly launch" "\"args\":\"${LAUNCH_ARGS% }\""
EXTRA_EXIT_CLEANUP=":"
trap 'rc=$?; eval "$EXTRA_EXIT_CLEANUP"; ctl "run-nightly exit" "\"exitCode\":$rc"' EXIT

# --out PATH is resolved here, before the cd into argus/ (10-19: a rerun's relative --out landed in argus/reports/merged/):
# absolute stays as is; a relative path starting with reports/ is under $QA_HOME (reports/merged/PR-N.json,
# reports/catch-up/PR-N.json); any other relative path is relative to the directory the script was called from.
resolve_out() {
  case "$1" in
    /*) printf '%s\n' "$1" ;;
    reports/*) printf '%s\n' "$QA_HOME/$1" ;;
    *) local p="$PWD/$1"; printf '%s\n' "${p//\/.\///}" ;;
  esac
}

# --wait-lock [SECONDS]: wait for the single-instance lock instead of exiting 75 (default 14400 = 4h).
# Consumed here; --out is made absolute (resolve_out); everything else is passed to the Node CLI unchanged.
WAIT_LOCK=""
CATCH_UP=""
PRIORITY=""
TRIGGER=""
ARGS=()
while (( $# )); do
  case "$1" in
    -h|--help)
      sed -n '2,/^set -euo/{/^set -euo/d;s/^# \{0,1\}//;p}' "${BASH_SOURCE[0]}"; exit 0 ;;
    --catch-up) CATCH_UP=1 ;;
    --priority) PRIORITY=1 ;;
    --trigger)
      [[ -n "${2:-}" ]] || { echo "--trigger needs event, catch-up or manual" >&2; exit 1; }
      TRIGGER="$2"; shift ;;
    --trigger=*) TRIGGER="${1#--trigger=}" ;;
    --wait-lock)
      WAIT_LOCK=14400
      if [[ "${2:-}" =~ ^[0-9]+$ ]]; then WAIT_LOCK="$2"; shift; fi ;;
    --wait-lock=*)
      WAIT_LOCK="${1#--wait-lock=}"
      [[ "$WAIT_LOCK" =~ ^[0-9]+$ ]] || { echo "--wait-lock needs whole seconds" >&2; exit 1; } ;;
    --out)
      if [[ -n "${2:-}" && "$2" != --* ]]; then ARGS+=(--out "$(resolve_out "$2")"); shift; else ARGS+=("$1"); fi ;;
    --out=*) ARGS+=(--out "$(resolve_out "${1#--out=}")") ;;
    *) ARGS+=("$1") ;;
  esac
  shift
done
set -- ${ARGS[@]+"${ARGS[@]}"}
[[ -n "$TRIGGER" ]] || { [[ -n "$CATCH_UP" ]] && TRIGGER=catch-up || TRIGGER=manual; }
case "$TRIGGER" in event|catch-up|manual) ;; *) echo "--trigger must be event, catch-up or manual (got '$TRIGGER')" >&2; exit 1 ;; esac
export PANOPTES_TRIGGER="$TRIGGER"

# Issue creation is OFF unless --create-issues is given AND ARGUS_ALLOW_ISSUES=1 is in the caller's env.
CREATE=""
for a in "$@"; do
  [[ "$a" == "--create-issues" ]] && CREATE=1
done
ALLOW_ISSUES=""
[[ -n "$CREATE" && "${ARGUS_ALLOW_ISSUES:-}" == "1" ]] && ALLOW_ISSUES=1
unset ARGUS_ALLOW_ISSUES || true

export ARGUS_ROOT="$QA_HOME/argus-root"
mkdir -p "$ARGUS_ROOT/logs"


# /tmp sweep: leftover argus-* dirs older than 24 h, owned by us, not held by a live process (scripts/sweep-tmp.sh).
# Runs at every start, so the catch-up cron (run-nightly.sh --catch-up) sweeps too. PANOPTES_TMP_SWEEP=0 disables.
if [[ "${PANOPTES_TMP_SWEEP:-1}" != "0" && -x "$PANOPTES_DIR/scripts/sweep-tmp.sh" ]]; then
  "$PANOPTES_DIR/scripts/sweep-tmp.sh" --quiet >>"$ARGUS_ROOT/logs/tmp-sweep.log" 2>&1 || true
fi

# ── Self-check + bootstrap (a replaced box loses home-dir and system installs) ──────────────────
# The result is handed to the Node CLI (PANOPTES_BOOTSTRAP_STATUS) and lands in system_health.
BOOT_STATUS="$ARGUS_ROOT/bootstrap-status-$$.json"
# Leftovers of runs that were killed (or exec'ed into catch-up before 2026-10-11): remove files whose pid is gone.
for f in "$ARGUS_ROOT"/bootstrap-status-*.json; do
  [[ -e "$f" ]] || continue
  pid="${f##*/bootstrap-status-}"; pid="${pid%.json}"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then continue; fi
  rm -f "$f"
done
BOOT_SCRIPT="$PANOPTES_DIR/scripts/bootstrap-box.sh"
json_list() { python3 -c 'import json,sys; print(json.dumps([l for l in sys.stdin.read().splitlines() if l.strip()]))' 2>/dev/null || echo '[]'; }
MISSING_BEFORE=$("$BOOT_SCRIPT" --check 2>/dev/null) && BOOT_RAN=false || BOOT_RAN=true
MISSING_AFTER="$MISSING_BEFORE"
BOOT_LOG=""
if [[ "$BOOT_RAN" == true ]]; then
  if [[ "${PANOPTES_BOOTSTRAP:-1}" == "0" ]]; then
    BOOT_RAN=false
    echo "[panoptes] self-check: dependencies missing, auto-install disabled (PANOPTES_BOOTSTRAP=0)" >&2
  else
    BOOT_LOG="$ARGUS_ROOT/logs/bootstrap-$(date +%Y%m%dT%H%M%S).log"
    echo "[panoptes] self-check: dependencies missing, running bootstrap-box.sh (log: $BOOT_LOG)" >&2
    "$BOOT_SCRIPT" >"$BOOT_LOG" 2>&1 || true
    MISSING_AFTER=$("$BOOT_SCRIPT" --check 2>/dev/null) || true
  fi
fi
printf '{"checked_at":"%s","ran":%s,"missing_before":%s,"missing_after":%s,"log":"%s"}\n' \
  "$(date -Is)" "$BOOT_RAN" "$(json_list <<<"$MISSING_BEFORE")" "$(json_list <<<"$MISSING_AFTER")" "$BOOT_LOG" >"$BOOT_STATUS"
export PANOPTES_BOOTSTRAP_STATUS="$BOOT_STATUS"
# Single quotes on purpose: expanded later by eval in the EXIT trap.
# shellcheck disable=SC2016
EXTRA_EXIT_CLEANUP='rm -f "$BOOT_STATUS"'

# The QA environment is unusable without Node 24: record it as a QA system failure instead of a bare exit.
env_failed() {
  local why="$1" date_arg="" out_arg="" prev=""
  # catch-up has no report of its own; its children (run-nightly.sh --pr N) record the failure per PR
  [[ -n "$CATCH_UP" ]] && { echo "[panoptes] QA environment broken: $why" >&2; exit 3; }
  for a in "$@"; do
    [[ "$prev" == "--date" ]] && date_arg="$a"
    [[ "$prev" == "--out" ]] && out_arg="$a"
    prev="$a"
  done
  [[ -n "$date_arg" ]] || date_arg=$(TZ=Asia/Shanghai date -d yesterday +%F)
  local out="${out_arg:-$QA_HOME/reports/$date_arg.json}"
  [[ "$out" = /* ]] || out="$PWD/$out"
  [[ -e "$out" ]] && out="${out%.json}.env-failed-$(date +%Y%m%dT%H%M%S).json"
  mkdir -p "$(dirname "$out")"
  WHY="$why" DATE="$date_arg" OUT="$out" ARGS_JSON="$(printf '%s\n' "${@:2}" | json_list)" MISSING_JSON="$(json_list <<<"$MISSING_AFTER")" \
  BOOT_LOG="$BOOT_LOG" python3 - <<'PY' || echo "[panoptes] could not write the failure report either" >&2
import json, os, datetime
missing = json.loads(os.environ["MISSING_JSON"])
doc = {
  "schema_version": "1.0", "date": os.environ["DATE"], "repo": "xindong/mivo-canvas-plugin",
  "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
  "run": {"id": "env-failed-" + datetime.datetime.now().strftime("%Y%m%dT%H%M%S"), "mode": "env-failed", "args": json.loads(os.environ["ARGS_JSON"])},
  "prs": [],
  "system_health": {
    "status": "failed",
    "qa_tool_failures": [{"stage": "bootstrap", "message": os.environ["WHY"], "detail": os.environ.get("BOOT_LOG") or None}],
    "env_problems": [{"message": m.removeprefix("missing: ")} for m in missing],
    "llm_errors": [], "notes": ["QA environment broken: no PR was tested; the run needs to be repeated once the box is repaired"],
  },
}
with open(os.environ["OUT"], "w") as f: json.dump(doc, f, ensure_ascii=False, indent=2)
print(f"[panoptes] QA environment broken; wrote {os.environ['OUT']}", flush=True)
PY
  echo "[panoptes] QA environment broken: $why" >&2
  exit 3
}

# Node 24 via nvm (system node stays 20 for other tools on this box). nvm lives under /workspace so it survives a
# box replacement; ~/.nvm is the fallback for older setups.
unset NPM_CONFIG_PREFIX npm_config_prefix
export NVM_DIR="${PANOPTES_NVM_DIR:-/workspace/_tools/nvm}"
[[ -s "$NVM_DIR/nvm.sh" || ! -s "$HOME/.nvm/nvm.sh" ]] || NVM_DIR="$HOME/.nvm"
NODE_OK=""
# shellcheck disable=SC1091
if [[ -s "$NVM_DIR/nvm.sh" ]] && . "$NVM_DIR/nvm.sh" --no-use >/dev/null 2>&1 && nvm use --silent 24 >/dev/null 2>&1 \
   && node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 24 ? 0 : 1)' 2>/dev/null; then NODE_OK=1; fi
[[ -n "$NODE_OK" ]] || env_failed "Node 24 unavailable after bootstrap (NVM_DIR=$NVM_DIR)" "$@"

[[ -r "$SECRETS" ]] || { echo "missing secrets file $SECRETS" >&2; exit 1; }
chmod 600 "$SECRETS" 2>/dev/null || true
# gh/git read access for the target repo comes from GITHUB_TOKEN in the secrets file
set -a
# shellcheck source=/dev/null
. "$SECRETS"
set +a
for v in GITHUB_TOKEN QA_LLM_BASE_URL QA_LLM_API_KEY; do
  [[ -n "${!v:-}" ]] || { echo "missing required variable $v in $SECRETS" >&2; exit 1; }
done
export GH_TOKEN="$GITHUB_TOKEN"
# The secrets file cannot switch issue creation on; only the caller's env + flag can.
unset ARGUS_ALLOW_ISSUES || true
[[ -n "$ALLOW_ISSUES" ]] && export ARGUS_ALLOW_ISSUES=1

[[ -f "$PANOPTES_DIR/argus/src/cli.mjs" ]] || { echo "argus code not found under $PANOPTES_DIR/argus" >&2; exit 1; }
export ARGUS_REPORTS_DIR="$QA_HOME/reports"
export ARGUS_SECRETS_FILE="$SECRETS"
export ARGUS_MINGQA_DIR="${ARGUS_MINGQA_DIR:-$PANOPTES_DIR/ming-qa}"
export ARGUS_PYTHON="${ARGUS_PYTHON:-$QA_HOME/.venv/bin/python}"
export ARGUS_LLM_MODEL="${ARGUS_LLM_MODEL:-openai/gpt-6-luna}"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$QA_HOME/cache/ms-playwright}"
# argus itself runs in Shanghai time; test subprocesses get TZ from runner.mjs testTz() (Asia/Shanghai, or an explicit PANOPTES_TEST_TZ), never from this TZ
export TZ=Asia/Shanghai
[[ -n "${PANOPTES_TEST_TZ:-}" ]] && export PANOPTES_TEST_TZ
mkdir -p "$ARGUS_ROOT/logs" "$ARGUS_REPORTS_DIR"

# Unified severity pin (argus/config/severity.md = pinned copy of PraiseZhu/Review-PR mivo/config/severity.md): compare
# with Review-PR main once per Shanghai day. Drift only raises an ALERT line in $ARGUS_ROOT/logs/severity-pin.log
# (and system_health.notes of the next report); it never blocks a run. PANOPTES_SEVERITY_PIN_CHECK=0 disables.
if [[ "${PANOPTES_SEVERITY_PIN_CHECK:-1}" != "0" && -f "$PANOPTES_DIR/scripts/check-severity-pin.mjs" ]]; then
  timeout 90 node "$PANOPTES_DIR/scripts/check-severity-pin.mjs" --once-per-day --quiet || true
fi

if [[ -n "$CATCH_UP" ]]; then
  CU_ARGS=()
  for a in "$@"; do
    case "$a" in
      --create-issues) [[ -n "$ALLOW_ISSUES" ]] && CU_ARGS+=(--create-issues) ;;
      --no-issues) ;;
      *) echo "--catch-up takes only --wait-lock, --trigger and --create-issues / --no-issues (got '$a')" >&2; exit 1 ;;
    esac
  done
  # catch-up writes no report of its own: the bootstrap status file is not needed (exec would skip the EXIT trap)
  rm -f "$BOOT_STATUS"
  # A second catch-up while one is running: nothing to add, exit 0 with one log line (stdout = catch-up-cron.log under cron)
  # so a skipped tick is visible instead of silent. The children take nightly.lock themselves.
  exec 8>"$ARGUS_ROOT/catch-up.lock"
  flock -n 8 || { printf '[catch-up] %s trigger=%s: catch-up.lock held by another catch-up; skipped\n' "$(TZ=Asia/Shanghai date +%Y-%m-%dT%H:%M:%S+08:00)" "${TRIGGER:-catch-up}"; exit 0; }
  cd "$PANOPTES_DIR/argus"
  export PANOPTES_RUN_NIGHTLY="$PANOPTES_DIR/run-nightly.sh"
  ctl "run-nightly exec catch-up (exec replaces the shell: no exit line follows)" "\"exitCode\":null"
  exec node --disable-warning=ExperimentalWarning src/cli.mjs catch-up --wait-lock "${WAIT_LOCK:-14400}" --trigger "$TRIGGER" ${CU_ARGS[@]+"${CU_ARGS[@]}"}
fi

# One QA slot (scripts/qa-slot.sh): nightly.lock; --priority (fresh merge, from mergewatch) jumps the queue.
# shellcheck disable=SC1091
source "$PANOPTES_DIR/scripts/qa-slot.sh"
exec 9>"$ARGUS_ROOT/nightly.lock"
ONMERGE_PRS=(); prev=""; for a in "$@"; do [[ "$prev" == --pr ]] && ONMERGE_PRS+=("$a"); [[ "$a" == --pr=* ]] && ONMERGE_PRS+=("${a#--pr=}"); prev="$a"; done
if [[ -n "$PRIORITY" && ${#ONMERGE_PRS[@]} -eq 1 && "${PANOPTES_ONMERGE_CONCURRENT:-1}" == 1 ]]; then
  echo "[panoptes] on-merge run #${ONMERGE_PRS[0]}: per-PR lock, slot or memory-admitted concurrent run…" >&2
  slot_acquire_onmerge 9 "${ONMERGE_PRS[0]}" "${WAIT_LOCK:-14400}" || { echo "gave up waiting for the QA slot" >&2; exit 75; }
  export PANOPTES_PRIORITY=1
  [[ "$SLOT_MODE" == concurrent ]] && export ARGUS_MAX_PARALLEL=1
  ctl "run-nightly priority slot" "\"mode\":\"$SLOT_MODE\""
elif [[ -n "$PRIORITY" ]]; then
  exec 7>"$ARGUS_ROOT/priority.lock"
  echo "[panoptes] priority run: taking the QA slot as soon as it is free…" >&2
  slot_acquire_priority 9 7 "${WAIT_LOCK:-14400}" || { echo "gave up waiting for the QA slot" >&2; exit 75; }
  export PANOPTES_PRIORITY=1
  ctl "run-nightly priority slot" "\"mode\":\"$SLOT_MODE\""
elif [[ -n "$WAIT_LOCK" ]]; then
  echo "[panoptes] waiting up to ${WAIT_LOCK}s for the run lock…" >&2
  slot_acquire_normal 9 "$WAIT_LOCK" || { echo "gave up waiting for the run lock after ${WAIT_LOCK}s" >&2; exit 75; }
else
  slot_acquire_normal 9 "" || { echo "another run is active (use --wait-lock to queue)" >&2; exit 75; }
fi

# Per-run temp dir: everything this run (and its node / git / test children) puts under TMPDIR lands in
# /tmp/argus-run-XXXXXX and is removed on exit (sandboxed runs bind their own dirs explicitly, unaffected).
RUN_TMP="$(mktemp -d "${TMPDIR:-/tmp}/argus-run-XXXXXX")"
export TMPDIR="$RUN_TMP"
# Single quotes on purpose: expanded later by eval in the EXIT trap.
# shellcheck disable=SC2016
EXTRA_EXIT_CLEANUP='slot_release_priority; rm -f "$BOOT_STATUS"; rm -rf --one-file-system "$RUN_TMP"'

cd "$PANOPTES_DIR/argus"
LOG="$ARGUS_ROOT/logs/nightly-$(date +%Y%m%dT%H%M%S).log"

# Memory gate (box: 15 GB + 8 GB swap, shared by several bots; 2026-10-14 froze from memory exhaustion).
# 电脑运维 rule (10-27), scripts/qa-slot.sh slot_mem_gate: start normally only at MemAvailable >= 8 GB AND SwapFree >= 4 GB,
# waiting up to PANOPTES_MEM_WAIT_S (1800 s). Still low after that: >= 5 GB and swap >= 4 GB -> degraded (one unit shard,
# coverage round after the first round, never in parallel); else skip: exit 76 without running (the slot lock is released
# on exit) and the next catch-up retests the PR. A single docs-only PR (no tests run) does not wait (10-27, #1315 waited
# 30 min while the host balloon held ~6 GB). The balloon is not subtracted: MemTotal stays 16 GB, inflated pages just look
# used, and lowering the 8 GB threshold would weaken the rule; it is only logged (slot_balloon_mb).
mem_avail_kb() { slot_mem_avail_kb; }
GATE_PRS=(); prev=""; for a in "$@"; do [[ "$prev" == --pr ]] && GATE_PRS+=("$a"); [[ "$a" == --pr=* ]] && GATE_PRS+=("${a#--pr=}"); prev="$a"; done
DOCS_CHECK=(); (( ${#GATE_PRS[@]} == 1 )) && DOCS_CHECK=(timeout 60 node --disable-warning=ExperimentalWarning src/cli.mjs docs-only --pr "${GATE_PRS[0]}")
slot_mem_gate ${DOCS_CHECK[@]+"${DOCS_CHECK[@]}"}
export PANOPTES_GATE_WAIT_S="$GATE_WAIT_S" PANOPTES_GATE_MODE="$GATE_MODE"   # nightly.mjs records gate_wait_s per PR (SLA: queue vs pipeline)
mem_now="MemAvailable $(( $(mem_avail_kb) / 1024 )) MiB, SwapFree $(( $(slot_swap_free_kb) / 1024 )) MiB, balloon $(slot_balloon_mb) MiB"
case "$GATE_MODE" in
  docs-only) msg="$mem_now: below gate, PR #${GATE_PRS[0]} is docs-only (no tests), not waiting"
    echo "[panoptes] $msg" | tee -a "$LOG" >&2; ctl "run-nightly memory gate skipped (docs-only)" "\"reason\":\"$msg\"" ;;
  degraded) msg="$mem_now after ${GATE_WAIT_S}s: still < ${PANOPTES_MIN_MEM_GB:-8} GiB, >= ${PANOPTES_DEGRADED_MEM_GB:-5} GiB: degraded run (ARGUS_UNIT_SHARDS=1, ARGUS_MAX_PARALLEL=1)"
    export ARGUS_UNIT_SHARDS=1 ARGUS_MAX_PARALLEL=1
    echo "[panoptes] $msg" | tee -a "$LOG" >&2; ctl "run-nightly low memory, degraded" "\"reason\":\"$msg\"" ;;
  skip) msg="$mem_now after ${GATE_WAIT_S}s: below the degraded floor (${PANOPTES_DEGRADED_MEM_GB:-5} GiB / swap ${PANOPTES_MIN_SWAP_GB:-4} GiB): not running, exit 76, next catch-up retries"
    echo "[panoptes] SKIP $msg" | tee -a "$LOG" >&2; ctl "run-nightly low memory, skipped" "\"reason\":\"$msg\""
    exit 76 ;;
  *) (( GATE_WAIT_S > 0 )) && ctl "run-nightly memory gate passed" "\"gate_wait_s\":$GATE_WAIT_S" ;;
esac
[[ -n "${PANOPTES_GATE_DRYRUN:-}" && -z "${PANOPTES_TEST:-}" ]] && { echo "[panoptes] WARNING PANOPTES_GATE_DRYRUN=$PANOPTES_GATE_DRYRUN set outside tests (PANOPTES_TEST unset): ignored, running normally" | tee -a "$LOG" >&2; ctl "run-nightly gate dryrun ignored" "\"level\":\"warn\""; unset PANOPTES_GATE_DRYRUN; }
# tests only (PANOPTES_TEST=1): stop right after the gate decision (normal / degraded print it; skip already exited 76)
[[ "${PANOPTES_GATE_DRYRUN:-}" == 1 ]] && { echo "[panoptes] gate: mode=$GATE_MODE wait=${GATE_WAIT_S}s shards=${ARGUS_UNIT_SHARDS:-auto} max_parallel=${ARGUS_MAX_PARALLEL:-auto}"; exit 0; }
echo "[panoptes] code: $PANOPTES_DIR ($(git -C "$PANOPTES_DIR" rev-parse --short HEAD 2>/dev/null || echo '?'))  data: $QA_HOME  log: $LOG" >&2
# Lower priority so the box stays responsive
nice -n 10 node --disable-warning=ExperimentalWarning src/cli.mjs nightly "$@" 2> >(tee -a "$LOG" >&2) | tee -a "$LOG"
rc="${PIPESTATUS[0]}"
exit "$rc"
