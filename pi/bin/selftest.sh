#!/bin/bash
# Pi line self-test (run by the git pre-commit hook; run it yourself after editing anything under bin/):
# every live .py compiles, every .sh parses, every test/test_*.py passes. No network, no model, no writes outside /tmp.
set -u; B=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd); rc=0; tmp=$(mktemp -d)
# git hooks export GIT_DIR / GIT_INDEX_FILE / GIT_WORK_TREE: tests that build throw-away repos would otherwise act on THIS repo
# (10-23: a hook run re-initialised ~/.pi-fallback.git as bare). Clear every GIT_* before anything runs.
for v in $(env | sed -n 's/^\(GIT_[A-Za-z_]*\)=.*/\1/p'); do unset "$v"; done
for f in "$B"/bin/*.py; do PYTHONPYCACHEPREFIX=$tmp python3 -m py_compile "$f" 2>&1 || { echo "FAIL compile $(basename "$f")"; rc=1; }; done
for f in "$B"/bin/*.sh "$B"/bin/run.sh; do bash -n "$f" || { echo "FAIL parse $(basename "$f")"; rc=1; }; done
# 10-24: tests run without our tokens. A Claude session's environment carries real ones (injected); a test that compares a token
# then printed the real Slack token in its failure message, and results differed between cron and a session. Allowlist, as in
# lib.model_env.
CLEAN=(env -i "PATH=$PATH" "HOME=$HOME" "USER=${USER:-$(id -un)}" "LOGNAME=${LOGNAME:-$(id -un)}" "LANG=C.UTF-8" "LC_ALL=C.UTF-8"
       "TZ=Asia/Shanghai" "TMPDIR=${TMPDIR:-/tmp}" "PI_WORK=$tmp/pi-work" "PI_WORK_ROOT=$tmp/pi-work"
       "PI_STATE=$tmp/pi-state")   # 10-27: never the real state dir (#68: tests appended 44 fake relays to the real muted-*.md)   # 10-24: never the real work root
for t in "$B"/test/test_*.py; do
  out=$(cd "$B/bin" && "${CLEAN[@]}" PYTHONPYCACHEPREFIX=$tmp timeout 300 python3 "$t" 2>&1) || { echo "FAIL $(basename "$t")"; echo "$out" | tail -"${PI_SELFTEST_TAIL:-5}"; rc=1; }
done
rm -rf "$tmp"; [ $rc = 0 ] && echo "selftest ok"; exit $rc
