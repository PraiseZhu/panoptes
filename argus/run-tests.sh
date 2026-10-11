#!/usr/bin/env bash
# npm test: every test run gets its own TMPDIR (/tmp/argus-testrun-XXXXXX); all mkdtemp(tmpdir(), 'argus-…') dirs the
# tests create land inside it and are removed on exit, pass or fail (trap). Leftovers of killed runs: scripts/sweep-tmp.sh.
set -uo pipefail
RUN_TMP="$(mktemp -d "${TMPDIR:-/tmp}/argus-testrun-XXXXXX")"
trap 'rm -rf --one-file-system "$RUN_TMP"' EXIT
cd "$(dirname "$0")"
if (( $# )); then set -- "$@"; else set -- tests/*.test.mjs; fi
TMPDIR="$RUN_TMP" node --disable-warning=ExperimentalWarning --test "$@"
