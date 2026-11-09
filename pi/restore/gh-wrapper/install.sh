#!/usr/bin/env bash
# Restore the box gh wrapper after a box reset or a gh reinstall (11-10). Idempotent: a second run changes nothing.
#   1. $LINK (/usr/local/bin/gh) not a symlink to $WRAPPER: keep the native gh at $REAL (/usr/bin/gh; copied from $LINK when
#      $REAL is missing), then point $LINK at $WRAPPER. gh_wrapper.py execs the first `gh` on PATH that is not itself (lib.real_gh).
#   2. global git config (gh_wrapper.py docstring, pi/README.md "gh wrapper 安装"): credential.useHttpPath=true and, for github.com
#      and gist.github.com, the helper list ["", "!$LINK auth git-credential"] (what `gh auth setup-git` writes).
#   3. the 9 App switches in $PI_STATE: checked only. A missing one prints "NEED <path>"; never created here (a human's call).
# Exit: 0 installed and all switches present, 3 installed but a switch is missing (NEED lines), 2 failed (nothing usable to install).
# Run as the box user (git config --global is per user); file steps on a non-writable dir go through $GH_RESTORE_SUDO (sudo -n).
# Every path is overridable (tests use temp dirs): GH_RESTORE_LINK, GH_RESTORE_REAL, GH_RESTORE_WRAPPER, PI_STATE, GIT_CONFIG_GLOBAL.
set -uo pipefail
LINK=${GH_RESTORE_LINK:-/usr/local/bin/gh}
REAL=${GH_RESTORE_REAL:-/usr/bin/gh}
WRAPPER=${GH_RESTORE_WRAPPER:-/workspace/panoptes/pi/bin/gh_wrapper.py}
STATE=${PI_STATE:-/workspace/mivo-qa/pi}
SUDO=${GH_RESTORE_SUDO-sudo -n}
# lib.gh_app_flag + lib.py:176-177 / gh_wrapper.wrapper_flag / lib.gh_app_writes_flag / lib.gh_app_product_flag / lib.gh_app_product_writes_flag
FLAGS=(gh-app-enabled gh-app-wrapper-enabled gh-app-writes-enabled gh-app-product-enabled gh-app-product-writes-enabled
       gh-app-merger-enabled gh-app-mergewatch-enabled gh-app-nightly-enabled gh-app-ledgers-enabled)
die() { echo "FAIL $*" >&2; exit 2; }
as_needed() { if [ -w "$(dirname "$1")" ]; then "${@:2}"; else $SUDO "${@:2}"; fi; }   # $1 = the path being written

[ -f "$WRAPPER" ] && [ -x "$WRAPPER" ] || die "wrapper $WRAPPER missing or not executable"
w=$(readlink -f "$WRAPPER")
if [ -L "$LINK" ] && [ "$(readlink -f "$LINK")" = "$w" ]; then
  echo "ok wrapper: $LINK -> $w"
else
  is_native() { [ -f "$1" ] && [ -x "$1" ] && case "$(basename "$(readlink -f "$1")")" in gh_wrapper.py|gh-praise-qa) false;; *) true;; esac; }
  if ! is_native "$REAL"; then
    is_native "$LINK" || die "no native gh at $REAL or $LINK"
    as_needed "$REAL" install -m 0755 "$(readlink -f "$LINK")" "$REAL" || die "copy native gh $LINK -> $REAL"
    echo "installed native gh: $REAL (from $LINK)"
  fi
  as_needed "$LINK" ln -sfn "$w" "$LINK.restore-tmp" && as_needed "$LINK" mv -Tf "$LINK.restore-tmp" "$LINK" || die "symlink $LINK -> $w"
  echo "installed wrapper: $LINK -> $w (native gh: $REAL)"
fi

want=$(printf '\n!%s auth git-credential' "$LINK")
for host in github.com gist.github.com; do
  key="credential.https://$host.helper"
  if [ "$(git config --global --get-all "$key")" = "$want" ]; then echo "ok git $key"; continue; fi
  git config --global --unset-all "$key" 2>/dev/null
  git config --global --add "$key" "" && git config --global --add "$key" "!$LINK auth git-credential" || die "git config $key"
  echo "set git $key"
done
if [ "$(git config --global --get credential.useHttpPath)" = true ]; then echo "ok git credential.useHttpPath"
else git config --global credential.useHttpPath true || die "git config credential.useHttpPath"; echo "set git credential.useHttpPath=true"; fi

rc=0
for f in "${FLAGS[@]}"; do [ -e "$STATE/$f" ] && echo "ok switch $f" || { echo "NEED $STATE/$f (not created: a human turns it on)"; rc=3; }; done
exit $rc
