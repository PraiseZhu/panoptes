#!/usr/bin/env python3
"""11-04 gh-app-1104 P3 (PM 17:00): xindong/mivo-canvas-plugin READS use the Praise QA App while $PI_STATE/gh-app-enabled AND
gh-app-product-enabled exist (lib.gh and prfix._gh_json). Product WRITES and every @me / author:@me / user call stay on the PAT; removing
either flag falls back to the PAT. pr-qa-agent-deploy stays on the PAT. ghtoken and gh are mocked."""
import io, os, sys, types, unittest
from contextlib import redirect_stdout
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin")); sys.path.insert(0, HERE)
import isolation  # noqa: E402
isolation.sandbox_state()
sys.modules.setdefault("merger", types.SimpleNamespace(FIELDS="number", blockers=lambda p: []))
import lib  # noqa: E402

FAKE = "ghs_" + "P" * 36
PROD = "xindong/mivo-canvas-plugin"


class T(unittest.TestCase):
    def setUp(self):
        self.flags = (lib.gh_app_flag(), lib.gh_app_product_flag())
        for f in self.flags + (lib.GH_RL_FILE, lib.gh_app_rl_file()):
            if os.path.exists(f): os.remove(f)
        self.tok = mock.patch.dict(sys.modules, {"ghtoken": types.SimpleNamespace(token=lambda repo: FAKE, invalidate=lambda repo: None, mask=str)}); self.tok.start()
    def tearDown(self):
        self.tok.stop()
        for f in self.flags:
            if os.path.exists(f): os.remove(f)
    def on(self):
        for f in self.flags: open(f, "w").close()
    def env_of(self, args):
        seen = []
        with mock.patch.object(lib.subprocess, "run", lambda cmd, **kw: seen.append(kw.get("env")) or types.SimpleNamespace(returncode=0, stdout="[]", stderr="")), \
             redirect_stdout(io.StringIO()):
            lib.gh(args)
        return seen[-1]

    def test_product_reads_use_app_with_both_flags(self):
        self.on()
        for a in (["pr", "list", "-R", PROD, "--state", "merged"], ["api", f"repos/{PROD}/pulls?state=closed"], ["pr", "view", "1", "-R", PROD]):
            self.assertEqual((self.env_of(a) or {}).get("GH_TOKEN"), FAKE, a)

    def test_removing_either_flag_falls_back_to_pat(self):
        for drop in self.flags:
            self.on(); os.remove(drop)
            self.assertIsNone(self.env_of(["pr", "list", "-R", PROD]), drop)

    def test_product_writes_and_me_searches_stay_on_pat(self):
        self.on()
        for a in (["pr", "create", "-R", PROD, "--draft", "--title", "t"], ["api", "-X", "POST", f"repos/{PROD}/issues/1/comments", "-f", "body=x"],
                  ["pr", "list", "-R", PROD, "--search", '"fp" in:body author:@me'], ["issue", "list", "-R", "PraiseZhu/pr-qa-agent-deploy"]):
            self.assertIsNone(self.env_of(a), a)

    def test_prfix_reads_follow_the_flag(self):
        import prfix as P
        seen = []
        fake = lambda cmd, **kw: seen.append(kw.get("env")) or types.SimpleNamespace(returncode=0, stdout="{}", stderr="")
        with mock.patch.object(P.subprocess, "run", fake), redirect_stdout(io.StringIO()):
            self.on(); P._gh_json(["pr", "view", "1", "-R", PROD, "--json", "baseRefName"], 30)
            os.remove(self.flags[1]); P._gh_json(["pr", "view", "1", "-R", PROD, "--json", "baseRefName"], 30)
        self.assertEqual(seen[0]["GH_TOKEN"], FAKE); self.assertIsNone(seen[1])


if __name__ == "__main__": unittest.main()
