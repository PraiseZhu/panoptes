#!/usr/bin/env python3
"""11-05 G3/G4/G5 (acceptance 1105 failed): G3 pi/bin/panoptes_merge.py (squash + sha lock, panoptes/vigil only, merged_by check),
G4 run-nightly.sh / scripts/gh-app-env.sh (catch-up and nightly env: App tokens only, no PAT), G5 @me / `api user` / gh search through
the App with the bot identity hard-coded. ghtoken, gh, git and GitHub are mocked: no key, no real token, no network."""
import io, json, os, subprocess, sys, tempfile, types, unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); BIN = os.path.join(os.path.dirname(HERE), "bin"); ROOT = os.path.dirname(os.path.dirname(BIN))
sys.path.insert(0, BIN); sys.path.insert(0, HERE)
import isolation  # noqa: E402
isolation.sandbox_state()
import lib  # noqa: E402

FAKE = "ghs_" + "G" * 36
FAKE_PAT = "ghp_" + "P" * 36
PROD, PAN, VIGIL = "xindong/mivo-canvas-plugin", "PraiseZhu/panoptes", "PraiseZhu/vigil"
SHA = "a" * 40
FLAGS = ["gh-app-enabled", "gh-app-wrapper-enabled", "gh-app-nightly-enabled"]


def flagfile(n): return f"{lib.PI_STATE}/{n}"


class Base(unittest.TestCase):
    def setUp(self):
        self.clear(); self.minted = []
        fake = types.SimpleNamespace(token=lambda repo: self.minted.append(repo) or FAKE, invalidate=lambda repo: None, mask=str,
                                     installation=lambda repo: 1, NoInstallation=KeyError)
        self.tok = mock.patch.dict(sys.modules, {"ghtoken": fake}); self.tok.start()
    def tearDown(self): self.tok.stop(); self.clear()
    def clear(self):
        for f in FLAGS:
            if os.path.exists(flagfile(f)): os.remove(flagfile(f))
    def on(self, *names):
        os.makedirs(lib.PI_STATE, exist_ok=True)
        for n in ("gh-app-enabled",) + names: open(flagfile(n), "w").close()


# ---------------- G3 ----------------
class FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


class G3Merge(unittest.TestCase):
    def setUp(self):
        import panoptes_merge as M; self.M = M
    def run_merge(self, argv, merged_by="praise-qa[bot]", merged=True):
        sent, minted = [], []
        def opener(req, timeout=None):
            sent.append({"method": req.get_method(), "url": req.full_url, "body": json.loads(req.data) if req.data else None,
                         "auth": req.get_header("Authorization")})
            if req.get_method() == "PUT": return FakeResp(json.dumps({"merged": merged, "sha": "b" * 40, "message": "ok"}).encode())
            return FakeResp(json.dumps({"merged": merged, "merged_by": {"login": merged_by}}).encode())
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = self.M.main(argv, opener=opener, token=lambda repo: minted.append(repo) or FAKE)
        self.assertNotIn(FAKE, out.getvalue() + err.getvalue())   # the token is never printed
        return rc, sent, minted, out.getvalue(), err.getvalue()

    def test_refuses_product_and_other_repos_before_any_token(self):
        for repo in (PROD, "xindong/other", "PraiseZhu/pr-qa-agent-deploy", "someone/panoptes"):
            rc, sent, minted, _, err = self.run_merge(["7", SHA, "-R", repo])
            self.assertEqual((rc, sent, minted), (3, [], []), repo); self.assertIn("refused", err)
        self.assertIn("product", self.run_merge(["7", SHA, "-R", PROD])[4])

    def test_squash_locked_to_head_sha_default_panoptes(self):
        rc, sent, minted, out, _ = self.run_merge(["226", SHA])
        self.assertEqual(rc, 0); self.assertEqual(minted, [PAN])
        put = sent[0]
        self.assertEqual(put["method"], "PUT"); self.assertTrue(put["url"].endswith(f"/repos/{PAN}/pulls/226/merge"))
        self.assertEqual(put["body"], {"merge_method": "squash", "sha": SHA})
        self.assertEqual(put["auth"], f"Bearer {FAKE}")
        self.assertEqual((sent[1]["method"], sent[1]["url"].endswith(f"/repos/{PAN}/pulls/226")), ("GET", True))
        self.assertIn("merged_by=praise-qa[bot]", out)

    def test_vigil_allowed(self):
        rc, sent, minted, _, _ = self.run_merge(["3", SHA, "-R", VIGIL]); self.assertEqual((rc, minted), (0, [VIGIL]))
        self.assertIn(f"/repos/{VIGIL}/pulls/3/merge", sent[0]["url"])

    def test_merged_by_not_bot_exits_nonzero(self):
        rc, _, _, out, err = self.run_merge(["226", SHA], merged_by="PraiseZhu")
        self.assertEqual(rc, 1); self.assertIn("merged_by=PraiseZhu", out); self.assertIn("expected praise-qa[bot]", err)

    def test_not_merged_exits_nonzero(self):
        self.assertEqual(self.run_merge(["226", SHA], merged=False)[0], 1)

    def test_bad_args(self):
        for a in (["226"], ["x", SHA], ["226", "abc123"], ["226", SHA.upper()], ["226", SHA, "-R"]):
            self.assertEqual(self.run_merge(a)[0], 64, a)

    def test_http_error_is_masked(self):
        import urllib.error
        def opener(req, timeout=None): raise urllib.error.HTTPError(req.full_url, 409, "x", {}, io.BytesIO(b'{"message":"Head branch was modified"}'))
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()): rc = self.M.main(["226", SHA], opener=opener, token=lambda r: FAKE)
        self.assertEqual(rc, 1); self.assertIn("409", err.getvalue()); self.assertNotIn(FAKE, err.getvalue())


# ---------------- G4 ----------------
class G4NightlyEnv(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="g4-"); self.st = os.path.join(self.d, "state"); os.makedirs(self.st)
        self.tool = os.path.join(self.d, "ghtoken.py")
        open(self.tool, "w").write("import sys\nassert sys.argv[1] == 'token'\nsys.stdout.write('ghs_' + ('X' if sys.argv[2].startswith('xindong/') else 'O') * 36)\n")
    def tearDown(self): subprocess.run(["rm", "-rf", self.d])
    def flags(self, *names):
        for n in names: open(os.path.join(self.st, n), "w").close()
    def child_env(self, tool=None):
        """source the helper as run-nightly.sh does, then print the env a child (node argus catch-up / nightly) inherits"""
        env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"), "GITHUB_TOKEN": FAKE_PAT, "GH_TOKEN": FAKE_PAT,
               "SOME_OTHER_PAT": "github_pat_" + "Z" * 30, "PANOPTES_GHTOKEN": tool or self.tool}
        sh = f'. "{ROOT}/scripts/gh-app-env.sh" && gh_app_env "{ROOT}" "{self.st}" && exec env -0'
        r = subprocess.run(["bash", "-c", sh], env=env, capture_output=True, text=True)
        return r.returncode, dict(x.split("=", 1) for x in r.stdout.split("\0") if "=" in x), r.stderr

    def test_flags_on_child_env_has_no_pat(self):
        self.flags("gh-app-enabled", "gh-app-nightly-enabled")
        rc, env, _ = self.child_env()
        self.assertEqual(rc, 0)
        pats = [k for k, v in env.items() if v.startswith(("ghp_", "github_pat_", "gho_", "ghu_"))]
        self.assertEqual(pats, []); self.assertNotIn("GITHUB_TOKEN", env)
        self.assertEqual(env["GH_TOKEN"], "ghs_" + "X" * 36); self.assertEqual(env["PRAISE_QA_GH_TOKEN"], env["GH_TOKEN"])
        self.assertEqual(env["PRAISE_QA_GH_REPOS"], PROD); self.assertEqual(env["PRAISE_QA_GH_OWN_TOKEN"], "ghs_" + "O" * 36)

    def test_mint_failure_never_falls_back_to_the_pat(self):
        self.flags("gh-app-enabled", "gh-app-nightly-enabled")
        bad = os.path.join(self.d, "bad.py"); open(bad, "w").write("import sys; sys.exit(2)\n")
        rc, env, err = self.child_env(bad)
        self.assertNotEqual(rc, 0); self.assertEqual(env, {}); self.assertIn("not running on the PAT", err)

    def test_rollback_flag_off_is_the_old_pat(self):
        self.flags("gh-app-enabled")
        rc, env, _ = self.child_env(); self.assertEqual((rc, env["GH_TOKEN"]), (0, FAKE_PAT))

    def test_run_nightly_uses_the_helper_before_catch_up_and_nightly(self):
        s = open(os.path.join(ROOT, "run-nightly.sh")).read()
        self.assertFalse('export GH_TOKEN="$GITHUB_TOKEN"' in s, "run-nightly.sh still exports the PAT as GH_TOKEN")
        i = s.index('gh_app_env "$PANOPTES_DIR"')
        self.assertLess(i, s.index("src/cli.mjs catch-up")); self.assertLess(i, s.index("src/cli.mjs nightly"))

    def test_argus_children_get_the_app_token(self):
        # argus/src/github.mjs ghEnvFor with the env gh_app_env exports: product + PraiseZhu/* + an unlisted repo all carry no PAT
        env = {"PATH": os.environ["PATH"], "GH_TOKEN": "ghs_" + "X" * 36, "PRAISE_QA_GH_TOKEN": "ghs_" + "X" * 36, "PRAISE_QA_GH_REPOS": PROD,
               "PRAISE_QA_GH_OWN_TOKEN": "ghs_" + "O" * 36}
        js = ("import('%s/argus/src/github.mjs').then(m => console.log(JSON.stringify([['pr','list','-R','%s'],['issue','list','-R','PraiseZhu/pr-qa-agent-deploy'],"
              "['run','list']].map(a => { const e = m.ghEnvFor(a); return [e.GH_TOKEN, e.GITHUB_TOKEN ?? null] }))))" % (ROOT, PROD))
        r = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, cwd=os.path.join(ROOT, "argus"), env=env)
        if r.returncode and "Cannot find" in r.stderr: self.skipTest("argus deps not installed")
        v = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(v, [["ghs_" + "X" * 36, None], ["ghs_" + "O" * 36, None], ["ghs_" + "X" * 36, None]])


# ---------------- G5 ----------------
class G5Identity(Base):
    def gh(self, args, js=True):
        seen = []
        def run(cmd, **kw): seen.append((cmd, kw.get("env"))); return types.SimpleNamespace(returncode=0, stdout="[]", stderr="")
        with mock.patch.object(lib.subprocess, "run", run), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            out = lib.gh(args, js=js)
        return out, seen

    def test_api_user_is_the_bot_and_nothing_is_sent(self):
        self.on()
        out, seen = self.gh(["api", "user"]); self.assertEqual((out["login"], out["type"], seen), ("praise-qa[bot]", "Bot", []))
        out, seen = self.gh(["api", "/user", "--jq", ".login"], js=False); self.assertEqual((out.strip(), seen), ("praise-qa[bot]", []))

    def test_at_me_rewritten_to_the_bot_and_sent_with_the_app(self):
        self.on()
        _, seen = self.gh(["pr", "list", "-R", PAN, "--author", "@me", "--json", "number"])
        cmd, env = seen[-1]
        self.assertNotIn("@me", " ".join(cmd)); self.assertIn("app/praise-qa", cmd); self.assertEqual(env["GH_TOKEN"], FAKE)
        _, seen = self.gh(["pr", "list", "-R", PAN, "--search", "author:@me review-requested:@me is:open", "--json", "number"])
        q = seen[-1][0][seen[-1][0].index("--search") + 1]
        self.assertEqual(q, "author:app/praise-qa review-requested:praise-qa[bot] is:open"); self.assertEqual(seen[-1][1]["GH_TOKEN"], FAKE)

    def test_gh_search_goes_through_the_app(self):
        self.on()
        _, seen = self.gh(["search", "prs", "--author", "@me", "--state", "open", "--json", "number"])
        cmd, env = seen[-1]
        self.assertEqual(env["GH_TOKEN"], FAKE); self.assertNotIn("GITHUB_TOKEN", env); self.assertIn("app/praise-qa", cmd)
        self.assertEqual(self.minted[-1], PAN)
        _, seen = self.gh(["search", "issues", "--repo", VIGIL, "x", "--json", "number"]); self.assertEqual(self.minted[-1], VIGIL)

    def test_flag_off_unchanged(self):
        _, seen = self.gh(["api", "user"]); self.assertEqual(seen[-1][0], ["gh", "api", "user"]); self.assertIsNone(seen[-1][1])

    def test_wrapper_api_user_and_search(self):
        import gh_wrapper as W
        self.on("gh-app-wrapper-enabled")
        calls, out = [], io.StringIO()
        fake_run = lambda cmd, **k: types.SimpleNamespace(returncode=1, stdout="", stderr="")
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = W.main(["api", "user", "--jq", ".login"], env={"PATH": "", "PRAISE_QA_REAL_GH": "/real/gh", "GITHUB_TOKEN": FAKE_PAT},
                        execve=lambda p, a, e: calls.append((p, a, e)), run=fake_run, caller=[])
        self.assertEqual((rc, out.getvalue().strip(), calls), (0, "praise-qa[bot]", []))
        with redirect_stderr(io.StringIO()):
            W.main(["search", "prs", "--author=@me"], env={"PATH": "", "PRAISE_QA_REAL_GH": "/real/gh", "GITHUB_TOKEN": FAKE_PAT},
                   execve=lambda p, a, e: calls.append((p, a, e)), run=fake_run, caller=[])
        _, a, e = calls[-1]
        self.assertEqual(a[1:], ["search", "prs", "--author=app/praise-qa"]); self.assertEqual(e["GH_TOKEN"], FAKE); self.assertNotIn("GITHUB_TOKEN", e)
        self.assertEqual(self.minted[-1], PAN)


if __name__ == "__main__": unittest.main()
