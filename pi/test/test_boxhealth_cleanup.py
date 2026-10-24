#!/usr/bin/env python3
"""python3 test/test_boxhealth_cleanup.py -- boxhealth's 7-day cleanup (2026-10-24: really deletes), in a temp dir
(a fake /tmp, a fake HOME with npm / pip caches, a fake QA lock). Never touches the real /tmp or caches."""
import os, sys, time, json, fcntl, tempfile, shutil, subprocess, unittest
T = tempfile.mkdtemp(prefix="boxhealth-test-")
os.environ.update(HOME=f"{T}/home", BOXHEALTH_TMP_ROOT=f"{T}/tmp", PI_STATE=f"{T}/state")
B_ = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B_)
import boxhealth as B   # noqa: E402

def mk(p, days, text="x"):
    os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "w").write(text)
    t = time.time() - days * 86400; os.utime(p, (t, t)); return p
def age_dir(p, days):
    t = time.time() - days * 86400
    for d, ds, fs in os.walk(p, topdown=False):          # files and every directory (a dir's mtime moves when its entries change)
        for x in fs: os.utime(os.path.join(d, x), (t, t))
        os.utime(d, (t, t))

class TestCleanup(unittest.TestCase):
    def setUp(self):
        for d in ("home", "tmp"): shutil.rmtree(f"{T}/{d}", ignore_errors=True); os.makedirs(f"{T}/{d}")
        B.QA_LOCKS = [f"{T}/nightly.lock"]; open(B.QA_LOCKS[0], "a").close()
    def paths(self): return {p: k for p, k in B.cleanup_candidates()[0]}

    def test_seven_days_unused_and_not_held(self):
        mk(f"{T}/tmp/old/sub/f", 8); age_dir(f"{T}/tmp/old", 8)
        mk(f"{T}/tmp/new/f", 6); age_dir(f"{T}/tmp/new", 6)
        mk(f"{T}/tmp/mixed/a", 9); mk(f"{T}/tmp/mixed/b", 1); os.utime(f"{T}/tmp/mixed", (time.time() - 9 * 86400,) * 2)
        mk(f"{T}/tmp/old.txt", 10); mk(f"{T}/tmp/argus-run-1/f", 30); mk(f"{T}/tmp/cindy-launch.log", 30); mk(f"{T}/tmp/cindy.deb", 0)
        mk(f"{T}/tmp/busy/f", 9); age_dir(f"{T}/tmp/busy", 9)
        p = subprocess.Popen(["sleep", "30"], cwd=f"{T}/tmp/busy")
        try: c = self.paths()
        finally: p.kill(); p.wait()
        self.assertEqual(c.get(f"{T}/tmp/old"), "tree"); self.assertEqual(c.get(f"{T}/tmp/old.txt"), "file")
        self.assertEqual(c.get(f"{T}/tmp/cindy.deb"), "file")
        for keep in ("new", "mixed", "argus-run-1", "cindy-launch.log", "busy"): self.assertNotIn(f"{T}/tmp/{keep}", c, keep)

    def test_caches_per_file_and_logs_cut(self):
        mk(f"{T}/home/.npm/_cacache/content-v2/a", 8); mk(f"{T}/home/.npm/_cacache/content-v2/b", 2); mk(f"{T}/home/.cache/pip/x", 8)
        big = f"{T}/big.log"; open(big, "wb").write(b"0" * (B.LOG_MAX + 10) + b"TAIL"); B.LOGS_CAP = [big]
        c = self.paths()
        self.assertIn(f"{T}/home/.npm/_cacache/content-v2/a", c); self.assertNotIn(f"{T}/home/.npm/_cacache/content-v2/b", c)
        self.assertIn(f"{T}/home/.cache/pip/x", c); self.assertEqual(c.get(big), "trunc")
        B.really_clean([(p, k) for p, k in c.items() if k != "sudo"])
        self.assertFalse(os.path.exists(f"{T}/home/.npm/_cacache/content-v2/a")); self.assertTrue(os.path.exists(f"{T}/home/.npm/_cacache/content-v2/b"))
        self.assertEqual(os.path.getsize(big), B.LOG_KEEP); self.assertTrue(open(big, "rb").read().endswith(b"TAIL"))

    def test_nothing_in_tmp_while_qa_holds_its_lock(self):
        mk(f"{T}/tmp/old.txt", 10)
        f = open(B.QA_LOCKS[0], "a"); fcntl.flock(f, fcntl.LOCK_EX)
        try:
            c, locks = B.cleanup_candidates(); self.assertEqual(locks, B.QA_LOCKS); self.assertNotIn(f"{T}/tmp/old.txt", dict(c))
        finally: f.close()
        c, locks = B.cleanup_candidates(); self.assertEqual(locks, []); self.assertIn(f"{T}/tmp/old.txt", dict(c))   # a lock FILE alone: not running



if __name__ == "__main__":
    try: unittest.main(verbosity=1)
    finally: shutil.rmtree(T, ignore_errors=True)
