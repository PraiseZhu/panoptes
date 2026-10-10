#!/usr/bin/env bash
# panoptes — Mivo QA nightly entrypoint (Linux).
#
#   run-nightly.sh [--date YYYY-MM-DD] [--pr N ...] [--limit N]
#                  [--no-review] [--skip-step ID ...]
#                  [--out PATH] [--no-reuse] [--wait-lock [SECONDS]]
#                  [--pr-timeout MIN] [--deadline HH:MM]
#                  [--no-issues | --create-issues]
#
#   --date        Asia/Shanghai calendar day to report on (default: yesterday in Shanghai)
#   --pr N        test only these PRs (repeatable); otherwise discovery picks
#                 open PRs updated that day + PRs merged that day (all authors)
#   --limit N     test at most N discovered PRs
#   --no-review   skip the ming-qa LLM code review
#   --skip-step ID  skip a profile step (repeatable)
#   --out PATH    write the report here instead of $QA_HOME/reports/YYYY-MM-DD.json (refuses to target a
#                 daily report file). On-merge trigger: --pr N --out $QA_HOME/reports/merged/PR-N.json
#   --no-reuse    re-test merged PRs even if reports/merged/PR-N.json exists for the same merge commit
#   --wait-lock [SECONDS]  queue behind a running instance (default 14400 = 4h) instead of exit 75
#   --pr-timeout MIN  per-PR time budget; a PR that exceeds it is stopped and reported as not-tested (P2)
#                 (env ARGUS_PR_TIMEOUT_MIN; default: no limit)
#   --deadline HH:MM  Asia/Shanghai wall-clock deadline (or ISO time); PRs not started by then are
#                 reported as not-tested (P2) and a running PR is stopped (env ARGUS_DEADLINE)
#   --no-issues   default behaviour; accepted for explicitness
#   --create-issues  OFF by default. Only acts when ARGUS_ALLOW_ISSUES=1 is also set.
#   -h, --help    print this help
#
# Environment (all optional):
#   QA_HOME            data dir: argus-root/, reports/, cache/ms-playwright, .venv   (default /workspace/mivo-qa)
#   QA_SECRETS_FILE    secrets env file, see secrets.env.example        (default /home/box/.config/mivo-qa/secrets.env)
#   PANOPTES_DIR       code dir (argus/, ming-qa/)                       (default: this script's directory)
#   ARGUS_LLM_MODEL    review/summary model                              (default openai/gpt-6-luna)
#
# Output: $QA_HOME/reports/YYYY-MM-DD.json  (logs: $QA_HOME/argus-root/logs)
# Exit codes: 0 ok/degraded, 3 pipeline failed (see system_health), 1 usage/config error, 75 another run active.
set -euo pipefail

PANOPTES_DIR="${PANOPTES_DIR:-$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)}"
QA_HOME="${QA_HOME:-/workspace/mivo-qa}"
SECRETS="${QA_SECRETS_FILE:-/home/box/.config/mivo-qa/secrets.env}"

# --wait-lock [SECONDS]: wait for the single-instance lock instead of exiting 75 (default 14400 = 4h).
# Consumed here; everything else is passed to the Node CLI unchanged.
WAIT_LOCK=""
ARGS=()
while (( $# )); do
  case "$1" in
    -h|--help)
      sed -n '2,/^set -euo/{/^set -euo/d;s/^# \{0,1\}//;p}' "${BASH_SOURCE[0]}"; exit 0 ;;
    --wait-lock)
      WAIT_LOCK=14400
      if [[ "${2:-}" =~ ^[0-9]+$ ]]; then WAIT_LOCK="$2"; shift; fi ;;
    --wait-lock=*)
      WAIT_LOCK="${1#--wait-lock=}"
      [[ "$WAIT_LOCK" =~ ^[0-9]+$ ]] || { echo "--wait-lock needs whole seconds" >&2; exit 1; } ;;
    *) ARGS+=("$1") ;;
  esac
  shift
done
set -- ${ARGS[@]+"${ARGS[@]}"}

# Issue creation is OFF unless --create-issues is given AND ARGUS_ALLOW_ISSUES=1 is in the caller's env.
CREATE=""
for a in "$@"; do
  [[ "$a" == "--create-issues" ]] && CREATE=1
done
ALLOW_ISSUES=""
[[ -n "$CREATE" && "${ARGUS_ALLOW_ISSUES:-}" == "1" ]] && ALLOW_ISSUES=1
unset ARGUS_ALLOW_ISSUES || true

# Node 24 via nvm (system node stays 20 for other tools on this box)
unset NPM_CONFIG_PREFIX npm_config_prefix
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
# shellcheck disable=SC1091
. "$NVM_DIR/nvm.sh" >/dev/null
nvm use --silent 24 >/dev/null
node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 24 ? 0 : 1)' || { echo "Node 24+ required" >&2; exit 1; }

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
export ARGUS_ROOT="$QA_HOME/argus-root"
export ARGUS_REPORTS_DIR="$QA_HOME/reports"
export ARGUS_SECRETS_FILE="$SECRETS"
export ARGUS_MINGQA_DIR="${ARGUS_MINGQA_DIR:-$PANOPTES_DIR/ming-qa}"
export ARGUS_PYTHON="${ARGUS_PYTHON:-$QA_HOME/.venv/bin/python}"
export ARGUS_LLM_MODEL="${ARGUS_LLM_MODEL:-openai/gpt-6-luna}"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$QA_HOME/cache/ms-playwright}"
export TZ=Asia/Shanghai
mkdir -p "$ARGUS_ROOT/logs" "$ARGUS_REPORTS_DIR"

# One run at a time
exec 9>"$ARGUS_ROOT/nightly.lock"
if [[ -n "$WAIT_LOCK" ]]; then
  echo "[panoptes] waiting up to ${WAIT_LOCK}s for the run lock…" >&2
  flock -w "$WAIT_LOCK" 9 || { echo "gave up waiting for the run lock after ${WAIT_LOCK}s" >&2; exit 75; }
else
  flock -n 9 || { echo "another run is active (use --wait-lock to queue)" >&2; exit 75; }
fi

cd "$PANOPTES_DIR/argus"
LOG="$ARGUS_ROOT/logs/nightly-$(date +%Y%m%dT%H%M%S).log"
echo "[panoptes] code: $PANOPTES_DIR ($(git -C "$PANOPTES_DIR" rev-parse --short HEAD 2>/dev/null || echo '?'))  data: $QA_HOME  log: $LOG" >&2
# Lower priority so the box stays responsive
nice -n 10 node --disable-warning=ExperimentalWarning src/cli.mjs nightly "$@" 2> >(tee -a "$LOG" >&2) | tee -a "$LOG"
exit "${PIPESTATUS[0]}"
