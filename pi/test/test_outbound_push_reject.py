#!/usr/bin/env python3
"""11-04 outbound-2026-11-04.log:9777: a rejected git push was logged as 'git rc=1: ... -> qa- |' -- the GH013 'remote:' reason
was cut off. Every 'remote:' line must survive verbatim (other noise stays capped) and tokens never reach the log.
Mocks only: fake subprocess.run, no network. Run: cd bin && python3 ../test/test_outbound_push_reject.py"""
import os, sys, types, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
sys.path.insert(0, HERE); import isolation  # noqa: E402
isolation.sandbox_state()
sys.modules.setdefault("merger", types.SimpleNamespace(FIELDS="number", blockers=lambda p: []))
import outbound as O  # noqa: E402

TOK = "ghs_" + "A1b2C3d4" * 5
REMOTE = ["remote: error: GH013: Repository rule violations found for refs/heads/qa-fix/fixpr-1675-mq1-45901bdf4d198267b29a.",
          "remote: Review all repository rules at https://github.com/xindong/mivo-canvas-plugin/rules?ref=refs%2Fheads%2Fqa-fix%2Ffixpr-1675",
          "remote: ",
          "remote: - Cannot update this protected ref: branch name must match the pattern 'feature/*' or 'fix/*' (rule 'branch-naming').",
          "remote: - Changes must be made through a pull request. Required status check 'qa-gate / verify (pull_request)' is expected."]
STDERR = "\n".join(["noise " + "x" * 400] + REMOTE + [
    "To https://x-access-token:" + TOK + "@github.com/xindong/mivo-canvas-plugin.git",
    " ! [remote rejected] qa-fix/fixpr-1675-mq1-45901bdf4d198267b29a -> qa-fix/fixpr-1675-mq1-45901bdf4d198267b29a (push declined due to repository rule violations)",
    "error: failed to push some refs to 'https://github.com/xindong/mivo-canvas-plugin.git'", "trailing " + "y" * 600])

def fake_run(cmd, **kw):
    if "get-url" in cmd: return types.SimpleNamespace(returncode=0, stdout="https://github.com/xindong/mivo-canvas-plugin.git\n", stderr="")
    return types.SimpleNamespace(returncode=1, stdout="", stderr=STDERR)

class T(unittest.TestCase):
    def err(self):
        with mock.patch.object(O.subprocess, "run", fake_run), mock.patch.object(O, "git_push_env", lambda repo: ([], None)):
            with self.assertRaises(RuntimeError) as c: O.run(["git", "-C", "/tmp/x", "push", "-u", "origin", "qa-fix/b"])
        return c.exception
    def test_run_keeps_every_remote_line(self):
        msg = str(self.err())
        for l in REMOTE: self.assertIn(l, msg)
        self.assertNotIn(TOK, msg); self.assertNotIn("noise " + "x" * 400, msg)
    def test_fx_git_keeps_remote_lines(self):
        with mock.patch.object(O.subprocess, "run", fake_run), mock.patch.object(O, "git_push_env", lambda repo: ([], None)):
            with self.assertRaises(RuntimeError) as c: O._fx_git("/tmp/x", "push", "origin", "HEAD:refs/heads/b")
        for l in REMOTE: self.assertIn(l, str(c.exception))
    def test_log_row_and_ledger_cap_keep_remote_lines_redacted(self):
        e = self.err()
        for n in (160, 200):
            row = O.clip_err(e, n)
            for l in REMOTE: self.assertIn(l, row)
            self.assertNotIn(TOK, row)
            self.assertLess(len(row), n + sum(len(l) + 1 for l in REMOTE) + 1)   # non-remote noise still capped
    def test_cap_unchanged_without_remote_lines(self):
        self.assertEqual(O.clip_err(RuntimeError("z" * 500), 160), "z" * 160)
    def test_redacts_bare_and_url_tokens(self):
        out = O.clip_err(RuntimeError(f"remote: bad {TOK} https://u:{TOK}@github.com/a/b github_pat_{'Q'*30}"), 300)
        self.assertNotIn(TOK, out); self.assertNotIn("Q" * 30, out)

HEX = "0123456789abcdef" * 2 + "01234567"
B64 = "eC1hY2Nlc3MtdG9rZW46Z2hzX0ExYjJDM2Q0QTFiMkMzZDRBMWIyQzNkNEExYjJDM2Q0QTFiMkMzZDQ="
HARDEN = {   # 11-04 #217 follow-up (accept-217/adversarial_redact.py): each must come out masked
    "bare 40-hex after key=": (f"remote: token={HEX}", HEX),
    "40-hex in url": (f"remote: https://x-access-token:{HEX}@github.com", HEX),
    "x-access-token outside url": (f"remote: x-access-token:{HEX}", HEX),
    "authorization basic": (f"remote: Authorization: Basic {B64}", B64[:20]),
    "authorization bearer": (f"remote: Authorization: Bearer {TOK}", TOK),
    "sk- key": ("remote: sk-" + "z" * 32, "z" * 32),
    "uppercase url creds": (f"remote: HTTPS://User:Tok{HEX[:8]}@github.com/x", "Tok" + HEX[:8]),
    "token-only url": (f"remote: https://{TOK}@github.com/x", TOK),
    "git+https url": (f"remote: git+https://x-access-token:{TOK}@github.com", TOK),
}
class Harden(unittest.TestCase):
    def test_every_case_masked(self):
        for name, (line, secret) in HARDEN.items():
            with self.subTest(name):
                out = O.clip_err(RuntimeError(line), 200)
                self.assertNotIn(secret, out); self.assertIn("remote:", out)
    def test_plain_commit_shas_stay_readable(self):
        line = f"pushed {HEX} onto qa-fix/b (main {HEX[::-1]}) head={HEX}"   # unlabelled shas, and head= is not a secret label
        self.assertEqual(O.clip_err(RuntimeError(line), 300), line)

LEGACY = {   # 11-04 #218 follow-up (accept-218/extra_cases.py): legacy 40-hex tokens, no gh prefix
    "json token spaced": f'{{"token": "{HEX}"}}', "json access_token": f'{{"access_token":"{HEX}"}}', "json key": f'"key":"{HEX}"',
    "GITHUB_TOKEN=": f"GITHUB_TOKEN={HEX}", "secret_key=": f"secret_key={HEX}", "bare Bearer": f"Bearer {HEX}",
}
class Legacy(unittest.TestCase):
    def test_masked(self):
        for name, line in LEGACY.items():
            with self.subTest(name): self.assertNotIn(HEX, O.clip_err(RuntimeError(line), 200))
    def test_shas_unmasked(self):
        for line in (f'{{"sha":"{HEX}"}}', f"head={HEX}", f"commit {HEX}", f"sha:{HEX}", f'{{"head_sha": "{HEX}"}}'):
            with self.subTest(line): self.assertEqual(O.clip_err(RuntimeError(line), 200), line)

if __name__ == "__main__": unittest.main()
