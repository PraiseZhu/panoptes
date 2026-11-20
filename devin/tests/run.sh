#!/usr/bin/env bash
# run.sh — every check for the devin/ adaptation layer (CI step "devin tests"; run locally the same way).
#   syntax: bash -n + shellcheck on devin/**/*.sh, py_compile on devin/**/*.py, node --check on devin/**/*.mjs
#   tests:  devin/**/*.test.sh (bash), *.test.py (python3), *.test.mjs (node --test)
# Any failure (or no test found at all) makes the exit code non-zero.
set -uo pipefail

DEVIN=$(cd "$(dirname "$0")/.." && pwd)
FAILED=() TESTS=0
PYCACHE=$(mktemp -d)
trap 'rm -rf "$PYCACHE"' EXIT

step() { local name=$1; shift; if "$@"; then echo "ok   $name"; else echo "FAIL $name" >&2; FAILED+=("$name"); fi; }
files() { find "$DEVIN" -type f -name "$1" -not -path '*/node_modules/*' -print0 | sort -z; }

command -v shellcheck >/dev/null || { echo "run.sh: shellcheck not installed" >&2; exit 1; }

while IFS= read -r -d '' f; do
  step "bash -n ${f#"$DEVIN"/}" bash -n "$f"
  step "shellcheck ${f#"$DEVIN"/}" shellcheck "$f"
done < <(files '*.sh')
while IFS= read -r -d '' f; do
  step "py_compile ${f#"$DEVIN"/}" env PYTHONPYCACHEPREFIX="$PYCACHE" python3 -m py_compile "$f"
done < <(files '*.py')
while IFS= read -r -d '' f; do
  step "node --check ${f#"$DEVIN"/}" node --check "$f"
done < <(files '*.mjs')

while IFS= read -r -d '' f; do
  TESTS=$((TESTS + 1))
  case "$f" in
    *.test.sh) step "test ${f#"$DEVIN"/}" bash "$f" ;;
    *.test.py) step "test ${f#"$DEVIN"/}" env PYTHONPYCACHEPREFIX="$PYCACHE" python3 "$f" ;;
    *.test.mjs) step "test ${f#"$DEVIN"/}" node --test "$f" ;;
  esac
done < <(find "$DEVIN" -type f \( -name '*.test.sh' -o -name '*.test.py' -o -name '*.test.mjs' \) -not -path '*/node_modules/*' -print0 | sort -z)

(( TESTS > 0 )) || { echo "run.sh: no devin tests found under $DEVIN" >&2; exit 1; }
if (( ${#FAILED[@]} )); then echo "devin tests: ${#FAILED[@]} failed: ${FAILED[*]}" >&2; exit 1; fi
echo "devin tests: all passed ($TESTS test files)"
