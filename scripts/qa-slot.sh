# shellcheck shell=bash
# qa-slot.sh — the single QA slot shared by every run-nightly.sh (nightly, catch-up children, on-merge, manual).
# Sourced by run-nightly.sh; tests source it directly (tests/qa-slot.test.mjs).
#
# Cap: PANOPTES_QA_SLOTS (default 1, the 电脑运维 safe value; nightly + catch-up count toward it). The slot is
# nightly.lock (fd 9). Values > 1 are refused here (parallel 2 needs ARGUS_UNIT_SHARDS=1 and a >= 10 GB gate after
# a measured peak; not wired yet).
# Priority (queue-jump) runs — run-nightly.sh --priority, launched by mergewatch for a fresh merge:
#   1. serialize among themselves on priority.lock,
#   2. drop a request file $ARGUS_ROOT/priority/req-<pid>,
#   3. take the slot as soon as it is free, or borrow it from a run that is holding it but parked between PRs
#      (that run writes priority/yield-<pid> and waits until no live request is left; src/priority.mjs).
# Normal runs never take the slot while a live priority request exists, so a merge goes to the front of the queue.
# On-merge runs (10-28): per-PR lock + memory-admitted concurrency, see slot_acquire_onmerge.
# Memory gate: MemAvailable >= PANOPTES_MIN_MEM_GB (default 8) AND SwapFree >= PANOPTES_MIN_SWAP_GB (default 4).

SLOT_POLL_S="${PANOPTES_SLOT_POLL_S:-2}"

slot_dir() { printf '%s\n' "$ARGUS_ROOT/priority"; }

# 11-05 (stdcheck gap qa_start:PR-1718: #1718 waited 10 min 51 s for a slot with nothing logged): every waiter logs when its wait
# starts and when it gets the slot (SLOT_ID e.g. PR-1718, SLOT_KIND merge|rerun|catch-up|manual, set by run-nightly.sh). A wait
# longer than PANOPTES_SLOT_WAIT_ALERT_S (default 480 = 8 min) raises one alert: control.log warn line + the state file
# $ARGUS_ROOT/slot-wait-alerts/<pid>.json, which watchdog.slot_wait_check turns into one slot:wait item (existing DM path).
SLOT_WAIT_ALERT_S="${PANOPTES_SLOT_WAIT_ALERT_S:-480}"
# epoch seconds; PANOPTES_TEST_CLOCK (a file holding an epoch) overrides it only with PANOPTES_TEST=1 (tests: simulated clock)
slot_now() { if [[ "${PANOPTES_TEST:-}" == 1 && -n "${PANOPTES_TEST_CLOCK:-}" ]]; then cat "$PANOPTES_TEST_CLOCK"; else date +%s; fi; }
slot_log() { echo "[panoptes] $1 ($2)" >&2; if declare -F ctl >/dev/null; then ctl "$1" "$2" "${3:-info}"; fi; return 0; }
slot_who() { printf '"id":"%s","kind":"%s"' "${SLOT_ID:--}" "${SLOT_KIND:-manual}"; }
slot_alert_file() { printf '%s\n' "$ARGUS_ROOT/slot-wait-alerts/$$.json"; }
slot_alert_write() {   # state waited_s
  local f; f=$(slot_alert_file); mkdir -p "${f%/*}"
  printf '{"id":"%s","kind":"%s","pid":%d,"wait_start":%d,"waited_s":%d,"state":"%s","t":%d}\n' \
    "${SLOT_ID:--}" "${SLOT_KIND:-manual}" "$$" "$SLOT_WAIT_T0" "$2" "$1" "$(slot_now)" >"$f.tmp" && mv -f "$f.tmp" "$f"
}
# called after every failed attempt: first call logs the wait start; past SLOT_WAIT_ALERT_S raises the alert once
slot_wait_tick() {
  if [[ -z "${SLOT_WAIT_T0:-}" ]]; then SLOT_WAIT_T0=$(slot_now); SLOT_WAIT_ALERTED=""; slot_log "run-nightly slot wait start" "$(slot_who)"; return 0; fi
  local w=$(( $(slot_now) - SLOT_WAIT_T0 ))
  if [[ -z "$SLOT_WAIT_ALERTED" ]] && (( w > SLOT_WAIT_ALERT_S )); then
    SLOT_WAIT_ALERTED=1; slot_alert_write waiting "$w"
    slot_log "run-nightly slot wait alert" "$(slot_who),\"waited_s\":$w,\"limit_s\":$SLOT_WAIT_ALERT_S" warn
  fi
  return 0
}
# end of a wait: acquired MODE | gave-up. Logs always (waited_s 0 = no wait); an alerted wait updates its state file.
slot_wait_done() {
  local w=0; [[ -n "${SLOT_WAIT_T0:-}" ]] && w=$(( $(slot_now) - SLOT_WAIT_T0 ))
  if [[ "$1" == gave-up ]]; then slot_log "run-nightly slot wait gave up" "$(slot_who),\"waited_s\":$w" warn
  else slot_log "run-nightly slot acquired" "$(slot_who),\"mode\":\"$1\",\"waited_s\":$w"; fi
  [[ -n "${SLOT_WAIT_ALERTED:-}" ]] && slot_alert_write "$([[ "$1" == gave-up ]] && echo gave-up || echo acquired)" "$w"
  SLOT_WAIT_T0=""; SLOT_WAIT_ALERTED=""; return 0
}

# 11-05: priority order among on-merge waiters (no higher cap). The request file req-<pid> holds "<tier> <since> <id>": tier 0 =
# fresh merge (SLOT_KIND merge), 1 = rerun (mergewatch requeue, try >= 2) or anything else. Only the head of the queue (lowest
# tier, then oldest since, then pid) may take a slot. Starvation-safe: a request that has waited >= SLOT_STARVE_S counts as tier 0
# (aging), so a rerun is passed by at most that long. Empty req files (slot_acquire_priority) are not part of the order.
# on-merge run got its slot: the req file stays (normal runs keep yielding, a parked holder keeps waiting) but leaves the order
slot_onmerge_got() { printf 'run - %s\n' "${SLOT_ID:--}" >"$SLOT_REQ"; slot_wait_done "$SLOT_MODE"; }
slot_tier() { [[ "${SLOT_KIND:-merge}" == merge ]] && echo 0 || echo 1; }
slot_is_head() {
  local f pid tier since rest now best="" key; now=$(slot_now)
  for f in "$(slot_dir)"/req-*; do
    [[ -e "$f" ]] || continue
    pid="${f##*-}"
    if ! [[ "$pid" =~ ^[0-9]+$ ]] || ! kill -0 "$pid" 2>/dev/null; then rm -f "$f"; continue; fi
    read -r tier since rest <"$f" 2>/dev/null || true
    [[ "$tier" =~ ^[0-9]+$ && "$since" =~ ^[0-9]+$ ]] || continue
    (( now - since >= SLOT_STARVE_S )) && tier=0
    key=$(printf '%d %012d %012d' "$tier" "$since" "$pid")
    [[ -z "$best" || "$key" < "$best" ]] && best="$key"
  done
  [[ -z "$best" || "${best##* }" == "$(printf '%012d' "$$")" ]]
}

# live files of a kind (req|yield); removes files whose pid is gone
slot_live() {
  local kind="$1" f pid n=0
  for f in "$(slot_dir)/$kind"-*; do
    [[ -e "$f" ]] || continue
    pid="${f##*-}"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then n=$((n + 1)); else rm -f "$f"; fi
  done
  printf '%d\n' "$n"
}

slot_check_cap() {
  local cap="${PANOPTES_QA_SLOTS:-1}"
  [[ "$cap" == 1 ]] || { echo "[panoptes] PANOPTES_QA_SLOTS=$cap not supported (only 1; see scripts/qa-slot.sh)" >&2; return 1; }
}

# 11-04 (late-1609-1618 #4): queue tickets. A waiting normal run (catch-up child, --wait-lock) drops priority/q-<epoch>-<pid> and keeps
# its place: waiters take the slot oldest ticket first, and once the oldest ticket has waited PANOPTES_SLOT_STARVE_S (default 1800)
# it goes ahead of later priority / on-merge requests for the free slot (11-03: #1610 waited ~93 min while 5 on-merge runs took the
# lock). On-merge runs can still borrow a parked slot or run concurrently under the memory rules.
SLOT_STARVE_S="${PANOPTES_SLOT_STARVE_S:-1800}"
# oldest live ticket file ("" when none); dead tickets are swept
slot_oldest_ticket() {
  local f pid best=""
  for f in "$(slot_dir)"/q-*; do
    [[ -e "$f" ]] || continue
    pid="${f##*-}"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then [[ -z "$best" || "$(basename "$f")" < "$(basename "$best")" ]] && best="$f"; else rm -f "$f"; fi
  done
  printf '%s\n' "$best"
}
# 0 when the oldest waiting ticket has waited >= SLOT_STARVE_S (it owns the next free slot)
slot_ticket_starving() {
  local t; t=$(slot_oldest_ticket); [[ -n "$t" ]] || return 1
  local at; at=$(basename "$t"); at=${at#q-}; at=${at%%-*}
  (( $(date +%s) - 10#$at >= SLOT_STARVE_S ))
}

# slot_acquire_normal FD WAIT_S (WAIT_S empty = do not wait). 0 = got the slot, 1 = gave up.
slot_acquire_normal() {
  local fd="$1" wait="$2" start=$SECONDS t mine=""
  slot_check_cap || return 1
  if [[ -n "$wait" ]]; then mkdir -p "$(slot_dir)"; mine="$(slot_dir)/q-$(printf '%012d' "$(date +%s)")-$$"; : >"$mine"; fi
  while :; do
    t=$(slot_oldest_ticket)
    if [[ -z "$mine" || "$t" == "$mine" ]] && { (( $(slot_live req) == 0 )) || { [[ -n "$mine" ]] && slot_ticket_starving; }; } && flock -n "$fd"; then
      [[ -n "$mine" ]] && rm -f "$mine"; [[ -n "$wait" ]] && slot_wait_done lock; return 0
    fi
    [[ -n "$wait" ]] && (( SECONDS - start < wait )) || { [[ -n "$mine" ]] && { rm -f "$mine"; slot_wait_done gave-up; }; return 1; }
    slot_wait_tick
    sleep "$SLOT_POLL_S"
  done
}

# slot_acquire_priority FD PRIO_FD WAIT_S. Sets SLOT_MODE=lock|borrowed. 0 = go, 1 = gave up.
slot_acquire_priority() {
  local fd="$1" pfd="$2" wait="${3:-14400}" start=$SECONDS
  slot_check_cap || return 1
  mkdir -p "$(slot_dir)"
  SLOT_REQ="$(slot_dir)/req-$$"; : >"$SLOT_REQ"   # before priority.lock: a parked run stays parked across back-to-back merges
  flock -w "$wait" "$pfd" || { rm -f "$SLOT_REQ"; return 1; }
  while :; do
    if ! slot_ticket_starving && flock -n "$fd"; then SLOT_MODE=lock; slot_wait_done lock; return 0; fi
    if (( $(slot_live yield) > 0 )); then SLOT_MODE=borrowed; slot_wait_done borrowed; return 0; fi
    (( SECONDS - start < wait )) || { rm -f "$SLOT_REQ"; slot_wait_done gave-up; return 1; }
    slot_wait_tick
    sleep "$SLOT_POLL_S"
  done
}

slot_release_priority() { [[ -n "${SLOT_REQ:-}" ]] && rm -f "$SLOT_REQ"; [[ -n "${SLOT_CONC:-}" ]] && rm -f "$SLOT_CONC"; return 0; }

# 10-28 (merge-to-test SLA p95 <= 2 min; #1345 waited 21 min, #1315 31 min behind one lock):
# slot_acquire_onmerge FD PR WAIT_S. On-merge runs lock per PR (priority/pr-<N>.lock: the same PR never runs twice) instead of
# serializing on priority.lock. Order each poll: free slot -> SLOT_MODE=lock; holder parked -> borrowed; else, when memory
# admission passes (slot_mem_ok: at least 1 shard fits per slot_shards_for_mem, 10-28 PM) and fewer than PANOPTES_ONMERGE_MAX_CONC
# (default 2) concurrent runs are live -> SLOT_MODE=concurrent (priority/conc-<pid>, caller limits ARGUS_MAX_PARALLEL=1).
# Not enough memory: keep waiting for the slot exactly as before (then run-nightly's own gate: normal / degraded / skip).
slot_acquire_onmerge() {
  local fd="$1" pr="$2" wait="${3:-14400}" start=$SECONDS
  slot_check_cap || return 1
  mkdir -p "$(slot_dir)"
  exec {SLOT_PR_FD}>"$(slot_dir)/pr-$pr.lock"
  flock -n "$SLOT_PR_FD" || { echo "[panoptes] on-merge run for PR #$pr already active" >&2; return 1; }
  SLOT_REQ="$(slot_dir)/req-$$"; printf '%d %d %s\n' "$(slot_tier)" "$(slot_now)" "${SLOT_ID:--}" >"$SLOT_REQ"
  while :; do
    if slot_is_head; then   # 11-05: only the head of the priority-ordered queue takes a slot (merge before rerun, aging)
      if ! slot_ticket_starving && flock -n "$fd"; then SLOT_MODE=lock; slot_onmerge_got; return 0; fi
      if (( $(slot_live yield) > 0 )); then SLOT_MODE=borrowed; slot_onmerge_got; return 0; fi
      if slot_mem_ok && slot_balloon_ok && (( $(slot_live conc) < ${PANOPTES_ONMERGE_MAX_CONC:-2} )); then
        SLOT_CONC="$(slot_dir)/conc-$$"; : >"$SLOT_CONC"; SLOT_MODE=concurrent; slot_onmerge_got; return 0
      fi
    fi
    (( SECONDS - start < wait )) || { rm -f "$SLOT_REQ"; slot_wait_done gave-up; return 1; }
    slot_wait_tick
    sleep "$SLOT_POLL_S"
  done
}

# 0 when memory is enough to start a run
# MemAvailable in kB; PI_TEST_MEM_AVAIL_MB overrides it only with PANOPTES_TEST=1 (tests: simulate 9000 / 5500 / 4000 MB)
slot_mem_avail_kb() {
  if [[ -n "${PI_TEST_MEM_AVAIL_MB:-}" && "${PANOPTES_TEST:-}" == 1 ]]; then echo $(( PI_TEST_MEM_AVAIL_MB * 1024 )); return; fi
  awk '/^MemAvailable:/{print $2}' "${PANOPTES_MEMINFO:-/proc/meminfo}"
}
slot_swap_free_kb() { awk '/^SwapFree:/{print $2}' "${PANOPTES_MEMINFO:-/proc/meminfo}"; }
# 10-28 (PM): SwapFree < PANOPTES_MIN_SWAP_GB (4G), including no swap at all (SwapTotal 0): the gate alerts and runs by
# memory rules only, capped at 1 shard (>= 6144 MiB; below that it waits as before). Low swap never blocks testing forever.
slot_low_swap() { (( $(slot_swap_free_kb || echo 0) < ${PANOPTES_MIN_SWAP_GB:-4} * 1024 * 1024 )); }
# host virtio balloon in MB: (balloon_inflate - balloon_deflate) pages from /proc/vmstat (cumulative counters; log only)
slot_balloon_mb() { awk -v pg="$(getconf PAGESIZE 2>/dev/null || echo 4096)" '/^balloon_inflate /{i=$2} /^balloon_deflate /{d=$2} END{v=(i-d)*pg/1048576; print (v>0?int(v):0)}' "${PANOPTES_VMSTAT:-/proc/vmstat}" 2>/dev/null || echo 0; }
# 10-28: host balloon took up to 9.3G (14:21-14:28 BJT, MemAvailable 2.5G). A second concurrent run is admitted only while
# balloon <= PANOPTES_MAX_BALLOON_CONC_MB (default 2048). Single runs are unaffected (8G + 4G rule and degraded fallback as before).
slot_balloon_ok() {
  local b; b=$(slot_balloon_mb)
  if (( ${b:-0} > ${PANOPTES_MAX_BALLOON_CONC_MB:-2048} )); then
    echo "[panoptes] balloon ${b} MiB > ${PANOPTES_MAX_BALLOON_CONC_MB:-2048} MiB: no second concurrent run, waiting for the slot" >&2; return 1
  fi
}
# 10-28 PM: on-merge admission reuses the shard table (no separate 8G + 4G rule that could diverge): admit iff >= 1 shard fits
slot_mem_ok() { (( $(slot_shards_for_mem) >= 1 )); }

# 10-28 shard <-> memory mapping (index = unit shards, value = MemAvailable needed in MiB; SwapFree >= 4G always required).
# Normal run = 2 shards (argus/src/shard.mjs hard cap ARGUS_SHARD_MAX default 2; mivo profile perShardGb 1.2), first round
# and coverage round may run in parallel, so 2 shards costs ~2 x 1.2G x 2 rounds on top of node/git/browser overhead:
#   shards  MemAvailable   source
#   2       >= 8192 MiB    电脑运维 8G rule (PANOPTES_MIN_MEM_GB) = the normal run
#   1       >= 6144 MiB    degraded floor (PANOPTES_DEGRADED_MEM_GB, 10-28 raised 5G -> 6G: one vitest process measured at
#                          2.77G RSS peak, and the host balloon reclaims memory mid-run): 1 shard, ARGUS_MAX_PARALLEL=1,
#                          lint and unit tests serialized
#   0       <  6144 MiB    not enough for one shard: wait (box would freeze), skip with exit 76 after PANOPTES_MEM_WAIT_S
slot_shard_mem_mb() { case "$1" in 2) echo $(( ${PANOPTES_MIN_MEM_GB:-8} * 1024 ));; 1) echo $(( ${PANOPTES_DEGRADED_MEM_GB:-6} * 1024 ));; *) echo 0;; esac; }
SLOT_NORMAL_SHARDS=2
# largest shard count whose row fits the current MemAvailable (0 = not even one shard, or swap below 4G)
slot_shards_for_mem() {
  local mb=$(( $(slot_mem_avail_kb) / 1024 )) max=$SLOT_NORMAL_SHARDS
  slot_low_swap && max=1   # swap free < 4G (or none): memory rules only, at most 1 shard
  local n; for (( n = max; n >= 1; n-- )); do (( mb >= $(slot_shard_mem_mb "$n") )) && { echo "$n"; return; }; done
  echo 0
}

# Memory gate (10-27 电脑运维's rule, 10-28: fewer shards instead of a 30-min wait). Sets GATE_MODE, GATE_WAIT_S (seconds waited),
# GATE_SHARDS (shards that fit, see the mapping above):
#   normal    MemAvailable >= 8G and SwapFree >= 4G: GATE_SHARDS=2
#   docs-only memory low but the optional command (args) says this run is docs-only (no tests run): no wait
#   degraded  < 8G (or swap free < 4G) but enough for 1 shard (>= 6G), at once or as soon as it gets there while waiting (no full
#             30-min wait any more: the host balloon reclaims memory, 8G cannot be guaranteed): caller runs GATE_SHARDS
#             unit shards and the coverage round after the first round (no parallel)
#   skip      below one shard for the whole PANOPTES_MEM_WAIT_S (1800 s, polled every PANOPTES_MEM_POLL_S = 60 s):
#             caller does not run (exit 76 = memory skip, slot released, next catch-up retries without counting an attempt;
#             mergewatch requeue counts it toward MAX_TRIES)
slot_mem_gate() {
  GATE_WAIT_S=0; GATE_MODE=normal; GATE_SHARDS=$SLOT_NORMAL_SHARDS; GATE_LOW_SWAP=0
  slot_low_swap && GATE_LOW_SWAP=1   # caller alerts; slot_shards_for_mem caps at 1 shard (>= 6144 MiB), < 6144 waits as before
  (( $(slot_shards_for_mem) >= SLOT_NORMAL_SHARDS )) && return 0
  if (( $# > 0 )) && "$@" >/dev/null 2>&1; then GATE_MODE=docs-only; return 0; fi
  local poll="${PANOPTES_MEM_POLL_S:-60}" start=$SECONDS n
  while :; do
    n=$(slot_shards_for_mem); GATE_WAIT_S=$(( SECONDS - start ))
    if (( n >= SLOT_NORMAL_SHARDS )); then GATE_MODE=normal; GATE_SHARDS=$n; return 0; fi
    if (( n >= 1 )); then GATE_MODE=degraded; GATE_SHARDS=$n; return 0; fi
    if (( GATE_WAIT_S >= ${PANOPTES_MEM_WAIT_S:-1800} )); then GATE_MODE=skip; GATE_SHARDS=0; return 0; fi
    echo "[panoptes] memory below one shard (>= $(slot_shard_mem_mb 1) MiB available$( (( GATE_LOW_SWAP )) || echo ", >= ${PANOPTES_MIN_SWAP_GB:-4} GiB swap free for 2 shards")), waiting (${GATE_WAIT_S}s so far)" >&2
    sleep "$poll"
  done
}
