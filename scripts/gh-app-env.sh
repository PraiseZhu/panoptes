#!/usr/bin/env bash
# G4 (11-05, acceptance 1105 §1: catch-up / nightly ran with GH_TOKEN=GITHUB_TOKEN=PAT): sourced by run-nightly.sh after the secrets file.
# gh_app_env PANOPTES_DIR PI_STATE
#   both $PI_STATE/gh-app-enabled and gh-app-nightly-enabled exist (the box today) -> App tokens ONLY:
#     GH_TOKEN = PRAISE_QA_GH_TOKEN = product installation (xindong/mivo-canvas-plugin), PRAISE_QA_GH_REPOS = that repo,
#     PRAISE_QA_GH_OWN_TOKEN = PraiseZhu/* installation (argus/src/github.mjs ghEnvFor picks it per target repo);
#     GITHUB_TOKEN and every other variable holding a PAT (ghp_ / github_pat_ / gho_ / ghu_) are unset, so no child (node argus
#     catch-up / nightly, its gh / git / test processes) inherits one. A mint failure returns 1 (the caller exits): never a PAT fallback.
#   either flag missing (the documented rollback, ACCEPTANCE §4) -> GH_TOKEN = GITHUB_TOKEN (PAT), exactly as before.
# Tokens are only ever assigned, never echoed. ghtoken: $PANOPTES_GHTOKEN (tests) or $PANOPTES_DIR/pi/bin/ghtoken.py.
gh_app_env() {
  local dir="$1" st="$2" tool v tok own
  tool="${PANOPTES_GHTOKEN:-$dir/pi/bin/ghtoken.py}"
  unset PRAISE_QA_GH_TOKEN PRAISE_QA_GH_REPOS PRAISE_QA_GH_OWN_TOKEN
  if [[ ! -f "$st/gh-app-enabled" || ! -f "$st/gh-app-nightly-enabled" ]]; then
    export GH_TOKEN="${GITHUB_TOKEN:-}"; return 0
  fi
  tok="$(python3 "$tool" token xindong/mivo-canvas-plugin)" && [[ "$tok" == ghs_* ]] || { echo "gh app token unavailable for xindong/mivo-canvas-plugin: not running on the PAT (rm \$PI_STATE/gh-app-nightly-enabled to roll back)" >&2; return 1; }
  own="$(python3 "$tool" token PraiseZhu/pr-qa-agent-deploy)" && [[ "$own" == ghs_* ]] || { echo "gh app token unavailable for PraiseZhu/*: not running on the PAT (rm \$PI_STATE/gh-app-nightly-enabled to roll back)" >&2; return 1; }
  for v in $(compgen -e); do
    case "${!v}" in ghp_*|github_pat_*|gho_*|ghu_*) unset "$v" ;; esac
  done
  unset GITHUB_TOKEN GH_ENTERPRISE_TOKEN GITHUB_ENTERPRISE_TOKEN
  export GH_TOKEN="$tok" PRAISE_QA_GH_TOKEN="$tok" PRAISE_QA_GH_REPOS="xindong/mivo-canvas-plugin" PRAISE_QA_GH_OWN_TOKEN="$own"
}
