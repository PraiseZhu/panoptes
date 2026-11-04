#!/usr/bin/env python3
"""11-04: grokbot-box-60-gh-auth removed from gh_wrapper.CALLER_ALLOWLIST (grokbot-box ebcda7536: verify.d/60-gh-auth.sh uses
GH="${PRAISE_QA_REAL_GH:-/usr/local/bin/gh}", the real gh, explicit identity). Checks: the caller path is no longer allowlisted
(App token, no PAT pass-through), and the real 60-gh-auth.sh still PASSes (a) with the real gh (mock) direct and (b) through the
wrapper when the wrapper is what sits at the default path. Mocks only: fake ghtoken, a mock real gh; no network, no real token."""
import io, json, os, shutil, stat, subprocess, sys, tempfile, types, unittest
from contextlib import redirect_stderr
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); BIN = os.path.join(os.path.dirname(HERE), "bin")
sys.path.insert(0, BIN); sys.path.insert(0, HERE)
import isolation  # noqa: E402
isolation.sandbox_state()
import lib  # noqa: E402
import gh_wrapper as W  # noqa: E402

PROD = "xindong/mivo-canvas-plugin"; FAKE_APP = "ghs_" + "F" * 36; FAKE_PAT = "ghp_" + "Q" * 36
LIVE = "/workspace/grokbot-box/verify.d/60-gh-auth.sh"
FIX = os.path.join(HERE, "fixtures", "grokbot-box", "verify.d", "60-gh-auth.sh")
CALLERS = (["bash", "/workspace/grokbot-box/verify.d/60-gh-auth.sh"], ["bash", "/opt/box/verify.d/60-gh-auth.sh"], ["/bin/bash", "verify.d/60-gh-auth.sh"])

MOCK_GH = r"""#!/bin/bash
# mock real gh: logs the identity it was handed (token kind only) and the command
k=none; case "${GH_TOKEN:-${GITHUB_TOKEN:-}}" in ghs_*) k=app;; ghp_*) k=pat;; esac
echo "$k $*" >> "$MOCK_LOG"
case "$1 $2" in "auth status") exit 0;; "api user") echo praise-qa-bot; exit 0;; esac
exit 1
"""
SHIM = """#!/usr/bin/env python3
# test shim for the installed wrapper: fake ghtoken (never a real mint), then the real gh_wrapper.main
import os, sys, types
sys.path.insert(0, {bin!r})
sys.modules["ghtoken"] = types.SimpleNamespace(token=lambda r: {tok!r}, invalidate=lambda r: None, mask=lambda e: e,
    installation=lambda r: 1, NoInstallation=type("NoInstallation", (Exception,), {{}}))
import gh_wrapper
sys.exit(gh_wrapper.main(sys.argv[1:]) or 0)
"""


def xw(path, body):
    with open(path, "w") as f: f.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class Not_Allowlisted(unittest.TestCase):
    def setUp(self):
        for f in (lib.gh_app_flag(), W.wrapper_flag()): os.makedirs(os.path.dirname(f), exist_ok=True); open(f, "w").close()
        self.tok = mock.patch.dict(sys.modules, {"ghtoken": types.SimpleNamespace(token=lambda r: FAKE_APP, invalidate=lambda r: None,
                                   mask=lambda e: e, installation=lambda r: 1, NoInstallation=type("NI", (Exception,), {}))}); self.tok.start()
    def tearDown(self):
        self.tok.stop()
        for f in (lib.gh_app_flag(), W.wrapper_flag()):
            if os.path.exists(f): os.remove(f)

    def wrap(self, argv, caller):
        calls = []; err = io.StringIO()
        with redirect_stderr(err):
            rc = W.main(argv, env={"PATH": "", "PRAISE_QA_REAL_GH": "/real/gh", "GITHUB_TOKEN": FAKE_PAT}, execve=lambda p, a, e: calls.append((p, a, e)),
                        run=lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="", stderr=""), caller=caller)
        return rc, (calls[-1] if calls else None), err.getvalue()

    def test_entry_gone_and_caller_gets_none(self):
        self.assertNotIn("grokbot-box-60-gh-auth", {n for n, _ in W.CALLER_ALLOWLIST})
        self.assertFalse(any(s.endswith("60-gh-auth.sh") for _, s in W.CALLER_ALLOWLIST))
        for c in CALLERS: self.assertIsNone(W.allowlisted(c), c)

    def test_app_token_only_no_pat_pass_through(self):
        al = W.allowlist_log()
        if os.path.exists(al): os.remove(al)
        for c in CALLERS:
            for a in (["pr", "list", "-R", PROD], ["auth", "status"], ["api", "user", "-q", ".login"]):
                rc, call, err = self.wrap(a, c)
                self.assertIsNotNone(call, (a, c)); self.assertEqual(call[2].get("GH_TOKEN"), FAKE_APP, (a, c))
                self.assertNotIn("GITHUB_TOKEN", call[2]); self.assertNotIn(FAKE_PAT, json.dumps(call[2])); self.assertNotIn("allowlisted", err)
        self.assertFalse(os.path.exists(al))

    def test_token_printing_still_refused_for_it(self):
        for c in CALLERS:
            for a in (["auth", "token"], ["auth", "status", "--show-token"], ["auth", "status", "-t"]):
                self.assertEqual(self.wrap(a, c)[:2], (4, None), (a, c))


class Script60(unittest.TestCase):
    """runs the actual 60-gh-auth.sh (live grokbot-box copy when present, else the fixture copy) as a subprocess"""
    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="gh60-"); self.addCleanup(shutil.rmtree, self.t, True)
        src = LIVE if os.path.isfile(LIVE) else FIX
        self.root = os.path.join(self.t, "box"); os.makedirs(f"{self.root}/verify.d"); os.makedirs(f"{self.root}/restore.d")
        shutil.copy(os.path.join(os.path.dirname(FIX), "..", "restore.d", "lib.sh"), f"{self.root}/restore.d/lib.sh")
        with open(src) as f: self.src = f.read()
        self.assertIn('GH="${PRAISE_QA_REAL_GH:-/usr/local/bin/gh}"', self.src)
        self.real = os.path.join(self.t, "real"); os.makedirs(self.real); xw(f"{self.real}/gh", MOCK_GH)
        self.wd = os.path.join(self.t, "wrap"); os.makedirs(self.wd)   # stands in for /usr/local/bin with the wrapper installed as gh
        xw(f"{self.wd}/gh-praise-qa", SHIM.format(bin=BIN, tok=FAKE_APP)); os.symlink(f"{self.wd}/gh-praise-qa", f"{self.wd}/gh")
        self.log = os.path.join(self.t, "gh.log"); self.state = os.path.join(self.t, "state"); os.makedirs(self.state)
        for f in ("gh-app-enabled", "gh-app-wrapper-enabled"): open(os.path.join(self.state, f), "w").close()

    def run60(self, default_gh, extra):
        p = f"{self.root}/verify.d/60-gh-auth.sh"
        with open(p, "w") as f: f.write(self.src.replace("/usr/local/bin/gh", default_gh))
        os.chmod(p, 0o755)
        env = {"PATH": f"{self.wd}:{self.real}:/usr/bin:/bin", "HOME": self.t, "PI_STATE": self.state, "PI_WORK": self.t, "PI_WORK_ROOT": self.t,
               "MOCK_LOG": self.log, "GITHUB_TOKEN": FAKE_PAT, "REPO": self.root, **extra}
        r = subprocess.run(["bash", p], env=env, capture_output=True, text=True, timeout=60, cwd=self.t)
        log = []
        if os.path.exists(self.log):
            with open(self.log) as f: log = f.read().splitlines()
        return r, log

    def test_real_gh_direct_via_env_bypasses_wrapper(self):
        r, log = self.run60("/nonexistent/gh", {"PRAISE_QA_REAL_GH": f"{self.real}/gh"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertIn("PASS gh auth ok (praise-qa-bot)", r.stdout)
        self.assertEqual(log, ["pat auth status", "pat api user -q .login"])   # the inherited login, no wrapper in between

    def test_default_path_is_real_gh(self):
        r, log = self.run60(f"{self.real}/gh", {})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertIn("PASS gh auth ok (praise-qa-bot)", r.stdout)
        self.assertEqual(log, ["pat auth status", "pat api user -q .login"])

    def test_default_path_is_the_wrapper_still_passes_as_non_allowlisted_read(self):
        """wrapper installed at /usr/local/bin/gh, PRAISE_QA_REAL_GH unset: 60-gh-auth goes through the wrapper, not allowlisted,
        gets the App token for both calls (auth status allowed, api user a read) and still PASSes"""
        r, log = self.run60(f"{self.wd}/gh", {})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertIn("PASS gh auth ok (praise-qa-bot)", r.stdout)
        self.assertEqual(log, ["app auth status", "app api user -q .login"])
        self.assertFalse(os.path.exists(os.path.join(self.state, "out", "gh-wrapper", "allowlist.jsonl")))
        self.assertNotIn(FAKE_APP, r.stdout + r.stderr); self.assertNotIn(FAKE_PAT, r.stdout + r.stderr)

    def test_missing_real_gh_is_need_not_pass(self):
        r, _ = self.run60("/nonexistent/gh", {})
        self.assertEqual(r.returncode, 2); self.assertIn("NEED real gh not found", r.stdout)


if __name__ == "__main__": unittest.main()
