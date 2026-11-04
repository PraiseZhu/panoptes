# shellcheck shell=bash
# Minimal copy of grokbot-box restore.d/lib.sh verify helpers (pass/fail/warn, RC) for test_gh_wrapper_60_gh_auth.py.
set -u
pass() { echo "  PASS $*"; }
fail() { echo "  FAIL $*"; RC=1; }
warn() { echo "  NEED $*"; [ "${RC:-0}" = 0 ] && RC=2; }
RC=0
