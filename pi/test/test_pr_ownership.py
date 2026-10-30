#!/usr/bin/env python3
"""lib PR ownership guards (10-30, prfix pushed onto #1444 owned by another worker): branch claims, foreign commits, PROTECTED_RE."""
import datetime as dt, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import lib

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 30, 3, 0, tzinfo=UTC)
def c(sha, msg, hours_ago): return {"sha": sha, "commit": {"message": msg, "committer": {"date": (NOW - dt.timedelta(hours=hours_ago)).isoformat()}}}

class Ownership(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.old = lib.TAKEOVER; lib.TAKEOVER = self.d; os.makedirs(f"{self.d}/branch-claims")
    def tearDown(self):
        lib.TAKEOVER = self.old; import shutil; shutil.rmtree(self.d, ignore_errors=True)
    def claim(self, s, mtime=None):
        p = f"{self.d}/branch-claims/1444"
        with open(p, "w") as f: f.write(s)
        if mtime is not None: os.utime(p, (mtime, mtime))

    def test_foreign_recent_commits(self):
        cs = [c("51802504aa", "fix\n\nPi-Revise: revise:1444:d45", 1), c("3d4737d0aa", "worker fix", 0.2), c("bbbb153caa", "old", 7)]
        self.assertEqual(lib.foreign_recent_commits(cs, NOW), ["3d4737d0a"])
        self.assertEqual(lib.foreign_recent_commits(cs[:1] + cs[2:], NOW), [])
        self.assertEqual(lib.foreign_recent_commits([{"sha": "x", "commit": {"message": "m"}}], NOW), ["x"])   # no date: fail closed
        self.assertIn("3d4737d0a", lib.ownership_block(1444, cs, NOW))
        self.assertIsNone(lib.ownership_block(1444, cs[:1], NOW))

    def test_claim_formats_and_expiry(self):
        self.assertIsNone(lib.branch_claim(1444, NOW))                                       # no file
        self.claim("2026-10-30T12:00:00+08:00\nworker-b: fixing #1409\n")                   # ISO with offset = 04:00Z
        self.assertIn("branch-claims/1444", lib.branch_claim(1444, NOW))
        self.assertIsNone(lib.branch_claim(1444, NOW + dt.timedelta(hours=1, seconds=1)))
        self.claim(str(int((NOW + dt.timedelta(minutes=5)).timestamp())))                     # Unix seconds
        self.assertTrue(lib.branch_claim(1444, NOW)); self.assertIsNone(lib.branch_claim(1444, NOW + dt.timedelta(minutes=6)))
        self.claim("", mtime=NOW.timestamp())                                                 # empty: mtime + CLAIM_TTL_H
        self.assertTrue(lib.branch_claim(1444, NOW + dt.timedelta(hours=lib.CLAIM_TTL_H - 0.1)))
        self.assertIsNone(lib.branch_claim(1444, NOW + dt.timedelta(hours=lib.CLAIM_TTL_H + 0.1)))
        self.claim("tomorrow-ish", mtime=NOW.timestamp())                                     # unparsable: mtime rule
        self.assertTrue(lib.branch_claim(1444, NOW))
        self.assertIn("branch-claims", lib.ownership_block(1444, [], NOW))

    def test_protected_re(self):
        hit = ["src/__tests__/mergeBaseGuard.ts", "src/app/__tests__/manifestVersionPolicy.test.ts", ".githooks/pre-push",
               "scripts/verify.mjs", "scripts/verify-size.mjs"]
        miss = ["src/mergeBaseGuard.ts", "src/__tests__/foo.test.ts", "githooks/x", "a/.githooks/x", "scripts/run-verify.mjs", "src/scripts/verify.mjs"]
        self.assertEqual(lib.protected_files(hit + miss), sorted(hit))

if __name__ == "__main__": unittest.main(verbosity=1)
