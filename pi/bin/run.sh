#!/bin/bash
# Pi line job runner. Usage: run.sh daily|pm|p2 [YYYY-MM-DD data day]
set -u
JOB=${1:?job}; shift
# code = this pi/ directory; state (out/, takeover/, outbound-*) = PI_STATE (lib.py has the same three roots)
CODE=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
BASE=${PI_STATE:-/workspace/mivo-qa/pi}
export PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
export TZ=Asia/Shanghai
LOGDIR=$BASE/out/logs; mkdir -p "$LOGDIR"
LOG=$LOGDIR/$JOB-$(date +%F).log
# optional Beijing-hour gate: run.sh JOB @HH  (cron fires hourly; DST-proof)
if [[ "${1:-}" == @* ]]; then [[ ",${1#@}," == *",$(date +%H),"* ]] || exit 0; shift; fi
exec >>"$LOG" 2>&1
echo "=== $(date '+%F %T %Z') start $JOB $* mode=${PI_FLEET_MODE:-$(cat /workspace/fleet-mode 2>/dev/null || echo grok)} outbound=$([ -e $BASE/outbound-enabled ] && echo open || echo closed)"
avail=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
[ -n "${PI_TEST_MEM_AVAIL_MB:-}" ] && { avail=$PI_TEST_MEM_AVAIL_MB; echo "TEST: MemAvailable overridden to ${avail}MB"; }
export PI_MEM_AVAIL_MB=$avail
# F4 (10-23, updated 10-27): exempt from the 3G gate = boxhealth, cindy and every zero-model job in LIGHT below (they log
# "lowmem ... exempt -> run" and run anyway); outbound has its own floor inside outbound.py. cindy relaunch guard: Cindy not running
# and avail < 2048MB -> run cindy.py in its read-only dry-run (check only, never starts Cindy), alert, retry next */5.
# 10-23: zero-model jobs are light and must not lose a slot to the platform's snapshot-time memory reclaim (1-3 min dips);
# model/heavy jobs wait up to 10 min for memory instead of giving the slot up at once.
LIGHT=" daily handoff ledger pm pmnudge opsenv opsdeploy prretro merger boxcheck compare outbound credcheck roster followup watchdog prwatch janitor "
case $JOB in
  boxhealth) [ "$avail" -lt 3072 ] && echo "lowmem: ${avail}MB < 3072MB, boxhealth exempt -> run" ;;
  cindy)
    if [ "$avail" -lt 2048 ]; then
      if [ "${PI_TEST_CINDY_PROC:-}" = absent ] || { [ -z "${PI_TEST_CINDY_PROC:-}" ] && ! pgrep -f '^(/usr/bin/cindy|/usr/lib/cindy/Cindy)( |$)' >/dev/null; }; then
        export PI_CINDY_DRYRUN=1 PI_LOWMEM=1
        echo "ALERT lowmem-relaunch-blocked: Cindy not running, MemAvailable ${avail}MB < 2048MB -> check only (no relaunch), retry next run"
        printf '%s cindy relaunch blocked: MemAvailable %sMB < 2048MB\n' "$(date '+%F %T %Z')" "$avail" >> "$BASE/out/logs/lowmem-alerts.log"
      fi
    fi ;;
  *) if [[ "$LIGHT" == *" $JOB "* ]]; then [ "$avail" -lt 3072 ] && echo "lowmem: ${avail}MB < 3072MB, zero-model job exempt -> run"
     else
       for _ in $(seq 20); do [ "$avail" -ge 3072 ] && break; sleep 30; avail=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo); [ -n "${PI_TEST_MEM_AVAIL_MB:-}" ] && avail=$PI_TEST_MEM_AVAIL_MB; done
       if [ "$avail" -lt 3072 ]; then echo "skip: MemAvailable ${avail}MB < 3072MB after waiting 10 min"; exit 0; fi
       export PI_MEM_AVAIL_MB=$avail
     fi ;;
esac
# secret: env file outside any git repo / sync path, mode 600; never echoed
if [ -z "${DEEPSEEK_API_KEY:-}" ] && [ -r "$HOME/.pi-fallback-secret/env" ]; then set -a; . "$HOME/.pi-fallback-secret/env"; set +a; fi
[ -n "${DEEPSEEK_API_KEY:-}" ] && echo "key: set" || echo "key: unset"
# 10-24 (Praise: "并发数量看内存 不设上限"): prfix runs in parallel -- one ticket per process, claimed atomically under
# out/praise-pr/tickets.json's flock; prwatch decides how many by memory. Every other job stays one run at a time.
if [ "$JOB" != prfix ]; then
  exec 9>"/tmp/pi-fallback-$JOB.lock"
  flock -n 9 || { echo "skip: another $JOB run holds lock"; exit 0; }
fi
# 10-23: refuse to run a script that does not compile (two SyntaxErrors went live today); watchdog alerts on "syntax-error".
# 10-24: the bin/ modules the job imports at top level too (praise_notice, finding_policy, ...): a broken shared module takes
# every importer down, and it should say "syntax-error" rather than look like a run of failures
deps=$(python3 - "$CODE/bin/$JOB.py" 2>/dev/null <<'PY'
import ast, sys
try: tree = ast.parse(open(sys.argv[1]).read())
except Exception: sys.exit(0)          # the job itself does not compile: the loop below reports it
for n in ast.walk(tree):               # every import, also `import a, b` and the ones inside try / functions
    if isinstance(n, ast.Import): print(*(a.name.split(".")[0] for a in n.names))
    elif isinstance(n, ast.ImportFrom) and n.module and not n.level: print(n.module.split(".")[0])
PY
)
for f in "$CODE/bin/lib.py" "$CODE/bin/$JOB.py" $(for m in $deps; do [ -f "$CODE/bin/$m.py" ] && echo "$CODE/bin/$m.py"; done); do
  if ! err=$(PYTHONPYCACHEPREFIX=/tmp/pi-fallback-pyc python3 -m py_compile "$f" 2>&1); then
    echo "syntax-error: $(basename "$f"): $(echo "$err" | tail -1)"; echo "=== $(date '+%F %T %Z') end $JOB rc=3"; exit 3
  fi
done
python3 "$CODE/bin/$JOB.py" "$@"; rc=$?
echo "=== $(date '+%F %T %Z') end $JOB rc=$rc"
exit $rc
