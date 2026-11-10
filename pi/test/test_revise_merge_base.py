#!/usr/bin/env python3
"""11-10: prfix --revise merged origin/main into a fix PR whose base is integration/* (product #2056, base
integration/cindy-media-phase2 -> head 013b4bce DIRTY, every pull_request check silently skipped). Now the merge uses the PR's
baseRefName; no baseRefName -> a human (never a default main); a head that already holds main commits its base does not have
(a wrong merge earlier) -> needs_praise once (one DM), no merge, no retry. Local throw-away git repos only, no network, no tokens.
Run: python3 test/test_revise_merge_base.py"""
import os, sys, types, contextlib, subprocess, tempfile, shutil, unittest
os.environ.setdefault("PRFIX_OWNERS", "*"); os.environ["PRFIX_DISPATCHED"] = "1"
for v in [k for k in os.environ if k.startswith("GIT_")]: del os.environ[v]
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin")); sys.path.insert(0, HERE)
import isolation  # noqa: E402
isolation.sandbox_state()
import prfix  # noqa: E402

BASE = "integration/media-phase2"; BR = "qa-fix/fixpr-1999-x"; N = 2099

def g(d, *a):
    r = subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main", *a],
                       capture_output=True, text=True)
    if r.returncode: raise AssertionError(f"git {a}: {r.stderr}")
    return r.stdout.strip()

def commit(d, f, text, msg):
    with open(os.path.join(d, f), "w") as fh: fh.write(text)
    g(d, "add", f); g(d, "commit", "-q", "-m", msg); return g(d, "rev-parse", "HEAD")

class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pi-test-revbase-"); t = self.tmp
        src = f"{t}/src"; os.makedirs(src); g(src, "init", "-q")
        commit(src, "a.txt", "0\n", "root")
        g(src, "checkout", "-q", "-b", BASE); commit(src, "b.txt", "base\n", "base work")
        g(src, "checkout", "-q", "-b", BR); self.head = commit(src, "fix.txt", "fix\n", "the fix")
        g(src, "checkout", "-q", BASE); self.base = commit(src, "b2.txt", "base moved\n", "base moves on")
        g(src, "checkout", "-q", "main"); self.main = commit(src, "m.txt", "main only\n", "main-only work")
        g(f"{t}", "clone", "-q", "--bare", src, f"{t}/origin.git")
        g(f"{t}", "clone", "-q", f"{t}/origin.git", f"{t}/clone")
        self.src = src; self.actions = []
        self.p = {"number": N, "headRefOid": self.head, "headRefName": BR, "baseRefName": BASE, "title": "fix", "mergeStateStatus": "DIRTY",
                  "statusCheckRollup": []}
        self.T = {"tickets": {}}
        self.patches = dict(GIT=f"{t}/clone", TMPROOT=f"{t}/tmp", WT_ROOT=f"{t}/wt", OUT=f"{t}/out",
                            npm_ci=lambda wd: (0, ""), related_run=lambda wd, s, e=(): (0, "", None), vitest=lambda wd, f: (0, ""),
                            preflight=lambda wd: ({"ok": True, "passed": 1, "total": 1, "seconds": 0, "timeout": False, "failed": None, "rc": 0}, ""),
                            finding_of=lambda qa, fp: {}, record_actions=lambda r, d, a: self.actions.extend(a), log=lambda *a: None,
                            takeover=lambda n: True, _gh_json=lambda a, t=0: self.p if "view" in a else [],
                            revise_target=lambda p, c: (1999, "mq1-x", None), mem_gb=lambda: 99.0, try_lock=lambda path: object(),
                            unlock=lambda f: getattr(f, "close", lambda: None)(), tickets_locked=lambda: contextlib.nullcontext(self.T), finish=lambda r, code, A: r,
                            outbound_ledger=lambda: {})
        os.makedirs(f"{t}/out/locks", exist_ok=True)
        self.saved = {k: getattr(prfix, k, None) for k in self.patches}; self.had = {k: hasattr(prfix, k) for k in self.patches}
        for k, v in self.patches.items(): setattr(prfix, k, v)
        self.mark = prfix.W.mark; prfix.W.mark = lambda *a, **k: None

    def tearDown(self):
        for k, v in self.saved.items():
            if self.had[k]: setattr(prfix, k, v)
            else: delattr(prfix, k)
        prfix.W.mark = self.mark; shutil.rmtree(self.tmp, ignore_errors=True)

    def merge(self, dry=True):
        return prfix.revise_merge(types.SimpleNamespace(dry_run=dry), "2026-11-10", N, 1999, "mq1-x", self.p, [])

    def test_integration_base_pr_gets_its_base_merged_not_main(self):
        out, res = self.merge(dry=False)
        self.assertEqual(out["status"], "pr_action_written", out)
        self.assertEqual(res["base"], self.base, "merged origin/main instead of the PR's base")
        a = [x for x in self.actions if x["type"] == "gh.pr.push-fix"][0]
        self.assertEqual((a["main"], a["base_ref"]), (self.base, BASE))   # outbound checks parents == [head, a["main"]]

    def test_no_base_ref_goes_to_a_human_never_main(self):
        self.p.pop("baseRefName")
        out, res = self.merge()
        self.assertEqual(out["kind"], "praise", out); self.assertNotEqual(res["base"], self.main)

    def wrong_merge_head(self):   # an earlier round merged main into the PR branch: push that to origin
        g(self.src, "checkout", "-q", BR); g(self.src, "merge", "-q", "--no-ff", "-m", "Merge main into qa-fix", "main")
        h = g(self.src, "rev-parse", "HEAD"); g(self.src, "push", "-q", f"{self.tmp}/origin.git", f"{BR}:{BR}")
        self.p["headRefOid"] = h; return h

    def test_head_already_holding_main_goes_to_praise_without_merging(self):
        h = self.wrong_merge_head()
        out, res = self.merge(dry=False)
        self.assertEqual(out["kind"], "praise", out)
        self.assertFalse([x for x in self.actions if x["type"] == "gh.pr.push-fix"], "merged again")
        self.assertEqual(g(f"{self.tmp}/origin.git", "rev-parse", BR), h)

    def test_wrong_merge_dm_once_and_never_retried(self):
        self.wrong_merge_head(); calls = []
        real = prfix.revise_merge
        def spy(*a): calls.append(1); return real(*a)
        prfix.revise_merge = spy
        try:
            A = types.SimpleNamespace(revise=N, dry_run=False)
            r1 = prfix.revise(A); r2 = prfix.revise(A)
        finally: prfix.revise_merge = real
        self.assertEqual(r1["kind"], "praise", r1); self.assertEqual(r2["status"], "needs_praise", r2)
        self.assertEqual(len(calls), 1, "retried")
        dms = [x for x in self.actions if x["type"] == "slack.dm"]
        self.assertEqual(len(dms), 1, dms); self.assertNotIn("workflow", dms[0]["text"])
        self.assertEqual(self.T["tickets"][f"rev:{N}"]["status"], "needs_praise")

if __name__ == "__main__": unittest.main(verbosity=2)
