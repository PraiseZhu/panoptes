#!/usr/bin/env python3
"""11-11 (#2022, Praise "只能用 app 接口"): pi/bin/open_product_pr.py is the one caller whose product `gh pr create` passes the
wrapper, on the praise-qa App installation token (never lib.pat_env). Everything else stays refused. Mocks only: no network, no PR."""
import io, json, os, sys, tempfile, types, unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); BIN = os.path.join(os.path.dirname(HERE), "bin")
sys.path.insert(0, BIN); sys.path.insert(0, HERE)
import isolation  # noqa: E402
isolation.sandbox_state()
import lib  # noqa: E402
import gh_wrapper as W  # noqa: E402

PROD = "xindong/mivo-canvas-plugin"
APP_TOKEN = "ghs_" + "P" * 36
SCRIPT = os.path.join(BIN, "open_product_pr.py")
CALLER = ["/usr/bin/python3", SCRIPT, "--head", "fix/qa-2022-codeql-dispatch"]
FLAGS = ("gh-app-enabled", "gh-app-wrapper-enabled", "gh-app-product-writes-enabled")
def F(n): return f"{lib.PI_STATE}/{n}"


class Base(unittest.TestCase):
    def setUp(self):
        self.minted = []
        import ghtoken as real
        tok = lambda repo: self.minted.append(repo) or APP_TOKEN
        self.tok = mock.patch.dict(sys.modules, {"ghtoken": types.SimpleNamespace(token=tok, invalidate=lambda r: None, mask=real.mask,
                                   installation=real.installation, NoInstallation=real.NoInstallation)}); self.tok.start()
        self.pat = mock.patch.object(lib, "pat_env", side_effect=AssertionError("pat_env must not be used")); self.pat.start()
        for n in FLAGS: open(F(n), "w").close()
        self.body = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False); self.body.write("QA: #2022\n"); self.body.close()
        if os.path.exists(self.audit_path()): os.remove(self.audit_path())

    def tearDown(self):
        self.tok.stop(); self.pat.stop(); os.unlink(self.body.name)
        for n in FLAGS:
            if os.path.exists(F(n)): os.remove(F(n))

    def audit_path(self): return f"{lib.PI_STATE}/out/gh-wrapper/open-product-pr.jsonl"
    def audit(self):
        with open(self.audit_path()) as f: return [json.loads(l) for l in f]

    def wrap(self, argv, caller, env=None):
        """the wrapper as the real gh would be reached: -> (rc, execve call or None, stderr)"""
        calls, err = [], io.StringIO()
        with redirect_stderr(err):
            rc = W.main(argv, env={"PATH": "", "PRAISE_QA_REAL_GH": "/real/gh", **(env or {})}, execve=lambda p, a, e: calls.append((p, a, e)),
                        run=lambda cmd, **k: types.SimpleNamespace(returncode=1, stdout="", stderr=""), caller=list(caller))
        return rc, (calls[-1] if calls else None), err.getvalue()

    def script(self, argv, env=None):
        """open_product_pr.main with its subprocess replaced by the wrapper in-process (caller = this script) -> (rc, wrapper calls)"""
        import open_product_pr as O
        seen = []
        def run(cmd, env=None, **k):
            self.assertEqual(cmd[:2], [sys.executable, os.path.join(BIN, "gh_wrapper.py")])
            rc, call, err = self.wrap(cmd[2:], CALLER, env=env)
            seen.append((cmd, env, call))
            return types.SimpleNamespace(returncode=rc or 0, stdout=f"https://github.com/{PROD}/pull/2099\n" if call else "", stderr=err)
        with mock.patch.dict(os.environ, env or {}), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = O.main(argv, run=run)
        return rc, seen

    def good(self, *extra): return ["--head", "fix/qa-2022-codeql-dispatch", "--title", "fix(ci): drop codeql workflow_dispatch", "--body-file", self.body.name, *extra]


class Wrapper(Base):
    def test_pr_create_from_the_script_passes_on_the_app_token(self):
        rc, call, err = self.wrap(["pr", "create", "--repo", PROD, "--base", "main", "--head=b", "--title=t", f"--body-file={self.body.name}"], CALLER,
                                  env={"GITHUB_TOKEN": "pat_from_secrets"})
        self.assertIsNone(rc, err); self.assertIsNotNone(call)
        self.assertEqual(call[2]["GH_TOKEN"], APP_TOKEN); self.assertNotIn("GITHUB_TOKEN", call[2]); self.assertEqual(self.minted, [PROD])
        self.assertEqual(lib.pat_env.call_count, 0)

    def test_script_is_not_a_pat_caller(self):
        self.assertIsNone(W.allowlisted(CALLER))
        self.assertFalse(any(s.endswith("open_product_pr.py") for _, s in W.CALLER_ALLOWLIST))
        self.assertEqual(W.write_exceptions(CALLER), frozenset({("pr", "create")}))

    def test_other_callers_pr_create_still_refused(self):
        a = ["pr", "create", "--repo", PROD, "--base", "main", "--head", "b", "--title", "t", "--body-file", self.body.name]
        for caller in ([], ["bash"], ["/bin/bash", "-i"], ["python3", "/x/pi/bin/outbound.py"], ["python3", "/x/pi/bin/prfix.py"],
                       ["python3", "-c", "1", SCRIPT], ["node", "/x/open_product_pr.mjs"], ["python3", "/x/pi/bin/open_product_pr.py.bak"]):
            rc, call, err = self.wrap(a, caller); self.assertEqual((rc, call), (4, None), caller); self.assertIn("product-write allowlist", err)

    def test_merge_close_review_ready_undo_from_the_script_refused(self):
        for a in (["pr", "merge", "1", "-R", PROD], ["pr", "close", "1", "-R", PROD], ["pr", "review", "1", "--approve", "-R", PROD],
                  ["pr", "comment", "1", "-b", "x", "-R", PROD], ["pr", "edit", "1", "--title", "x", "-R", PROD],
                  ["issue", "create", "-R", PROD, "-t", "x", "-b", "y"], ["api", "-X", "PUT", f"repos/{PROD}/pulls/1/merge"]):
            rc, call, _ = self.wrap(a, CALLER); self.assertEqual((rc, call), (4, None), a)
        # pr ready / ready --undo are open to every caller already (#214 r4 D); this change does not touch them
        self.assertIsNone(lib.gh_product_write(["pr", "ready", "1", "--undo", "-R", PROD], PROD))


class Script(Base):
    def test_good_call_reaches_gh_with_the_app_token_and_audits(self):
        rc, seen = self.script(self.good(), env={"GH_TOKEN": "ghs_someone_else", "GITHUB_TOKEN": "pat_x"})
        self.assertEqual(rc, 0); self.assertEqual(len(seen), 1)
        cmd, env, call = seen[0]
        self.assertEqual(cmd[2:], ["pr", "create", "--repo", PROD, "--base", "main", "--head=fix/qa-2022-codeql-dispatch",
                                   "--title=fix(ci): drop codeql workflow_dispatch", f"--body-file={self.body.name}"])
        self.assertFalse({"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN"} & set(env))
        self.assertEqual(call[2]["GH_TOKEN"], APP_TOKEN); self.assertEqual(lib.pat_env.call_count, 0)
        self.assertNotIn("--draft", call[1])
        line = self.audit()[-1]
        self.assertEqual({k: line[k] for k in ("branch", "title", "result", "rc", "url")},
                         {"branch": "fix/qa-2022-codeql-dispatch", "title": "fix(ci): drop codeql workflow_dispatch", "result": "opened", "rc": 0,
                          "url": f"https://github.com/{PROD}/pull/2099"})
        self.assertTrue(line["at"].endswith("+08:00"))

    def test_refusals_send_nothing_and_audit(self):
        cases = (self.good("--draft"), self.good("-d"), self.good("--draft=true"), self.good("--repo", "PraiseZhu/panoptes"),
                 self.good("--repo=xindong/other"), self.good("--base", "release"), self.good("--label", "x"), self.good("--fill"),
                 ["--title", "t", "--body-file", self.body.name], self.good("--head", "b2"), self.good("--reviewer"),
                 ["--head", "someone:branch", "--title", "t", "--body-file", self.body.name],
                 ["--head", "-b", "--title", "t", "--body-file", self.body.name], ["--head", "b", "--title", "t", "--body-file", "/nonexistent"])
        for a in cases:
            rc, seen = self.script(a); self.assertEqual((rc, seen), (4, []), a)
            self.assertEqual(self.audit()[-1]["result"], "refused", a)
        self.assertEqual(len(self.audit()), len(cases))

    def test_app_route_off_refuses_instead_of_the_pat(self):
        for n in ("gh-app-product-writes-enabled", "gh-app-wrapper-enabled"):
            os.remove(F(n))
            rc, seen = self.script(self.good()); self.assertEqual((rc, seen), (4, []), n)
            self.assertIn("App route is off", self.audit()[-1]["why"])
            open(F(n), "w").close()

    def test_gh_failure_is_audited_as_failed(self):
        import open_product_pr as O
        run = lambda cmd, **k: types.SimpleNamespace(returncode=1, stdout="", stderr="pull request create failed: GraphQL: x " + APP_TOKEN)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()): rc = O.main(self.good(), run=run)
        line = self.audit()[-1]
        self.assertEqual((rc, line["result"], line["url"]), (1, "failed", None)); self.assertNotIn(APP_TOKEN, json.dumps(line))


if __name__ == "__main__": unittest.main()
