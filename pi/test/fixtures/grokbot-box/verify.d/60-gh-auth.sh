#!/bin/bash
. "$(dirname "$0")/../restore.d/lib.sh" "$@"
# PAT check: call the real gh directly, not the praise-qa gh wrapper (panoptes #214 caller allowlist).
GH="${PRAISE_QA_REAL_GH:-/usr/local/bin/gh}"
if [ ! -x "$GH" ]; then warn "real gh not found at $GH (set PRAISE_QA_REAL_GH)"
elif "$GH" auth status >/dev/null 2>&1; then pass "gh auth ok ($("$GH" api user -q .login 2>/dev/null || echo '?'))"; else warn "gh not logged in: run gh auth login"; fi
exit $RC
