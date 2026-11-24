#!/usr/bin/env bash
# state-sync.sh — move Mivo QA state between a Devin session ($QA_HOME) and the data branch of PraiseZhu/grokbot-box.
#
#   state-sync.sh pull qa    --pr N [--mode MODE] [options]   sparse pull of PR N's own state into $QA_HOME
#   state-sync.sh push qa    --pr N [--mode MODE] [options]   add/modify only PR N's whitelisted paths; never deletes
#   state-sync.sh pull slots [--mode HH:MM] [options]         pull the mivo-qa data dir + fleet-mode into $QA_HOME
#   state-sync.sh push slots [--mode HH:MM] [options]         mirror $QA_HOME back (rsync --delete, local dirs excluded)
#
# Options:
#   --repo-url URL    data repo (default $STATE_SYNC_REPO_URL or https://github.com/PraiseZhu/grokbot-box.git)
#   --branch NAME     data branch (default devin)
#   --work-dir DIR    git clone used for the sync (default /workspace/devin-state/qa-PR-N or /workspace/devin-state/slots)
#   --qa-home DIR     local data dir (default $QA_HOME or /workspace/mivo-qa)
#   --mode MODE       qa: event | catch-up | manual | replay | backfill (default event; backfill also appends
#                     reports/merged/manual-runs.jsonl). slots: the schedule slot shown in the commit subject
#                     (default the current Beijing HH:MM)
#   -m TEXT           extra line for the commit body (the subject is fixed)
#
# The data dir sits at box/workspace/mivo-qa/ in the data repo (= $QA_HOME), fleet-mode at box/workspace/fleet-mode
# (= $STATE_SYNC_FLEET_MODE_FILE, default the fleet-mode file next to $QA_HOME; pulled only, never pushed).
# A push needs the work dir of an earlier pull: it records the pulled commit, which a slots push mirrors onto (so files
# other sessions pushed since are kept by the rebase) and a backfill push diffs manual-runs.jsonl against.
# qa whitelist (relative to the data dir; N = --pr, D = today in Asia/Shanghai):
#   reports/merged/PR-N.json  reports/catch-up/PR-N.json  argus-root/gate-wait/PR-N  pi/out/devin/quota/D/PR-N.json
#   pi/out/devin/runs/PR-N/**  pi/out/qa-fleet/runs/<run_id of argus-root/qa-fleet-run-manifest.json>.json
#   argus-root/cache/review-verdicts/** (new files only)  reports/merged/manual-runs.jsonl (backfill, lines appended
#   after the pulled version only)
# qa pull also brings the pi/gh-app-enabled and pi/gh-app-*-enabled switches (pull only: never on the push whitelist).
# slots pull skips memguard/ logs/ tasks/ *.out *.bak* and the handoff HMAC key; slots push also leaves those alone and never sends cache/ .venv/
# argus-root/app/ argus-root/logs/ *.lock. A slots push that would delete more than 200 files is refused (exit 90).
# Pushing: commit as praisedevin[bot], then up to 5 rounds of git pull --rebase + git push with a random 2-10 s back-off
# (STATE_SYNC_BACKOFF_MIN / STATE_SYNC_BACKOFF_MAX shorten it for tests). A qa rebase conflict re-applies the whitelist
# onto the new remote head; a slots rebase conflict stops (exit 91).
#
# Exit: 0 ok · 2 usage · 3 precondition (no earlier pull, manual-runs.jsonl rewritten, bad manifest) · 90 too many
#       deletions · 91 slots rebase conflict · 92 push still rejected after 5 rounds · 93 qa commit outside the whitelist · 94 staged handoff HMAC key
set -euo pipefail

readonly MAX_DELETES=200 PUSH_ROUNDS=5
readonly BOT_NAME='praisedevin[bot]' BOT_EMAIL='358239493+praisedevin[bot]@users.noreply.github.com'
readonly DATA_PATH='box/workspace/mivo-qa' FLEET_MODE_PATH='box/workspace/fleet-mode'
readonly HANDOFF_KEY='pi/outbound/handoff-files.key'
readonly PULL_EXCLUDES=(--exclude=memguard/ --exclude=logs/ --exclude=tasks/ --exclude='*.out' --exclude='*.bak*' "--exclude=/$HANDOFF_KEY")
readonly LOCAL_EXCLUDES=(--exclude=cache/ --exclude=.venv/ --exclude=/argus-root/app/ --exclude=/argus-root/logs/ --exclude='*.lock')
readonly REVIEW_CACHE='argus-root/cache/review-verdicts' MANUAL_RUNS='reports/merged/manual-runs.jsonl'

die() { local rc=$1; shift; echo "state-sync: $*" >&2; exit "$rc"; }
log() { echo "[state-sync] $*" >&2; }
usage() { sed -n '2,/^set -euo/{/^set -euo/d;s/^# \{0,1\}//;p}' "$0" >&2; exit 2; }

[[ $# -ge 2 ]] || usage
CMD=$1 TIER=$2; shift 2
case "$CMD" in pull|push) ;; *) die 2 "first argument must be pull or push (got '$CMD')" ;; esac
case "$TIER" in qa|slots) ;; *) die 2 "second argument must be qa or slots (got '$TIER')" ;; esac

PR="" MODE="" EXTRA_MSG="" WORK_DIR=""
REPO_URL="${STATE_SYNC_REPO_URL:-https://github.com/PraiseZhu/grokbot-box.git}" BRANCH=devin QA_DIR="${QA_HOME:-/workspace/mivo-qa}"
while [[ $# -gt 0 ]]; do
  [[ "$1" == -h || "$1" == --help ]] && usage
  [[ $# -ge 2 ]] || die 2 "$1 needs a value"
  case "$1" in
    --pr) PR=$2 ;; --repo-url) REPO_URL=$2 ;; --branch) BRANCH=$2 ;; --work-dir) WORK_DIR=$2 ;;
    --qa-home) QA_DIR=$2 ;; --mode) MODE=$2 ;; -m) EXTRA_MSG=$2 ;;
    *) die 2 "unknown option $1" ;;
  esac
  shift 2
done

if [[ "$TIER" == qa ]]; then
  [[ "$PR" =~ ^[1-9][0-9]*$ ]] || die 2 "qa needs --pr N (got '${PR}')"
  MODE=${MODE:-event}
  case "$MODE" in event|catch-up|manual|replay|backfill) ;; *) die 2 "qa --mode must be event, catch-up, manual, replay or backfill (got '$MODE')" ;; esac
else
  [[ -z "$PR" ]] || die 2 "slots takes no --pr"
  MODE=${MODE:-$(TZ=Asia/Shanghai date +%H:%M)}
  [[ "$MODE" =~ ^[0-9A-Za-z:._-]+$ ]] || die 2 "slots --mode must be a slot label like 07:35 (got '$MODE')"
fi
[[ "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ && "$BRANCH" != main ]] || die 2 "--branch must be a plain branch name other than main (got '$BRANCH')"
WORK_DIR=${WORK_DIR:-/workspace/devin-state/$([[ "$TIER" == qa ]] && echo "qa-PR-$PR" || echo slots)}
QA_DIR=${QA_DIR%/}
FLEET_MODE_FILE="${STATE_SYNC_FLEET_MODE_FILE:-$(dirname "$QA_DIR")/fleet-mode}"
BASE_FILE="$WORK_DIR/.git/state-sync.base"
DAY=$(TZ=Asia/Shanghai date +%F)

g() { git -C "$WORK_DIR" -c advice.detachedHead=false "$@"; }

sparse_patterns() {
  if [[ "$TIER" == slots ]]; then printf '%s\n' "/$DATA_PATH/" "/$FLEET_MODE_PATH"; return; fi
  printf "/$DATA_PATH/%s\n" "reports/merged/PR-$PR.json" "reports/catch-up/PR-$PR.json" "argus-root/gate-wait/PR-$PR" \
    "pi/out/devin/quota/$DAY/" "pi/out/devin/runs/PR-$PR/" "$REVIEW_CACHE/" "$MANUAL_RUNS" "pi/gh-app-enabled" "pi/gh-app-*-enabled"
}

# clone or refresh the work dir and check out the remote head of $BRANCH (sparse)
checkout_remote() {
  if [[ ! -d "$WORK_DIR/.git" ]]; then
    mkdir -p "$(dirname "$WORK_DIR")"
    git clone -q --filter=blob:none --no-checkout --single-branch --branch "$BRANCH" "$REPO_URL" "$WORK_DIR" \
      || die 1 "cannot clone branch $BRANCH of $REPO_URL"
  else
    g fetch -q origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH" || die 1 "cannot fetch $BRANCH from origin"
  fi
  sparse_patterns | g sparse-checkout set --no-cone --stdin
  g checkout -q -f -B "$BRANCH" "origin/$BRANCH"
}

cmd_pull() {
  checkout_remote
  mkdir -p "$QA_DIR"
  if [[ "$TIER" == qa ]]; then
    [[ -d "$WORK_DIR/$DATA_PATH" ]] && rsync -a "$WORK_DIR/$DATA_PATH/" "$QA_DIR/"
  else
    [[ -d "$WORK_DIR/$DATA_PATH" ]] || die 1 "$DATA_PATH missing on $BRANCH"
    rsync -a "${PULL_EXCLUDES[@]}" "$WORK_DIR/$DATA_PATH/" "$QA_DIR/"
    if [[ -f "$WORK_DIR/$FLEET_MODE_PATH" ]]; then mkdir -p "$(dirname "$FLEET_MODE_FILE")"; cp "$WORK_DIR/$FLEET_MODE_PATH" "$FLEET_MODE_FILE"; fi
  fi
  g rev-parse HEAD >"$BASE_FILE"
  log "pull $TIER ${PR:+PR-$PR }from $BRANCH @ $(cut -c1-12 "$BASE_FILE") -> $QA_DIR"
}

copy_in() {   # rel: copy $QA_DIR/rel over the work tree copy
  { mkdir -p "$(dirname "$WORK_DIR/$DATA_PATH/$1")" && cp -p "$QA_DIR/$1" "$WORK_DIR/$DATA_PATH/$1" \
    && g add --sparse -- "$DATA_PATH/$1"; } || die 1 "cannot stage $1"
}

qa_run_id() {
  local m="$QA_DIR/argus-root/qa-fleet-run-manifest.json" id
  [[ -f "$m" ]] || return 0
  id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("run_id") or "")' "$m") \
    || die 3 "cannot read run_id from $m"
  [[ -z "$id" || "$id" =~ ^fleet-[0-9a-f]{32}$ ]] || die 3 "run_id in $m is not fleet-<32 hex> (got '$id')"
  printf '%s\n' "$id"
}

# put this PR's whitelisted files from $QA_DIR onto the checked-out remote head and stage them
apply_qa() {
  local rel f id
  for rel in "reports/merged/PR-$PR.json" "reports/catch-up/PR-$PR.json" "argus-root/gate-wait/PR-$PR" "pi/out/devin/quota/$DAY/PR-$PR.json"; do
    [[ -f "$QA_DIR/$rel" ]] && copy_in "$rel"
  done
  if [[ -d "$QA_DIR/pi/out/devin/runs/PR-$PR" ]]; then
    while IFS= read -r -d '' f; do copy_in "${f#"$QA_DIR"/}"; done < <(find "$QA_DIR/pi/out/devin/runs/PR-$PR" -type f -print0)
  fi
  id=$(qa_run_id) || exit $?
  [[ -n "$id" && -f "$QA_DIR/pi/out/qa-fleet/runs/$id.json" ]] && copy_in "pi/out/qa-fleet/runs/$id.json"
  if [[ -d "$QA_DIR/$REVIEW_CACHE" ]]; then
    while IFS= read -r -d '' f; do
      rel=${f#"$QA_DIR"/}
      g cat-file -e "HEAD:$DATA_PATH/$rel" 2>/dev/null || copy_in "$rel"
    done < <(find "$QA_DIR/$REVIEW_CACHE" -type f -print0)
  fi
  [[ "$MODE" == backfill ]] && apply_manual_runs
  return 0
}

apply_manual_runs() {
  local local_file="$QA_DIR/$MANUAL_RUNS" tree="$WORK_DIR/$DATA_PATH/$MANUAL_RUNS" base="$WORK_DIR/.git/state-sync.manual-runs" base_len
  [[ -f "$local_file" ]] || return 0
  g show "$(cat "$BASE_FILE"):$DATA_PATH/$MANUAL_RUNS" >"$base" 2>/dev/null || : >"$base"
  base_len=$(wc -c <"$base")
  cmp -s -n "$base_len" "$base" "$local_file" \
    || die 3 "$MANUAL_RUNS no longer starts with the pulled version (first $base_len bytes differ): only appending is allowed"
  (( $(wc -c <"$local_file") > base_len )) || return 0
  { mkdir -p "$(dirname "$tree")" && tail -c +"$((base_len + 1))" "$local_file" >>"$tree" \
    && g add --sparse -- "$DATA_PATH/$MANUAL_RUNS"; } || die 1 "cannot append $MANUAL_RUNS"
}

# every staged qa path must be on the whitelist and nothing may be deleted
check_qa_staged() {
  local p rel re
  re="^(reports/merged/PR-$PR\.json|reports/catch-up/PR-$PR\.json|argus-root/gate-wait/PR-$PR|pi/out/devin/quota/$DAY/PR-$PR\.json"
  re+="|pi/out/devin/runs/PR-$PR/.+|pi/out/qa-fleet/runs/fleet-[0-9a-f]{32}\.json|$REVIEW_CACHE/.+"
  [[ "$MODE" == backfill ]] && re+="|reports/merged/manual-runs\.jsonl"
  re+=')$'
  [[ -z "$(g diff --cached --name-only --diff-filter=DR)" ]] || die 93 "qa push would delete or rename files"
  while IFS= read -r p; do
    rel=${p#"$DATA_PATH"/}
    [[ "$p" == "$DATA_PATH/$rel" && "$rel" =~ $re ]] || die 93 "qa push staged a path outside the PR-$PR whitelist: $p"
  done < <(g diff --cached --name-only)
}

check_slots_staged() {
  g diff --cached --quiet -- "$DATA_PATH/$HANDOFF_KEY" || die 94 "push slots: refused staged $HANDOFF_KEY"
}

apply_slots() {
  mkdir -p "$WORK_DIR/$DATA_PATH"
  rsync -a --delete "${LOCAL_EXCLUDES[@]}" "${PULL_EXCLUDES[@]}" "$QA_DIR/" "$WORK_DIR/$DATA_PATH/"
  g add -A --sparse -- "$DATA_PATH"
  check_slots_staged
  local n; n=$(g diff --cached --name-only --diff-filter=D | wc -l)
  (( n <= MAX_DELETES )) || die 90 "push slots would delete $n files (> $MAX_DELETES): refused, $BRANCH left unchanged"
}

has_staged() { ! g diff --cached --quiet; }

commit_staged() {
  local who="PR-$PR"; [[ "$TIER" == slots ]] && who=$MODE
  local args=(-m "devin sync $TIER $who $(date -u +%Y-%m-%dT%H:%M:%SZ)" -m "mode: $MODE")
  [[ -n "$EXTRA_MSG" ]] && args+=(-m "$EXTRA_MSG")
  GIT_AUTHOR_NAME=$BOT_NAME GIT_AUTHOR_EMAIL=$BOT_EMAIL GIT_COMMITTER_NAME=$BOT_NAME GIT_COMMITTER_EMAIL=$BOT_EMAIL \
    g -c user.name="$BOT_NAME" -c user.email="$BOT_EMAIL" -c commit.gpgsign=false commit -q "${args[@]}" || die 1 "git commit failed"
}

backoff() {
  local lo=${STATE_SYNC_BACKOFF_MIN:-2} hi=${STATE_SYNC_BACKOFF_MAX:-10}
  (( hi >= lo )) || hi=$lo
  sleep $(( lo + RANDOM % (hi - lo + 1) ))
}

# rebase onto the remote head; on conflict qa re-applies its whitelist there, slots stops
rebase_on_remote() {
  local who=(-c user.name="$BOT_NAME" -c user.email="$BOT_EMAIL" -c commit.gpgsign=false)
  g "${who[@]}" pull -q --rebase origin "$BRANCH" && return 0
  g rebase --abort 2>/dev/null || true
  [[ "$TIER" == slots ]] && die 91 "push slots: rebase onto origin/$BRANCH conflicts; nothing pushed, pull again"
  log "push qa: rebase conflict, re-applying PR-$PR onto origin/$BRANCH"
  g fetch -q origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH" || die 1 "cannot fetch $BRANCH from origin"
  g checkout -q -f -B "$BRANCH" "origin/$BRANCH" || die 1 "cannot check out origin/$BRANCH"
  apply_qa; check_qa_staged
  has_staged || return 2
  commit_staged
}

cmd_push() {
  [[ -f "$BASE_FILE" ]] || die 3 "no earlier 'state-sync.sh pull $TIER' in $WORK_DIR: pull first"
  local base; base=$(cat "$BASE_FILE")
  if [[ "$TIER" == qa ]]; then
    sparse_patterns | g sparse-checkout set --no-cone --stdin
    g fetch -q origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH" || die 1 "cannot fetch $BRANCH from origin"
    g checkout -q -f -B "$BRANCH" "origin/$BRANCH"
    apply_qa; check_qa_staged
  else
    check_slots_staged
    g checkout -q -f -B "$BRANCH" "$base"
    apply_slots
  fi
  if ! has_staged; then log "push $TIER: nothing to push"; rm -f "$BASE_FILE"; return 0; fi
  commit_staged
  local round rc
  for (( round = 1; round <= PUSH_ROUNDS; round++ )); do
    rc=0; rebase_on_remote || rc=$?
    if (( rc == 2 )); then log "push $TIER: nothing left to push after re-apply"; rm -f "$BASE_FILE"; return 0; fi
    if g push -q origin "HEAD:refs/heads/$BRANCH"; then
      log "push $TIER ${PR:+PR-$PR }-> $BRANCH @ $(g rev-parse --short=12 HEAD) (round $round)"
      rm -f "$BASE_FILE"
      return 0
    fi
    log "push $TIER: rejected (round $round/$PUSH_ROUNDS)"
    (( round < PUSH_ROUNDS )) && backoff
  done
  die 92 "push $TIER: still rejected after $PUSH_ROUNDS rounds"
}

"cmd_$CMD"
