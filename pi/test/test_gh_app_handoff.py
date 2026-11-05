#!/usr/bin/env python3
"""11-05: third-party handoff repos (aj0928/aj0928-handoff, zhongxingtian-ai/handover-mivo) go through the Praise QA App
after those orgs installed it. Reads and writes (issue list/view/create/comment/close, contents, clone/push credentials)
use the App while the flags are on. Mint failure does not fall back to the PAT; flag off rolls back to the PAT; a repo
without an installation stays on the PAT. ghtoken, gh and git are mocked: no network, no real key or token."""
import io, os, sys, types, unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); BIN = os.path.join(os.path.dirname(HERE), "bin")
sys.path.insert(0, BIN); sys.path.insert(0, HERE)
import isolation  # noqa: E402
isolation.sandbox_state()
import lib  # noqa: E402

FAKE = "ghs_" + "H" * 36
AJ, ZX = "aj0928/aj0928-handoff", "zhongxingtian-ai/handover-mivo"
AJ_INST, ZX_INST = 178902423, 178909971
HANDOFF = (AJ, ZX)
OTHER = "someone/else"


class Base(unittest.TestCase):
    def setUp(self):
        self.runs = []
        self.flags = (lib.gh_app_flag(), lib.gh_app_writes_flag())
        for f in self.flags:
            if os.path.exists(f): os.remove(f)
        self.tok = mock.patch.dict(sys.modules, {"ghtoken": types.SimpleNamespace(
            token=lambda repo: FAKE, invalidate=lambda repo: None, mask=lambda s: str(s).replace(FAKE, "ghs_***"))})
        self.tok.start()

    def tearDown(self):
        self.tok.stop()
        for f in self.flags:
            if os.path.exists(f): os.remove(f)

    def on(self, writes=False):
        open(self.flags[0], "w").close()
        if writes: open(self.flags[1], "w").close()

    def env_of(self, args):
        seen = []
        with mock.patch.object(lib.subprocess, "run", lambda cmd, **kw: seen.append(kw.get("env")) or types.SimpleNamespace(returncode=0, stdout="[]", stderr="")), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            lib.gh(args)
        return seen[-1] if seen else None

    def app(self, args):
        env = self.env_of(args)
        return env is not None and env.get("GH_TOKEN") == FAKE


class Installation(unittest.TestCase):
    def test_handoff_repos_map_to_their_installations(self):
        sys.modules.pop("ghtoken", None)
        import importlib; G = importlib.import_module("ghtoken")
        for repo, inst in ((AJ, AJ_INST), (ZX, ZX_INST), ("PraiseZhu/panoptes", 164262283)):
            try: got = G.installation(repo)
            except G.NoInstallation as e: self.fail(f"expected installation {inst} for {repo}, got NoInstallation: {e}")
            else: self.assertEqual(got, inst, repo)
        with self.assertRaises(G.NoInstallation): G.installation(OTHER)
        with self.assertRaises(G.NoInstallation): G.installation("aj0928/other-repo")
        with self.assertRaises(G.NoInstallation): G.installation("zhongxingtian-ai/other-repo")


class Reads(Base):
    def test_flag_on_handoff_issue_reads_use_app(self):
        self.on()
        for r in HANDOFF:
            for a in (["issue", "list", "-R", r], ["issue", "view", "1", "-R", r],
                      ["api", f"repos/{r}/issues"], ["api", f"repos/{r}/contents/README.md"]):
                self.assertTrue(self.app(a), a)
                self.assertTrue(lib.gh_app_wanted(a), a)

    def test_flag_off_falls_back_to_pat(self):
        for r in HANDOFF:
            a = ["issue", "list", "-R", r]
            self.assertFalse(lib.gh_app_wanted(a), a)
            self.assertIsNone(self.env_of(a), a)
        self.on()
        self.assertTrue(self.app(["issue", "list", "-R", AJ]))
        os.remove(self.flags[0])
        self.assertIsNone(self.env_of(["issue", "list", "-R", AJ]), "master flag off -> PAT")
        self.assertFalse(lib.gh_app_wanted(["issue", "list", "-R", AJ]))

    def test_uninstalled_repo_stays_on_pat(self):
        self.on()
        for a in (["issue", "list", "-R", OTHER], ["issue", "list", "-R", "aj0928/other-repo"],
                  ["pr", "list", "-R", "zhongxingtian-ai/other-repo"]):
            self.assertFalse(lib.gh_app_wanted(a), a)
            self.assertIsNone(self.env_of(a), a)


class Writes(Base):
    WRITES = lambda r: (
        ["issue", "create", "-R", r, "--title", "t", "--body", "b"],
        ["issue", "comment", "1", "-R", r, "--body", "c"],
        ["issue", "close", "1", "-R", r],
        ["api", "-X", "PUT", f"repos/{r}/contents/daily/x.md", "-f", "content=eA=="],
    )

    def test_writes_flag_on_handoff_writes_use_app(self):
        self.on(writes=True)
        for r in HANDOFF:
            for a in Writes.WRITES(r):
                self.assertTrue(self.app(a), a)
                self.assertTrue(lib.gh_app_wanted(a), a)

    def test_removing_writes_flag_puts_writes_back_on_pat_reads_stay_app(self):
        self.on(writes=True); os.remove(self.flags[1])
        for r in HANDOFF:
            for a in Writes.WRITES(r):
                self.assertIsNone(self.env_of(a), a)
            self.assertTrue(self.app(["issue", "list", "-R", r]), r)


class MintFailure(Base):
    def test_mint_failure_raises_and_never_uses_pat(self):
        self.on()
        al = lib.gh_app_mint_alert_file()
        if os.path.exists(al): os.remove(al)
        def boom(repo): raise RuntimeError(f"HTTP 401 {FAKE}")
        sys.modules["ghtoken"].token = boom
        seen = []
        with mock.patch.object(lib.subprocess, "run", lambda *a, **k: seen.append(1) or self.fail("sent")), \
             redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
            with self.assertRaises(lib.GhAppMintFailed): lib.gh(["issue", "list", "-R", AJ])
        self.assertEqual(seen, []); self.assertNotIn(FAKE, out.getvalue())
        self.assertTrue(os.path.exists(al))
        with open(al) as fh: self.assertNotIn(FAKE, fh.read())


class GitCreds(Base):
    def test_git_read_and_push_env_follow_flags(self):
        self.assertIsNone(lib.git_read_env(AJ))
        self.assertEqual(lib.git_push_env(AJ), ([], None))
        self.on()
        e = lib.git_read_env(AJ)
        self.assertIsNotNone(e, "handoff git reads should use the App")
        self.assertEqual(e["GH_TOKEN"], FAKE)
        self.assertEqual(lib.git_push_env(ZX), ([], None), "writes flag off -> PAT for push")
        self.on(writes=True)
        c, env = lib.git_push_env(ZX)
        self.assertEqual(env["GH_TOKEN"], FAKE)
        self.assertIn("credential.helper=!gh auth git-credential", c)
        self.assertIsNone(lib.git_read_env(OTHER))
        self.assertEqual(lib.git_push_env(OTHER), ([], None))

    def test_credential_helper_mints_for_handoff_not_for_uninstalled(self):
        self.tok.stop()
        import importlib, importlib.machinery, importlib.util
        sys.modules.pop("ghtoken", None)
        real = importlib.import_module("ghtoken")
        spec = importlib.util.spec_from_loader("gcp", importlib.machinery.SourceFileLoader("gcp", os.path.join(BIN, "git-credential-praise-qa")))
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        def token(repo):
            if repo in HANDOFF: return FAKE
            raise AssertionError(f"minted for {repo}")
        sys.modules["ghtoken"] = types.SimpleNamespace(token=token, invalidate=lambda r: None, mask=real.mask,
                                                       installation=real.installation, NoInstallation=real.NoInstallation)
        q = "protocol=https\nhost=github.com\npath={}\n"
        out = io.StringIO(); m.main(["get"], stdin=io.StringIO(q.format(AJ + ".git")), stdout=out)
        self.assertEqual(out.getvalue(), "", "flag off -> next helper")
        os.makedirs(lib.PI_STATE, exist_ok=True)
        open(lib.gh_app_flag(), "w").close(); open(f"{lib.PI_STATE}/gh-app-wrapper-enabled", "w").close()
        for r in HANDOFF:
            out = io.StringIO(); m.main(["get"], stdin=io.StringIO(q.format(r + ".git")), stdout=out)
            self.assertEqual(out.getvalue(), f"username=x-access-token\npassword={FAKE}\n", r)
        out = io.StringIO(); m.main(["get"], stdin=io.StringIO(q.format(OTHER)), stdout=out)
        self.assertEqual(out.getvalue(), "")
        os.remove(f"{lib.PI_STATE}/gh-app-wrapper-enabled")


class Unchanged(Base):
    def test_product_red_line_and_pat_rerun_untouched(self):
        self.on(writes=True)
        with mock.patch.object(lib.subprocess, "run", lambda *a, **k: self.fail("sent")):
            with self.assertRaises(lib.RedLine): lib.gh(["pr", "merge", "1", "-R", "xindong/mivo-canvas-plugin", "--squash"])
        self.assertEqual(lib.product_write_identity(["run", "rerun", "1", "-R", "xindong/mivo-canvas-plugin"]), "pat")
        self.assertFalse(lib.pat_rerun(["run", "rerun", "1", "-R", AJ]))


if __name__ == "__main__": unittest.main()
