#!/usr/bin/env python3
"""G2 (11-05, Jasper): product-repo CI reruns (gh run rerun, REST POST actions/runs/N/rerun(-failed-jobs), actions/jobs/N/rerun) go
through the PAT; every other product write keeps the App, merges stay refused, look-alikes never get the PAT. Mocked: no token."""
import io, os, sys, types, unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); BIN = os.path.join(os.path.dirname(HERE), "bin")
sys.path.insert(0, BIN); sys.path.insert(0, HERE)
import isolation  # noqa: E402
isolation.sandbox_state()
import lib  # noqa: E402

APP = "ghs_" + "A" * 36; PAT = "ghp_" + "P" * 36
PROD, PAN = "xindong/mivo-canvas-plugin", "PraiseZhu/panoptes"
FLAGS = ["gh-app-enabled", "gh-app-product-enabled", "gh-app-writes-enabled", "gh-app-product-writes-enabled", "gh-app-wrapper-enabled"]
RERUNS = [["run", "rerun", "123", "-R", PROD], ["run", "rerun", "123", "-R", PROD, "--failed"], ["run", "rerun", "-R", PROD, "--job", "456"],
          ["run", "rerun", "123", "--repo=" + PROD],
          ["api", "-X", "POST", f"repos/{PROD}/actions/runs/123/rerun"], ["api", "--method", "POST", f"/repos/{PROD}/actions/runs/123/rerun-failed-jobs"],
          ["api", "-X", "POST", f"repos/{PROD}/actions/jobs/456/rerun"]]
LOOKALIKES = [["run", "cancel", "123", "-R", PROD], ["run", "delete", "123", "-R", PROD], ["api", "-X", "POST", f"repos/{PROD}/actions/runs/123/cancel"],
              ["api", f"repos/{PROD}/actions/runs/123/rerun"], ["api", "-X", "GET", f"repos/{PROD}/actions/jobs/456/rerun"],
              ["run", "rerun", "123", "-R", PAN], ["api", "-X", "POST", f"repos/{PAN}/actions/runs/123/rerun"],
              ["run", "rerun", "123", "-R", "xindong/other"], ["run", "rerun", "123"],
              ["api", "-X", "POST", f"repos/{PROD}/actions/runs/123/rerun/extra"]]


class Base(unittest.TestCase):
    def setUp(self):
        os.makedirs(lib.PI_STATE, exist_ok=True)
        for f in FLAGS: open(f"{lib.PI_STATE}/{f}", "w").close()
        g = types.SimpleNamespace(token=lambda r: APP, invalidate=lambda r: None, mask=str, installation=lambda r: 1, NoInstallation=KeyError)
        self.p = mock.patch.dict(sys.modules, {"ghtoken": g}); self.p.start()
        self.e = mock.patch.dict(os.environ, {"GITHUB_TOKEN": PAT}); self.e.start()
    def tearDown(self):
        self.p.stop(); self.e.stop()
        for f in FLAGS:
            if os.path.exists(f"{lib.PI_STATE}/{f}"): os.remove(f"{lib.PI_STATE}/{f}")
    def lib_token(self, args):
        seen = []
        run = lambda cmd, **kw: seen.append(kw.get("env")) or types.SimpleNamespace(returncode=0, stdout="{}", stderr="")
        with mock.patch.object(lib.subprocess, "run", run), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            lib.gh(args, js=False)
        env = seen[-1] if seen else None
        return None if env is None else env.get("GH_TOKEN")
    def wrap(self, argv):
        import gh_wrapper as W
        calls = []
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            rc = W.main(argv, env={"PATH": "", "PRAISE_QA_REAL_GH": "/real/gh", "GITHUB_TOKEN": PAT, "GH_TOKEN": "ghs_" + "I" * 36},
                        execve=lambda p, a, e: calls.append(e), run=lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="", stderr=""), caller=[])
        return rc, (calls[-1] if calls else None)


class Rerun(Base):
    def test_reruns_get_the_pat_in_lib(self):
        for a in RERUNS: self.assertEqual(self.lib_token(a), PAT, a)

    def test_reruns_get_the_pat_in_the_wrapper(self):
        for a in RERUNS:
            rc, env = self.wrap(a)
            self.assertIsNotNone(env, a); self.assertEqual(env.get("GH_TOKEN"), PAT, a); self.assertNotIn("GITHUB_TOKEN", env)

    def test_lookalikes_never_get_the_pat(self):
        for a in LOOKALIKES: self.assertFalse(lib.pat_rerun(a), a)
        for a in (["api", "-X", "POST", f"repos/{PROD}/actions/runs/123/cancel"], ["api", f"repos/{PROD}/actions/runs/123/rerun"],
                  ["run", "rerun", "123", "-R", PAN], ["api", "-X", "POST", f"repos/{PAN}/actions/runs/123/rerun"]):
            self.assertNotEqual(self.lib_token(a), PAT, a)

    def test_other_product_writes_keep_the_app(self):
        for a in (["pr", "create", "-R", PROD, "--draft", "--title", "t"], ["pr", "ready", "5", "-R", PROD],
                  ["api", "-X", "POST", f"repos/{PROD}/issues/1/comments", "-f", "body=x"]):
            self.assertEqual(self.lib_token(a), APP, a)

    def test_merges_still_refused(self):
        for a in (["pr", "merge", "5", "-R", PROD], ["api", "-X", "PUT", f"repos/{PROD}/pulls/5/merge"]):
            with self.assertRaises(lib.RedLine): self.lib_token(a)
            rc, env = self.wrap(a); self.assertEqual((rc, env), (4, None), a)
        self.assertFalse(lib.pat_rerun(["pr", "merge", "5", "-R", PROD]))


if __name__ == "__main__": unittest.main()
