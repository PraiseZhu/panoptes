#!/usr/bin/env python3
"""python3 test/test_boxhealth_cleanup.py -- boxhealth's 7-day cleanup (2026-10-24: really deletes), in a temp dir
(a fake /tmp, a fake HOME with npm / pip caches, a fake QA lock). Never touches the real /tmp or caches."""
import os, sys, time, json, fcntl, tempfile, shutil, subprocess, unittest
T = tempfile.mkdtemp(prefix="boxhealth-test-")
os.environ.update(HOME=f"{T}/home", BOXHEALTH_TMP_ROOT=f"{T}/tmp", PI_STATE=f"{T}/state", BOXHEALTH_LOG_DIRS=f"{T}/logs1:{T}/logs2")
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
        for d in ("home", "tmp", "logs1", "logs2"): shutil.rmtree(f"{T}/{d}", ignore_errors=True); os.makedirs(f"{T}/{d}")
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

    def test_qa_holding_its_lock_does_not_stop_tmp(self):   # 10-24 review: it used to skip all of /tmp, often for the whole :17 slot
        mk(f"{T}/tmp/old.txt", 10); mk(f"{T}/tmp/argus-run-9/f", 30); age_dir(f"{T}/tmp/argus-run-9", 30)
        f = open(B.QA_LOCKS[0], "a"); fcntl.flock(f, fcntl.LOCK_EX)
        try: c, locks = B.cleanup_candidates()
        finally: f.close()
        self.assertEqual(locks, B.QA_LOCKS); self.assertIn(f"{T}/tmp/old.txt", dict(c)); self.assertNotIn(f"{T}/tmp/argus-run-9", dict(c))

    def test_logs_older_than_7_days_by_name_else_mtime(self):
        old = (B.now_cst().date() - B.dt.timedelta(days=9)); edge = (B.now_cst().date() - B.dt.timedelta(days=7))
        mk(f"{T}/logs1/nightly-{old:%Y%m%d}T010101.log", 0)            # restored yesterday, about 9 days ago: by name -> goes
        mk(f"{T}/logs1/prwatch-{edge:%Y-%m-%d}.log", 0)                 # exactly 7 days ago: still in the window
        mk(f"{T}/logs1/control.log", 1); mk(f"{T}/logs1/tmp-sweep.log", 9)   # no date: mtime decides
        mk(f"{T}/logs2/mem.log.{old:%Y%m%d}-022110.gz", 0); mk(f"{T}/logs2/mem.log.2.gz", 3); mk(f"{T}/logs2/box-ops-mem.logrotate.bak.{old:%Y%m%d}", 30)
        mk(f"{T}/logs2/severity-pin.json", 30)
        c = self.paths()
        for gone in (f"logs1/nightly-{old:%Y%m%d}T010101.log", "logs1/tmp-sweep.log", f"logs2/mem.log.{old:%Y%m%d}-022110.gz"): self.assertIn(f"{T}/{gone}", c, gone)
        for kept in (f"logs1/prwatch-{edge:%Y-%m-%d}.log", "logs1/control.log", "logs2/mem.log.2.gz", f"logs2/box-ops-mem.logrotate.bak.{old:%Y%m%d}", "logs2/severity-pin.json"):
            self.assertNotIn(f"{T}/{kept}", c, kept)
        p = f"{T}/logs1/held-{old:%Y%m%d}.log"; mk(p, 9); h = open(p)
        try: self.assertNotIn(p, self.paths())                          # a log a process has open stays
        finally: h.close()

if __name__ == "__main__":
    try: unittest.main(verbosity=1)
    finally: shutil.rmtree(T, ignore_errors=True)
