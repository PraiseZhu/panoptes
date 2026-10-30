#!/usr/bin/env python3
"""#112 acceptance follow-ups (QA 10-30): outbound push-fix fails closed, paginated commits, protected check on the real send,
strict trailer block, case-insensitive PROTECTED_RE, naive dates."""
import datetime as dt, json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
os.environ.setdefault("PI_TAKEOVER_DIR", tempfile.mkdtemp())
import lib, outbound

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 30, 3, 0, tzinfo=UTC)
HEAD = "51802504f8dabc49b866018857057bcb532c1533"
PV = {"state": "OPEN", "isDraft": True, "author": {"login": "PraiseZhu"}, "headRefName": "qa-fix/fixpr-1420-x", "headRefOid": HEAD,
      "body": "x <!-- pi-line fixpr:1420:mq1-e4326dd686de44c39867 -->"}
def c(sha, msg, hours_ago=1, naive=False):
    t = NOW - dt.timedelta(hours=hours_ago)
    return {"sha": sha, "commit": {"message": msg, "committer": {"date": t.replace(tzinfo=None).isoformat() if naive else t.isoformat()}}}

class PushFixFailClosed(unittest.TestCase):
    def setUp(self):
        self.old_gh, self.old_run, self.old_tk = outbound.gh, outbound.run, lib.TAKEOVER
        lib.TAKEOVER = tempfile.mkdtemp(); self.calls = []
        def run(args, *a, **k):
            self.calls.append(args)
            if "push" in args: raise AssertionError("git push must not be called")
            return HEAD if "rev-parse" in args else ""
        outbound.run = run
    def tearDown(self): outbound.gh, outbound.run, lib.TAKEOVER = self.old_gh, self.old_run, self.old_tk
    def h(self, patch="/nonexistent"):
        return outbound.h_push_fix({"key": "revise:1444:51802504f8da", "pr": 1444, "branch": PV["headRefName"], "head": HEAD, "patch": patch})
    def test_commits_api_error_is_stale(self):   # item 1
        def gh(args, js=True):
            if args[0] == "pr": return dict(PV)
            raise RuntimeError("gh api: HTTP 502")
        outbound.gh = gh
        st = self.h()[1](); self.assertTrue(st.startswith("stale:ownership unknown"), st)
    def test_state_error_skips_push_fix(self):   # item 1, belt and braces in the main loop
        self.assertTrue(outbound.state_error_skips("gh.pr.push-fix"))
    def test_paginated_pages_flattened(self):    # item 2
        pages = [[c(f"p{i}", "x\n\nPi-Revise: revise:1444:51802504f8da", 1) for i in range(100)], [c("foreign9999", "human", 0.5)]]
        self.assertIn("--slurp", lib.pr_commits_args(1444))
        self.assertEqual(len(lib.flatten_pages(pages)), 101)
        self.assertEqual(lib.flatten_pages([{"sha": "a"}]), [{"sha": "a"}])   # already flat (old gh / stubs)
        outbound.gh = lambda args, js=True: dict(PV) if args[0] == "pr" else pages
        self.assertIn("foreign99", self.h()[1]())
    def test_protected_patch_real_send_raises_before_push(self):   # item 3
        p = tempfile.mktemp()
        with open(p, "w") as f: f.write("diff --git a/src/__tests__/kvNoWrite.guard.test.ts b/src/__tests__/kvNoWrite.guard.test.ts\n")
        outbound.W.mkdtemp = lambda prefix: tempfile.mkdtemp(prefix=prefix)
        with self.assertRaises(RuntimeError) as e: self.h(p)[3]()
        self.assertIn("gate files", str(e.exception))
        self.assertFalse([a for a in self.calls if "push" in a])

class Trailers(unittest.TestCase):   # item 4
    F = staticmethod(lambda cs: lib.foreign_recent_commits(cs, NOW))
    def test_spoofed_body_line_is_foreign(self):
        self.assertEqual(self.F([c("sf", "human\n\nPi-Revise: revise:1:0123456789ab\n\nmore text by human")]), ["sf"])
        self.assertEqual(self.F([c("bad", "human\n\nPi-Revise: whatever")]), ["bad"])
        self.assertEqual(self.F([c("bo", "human\n\nPi-Open: fixpr:x:y")]), ["bo"])
    def test_builders_formats_accepted(self):
        fp = "mq1-e4326dd686de44c39867"
        msgs = [f"fix\n\nQA: #1420\nFingerprint: {fp}\nPi-Open: fixpr:1420:{fp}",                              # prfix.py fix_one
                f"fix: CI\n\nQA: #1420\nFingerprint: {fp}\nPi-Revise: revise:1444:{HEAD[:12]}",                 # prfix.py revise
                f"fix: CI\n\nQA: #1420\nFingerprint: {fp}\nPi-Revise: revise:1444:{HEAD[:12]}\nSensitive-Files: a, b",
                f"Merge main abc into br\n\nQA: #1420\nFingerprint: {fp}\nPi-Revise: revise:1444:{HEAD[:12]}:merge",  # prfix.py merge
                f"fix: CI\n\nPi-Revise: revise:1444:{HEAD[:12]}"]                                                  # outbound.py default
        self.assertEqual(self.F([c(str(i), m) for i, m in enumerate(msgs)]), [])

class ProtectedCase(unittest.TestCase):   # item 5
    def test_lowercase_and_backslash(self):
        fs = ["mivo/src/__tests__/ghostFsNoWrite.guard.test.ts", "mivo/src/__tests__/kvNoWrite.guard.test.ts",
              "cindyplugin\\src\\__tests__\\mergeBaseGuard.ts", "src/__tests__/manifestpolicy.test.ts"]
        self.assertEqual(len(lib.protected_files(fs)), 4)
        self.assertEqual(lib.protected_files(["cindyplugin/src/policyHelpers.ts", "src/guards/thing.ts"]), [])

class NaiveDate(unittest.TestCase):   # item 6
    def test_naive_counts_as_recent(self):
        self.assertEqual(lib.foreign_recent_commits([c("nv", "x", 30, naive=True)], NOW), ["nv"])
        self.assertEqual(lib.foreign_recent_commits([c("nvp", "x\n\nPi-Revise: revise:1:0123456789ab", 30, naive=True)], NOW), [])

if __name__ == "__main__": unittest.main(verbosity=2)
