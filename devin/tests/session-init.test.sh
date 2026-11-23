#!/bin/bash
# session-init.sh + qa-run.sh tests (PraiseZhu/panoptes#311): secret files, real state-sync.sh pull from an empty QA home
# against a throw-away bare remote, slots-only pins check, qa-run argument assembly / exit codes / run metadata.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
BIN="$HERE/../bin"
INIT="$BIN/session-init.sh" QARUN="$BIN/qa-run.sh"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
D=box/workspace/mivo-qa
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); echo "ok   $1"; }
bad() { FAIL=$((FAIL + 1)); echo "FAIL $1" >&2; }
check() { local name=$1; shift; if "$@"; then ok "$name"; else bad "$name"; fi; }
eq() { local name=$1 want=$2 got=$3; if [[ "$want" == "$got" ]]; then ok "$name"; else bad "$name: want [$want] got [$got]"; fi; }
jget() { python3 -c 'import json,sys; v=json.loads(sys.argv[1]); [v := v[k] for k in sys.argv[2:]]; print(json.dumps(v) if not isinstance(v, str) else v)' "$@"; }
mode_of() { stat -c %a "$1" 2>/dev/null; }

SENT_LLM=SENTINEL_LLM_9f3a1c SENT_JEV=SENTINEL_JEV_77c1e0 SENT_PEM=SENTINEL_PEM_c0de42
SENT_SLACK=SENTINEL_SLACK_5b8d21 SENT_DEEPSEEK=SENTINEL_DEEPSEEK_e4a907
# shellcheck disable=SC2016 # literal $x: must survive bash source and lib.py unquoted
SENT_URL='https://llm.example.invalid/v1?a=1&b=$x c!d"e\f#g;h|i*SENTINEL_URL_31f0aa'
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL="$T/gitconfig" STATE_SYNC_BACKOFF_MIN=0 STATE_SYNC_BACKOFF_MAX=1
: >"$GIT_CONFIG_GLOBAL"
export SESSION_INIT_SKIP_TOKEN=1 SESSION_INIT_SKIP_FF=1 SESSION_INIT_BIN_DIR="$T/usr-local-bin" SESSION_INIT_WORKSPACE="$T/ws"
export GH_RESTORE_REAL="$T/native-gh" GH_RESTORE_SUDO="" SESSION_INIT_MEMINFO="$T/meminfo" SESSION_INIT_MIRROR_URL="file://$T/mivo.git"
export SESSION_INIT_SELFCHECK="$T/selfcheck.sh" SESSION_INIT_PINS="$T/pins.json"
export SESSION_INIT_MERGER_DIR="$T/mivo-merger"
export QA_SECRETS_FILE="$T/box/.config/mivo-qa/secrets.env" PRAISE_QA_KEY="$T/box/.secrets/praise-qa/app.pem"
export QA_LLM_BASE_URL=https://llm.example.invalid/v1 QA_LLM_API_KEY=$SENT_LLM ARGUS_TYPESAFE_API_KEY=$SENT_JEV
PRAISE_QA_APP_KEY_B64=$(printf -- '-----BEGIN KEY-----\n%s\n-----END KEY-----\n' "$SENT_PEM" | base64 -w0)
export PRAISE_QA_APP_KEY_B64
unset DEVIN_SESSION_MODE QA_HOME
mkdir -p "$SESSION_INIT_BIN_DIR" "$T/ws"
printf '#!/bin/sh\necho native\n' >"$GH_RESTORE_REAL"; chmod +x "$GH_RESTORE_REAL"
printf 'MemTotal: 16000000 kB\nSwapTotal: 8388608 kB\nSwapFree: 8388608 kB\n' >"$SESSION_INIT_MEMINFO"
# shellcheck disable=SC2016 # expanded by the self-check stub
printf '#!/bin/bash\necho "timer=${PANOPTES_CATCHUP_TIMER-unset}" >>"%s"\n' "$T/selfcheck.log" >"$SESSION_INIT_SELFCHECK"
chmod +x "$SESSION_INIT_SELFCHECK"
git init -q --bare -b main "$T/mivo.git"
git init -q -b main "$T/mivo-src"
mkdir -p "$T/mivo-src/scripts/loops/automerge"
echo '{"testPolicy":true}' >"$T/mivo-src/scripts/loops/automerge/policy.json"
git -C "$T/mivo-src" add scripts/loops/automerge/policy.json
git -C "$T/mivo-src" -c user.name=m -c user.email=m@x commit -q -m init
git -C "$T/mivo-src" push -q "$T/mivo.git" main

SCRIPTS=(report/slack/send-slack-report.mjs report/send-dm-recorded.mjs report/open-handoff-issue.mjs report/lib/jsonlog.mjs
         ledger/build-ledger.mjs ledger/apply-fix-retests.mjs handoff/gen/gen.py)
git init -q --bare -b devin "$T/remote.git"
git -C "$T/remote.git" config uploadpack.allowFilter true
git init -q -b devin "$T/seed"
s="$T/seed/$D"
mkdir -p "$s/reports/merged"
echo '{"pr":2464}' >"$s/reports/merged/PR-2464.json"
echo '{"pr":1000}' >"$s/reports/merged/PR-1000.json"
for f in "${SCRIPTS[@]}"; do mkdir -p "$(dirname "$s/$f")"; echo "// $f v1" >"$s/$f"; done
echo pi >"$T/seed/box/workspace/fleet-mode"
python3 - "$s" "$SESSION_INIT_PINS" "${SCRIPTS[@]}" <<'PY'
import hashlib, json, os, sys
root, out, *rels = sys.argv[1:]
json.dump({"sha256": {r: hashlib.sha256(open(os.path.join(root, r), "rb").read()).hexdigest() for r in rels}}, open(out, "w"))
PY
git -C "$T/seed" add -A && git -C "$T/seed" -c user.name=s -c user.email=s@x commit -q -m seed
git -C "$T/seed" push -q "$T/remote.git" devin
URL="file://$T/remote.git"
remote_commit() { # remote_commit SHELL-SNIPPET: change the remote devin branch (cwd = data dir)
  rm -rf "$T/other"; git clone -q -b devin "$URL" "$T/other"
  (cd "$T/other/$D" && eval "$1")
  git -C "$T/other" add -A; git -C "$T/other" -c user.name=o -c user.email=o@x commit -q -m other; git -C "$T/other" push -q origin devin
}
ALL="$T/all-output.log"
init() { # init NAME ARGS...: real session-init.sh against the bare remote with an empty QA home $T/NAME/mivo-qa
  local name=$1; shift
  RC=0
  OUTPUT=$("$INIT" "$@" --repo-url "$URL" --work-dir "$T/wd-$name" --qa-home "$T/$name/mivo-qa" 2>"$T/$name.err") || RC=$?
  printf '%s\n' "$OUTPUT" >>"$ALL"; cat "$T/$name.err" >>"$ALL"
  LAST=$(tail -n1 <<<"$OUTPUT")
}

# --- SC1 / SC2: qa tier from an empty QA home, real state-sync pull qa, no pinned scripts
init qa1 qa --pr 2464
eq "qa: exit 0" 0 "$RC"
eq "qa: json ok" true "$(jget "$LAST" ok)"
eq "qa: json tier" qa "$(jget "$LAST" tier)"
eq "qa: json platform" "$(uname -s | tr '[:upper:]' '[:lower:]')" "$(jget "$LAST" platform)"
eq "qa: steps (no pins)" '["token", "secrets", "swap", "ff", "links", "pull", "mirror", "check"]' "$(jget "$LAST" steps)"
check "qa: pulled own PR state" test -f "$T/qa1/mivo-qa/reports/merged/PR-2464.json"
check "qa: other PR not pulled" test ! -e "$T/qa1/mivo-qa/reports/merged/PR-1000.json"
for f in "${SCRIPTS[@]}"; do check "qa: $f not pulled" test ! -e "$T/qa1/mivo-qa/$f"; done
eq "qa: secrets file 600" 600 "$(mode_of "$QA_SECRETS_FILE")"
eq "qa: app key 600" 600 "$(mode_of "$PRAISE_QA_KEY")"
check "qa: GITHUB_TOKEN placeholder" grep -qx "GITHUB_TOKEN='ghs_devin_no_pat'" "$QA_SECRETS_FILE"
no_key() { ! grep -q "^$1=" "$QA_SECRETS_FILE"; }
check "qa: no Slack token configured -> not written" no_key MIVO_SLACK_BOT_TOKEN
check "qa: no DeepSeek key configured -> not written" no_key DEEPSEEK_API_KEY
eq "qa: secrets file is sourceable" "$SENT_LLM" "$(unset QA_LLM_API_KEY; set -a; # shellcheck disable=SC1090
  . "$QA_SECRETS_FILE"; echo "$QA_LLM_API_KEY")"
check "qa: app key decoded" grep -q "$SENT_PEM" "$PRAISE_QA_KEY"
eq "qa: self-check with PANOPTES_CATCHUP_TIMER=0" timer=0 "$(tail -n1 "$T/selfcheck.log")"
for l in gh gh-praise-qa git-credential-praise-qa pi-dispatch; do check "qa: link $l" test -L "$SESSION_INIT_BIN_DIR/$l"; done
eq "qa: gh -> wrapper" "$(readlink -f "$HERE/../../pi/bin/gh_wrapper.py")" "$(readlink -f "$SESSION_INIT_BIN_DIR/gh")"
eq "qa: /workspace/panoptes link" "$(cd "$HERE/../.." && pwd -P)" "$(readlink -f "$T/ws/panoptes")"
eq "qa: /workspace/mivo-qa link" "$(readlink -f "$T/qa1/mivo-qa")" "$(readlink -f "$T/ws/mivo-qa")"
check "qa: mirror cloned" git --git-dir="$T/qa1/mivo-qa/argus-root/cache/mirrors/xindong__mivo-canvas-plugin.git" rev-parse -q --verify HEAD >/dev/null
mkdir -p "$T/half/mivo-qa/argus-root/cache/mirrors/xindong__mivo-canvas-plugin.git/objects"
rm -f "$T/ws/mivo-qa"
init half qa --pr 2464
eq "qa: half-finished mirror re-cloned (exit 0)" 0 "$RC"
check "qa: half-finished mirror now valid" git --git-dir="$T/half/mivo-qa/argus-root/cache/mirrors/xindong__mivo-canvas-plugin.git" rev-parse -q --verify HEAD >/dev/null

# Issue #325: only a clean panoptes checkout may be moved to make room for the workspace link.
for fixture in clean dirty foreign; do
  workspace="$T/ws-$fixture"
  checkout="$workspace/panoptes"
  git clone -q "$T/mivo-src" "$checkout"
  origin=https://github.com/PraiseZhu/panoptes.git
  [[ "$fixture" != foreign ]] || origin=https://github.com/PraiseZhu/other.git
  git -C "$checkout" remote set-url origin "$origin"
  policy=scripts/loops/automerge/policy.json
  [[ "$fixture" != dirty ]] || echo 'local edit' >>"$checkout/$policy"

  SESSION_INIT_WORKSPACE="$workspace" init "workspace-$fixture" ops
  mapfile -t snapshots < <(find "$workspace" -maxdepth 1 -name 'panoptes.snapshot-*')
  if [[ "$fixture" == clean ]]; then
    eq "workspace clean: exit 0" 0 "$RC"
    eq "workspace clean: reaches check after links" '["token", "secrets", "swap", "ff", "links", "check"]' "$(jget "$LAST" steps)"
    check "workspace clean: panoptes is a symlink" test -L "$checkout"
    eq "workspace clean: link points to this checkout" "$(cd "$HERE/../.." && pwd -P)" "$(readlink -f "$checkout")"
    eq "workspace clean: one snapshot" 1 "${#snapshots[@]}"
    snapshot="${snapshots[0]:-$workspace/no-snapshot}"
    if [[ "${snapshot##*/}" =~ ^panoptes\.snapshot-[0-9]{8}T[0-9]{6}Z$ ]]; then
      ok "workspace clean: UTC timestamp in snapshot name"
    else
      bad "workspace clean: UTC timestamp in snapshot name"
    fi
    check "workspace clean: clone saved in snapshot" cmp "$T/mivo-src/$policy" "$snapshot/$policy"
  else
    eq "workspace $fixture: exit 91" 91 "$RC"
    eq "workspace $fixture: failed step" links "$(jget "$LAST" failed_step)"
    check "workspace $fixture: original directory is not a symlink" test ! -L "$checkout"
    eq "workspace $fixture: original origin" "$origin" "$(git -C "$checkout" remote get-url origin)"
    eq "workspace $fixture: no snapshot" 0 "${#snapshots[@]}"
    if [[ "$fixture" == dirty ]]; then
      check "workspace dirty: local edit preserved" grep -qx 'local edit' "$checkout/$policy"
    fi
  fi
done

# secrets.env reads back byte-identical through bash `source` and pi/bin/lib.py secret_env_dict() (Slack/DeepSeek optional keys)
same_both() { # same_both KEY WANT: prints "bash=same py=same" without ever printing a value
  local b p
  # shellcheck disable=SC2016 # positional args of the inner bash
  b=$(env -i bash -c 'set -a; . "$1"; printf %s "${!2}"' _ "$QA_SECRETS_FILE" "$1")
  p=$(HOME="$T/box" PI_STATE="$T/pistate" PI_WORK="$T/piwork" python3 -c \
    'import sys; sys.path.insert(0, sys.argv[1]); import lib; sys.stdout.write(lib.secret_env_dict().get(sys.argv[2], "<missing>"))' \
    "$HERE/../../pi/bin" "$1")
  printf 'bash=%s py=%s' "$([[ "$b" == "$2" ]] && echo same || echo diff)" "$([[ "$p" == "$2" ]] && echo same || echo diff)"
}
rm -f "$T/ws/mivo-qa"
MIVO_SLACK_BOT_TOKEN=$SENT_SLACK DEEPSEEK_API_KEY=$SENT_DEEPSEEK QA_LLM_BASE_URL=$SENT_URL init quote qa --pr 2464
eq "quote: exit 0" 0 "$RC"
eq "quote: secrets file 600" 600 "$(mode_of "$QA_SECRETS_FILE")"
eq "quote: MIVO_SLACK_BOT_TOKEN bash/lib.py identical" "bash=same py=same" "$(same_both MIVO_SLACK_BOT_TOKEN "$SENT_SLACK")"
eq "quote: DEEPSEEK_API_KEY bash/lib.py identical" "bash=same py=same" "$(same_both DEEPSEEK_API_KEY "$SENT_DEEPSEEK")"
eq "quote: ?a=1&b=\$x value bash/lib.py identical" "bash=same py=same" "$(same_both QA_LLM_BASE_URL "$SENT_URL")"
eq "quote: QA_LLM_API_KEY bash/lib.py identical" "bash=same py=same" "$(same_both QA_LLM_API_KEY "$SENT_LLM")"
eq "quote: GITHUB_TOKEN placeholder bash/lib.py identical" "bash=same py=same" "$(same_both GITHUB_TOKEN ghs_devin_no_pat)"
for bad_value in "it's-SENTINEL_QUOTE_6d2c" $'line1\nSENTINEL_NL_8a4e'; do
  rm -f "$T/ws/mivo-qa" "$QA_SECRETS_FILE"
  MIVO_SLACK_BOT_TOKEN=$bad_value init badq qa --pr 2464
  eq "quote: unsafe value rejected (exit 91)" 91 "$RC"
  check "quote: unsafe value names the key" grep -q MIVO_SLACK_BOT_TOKEN "$T/badq.err"
  check "quote: no secrets file left" test ! -e "$QA_SECRETS_FILE"
done
rm -f "$T/ws/mivo-qa"

RC=0; "$INIT" --cleanup >>"$ALL" 2>&1 || RC=$?
eq "cleanup: exit 0" 0 "$RC"
check "cleanup: secrets file gone" test ! -e "$QA_SECRETS_FILE"
check "cleanup: app key gone" test ! -e "$PRAISE_QA_KEY"

# --- SC2: slots tier, real state-sync pull slots, pins
rm -f "$T/ws/mivo-qa"
init slots1 slots
eq "slots: exit 0 when all 7 match" 0 "$RC"
eq "slots: json tier" slots "$(jget "$LAST" tier)"
eq "slots: steps include pins" '["token", "secrets", "swap", "ff", "links", "pull", "mirror", "pins", "check"]' "$(jget "$LAST" steps)"
for f in "${SCRIPTS[@]}"; do check "slots: $f pulled" test -f "$T/slots1/mivo-qa/$f"; done
for attempt in 1 2; do
  if ((attempt == 2)); then init slots1 slots; fi
  eq "slots merger: attempt $attempt exit 0" 0 "$RC"
  check "slots merger: policy matches mirror ($attempt)" cmp "$T/mivo-src/scripts/loops/automerge/policy.json" \
    "$SESSION_INIT_MERGER_DIR/app/scripts/loops/automerge/policy.json"
  check "slots merger: config ($attempt)" python3 -c \
    'import json,sys; assert json.load(open(sys.argv[1]+"/config.json")) == {"pausedPath": sys.argv[1]+"/PAUSED"}' "$SESSION_INIT_MERGER_DIR"
  check "slots merger: paused ($attempt)" test -f "$SESSION_INIT_MERGER_DIR/PAUSED"
done

remote_commit "echo tampered >>ledger/build-ledger.mjs"
rm -f "$T/ws/mivo-qa"
init slots2 slots
eq "slots: tampered script -> exit 91" 91 "$RC"
check "slots: tampered file named in stderr" grep -q 'sha256 mismatch ledger/build-ledger.mjs' "$T/slots2.err"
eq "slots: tampered json failed_step" pins "$(jget "$LAST" failed_step)"
check "slots: secrets removed after failure" test ! -e "$QA_SECRETS_FILE"
check "slots: app key removed after failure" test ! -e "$PRAISE_QA_KEY"

remote_commit "git checkout -q HEAD~1 -- ledger/build-ledger.mjs 2>/dev/null || echo '// ledger/build-ledger.mjs v1' >ledger/build-ledger.mjs; rm handoff/gen/gen.py"
rm -f "$T/ws/mivo-qa"
init slots3 slots
eq "slots: missing script -> exit 91" 91 "$RC"
check "slots: missing file named in stderr" grep -q 'missing handoff/gen/gen.py' "$T/slots3.err"

RC=0; OUT=$(env -u QA_LLM_API_KEY "$INIT" ops --qa-home "$T/ops/mivo-qa" 2>"$T/ops.err") || RC=$?
printf '%s\n' "$OUT" >>"$ALL"; cat "$T/ops.err" >>"$ALL"
eq "ops: missing secret -> exit 91" 91 "$RC"
check "ops: missing variable named" grep -q 'QA_LLM_API_KEY is not set' "$T/ops.err"
RC=0; "$INIT" qa >>"$ALL" 2>&1 || RC=$?
eq "qa without --pr -> usage 2" 2 "$RC"

# real pins.json: the 7 data-dir scripts, each a 64-hex SHA-256
eq "pins.json: 7 scripts" "$(printf '%s\n' "${SCRIPTS[@]}" | sort)" \
  "$(python3 -c 'import json,sys; print("\n".join(sorted(json.load(open(sys.argv[1]))["sha256"])))' "$HERE/../conf/pins.json")"
check "pins.json: all values are 64-hex" python3 -c 'import json,re,sys; sys.exit(0 if all(re.fullmatch("[0-9a-f]{64}", v) for v in json.load(open(sys.argv[1]))["sha256"].values()) else 1)' "$HERE/../conf/pins.json"

# --- SC3: qa-run.sh with stubbed init / run-nightly / state-sync (real quota-sync.mjs)
Q="$T/qr/mivo-qa"
cat >"$T/stub-init.sh" <<'SH'
#!/bin/bash
echo "$*" >>"$STUB_DIR/init.args"
mkdir -p "$(dirname "$QA_SECRETS_FILE")" "$(dirname "$PRAISE_QA_KEY")"
(umask 077; printf 'GITHUB_TOKEN=ghs_devin_no_pat\nARGUS_TYPESAFE_API_KEY=x\n' >"$QA_SECRETS_FILE"; echo k >"$PRAISE_QA_KEY")
exit "${STUB_INIT_RC:-0}"
SH
cat >"$T/stub-nightly.sh" <<'SH'
#!/bin/bash
printf '%s\n' "$@" >"$STUB_DIR/nightly.args"
{ echo "timer=${PANOPTES_CATCHUP_TIMER-unset}"; echo "home=$HOME"; echo "path=$PATH"; echo "qa_home=$QA_HOME"
  echo "real_gh=${PRAISE_QA_REAL_GH-unset}"
  echo "jev_env=${ARGUS_TYPESAFE_API_KEY-unset}"; grep -c '^ARGUS_TYPESAFE_API_KEY=' "$QA_SECRETS_FILE" | sed 's/^/jev_file=/'
  echo "secrets_mode=$(stat -c %a "$QA_SECRETS_FILE")"; } >"$STUB_DIR/nightly.env"
exit "${STUB_RC:-0}"
SH
cat >"$T/stub-sync.sh" <<'SH'
#!/bin/bash
echo "$*" >>"$STUB_DIR/sync.args"
exit "${STUB_SYNC_RC:-0}"
SH
chmod +x "$T"/stub-*.sh
export STUB_DIR="$T/stub"
qarun() { # qarun ARGS...: RC, LAST
  rm -rf "$STUB_DIR"; mkdir -p "$STUB_DIR"
  RC=0
  OUTPUT=$(QA_HOME="$Q" QA_RUN_INIT="${QR_INIT:-$T/stub-init.sh}" QA_RUN_NIGHTLY="$T/stub-nightly.sh" QA_RUN_SYNC="$T/stub-sync.sh" \
    "$QARUN" "$@" 2>"$T/qr.err") || RC=$?
  printf '%s\n' "$OUTPUT" >>"$ALL"; cat "$T/qr.err" >>"$ALL"
  LAST=$(tail -n1 <<<"$OUTPUT")
}
args() { paste -sd' ' "$STUB_DIR/nightly.args"; }
DAY=2026-11-19
common="--pr 2464 --date $DAY"

qarun --pr 2464 --day $DAY --mode event --platform linux --requested-mode swe-2
eq "event: exit 0" 0 "$RC"
eq "event: run-nightly args" "$common --write-merged --wait-lock 14400 --no-issues --trigger event --out $Q/reports/catch-up/PR-2464.json" "$(args)"
eq "event: PANOPTES_CATCHUP_TIMER=0" timer=0 "$(grep timer "$STUB_DIR/nightly.env")"
eq "event: box HOME" home=/home/box "$(grep home= "$STUB_DIR/nightly.env" | head -1)"
eq "event: box PATH" path=/usr/local/bin:/usr/bin:/bin "$(grep path= "$STUB_DIR/nightly.env")"
eq "event: real gh" real_gh=/usr/bin/gh "$(grep real_gh= "$STUB_DIR/nightly.env")"
eq "event: QA_HOME" "qa_home=$Q" "$(grep qa_home= "$STUB_DIR/nightly.env")"
eq "event: secrets 600 during the run" secrets_mode=600 "$(grep secrets_mode= "$STUB_DIR/nightly.env")"
eq "event: init args" "qa --pr 2464 --qa-home $Q" "$(cat "$STUB_DIR/init.args")"
eq "event: push" "push qa --pr 2464 --mode event --qa-home $Q" "$(cat "$STUB_DIR/sync.args")"
eq "event: json platform" linux "$(jget "$LAST" platform)"
eq "event: json requested_mode" swe-2 "$(jget "$LAST" requested_mode)"
eq "event: json effective_mode unknown without DEVIN_SESSION_MODE" unknown "$(jget "$LAST" effective_mode)"
eq "event: json pushed" true "$(jget "$LAST" pushed)"
REC="$Q/pi/out/devin/runs/PR-2464/$DAY-try1-event.json"
check "event: run record written" test -f "$REC"
eq "event: record platform/requested/effective" "linux swe-2 unknown" \
  "$(python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); print(r["platform"], r["requested_mode"], r["effective_mode"])' "$REC")"
check "event: quota file exported" test -f "$Q/pi/out/devin/quota/$DAY/PR-2464.json"
check "event: secrets file gone after success" test ! -e "$QA_SECRETS_FILE"
check "event: app key gone after success" test ! -e "$PRAISE_QA_KEY"

qarun --pr 2464 --day $DAY --mode catch-up --try 2
eq "catch-up: run-nightly args" "$common --write-merged --wait-lock 14400 --no-issues --trigger catch-up --out $Q/reports/catch-up/PR-2464.json" "$(args)"
eq "catch-up: push mode" "push qa --pr 2464 --mode catch-up --qa-home $Q" "$(cat "$STUB_DIR/sync.args")"
eq "catch-up: requested_mode unknown when not given" unknown "$(jget "$LAST" requested_mode)"
check "catch-up: record per try" test -f "$Q/pi/out/devin/runs/PR-2464/$DAY-try2-catch-up.json"

qarun --pr 2464 --day $DAY --mode backfill --out "$T/bf.json"
eq "backfill: run-nightly args" "$common --write-merged --wait-lock 14400 --no-issues --trigger manual --quota-pool production --out $T/bf.json" "$(args)"
eq "backfill: push mode" "push qa --pr 2464 --mode backfill --qa-home $Q" "$(cat "$STUB_DIR/sync.args")"

qarun --pr 2464 --day $DAY --mode replay --full-steps
eq "replay full: exit 0" 0 "$RC"
eq "replay full: run-nightly args" "$common --no-write-merged --no-reuse --wait-lock 14400 --no-issues --trigger manual --quota-pool test --no-review --no-ai-tests --out $Q/pi/out/devin/runs/PR-2464/replay-$DAY-try1.json" "$(args)"
eq "replay full: timer" timer=0 "$(grep timer "$STUB_DIR/nightly.env")"
eq "replay full: Jev key not in env" jev_env=unset "$(grep jev_env= "$STUB_DIR/nightly.env")"
eq "replay full: Jev key not in secrets file" jev_file=0 "$(grep jev_file= "$STUB_DIR/nightly.env")"
check "replay full: no push" test ! -e "$STUB_DIR/sync.args"
eq "replay full: json pushed" false "$(jget "$LAST" pushed)"
check "replay full: secrets gone" test ! -e "$QA_SECRETS_FILE"

qarun --pr 2464 --day $DAY --mode replay
eq "replay: production steps kept" "$common --no-write-merged --no-reuse --wait-lock 14400 --no-issues --trigger manual --quota-pool test --out $Q/pi/out/devin/runs/PR-2464/replay-$DAY-try1.json" "$(args)"
eq "replay: Jev key kept" jev_env=$SENT_JEV "$(grep jev_env= "$STUB_DIR/nightly.env")"
check "replay: no push" test ! -e "$STUB_DIR/sync.args"

RC=0; QA_HOME="$Q" QA_RUN_INIT="$T/stub-init.sh" "$QARUN" --pr 2464 --mode event --full-steps >>"$ALL" 2>&1 || RC=$?
eq "--full-steps outside replay -> usage 2" 2 "$RC"

DEVIN_SESSION_MODE=normal qarun --pr 2464 --day $DAY --mode event --requested-mode lite
eq "effective_mode read from DEVIN_SESSION_MODE" normal "$(jget "$LAST" effective_mode)"
eq "requested_mode kept separately" lite "$(jget "$LAST" requested_mode)"

for code in 1 3 75 76; do
  STUB_RC=$code qarun --pr 2464 --day $DAY --mode event
  eq "run-nightly exit $code preserved" "$code" "$RC"
  eq "run-nightly exit $code: still pushed" "push qa --pr 2464 --mode event --qa-home $Q" "$(cat "$STUB_DIR/sync.args")"
  eq "run-nightly exit $code: json exit_code" "$code" "$(jget "$LAST" exit_code)"
  check "run-nightly exit $code: secrets gone" test ! -e "$QA_SECRETS_FILE"
done

STUB_SYNC_RC=92 qarun --pr 2464 --day $DAY --mode event
eq "push failure -> 90" 90 "$RC"
eq "push failure: json pushed" false "$(jget "$LAST" pushed)"

STUB_INIT_RC=91 qarun --pr 2464 --day $DAY --mode event
eq "init failure -> 91" 91 "$RC"
check "init failure: run-nightly not called" test ! -e "$STUB_DIR/nightly.args"
check "init failure: no push" test ! -e "$STUB_DIR/sync.args"
check "init failure: secrets gone" test ! -e "$QA_SECRETS_FILE"
check "init failure: app key gone" test ! -e "$PRAISE_QA_KEY"
eq "init failure: json effective_mode" unknown "$(jget "$LAST" effective_mode)"

# SC1 with the real session-init.sh behind qa-run.sh: secret files gone after a passing and a failing run
cat >"$T/real-init.sh" <<SH
#!/bin/bash
exec "$INIT" "\$@" --repo-url "$URL" --work-dir "$T/wd-qr"
SH
chmod +x "$T/real-init.sh"
rm -f "$T/ws/mivo-qa"
QR_INIT="$T/real-init.sh" qarun --pr 2464 --day $DAY --mode event
eq "real init + pass: exit 0" 0 "$RC"
eq "real init: secrets were 600 during the run" secrets_mode=600 "$(grep secrets_mode= "$STUB_DIR/nightly.env")"
check "real init + pass: secrets gone" test ! -e "$QA_SECRETS_FILE"
check "real init + pass: app key gone" test ! -e "$PRAISE_QA_KEY"
STUB_RC=3 QR_INIT="$T/real-init.sh" qarun --pr 2464 --day $DAY --mode event
eq "real init + failing run: exit 3" 3 "$RC"
check "real init + failing run: secrets gone" test ! -e "$QA_SECRETS_FILE"
check "real init + failing run: app key gone" test ! -e "$PRAISE_QA_KEY"

# SC1: no sentinel secret value anywhere in stdout/stderr of every run above
for v in "$SENT_LLM" "$SENT_JEV" "$SENT_PEM" "$PRAISE_QA_APP_KEY_B64" "$SENT_SLACK" "$SENT_DEEPSEEK" "$SENT_URL" \
  SENTINEL_URL_31f0aa SENTINEL_QUOTE_6d2c SENTINEL_NL_8a4e; do
  # shellcheck disable=SC2016 # positional args of the inner bash
  check "no secret value in output (${v:0:12}…)" bash -c '! grep -qF -- "$1" "$2"' _ "$v" "$ALL"
done

echo "session-init tests: $PASS passed, $FAIL failed"
((FAIL == 0))
