#!/usr/bin/env python3
"""11-04 gh-app-1104 P1: lib.gh() sends PraiseZhu/panoptes READS through the Praise QA App token while $PI_STATE/gh-app-enabled exists;
removing the flag falls back to the PAT (default gh login). Writes, other repos, @me / user calls and mint failures stay on the PAT.
The App's rate-limit marker is its own file. ghtoken and gh are mocked: no network, no real key or token."""
import io, json, os, sys, types, unittest
from contextlib import redirect_stdout
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin")); sys.path.insert(0, HERE)
import isolation  # noqa: E402
isolation.sandbox_state()
import lib  # noqa: E402

FAKE = "ghs_" + "A" * 36
PAN = "PraiseZhu/panoptes"


class T(unittest.TestCase):
    def setUp(self):
        self.runs = []; self.flag = lib.gh_app_flag()
        for f in (self.flag, lib.GH_RL_FILE, lib.gh_app_rl_file()):
            if os.path.exists(f): os.remove(f)
        self.tok = mock.patch.dict(sys.modules, {"ghtoken": types.SimpleNamespace(token=lambda repo: FAKE, invalidate=lambda repo: None, mask=lambda s: str(s).replace(FAKE, "ghs_***"))})
        self.tok.start()
        self.env = mock.patch.dict(os.environ, {"GITHUB_TOKEN": "pat-from-env"}); self.env.start()

    def tearDown(self):
        self.tok.stop(); self.env.stop()
        for f in (self.flag, lib.GH_RL_FILE, lib.gh_app_rl_file()):
            if os.path.exists(f): os.remove(f)

    def run_gh(self, args, rc=0, err=""):
        def fake(cmd, **kw): self.runs.append(kw.get("env")); return types.SimpleNamespace(returncode=rc, stdout="[]", stderr=err)
        with mock.patch.object(lib.subprocess, "run", fake), redirect_stdout(io.StringIO()) as out:
            try: lib.gh(args)
            except Exception as e: return e, out.getvalue()
        return None, out.getvalue()

    def flag_on(self): open(self.flag, "w").close()

    def test_flag_on_panoptes_read_uses_app_token(self):
        self.flag_on()
        self.run_gh(["pr", "list", "-R", PAN, "--json", "number"])
        env = self.runs[-1]
        self.assertIsNotNone(env); self.assertEqual(env["GH_TOKEN"], FAKE); self.assertNotIn("GITHUB_TOKEN", env)
        self.run_gh(["api", f"repos/{PAN}/pulls?state=open"]); self.assertEqual(self.runs[-1]["GH_TOKEN"], FAKE)

    def test_removing_the_flag_falls_back_to_pat(self):
        self.flag_on(); self.run_gh(["pr", "list", "-R", PAN]); self.assertEqual(self.runs[-1]["GH_TOKEN"], FAKE)
        os.remove(self.flag); self.run_gh(["pr", "list", "-R", PAN])
        self.assertIsNone(self.runs[-1], "flag removed: inherit the default gh login (PAT)")

    def test_writes_other_repos_and_identity_calls_stay_on_pat(self):
        self.flag_on()
        for args in (["pr", "create", "-R", PAN, "--title", "t"], ["api", "-X", "POST", f"repos/{PAN}/issues/1/comments", "-f", "body=x"],
                     ["pr", "list", "-R", "xindong/mivo-canvas-plugin"], ["pr", "list", "-R", "someone/else"],
                     ):
            self.run_gh(args); self.assertIsNone(self.runs[-1], args)
        # G5 (11-05): @me -> the bot through the App; bare `api user` is answered locally (praise-qa[bot]), nothing sent
        self.run_gh(["pr", "list", "-R", PAN, "--author", "@me"]); self.assertEqual(self.runs[-1]["GH_TOKEN"], FAKE)
        n = len(self.runs); self.run_gh(["api", "user"]); self.assertEqual(len(self.runs), n)

    def test_mint_failure_falls_back_to_pat_without_leaking(self):
        self.flag_on()
        def boom(repo): raise RuntimeError(f"HTTP 401 {FAKE}")
        sys.modules["ghtoken"].token = boom
        _, out = self.run_gh(["pr", "list", "-R", PAN])
        self.assertIsNone(self.runs[-1]); self.assertIn("using the default gh login", out); self.assertNotIn(FAKE, out)

    def test_app_has_its_own_rate_limit_marker(self):
        self.flag_on()
        lib.write_atomic(lib.GH_RL_FILE, json.dumps({"since": lib._epoch(), "until": lib._epoch() + 600}))   # PAT pool exhausted
        e, _ = self.run_gh(["pr", "list", "-R", PAN]); self.assertIsNone(e); self.assertEqual(self.runs[-1]["GH_TOKEN"], FAKE)
        e, _ = self.run_gh(["pr", "list", "-R", "xindong/mivo-canvas-plugin"]); self.assertIsInstance(e, lib.GhRateLimited)   # PAT call still skipped
        os.remove(lib.GH_RL_FILE)
        with mock.patch.object(lib, "_gh_rl_reset", lambda err, env=None: lib._epoch() + 300):
            e, _ = self.run_gh(["pr", "list", "-R", PAN], rc=1, err="HTTP 403: API rate limit exceeded for installation")
        self.assertIsInstance(e, lib.GhRateLimited)
        self.assertTrue(os.path.exists(lib.gh_app_rl_file())); self.assertFalse(os.path.exists(lib.GH_RL_FILE), "App limit must not block PAT calls")


if __name__ == "__main__": unittest.main()
