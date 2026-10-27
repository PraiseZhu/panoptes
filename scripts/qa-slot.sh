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
# Memory gate: MemAvailable >= PANOPTES_MIN_MEM_GB (default 8) AND SwapFree >= PANOPTES_MIN_SWAP_GB (default 4).

SLOT_POLL_S="${PANOPTES_SLOT_POLL_S:-2}"

slot_dir() { printf '%s\n' "$ARGUS_ROOT/priority"; }

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

# slot_acquire_normal FD WAIT_S (WAIT_S empty = do not wait). 0 = got the slot, 1 = gave up.
slot_acquire_normal() {
  local fd="$1" wait="$2" start=$SECONDS
  slot_check_cap || return 1
  while :; do
    if (( $(slot_live req) == 0 )) && flock -n "$fd"; then return 0; fi
    [[ -n "$wait" ]] && (( SECONDS - start < wait )) || return 1
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
    if flock -n "$fd"; then SLOT_MODE=lock; return 0; fi
    if (( $(slot_live yield) > 0 )); then SLOT_MODE=borrowed; return 0; fi
    (( SECONDS - start < wait )) || { rm -f "$SLOT_REQ"; return 1; }
    sleep "$SLOT_POLL_S"
  done
}

slot_release_priority() { [[ -n "${SLOT_REQ:-}" ]] && rm -f "$SLOT_REQ"; return 0; }

# 0 when memory is enough to start a run
# MemAvailable in kB; PI_TEST_MEM_AVAIL_MB overrides it only with PANOPTES_TEST=1 (tests: simulate 9000 / 5500 / 4000 MB)
slot_mem_avail_kb() {
  if [[ -n "${PI_TEST_MEM_AVAIL_MB:-}" && "${PANOPTES_TEST:-}" == 1 ]]; then echo $(( PI_TEST_MEM_AVAIL_MB * 1024 )); return; fi
  awk '/^MemAvailable:/{print $2}' "${PANOPTES_MEMINFO:-/proc/meminfo}"
}
slot_swap_free_kb() { awk '/^SwapFree:/{print $2}' "${PANOPTES_MEMINFO:-/proc/meminfo}"; }
# host virtio balloon in MB: (balloon_inflate - balloon_deflate) pages from /proc/vmstat (cumulative counters; log only)
slot_balloon_mb() { awk -v pg="$(getconf PAGESIZE 2>/dev/null || echo 4096)" '/^balloon_inflate /{i=$2} /^balloon_deflate /{d=$2} END{v=(i-d)*pg/1048576; print (v>0?int(v):0)}' "${PANOPTES_VMSTAT:-/proc/vmstat}" 2>/dev/null || echo 0; }
slot_mem_ok() {
  local avail swap
  avail=$(slot_mem_avail_kb); swap=$(slot_swap_free_kb)
  (( ${avail:-0} >= ${PANOPTES_MIN_MEM_GB:-8} * 1024 * 1024 && ${swap:-0} >= ${PANOPTES_MIN_SWAP_GB:-4} * 1024 * 1024 ))
}

# Memory gate (10-27, 电脑运维's rule). Sets GATE_MODE and GATE_WAIT_S (seconds waited):
#   normal    MemAvailable >= 8G and SwapFree >= 4G (at once or within PANOPTES_MEM_WAIT_S = 1800 s, polled every PANOPTES_MEM_POLL_S = 60 s)
#   docs-only memory low but the optional command (args) says this run is docs-only (no tests run): no wait
#   degraded  still low after the full wait, but MemAvailable >= 5G (PANOPTES_DEGRADED_MEM_GB) and SwapFree >= 4G:
#             caller runs one unit shard and the coverage round after the first round (no parallel)
#   skip      otherwise: caller does not run (exit 76 = memory skip, slot released, next catch-up retries without counting an attempt)
slot_mem_gate() {
  GATE_WAIT_S=0; GATE_MODE=normal
  slot_mem_ok && return 0
  if (( $# > 0 )) && "$@" >/dev/null 2>&1; then GATE_MODE=docs-only; return 0; fi
  local poll="${PANOPTES_MEM_POLL_S:-60}" start=$SECONDS
  while ! slot_mem_ok; do
    if (( SECONDS - start >= ${PANOPTES_MEM_WAIT_S:-1800} )); then
      GATE_WAIT_S=$(( SECONDS - start ))
      local avail swap; avail=$(slot_mem_avail_kb); swap=$(slot_swap_free_kb)
      if (( ${avail:-0} >= ${PANOPTES_DEGRADED_MEM_GB:-5} * 1024 * 1024 && ${swap:-0} >= ${PANOPTES_MIN_SWAP_GB:-4} * 1024 * 1024 )); then GATE_MODE=degraded; else GATE_MODE=skip; fi
      return 0
    fi
    echo "[panoptes] memory below gate (>= ${PANOPTES_MIN_MEM_GB:-8} GiB available, >= ${PANOPTES_MIN_SWAP_GB:-4} GiB swap free), waiting ($(( SECONDS - start ))s so far)" >&2
    sleep "$poll"
  done
  GATE_WAIT_S=$(( SECONDS - start )); return 0
}
