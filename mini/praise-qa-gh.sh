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
# Any failure (no key, network, API) -> one masked line on stderr and the gh login is used for that call.

PQA_APP_ID="${PRAISE_QA_APP_ID:-5055491}"
PQA_KEY="${PRAISE_QA_KEY:-/Users/praise/.config/praise-qa/app.pem}"
PQA_FLAG="${PRAISE_QA_FLAG:-$HOME/.config/praise-qa/gh-app-enabled}"
PQA_CACHE="${PRAISE_QA_CACHE:-$HOME/.cache/praise-qa}"
PQA_API="${PRAISE_QA_API:-https://api.github.com}"
PQA_CURL="${PRAISE_QA_CURL:-curl}"
PQA_GH="${GH_BIN:-gh}"

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
pqa_env_for() {   # export GH_TOKEN for the repo when the flag is on and a token mints; else leave the gh login alone
  local t
  pqa_on || return 0
  [ -n "${1:-}" ] || return 0
  pqa_installation "$1" >/dev/null || return 0
  if t=$(pqa_token "$1"); then export GH_TOKEN="$t"; unset GITHUB_TOKEN; return 0; fi
  echo "praise-qa: using the gh login for $1" >&2
}

if [ "${1:-}" = "--lib" ]; then return 0 2>/dev/null || exit 0; fi
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  pqa_env_for "$(pqa_target_repo "$@")"
  exec "$PQA_GH" "$@"
fi
