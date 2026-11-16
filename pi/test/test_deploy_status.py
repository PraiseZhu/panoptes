#!/usr/bin/env python3
"""11-16 (PraiseZhu/panoptes#303) deploy status: after opsenv.deploy() fast-forwards the box checkout, the deployed commit gets the
commit status panoptes/deployed (Praise QA App token, description = host + CST time, nothing else). No credential / a failed request
never fails the deploy and leaves "- 部署状态未发布：<code>" in out/ops/env-<day>.md. Fake token, fake HTTP, fake git; temp dirs only."""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import datetime as dt, glob, io, json, os, sys, tempfile, types, unittest, urllib.error
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); BIN = os.path.join(os.path.dirname(HERE), "bin")
sys.path.insert(0, BIN); sys.path.insert(0, HERE)
import isolation  # noqa: E402
isolation.sandbox_state()
import lib, ghtoken, deploy_status as DS, opsenv, opsdeploy  # noqa: E402

SHA = "0123456789abcdef0123456789abcdef01234567"
OLD = "fedcba9876543210fedcba9876543210fedcba98"
FAKE_TOKEN = "ghs_FAKEfakeFAKEfake0123456789"
NOW = dt.datetime(2026, 11, 16, 21, 5, tzinfo=lib.CST)


def tok(perms=None): return lambda: {"token": FAKE_TOKEN, "expires_at": 0, "installation": 164262283, "permissions": perms if perms is not None else {"statuses": "write"}}


class Post:
    def __init__(self, code=201, exc=None): self.calls = []; self.code = code; self.exc = exc
    def __call__(self, url, token, payload):
        self.calls.append((url, token, payload))
        if self.exc: raise self.exc
        return self.code


def http_error(code): return urllib.error.HTTPError("https://api.github.com/x", code, "err", {}, io.BytesIO(b'{"message":"' + FAKE_TOKEN.encode() + b'"}'))


class Publish(unittest.TestCase):
    def test_writes_one_status_on_the_deployed_commit(self):
        p = Post()
        self.assertEqual(DS.publish(SHA, host="qa-box-1", now=NOW, token_info=tok(), post=p), (True, None))
        self.assertEqual(len(p.calls), 1)
        url, token, payload = p.calls[0]
        self.assertTrue(url.endswith(f"/repos/PraiseZhu/panoptes/statuses/{SHA}"), url)
        self.assertEqual(token, FAKE_TOKEN)
        self.assertEqual(payload["context"], "panoptes/deployed"); self.assertEqual(payload["state"], "success")
        self.assertEqual(payload["description"], "部署于 qa-box-1 2026-11-16 21:05 CST")
        self.assertEqual(set(payload), {"state", "context", "description"})   # no target_url: no path, nothing else leaves the box

    def test_description_has_no_path_token_or_odd_host_chars(self):
        d = DS.describe(host="box/../home/praise ghs_x@example.com", now=NOW)
        self.assertNotIn("/", d); self.assertNotIn("@", d); self.assertNotIn(" ghs_", d)
        self.assertIn("CST", d); self.assertLessEqual(len(d), 140)   # GitHub's description limit
        self.assertEqual(DS.describe(host="", now=NOW), "部署于 unknown-host 2026-11-16 21:05 CST")
        utc = dt.datetime(2026, 11, 16, 13, 5, tzinfo=dt.timezone.utc)   # 13:05 UTC = 21:05 CST
        self.assertIn("21:05 CST", DS.describe(host="h", now=utc))

    def test_no_credential_reason_codes(self):
        def boom(): raise ghtoken.MintError("openssl could not sign the App JWT: no key")
        def none(): raise ghtoken.NoInstallation("x")
        p = Post()
        self.assertEqual(DS.publish(SHA, host="h", now=NOW, token_info=boom, post=p), (False, "no_credential"))
        self.assertEqual(DS.publish(SHA, host="h", now=NOW, token_info=none, post=p), (False, "no_installation"))
        self.assertEqual(p.calls, [])

    def test_real_mint_without_app_key_is_no_credential(self):
        # selftest / conftest point PRAISE_QA_KEY at a missing file: the real ghtoken path must end in a reason code, not raise
        with mock.patch.dict(os.environ, {"PRAISE_QA_KEY": os.path.join(tempfile.mkdtemp(), "no-key"), "PRAISE_QA_CACHE": tempfile.mkdtemp(),
                                          "PRAISE_QA_API": "http://127.0.0.1:9"}):
            p = Post()
            self.assertEqual(DS.publish(SHA, host="h", now=NOW, post=p), (False, "no_credential")); self.assertEqual(p.calls, [])

    def test_missing_statuses_permission_sends_nothing(self):
        p = Post()
        self.assertEqual(DS.publish(SHA, host="h", now=NOW, token_info=tok({"contents": "read", "statuses": "read"}), post=p), (False, "no_statuses_permission"))
        self.assertEqual(p.calls, [])
        self.assertEqual(DS.publish(SHA, host="h", now=NOW, token_info=tok({}), post=Post()), (True, None))   # permissions unknown: try

    def test_http_and_network_failures(self):
        self.assertEqual(DS.publish(SHA, host="h", now=NOW, token_info=tok(), post=Post(exc=http_error(403))), (False, "http_403"))
        self.assertEqual(DS.publish(SHA, host="h", now=NOW, token_info=tok(), post=Post(exc=urllib.error.URLError("dns"))), (False, "network_error"))
        self.assertEqual(DS.publish(SHA, host="h", now=NOW, token_info=tok(), post=Post(exc=TimeoutError())), (False, "network_error"))
        self.assertEqual(DS.publish(SHA, host="h", now=NOW, token_info=tok(), post=Post(code=200)), (False, "http_200"))
        self.assertEqual(DS.publish(SHA, host="h", now=NOW, token_info=tok(), post=Post(exc=ValueError("x"))), (False, "error_ValueError"))
        self.assertEqual(DS.publish("main", host="h", now=NOW, token_info=tok(), post=Post()), (False, "bad_sha"))

    def test_401_drops_the_cached_token(self):
        with mock.patch.object(ghtoken, "invalidate") as inv:
            self.assertEqual(DS.publish(SHA, host="h", now=NOW, token_info=tok(), post=Post(exc=http_error(401))), (False, "http_401"))
        inv.assert_called_once_with("PraiseZhu/panoptes")

    def test_after_deploy_lines_never_carry_the_token(self):
        L = []; DS.after_deploy(SHA, L, host="h", now=NOW, token_info=tok(), post=Post(exc=http_error(422)))
        self.assertEqual(L, ["- 部署状态未发布：http_422"])
        L = []; DS.after_deploy(SHA, L, host="h", now=NOW, token_info=tok(), post=Post())
        self.assertEqual(L, ["- 部署状态已发布：panoptes/deployed @ 0123456"])


class Deploy(unittest.TestCase):
    """the real opsenv.deploy() with fake git / gh / lock dir, then opsdeploy.main() writing env-<day>.md"""
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="deploy-status-"); os.makedirs(f"{self.d}/qa/argus-root")
        self.merged = []
        def sh(c, t=120):
            if "rev-parse --abbrev-ref HEAD" in c: return 0, "main"
            if "fetch -q origin main" in c: return 0, ""
            if "rev-parse HEAD" in c: return 0, SHA if self.merged else OLD
            if "rev-parse origin/main" in c: return 0, SHA
            if "merge-base --is-ancestor" in c: return 0, ""
            if "status --porcelain" in c: return 0, "0"
            if "merge -q --ff-only" in c: self.merged.append(c); return 0, ""
            raise AssertionError(f"unexpected command {c}")
        self.posts = Post()
        self.mp = [mock.patch.object(opsenv, "sh", sh), mock.patch.object(opsenv, "QA", f"{self.d}/qa"), mock.patch.object(opsenv, "mode", lambda: "pi"),
                   mock.patch.object(opsenv, "gh", lambda a, **k: [{"name": "CI", "status": "completed", "conclusion": "success"}]),
                   mock.patch.object(opsenv, "log", lambda *a: None), mock.patch.object(opsdeploy, "log", lambda *a: None),
                   mock.patch.object(opsdeploy, "mode", lambda: "pi"), mock.patch.object(opsdeploy, "BASE", f"{self.d}/state"),
                   mock.patch.object(DS, "_post", self.posts), mock.patch.object(DS.socket, "gethostname", lambda: "qa-box-1"),
                   mock.patch.dict(sys.modules, {"picall_sync": types.SimpleNamespace(sync=lambda: []), "cron_sync": types.SimpleNamespace(sync=lambda: None)})]
        for m in self.mp: m.start()
        self.addCleanup(lambda: [m.stop() for m in reversed(self.mp)])

    def env_md(self):
        fs = glob.glob(f"{self.d}/state/out/ops/env-*.md"); self.assertEqual(len(fs), 1, fs)
        with open(fs[0]) as f: return f.read()

    def test_success_publishes_and_records(self):
        with mock.patch.object(DS, "_token_info", tok()):
            self.assertEqual(opsdeploy.main(), 0)
        self.assertEqual(len(self.merged), 1)
        self.assertEqual(len(self.posts.calls), 1); url, _, payload = self.posts.calls[0]
        self.assertTrue(url.endswith(f"/statuses/{SHA}")); self.assertIn("qa-box-1", payload["description"])
        md = self.env_md()
        self.assertIn(f"- 部署: {OLD[:7]} → {SHA[:7]}", md); self.assertIn("- 部署状态已发布：panoptes/deployed @ 0123456", md)

    def test_no_credential_deploy_still_succeeds(self):
        def boom(): raise ghtoken.MintError("no key")
        with mock.patch.object(DS, "_token_info", boom):
            L = []; self.assertIsNone(opsenv.deploy(L))   # None = no deploy error
        self.assertEqual(len(self.merged), 1); self.assertEqual(self.posts.calls, [])
        self.assertEqual(L[-1], "- 部署状态未发布：no_credential")

    def test_github_failure_recorded_in_env_md_without_token(self):
        self.posts.exc = http_error(500)
        with mock.patch.object(DS, "_token_info", tok()):
            self.assertEqual(opsdeploy.main(), 0)
        md = self.env_md()
        self.assertEqual(len(self.merged), 1); self.assertIn("- 部署状态未发布：http_500", md); self.assertNotIn(FAKE_TOKEN, md)

    def test_broken_module_never_fails_the_deploy(self):
        with mock.patch.object(DS, "after_deploy", mock.Mock(side_effect=RuntimeError("bug"))):
            L = []; self.assertIsNone(opsenv.deploy(L))
        self.assertEqual(L[-1], "- 部署状态未发布：error_RuntimeError")

    def test_nothing_to_deploy_publishes_nothing(self):
        self.merged.append("already")   # HEAD == origin/main
        with mock.patch.object(DS, "_token_info", tok()):
            L = []; self.assertIsNone(opsenv.deploy(L))
        self.assertEqual(L, []); self.assertEqual(self.posts.calls, [])


class Read(unittest.TestCase):
    def fake_gh(self, commits, statuses):
        calls = []
        def run(cmd, **k):
            calls.append(cmd); path = cmd[2]
            if "/statuses" in path: out = statuses.get(path.split("/commits/")[1].split("/")[0], [])
            else: out = commits
            return types.SimpleNamespace(returncode=0, stdout=json.dumps(out), stderr="")
        return run, calls

    def test_show_finds_the_newest_deployed_commit(self):
        cs = [{"sha": "c" * 40}, {"sha": SHA}, {"sha": OLD}]
        st = {SHA: [{"context": "ci", "description": "x"}, {"context": "panoptes/deployed", "description": "部署于 h 2026-11-16 21:05 CST", "created_at": "t"}],
              OLD: [{"context": "panoptes/deployed", "description": "old"}]}
        run, calls = self.fake_gh(cs, st)
        cur = DS.current("main", 30, run)
        self.assertEqual((cur["sha"], cur["behind"], cur["description"]), (SHA, 1, "部署于 h 2026-11-16 21:05 CST"))
        self.assertEqual(calls[0][:2], ["gh", "api"]); self.assertIn("repos/PraiseZhu/panoptes/commits?sha=main", calls[0][2])
        out = io.StringIO()
        with mock.patch("sys.stdout", out): rc = DS.main(["show"], run=run)
        self.assertEqual(rc, 0); self.assertIn(SHA, out.getvalue()); self.assertIn("落后", out.getvalue())

    def test_show_without_any_status(self):
        run, _ = self.fake_gh([{"sha": SHA}], {})
        out = io.StringIO()
        with mock.patch("sys.stdout", out): rc = DS.main(["show"], run=run)
        self.assertEqual(rc, 1); self.assertIn("查不到当前部署版本", out.getvalue())


if __name__ == "__main__":
    unittest.main()
