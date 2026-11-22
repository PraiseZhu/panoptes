#!/usr/bin/env bash
# pi-slot.sh — run one of the 14 Beijing-time slots of devin/conf/slots.json (PraiseZhu/panoptes#313).
#
#   pi-slot.sh [--slot HH:MM] [--shadow] [--dry-run] [options]
#
# --slot HH:MM       run this slot now. Without it: the slot within +-match_window_min (10) of Beijing now that has not run
#                    today; a slot still ahead is waited for (07:30 warm-up -> 07:35 daily). No slot in the window: exit 0.
# --shadow           never sends: 1) $QA_HOME/pi/outbound-enabled is removed before any job (and again after the pull);
#                    2) daily always gets --no-send (daily.py would otherwise call send_own() -> outbound.py right away);
#                    3) the last step is `outbound --dry-run` only; the plain outbound is never called. The only push is the
#                    daily draft + dry-run plan to <data branch>:box/workspace/mivo-qa/pi/out/devin/shadow/<data day>/.
# --dry-run          print the plan (every job line and the outbound line); run nothing, no lock, no network.
# --lock-branch B    lock branch in the data repo (default devin-lock; never the data branch)
# --lock-timeout S   give up waiting for the lock after S seconds: exit 75 (default 1200)
# --slots FILE       slot table (default devin/conf/slots.json)
# --repo-url URL     data repo (default $STATE_SYNC_REPO_URL or https://github.com/PraiseZhu/grokbot-box.git)
# --branch B         data branch (default devin)
# --work-dir DIR     state-sync.sh slots work dir (default: state-sync.sh's own)
# --state-dir DIR    clones of the lock branch and the shadow push (default /workspace/devin-state/pi-slot)
# --qa-home DIR      data dir (default $QA_HOME, else /workspace/mivo-qa)
#
# Order: lock (atomic lease: a commit of lock.json pushed as a fast-forward onto the fetched head of the lock branch; a lease
# older than lock_stale_min (30) may be taken over) -> skip when runs/<day>/<HHMM>[.shadow].json exists on the lock branch ->
# state-sync.sh pull slots -> every job via pi/bin/run.sh JOB ARGS (a failing job does not stop the next one) -> outbound ->
# push (normal: state-sync.sh push slots; shadow: shadow/ only) -> release the lease together with the run marker.
# On exit (trap) the lease is released and session-init.sh --cleanup removes the secret files.
# Exit: 0 ok or skipped · 2 usage / bad slot table · 75 lock wait timed out · else the largest non-zero rc of the steps.
# Last stdout line: one JSON object {ok, slot, day, data_day, mode, skipped, lock, jobs, outbound, push, rc}.
# Tests only: PI_SLOT_NOW (fake Beijing "now" for slot choice), PI_SLOT_SLEEP (sleep command), PI_SLOT_RUNNER (job runner),
# PI_SLOT_LOCK_POLL (seconds between lock attempts, default 15).
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
PANOPTES_DIR=$(cd "$HERE/../.." && pwd)
readonly BOT_NAME='praisedevin[bot]' BOT_EMAIL='358239493+praisedevin[bot]@users.noreply.github.com'
readonly DATA_PATH='box/workspace/mivo-qa' PUSH_ROUNDS=5
RUNNER="${PI_SLOT_RUNNER:-$PANOPTES_DIR/pi/bin/run.sh}"
SYNC="$PANOPTES_DIR/devin/bin/state-sync.sh"
SLEEP="${PI_SLOT_SLEEP:-sleep}"
POLL="${PI_SLOT_LOCK_POLL:-15}"

usage() { sed -n '2,/^set -uo/{/^set -uo/d;s/^# \{0,1\}//;p}' "$0" >&2; exit 2; }
die() { echo "pi-slot: $2" >&2; exit "$1"; }
log() { echo "[pi-slot] $*" >&2; }

SLOT="" SHADOW=0 DRY=0 LOCK_BRANCH=devin-lock LOCK_TIMEOUT=1200 SLOTS="$PANOPTES_DIR/devin/conf/slots.json"
REPO_URL="${STATE_SYNC_REPO_URL:-https://github.com/PraiseZhu/grokbot-box.git}" BRANCH=devin SYNC_WORK=""
STATE_DIR=/workspace/devin-state/pi-slot QA_DIR="${QA_HOME:-/workspace/mivo-qa}"
while (($#)); do
  case "$1" in
    -h|--help) usage ;;
    --shadow) SHADOW=1 ;;
    --dry-run) DRY=1 ;;
    --slot|--lock-branch|--lock-timeout|--slots|--repo-url|--branch|--work-dir|--state-dir|--qa-home)
      (($# >= 2)) || die 2 "$1 needs a value"
      case "$1" in
        --slot) SLOT=$2 ;; --lock-branch) LOCK_BRANCH=$2 ;; --lock-timeout) LOCK_TIMEOUT=$2 ;; --slots) SLOTS=$2 ;;
        --repo-url) REPO_URL=$2 ;; --branch) BRANCH=$2 ;; --work-dir) SYNC_WORK=$2 ;; --state-dir) STATE_DIR=$2 ;;
        --qa-home) QA_DIR=$2 ;;
      esac; shift ;;
    *) die 2 "unknown argument $1" ;;
  esac
  shift
done
[[ -z "$SLOT" || "$SLOT" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || die 2 "--slot must be HH:MM (got '$SLOT')"
[[ "$LOCK_TIMEOUT" =~ ^[0-9]+$ ]] || die 2 "--lock-timeout must be seconds (got '$LOCK_TIMEOUT')"
[[ "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ && "$BRANCH" != main ]] || die 2 "--branch must be a plain branch name other than main"
[[ "$LOCK_BRANCH" =~ ^[A-Za-z0-9._/-]+$ && "$LOCK_BRANCH" != main && "$LOCK_BRANCH" != "$BRANCH" ]] \
  || die 2 "--lock-branch must be a plain branch name other than main and the data branch ($BRANCH)"
QA_DIR=${QA_DIR%/}
PI_DIR="$QA_DIR/pi"
LOCK_DIR="$STATE_DIR/lock" SHADOW_DIR="$STATE_DIR/shadow"
MODE=normal; ((SHADOW)) && MODE=shadow; ((DRY)) && MODE="$MODE-dry-run"
SYNC_OPTS=(--repo-url "$REPO_URL" --branch "$BRANCH" --qa-home "$QA_DIR"); [[ -n "$SYNC_WORK" ]] && SYNC_OPTS+=(--work-dir "$SYNC_WORK")

# slot table: validate and print "day<TAB>HH:MM<TAB>wait_seconds" of the chosen slot, then one line per job
# "job<TAB>unless_exists or -<TAB>arg..." (exit 3 = no slot in the window, 2 = bad table / unknown slot)
resolve() { # resolve SLOT DONE_LIST(comma separated day/HHMM)
  python3 - "$SLOTS" "$1" "$2" "${PI_SLOT_NOW:-}" <<'PY'
import datetime as dt, json, re, sys
path, want, done, fake = sys.argv[1:]
CST = dt.timezone(dt.timedelta(hours=8))
def bad(msg): print(f"pi-slot: {path}: {msg}", file=sys.stderr); sys.exit(2)
try: conf = json.load(open(path))
except Exception as e: bad(f"not readable JSON ({type(e).__name__})")
slots = conf.get("slots") or []
times = [s.get("at") for s in slots]
if len(set(times)) != len(times): bad("duplicate slot times")
for s in slots:
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(s.get("at"))): bad(f"bad slot time {s.get('at')!r}")
    if not s.get("jobs"): bad(f"slot {s['at']} has no jobs")
    for j in s["jobs"]:
        if not re.fullmatch(r"[a-z_]+", str(j.get("job"))) or not all(isinstance(a, str) and a and "\t" not in a for a in j.get("args", [])):
            bad(f"slot {s['at']}: bad job entry")
now = dt.datetime.fromisoformat(fake).astimezone(CST) if fake else dt.datetime.now(CST)
if want:
    slot = next((s for s in slots if s["at"] == want), None)
    if not slot: bad(f"no slot {want}")
    day, wait = now.date(), 0
else:
    window = dt.timedelta(minutes=int(conf.get("match_window_min", 10)))
    done = set(filter(None, done.split(",")))
    cands = []
    for s in slots:
        h, m = map(int, s["at"].split(":"))
        for d in (-1, 0, 1):
            at = dt.datetime.combine(now.date() + dt.timedelta(days=d), dt.time(h, m), CST)
            if abs(at - now) <= window and f"{at.date()}/{s['at'].replace(':', '')}" not in done:
                cands.append((abs(at - now), at, s))
    if not cands: sys.exit(3)
    _, at, slot = min(cands, key=lambda c: c[0])
    day, wait = at.date(), max(0, int((at - now).total_seconds()))
print(f"{day}\t{slot['at']}\t{wait}\t{day - dt.timedelta(days=1)}")
for j in slot["jobs"]:
    print("\t".join([j["job"], j.get("unless_exists") or "-"] + list(j.get("args", []))))
PY
}

# ---------- lock branch ----------
lg() { git -C "$LOCK_DIR" -c advice.detachedHead=false "$@"; }
bot_git() { GIT_AUTHOR_NAME=$BOT_NAME GIT_AUTHOR_EMAIL=$BOT_EMAIL GIT_COMMITTER_NAME=$BOT_NAME GIT_COMMITTER_EMAIL=$BOT_EMAIL \
  git -c user.name="$BOT_NAME" -c user.email="$BOT_EMAIL" -c commit.gpgsign=false "$@"; }
LOCK_HELD=0 LOCK_STATE="" TOKEN="$(hostname 2>/dev/null || echo host)-$$-$(date +%s)-$RANDOM"
STALE_S=1800

# lock_fetch: check out the remote head of the lock branch (an empty orphan when the branch does not exist yet); 1 = remote error
lock_fetch() {
  if [[ ! -d "$LOCK_DIR/.git" ]]; then
    mkdir -p "$LOCK_DIR" && git init -q "$LOCK_DIR" && lg remote add origin "$REPO_URL" || return 1
  fi
  lg remote set-url origin "$REPO_URL"
  local heads; heads=$(lg ls-remote --heads origin "refs/heads/$LOCK_BRANCH") || return 1
  if [[ -z "$heads" ]]; then
    lg symbolic-ref HEAD "refs/heads/pi-slot-new-$$-$RANDOM" && lg read-tree --empty && lg clean -fdxq
    return
  fi
  lg fetch -q origin "+refs/heads/$LOCK_BRANCH:refs/remotes/origin/$LOCK_BRANCH" \
    && lg checkout -q -f -B "$LOCK_BRANCH" "origin/$LOCK_BRANCH" && lg clean -fdxq
}
lock_field() { python3 -c 'import json,sys
try: d=json.load(open(sys.argv[1]))
except Exception: d={}
v=d.get(sys.argv[2]); print("" if v is None else v)' "$LOCK_DIR/lock.json" "$1"; }
lock_push() { lg push -q origin "HEAD:refs/heads/$LOCK_BRANCH" 2>/dev/null; }   # fast-forward only: the compare-and-swap
write_lock() { # write_lock HOLDER(empty = released)
  python3 - "$LOCK_DIR/lock.json" "$1" "$SLOT" "$MODE" "$(date +%s)" <<'PY'
import json, sys, datetime as dt
path, holder, slot, mode, at = sys.argv[1:]
iso = dt.datetime.fromtimestamp(int(at), dt.timezone(dt.timedelta(hours=8))).isoformat()
d = {"holder": holder, "slot": slot, "mode": mode, "at": int(at), "at_iso": iso} if holder else {"holder": None, "released_at": iso, "slot": slot}
json.dump(d, open(path, "w")); open(path, "a").write("\n")
PY
}

acquire_lock() {
  local deadline=$(( $(date +%s) + LOCK_TIMEOUT )) holder at age
  while :; do
    if lock_fetch; then
      holder=$(lock_field holder) at=$(lock_field at); age=$(( $(date +%s) - ${at:-0} ))
      if [[ -z "$holder" ]] || (( age > STALE_S )); then
        LOCK_STATE=acquired; [[ -n "$holder" ]] && LOCK_STATE=takeover
        write_lock "$TOKEN" && lg add lock.json \
          && bot_git -C "$LOCK_DIR" commit -q -m "pi-slot lock $SLOT $MODE${holder:+ (takeover of $holder, ${age}s old)}" \
          && lock_push && { LOCK_HELD=1; log "lock $LOCK_STATE on $LOCK_BRANCH"; return 0; }
        log "lock race lost, retrying"
      else
        log "lock held by $holder (${age}s old)"
      fi
    else
      log "lock branch $LOCK_BRANCH unreachable, retrying"
    fi
    (( $(date +%s) + POLL <= deadline )) || return 75
    "$SLEEP" "$POLL"
  done
}

# release_lock MARKER_JSON(may be empty): one commit that adds the run marker and (if still ours) frees lock.json
release_lock() {
  ((LOCK_HELD)) || return 0
  local marker=$1 round
  for (( round = 1; round <= PUSH_ROUNDS; round++ )); do
    lock_fetch || { sleep 1; continue; }
    [[ "$(lock_field holder)" == "$TOKEN" ]] && write_lock "" && lg add lock.json
    if [[ -n "$marker" ]]; then
      mkdir -p "$LOCK_DIR/$(dirname "$MARKER_PATH")" && printf '%s\n' "$marker" >"$LOCK_DIR/$MARKER_PATH" && lg add "$MARKER_PATH"
    fi
    lg diff --cached --quiet && { LOCK_HELD=0; return 0; }
    bot_git -C "$LOCK_DIR" commit -q -m "pi-slot release $SLOT $MODE" && lock_push && { LOCK_HELD=0; log "lock released"; return 0; }
  done
  log "could not release the lock after $PUSH_ROUNDS rounds (it expires after $((STALE_S / 60)) min)"; LOCK_HELD=0; return 1
}

on_exit() {
  release_lock "" || true
  ((DRY)) || bash "$PANOPTES_DIR/devin/bin/session-init.sh" --cleanup || true
}
trap on_exit EXIT
trap 'exit 130' INT TERM

# ---------- result ----------
RESULTS=() OUT_RESULT="" PUSH_RESULT="" RC=0 SKIPPED="" DAY="" DATA_DAY=""
bump() { (( $1 > RC )) && RC=$1; return 0; }
emit() {
  python3 - "$RC" "$SLOT" "$DAY" "$DATA_DAY" "$MODE" "$SKIPPED" "$LOCK_STATE" "$OUT_RESULT" "$PUSH_RESULT" "${RESULTS[@]}" <<'PY'
import json, sys
rc, slot, day, data_day, mode, skipped, lock, out, push, *jobs = sys.argv[1:]
def row(s, *names):
    if not s: return None
    v = s.split("\t"); d = dict(zip(names, v[:len(names)])); d["rc"] = None if d.get("rc") in (None, "") else int(d["rc"]); return d
J = [row(j, "job", "args", "rc") for j in jobs]
print(json.dumps({"ok": rc == "0", "slot": slot or None, "day": day or None, "data_day": data_day or None, "mode": mode,
                  "skipped": skipped or None, "lock": lock or None, "jobs": J, "outbound": row(out, "args", "rc"),
                  "push": row(push, "target", "rc"), "rc": int(rc)}, ensure_ascii=False))
PY
}
finish() { release_lock "${1:-}" || bump 1; emit; exit "$RC"; }

# ---------- choose the slot ----------
DONE=""
if [[ -z "$SLOT" ]] && ! ((DRY)); then
  lock_fetch || die 1 "cannot read lock branch $LOCK_BRANCH of $REPO_URL"
  DONE=$(cd "$LOCK_DIR" && find runs -name '*.json' 2>/dev/null | sed -n "s#^runs/\([0-9-]*\)/\([0-9]*\)$( ((SHADOW)) && echo '\.shadow')\.json\$#\1/\2#p" | paste -sd, -)
fi
plan=$(resolve "$SLOT" "$DONE"); prc=$?
if ((prc == 3)); then SKIPPED="no slot within the match window"; emit; exit 0; fi
((prc == 0)) || exit 2
IFS=$'\t' read -r DAY SLOT WAIT DATA_DAY <<<"$(head -1 <<<"$plan")"
mapfile -t JOBS < <(tail -n +2 <<<"$plan")
MARKER_PATH="runs/$DAY/${SLOT/:/}$( ((SHADOW)) && echo .shadow).json"

job_args() { # job_args JOB ARGS...: final argument list (shadow: daily always --no-send)
  local job=$1; shift
  local a=("$@")
  if ((SHADOW)) && [[ "$job" == daily ]] && [[ " ${a[*]} " != *" --no-send "* ]]; then a+=(--no-send); fi
  ((${#a[@]})) && printf '%s\n' "${a[@]}"
  return 0
}
OUT_ARGS=(); ((SHADOW)) && OUT_ARGS=(--dry-run)

if ((DRY)); then
  for line in "${JOBS[@]}"; do
    IFS=$'\t' read -r -a F <<<"$line"; mapfile -t A < <(job_args "${F[0]}" "${F[@]:2}")
    echo "plan: $RUNNER ${F[0]}${A[*]:+ ${A[*]}}$([[ "${F[1]}" != - ]] && echo " (unless $QA_DIR/${F[1]} exists)")"
    RESULTS+=("${F[0]}"$'\t'"${A[*]}"$'\t')
  done
  echo "plan: $RUNNER outbound${OUT_ARGS[*]:+ ${OUT_ARGS[*]}}"
  OUT_RESULT="${OUT_ARGS[*]}"$'\t'
  ((SHADOW)) && echo "plan: push $BRANCH:$DATA_PATH/pi/out/devin/shadow/$DATA_DAY/" || echo "plan: $SYNC push slots --mode $SLOT"
  emit; exit 0
fi

if ((WAIT > 0)); then log "slot $SLOT starts in ${WAIT}s, waiting"; "$SLEEP" "$WAIT"; fi

# ---------- run ----------
((SHADOW)) && rm -f -- "$PI_DIR/outbound-enabled"
acquire_lock || { RC=75; log "lock wait timed out after ${LOCK_TIMEOUT}s"; emit; exit 75; }
if [[ -f "$LOCK_DIR/$MARKER_PATH" ]]; then SKIPPED="slot $SLOT already ran on $DAY ($MODE)"; log "$SKIPPED"; finish ""; fi

"$SYNC" pull slots --mode "$SLOT" "${SYNC_OPTS[@]}" >&2 || { log "state-sync pull failed"; bump 1; finish ""; }
if ((SHADOW)); then
  rm -f -- "$PI_DIR/outbound-enabled"
  [[ ! -e "$PI_DIR/outbound-enabled" ]] || { log "cannot remove $PI_DIR/outbound-enabled: refusing to run in shadow mode"; bump 1; finish ""; }
fi

export PI_STATE="$PI_DIR" QA_HOME="$QA_DIR" TZ=Asia/Shanghai
for line in "${JOBS[@]}"; do
  IFS=$'\t' read -r -a F <<<"$line"; mapfile -t A < <(job_args "${F[0]}" "${F[@]:2}")
  if [[ "${F[1]}" != - && -e "$QA_DIR/${F[1]}" ]]; then log "skip ${F[0]}: $QA_DIR/${F[1]} exists"; RESULTS+=("${F[0]}"$'\t'"${A[*]}"$'\t'); continue; fi
  log "run ${F[0]} ${A[*]}"
  "$RUNNER" "${F[0]}" "${A[@]}"; rc=$?
  RESULTS+=("${F[0]}"$'\t'"${A[*]}"$'\t'"$rc"); bump "$rc"
done
"$RUNNER" outbound "${OUT_ARGS[@]}"; rc=$?
OUT_RESULT="${OUT_ARGS[*]}"$'\t'"$rc"; bump "$rc"

# ---------- push ----------
shadow_push() {
  local dest="$DATA_PATH/pi/out/devin/shadow/$DATA_DAY" sg=(git -C "$SHADOW_DIR" -c advice.detachedHead=false) f n round
  if [[ ! -d "$SHADOW_DIR/.git" ]]; then
    mkdir -p "$(dirname "$SHADOW_DIR")" && git clone -q --filter=blob:none --no-checkout --single-branch --branch "$BRANCH" "$REPO_URL" "$SHADOW_DIR" || return 1
  fi
  "${sg[@]}" sparse-checkout set --no-cone "/$dest/" || return 1
  for (( round = 1; round <= PUSH_ROUNDS; round++ )); do
    "${sg[@]}" fetch -q origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH" && "${sg[@]}" checkout -q -f -B "$BRANCH" "origin/$BRANCH" || return 1
    n=0
    mkdir -p "$SHADOW_DIR/$dest"
    for f in "$PI_DIR/out/daily/$DATA_DAY.md" "$PI_DIR/out/daily/$DATA_DAY.json" "$PI_DIR"/out/outbound/"$DAY"*.dry-run.md; do
      [[ -f "$f" ]] && cp -p "$f" "$SHADOW_DIR/$dest/${SLOT/:/}-$(basename "$f")" && n=$((n + 1))
    done
    ((n)) || { log "shadow: nothing to push"; return 0; }
    "${sg[@]}" add --sparse -- "$dest" || return 1
    "${sg[@]}" diff --cached --quiet && return 0
    if "${sg[@]}" diff --cached --name-only | grep -qv "^$dest/"; then log "shadow: staged a path outside $dest"; return 1; fi
    bot_git -C "$SHADOW_DIR" commit -q -m "devin shadow $SLOT $DATA_DAY $(date -u +%Y-%m-%dT%H:%M:%SZ)" || return 1
    "${sg[@]}" push -q origin "HEAD:refs/heads/$BRANCH" && { log "shadow pushed to $BRANCH:$dest/"; return 0; }
    log "shadow push rejected (round $round/$PUSH_ROUNDS)"; sleep $(( round ))
  done
  return 1
}
if ((SHADOW)); then
  shadow_push; rc=$?; PUSH_RESULT="$BRANCH:$DATA_PATH/pi/out/devin/shadow/$DATA_DAY/"$'\t'"$rc"
else
  "$SYNC" push slots --mode "$SLOT" "${SYNC_OPTS[@]}" >&2; rc=$?; PUSH_RESULT="$BRANCH (state-sync push slots)"$'\t'"$rc"
fi
bump "$rc"

MARKER=$(python3 -c 'import json,sys,datetime as dt; print(json.dumps({"slot":sys.argv[1],"day":sys.argv[2],"mode":sys.argv[3],"rc":int(sys.argv[4]),"at":dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")}))' "$SLOT" "$DAY" "$MODE" "$RC")
finish "$MARKER"
