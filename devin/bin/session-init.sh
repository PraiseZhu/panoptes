#!/bin/bash
# session-init.sh — prepare a Devin Linux session for one Mivo QA / job tier (PraiseZhu/panoptes#311).
#
#   session-init.sh qa --pr N [options]   single-PR QA session (pairs with state-sync.sh pull qa)
#   session-init.sh slots [options]       scheduled-job session: full data pull + devin/conf/pins.json check
#   session-init.sh ops|fix [options]     Bot 1 / Bot 2 dialog session: no data pull, no mirror, no pins
#   session-init.sh --cleanup             delete the two secret files and exit 0
# options: --repo-url URL  --branch B  --work-dir DIR (passed to state-sync.sh)
#          --qa-home DIR (default $QA_HOME, else /workspace/mivo-qa)
#
# Steps, in order. Any failure: the secret files are removed and the script exits 91; no secret value is ever printed.
#   1 token    bash ~/.praisedevin/gh-app-token.sh --setup                       (SESSION_INIT_SKIP_TOKEN=1 skips: tests)
#   2 secrets  $QA_SECRETS_FILE (default /home/box/.config/mivo-qa/secrets.env): QA_LLM_BASE_URL, QA_LLM_API_KEY, optional
#              ARGUS_TYPESAFE_API_KEY / MIVO_FEISHU_APP_ID / MIVO_FEISHU_APP_SECRET, GITHUB_TOKEN=ghs_devin_no_pat (placeholder:
#              GitHub goes through the gh wrapper's App tokens); $PRAISE_QA_KEY (default /home/box/.secrets/praise-qa/app.pem)
#              decoded from PRAISE_QA_APP_KEY_B64. Both mode 600.
#   3 swap     SwapFree < 4G ($SESSION_INIT_MEMINFO, default /proc/meminfo) -> 8G swap file $SESSION_INIT_SWAPFILE (/swapfile)
#   4 ff       fast-forward the panoptes checkout (main) to the newest origin/main commit whose CI run succeeded
#              (SESSION_INIT_SKIP_FF=1 skips: tests)
#   5 links    gh (via pi/restore/gh-wrapper/install.sh), gh-praise-qa, git-credential-praise-qa, pi-dispatch in
#              $SESSION_INIT_BIN_DIR (default /usr/local/bin); $SESSION_INIT_WORKSPACE (default /workspace)/panoptes and
#              /mivo-qa pointing at this checkout and the QA home (the box paths panoptes hard-codes)
#   6 pull     devin/bin/state-sync.sh pull qa --pr N (qa) | pull slots (slots)
#   7 mirror   bare clone of $SESSION_INIT_MIRROR_URL (default xindong/mivo-canvas-plugin) at
#              $QA_HOME/argus-root/cache/mirrors/xindong__mivo-canvas-plugin.git, once and before any QA starts (qa, slots)
#   8 pins     slots only: the 7 outbound/ledger scripts in $QA_HOME match $SESSION_INIT_PINS (default devin/conf/pins.json)
#   9 check    PANOPTES_CATCHUP_TIMER=0 $SESSION_INIT_SELFCHECK (default scripts/bootstrap-box.sh --check)
# Last stdout line: one JSON object {ok, tier, platform, pr, qa_home, panoptes_sha, steps, failed_step}.
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
PANOPTES_DIR=$(cd "$HERE/../.." && pwd)
SECRETS="${QA_SECRETS_FILE:-/home/box/.config/mivo-qa/secrets.env}"
APP_KEY="${PRAISE_QA_KEY:-/home/box/.secrets/praise-qa/app.pem}"
BIN_DIR="${SESSION_INIT_BIN_DIR:-/usr/local/bin}"
WORKSPACE="${SESSION_INIT_WORKSPACE:-/workspace}"
PINS="${SESSION_INIT_PINS:-$PANOPTES_DIR/devin/conf/pins.json}"
MIRROR_URL="${SESSION_INIT_MIRROR_URL:-https://github.com/xindong/mivo-canvas-plugin.git}"
SELFCHECK="${SESSION_INIT_SELFCHECK:-$PANOPTES_DIR/scripts/bootstrap-box.sh --check}"
SYNC="$PANOPTES_DIR/devin/bin/state-sync.sh"
SWAP_MIN_KB=$((4 * 1024 * 1024)) SWAP_SIZE=8G

usage() { echo "usage: session-init.sh qa --pr N | slots | ops | fix [--repo-url URL] [--branch B] [--work-dir DIR] [--qa-home DIR]; session-init.sh --cleanup" >&2; exit 2; }
cleanup() { rm -f -- "$SECRETS" "$APP_KEY"; }

TIER="" PR="" QA_DIR="${QA_HOME:-/workspace/mivo-qa}" SYNC_OPTS=()
while (($#)); do
  case "$1" in
    qa|slots|ops|fix) [[ -z "$TIER" ]] || usage; TIER=$1 ;;
    --cleanup) cleanup; exit 0 ;;
    --pr|--repo-url|--branch|--work-dir|--qa-home) (($# >= 2)) || usage
      case "$1" in --pr) PR=$2 ;; --qa-home) QA_DIR=$2 ;; *) SYNC_OPTS+=("$1" "$2") ;; esac; shift ;;
    *) usage ;;
  esac
  shift
done
[[ -n "$TIER" ]] || usage
if [[ "$TIER" == qa ]]; then [[ "$PR" =~ ^[1-9][0-9]*$ ]] || usage; else [[ -z "$PR" ]] || usage; fi
QA_DIR=${QA_DIR%/}
STEPS=()

emit() { # emit OK FAILED_STEP
  python3 - "$1" "$TIER" "$(uname -s | tr '[:upper:]' '[:lower:]')" "$PR" "$QA_DIR" \
    "$(git -C "$PANOPTES_DIR" rev-parse HEAD 2>/dev/null)" "$2" "${STEPS[@]}" <<'PY'
import json, sys
ok, tier, platform, pr, qa_home, sha, failed, *steps = sys.argv[1:]
print(json.dumps({"ok": ok == "true", "tier": tier, "platform": platform, "pr": int(pr) if pr else None, "qa_home": qa_home,
                  "panoptes_sha": sha or None, "steps": steps, "failed_step": failed or None}))
PY
}
fail() { echo "session-init: step $1 failed${2:+: $2}" >&2; cleanup; emit false "$1"; exit 91; }
# as_root DIR CMD...: run CMD directly when DIR (or its nearest existing parent) is writable, else via sudo -n
as_root() {
  local d=$1; shift
  while [[ ! -e "$d" ]]; do d=$(dirname "$d"); done
  if [[ -w "$d" ]]; then "$@"; else sudo -n "$@"; fi
}

step_token() {
  [[ "${SESSION_INIT_SKIP_TOKEN:-0}" == 1 ]] && return 0
  bash "$HOME/.praisedevin/gh-app-token.sh" --setup >&2
}

step_secrets() {
  local v tmp
  for v in QA_LLM_BASE_URL QA_LLM_API_KEY PRAISE_QA_APP_KEY_B64; do
    [[ -n "${!v:-}" ]] || { echo "session-init: required variable $v is not set" >&2; return 1; }
  done
  mkdir -p "$(dirname "$SECRETS")" "$(dirname "$APP_KEY")" || return 1
  tmp=$(umask 077 && mktemp "$SECRETS.XXXXXX") || return 1
  # KEY='VALUE' reads back identically via bash `source` and pi/bin/lib.py secret_env_dict() (strips quotes, no unescaping)
  local -a keys=(QA_LLM_BASE_URL QA_LLM_API_KEY ARGUS_TYPESAFE_API_KEY MIVO_FEISHU_APP_ID MIVO_FEISHU_APP_SECRET
    MIVO_SLACK_BOT_TOKEN DEEPSEEK_API_KEY)
  for v in "${keys[@]}"; do
    if [[ "${!v:-}" == *[$'\n\r'\']* ]]; then
      rm -f "$tmp"; echo "session-init: $v contains a newline or single quote" >&2; return 1
    fi
  done
  {
    printf "GITHUB_TOKEN='%s'\n" ghs_devin_no_pat
    for v in "${keys[@]}"; do
      [[ -n "${!v:-}" ]] && printf "%s='%s'\n" "$v" "${!v}"
    done
  } >"$tmp"
  if ! { chmod 600 "$tmp" && mv -f "$tmp" "$SECRETS"; }; then rm -f "$tmp"; return 1; fi
  tmp=$(umask 077 && mktemp "$APP_KEY.XXXXXX") || return 1
  if ! { printf '%s' "$PRAISE_QA_APP_KEY_B64" | base64 -d >"$tmp" 2>/dev/null && [[ -s "$tmp" ]]; }; then
    rm -f "$tmp"; echo "session-init: PRAISE_QA_APP_KEY_B64 is not valid base64" >&2; return 1
  fi
  chmod 600 "$tmp" && mv -f "$tmp" "$APP_KEY"
}

step_swap() {
  local free file="${SESSION_INIT_SWAPFILE:-/swapfile}"
  free=$(awk '/^SwapFree:/ {print $2}' "${SESSION_INIT_MEMINFO:-/proc/meminfo}") || return 1
  [[ "$free" =~ ^[0-9]+$ ]] || { echo "session-init: no SwapFree in meminfo" >&2; return 1; }
  ((free >= SWAP_MIN_KB)) && return 0
  if [[ ! -f "$file" ]]; then
    as_root "$file" fallocate -l "$SWAP_SIZE" "$file" && as_root "$file" chmod 600 "$file" \
      && as_root "$file" mkswap "$file" >&2 || return 1
  fi
  grep -q "^$file " /proc/swaps 2>/dev/null || as_root "$file" swapon "$file"
}

step_ff() {
  [[ "${SESSION_INIT_SKIP_FF:-0}" == 1 ]] && return 0
  local branch green
  branch=$(git -C "$PANOPTES_DIR" symbolic-ref --short HEAD 2>/dev/null)
  [[ "$branch" == main ]] || { echo "session-init: panoptes checkout is on '$branch', want main" >&2; return 1; }
  git -C "$PANOPTES_DIR" fetch -q origin main || return 1
  green=$(gh api 'repos/PraiseZhu/panoptes/actions/workflows/ci.yml/runs?branch=main&status=success&per_page=1' \
    -q '.workflow_runs[0].head_sha') || return 1
  [[ "$green" =~ ^[0-9a-f]{40}$ ]] || { echo "session-init: no successful CI run on main" >&2; return 1; }
  git -C "$PANOPTES_DIR" merge-base --is-ancestor "$green" HEAD && return 0
  git -C "$PANOPTES_DIR" merge -q --ff-only "$green" >&2
}

link() { # link TARGET PATH: PATH becomes a symlink to TARGET (an equal real directory is accepted as is)
  local target=$1 path=$2
  [[ -e "$path" && "$(readlink -f "$path")" == "$(readlink -f "$target")" ]] && return 0
  if [[ -e "$path" && ! -L "$path" ]]; then
    echo "session-init: $path exists and is not $target" >&2; return 1
  fi
  as_root "$(dirname "$path")" mkdir -p "$(dirname "$path")" && as_root "$(dirname "$path")" ln -sfn "$target" "$path"
}

step_links() {
  local rc=0 name
  GH_RESTORE_LINK="$BIN_DIR/gh" GH_RESTORE_WRAPPER="$PANOPTES_DIR/pi/bin/gh_wrapper.py" PI_STATE="$QA_DIR/pi" \
    bash "$PANOPTES_DIR/pi/restore/gh-wrapper/install.sh" >&2 || rc=$?
  # 3 = an App switch under $QA_DIR/pi is missing (state-sync has not pulled it yet / a human's call): reported, not fatal
  ((rc == 0 || rc == 3)) || return 1
  for name in gh-praise-qa git-credential-praise-qa pi-dispatch; do link "$PANOPTES_DIR/pi/bin/$name" "$BIN_DIR/$name" || return 1; done
  link "$PANOPTES_DIR" "$WORKSPACE/panoptes" && mkdir -p "$QA_DIR" && link "$QA_DIR" "$WORKSPACE/mivo-qa"
}

step_pull() {
  if [[ "$TIER" == qa ]]; then
    "$SYNC" pull qa --pr "$PR" --qa-home "$QA_DIR" "${SYNC_OPTS[@]}" >&2
  else
    "$SYNC" pull slots --qa-home "$QA_DIR" "${SYNC_OPTS[@]}" >&2
  fi
}

step_mirror() {
  local m="$QA_DIR/argus-root/cache/mirrors/xindong__mivo-canvas-plugin.git" tmp
  git --git-dir="$m" rev-parse -q --verify HEAD >/dev/null 2>&1 && return 0
  # a directory without HEAD is a half-finished clone (the first-run race): start over
  rm -rf -- "$m" && mkdir -p "$(dirname "$m")" || return 1
  tmp="$m.init-$$"
  if ! { git clone -q --bare "$MIRROR_URL" "$tmp" >&2 && mv "$tmp" "$m"; }; then rm -rf -- "$tmp"; return 1; fi
}

step_pins() {
  python3 - "$PINS" "$QA_DIR" <<'PY'
import hashlib, json, os, sys
pins, root = sys.argv[1], sys.argv[2]
bad = 0
for rel, want in json.load(open(pins))["sha256"].items():
    path = os.path.join(root, rel)
    if not os.path.isfile(path):
        print(f"session-init: pins: missing {rel}", file=sys.stderr); bad += 1; continue
    got = hashlib.sha256(open(path, "rb").read()).hexdigest()
    if got != want:
        print(f"session-init: pins: sha256 mismatch {rel}: want {want} got {got}", file=sys.stderr); bad += 1
sys.exit(1 if bad else 0)
PY
}

step_check() {
  # shellcheck disable=SC2086 # SELFCHECK is a command line
  PANOPTES_CATCHUP_TIMER=0 QA_HOME="$QA_DIR" $SELFCHECK >&2
}

run() { "step_$1" || fail "$1"; STEPS+=("$1"); }
run token; run secrets; run swap; run ff; run links
if [[ "$TIER" == qa || "$TIER" == slots ]]; then run pull; run mirror; fi
[[ "$TIER" == slots ]] && run pins
run check
emit true ""
