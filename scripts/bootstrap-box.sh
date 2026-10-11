#!/usr/bin/env bash
# panoptes — (re)install everything a QA run needs on a fresh box. Idempotent: safe to re-run; only missing
# pieces are installed. Called automatically by run-nightly.sh when its self-check finds something missing.
#
#   scripts/bootstrap-box.sh            check, then install whatever is missing, then check again
#   scripts/bootstrap-box.sh --check    only check; prints one "missing: <item> — <why>" line per gap; exit 0 = all ok
#
# What lives where (persistent locations under /workspace survive a box replacement; system packages do not):
#   Node 24 via nvm      $PANOPTES_TOOLS/nvm            (NVM_DIR; default /workspace/_tools/nvm)
#   argus npm deps       $PANOPTES_DIR/argus/node_modules (npm ci)
#   Playwright browsers  $QA_HOME/cache/ms-playwright   (PLAYWRIGHT_BROWSERS_PATH)
#   ming-qa Python venv  $QA_HOME/.venv                 (stdlib only)
#   system (apt, sudo)   chromium, bubblewrap, jq, git, util-linux (flock), python3-venv, Playwright's chromium deps
#   chromium launcher    /usr/local/bin/chromium  ←  scripts/chromium-linux.sh (see README "Why a wrapper")
#   catch-up timer       /etc/cron.d/panoptes-catch-up ← scripts/catch-up.cron (every 2 h) + a running cron daemon
#                        (the box has no systemd; PANOPTES_CATCHUP_TIMER=0 skips the timer)
#
# Exit codes: 0 everything present, 2 something is still missing after installing (details on stderr / stdout).
# Never prints secrets; needs network and passwordless sudo for the system part.
set -uo pipefail

PANOPTES_DIR="${PANOPTES_DIR:-$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)}"
QA_HOME="${QA_HOME:-/workspace/mivo-qa}"
PANOPTES_TOOLS="${PANOPTES_TOOLS:-/workspace/_tools}"
export NVM_DIR="${PANOPTES_NVM_DIR:-$PANOPTES_TOOLS/nvm}"
NVM_VERSION="v0.40.3"
NODE_MAJOR=24
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$QA_HOME/cache/ms-playwright}"
VENV="${ARGUS_VENV:-$QA_HOME/.venv}"
WRAPPER_SRC="$PANOPTES_DIR/scripts/chromium-linux.sh"
WRAPPER=/usr/local/bin/chromium
APT_PKGS=(chromium bubblewrap jq git util-linux python3 python3-venv ca-certificates curl cron)
CRON_SRC="$PANOPTES_DIR/scripts/catch-up.cron"
CRON_FILE=/etc/cron.d/panoptes-catch-up
WANT_TIMER="${PANOPTES_CATCHUP_TIMER:-1}"
render_cron() {
  sed -e "s#@USER@#$(id -un)#g" -e "s#@QA_HOME@#$QA_HOME#g" -e "s#@PANOPTES_DIR@#$PANOPTES_DIR#g" "$CRON_SRC"
}
CHECK_ONLY=""
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

log() { echo "[bootstrap] $*" >&2; }
unset NPM_CONFIG_PREFIX npm_config_prefix

load_node() {
  [[ -s "$NVM_DIR/nvm.sh" ]] || return 1
  # shellcheck disable=SC1091
  . "$NVM_DIR/nvm.sh" --no-use >/dev/null 2>&1 || return 1
  nvm use --silent "$NODE_MAJOR" >/dev/null 2>&1 || return 1
  node -e "process.exit(Number(process.versions.node.split('.')[0]) >= $NODE_MAJOR ? 0 : 1)" 2>/dev/null
}

# Each check prints "missing: <item> — <reason>" and returns 1 when something is missing.
check_all() {
  local bad=0 p
  for p in "${APT_PKGS[@]}"; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'install ok installed' || { echo "missing: apt:$p"; bad=1; }
  done
  command -v bwrap >/dev/null || { echo "missing: bwrap — bubblewrap not on PATH"; bad=1; }
  [[ -x /usr/lib/chromium/chromium ]] || { echo "missing: chromium — /usr/lib/chromium/chromium"; bad=1; }
  cmp -s "$WRAPPER_SRC" "$WRAPPER" || { echo "missing: chromium-wrapper — $WRAPPER differs from scripts/chromium-linux.sh"; bad=1; }
  load_node || { echo "missing: node$NODE_MAJOR — no Node $NODE_MAJOR under $NVM_DIR"; bad=1; }
  if [[ ! -f "$PANOPTES_DIR/argus/node_modules/.package-lock.json" ]] \
     || [[ "$PANOPTES_DIR/argus/package-lock.json" -nt "$PANOPTES_DIR/argus/node_modules/.package-lock.json" ]]; then
    echo "missing: argus-npm — argus/node_modules absent or older than package-lock.json"; bad=1
  fi
  compgen -G "$PLAYWRIGHT_BROWSERS_PATH/chromium_headless_shell-*/INSTALLATION_COMPLETE" >/dev/null \
    && compgen -G "$PLAYWRIGHT_BROWSERS_PATH/chromium-*/INSTALLATION_COMPLETE" >/dev/null \
    || { echo "missing: playwright-browsers — none in $PLAYWRIGHT_BROWSERS_PATH"; bad=1; }
  local shell_bin
  shell_bin=$(compgen -G "$PLAYWRIGHT_BROWSERS_PATH/chromium_headless_shell-*/chrome-*/chrome-headless-shell" | sort | tail -1)
  if [[ -n "$shell_bin" ]] && ldd "$shell_bin" 2>/dev/null | grep -q 'not found'; then
    echo "missing: playwright-deps — system libraries for Playwright's chromium"; bad=1
  fi
  "$VENV/bin/python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null \
    || { echo "missing: venv — $VENV"; bad=1; }
  if [[ "$WANT_TIMER" != 0 ]]; then
    cmp -s <(render_cron) "$CRON_FILE" || { echo "missing: catch-up-timer — $CRON_FILE absent or differs from scripts/catch-up.cron"; bad=1; }
    pgrep -x cron >/dev/null || { echo "missing: cron-daemon — cron is not running"; bad=1; }
  fi
  return $bad
}

if [[ -n "$CHECK_ONLY" ]]; then check_all; exit $(( $? ? 2 : 0 )); fi

# One bootstrap at a time (two runs noticing the same gap would otherwise race apt / npm)
mkdir -p "$PANOPTES_TOOLS" "$QA_HOME/cache"
exec 7>"$PANOPTES_TOOLS/.bootstrap.lock"
flock -w 1800 7 || { log "another bootstrap is still running after 30 min"; exit 2; }

MISSING=$(check_all) && { log "everything present"; exit 0; }
log "missing before install:"; printf '%s\n' "$MISSING" | sed 's/^/[bootstrap]   /' >&2
FAILED=()
SUDO=""; [[ $(id -u) -ne 0 ]] && SUDO="sudo -n"
# exact item match ("chromium" must not match "chromium-wrapper"); "apt" matches every "apt:<pkg>"
has() { grep -Eq "^missing: $1([: ]|\$)" <<<"$MISSING"; }

if has apt || has bwrap || has chromium; then
  log "apt: installing ${APT_PKGS[*]}"
  if $SUDO env DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null \
     && $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${APT_PKGS[@]}" >/dev/null; then :
  else FAILED+=("apt install (needs passwordless sudo + network)"); fi
fi
if has chromium-wrapper || has chromium; then
  if [[ -x /usr/lib/chromium/chromium ]]; then
    $SUDO install -m 755 "$WRAPPER_SRC" "$WRAPPER" || FAILED+=("chromium wrapper install")
  else FAILED+=("chromium wrapper: Debian chromium not installed"); fi
fi
if has "node$NODE_MAJOR"; then
  if [[ ! -s "$NVM_DIR/nvm.sh" ]]; then
    log "nvm $NVM_VERSION → $NVM_DIR"
    rm -rf "$NVM_DIR.partial"
    git clone -q -c advice.detachedHead=false --depth 1 --branch "$NVM_VERSION" https://github.com/nvm-sh/nvm.git "$NVM_DIR.partial" \
      && mv "$NVM_DIR.partial" "$NVM_DIR" || FAILED+=("nvm clone")
  fi
  # shellcheck disable=SC1091
  if [[ -s "$NVM_DIR/nvm.sh" ]] && . "$NVM_DIR/nvm.sh" --no-use >/dev/null 2>&1; then
    log "Node $NODE_MAJOR via nvm"
    nvm install "$NODE_MAJOR" >/dev/null 2>&1 && nvm alias default "$NODE_MAJOR" >/dev/null || FAILED+=("nvm install $NODE_MAJOR")
  fi
fi
if load_node; then
  if has argus-npm; then
    log "npm ci (argus)"
    (cd "$PANOPTES_DIR/argus" && npm ci --no-audit --no-fund --loglevel=error >/dev/null) || FAILED+=("npm ci in argus")
  fi
  if has playwright-browsers || has playwright-deps; then
    PW="$PANOPTES_DIR/argus/node_modules/.bin/playwright"
    if [[ -x "$PW" ]]; then
      log "Playwright chromium → $PLAYWRIGHT_BROWSERS_PATH"
      "$PW" install chromium >/dev/null 2>&1 || FAILED+=("playwright install chromium")
      log "Playwright system deps (apt)"
      $SUDO env "PATH=$PATH" PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_PATH" "$PW" install-deps chromium >/dev/null 2>&1 \
        || FAILED+=("playwright install-deps chromium")
    else FAILED+=("playwright CLI missing (argus npm ci failed?)"); fi
  fi
else FAILED+=("Node $NODE_MAJOR unavailable, skipped npm / Playwright"); fi
if has catch-up-timer; then
  log "catch-up timer → $CRON_FILE"
  render_cron | $SUDO tee "$CRON_FILE.tmp" >/dev/null && $SUDO chmod 644 "$CRON_FILE.tmp" && $SUDO mv "$CRON_FILE.tmp" "$CRON_FILE" \
    || FAILED+=("catch-up timer install")
fi
if has cron-daemon || has catch-up-timer; then
  # no systemd on the box: start the daemon directly (it daemonizes itself); cron re-reads /etc/cron.d every minute
  pgrep -x cron >/dev/null || { log "starting cron"; $SUDO cron || FAILED+=("start cron"); }
fi
if has venv; then
  log "python venv → $VENV"
  python3 -m venv --clear "$VENV" >/dev/null 2>&1 || FAILED+=("python venv")
fi

if LEFT=$(check_all); then
  log "done: everything present"; exit 0
fi
log "still missing after install:"; printf '%s\n' "$LEFT" | sed 's/^/[bootstrap]   /' >&2
(( ${#FAILED[@]} )) && log "failed steps: ${FAILED[*]}"
printf '%s\n' "$LEFT"
exit 2
