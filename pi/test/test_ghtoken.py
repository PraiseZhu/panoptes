#!/usr/bin/env python3
"""11-04 gh-app-1104 P0: ghtoken helper. A throwaway RSA key is generated per run (openssl); GitHub is mocked; no real key or token
is read or printed. Checks: installation by repo, JWT signed RS256 and verifiable with the throwaway public key, token cached 0600 and
reused, re-minted shortly before expiry, 401/invalidate path, unknown repo -> NoInstallation, tokens masked in every message."""
import base64, io, json, os, shutil, stat, subprocess, sys, tempfile, time, unittest, urllib.error
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import ghtoken as G  # noqa: E402

FAKE = "ghs_" + "T" * 36   # fake token shape, never a real one


def b64d(s): return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp(prefix="ghtoken-"); cls.addClassCleanup(shutil.rmtree, cls.d, True)
        cls.key = os.path.join(cls.d, "throwaway.pem"); cls.pub = os.path.join(cls.d, "throwaway.pub")
        subprocess.run(["openssl", "genrsa", "-out", cls.key, "2048"], check=True, capture_output=True)
        subprocess.run(["openssl", "rsa", "-in", cls.key, "-pubout", "-out", cls.pub], check=True, capture_output=True)

    def setUp(self):
        self.cache = tempfile.mkdtemp(prefix="ghtoken-cache-"); self.addCleanup(shutil.rmtree, self.cache, True)
        self.env = mock.patch.dict(os.environ, {"PRAISE_QA_KEY": self.key, "PRAISE_QA_CACHE": f"{self.cache}/c", "PRAISE_QA_APP_ID": "123"}); self.env.start()
        self.calls = []; self.exp = time.time() + 3600

    def tearDown(self): self.env.stop()

    def urlopen(self, req, timeout=None):
        self.calls.append(req)
        e = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.exp))
        return Resp(json.dumps({"token": FAKE, "expires_at": e}).encode())

    def test_installation_by_repo(self):
        self.assertEqual(G.installation("PraiseZhu/panoptes"), 164262283)
        self.assertEqual(G.installation("xindong/mivo-canvas-plugin"), 178486896)
        with self.assertRaises(G.NoInstallation): G.installation("someone/else")

    def test_jwt_is_rs256_and_verifies_with_the_key(self):
        now = 1_793_700_000
        j = G.jwt(now); h, b, sig = j.split(".")
        self.assertEqual(json.loads(b64d(h)), {"alg": "RS256", "typ": "JWT"})
        self.assertEqual(json.loads(b64d(b)), {"iat": now - 60, "exp": now + 540, "iss": "123"})
        sf = os.path.join(self.cache, "sig"); open(sf, "wb").write(b64d(sig))
        r = subprocess.run(["openssl", "dgst", "-sha256", "-verify", self.pub, "-signature", sf], input=f"{h}.{b}".encode(), capture_output=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_mint_posts_to_the_repo_installation_and_caches_0600(self):
        with mock.patch.object(G.urllib.request, "urlopen", self.urlopen), redirect_stderr(io.StringIO()) as err:
            self.assertEqual(G.token("xindong/mivo-canvas-plugin"), FAKE)
            self.assertEqual(G.token("xindong/mivo-canvas-plugin"), FAKE)    # cached: no second mint
        self.assertEqual(len(self.calls), 1)
        req = self.calls[0]
        self.assertEqual(req.get_method(), "POST"); self.assertTrue(req.full_url.endswith("/app/installations/178486896/access_tokens"))
        self.assertTrue(req.headers["Authorization"].startswith("Bearer "))
        f = os.path.join(self.cache, "c", "inst-178486896.json")
        self.assertEqual(stat.S_IMODE(os.stat(f).st_mode), 0o600); self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(f)).st_mode), 0o700)
        self.assertNotIn(FAKE, err.getvalue()); self.assertIn("minted for installation 178486896", err.getvalue())

    def test_remints_shortly_before_expiry_and_after_invalidate(self):
        with mock.patch.object(G.urllib.request, "urlopen", self.urlopen), redirect_stderr(io.StringIO()):
            self.exp = time.time() + G.MARGIN_S - 30; G.token()           # cached token has < 10 min left
            self.exp = time.time() + 3600; G.token()
            self.assertEqual(len(self.calls), 2)
            G.token(); self.assertEqual(len(self.calls), 2)
            G.invalidate(); G.token(); self.assertEqual(len(self.calls), 3)

    def test_errors_and_cli_never_show_a_token(self):
        def bad(req, timeout=None): raise urllib.error.HTTPError(req.full_url, 401, "x", {}, io.BytesIO(f'{{"message":"Bad credentials {FAKE}"}}'.encode()))
        with mock.patch.object(G.urllib.request, "urlopen", bad), redirect_stderr(io.StringIO()) as err, redirect_stdout(io.StringIO()) as out:
            self.assertEqual(G.main(["check"]), 2)
        self.assertNotIn(FAKE, err.getvalue() + out.getvalue()); self.assertIn("HTTP 401", err.getvalue()); self.assertIn("ghs_***", err.getvalue())
        with mock.patch.object(G.urllib.request, "urlopen", self.urlopen), redirect_stderr(io.StringIO()) as err, redirect_stdout(io.StringIO()) as out:
            self.assertEqual(G.main(["check"]), 0)
        self.assertNotIn(FAKE, err.getvalue() + out.getvalue()); self.assertIn("installation 164262283", out.getvalue())
        with redirect_stderr(io.StringIO()) as err: self.assertEqual(G.main(["check", "someone/else"]), 2)
        self.assertIn("keep this caller on the PAT", err.getvalue())

    def test_missing_key_is_a_clear_error(self):
        with mock.patch.dict(os.environ, {"PRAISE_QA_KEY": os.path.join(self.cache, "nope.pem")}):
            with self.assertRaises(G.MintError): G.jwt()


if __name__ == "__main__": unittest.main()
