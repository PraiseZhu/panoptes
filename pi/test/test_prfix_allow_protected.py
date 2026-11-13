#!/usr/bin/env python3
"""prfix --allow-protected: exact-path allow for PROTECTED_RE, only with --qa.
Run: cd bin && python3 ../test/test_prfix_allow_protected.py"""
import os as _os
_os.environ.setdefault("PRFIX_OWNERS", "*")
for _k in [k for k in _os.environ if k.startswith("GIT_")]: del _os.environ[_k]
import argparse, io, json, os, shutil, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
sys.path.insert(0, HERE)
import prfix  # noqa: E402
from test_prfix_unit import TestFixOneEndToEnd, wf  # noqa: E402
from test_prbot_autonomy import TestRevise  # noqa: E402

GATE = "cindyplugin/src/__tests__/chatAttachmentBoundary.guard.test.ts"
GATE2 = "src/__tests__/other.policy.test.ts"
OTHER = "src/foo.js"  # not a gate file


def _reset_allow():
    prfix.A_ALLOW["sensitive"] = False
    prfix.A_ALLOW["protected"] = []


class AllowTmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="allow-prot-")
        self.log = os.path.join(self.tmp, "allow-protected.jsonl")
        self.saved_log = getattr(prfix, "ALLOW_PROTECTED_LOG", None)
        prfix.ALLOW_PROTECTED_LOG = self.log
        _reset_allow()

    def tearDown(self):
        if self.saved_log is None:
            prfix.__dict__.pop("ALLOW_PROTECTED_LOG", None)
        else:
            prfix.ALLOW_PROTECTED_LOG = self.saved_log
        _reset_allow()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def records(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f: return [json.loads(l) for l in f if l.strip()]


class TestNormAndGate(AllowTmp):
    def test_exact_path_is_allowed_and_logged(self):
        let = prfix.gate_protected([GATE, OTHER], "修复", ticket="1977:mq1-abc", allowed=[GATE])
        self.assertEqual(let, [GATE])
        rec = self.records()
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec[0]["ticket"], "1977:mq1-abc")
        self.assertEqual(rec[0]["path"], GATE)
        self.assertIn("ts", rec[0])
        self.assertTrue(rec[0]["ts"])  # ISO time

    def test_unlisted_path_still_hard_stops(self):
        with self.assertRaises(prfix.Deterministic) as cm:
            prfix.gate_protected([GATE], "修复", ticket="1977:x", allowed=[])
        self.assertIn("PROTECTED_RE", str(cm.exception))
        self.assertIn("硬停不开 PR", str(cm.exception))
        self.assertIn(GATE, str(cm.exception))
        self.assertEqual(self.records(), [])

    def test_mixed_allowed_and_unlisted_stops_only_unlisted(self):
        with self.assertRaises(prfix.Deterministic) as cm:
            prfix.gate_protected([GATE, GATE2], "修复", ticket="1977:x", allowed=[GATE])
        self.assertIn(GATE2, str(cm.exception))
        self.assertNotIn(GATE + "'", str(cm.exception))
        self.assertIn(GATE2, str(cm.exception))
        # the allowed one was let through and logged before the stop
        rec = self.records()
        self.assertEqual([r["path"] for r in rec], [GATE])
        # leftover in the error is only the unlisted file (list truncated form)
        self.assertIn(GATE2, str(cm.exception))

    def test_wildcard_is_not_a_match(self):
        with self.assertRaises(prfix.Deterministic) as cm:
            prfix.gate_protected([GATE], "修复", ticket="1977:x",
                                 allowed=["cindyplugin/src/__tests__/*", "*guard.test.ts", "**/chatAttachmentBoundary.guard.test.ts"])
        self.assertIn(GATE, str(cm.exception))
        self.assertEqual(self.records(), [])

    def test_match_is_after_normalization(self):
        let = prfix.gate_protected(
            [GATE.replace("/", "\\")], "修复", ticket="1977:x",
            allowed=["./" + GATE],
        )
        self.assertEqual([prfix.norm_repo_path(p) for p in let], [GATE])
        self.assertEqual(prfix.norm_repo_path(self.records()[0]["path"]), GATE)

    def test_ci_revise_wording_and_same_rule(self):
        let = prfix.gate_protected([GATE], "CI 修正", ticket="1977:x", allowed=[GATE])
        self.assertEqual(let, [GATE])
        with self.assertRaises(prfix.Deterministic) as cm:
            prfix.gate_protected([GATE2], "CI 修正", ticket="1977:x", allowed=[GATE])
        self.assertIn("硬停不推送", str(cm.exception))
        self.assertIn("CI 修正要改闸门文件", str(cm.exception))

    def test_default_allow_list_is_A_ALLOW(self):
        prfix.A_ALLOW["protected"] = [GATE]
        let = prfix.gate_protected([GATE], "修复", ticket="t")
        self.assertEqual(let, [GATE])
        prfix.A_ALLOW["protected"] = []
        with self.assertRaises(prfix.Deterministic):
            prfix.gate_protected([GATE], "修复", ticket="t")

    def test_nothing_protected_returns_empty_and_does_not_log(self):
        self.assertEqual(prfix.gate_protected([OTHER], "修复", ticket="t", allowed=[GATE]), [])
        self.assertEqual(self.records(), [])


class TestApplyFlags(AllowTmp):
    def _ap(self):
        ap = argparse.ArgumentParser()
        ap.add_argument("--qa", type=int)
        ap.add_argument("--fp")
        ap.add_argument("--revise", type=int)
        ap.add_argument("--allow-protected", action="append")
        ap.add_argument("--allow-sensitive", action="store_true")
        return ap

    def test_without_qa_errors(self):
        ap = self._ap()
        A = ap.parse_args(["--allow-protected", GATE])
        err = io.StringIO()
        import contextlib
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            prfix.apply_allow_flags(A, ap)
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("--allow-protected needs --qa", err.getvalue())
        self.assertEqual(prfix.A_ALLOW["protected"], [])

    def test_revise_alone_with_allow_errors_and_leave_empty(self):
        ap = self._ap()
        A = ap.parse_args(["--revise", "1301", "--allow-protected", GATE])
        err = io.StringIO()
        import contextlib
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            prfix.apply_allow_flags(A, ap)
        self.assertIn("--allow-protected needs --qa", err.getvalue())
        self.assertEqual(prfix.A_ALLOW["protected"], [])

    def test_with_qa_sets_exact_paths_fp_optional(self):
        ap = self._ap()
        A = ap.parse_args(["--qa", "1977", "--allow-protected", GATE, "--allow-protected", GATE2])
        prfix.apply_allow_flags(A, ap)
        self.assertEqual(prfix.A_ALLOW["protected"], [GATE, GATE2])
        _reset_allow()
        A = ap.parse_args(["--qa", "1977", "--fp", "mq1-abc", "--allow-protected", GATE])
        prfix.apply_allow_flags(A, ap)
        self.assertEqual(prfix.A_ALLOW["protected"], [GATE])

    def test_next_and_revise_paths_have_empty_allow(self):
        ap = self._ap()
        prfix.apply_allow_flags(ap.parse_args([]), ap)
        self.assertEqual(prfix.A_ALLOW["protected"], [])
        prfix.apply_allow_flags(ap.parse_args(["--revise", "1301"]), ap)
        self.assertEqual(prfix.A_ALLOW["protected"], [])

    def test_real_cli_without_qa_exits(self):
        import subprocess
        env = {k: os.environ[k] for k in ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TZ", "TMPDIR") if k in os.environ}
        env.update({
            "PI_STATE": self.tmp, "PI_WORK": self.tmp, "PI_WORK_ROOT": self.tmp,
            "PRAISE_QA_KEY": os.path.join(self.tmp, "no-key"),
            "PRFIX_OWNERS": "*", "TZ": "Asia/Shanghai",
        })
        script = os.path.join(os.path.dirname(prfix.__file__), "prfix.py")
        r = subprocess.run([sys.executable, script, "--allow-protected", GATE],
                           capture_output=True, text=True, env=env, cwd=os.path.dirname(script))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("--allow-protected needs --qa", r.stderr)


class TestPrwatchNeverPassesFlag(unittest.TestCase):
    def test_prwatch_launches_without_allow_protected(self):
        path = os.path.join(os.path.dirname(prfix.__file__), "prwatch.py")
        with open(path) as f: src = f.read()
        self.assertNotIn("--allow-protected", src)
        # the two launch sites stay --next and --revise N only
        self.assertIn(', "prfix", "--next"]', src)
        self.assertIn(', "prfix", "--revise", str(n)]', src)

    def test_apply_flags_on_those_argv_leaves_allow_empty(self):
        ap = argparse.ArgumentParser()
        ap.add_argument("--qa", type=int)
        ap.add_argument("--next", action="store_true")
        ap.add_argument("--revise", type=int)
        ap.add_argument("--allow-protected", action="append")
        try:
            prfix.apply_allow_flags(ap.parse_args(["--next"]), ap)
            self.assertEqual(prfix.A_ALLOW["protected"], [])
            prfix.apply_allow_flags(ap.parse_args(["--revise", "9"]), ap)
            self.assertEqual(prfix.A_ALLOW["protected"], [])
        finally:
            _reset_allow()


class TestPrBodyRisk(AllowTmp):
    def test_risk_section_lists_allowed_gate_files(self):
        c = {"qa": 1977, "sev": "P2", "fp": "mq1-abc", "title": "t",
             "finding": {"file": GATE, "line": 82, "symbol": "pointsAtMedia"}}
        res = {"root": f"{GATE}:82 root", "rc_repro": 1, "rc_revert": 1, "related": "ok",
               "pred_pr": 1, "allowed_protected": [GATE], "sensitive": []}
        pf = {"steps": ["lint"], "passed": 1, "total": 1, "seconds": 8}
        body = prfix.pr_body(c, res, [GATE], [], pf, True, None, None)
        self.assertIn("## 风险", body)
        self.assertIn("--allow-protected", body)
        self.assertIn(GATE, body)
        self.assertIn("PROTECTED_RE", body)

    def test_no_allow_omits_the_warning(self):
        c = {"qa": 1, "sev": "P2", "fp": "x", "title": "t", "finding": {"file": "a.js", "line": 1}}
        res = {"root": "a.js:1", "rc_repro": 1, "rc_revert": 1, "pred_pr": 1}
        pf = {"passed": 1, "total": 1, "seconds": 1}
        body = prfix.pr_body(c, res, ["t.js"], ["a.js"], pf, True, None, None)
        self.assertIn("## 风险", body)
        self.assertNotIn("--allow-protected", body)




class TestCreatePath(TestFixOneEndToEnd):
    """fix_one (PR-create gate) uses the same helper."""

    def tearDown(self):
        _reset_allow()
        super().tearDown()

    def _write_gates(self, *paths):
        inner = prfix.pi

        def wrap(tier, cwd, prompt, session=None, **kw):
            r = inner(tier, cwd, prompt, session=session, **kw)
            import re
            m = re.match(r"\[step (\S+)\]", prompt or "")
            if m and m.group(1).startswith("fix"):
                for p in paths:
                    os.makedirs(os.path.dirname(f"{cwd}/{p}"), exist_ok=True)
                    wf(f"{cwd}/{p}", "export {}\n")
            return r

        prfix.pi = wrap

    def test_create_exact_path_allowed_opens_pr_and_lists_risk(self):
        gate = "scripts/verify-foo.sh"
        self._write_gates(gate)
        prfix.A_ALLOW["protected"] = [gate]
        prfix.ALLOW_PROTECTED_LOG = os.path.join(self.tmp, "allow.jsonl")
        out, res = self.run_fix()
        self.assertEqual(out["status"], "pr_action_written", out)
        self.assertEqual(res.get("allowed_protected"), [gate])
        with open(self.acts[-1]["patch"][:-6] + ".md") as f: body = f.read()
        self.assertIn("--allow-protected", body)
        self.assertIn(gate, body)
        with open(prfix.ALLOW_PROTECTED_LOG) as f: rec = [json.loads(l) for l in f if l.strip()]
        self.assertEqual(rec[0]["path"], gate)
        self.assertIn("1178", rec[0]["ticket"])

    def test_create_unlisted_still_hard_stops(self):
        gate, other = "scripts/verify-foo.sh", "scripts/verify-bar.sh"
        self._write_gates(gate, other)
        prfix.A_ALLOW["protected"] = [gate]  # other is not listed
        out, res = self.run_fix()
        self.assertEqual(out["status"], "failed", out)
        self.assertEqual(out["kind"], "deterministic")
        self.assertIn("PROTECTED_RE", out["error"])
        self.assertIn(other, out["error"])
        self.assertNotIn(gate, out["error"])
        self.assertFalse(self.acts)


class TestRevisePath(TestRevise):
    """CI-revise gate uses the same helper."""

    def tearDown(self):
        _reset_allow()
        super().tearDown()

    def _revise_with_gates(self, paths, allowed):
        inner = prfix.pi

        def wrap(tier, cwd, *a, **k):
            r = inner(tier, cwd, *a, **k)
            for p in paths:
                os.makedirs(os.path.dirname(f"{cwd}/{p}"), exist_ok=True)
                wf(f"{cwd}/{p}", "export {}\n")
            return r

        prfix.pi = wrap
        prfix.A_ALLOW["protected"] = list(allowed)
        prfix.ALLOW_PROTECTED_LOG = os.path.join(self.tmp, "allow.jsonl")
        return self.revise()

    def test_revise_exact_path_allowed_is_pushed(self):
        gate = "src/__tests__/foo.guard.test.ts"
        rc, r = self._revise_with_gates([gate], [gate])
        self.assertEqual((rc, r["status"]), (0, "pr_action_written"), r)
        self.assertTrue([a for a in self.acts if a["type"] == "gh.pr.push-fix"])

    def test_revise_unlisted_still_hard_stops(self):
        gate, other = "src/__tests__/foo.guard.test.ts", "src/__tests__/bar.policy.test.ts"
        rc, r = self._revise_with_gates([gate, other], [gate])
        self.assertEqual(r["status"], "failed", r)
        err = r.get("error") or json.dumps(r, ensure_ascii=False)
        self.assertIn("PROTECTED_RE", err)
        self.assertIn(other, err)
        self.assertNotIn(gate, err)
        self.assertFalse([a for a in self.acts if a["type"] == "gh.pr.push-fix"])


def load_tests(loader, standard_tests, pattern):
    """Only this file's new cases — do not re-run imported TestFixOneEndToEnd / TestRevise suites."""
    suite = unittest.TestSuite()
    for cls in (TestNormAndGate, TestApplyFlags, TestPrwatchNeverPassesFlag, TestPrBodyRisk):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    for cls, names in (
        (TestCreatePath, ("test_create_exact_path_allowed_opens_pr_and_lists_risk", "test_create_unlisted_still_hard_stops")),
        (TestRevisePath, ("test_revise_exact_path_allowed_is_pushed", "test_revise_unlisted_still_hard_stops")),
    ):
        for n in names:
            suite.addTest(cls(n))
    return suite


if __name__ == "__main__":
    unittest.main()
