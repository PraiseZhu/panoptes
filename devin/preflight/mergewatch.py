"""mergewatch — Devin Preflight script of automation A1 "merge -> test" (PraiseZhu/panoptes#312).

Devin port of the polling half of pi/bin/mergewatch.py. Each run (Devin calls it on a schedule) reads the head of every watched
branch of Mivo (main, integration/*, release/*) over the GitHub REST API. Only when a head moved, at least 10 minutes passed
since the last PR scan, or a retry is due, it lists the 100 most recently closed PRs and emits one work item per merged PR
that still needs a test: base not skipped (probe-*; PANOPTES_MAIN_ONLY=1 -> main only), merged in the last 48 h, no report
on the data branch whose tested_sha (docs-only: merged_sha) is the merge commit, fewer than 3 tries, backoff since the last
try reached (2 min before try 2, 10 min before try 3) and its previous run not still running (LAST_RUN_FILE). Main first,
then oldest first, at most 10 items per run.
Item: {"id": "PR-N@<sha12>", "version": "try-K", "title", "pr", "sha", "day", "try", "base", "mode": "event"}; a Devin
session runs `devin/bin/qa-run.sh --pr N --day D --try K --mode event` for it. Devin handles one (id, version) once, so the
version makes retries of the same merge commit new work.
Contract (docs.devin.ai/product-guides/automations): reads $EVENT_FILE, $STATE_FILE (JSON, < 64 KiB, no secrets),
$LAST_RUN_FILE; always writes the decision to $OUTPUT_FILE ({"items": [...]}); --stdout also prints it (local debugging).
A GitHub or auth error exits 1 without touching STATE_FILE or OUTPUT_FILE (Devin then keeps the old state).
Env: PRAISEDEVIN_PRIVATE_KEY_B64 (App key, see gh_app_jwt), GITHUB_API_URL (tests), MIVO_REPO, DATA_REPO, DATA_BRANCH,
PREFLIGHT_NOW (epoch seconds, tests only).
"""
import base64
import datetime as dt
import fnmatch
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gh_app_jwt  # noqa: E402

MIVO_REPO = os.environ.get("MIVO_REPO") or "xindong/mivo-canvas-plugin"
DATA_REPO = os.environ.get("DATA_REPO") or "PraiseZhu/grokbot-box"
DATA_BRANCH = os.environ.get("DATA_BRANCH") or "devin"
REPORTS_PATH = "box/workspace/mivo-qa/reports/merged"
WATCH_PREFIXES = ("heads/main", "heads/integration/", "heads/release/")
SKIP_BASES = ("probe-*",)
MAX_TRIES = 3
BACKOFF_S = (120, 600)
SCAN_EVERY_S = 600
LOOKBACK_S = 48 * 3600
MAX_ITEMS = 10
STATE_LIMIT = 64 * 1024
UNFINISHED = ("qa-error", "not-tested")
BUSY_STATUSES = ("running", "pending", "queued")
SHANGHAI = dt.timezone(dt.timedelta(hours=8))
SCHEMA = "panoptes-preflight-mergewatch/v1"


class PreflightError(Exception):
    pass


def now_s():
    return float(os.environ.get("PREFLIGHT_NOW") or time.time())


def read_json_file(path, default):
    """JSON of path, default when the variable is unset, the file is missing / empty or not JSON"""
    if not path:
        return default
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return default
    if not raw.strip():
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def write_json_file(path, doc, limit=None):
    data = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if limit is not None and len(data.encode()) >= limit:
        raise PreflightError(f"{path}: {len(data.encode())} bytes, limit is {limit}")
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data + "\n")
    os.replace(tmp, path)


def write_outputs(state, decision, stdout=False):
    """STATE_FILE first (a lost OUTPUT_FILE only repeats work), then the required OUTPUT_FILE."""
    out = os.environ.get("OUTPUT_FILE")
    if not out:
        raise PreflightError("OUTPUT_FILE is not set (Devin Preflight passes it; set it for local runs)")
    if os.environ.get("STATE_FILE"):
        write_json_file(os.environ["STATE_FILE"], state, STATE_LIMIT)
    write_json_file(out, decision, 256 * 1024)
    if stdout:
        print(json.dumps(decision, ensure_ascii=False, indent=2))


def to_epoch(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def to_shanghai_day(epoch):
    return dt.datetime.fromtimestamp(epoch, SHANGHAI).strftime("%Y-%m-%d")


def main_only():
    return os.environ.get("PANOPTES_MAIN_ONLY") == "1"


def should_test_base(ref):
    """pi/bin/basegate.should_test: every base except probe-*; a missing ref counts as main"""
    if ref is None:
        return True
    if main_only():
        return ref == "main"
    return not any(fnmatch.fnmatchcase(ref, g) for g in SKIP_BASES)


def select_busy_keys(last_run):
    """keys of LAST_RUN_FILE items whose run is still going"""
    items = last_run.get("items") if isinstance(last_run, dict) else last_run
    out = set()
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict) and str(it.get("status", "")).lower() in BUSY_STATUSES and it.get("key"):
            out.add(str(it["key"]))
    return out


def is_busy(item_id, busy_keys):
    return any(k == item_id or k.startswith(item_id + ":") or k.startswith(item_id + "#") for k in busy_keys)


def fetch_heads(client, repo=None):
    """{ref: sha} of the watched branches"""
    heads = {}
    for prefix in WATCH_PREFIXES:
        if main_only() and prefix != "heads/main":
            continue
        refs = client.get(repo or MIVO_REPO, f"/git/matching-refs/{prefix}")
        if not isinstance(refs, list):
            raise PreflightError(f"matching-refs/{prefix}: expected a list")
        for r in refs:
            ref, sha = r.get("ref", ""), (r.get("object") or {}).get("sha")
            if prefix == "heads/main" and ref != "refs/heads/main":
                continue
            if ref and sha:
                heads[ref[len("refs/heads/"):]] = sha
    return heads


def fetch_closed_pulls(client, page=1, repo=None):
    pulls = client.get(repo or MIVO_REPO, f"/pulls?state=closed&sort=updated&direction=desc&per_page=100&page={page}")
    if not isinstance(pulls, list):
        raise PreflightError("pulls: expected a list")
    return pulls


def to_merged(pull):
    """REST pull -> {number, sha, base, merged_at, day} or None when it was closed unmerged"""
    merged_at, sha = pull.get("merged_at"), pull.get("merge_commit_sha")
    if not merged_at or not sha:
        return None
    t = to_epoch(merged_at)
    return {"number": int(pull["number"]), "sha": sha, "base": (pull.get("base") or {}).get("ref"),
            "merged_at": t, "day": to_shanghai_day(t)}


def fetch_report_index(client, repo=None, branch=None):
    """{"PR-N.json": blob_sha} of reports/merged on the data branch ({} when the directory does not exist yet)"""
    spec = urllib.parse.quote(f"{branch or DATA_BRANCH}:{REPORTS_PATH}", safe="/:")
    try:
        tree = client.get(repo or DATA_REPO, f"/git/trees/{spec}")
    except gh_app_jwt.GitHubError as e:
        if "HTTP 404" in str(e):
            return {}
        raise
    if not isinstance(tree, dict) or tree.get("truncated"):
        raise PreflightError("reports tree: missing or truncated")
    return {e["path"]: e["sha"] for e in tree.get("tree", []) if e.get("type") == "blob" and "path" in e}


def fetch_report(client, blob_sha, repo=None):
    """report JSON of a blob, None when it is not readable JSON"""
    blob = client.get(repo or DATA_REPO, f"/git/blobs/{blob_sha}")
    try:
        return json.loads(base64.b64decode(blob.get("content", "")).decode("utf-8"))
    except (ValueError, AttributeError, UnicodeDecodeError):
        return None


def report_gap(pr, doc, present=True):
    """argus/src/catchup.mjs gapOf: None when the report covers pr["sha"], else the reason"""
    if not present:
        return "missing"
    if not isinstance(doc, dict):
        return "unreadable"
    entry = next((x for x in doc.get("prs") or [] if isinstance(x, dict) and x.get("number") == pr["number"]), None)
    if entry is None:
        return "no-entry"
    verdict = entry.get("verdict")
    if verdict == "skipped" and entry.get("skip_reason") == "non-main-base":
        return "stale-skip" if should_test_base(pr.get("base") or entry.get("base_ref")) else None
    if verdict in UNFINISHED:
        return f"unfinished ({verdict})"
    tested = (doc.get("merged_sha") or entry.get("merge_sha")) if verdict == "docs-only" else entry.get("tested_sha")
    if tested != pr["sha"]:
        return f"stale (tested {str(tested or 'none')[:12]} != merge {pr['sha'][:12]})"
    return None


def gap_of(client, pr, index):
    blob = index.get(f"PR-{pr['number']}.json")
    return report_gap(pr, fetch_report(client, blob) if blob else None, blob is not None)


def item_id(pr):
    return f"PR-{pr['number']}@{pr['sha'][:12]}"


def to_order(pr):
    return (pr["base"] not in (None, "main"), pr["merged_at"], pr["number"])


def prune(state, t):
    """drop tries / done of merges older than the lookback window; keep the state well under 64 KiB"""
    for key in ("tries", "done"):
        state[key] = {k: v for k, v in state[key].items() if t - v.get("merged_at", 0) <= LOOKBACK_S}
    while len(json.dumps(state, separators=(",", ":")).encode()) > STATE_LIMIT // 2 and (state["done"] or state["tries"]):
        bucket = state["done"] or state["tries"]
        del bucket[min(bucket, key=lambda k: bucket[k].get("merged_at", 0))]


def is_retry_due(entry, t):
    n = entry.get("n", 0)
    return 0 < n < MAX_TRIES and t - entry.get("at", 0) >= BACKOFF_S[n - 1]


def to_state(raw):
    st = raw if isinstance(raw, dict) and raw.get("schema") == SCHEMA else {}
    return {"schema": SCHEMA, "heads": dict(st.get("heads") or {}), "last_scan": float(st.get("last_scan") or 0),
            "tries": dict(st.get("tries") or {}), "done": dict(st.get("done") or {})}


def decide(client, state, busy_keys, t):
    """-> (new_state, decision, scanned) — the whole poll; raises on any GitHub error before anything is written"""
    heads = fetch_heads(client)
    moved = heads != state["heads"]
    retry_due = any(is_retry_due(e, t) for e in state["tries"].values())
    if not moved and not retry_due and t - state["last_scan"] < SCAN_EVERY_S:
        return state, {"items": []}, False
    candidates = []
    for pull in fetch_closed_pulls(client):
        pr = to_merged(pull)
        if pr is None or t - pr["merged_at"] > LOOKBACK_S or not should_test_base(pr["base"]):
            continue
        if item_id(pr) in state["done"]:
            continue
        candidates.append(pr)
    index = fetch_report_index(client) if candidates else {}
    items = []
    for pr in sorted(candidates, key=to_order):
        key = item_id(pr)
        entry = state["tries"].get(key, {"n": 0, "at": 0})
        if entry.get("n", 0) >= MAX_TRIES:
            continue
        if entry.get("n", 0) and not is_retry_due(entry, t):
            continue
        if is_busy(key, busy_keys):
            continue
        if len(items) >= MAX_ITEMS:
            break
        if gap_of(client, pr, index) is None:
            state["tries"].pop(key, None)
            state["done"][key] = {"merged_at": pr["merged_at"]}
            continue
        n = entry.get("n", 0) + 1
        state["tries"][key] = {"n": n, "at": t, "merged_at": pr["merged_at"], "pr": pr["number"]}
        items.append({"id": key, "version": f"try-{n}", "title": f"合并即测 PR #{pr['number']}（{pr['base']} "
                      f"{pr['sha'][:12]}，第 {n}/{MAX_TRIES} 次）", "pr": pr["number"], "sha": pr["sha"],
                      "day": pr["day"], "try": n, "base": pr["base"], "mode": "event"})
    state["heads"], state["last_scan"] = heads, t
    prune(state, t)
    return state, {"items": items}, True


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    stdout = "--stdout" in argv
    read_json_file(os.environ.get("EVENT_FILE"), {})
    state = to_state(read_json_file(os.environ.get("STATE_FILE"), {}))
    busy = select_busy_keys(read_json_file(os.environ.get("LAST_RUN_FILE"), {}))
    t = now_s()
    try:
        state, decision, scanned = decide(gh_app_jwt.Client(), state, busy, t)
        write_outputs(state, decision, stdout)
    except (gh_app_jwt.AppAuthError, gh_app_jwt.GitHubError, PreflightError, OSError, ValueError, KeyError) as e:
        print(f"mergewatch: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"mergewatch: {'scanned' if scanned else 'quiet'}, {len(decision['items'])} item(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
