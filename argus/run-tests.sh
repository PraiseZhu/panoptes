#!/usr/bin/env bash
# npm test: every test run gets its own TMPDIR (/tmp/argus-testrun-XXXXXX); all mkdtemp(tmpdir(), 'argus-…') dirs the
# tests create land inside it and are removed on exit, pass or fail (trap). Leftovers of killed runs: scripts/sweep-tmp.sh.
set -uo pipefail
RUN_TMP="$(mktemp -d "${TMPDIR:-/tmp}/argus-testrun-XXXXXX")"
trap 'rm -rf -- "$RUN_TMP"' EXIT
cd "$(dirname "$0")"
if (( $# )); then set -- "$@"; else set -- tests/*.test.mjs; fi
# ARGUS_TEST_TIMEOUT_MS bounds each Node test; ARGUS_TEST_SUITE_TIMEOUT_MS bounds the
# complete run. The supervisor kills the process group before removing scratch data.
TMPDIR="$RUN_TMP" node src/exec.mjs "$@"
