#!/usr/bin/env bash
# praise-qa-gh.sh — run gh as the GitHub App "Praise QA" (praise-qa[bot]) on the Mac mini, with the mini's own gh login as fallback.
# macOS /bin/bash 3.2, BSD tools, openssl + curl only (no jq, no python).
#
#   praise-qa-gh.sh <gh args...>        exec gh with GH_TOKEN = an installation token for the target repo (-R/--repo or api repos/O/R)
#   . praise-qa-gh.sh --lib             source it: pqa_token <owner/repo> prints a token (or fails), pqa_on tells whether the flag is on
#
# Rollback flag: $PRAISE_QA_FLAG (default ~/.config/praise-qa/gh-app-enabled). Removed -> plain gh (the mini's own login), exactly as
# before. Key: $PRAISE_QA_KEY (default /Users/praise/.config/praise-qa/app.pem), opened only by openssl at run time, never printed.
# Installations: xindong/mivo-canvas-plugin -> 178486896, PraiseZhu/* -> 164262283; other repos -> the gh login.
# Token cache: ~/.cache/praise-qa/inst-<id> (dir 0700, file 0600: "<expiry epoch> <token>"), re-minted with < 10 min left.
# 11-05 R2 (same rule as panoptes #246): while the flag is on, a mint failure for an installed repo (no key, JWT, network, API) is an
# ERROR, never the mini's own gh login: one masked stderr line, the alert $PRAISE_QA_ALERT_DIR/gh_app_mint.json (default
# ~/.config/praise-qa/alerts; removed by the next successful mint) and exit 5 -- gh is not run. Repos with no installation and the
# flag-off rollback still use the gh login, unchanged.
#   praise-qa-gh.sh auth git-credential get   git credential helper: App token for an installed repo (needs credential.useHttpPath);
#                                             mint failure -> exit 5 and NO password; store/erase -> no-op; other repos/flag off -> gh.

PQA_APP_ID="${PRAISE_QA_APP_ID:-5055491}"
PQA_KEY="${PRAISE_QA_KEY:-/Users/praise/.config/praise-qa/app.pem}"
PQA_FLAG="${PRAISE_QA_FLAG:-$HOME/.config/praise-qa/gh-app-enabled}"
PQA_CACHE="${PRAISE_QA_CACHE:-$HOME/.cache/praise-qa}"
PQA_API="${PRAISE_QA_API:-https://api.github.com}"
PQA_CURL="${PRAISE_QA_CURL:-curl}"
PQA_GH="${GH_BIN:-gh}"
PQA_ALERT_DIR="${PRAISE_QA_ALERT_DIR:-$HOME/.config/praise-qa/alerts}"
PQA_EXIT_MINT=5

pqa_on() { [ -f "$PQA_FLAG" ]; }
pqa_mask() { sed -E 's/(gh[pousr]|github_pat)_[A-Za-z0-9_]+/\1_***/g; s/eyJ[A-Za-z0-9_.-]{20,}/eyJ***/g'; }
pqa_installation() {
  case "$1" in
    xindong/mivo-canvas-plugin) echo 178486896 ;;
    PraiseZhu/*/*) return 1 ;;
    PraiseZhu/?*) echo 164262283 ;;
    *) return 1 ;;
  esac
}
pqa_b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }
pqa_jwt() {
  local now h p sig
  now=$(date +%s)
  h=$(printf '{"alg":"RS256","typ":"JWT"}' | pqa_b64url)
  p=$(printf '{"iat":%s,"exp":%s,"iss":"%s"}' "$((now - 60))" "$((now + 540))" "$PQA_APP_ID" | pqa_b64url)
  sig=$(printf '%s.%s' "$h" "$p" | openssl dgst -sha256 -sign "$PQA_KEY" 2>/dev/null | pqa_b64url) || return 1
  [ -n "$sig" ] || return 1
  printf '%s.%s.%s' "$h" "$p" "$sig"
}
pqa_target_repo() {
  local prev="" a
  for a in "$@"; do
    case "$prev" in -R|--repo) echo "$a"; return 0 ;; esac
    case "$a" in --repo=*) echo "${a#--repo=}"; return 0 ;; esac
    prev="$a"
  done
  if [ "${1:-}" = api ]; then
    for a in "$@"; do
      case "$a" in repos/*/*|/repos/*/*) a="${a#/}"; a="${a#repos/}"; echo "$a" | cut -d/ -f1-2 | cut -d'?' -f1; return 0 ;; esac
    done
  fi
  return 1
}
pqa_token() {   # $1 owner/repo -> token on stdout
  local id f now exp="" tok="" jwt resp
  id=$(pqa_installation "$1") || { echo "praise-qa: no installation for $1" >&2; return 1; }
  [ -r "$PQA_KEY" ] || { echo "praise-qa: key not readable ($PQA_KEY)" >&2; return 1; }
  ( umask 077; mkdir -p "$PQA_CACHE" ) && chmod 700 "$PQA_CACHE" 2>/dev/null
  f="$PQA_CACHE/inst-$id"; now=$(date +%s)
  if [ -f "$f" ]; then
    read -r exp tok <"$f" 2>/dev/null
    case "$exp" in ''|*[!0-9]*) exp=0 ;; esac
    if [ -n "${tok:-}" ] && [ "$((exp - now))" -gt 600 ]; then printf '%s' "$tok"; return 0; fi
  fi
  jwt=$(pqa_jwt) || { echo "praise-qa: JWT signing failed" >&2; return 1; }
  resp=$("$PQA_CURL" -sS -m 30 -X POST -H "Authorization: Bearer $jwt" -H "Accept: application/vnd.github+json" \
         -H "X-GitHub-Api-Version: 2022-11-28" "$PQA_API/app/installations/$id/access_tokens" 2>&1) \
    || { echo "praise-qa: mint failed: $(printf '%s' "$resp" | pqa_mask | tr '\n' ' ' | cut -c1-200)" >&2; return 1; }
  tok=$(printf '%s' "$resp" | tr -d '\n' | sed -n 's/.*"token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
  [ -n "$tok" ] || { echo "praise-qa: mint failed: $(printf '%s' "$resp" | pqa_mask | tr '\n' ' ' | cut -c1-200)" >&2; return 1; }
  ( umask 077; printf '%s %s\n' "$((now + 3600))" "$tok" >"$f.tmp.$$" && mv -f "$f.tmp.$$" "$f" )
  printf '%s' "$tok"
}
pqa_alert() {   # pqa_alert "<masked reason>" <repo> | pqa_alert   (no args = recovered: remove the alert)
  local f="$PQA_ALERT_DIR/gh_app_mint.json" now prev=0 since
  if [ -z "${1:-}" ]; then rm -f "$f" 2>/dev/null; return 0; fi
  ( umask 077; mkdir -p "$PQA_ALERT_DIR" ) 2>/dev/null || { echo "praise-qa: could not write $f" >&2; return 0; }
  now=$(date +%Y-%m-%dT%H:%M:%S%z); since="$now"
  if [ -f "$f" ]; then
    prev=$(sed -n 's/.*"consecutive_failures": *\([0-9][0-9]*\).*/\1/p' "$f" | head -1); case "$prev" in ''|*[!0-9]*) prev=0 ;; esac
    since=$(sed -n 's/.*"since": *"\([^"]*\)".*/\1/p' "$f" | head -1); [ -n "$since" ] || since="$now"
  fi
  printf '{"check": "gh_app_mint", "host": "mac-mini", "consecutive_failures": %s, "since": "%s", "last": "%s", "repo": "%s", "error": "%s", "owner": "Pi 线运维"}\n' \
    "$((prev + 1))" "$since" "$now" "$2" "$(printf '%s' "$1" | pqa_mask | tr -d '"\\' | tr '\n' ' ' | cut -c1-200)" >"$f.tmp.$$" && mv -f "$f.tmp.$$" "$f"
}
pqa_env_for() {   # flag on + installed repo: export GH_TOKEN or FAIL (rc 5, alert); else leave the gh login alone (rc 0)
  local t err
  pqa_on || return 0
  [ -n "${1:-}" ] || return 0
  pqa_installation "$1" >/dev/null || return 0
  if t=$(pqa_token "$1" 2>"$PQA_CACHE.err.$$"); then rm -f "$PQA_CACHE.err.$$"; pqa_alert; export GH_TOKEN="$t"; unset GITHUB_TOKEN; return 0; fi
  err=$(cat "$PQA_CACHE.err.$$" 2>/dev/null); rm -f "$PQA_CACHE.err.$$"
  [ -n "$err" ] && printf '%s\n' "$err" | pqa_mask >&2
  echo "praise-qa: App token unavailable for $1: refused, not falling back to the gh login (rm $PQA_FLAG to roll back)" >&2
  pqa_alert "${err:-mint failed}" "$1"
  return "$PQA_EXIT_MINT"
}
pqa_credential() {   # git credential helper protocol on stdin; $1 = op
  local req repo p t
  case "${1:-}" in store|erase) cat >/dev/null; return 0 ;; get) ;; *) return 4 ;; esac
  req=$(cat)
  p=$(printf '%s\n' "$req" | sed -n 's/^path=//p' | head -1); p="${p%.git}"; p="${p%/}"
  printf '%s\n' "$req" | grep -qx 'protocol=https' && printf '%s\n' "$req" | grep -qx 'host=github.com' && repo=$(echo "$p" | cut -d/ -f1-2)
  if [ -n "${repo:-}" ] && pqa_on && pqa_installation "$repo" >/dev/null; then
    pqa_env_for "$repo" || return "$PQA_EXIT_MINT"   # no password: git gets nothing from this helper
    printf 'protocol=https\nhost=github.com\nusername=x-access-token\npassword=%s\n' "$GH_TOKEN"; return 0
  fi
  printf '%s\n' "$req" | "$PQA_GH" auth git-credential get   # not ours (flag off / other repo): the gh login, as before
}

if [ "${1:-}" = "--lib" ]; then return 0 2>/dev/null || exit 0; fi
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  if [ "${1:-}" = auth ] && [ "${2:-}" = git-credential ]; then pqa_credential "${3:-}"; exit $?; fi
  pqa_env_for "$(pqa_target_repo "$@")" || exit "$PQA_EXIT_MINT"
  exec "$PQA_GH" "$@"
fi
