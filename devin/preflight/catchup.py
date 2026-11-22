"""catchup — Devin Preflight script of automation A2 "hourly catch-up" (PraiseZhu/panoptes#312).

Devin port of the gap scan of argus/src/catchup.mjs (run-nightly.sh --catch-up): PRs of Mivo merged on yesterday or today
(Asia/Shanghai) into a tested base (every base except probe-*; PANOPTES_MAIN_ONLY=1 -> main only) are checked against
reports/merged/PR-N.json on the data branch. Gap = report missing, unreadable, without an entry for the PR, unfinished
verdict (qa-error, not-tested), stale non-main-base skip, or tested_sha (docs-only: merged_sha) != merge commit — the same
gapOf rules (mergewatch.report_gap). PRs merged less than 60 min ago are left to mergewatch (A1); a gap is offered at most
3 times (then given up, recorded in the state); while the previous catch-up run is still running nothing new is emitted.
No gap -> {"items": []}. Gaps -> exactly one item that lists every gap PR:
{"id": "catchup-<today>@<hash12 of the gap set>", "version": "try-K", "title", "days", "prs": [N, ...],
 "gaps": [{"pr", "sha", "day", "base", "reason", "try"}], "mode": "catch-up"}; its Devin session runs
`devin/bin/qa-run.sh --pr N --day D --try K --mode catch-up` per gap.
Contract and env: as mergewatch (EVENT_FILE, STATE_FILE < 64 KiB, LAST_RUN_FILE, required OUTPUT_FILE, --stdout, exit 1 on
a GitHub error without writing anything).
"""
import datetime as dt
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gh_app_jwt  # noqa: E402
import mergewatch as mw  # noqa: E402

MAX_TRIES = 3
GRACE_S = 3600
MAX_PAGES = 10
SCHEMA = "panoptes-preflight-catchup/v1"


def catchup_days(t):
    """[yesterday, today] in Asia/Shanghai"""
    today = dt.datetime.fromtimestamp(t, mw.SHANGHAI).date()
    return [(today - dt.timedelta(days=1)).isoformat(), today.isoformat()]


def window_start(days):
    return dt.datetime.fromisoformat(f"{days[0]}T00:00:00+08:00").timestamp()


def fetch_window_merges(client, days):
    """merged PRs of the window, newest updated first, paging until updates are older than the window"""
    start, out = window_start(days), []
    for page in range(1, MAX_PAGES + 1):
        pulls = mw.fetch_closed_pulls(client, page)
        for pull in pulls:
            pr = mw.to_merged(pull)
            if pr and pr["day"] in days:
                out.append(pr)
        if len(pulls) < 100 or all(mw.to_epoch(p.get("updated_at") or "1970-01-01T00:00:00Z") < start for p in pulls):
            break
    return out


def to_state(raw):
    st = raw if isinstance(raw, dict) and raw.get("schema") == SCHEMA else {}
    return {"schema": SCHEMA, "tries": dict(st.get("tries") or {}), "gave_up": dict(st.get("gave_up") or {}),
            "last_check": st.get("last_check")}


def gap_set_id(today, gaps):
    digest = hashlib.sha256(",".join(sorted(f"{g['pr']}@{g['sha']}" for g in gaps)).encode()).hexdigest()
    return f"catchup-{today}@{digest[:12]}"


def decide(client, state, busy_keys, t):
    """-> (new_state, decision)"""
    days = catchup_days(t)
    if any(k.startswith("catchup-") for k in busy_keys):
        return state, {"items": []}
    merges = [pr for pr in fetch_window_merges(client, days) if mw.should_test_base(pr["base"])]
    index = mw.fetch_report_index(client) if merges else {}
    keep = {mw.item_id(pr) for pr in merges}
    gaps = []
    for pr in sorted(merges, key=lambda p: (p["merged_at"], p["number"])):
        key = mw.item_id(pr)
        if key in state["gave_up"] or t - pr["merged_at"] < GRACE_S:
            continue
        reason = mw.gap_of(client, pr, index)
        if reason is None:
            state["tries"].pop(key, None)
            continue
        n = state["tries"].get(key, 0)
        if n >= MAX_TRIES:
            state["gave_up"][key] = {"at": t, "reason": reason}
            continue
        gaps.append({"pr": pr["number"], "sha": pr["sha"], "day": pr["day"], "base": pr["base"], "reason": reason,
                     "try": n + 1})
    for g in gaps:
        state["tries"][f"PR-{g['pr']}@{g['sha'][:12]}"] = g["try"]
    state["tries"] = {k: v for k, v in state["tries"].items() if k in keep}
    state["gave_up"] = {k: v for k, v in state["gave_up"].items() if k in keep}
    state["last_check"] = mw.to_shanghai_day(t)
    if not gaps:
        return state, {"items": []}
    prs = [g["pr"] for g in gaps]
    item = {"id": gap_set_id(days[1], gaps), "version": f"try-{max(g['try'] for g in gaps)}",
            "title": f"补测 {len(prs)} 个缺口 PR：" + " ".join(f"#{n}" for n in prs),
            "days": days, "prs": prs, "gaps": gaps, "mode": "catch-up"}
    return state, {"items": [item]}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    stdout = "--stdout" in argv
    mw.read_json_file(os.environ.get("EVENT_FILE"), {})
    state = to_state(mw.read_json_file(os.environ.get("STATE_FILE"), {}))
    busy = mw.select_busy_keys(mw.read_json_file(os.environ.get("LAST_RUN_FILE"), {}))
    try:
        state, decision = decide(gh_app_jwt.Client(), state, busy, mw.now_s())
        mw.write_outputs(state, decision, stdout)
    except (gh_app_jwt.AppAuthError, gh_app_jwt.GitHubError, mw.PreflightError, OSError, ValueError, KeyError) as e:
        print(f"catchup: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    items = decision["items"]
    print(f"catchup: {len(items[0]['prs']) if items else 0} gap PR(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
