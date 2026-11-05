#!/usr/bin/env python3
"""11-05 audit / PAT-attribution log lines (logging only): panoptes_merge audit JSONL (success, failure, log write failure),
lib.gh_env PAT fallback reasons, gh_wrapper allowlist full argv, credcheck / opsenv direct real-gh source. Everything mocked."""
import io, json, os, sys, types, unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); BIN = os.path.join(os.path.dirname(HERE), "bin")
sys.path.insert(0, BIN); sys.path.insert(0, HERE)
import isolation  # noqa: E402
isolation.sandbox_state()
import lib  # noqa: E402

FAKE = "ghs_" + "G" * 36
SHA = "a" * 40


def lines(path):
    if not os.path.exists(path): return []
    return [json.loads(l) for l in open(path)]


def rm(path):
    if os.path.exists(path): os.remove(path)


class FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


class MergeAudit(unittest.TestCase):
    def setUp(self):
        import panoptes_merge as M; self.M = M; rm(M.audit_file())
    def run_merge(self, put_fail=False):
        import urllib.error
        def opener(req, timeout=None):
            if req.get_method() == "PUT":
                if put_fail: raise urllib.error.HTTPError(req.full_url, 409, "x", {}, io.BytesIO(b'{"message":"Head branch was modified"}'))
                return FakeResp(json.dumps({"merged": True, "sha": "b" * 40}).encode())
            return FakeResp(json.dumps({"merged": True, "merged_by": {"login": lib.app_login()}}).encode())
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err), mock.patch.dict(os.environ, {"PANOPTES_MERGE_CALLER": "tester"}):
            rc = self.M.main(["226", SHA], opener=opener, token=lambda r: FAKE)
        return rc, err.getvalue()
    def test_success_line(self):
        rc, _ = self.run_merge()
        self.assertEqual(rc, 0)
        [l] = lines(self.M.audit_file())
        self.assertEqual({k: l[k] for k in ("pr", "head_sha", "merge_commit", "merged_by", "caller", "result", "error")},
                         {"pr": "PraiseZhu/panoptes#226", "head_sha": SHA, "merge_commit": "b" * 40, "merged_by": lib.app_login(),
                          "caller": "tester", "result": "success", "error": None})
        self.assertTrue(l["ts"])
    def test_failure_line(self):
        rc, _ = self.run_merge(put_fail=True)
        self.assertEqual(rc, 1)
        [l] = lines(self.M.audit_file())
        self.assertEqual((l["result"], l["merge_commit"], l["merged_by"]), ("failure", None, None))
        self.assertIn("409", l["error"]); self.assertNotIn(FAKE, json.dumps(l))
    def test_log_write_failure_keeps_outcome(self):
        with mock.patch("builtins.open", side_effect=OSError("disk full")):
            rc, err = self.run_merge()
        self.assertEqual(rc, 0); self.assertIn("audit log write failed", err)


class PatFallbackLog(unittest.TestCase):
    def setUp(self):
        rm(lib.pat_log_file()); self.clear()
        fake = types.SimpleNamespace(token=lambda repo: FAKE, mask=str, invalidate=lambda r: None)
        self.tok = mock.patch.dict(sys.modules, {"ghtoken": fake}); self.tok.start()
    def tearDown(self): self.tok.stop(); self.clear()
    def clear(self):
        for n in ("gh-app-enabled", "gh-app-writes-enabled"): rm(f"{lib.PI_STATE}/{n}")
    def on(self):
        os.makedirs(lib.PI_STATE, exist_ok=True); open(f"{lib.PI_STATE}/gh-app-enabled", "w").close()
    def reason(self, args):
        with redirect_stdout(io.StringIO()): ident, _ = lib.gh_env(args)
        self.assertEqual(ident, "pat")
        l = lines(lib.pat_log_file())[-1]
        self.assertEqual(l["argv"], args); self.assertNotIn(FAKE, json.dumps(l)); self.assertIn("source", l); self.assertIn("caller", l)
        return l["reason"]
    def test_switch_off(self): self.assertEqual(self.reason(["pr", "list", "-R", "PraiseZhu/panoptes"]), "switch off")
    def test_at_me(self): self.on(); self.assertEqual(self.reason(["pr", "list", "-R", "PraiseZhu/panoptes", "--author", "@me"]), "@me")
    def test_user(self): self.on(); self.assertEqual(self.reason(["api", "/user/repos"]), "/user")
    def test_non_app_repo(self): self.on(); self.assertEqual(self.reason(["pr", "list", "-R", "someone/else"]), "non-App repo")
    def test_mint_failed(self):
        self.on()
        def boom(repo): raise RuntimeError("mint")
        sys.modules["ghtoken"].token = boom
        with redirect_stdout(io.StringIO()), self.assertRaises(lib.GhAppMintFailed): lib.gh_env(["pr", "list", "-R", "PraiseZhu/panoptes"])
        self.assertEqual(lines(lib.pat_log_file()), [], "11-05 R2: a mint failure is not a PAT use any more")
    def test_app_not_logged(self):
        self.on()
        with redirect_stdout(io.StringIO()): self.assertEqual(lib.gh_env(["pr", "list", "-R", "PraiseZhu/panoptes"])[0], "app")
        self.assertEqual(lines(lib.pat_log_file()), [])
    def test_token_masked(self):
        lib.log_pat("x", ["api", "-H", "Authorization: token ghp_" + "Z" * 36])
        self.assertNotIn("Z" * 36, open(lib.pat_log_file()).read())
    def test_write_failure_warns(self):
        err = io.StringIO()
        with mock.patch("builtins.open", side_effect=OSError("ro")), redirect_stderr(err): lib.log_pat("x", ["a"])
        self.assertIn("pat log: write failed", err.getvalue())


class AllowlistLog(unittest.TestCase):
    def test_full_argv_and_file_created(self):
        import gh_wrapper as W
        rm(W.allowlist_log())
        argv = ["api", "repos/xindong/mivo-canvas-plugin/pulls/1", "--jq", ".head.sha"]
        with redirect_stderr(io.StringIO()): W.log_allowlisted("lifeline-tick", argv)
        [l] = lines(W.allowlist_log())
        self.assertEqual((l["caller"], l["argv"]), ("lifeline-tick", argv))


class DefaultLoginLog(unittest.TestCase):
    """11-05 R2 (e): the remaining default-login (PraiseZhu) real-gh paths leave a gh-pat.jsonl line: reason, caller, no token"""
    def setUp(self):
        rm(lib.pat_log_file())
        import importlib.util   # a fresh lib: other test modules patch lib.ub_lookup / gql_node_repos module-wide
        spec = importlib.util.spec_from_file_location("lib_fresh_dl", os.path.join(BIN, "lib.py")); self.L = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.L)
    def last(self, L=lib): return lines(L.pat_log_file())[-1]
    def test_gql_node_repos(self):
        r = mock.Mock(stdout='{"data":{"nodes":[{"nameWithOwner":"a/b"}]}}')
        with mock.patch.object(self.L.subprocess, "run", return_value=r), mock.patch.object(self.L, "real_gh", return_value="/usr/bin/gh"):
            self.assertEqual(self.L.gql_node_repos(["R_1"]), ["a/b"])
        l = self.last(self.L); self.assertIn("gql_node_repos", l["reason"]); self.assertEqual(l["source"], "lib.gql_node_repos"); self.assertIn("R_1", l["argv"])
    def test_ub_lookup(self):
        run = mock.Mock(return_value=mock.Mock(returncode=0, stdout="{}"))
        with mock.patch.object(self.L, "real_gh", return_value="/usr/bin/gh"): self.L.ub_lookup(7, run=run)
        l = self.last(self.L); self.assertIn("ub_lookup", l["reason"]); self.assertEqual(l["argv"][:3], ["pr", "view", "7"])
    def wrapper(self, argv, stdin=None, on=False, install=False):
        import gh_wrapper as W
        ex = mock.Mock(return_value=0)
        fake = types.SimpleNamespace(installation=lambda r: 1 if install else (_ for _ in ()).throw(fake.NoInstallation()),
                                     NoInstallation=type("NI", (Exception,), {}), token=lambda r: FAKE, mask=str)
        with mock.patch.dict(sys.modules, {"ghtoken": fake}), mock.patch.object(W, "wrapper_on", return_value=on), \
             mock.patch.object(W, "refeed_stdin"), mock.patch.object(W, "real_gh", return_value="/usr/bin/gh"), \
             mock.patch.object(W, "script_of", return_value="/x/caller.py"), redirect_stdout(io.StringIO()) as out:
            W.main(argv, env={"PATH": "/usr/bin"}, execve=ex, run=mock.Mock(return_value=mock.Mock(returncode=1, stdout="")), caller=[], stdin=stdin)
        return ex, out.getvalue()
    def test_wrapper_credential_no_install(self):
        ex, out = self.wrapper(["auth", "git-credential", "get"], stdin="protocol=https\nhost=github.com\npath=aj0928/aj0928-handoff.git\n", on=True)
        self.assertTrue(ex.called); l = self.last()
        self.assertEqual((l["reason"], l["source"], l["caller"]), ("credential: no App installation", "gh_wrapper", "/x/caller.py"))
        self.assertEqual(l["argv"], ["git", "credential", "get", "aj0928/aj0928-handoff"]); self.assertNotIn(FAKE, json.dumps(l))
    def test_wrapper_credential_app_not_logged(self):
        ex, out = self.wrapper(["auth", "git-credential", "get"], stdin="protocol=https\nhost=github.com\npath=PraiseZhu/panoptes.git\n", on=True, install=True)
        self.assertFalse(ex.called); self.assertIn("password=", out); self.assertEqual(lines(lib.pat_log_file()), [])
    def test_wrapper_switch_off(self):
        ex, _ = self.wrapper(["pr", "list", "-R", "PraiseZhu/panoptes"])
        self.assertTrue(ex.called); l = self.last(); self.assertEqual((l["reason"], l["caller"]), ("switch off", "/x/caller.py"))


class DirectRealGh(unittest.TestCase):
    def test_credcheck_and_opsenv_log_source(self):
        src = {n: open(os.path.join(BIN, f"{n}.py")).read() for n in ("credcheck", "opsenv")}
        for n, s in src.items(): self.assertIn(f'source="{n}"', s)
        for n, s in src.items():   # the log call sits right before the real-gh subprocess.run (importing credcheck needs box files)
            k = s.index("log_pat("); self.assertLess(k, s.index("subprocess.run([real_gh()")); self.assertIn("[1:]", s[k:k + 200])
        rm(lib.pat_log_file())
        lib.log_pat("direct real gh", ["auth", "status"], source="opsenv")
        l = lines(lib.pat_log_file())[-1]
        self.assertEqual((l["reason"], l["source"], l["argv"]), ("direct real gh", "opsenv", ["auth", "status"]))

if __name__ == "__main__": unittest.main()
