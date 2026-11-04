#!/usr/bin/env python3
"""11-05 PM (on the G2 PR): (A) every Pi-line commit -- pushed with the App token or the PAT -- is authored and committed as
Praise <290989140+PraiseZhu@users.noreply.github.com>; (B) the App slug is one constant, lib.APP_SLUG, and every bot form follows it.
Real git in temp repos, gh / GitHub mocked; no token."""
import io, json, os, re, subprocess, sys, tempfile, types, unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); BIN = os.path.join(os.path.dirname(HERE), "bin")
sys.path.insert(0, BIN); sys.path.insert(0, HERE)
import isolation  # noqa: E402
isolation.sandbox_state()
sys.modules.setdefault("merger", types.SimpleNamespace(FIELDS="number", blockers=lambda p: []))
import lib, git_identity, outbound, infra_outbound, prfix  # noqa: E402

PRAISE = "Praise <290989140+PraiseZhu@users.noreply.github.com>"


def temp_repo():
    d = tempfile.mkdtemp(prefix="ident-")
    for c in (["init", "-q"], ["config", "user.name", "Some Bot"], ["config", "user.email", "bot@example.com"], ["config", "commit.gpgsign", "false"]):
        subprocess.run(["git", "-C", d, *c], check=True, capture_output=True)
    open(os.path.join(d, "f"), "w").write("x")
    subprocess.run(["git", "-C", d, "add", "f"], check=True, capture_output=True)
    return d


def who(d): return subprocess.run(["git", "-C", d, "log", "-1", "--format=%an <%ae>|%cn <%ce>"], capture_output=True, text=True).stdout.strip()


HOSTILE = {"GIT_AUTHOR_NAME": "praise-qa[bot]", "GIT_AUTHOR_EMAIL": "x@users.noreply.github.com", "GIT_COMMITTER_NAME": "box", "GIT_COMMITTER_EMAIL": "box@box"}


class A_CommitIdentity(unittest.TestCase):
    def test_identity_constants(self):
        self.assertEqual(git_identity.NOREPLY_EMAIL, "290989140+PraiseZhu@users.noreply.github.com")
        self.assertEqual(git_identity.ident(), PRAISE)
        self.assertEqual((outbound.GIT_NAME, outbound.GIT_EMAIL), ("Praise", git_identity.NOREPLY_EMAIL))
        self.assertEqual((infra_outbound.GIT_NAME, infra_outbound.GIT_EMAIL), ("Praise", git_identity.NOREPLY_EMAIL))

    def test_outbound_commit_is_praise_even_with_a_hostile_env_and_repo_config(self):
        d = temp_repo()
        with mock.patch.dict(os.environ, HOSTILE): outbound.run(["git", "-C", d, "commit", "-q", "-m", "t"])
        self.assertEqual(who(d), f"{PRAISE}|{PRAISE}")

    def test_prfix_commit_is_praise(self):
        d = temp_repo()
        with mock.patch.dict(os.environ, HOSTILE): rc, o = prfix.git(*prfix.GIT_ID, "commit", "-q", "-m", "t", cwd=d)
        self.assertEqual(rc, 0, o); self.assertEqual(who(d), f"{PRAISE}|{PRAISE}")

    def test_infra_outbound_refuses_a_foreign_author_before_push(self):
        infra_outbound.check_authors(f"{PRAISE}|{PRAISE}\n{PRAISE}|{PRAISE}\n")
        for bad in (f"praise-qa[bot] <1+praise-qa[bot]@users.noreply.github.com>|{PRAISE}", f"{PRAISE}|PraiseZhu <{git_identity.NOREPLY_EMAIL}>"):
            with self.assertRaises(RuntimeError): infra_outbound.check_authors(bad)

    def test_git_am_gets_praise_as_committer(self):
        e = git_identity.env({"GIT_COMMITTER_NAME": "box"})
        self.assertEqual((e["GIT_AUTHOR_NAME"], e["GIT_COMMITTER_NAME"], e["GIT_AUTHOR_EMAIL"], e["GIT_COMMITTER_EMAIL"]),
                         ("Praise", "Praise", git_identity.NOREPLY_EMAIL, git_identity.NOREPLY_EMAIL))


class B_AppSlug(unittest.TestCase):
    def setUp(self):
        os.makedirs(lib.PI_STATE, exist_ok=True)
        for f in ("gh-app-enabled", "gh-app-wrapper-enabled"): open(f"{lib.PI_STATE}/{f}", "w").close()
        self.s = mock.patch.object(lib, "APP_SLUG", "praisesuper"); self.s.start()
        g = types.SimpleNamespace(token=lambda r: "ghs_" + "S" * 36, invalidate=lambda r: None, mask=str, installation=lambda r: 1, NoInstallation=KeyError,
                                  MintError=RuntimeError, _api=lambda: "https://api.github.com")
        self.g = mock.patch.dict(sys.modules, {"ghtoken": g}); self.g.start()
    def tearDown(self):
        self.s.stop(); self.g.stop()
        for f in ("gh-app-enabled", "gh-app-wrapper-enabled"):
            if os.path.exists(f"{lib.PI_STATE}/{f}"): os.remove(f"{lib.PI_STATE}/{f}")

    def test_lib_forms(self):
        self.assertEqual((lib.app_login(), lib.app_author()), ("praisesuper[bot]", "app/praisesuper"))
        self.assertEqual(lib.self_authors(), ("PraiseZhu", "app/praisesuper"))
        self.assertTrue(lib.is_self({"login": "praisesuper[bot]"})); self.assertFalse(lib.is_self("praise-qa[bot]"))
        self.assertEqual(lib.self_bot()["html_url"], "https://github.com/apps/praisesuper")

    def test_g5_rewrite_and_api_user(self):
        self.assertEqual(lib.self_identity_args(["pr", "list", "--author", "@me", "--search", "author:@me involves:@me"]),
                         ["pr", "list", "--author", "app/praisesuper", "--search", "author:app/praisesuper involves:praisesuper[bot]"])
        self.assertEqual(lib.self_identity_answer(["api", "user", "--jq", ".login"]).strip(), "praisesuper[bot]")
        self.assertEqual(json.loads(lib.self_identity_answer(["api", "user"]))["login"], "praisesuper[bot]")

    def test_wrapper_api_user(self):
        import gh_wrapper as W
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = W.main(["api", "user", "-q", ".login"], env={"PATH": "", "PRAISE_QA_REAL_GH": "/real/gh"}, execve=lambda *a: None,
                        run=lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="", stderr=""), caller=[])
        self.assertEqual((rc, out.getvalue().strip()), (0, "praisesuper[bot]"))

    def test_panoptes_merge_checks_the_derived_login(self):
        import panoptes_merge as M
        class R(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False
        def run(login):
            op = lambda req, timeout=None: R(json.dumps({"merged": True, "sha": "b" * 40, "merged_by": {"login": login}}).encode())
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()): return M.main(["1", "a" * 40], opener=op, token=lambda r: "t")
        self.assertEqual(run("praisesuper[bot]"), 0); self.assertEqual(run("praise-qa[bot]"), 1)

    def test_merger_identity_uses_the_derived_login(self):
        self.assertIn("app_login()", open(os.path.join(BIN, "merger.py")).read())

    def test_no_hard_coded_bot_forms_left(self):
        bad = []
        for f in sorted(os.listdir(BIN)):
            if not f.endswith(".py"): continue
            for i, line in enumerate(open(os.path.join(BIN, f), errors="replace"), 1):
                code = line.split("#")[0]
                if re.search(r"""["'](app/)?praise-qa(\[bot\])?["']""", code) and not re.match(r"\s*APP_SLUG\s*=", code): bad.append(f"{f}:{i}")
        self.assertEqual(bad, [])


if __name__ == "__main__": unittest.main()
