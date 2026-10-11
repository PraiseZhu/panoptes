#!/usr/bin/env bash
# panoptes /tmp sweep: remove leftover argus-* temp dirs (test runs, killed pipeline runs).
#
#   scripts/sweep-tmp.sh [--max-age-hours H] [--root DIR] [--dry-run] [--quiet]
#
# A directory is removed only when ALL of these hold:
#   - it is a direct child of ROOT (default ${TMPDIR_SWEEP_ROOT:-/tmp}) named argus-* and is a real directory (no symlink)
#   - it is owned by the current user (box)
#   - nothing inside it (the dir itself included) was modified within the last H hours (default 24)
#   - no live process has its cwd, root or an open file descriptor inside it (/proc scan)
# Output: one JSON line { root, max_age_hours, candidates, removed, freed_bytes, skipped_in_use, skipped_recent, dry_run }.
# Exit 0 always (a sweep must never fail the run that calls it).
set -uo pipefail
ROOT="${TMPDIR_SWEEP_ROOT:-/tmp}"
MAX_AGE_H=24
DRY=0
QUIET=0
while (( $# )); do
  case "$1" in
    --max-age-hours) MAX_AGE_H="${2:?}"; shift ;;
    --root) ROOT="${2:?}"; shift ;;
    --dry-run) DRY=1 ;;
    --quiet) QUIET=1 ;;
    -h|--help) sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 0 ;;
  esac
  shift
done
[[ "$MAX_AGE_H" =~ ^[0-9]+$ ]] || { echo "--max-age-hours needs whole hours" >&2; exit 0; }
ROOT="${ROOT%/}"
me="$(id -un)"
max_min=$(( MAX_AGE_H * 60 ))

# paths held by live processes (cwd, root, open fds), resolved
declare -A held=()
while IFS= read -r t; do
  d="${t#"$ROOT"/}"; held["${d%%/*}"]=1
done < <(find /proc/[0-9]*/cwd /proc/[0-9]*/root /proc/[0-9]*/fd -maxdepth 1 \( -lname "$ROOT/argus-*" \) -printf '%l\n' 2>/dev/null)

candidates=0 removed=0 freed=0 in_use=0 recent=0
shopt -s nullglob
for dir in "$ROOT"/argus-*; do
  [[ -d "$dir" && ! -L "$dir" ]] || continue
  [[ "$(stat -c %U "$dir" 2>/dev/null)" == "$me" ]] || continue
  candidates=$((candidates + 1))
  name="${dir##*/}"
  if [[ -n "${held[$name]:-}" ]]; then in_use=$((in_use + 1)); continue; fi
  # anything (dir itself included) modified within the window → keep
  if [[ -n "$(find "$dir" -mmin "-$max_min" -print -quit 2>/dev/null)" ]]; then recent=$((recent + 1)); continue; fi
  size=$(du -sb "$dir" 2>/dev/null | cut -f1); size=${size:-0}
  if (( DRY )); then removed=$((removed + 1)); freed=$((freed + size)); continue; fi
  if rm -rf --one-file-system -- "$dir" 2>/dev/null && [[ ! -e "$dir" ]]; then removed=$((removed + 1)); freed=$((freed + size)); fi
done
(( QUIET )) && (( removed == 0 )) && exit 0
printf '{"root":"%s","max_age_hours":%s,"candidates":%s,"removed":%s,"freed_bytes":%s,"skipped_in_use":%s,"skipped_recent":%s,"dry_run":%s}\n' \
  "$ROOT" "$MAX_AGE_H" "$candidates" "$removed" "$freed" "$in_use" "$recent" "$([[ $DRY == 1 ]] && echo true || echo false)"
exit 0
