"""basegate — which merged-PR base branches panoptes tests (11-03 integration-branch QA, phase 1).

One rule for every entry point (mergewatch, watchdog here; argus/src/catchup.mjs + nightly.mjs in JS via config.mjs
matchesSkipBase / shouldTestBase): a merge is tested unless its base matches registry.skipBaseBranches (glob, `*` may cross
`/`, today only `probe-*`). feat/*, integration/*, release/* are all tested; the merge commit is tested against its first
parent, exactly like main. Contract: argus/tests/fixtures/skip-base-cases.json is run by both languages.

Rollback switch (re-read on every call, nothing cached):
  PANOPTES_MAIN_ONLY=1 in the environment, or the flag file $PI_STATE/panoptes-main-only (default
  /workspace/mivo-qa/pi/panoptes-main-only, next to outbound-brief-route-off / outbound-disabled-types)
  -> only base == main is tested again (pre-11-03 behaviour). nightly.mjs itself is not affected.
No imports from lib (mergewatch must stay import-light)."""
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.environ.get("ARGUS_REGISTRY") or os.path.join(os.path.dirname(os.path.dirname(HERE)), "argus", "config", "registry.json")
DEFAULT_SKIP = ["probe-*"]   # = config.mjs DEFAULT_SKIP_BASE_BRANCHES


def flag_file():
    return os.path.join(os.environ.get("PI_STATE") or "/workspace/mivo-qa/pi", "panoptes-main-only")


def main_only():
    return os.environ.get("PANOPTES_MAIN_ONLY") == "1" or os.path.exists(flag_file())


def skip_globs(path=None):
    try:
        with open(path or REGISTRY) as f: g = json.load(f).get("skipBaseBranches", DEFAULT_SKIP)
    except (OSError, ValueError): return list(DEFAULT_SKIP)
    return g if isinstance(g, list) and all(isinstance(x, str) and x for x in g) else list(DEFAULT_SKIP)


def matches_skip(ref, globs):
    if not isinstance(ref, str) or not ref: return False
    return any(re.fullmatch(".*".join(re.escape(p) for p in g.split("*")), ref, re.S) for g in globs)


def should_test(ref, globs=None, mainonly=None):
    """True = this merge must get a report. ref None (field missing) counts as main: never drop a test on missing data."""
    if ref is None: return True
    if main_only() if mainonly is None else mainonly: return ref == "main"
    return not matches_skip(ref, skip_globs() if globs is None else globs)
