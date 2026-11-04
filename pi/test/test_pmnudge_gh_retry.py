"""python3 test/test_pmnudge_gh_retry.py (or pytest) — 11-05, Jasper's audit: every 白天催办 round 11:05–18:05 on 11-04 failed
because gh was rate-limited, with no retry and no alert. Now gh pr list is retried with backoff (up to 2 retries); still failing
-> alert state for watchdog (pmnudge:gh) + a gaps-open.json entry. gh and sleep are mocks; everything lives in a temp PI_STATE."""
import os, sys, json, tempfile, importlib.util
T = tempfile.mkdtemp()
os.environ.update(PI_STATE=T, PI_FLEET_MODE="grok", PI_GAPS_FILE=f"{T}/gaps-open.json")
B = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
s = importlib.util.spec_from_file_location("pmnudge", f"{B}/pmnudge.py"); M = importlib.util.module_from_spec(s); s.loader.exec_module(M)
RL = getattr(M, "GhRateLimited", RuntimeError)

def setup(fail_first):
    for f in (f"{M.BASE}/out/pm/{M.today()}.md", f"{T}/gaps-open.json", f"{M.BASE}/out/pm/pmnudge-gh-alert.json", f"{M.BASE}/gh-ratelimit.json"):
        if os.path.exists(f): os.remove(f)
    calls, sleeps = [], []
    def gh(args, *a, **k):
        calls.append(args)
        if args[:2] == ["pr", "list"] and len([c for c in calls if c[:2] == ["pr", "list"]]) <= fail_first:
            raise RL("gh pr list: GraphQL: API rate limit already exceeded for user ID 290989140.")
        return []
    M.gh = gh; M.SLEEP = sleeps.append; sys.argv = ["pmnudge.py"]
    return calls, sleeps

def md():
    p = f"{M.BASE}/out/pm/{M.today()}.md"
    return open(p).read() if os.path.exists(p) else ""

def test_pass_after_one_retry():
    calls, sleeps = setup(fail_first=1)
    M.main()
    txt = md()
    assert "gh 实查" in txt and "gh pr list 失败，本轮未催" not in txt, txt
    assert len(sleeps) == 1 and sleeps[0] > 0, sleeps
    assert not os.path.exists(f"{M.BASE}/out/pm/pmnudge-gh-alert.json") and not os.path.exists(f"{T}/gaps-open.json")

def test_alert_and_gap_after_retries_exhausted():
    calls, sleeps = setup(fail_first=99)
    M.main()
    assert len([c for c in calls if c[:2] == ["pr", "list"]]) == 3, calls        # first try + 2 retries
    assert len(sleeps) == 2 and sleeps[1] >= sleeps[0], sleeps                    # backoff grows
    assert "gh pr list 失败，本轮未催" in md()
    a = json.load(open(f"{M.BASE}/out/pm/pmnudge-gh-alert.json"))
    assert a["attempts"] == 3 and "rate limit" in a["error"], a
    gs = json.load(open(f"{T}/gaps-open.json"))
    g = [x for x in gs if x["id"] == f"pmnudge-gh-fail-{M.today()}"]
    assert len(g) == 1 and g[0]["status"] == "open", gs
    assert {"id", "opened", "bot", "owner", "item", "evidence_needed", "deadline", "last_chased", "chase_count", "status"} <= set(g[0])
    M.main()                                                                       # same day again: one entry, updated
    assert len([x for x in json.load(open(f"{T}/gaps-open.json")) if x["id"] == g[0]["id"]]) == 1
    import watchdog
    probs = {}; watchdog.pmnudge_gh_check(None, probs)
    assert "pmnudge:gh" in probs, probs
    setup(fail_first=0); M.main()                                                  # recovery clears the alert
    assert not os.path.exists(f"{M.BASE}/out/pm/pmnudge-gh-alert.json")

if __name__ == "__main__":
    test_pass_after_one_retry(); test_alert_and_gap_after_retries_exhausted(); print("ok test_pmnudge_gh_retry")
