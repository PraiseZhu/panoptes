#!/usr/bin/env bash
# state-sync.test.sh — devin/bin/state-sync.sh against a throw-away bare repo (never the real grokbot-box).
# Run: bash devin/tests/state-sync.test.sh
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
SYNC="$HERE/../bin/state-sync.sh"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL="$T/gitconfig" STATE_SYNC_BACKOFF_MIN=0 STATE_SYNC_BACKOFF_MAX=1
: >"$GIT_CONFIG_GLOBAL"
D=box/workspace/mivo-qa
BOT='praisedevin[bot] <358239493+praisedevin[bot]@users.noreply.github.com>'
DAY=$(TZ=Asia/Shanghai date +%F)
RUN_ID=fleet-0123456789abcdef0123456789abcdef
PASS=0 FAIL=0

ok() { PASS=$((PASS + 1)); echo "ok   $1"; }
bad() { FAIL=$((FAIL + 1)); echo "FAIL $1" >&2; }
check() { local name=$1; shift; if "$@"; then ok "$name"; else bad "$name"; fi; }
eq() { local name=$1 want=$2 got=$3; if [[ "$want" == "$got" ]]; then ok "$name"; else bad "$name: want [$want] got [$got]"; fi; }

seed() { # fresh bare remote with a devin branch holding a small mivo-qa tree
  rm -rf "$T/remote.git" "$T/seed"
  git init -q --bare -b devin "$T/remote.git"
  git -C "$T/remote.git" config uploadpack.allowFilter true
  git init -q -b devin "$T/seed"
  mkdir -p "$T/seed/$D"/{reports/merged,reports/catch-up,argus-root/gate-wait,argus-root/cache/review-verdicts,pi/out/logs,memguard,pi/out/tasks,ledger}
  local s="$T/seed/$D"
  echo '{"pr":1000}' >"$s/reports/merged/PR-1000.json"
  echo '{"pr":1001}' >"$s/reports/merged/PR-1001.json"
  echo '{"pr":2464,"v":1}' >"$s/reports/merged/PR-2464.json"
  printf '%s\n' '{"pr":1}' '{"pr":2}' >"$s/reports/merged/manual-runs.jsonl"
  echo old >"$s/argus-root/cache/review-verdicts/aa.json"
  for f in gh-app-enabled gh-app-nightly-enabled gh-app-wrapper-enabled; do echo on >"$s/pi/$f"; done
  echo log >"$s/pi/out/logs/x.log"; echo m >"$s/memguard/m.txt"; echo t >"$s/pi/out/tasks/t.json"
  echo out >"$s/run.out"; echo bak >"$s/ledger/SCHEMA.md.bak-1"; echo schema >"$s/ledger/SCHEMA.md"
  for i in $(seq 1 "${1:-0}"); do mkdir -p "$s/bulk"; echo "$i" >"$s/bulk/f$i"; done
  echo pi >"$T/seed/box/workspace/fleet-mode"
  echo other >"$T/seed/README.md"
  git -C "$T/seed" add -A
  git -C "$T/seed" -c user.name=seed -c user.email=seed@x commit -q -m seed
  git -C "$T/seed" push -q "$T/remote.git" devin
}
URL() { echo "file://$T/remote.git"; }
remote_head() { git -C "$T/remote.git" rev-parse devin; }
remote_cat() { git -C "$T/remote.git" show "devin:$1" 2>/dev/null; }
remote_has() { git -C "$T/remote.git" cat-file -e "devin:$1" 2>/dev/null; }
sync() { local name=$1; shift; "$SYNC" "$@" --repo-url "$(URL)" --work-dir "$T/wd-$name" --qa-home "$T/home-$name/mivo-qa"; }
other_commit() { # someone else pushes path=content to the remote
  rm -rf "$T/other"; git clone -q -b devin "$(URL)" "$T/other"
  mkdir -p "$(dirname "$T/other/$1")"; printf '%s' "$2" >>"$T/other/$1"
  git -C "$T/other" add -A; git -C "$T/other" -c user.name=o -c user.email=o@x commit -q -m other; git -C "$T/other" push -q origin devin
}

echo "# usage"
set +e
"$SYNC" fetch qa --pr 1 >/dev/null 2>&1; eq "bad subcommand exits 2" 2 $?
"$SYNC" pull qa >/dev/null 2>&1; eq "qa without --pr exits 2" 2 $?
"$SYNC" pull slots --branch main >/dev/null 2>&1; eq "--branch main refused (2)" 2 $?
seed
sync u push qa --pr 2464 >/dev/null 2>&1; eq "push before pull exits 3" 3 $?
set -e

echo "# SC1 qa pull is sparse, qa push only adds/modifies PR-2464 whitelisted paths"
seed
sync a pull qa --pr 2464 2>/dev/null
H="$T/home-a/mivo-qa"
eq "pull qa brings PR-2464 report" '{"pr":2464,"v":1}' "$(cat "$H/reports/merged/PR-2464.json")"
check "pull qa skips other PR reports" test ! -e "$H/reports/merged/PR-1000.json"
check "pull qa brings review cache" test -f "$H/argus-root/cache/review-verdicts/aa.json"
for f in gh-app-enabled gh-app-nightly-enabled gh-app-wrapper-enabled; do
  eq "pull qa brings switch pi/$f (#323)" on "$(cat "$H/pi/$f" 2>/dev/null)"
done
before=$(remote_head)
mkdir -p "$H/reports/catch-up" "$H/argus-root/gate-wait" "$H/pi/out/devin/quota/$DAY" "$H/pi/out/devin/runs/PR-2464/r1" "$H/pi/out/qa-fleet/runs"
echo '{"pr":2464,"v":2}' >"$H/reports/merged/PR-2464.json"
echo '{"pr":2464}' >"$H/reports/catch-up/PR-2464.json"
echo 42 >"$H/argus-root/gate-wait/PR-2464"
echo '{"calls":3}' >"$H/pi/out/devin/quota/$DAY/PR-2464.json"
echo run >"$H/pi/out/devin/runs/PR-2464/r1/run.json"
printf '{"schema":"qa.fleet.run/v1","run_id":"%s"}\n' "$RUN_ID" >"$H/argus-root/qa-fleet-run-manifest.json"
echo receipt >"$H/pi/out/qa-fleet/runs/$RUN_ID.json"
echo stale >"$H/pi/out/qa-fleet/runs/fleet-ffffffffffffffffffffffffffffffff.json"
echo new >"$H/argus-root/cache/review-verdicts/bb.json"
echo changed >"$H/argus-root/cache/review-verdicts/aa.json"
echo '{"pr":1000,"tampered":1}' >"$H/reports/merged/PR-1000.json"
echo '{"pr":2465}' >"$H/reports/merged/PR-2465.json"
echo 9 >"$H/argus-root/gate-wait/PR-2465"
echo '{"pr":99}' >>"$H/reports/merged/manual-runs.jsonl"
rm -f "$H/reports/merged/PR-1001.json"
check "switch pi/gh-app-enabled is local before push (#323)" test -f "$H/pi/gh-app-enabled"
echo off >"$H/pi/gh-app-enabled"; echo on >"$H/pi/gh-app-extra-enabled"
sync a push qa --pr 2464 2>/dev/null
changed=$(git -C "$T/remote.git" diff --name-status "$before" devin | sort | tr '\t' ' ' | tr '\n' ';')
want="A $D/argus-root/cache/review-verdicts/bb.json;A $D/argus-root/gate-wait/PR-2464;A $D/pi/out/devin/quota/$DAY/PR-2464.json;A $D/pi/out/devin/runs/PR-2464/r1/run.json;A $D/pi/out/qa-fleet/runs/$RUN_ID.json;A $D/reports/catch-up/PR-2464.json;M $D/reports/merged/PR-2464.json;"
eq "push qa changes exactly the PR-2464 whitelist" "$want" "$changed"
eq "other PR report untouched" '{"pr":1000}' "$(remote_cat "$D/reports/merged/PR-1000.json")"
check "locally deleted PR-1001 still on remote" remote_has "$D/reports/merged/PR-1001.json"
eq "push qa leaves switch pi/gh-app-enabled as pulled (#323)" on "$(remote_cat "$D/pi/gh-app-enabled")"
if remote_has "$D/pi/gh-app-extra-enabled"; then bad "push qa never adds switch files (#323)"; else ok "push qa never adds switch files (#323)"; fi
eq "existing review cache entry not overwritten" old "$(remote_cat "$D/argus-root/cache/review-verdicts/aa.json")"
eq "manual-runs.jsonl untouched outside backfill" 2 "$(remote_cat "$D/reports/merged/manual-runs.jsonl" | wc -l | tr -d ' ')"
check "base marker removed after push" test ! -e "$T/wd-a/.git/state-sync.base"

echo "# SC4 commit identity and subject"
eq "author is praisedevin[bot]" "$BOT" "$(git -C "$T/remote.git" log -1 --format='%an <%ae>' devin)"
eq "committer is praisedevin[bot]" "$BOT" "$(git -C "$T/remote.git" log -1 --format='%cn <%ce>' devin)"
subj=$(git -C "$T/remote.git" log -1 --format=%s devin)
check "qa subject 'devin sync qa PR-2464 <UTC>' ($subj)" grep -Eq '^devin sync qa PR-2464 [0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' <<<"$subj"

echo "# qa backfill appends manual-runs.jsonl after the pulled version only"
seed
sync b pull qa --pr 2464 --mode backfill 2>/dev/null
echo '{"pr":2464,"kind":"backfill"}' >>"$T/home-b/mivo-qa/reports/merged/manual-runs.jsonl"
other_commit "$D/reports/merged/manual-runs.jsonl" $'{"pr":7}\n'
sync b push qa --pr 2464 --mode backfill -m "停摆补测" 2>/dev/null
eq "both appended lines kept, in order" $'{"pr":1}\n{"pr":2}\n{"pr":7}\n{"pr":2464,"kind":"backfill"}' "$(remote_cat "$D/reports/merged/manual-runs.jsonl")"
check "-m text lands in the commit body" grep -q 停摆补测 <<<"$(git -C "$T/remote.git" log -1 --format=%b devin)"
sync b pull qa --pr 2464 --mode backfill 2>/dev/null
printf '%s\n' '{"rewritten":1}' >"$T/home-b/mivo-qa/reports/merged/manual-runs.jsonl"
before=$(remote_head)
set +e; sync b push qa --pr 2464 --mode backfill >/dev/null 2>&1; rc=$?; set -e
eq "rewriting manual-runs.jsonl refused (3)" 3 "$rc"
eq "remote unchanged after refusal" "$before" "$(remote_head)"

echo "# SC2 two concurrent qa pushes both land"
seed
for pr in 2464 2465; do
  "$SYNC" pull qa --pr "$pr" --repo-url "$(URL)" --work-dir "$T/wd-c$pr" --qa-home "$T/home-c$pr/mivo-qa" 2>/dev/null
  mkdir -p "$T/home-c$pr/mivo-qa/reports/merged" "$T/home-c$pr/mivo-qa/argus-root/cache/review-verdicts"
  echo "{\"pr\":$pr,\"concurrent\":1}" >"$T/home-c$pr/mivo-qa/reports/merged/PR-$pr.json"
  echo same >"$T/home-c$pr/mivo-qa/argus-root/cache/review-verdicts/shared.json"
done
declare -A pid
for pr in 2464 2465; do
  "$SYNC" push qa --pr "$pr" --repo-url "$(URL)" --work-dir "$T/wd-c$pr" --qa-home "$T/home-c$pr/mivo-qa" 2>"$T/c$pr.err" &
  pid[$pr]=$!
done
rc1=0; wait "${pid[2464]}" || rc1=$?; rc2=0; wait "${pid[2465]}" || rc2=$?
eq "push PR-2464 exits 0" 0 "$rc1"; eq "push PR-2465 exits 0" 0 "$rc2"
eq "remote keeps PR-2464 report" '{"pr":2464,"concurrent":1}' "$(remote_cat "$D/reports/merged/PR-2464.json")"
eq "remote keeps PR-2465 report" '{"pr":2465,"concurrent":1}' "$(remote_cat "$D/reports/merged/PR-2465.json")"
eq "seed files survive" '{"pr":1000}' "$(remote_cat "$D/reports/merged/PR-1000.json")"
eq "two sync commits on top of seed" 3 "$(git -C "$T/remote.git" rev-list --count devin)"

echo "# SC3 slots pull/push, delete guard and local excludes"
seed 250
sync s pull slots 2>/dev/null
H="$T/home-s/mivo-qa"
eq "fleet-mode pulled" pi "$(cat "$T/home-s/fleet-mode")"
check "pull slots brings ledger" test -f "$H/ledger/SCHEMA.md"
for x in pi/out/logs memguard pi/out/tasks run.out ledger/SCHEMA.md.bak-1; do check "pull slots skips $x" test ! -e "$H/$x"; done
rm -f "$H"/bulk/f{1..201}
before=$(remote_head)
set +e; sync s push slots --mode 07:35 >/dev/null 2>"$T/s.err"; rc=$?; set -e
eq "201 deletions refused (90)" 90 "$rc"
eq "remote HEAD unchanged after refusal" "$before" "$(remote_head)"
check "refusal names the count" grep -q 'delete 201 files' "$T/s.err"
echo 1 >"$H/bulk/f1"
mkdir -p "$H/cache/ms-playwright" "$H/.venv/bin" "$H/argus-root/app/state" "$H/argus-root/logs" "$H/reports/merged"
echo c >"$H/cache/ms-playwright/x"; echo v >"$H/.venv/bin/py"; echo db >"$H/argus-root/app/state/argus.sqlite"
echo l >"$H/argus-root/logs/control.log"; echo k >"$H/catch-up.lock"; echo '{"new":1}' >"$H/reports/merged/PR-3000.json"
echo '{"pr":1000,"v":2}' >"$H/reports/merged/PR-1000.json"
other_commit "$D/reports/merged/PR-3001.json" '{"pr":3001}'
sync s push slots --mode 07:35 2>/dev/null
eq "200 deletions pushed" 50 "$(git -C "$T/remote.git" ls-tree --name-only "devin:$D/bulk" | wc -l | tr -d ' ')"
eq "slots change pushed" '{"pr":1000,"v":2}' "$(remote_cat "$D/reports/merged/PR-1000.json")"
check "concurrent remote commit kept by rebase" remote_has "$D/reports/merged/PR-3001.json"
for x in cache/ms-playwright/x .venv/bin/py argus-root/app/state/argus.sqlite argus-root/logs/control.log catch-up.lock; do
  if remote_has "$D/$x"; then bad "push slots excludes $x"; else ok "push slots excludes $x"; fi
done
for x in pi/out/logs/x.log memguard/m.txt pi/out/tasks/t.json run.out ledger/SCHEMA.md.bak-1 argus-root/cache/review-verdicts/aa.json; do
  check "push slots keeps unpulled $x" remote_has "$D/$x"
done
subj=$(git -C "$T/remote.git" log -1 --format=%s devin)
check "slots subject 'devin sync slots 07:35 <UTC>' ($subj)" grep -Eq '^devin sync slots 07:35 [0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' <<<"$subj"
eq "slots author is praisedevin[bot]" "$BOT" "$(git -C "$T/remote.git" log -1 --format='%an <%ae>' devin)"

echo "# issue #331 SC7: slots never sync the handoff key"
seed
key=pi/outbound/handoff-files.key
H="$T/home-key/mivo-qa"
mkdir -p "$H/pi/outbound"
printf local-fixture >"$H/$key"
printf neighbor >"$H/$key.example"
sync key pull slots
sync key push slots
if remote_has "$D/$key"; then bad "slots push excludes the key"; else ok "slots push excludes the key"; fi
check "slots push includes similarly named file" remote_has "$D/$key.example"
other_commit "$D/$key" remote-fixture
sync key pull slots
check "slots pull keeps the local key" grep -qx local-fixture "$H/$key"
printf 'changed\n' >"$H/ledger/SCHEMA.md"
sync key push slots
eq "slots push leaves the remote key untouched" remote-fixture "$(remote_cat "$D/$key")"
sync key pull slots
printf staged-fixture >"$T/wd-key/$D/$key"
git -C "$T/wd-key" add -- "$D/$key"
before=$(remote_head)
set +e; sync key push slots >"$T/key.out" 2>"$T/key.err"; rc=$?; set -e
eq "slots push refuses a staged key (94)" 94 "$rc"
check "staged-key refusal names the path" grep -Fq "refused staged $key" "$T/key.err"
eq "remote HEAD unchanged after staged-key refusal" "$before" "$(remote_head)"

echo "# slots rebase conflict stops (91)"
seed
sync r pull slots 2>/dev/null
echo mine >"$T/home-r/mivo-qa/ledger/SCHEMA.md"
other_commit "$D/ledger/SCHEMA.md" theirs
before=$(remote_head)
set +e; sync r push slots --mode 10:35 >/dev/null 2>&1; rc=$?; set -e
eq "conflicting slots push exits 91" 91 "$rc"
eq "remote HEAD unchanged after conflict" "$before" "$(remote_head)"

echo "# push retries: pull --rebase + push up to 5 rounds"
seed
cat >"$T/remote.git/hooks/pre-receive" <<HOOK
#!/bin/sh
n=\$(cat "$T/rejects" 2>/dev/null || echo 0); n=\$((n + 1)); echo "\$n" >"$T/rejects"
[ "\$n" -gt "\$(cat "$T/reject-until")" ]
HOOK
chmod +x "$T/remote.git/hooks/pre-receive"
echo 2 >"$T/reject-until"; rm -f "$T/rejects"
sync t pull qa --pr 2464 2>/dev/null
echo '{"retry":1}' >"$T/home-t/mivo-qa/reports/merged/PR-2464.json"
sync t push qa --pr 2464 2>/dev/null
eq "third round lands after two rejections" 3 "$(cat "$T/rejects")"
eq "retried push content" '{"retry":1}' "$(remote_cat "$D/reports/merged/PR-2464.json")"
echo 99 >"$T/reject-until"; rm -f "$T/rejects"
sync t pull qa --pr 2464 2>/dev/null
echo '{"retry":2}' >"$T/home-t/mivo-qa/reports/merged/PR-2464.json"
set +e; sync t push qa --pr 2464 >/dev/null 2>&1; rc=$?; set -e
eq "always rejected push exits 92" 92 "$rc"
eq "exactly 5 push rounds" 5 "$(cat "$T/rejects")"

echo "state-sync.test.sh: $PASS passed, $FAIL failed"
(( FAIL == 0 ))
