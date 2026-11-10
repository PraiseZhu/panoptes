#!/usr/bin/env bash
# Check the box gh wrapper is live (11-10). Prints OK/FAIL per check, never a token.
#   wrapper     $LINK (/usr/local/bin/gh) is a symlink to $WRAPPER (gh_wrapper.py)
#   identity    `$GH api user --jq .login` = praise-qa[bot]
#   credential  `git credential fill` for https://github.com/$REPO answers username=x-access-token (the App helper)
# Exit: 0 all pass, 1 any check failed. Overrides: GH_RESTORE_LINK, GH_RESTORE_WRAPPER, GH_RESTORE_GH (gh to call, default $LINK),
# GH_RESTORE_CRED_REPO (default PraiseZhu/panoptes), GIT_CONFIG_GLOBAL.
set -uo pipefail
LINK=${GH_RESTORE_LINK:-/usr/local/bin/gh}
WRAPPER=${GH_RESTORE_WRAPPER:-/workspace/panoptes/pi/bin/gh_wrapper.py}
GH=${GH_RESTORE_GH:-$LINK}
REPO=${GH_RESTORE_CRED_REPO:-PraiseZhu/panoptes}
rc=0
ok() { echo "OK $1"; }
bad() { echo "FAIL $1"; rc=1; }

if [ -L "$LINK" ] && [ "$(readlink -f "$LINK")" = "$(readlink -f "$WRAPPER")" ]; then ok "wrapper: $LINK -> $(readlink -f "$LINK")"
else bad "wrapper: $LINK is not a symlink to $WRAPPER (now: $(readlink -f "$LINK" 2>/dev/null || echo missing))"; fi

login=$("$GH" api user --jq .login 2>/dev/null)
[ "$login" = "praise-qa[bot]" ] && ok "identity: gh api user = $login" || bad "identity: gh api user = '${login:-<error>}', want praise-qa[bot]"

user=$(printf 'protocol=https\nhost=github.com\npath=%s.git\n\n' "$REPO" | GIT_TERMINAL_PROMPT=0 git credential fill 2>/dev/null | sed -n 's/^username=//p')
[ "$user" = "x-access-token" ] && ok "credential: username=$user for $REPO" || bad "credential: username='${user:-<none>}' for $REPO, want x-access-token"
exit $rc
