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
# 11-05 (PM): the mint failure must be visible, not only an exit code: it is written to the stdcheck alert dir
# $PI_STATE/out/stdcheck/alerts/gh_app_token.json (Jasper's 09:00 brief reads every alerts/*.json, stdcheck.brief_facts) and removed
# again by the next run that mints. stdcheck itself only rewrites / removes the alerts of its own checks, so it never touches this one.
gh_app_alert() {   # gh_app_alert PI_STATE "reason" | gh_app_alert PI_STATE  (no reason = recovered)
  local f="$1/out/stdcheck/alerts/gh_app_token.json"
  if [[ -z "${2:-}" ]]; then rm -f "$f" 2>/dev/null || true; return 0; fi
  mkdir -p "${f%/*}" 2>/dev/null && ALERT_FILE="$f" WHY="$2" python3 - <<'PY' || echo "could not write $f" >&2
import datetime, json, os
f = os.environ["ALERT_FILE"]; now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec="seconds")
try: prev = json.load(open(f))
except (OSError, ValueError): prev = {}
doc = {"check": "gh_app_token", "consecutive_failures": int(prev.get("consecutive_failures") or 0) + 1, "since": prev.get("since") or now,
       "last": now, "error": os.environ["WHY"], "owner": "Pi 线运维", "for_brief": now[:10],
       "breaches": {"run-nightly": "Praise QA App token could not be minted: catch-up / nightly did not run (no PAT fallback by design)"}}
tmp = f + ".tmp"
with open(tmp, "w") as fh: json.dump(doc, fh, ensure_ascii=False, indent=2)
os.replace(tmp, f)
PY
}
gh_app_env() {
  local dir="$1" st="$2" tool v tok own
  tool="${PANOPTES_GHTOKEN:-$dir/pi/bin/ghtoken.py}"
  unset PRAISE_QA_GH_TOKEN PRAISE_QA_GH_REPOS PRAISE_QA_GH_OWN_TOKEN
  if [[ ! -f "$st/gh-app-enabled" || ! -f "$st/gh-app-nightly-enabled" ]]; then
    gh_app_alert "$st"; export GH_TOKEN="${GITHUB_TOKEN:-}"; return 0
  fi
  tok="$(python3 "$tool" token xindong/mivo-canvas-plugin)" && [[ "$tok" == ghs_* ]] || { echo "gh app token unavailable for xindong/mivo-canvas-plugin: not running on the PAT (rm \$PI_STATE/gh-app-nightly-enabled to roll back)" >&2; gh_app_alert "$st" "ghtoken.py token xindong/mivo-canvas-plugin failed"; return 1; }
  own="$(python3 "$tool" token PraiseZhu/pr-qa-agent-deploy)" && [[ "$own" == ghs_* ]] || { echo "gh app token unavailable for PraiseZhu/*: not running on the PAT (rm \$PI_STATE/gh-app-nightly-enabled to roll back)" >&2; gh_app_alert "$st" "ghtoken.py token PraiseZhu/pr-qa-agent-deploy failed"; return 1; }
  for v in $(compgen -e); do
    case "${!v}" in ghp_*|github_pat_*|gho_*|ghu_*) unset "$v" ;; esac
  done
  unset GITHUB_TOKEN GH_ENTERPRISE_TOKEN GITHUB_ENTERPRISE_TOKEN
  gh_app_alert "$st"
  export GH_TOKEN="$tok" PRAISE_QA_GH_TOKEN="$tok" PRAISE_QA_GH_REPOS="xindong/mivo-canvas-plugin" PRAISE_QA_GH_OWN_TOKEN="$own"
}
