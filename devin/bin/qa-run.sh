#!/bin/bash
# qa-run.sh — test one Mivo PR in a Devin Linux session (PraiseZhu/panoptes#311).
#
#   qa-run.sh --pr N [--day YYYY-MM-DD] [--try K] [--mode event|catch-up|backfill|replay] [--full-steps] [--out PATH]
#             [--platform P] [--requested-mode M]
#
#   1 init     devin/bin/session-init.sh qa --pr N ($QA_RUN_INIT)                     failure -> exit 91
#   2 seed     devin/bin/quota-sync.mjs seed --day D                                 failure -> exit 91
#   3 run      run-nightly.sh ($QA_RUN_NIGHTLY) with HOME=/home/box, PATH=/usr/local/bin:/usr/bin:/bin,
#              PANOPTES_CATCHUP_TIMER=0, QA_HOME and QA_SECRETS_FILE forced:
#                event     --pr N --date D --write-merged --wait-lock 14400 --no-issues --trigger event --out OUT
#                catch-up  same, --trigger catch-up                     (argus catchUpChildArgs / mergewatch priority_cmd)
#                backfill  same, --trigger manual --quota-pool production        (outage backfill of a missed merge)
#                replay    --pr N --date D --no-write-merged --no-reuse --wait-lock 14400 --no-issues --trigger manual
#                          --quota-pool test --out OUT; --full-steps also adds --no-review --no-ai-tests and drops the Jev
#                          key (ARGUS_TYPESAFE_API_KEY) from the environment and the secrets file
#              OUT defaults to $QA_HOME/reports/catch-up/PR-N.json, for replay $QA_HOME/pi/out/devin/runs/PR-N/replay-D-tryK.json
#   4 export   quota-sync.mjs export --pr N --day D                                  (not for replay)
#   5 record   $QA_HOME/pi/out/devin/runs/PR-N/D-tryK-MODE.json with platform, requested_mode and effective_mode
#              (effective_mode = $DEVIN_SESSION_MODE as observed in this session, "unknown" when unset; never the input)
#   6 push     devin/bin/state-sync.sh push qa --pr N --mode MODE ($QA_RUN_SYNC)     (not for replay) failure -> exit 90
# The two secret files are deleted on every exit (trap). Exit: run-nightly.sh's own code (0 · 1 · 3 · 75 · 76),
# 90 push failed, 91 init failed, 2 usage. Last stdout line: one JSON object.
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
PANOPTES_DIR=$(cd "$HERE/../.." && pwd)
INIT="${QA_RUN_INIT:-$HERE/session-init.sh}"
SYNC="${QA_RUN_SYNC:-$HERE/state-sync.sh}"
NIGHTLY="${QA_RUN_NIGHTLY:-$PANOPTES_DIR/run-nightly.sh}"
QUOTA="$HERE/quota-sync.mjs"
QA_DIR="${QA_HOME:-/workspace/mivo-qa}"
SECRETS="${QA_SECRETS_FILE:-/home/box/.config/mivo-qa/secrets.env}"
APP_KEY="${PRAISE_QA_KEY:-/home/box/.secrets/praise-qa/app.pem}"
BOX_HOME=/home/box BOX_PATH=/usr/local/bin:/usr/bin:/bin

usage() { echo "usage: qa-run.sh --pr N [--day YYYY-MM-DD] [--try K] [--mode event|catch-up|backfill|replay] [--full-steps] [--out PATH] [--platform P] [--requested-mode M]" >&2; exit 2; }
trap 'rm -f -- "$SECRETS" "$APP_KEY"' EXIT

PR="" DAY="" TRY=1 MODE=event FULL=false OUT="" PLATFORM="" REQUESTED=unknown
while (($#)); do
  case "$1" in
    --full-steps) FULL=true ;;
    --pr|--day|--try|--mode|--out|--platform|--requested-mode) (($# >= 2)) || usage
      case "$1" in
        --pr) PR=$2 ;; --day) DAY=$2 ;; --try) TRY=$2 ;; --mode) MODE=$2 ;; --out) OUT=$2 ;;
        --platform) PLATFORM=$2 ;; --requested-mode) REQUESTED=$2 ;;
      esac; shift ;;
    *) usage ;;
  esac
  shift
done
[[ "$PR" =~ ^[1-9][0-9]*$ && "$TRY" =~ ^[1-9][0-9]*$ ]] || usage
[[ "$MODE" =~ ^(event|catch-up|backfill|replay)$ ]] || usage
[[ "$FULL" == false || "$MODE" == replay ]] || { echo "qa-run: --full-steps only applies to --mode replay" >&2; usage; }
DAY=${DAY:-$(TZ=Asia/Shanghai date +%F)}
[[ "$DAY" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || usage
PLATFORM=${PLATFORM:-$(uname -s | tr '[:upper:]' '[:lower:]')}
EFFECTIVE=${DEVIN_SESSION_MODE:-unknown}
QA_DIR=${QA_DIR%/}
RUNS_DIR="$QA_DIR/pi/out/devin/runs/PR-$PR"
RECORD="$RUNS_DIR/$DAY-try$TRY-$MODE.json"
if [[ "$MODE" == replay ]]; then OUT=${OUT:-$RUNS_DIR/replay-$DAY-try$TRY.json}; else OUT=${OUT:-$QA_DIR/reports/catch-up/PR-$PR.json}; fi

result() { # result EXIT_CODE RUN_EXIT PUSHED QUOTA_EXPORTED -> run record (when the run started) + last stdout line
  python3 - "$@" "$PR" "$DAY" "$TRY" "$MODE" "$FULL" "$PLATFORM" "$REQUESTED" "$EFFECTIVE" "$OUT" "$RECORD" \
    "$(date -Is)" "${CMD[@]}" <<'PY'
import json, os, sys
(code, run_code, pushed, exported, pr, day, try_, mode, full, platform, requested, effective, out, record, at, *cmd) = sys.argv[1:]
rec = {"pr": int(pr), "day": day, "try": int(try_), "mode": mode, "full_steps": full == "true", "platform": platform,
       "requested_mode": requested, "effective_mode": effective,
       "effective_mode_source": "DEVIN_SESSION_MODE" if effective != "unknown" else None,
       "exit_code": int(code), "run_exit_code": int(run_code) if run_code else None,
       "pushed": {"true": True, "false": False}.get(pushed), "quota_exported": {"true": True, "false": False}.get(exported),
       "out": out, "record": record if run_code else None, "command": cmd, "finished_at": at}
if run_code and pushed == "pending":
    os.makedirs(os.path.dirname(record), exist_ok=True)
    with open(record + ".tmp", "w") as f: json.dump(rec, f, ensure_ascii=False, indent=2); f.write("\n")
    os.replace(record + ".tmp", record)
else:
    print(json.dumps(rec, ensure_ascii=False))
PY
}

CMD=()
init_failed() { echo "qa-run: $1 failed" >&2; result 91 "" false false; exit 91; }
"$INIT" qa --pr "$PR" --qa-home "$QA_DIR" >&2 || init_failed session-init
QA_HOME="$QA_DIR" node "$QUOTA" seed --day "$DAY" --qa-home "$QA_DIR" >&2 || init_failed "quota seed"

CMD=("$NIGHTLY" --pr "$PR" --date "$DAY")
ENV=(HOME="$BOX_HOME" PATH="$BOX_PATH" PANOPTES_CATCHUP_TIMER=0 QA_HOME="$QA_DIR" QA_SECRETS_FILE="$SECRETS")
UNSET=()
case "$MODE" in
  event|catch-up) CMD+=(--write-merged --wait-lock 14400 --no-issues --trigger "$MODE") ;;
  backfill) CMD+=(--write-merged --wait-lock 14400 --no-issues --trigger manual --quota-pool production) ;;
  replay) CMD+=(--no-write-merged --no-reuse --wait-lock 14400 --no-issues --trigger manual --quota-pool test)
    if [[ "$FULL" == true ]]; then
      CMD+=(--no-review --no-ai-tests)
      UNSET=(-u ARGUS_TYPESAFE_API_KEY)
      sed -i '/^ARGUS_TYPESAFE_API_KEY=/d' "$SECRETS" || init_failed "drop Jev key"
    fi ;;
esac
CMD+=(--out "$OUT")
mkdir -p "$(dirname "$OUT")"
env "${UNSET[@]}" "${ENV[@]}" bash "${CMD[@]}" >&2
RC=$?

EXPORTED=false PUSHED=false CODE=$RC
if [[ "$MODE" != replay ]]; then
  if QA_HOME="$QA_DIR" node "$QUOTA" export --pr "$PR" --day "$DAY" --qa-home "$QA_DIR" >&2; then EXPORTED=true
  else echo "qa-run: quota export failed (the next session will under-count today's calls)" >&2; fi
fi
result "$RC" "$RC" pending "$EXPORTED"
if [[ "$MODE" != replay ]]; then
  if "$SYNC" push qa --pr "$PR" --mode "$MODE" --qa-home "$QA_DIR" >&2; then PUSHED=true
  else echo "qa-run: state-sync push failed" >&2; CODE=90; fi
fi
result "$CODE" "$RC" "$PUSHED" "$EXPORTED"
exit "$CODE"
