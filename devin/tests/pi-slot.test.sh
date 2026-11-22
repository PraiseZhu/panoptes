#!/usr/bin/env bash
# pi-slot.test.sh — devin/bin/pi-slot.sh with fake jobs against throw-away bare repos (never the real grokbot-box).
# Run: bash devin/tests/pi-slot.test.sh
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
SLOT_SH="$HERE/../bin/pi-slot.sh"
SLOTS="$HERE/../conf/slots.json"
CRON="$HERE/../../pi/cron/pi-line.cron"
T=$(mktemp -d)
trap '[[ -n "${KEEP:-}" ]] || rm -rf "$T"' EXIT
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL="$T/gitconfig" STATE_SYNC_BACKOFF_MIN=0 STATE_SYNC_BACKOFF_MAX=1
: >"$GIT_CONFIG_GLOBAL"
export QA_SECRETS_FILE="$T/secrets.env" PRAISE_QA_KEY="$T/app.pem" PI_SLOT_LOCK_POLL=1 PI_SLOT_SLEEP="$T/fake-sleep"
D=box/workspace/mivo-qa
DAY=$(TZ=Asia/Shanghai date +%F)
PASS=0 FAIL=0

ok() { PASS=$((PASS + 1)); echo "ok   $1"; }
bad() { FAIL=$((FAIL + 1)); echo "FAIL $1" >&2; }
check() { local name=$1; shift; if "$@"; then ok "$name"; else bad "$name"; fi; }
eq() { local name=$1 want=$2 got=$3; if [[ "$want" == "$got" ]]; then ok "$name"; else bad "$name: want [$want] got [$got]"; fi; }
jget() { python3 -c 'import json,sys; d=json.loads(sys.stdin.read().strip().splitlines()[-1])
for k in sys.argv[1].split("."): d=d[int(k)] if isinstance(d,list) else d[k]
print(json.dumps(d) if isinstance(d,(dict,list)) else d)' "$1"; }

# --- fakes ---
printf '#!/usr/bin/env bash\necho "sleep $*" >>"%s"\n' "$T/calls.log" >"$PI_SLOT_SLEEP"
# fake pi/bin/run.sh: logs every call; daily without --no-send "sends" (like daily.py send_own) when the gate file is there;
# outbound without --dry-run is a real send attempt; FAIL_JOBS lists jobs that exit 3
cat >"$T/fake-run.sh" <<'FAKE'
#!/usr/bin/env bash
job=$1; shift
echo "$job $*" >>"$CALLS"
gate=0; [[ -e "$QA_HOME/pi/outbound-enabled" ]] && gate=1
case "$job" in
  daily)
    yday=$(TZ=Asia/Shanghai date -d yesterday +%F); mkdir -p "$QA_HOME/pi/out/daily"
    echo "# daily $yday" >"$QA_HOME/pi/out/daily/$yday.md"
    [[ " $* " == *" --no-send "* ]] || echo "REAL-SEND daily gate=$gate" >>"$SENDS" ;;
  outbound)
    mkdir -p "$QA_HOME/pi/out/outbound"
    if [[ " $* " == *" --dry-run "* ]]; then echo plan >"$QA_HOME/pi/out/outbound/$(TZ=Asia/Shanghai date +%F).dry-run.md"
    else echo "REAL-SEND outbound gate=$gate" >>"$SENDS"; echo sent >"$QA_HOME/pi/out/outbound-sent.txt"; fi ;;
  *) mkdir -p "$QA_HOME/pi/out"; echo "$job ran" >"$QA_HOME/pi/out/$job.txt" ;;
esac
[[ " ${FAIL_JOBS:-} " == *" $job "* ]] && exit 3
exit 0
FAKE
chmod +x "$T/fake-run.sh" "$PI_SLOT_SLEEP"
export PI_SLOT_RUNNER="$T/fake-run.sh" CALLS="$T/calls.log" SENDS="$T/sends.log"

seed() { # bare remote: devin branch with mivo-qa data incl. pi/outbound-enabled (open gate); devin-lock absent
  rm -rf "$T/remote.git" "$T/seed" "$T/state" "$T/work" "$T/qa" "$T/calls.log" "$T/sends.log"
  : >"$T/calls.log"; : >"$T/sends.log"
  git init -q --bare -b devin "$T/remote.git"
  git -C "$T/remote.git" config uploadpack.allowFilter true
  git init -q -b devin "$T/seed"
  mkdir -p "$T/seed/$D/pi/out" "$T/seed/$D/reports/merged"
  echo open >"$T/seed/$D/pi/outbound-enabled"
  echo '{"pr":1}' >"$T/seed/$D/reports/merged/PR-1.json"
  echo pi >"$T/seed/box/workspace/fleet-mode"
  git -C "$T/seed" add -A
  git -C "$T/seed" -c user.name=t -c user.email=t@t commit -qm seed
  git -C "$T/seed" push -q "$T/remote.git" devin
}
run_slot() { # run_slot ARGS... -> stdout in $T/out, rc in RC
  RC=0
  bash "$SLOT_SH" --repo-url "$T/remote.git" --state-dir "$T/state" --work-dir "$T/work" --qa-home "$T/qa/mivo-qa" \
    --slots "$SLOTS" "$@" >"$T/out" 2>"$T/err" || RC=$?
}
remote_has() { git -C "$T/remote.git" rev-parse -q --verify "refs/heads/$1" >/dev/null; }
lock_file() { git -C "$T/remote.git" show "devin-lock:$1" 2>/dev/null; }
set_lock() { # set_lock HOLDER AGE_SECONDS: put a lease onto the remote devin-lock branch
  rm -rf "$T/lk"; git init -q -b devin-lock "$T/lk"
  printf '{"holder":"%s","slot":"07:00","at":%s}\n' "$1" "$(( $(date +%s) - $2 ))" >"$T/lk/lock.json"
  git -C "$T/lk" add lock.json; git -C "$T/lk" -c user.name=t -c user.email=t@t commit -qm lock
  git -C "$T/lk" push -qf "$T/remote.git" devin-lock
}

# --- slots.json: 14 Beijing slots, cron mapping ---
check "slots.json maps pi-line.cron (14 slots, 3 alignments)" python3 - "$SLOTS" "$CRON" <<'PY'
import json, re, sys
conf = json.load(open(sys.argv[1])); cron = open(sys.argv[2]).read()
slots = conf["slots"]; at = [s["at"] for s in slots]
assert conf["timezone"] == "Asia/Shanghai" and len(slots) == 14 and len(set(at)) == 14 and at == sorted(at), at
want = {"00:05": ["handoff"], "06:20": ["handoff", "followup"], "07:00": ["credcheck"], "07:35": ["daily"], "08:20": ["roster"],
        "08:35": ["daily"], "09:30": ["opsfix"], "09:50": ["handoff"], "12:30": ["opsfix"], "15:20": ["handoff", "followup"],
        "16:30": ["opsfix"], "18:35": ["handoff"], "20:30": ["opsfix"], "23:40": ["ledger"]}
assert {s["at"]: [j["job"] for j in s["jobs"]] for s in slots} == want
assert next(s for s in slots if s["at"] == "07:35").get("warmup_at") == "07:30"
assert next(s for s in slots if s["at"] == "09:50")["jobs"][0]["args"] == ["backfill"]
for s in slots:
    assert len(s["cron"]) == len(s["jobs"])
    for c in s["cron"]:
        assert c["entry"] in cron, c["entry"]                        # every mapped entry is a real line of pi-line.cron
        m, hours = re.match(r"(\d+)\s.*@([\d,]+)", c["entry"]).groups()
        assert c["cron_at"][:2] in hours.split(",") and int(c["cron_at"][3:]) == int(m), c
        h1, m1 = map(int, c["cron_at"].split(":")); h2, m2 = map(int, s["at"].split(":"))
        assert (h2 * 60 + m2) - (h1 * 60 + m1) == c["offset_min"], c
moved = sorted((c["job"], c["cron_at"], s["at"]) for s in slots for c in s["cron"] if c["offset_min"])
assert moved == [("credcheck", "07:01", "07:00"), ("followup", "06:27", "06:20"), ("followup", "15:27", "15:20"), ("roster", "08:23", "08:20")], moved
assert sorted((a["job"], a["cron"], a["slot"]) for a in conf["alignments"]) == moved
for line in cron.splitlines():                                     # every daily-pinned job line of the cron is mapped
    if re.search(r"\$R (handoff|daily|opsfix|followup|roster|credcheck|ledger) @", line):
        assert any(c["entry"] == line.strip() for s in slots for c in s["cron"]), line
PY

# --- SC1 dry-run: plan only, no job, no lock ---
seed
run_slot --slot 07:35 --dry-run
eq "dry-run rc" 0 "$RC"
check "dry-run prints daily and outbound" bash -c "grep -q 'plan: .* daily\$' '$T/out' && grep -q 'plan: .* outbound\$' '$T/out'"
eq "dry-run runs no job" "" "$(cat "$T/calls.log")"
check "dry-run writes no lock branch" bash -c "! git -C '$T/remote.git' rev-parse -q --verify refs/heads/devin-lock"
run_slot --slot 07:35 --dry-run --shadow
check "shadow dry-run plans daily --no-send and outbound --dry-run" bash -c "grep -q 'daily --no-send\$' '$T/out' && grep -q 'outbound --dry-run\$' '$T/out'"
run_slot --slot 7:35 --dry-run; eq "bad --slot is a usage error" 2 "$RC"
run_slot --slot 07:36 --dry-run; eq "unknown slot is rejected" 2 "$RC"
run_slot --dry-run --slot 07:35 --lock-branch devin; eq "lock branch = data branch is refused" 2 "$RC"

# --- normal run, then same-day duplicate skipped ---
seed
run_slot --slot 06:20
eq "normal run rc" 0 "$RC"
eq "normal run job order" $'handoff \nfollowup \noutbound ' "$(cat "$T/calls.log")"
eq "result json jobs" "handoff,followup" "$(jget jobs <"$T/out" | python3 -c 'import json,sys; print(",".join(j["job"] for j in json.load(sys.stdin)))')"
eq "lock taken fresh" acquired "$(jget lock <"$T/out")"
check "run marker on devin-lock" bash -c "git -C '$T/remote.git' show 'devin-lock:runs/$DAY/0620.json' | grep -q '\"rc\": 0'"
eq "lease released" None "$(lock_file lock.json | python3 -c 'import json,sys; print(json.load(sys.stdin)["holder"])')"
check "devin branch has no lock or marker files" bash -c "! git -C '$T/remote.git' ls-tree -r --name-only devin | grep -Eq '(^|/)(lock\.json|runs/)'"
check "normal push reached devin" bash -c "git -C '$T/remote.git' show 'devin:$D/pi/out/followup.txt' | grep -q 'followup ran'"
: >"$T/calls.log"
run_slot --slot 06:20
eq "second run same day rc" 0 "$RC"
check "second run same day skipped" grep -q "already ran on $DAY" <<<"$(jget skipped <"$T/out")"
eq "second run runs nothing" "" "$(cat "$T/calls.log")"

# --- automatic slot choice: +-10 min, waits for the slot, skips a slot done today ---
: >"$T/calls.log"
PI_SLOT_NOW="${DAY}T06:15:00+08:00" run_slot
eq "auto: 06:20 done today -> nothing in window" "no slot within the match window" "$(jget skipped <"$T/out")"
PI_SLOT_NOW="${DAY}T07:28:00+08:00" run_slot
eq "auto: 07:28 picks 07:35" "07:35" "$(jget slot <"$T/out")"
check "auto: waits 420s for 07:35 (07:30 warm-up)" grep -qx 'sleep 420' "$T/calls.log"
PI_SLOT_NOW="${DAY}T03:00:00+08:00" run_slot
eq "auto: 03:00 has no slot" "no slot within the match window" "$(jget skipped <"$T/out")"

# --- SC2 lock: busy lease times out with 75, stale lease (>30 min) is taken over ---
seed; set_lock other-session 60
PI_SLOT_SLEEP="sleep" run_slot --slot 07:00 --lock-timeout 2
eq "busy lock -> exit 75" 75 "$RC"
eq "busy lock runs nothing" "" "$(cat "$T/calls.log")"
eq "busy lock left alone" other-session "$(lock_file lock.json | python3 -c 'import json,sys; print(json.load(sys.stdin)["holder"])')"
seed; set_lock dead-session 1900
run_slot --slot 07:00 --lock-timeout 2
eq "stale lock taken over rc" 0 "$RC"
eq "stale lock state" takeover "$(jget lock <"$T/out")"
eq "stale lock job ran" $'credcheck \noutbound ' "$(cat "$T/calls.log")"
check "takeover recorded in lock history" bash -c "git -C '$T/remote.git' log --format=%s devin-lock | grep -q 'takeover of dead-session'"

# --- SC2 failure: a failing job does not stop the next job or outbound; rc = max ---
seed
FAIL_JOBS=handoff run_slot --slot 15:20
eq "failed job -> rc 3" 3 "$RC"
eq "followup and outbound still ran" $'handoff \nfollowup \noutbound ' "$(cat "$T/calls.log")"
eq "result json rc of handoff" 3 "$(jget jobs.0.rc <"$T/out")"
eq "result json ok false" False "$(jget ok <"$T/out")"

# --- SC3 shadow: remote devin has pi/outbound-enabled, fake daily sends unless --no-send -> zero sends ---
seed
mkdir -p "$T/qa/mivo-qa/pi"; echo open >"$T/qa/mivo-qa/pi/outbound-enabled"
before=$(git -C "$T/remote.git" rev-parse devin)
run_slot --slot 07:35 --shadow
eq "shadow rc" 0 "$RC"
eq "shadow: zero real sends" "" "$(cat "$T/sends.log")"
eq "shadow: daily got --no-send, outbound only --dry-run" $'daily --no-send\noutbound --dry-run' "$(cat "$T/calls.log")"
check "shadow: local outbound gate removed" test ! -e "$T/qa/mivo-qa/pi/outbound-enabled"
YDAY=$(TZ=Asia/Shanghai date -d yesterday +%F)
changed=$(git -C "$T/remote.git" diff --name-only "$before" devin)
check "shadow: devin changed only under pi/out/devin/shadow/<data day>/" bash -c "[[ -n '$changed' ]] && ! grep -v '^$D/pi/out/devin/shadow/$YDAY/' <<<'$changed'"
check "shadow: daily draft and dry-run plan pushed" bash -c "grep -q '0735-$YDAY.md' <<<'$changed' && grep -q '0735-$DAY.dry-run.md' <<<'$changed'"
check "shadow: remote gate file untouched" bash -c "git -C '$T/remote.git' show 'devin:$D/pi/outbound-enabled' | grep -q open"
check "shadow marker separate from normal" bash -c "git -C '$T/remote.git' show 'devin-lock:runs/$DAY/0735.shadow.json' >/dev/null"
# guard check: the fake really sends when daily lacks --no-send and the gate is open (so the zero above means something)
: >"$T/sends.log"; mkdir -p "$T/qa/mivo-qa/pi"; echo open >"$T/qa/mivo-qa/pi/outbound-enabled"
QA_HOME="$T/qa/mivo-qa" "$T/fake-run.sh" daily
eq "control: fake daily without --no-send sends" "REAL-SEND daily gate=1" "$(cat "$T/sends.log")"

echo "pi-slot.test: $PASS passed, $FAIL failed"
((FAIL == 0))
