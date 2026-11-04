#!/usr/bin/env python3
"""11-05 (#1762 REPORT items 1-6): open-fix-PR cap in h_fixpr validate/do and prwatch.admit_block (fail closed), the reserve flag's
polarity, one shared is_fix_pr, no pr-ci-freshness rerun loop when the head has no CI run, and xindong/* writes on the same identity
in gh_wrapper as in lib.gh_env (the App only with gh-app-product-writes-enabled). Mocks only, no network."""
import json, os, sys, tempfile, types, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin")); sys.path.insert(0, HERE)
import test_gh_app_fixes as G  # noqa: E402  (sandboxed PI_STATE, ghtoken mocks, wrapper harness)
import lib, outbound, prwatch, stdcheck  # noqa: E402

MARK = "<!-- pi-line fixpr: t1 -->"
def prs(*specs): return ([{"number": n, "headRefName": h, "body": b, "isDraft": d} for n, h, b, d in specs])
def fake_gh(out=None, fail=False):
    def gh(args, *a, **k):
        if args[:2] == ["pr", "list"]:
            if fail: raise RuntimeError("HTTP 502")
            if out is None: return "not a list"
            return out
        raise AssertionError(f"unexpected gh {args}")
    return gh
TWO = prs((1, "qa-fix/a", "", True), (2, "feature/x", MARK, False))   # a draft without marker + a marker on any branch: both count
ONE = prs((1, "qa-fix/a", "", True), (3, "feature/y", "", False))


class Cap(unittest.TestCase):
    def test_count_like_stdcheck_and_fail_closed(self):
        self.assertIn("2 open fix PRs", lib.fixpr_cap_reason(fake_gh(TWO)))
        self.assertIsNone(lib.fixpr_cap_reason(fake_gh(ONE)))
        self.assertIsNone(lib.fixpr_cap_reason(fake_gh(TWO), exclude=2))   # a superseding PR replaces its own predecessor
        self.assertIn("fail closed", lib.fixpr_cap_reason(fake_gh(fail=True)))
        self.assertIn("fail closed", lib.fixpr_cap_reason(fake_gh(None)))

    def test_h_fixpr_validate_and_do_refuse_and_stay_pending(self):
        a = {"ticket": "t1", "title": "x", "patch": "/nonexistent.patch"}
        for out, fail in ((TWO, False), (None, True)):
            with mock.patch.object(outbound, "gh", fake_gh(out, fail)), mock.patch.object(outbound, "fixpr_gate_reason", return_value=None), \
                 mock.patch.object(outbound, "fixpr_base_ref", return_value="main"), mock.patch.object(outbound, "fixpr_check_source_base"), \
                 mock.patch.object(outbound, "run", side_effect=AssertionError("no clone / push while gated")):
                _, _, validate, do = outbound.h_fixpr(a)
                with self.assertRaisesRegex(RuntimeError, "gated"): validate()
                with self.assertRaisesRegex(RuntimeError, "refused: gated"): do()

    def test_admit_block_uses_the_same_count(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(prwatch, "gh", fake_gh(TWO)), \
             mock.patch.object(prwatch, "TEST_RESERVE_OFF", f"{d}/off"), mock.patch.object(prwatch, "headroom_mb", return_value=99999), \
             mock.patch.object(prwatch, "prfix_runs", return_value=([], [])), mock.patch.object(prwatch, "tests_queued", return_value=0):
            self.assertIn("2 open fix PRs", prwatch.admit_block({}, {})[0])
            with mock.patch.object(prwatch, "gh", fake_gh(fail=True)): self.assertIn("fail closed", prwatch.admit_block({}, {})[0])

    def test_reserve_flag_polarity(self):
        # REPORT item 3 checked: flag_on(OFF_FILE) is True while the off file is ABSENT, so the cap is the default and the file rolls it back
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(prwatch.flag_on(f"{d}/off")); open(f"{d}/off", "w").close(); self.assertFalse(prwatch.flag_on(f"{d}/off"))

    def test_one_shared_is_fix_pr(self):
        self.assertIs(prwatch.is_fix_pr, lib.is_fix_pr); self.assertIs(stdcheck.is_fix_pr, lib.is_fix_pr)
        self.assertTrue(lib.is_fix_pr({"headRefName": "qa-fix/a", "body": ""})); self.assertTrue(lib.is_fix_pr({"headRefName": "fix/qa-b"}))
        self.assertTrue(lib.is_fix_pr({"headRefName": "x", "body": MARK})); self.assertFalse(lib.is_fix_pr({"headRefName": "fix/other"}))


class NoCiRun(unittest.TestCase):
    def test_freshness_only_red_without_ci_run_reminds_once_never_reruns(self):
        pr = {"number": 7, "title": "t", "headRefName": "qa-fix/t1", "headRefOid": "abc", "body": MARK, "isDraft": True, "url": "u",
              "mergeStateStatus": "CLEAN", "reviewThreads": [],
              "statusCheckRollup": [{"name": "pr-ci-freshness", "status": "COMPLETED", "conclusion": "FAILURE"}]}
        acts, cmds = [], []
        def gh(args, *a, **k): cmds.append(list(args)); return json.dumps([pr]) if args[:2] == ["pr", "list"] else "[]"
        st = {}
        with mock.patch.object(prwatch, "gh", gh), mock.patch.object(prwatch, "act", lambda who, x: acts.append(x)), \
             mock.patch.object(prwatch, "self_pr_list", return_value=[pr], create=True), mock.patch.object(prwatch, "qlog", lambda *a: None):
            for _ in range(3):
                try: prwatch.check_drafts(st)
                except Exception: pass
        self.assertFalse([c for c in cmds if "rerun" in c or c[:2] == ["run", "rerun"]], cmds)
        self.assertEqual([x["key"] for x in acts if "nocirun" in x.get("key", "")], ["nocirun:" + [x["key"] for x in acts if "nocirun" in x.get("key", "")][0].split(":", 1)[1]])


class ProductWritesIdentity(G.Base):
    wrap = G.Wrapper.wrap; get = G.CredentialHelper.get; cred = G.WrapperGitCredential.cred
    OTHER = G.WrapperGitCredential.OTHER
    OUT = ["python3", os.path.join(G.BIN, "outbound.py")]
    WRITES = ((["pr", "create", "-R", G.PROD, "--draft", "--title", "t", "--body", "b"], OUT), (["pr", "ready", "5", "-R", G.PROD], OUT),
              (["pr", "comment", "5", "-R", G.PROD, "--body", "b"], OUT), (["issue", "create", "-R", G.PROD, "--title", "t", "--body", "b"], OUT),
              (["issue", "close", "5", "-R", G.PROD], OUT))
    def idt(self, c): return (c[2] if c else {}).get("GH_TOKEN", "")

    def test_shared_decision(self):
        self.on()
        for a, _ in self.WRITES + ((["api", "-X", "POST", f"repos/{G.PROD}/issues/5/comments", "-f", "body=b"], ()),):
            self.assertEqual(lib.product_write_identity(a), "pat", a); self.assertFalse(lib.gh_app_wanted(a), a)
        self.assertEqual(lib.product_write_identity(["git", "push"], G.PROD), "pat")
        self.assertEqual(lib.product_write_identity(["run", "rerun", "9", "-R", G.PROD]), "pat")
        self.assertIsNone(lib.product_write_identity(["pr", "view", "5", "-R", G.PROD]))
        self.assertIsNone(lib.product_write_identity(["pr", "create", "-R", G.PAN, "--title", "t"]))
        self.on("gh-app-product-writes-enabled")
        for a, _ in self.WRITES: self.assertEqual(lib.product_write_identity(a), "app", a); self.assertTrue(lib.gh_app_wanted(a), a)
        self.assertEqual(lib.product_write_identity(["git", "push"], G.PROD), "app")
        self.assertEqual(lib.product_write_identity(["run", "rerun", "9", "-R", G.PROD]), "pat")   # G2 unchanged
        os.remove(G.F("gh-app-product-writes-enabled"))

    def test_wrapper_matches_lib_flag_off_and_on(self):
        self.on("gh-app-wrapper-enabled")
        for a, caller in self.WRITES:
            rc, c, err = self.wrap(a, env={"GITHUB_TOKEN": "pat", "GH_TOKEN": ""}, caller=caller)
            self.assertIsNotNone(c, (a, err)); self.assertNotEqual(self.idt(c), G.FAKE[G.PROD], a)
        self.on("gh-app-product-writes-enabled")
        for a, caller in self.WRITES:
            rc, c, err = self.wrap(a, env={"GITHUB_TOKEN": "pat"}, caller=caller)
            self.assertIsNotNone(c, (a, err)); self.assertEqual(self.idt(c), G.FAKE[G.PROD], a)
        os.remove(G.F("gh-app-product-writes-enabled"))

    def test_git_credentials_flag_off_and_on(self):
        self.on("gh-app-wrapper-enabled"); q = G.CredentialHelper and "protocol=https\nhost=github.com\npath={}\n"
        self.assertEqual(self.get(q.format(G.PROD + ".git")), "")
        rc, c, fed, out, err = self.cred(["auth", "git-credential", "get"], q.format(G.PROD + ".git"))
        self.assertNotIn(G.FAKE[G.PROD], out); self.assertNotEqual(self.idt(c), G.FAKE[G.PROD])
        self.assertIn(G.FAKE[G.PAN], self.get(q.format(G.PAN + ".git")))   # own repos unchanged
        self.on("gh-app-product-writes-enabled")
        self.assertIn(G.FAKE[G.PROD], self.get(q.format(G.PROD + ".git")))
        rc, c, fed, out, err = self.cred(["auth", "git-credential", "get"], q.format(G.PROD + ".git")); self.assertIn(G.FAKE[G.PROD], out)
        os.remove(G.F("gh-app-product-writes-enabled"))


if __name__ == "__main__": unittest.main()
