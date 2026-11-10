#!/usr/bin/env python3
"""cindy.py: 6 lock-SKIPs in a row are a FAIL (10-24: ensure-cindy.sh's lock fd leaked into the nudge-loop it starts, every later
keep-alive run said SKIP and counted as OK). Local only: temp state file. Run: cd bin && python3 ../test/test_cindy_guard.py"""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import os, shutil, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import cindy  # noqa: E402

SKIP = "STATUS=SKIP another ensure-cindy run holds lock"
# Mirrors ensure-cindy.sh:9 (`exec 8>/tmp/ensure-cindy.v2.lock`). Fixture only, not the live script.
_ENSURE_CINDY_LOCK = "/tmp/ensure-cindy.v2.lock"

class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cindy-test-"); self.saved = cindy.NSTATE; cindy.NSTATE = f"{self.tmp}/notify-state.json"
    def tearDown(self):
        cindy.NSTATE = self.saved; shutil.rmtree(self.tmp, ignore_errors=True)
    def test_sixth_lock_skip_in_a_row_is_a_fail_and_any_other_status_resets(self):
        self.assertEqual([cindy.lock_skip_guard(SKIP) for _ in range(5)], [SKIP] * 5)
        self.assertTrue(cindy.lock_skip_guard(SKIP).startswith("STATUS=FAIL ensure-cindy.sh lock held for 6 runs"))
        self.assertEqual(cindy.lock_skip_guard("STATUS=OK"), "STATUS=OK")
        self.assertEqual(cindy.lock_skip_guard(SKIP), SKIP)                          # counting starts again
        self.assertEqual(cindy.lock_skip_guard("STATUS=SKIP pi-line takeover/cindy active"), "STATUS=SKIP pi-line takeover/cindy active")

    def test_lock_path_matches_ensure_cindy_sh(self):
        # 11-10: ensure-cindy.sh:9 is `exec 8>/tmp/ensure-cindy.v2.lock` (old /tmp/ensure-cindy.lock
        # is held forever by an inherited fd). Constant only: do not open the live script or take the lock.
        self.assertEqual(cindy.LOCK, _ENSURE_CINDY_LOCK)
        self.assertIn(_ENSURE_CINDY_LOCK, cindy.__doc__)

    def test_lowmem_blocked_relaunch_is_reported_once_per_episode(self):
        acts = []; saved = cindy.record_actions; cindy.record_actions = lambda job, day, a: acts.extend(a)
        try:
            msg = "STATUS=FAIL Cindy 没在运行；机器可用内存不到 2GB，为了不拖垮别的任务暂时不拉起，内存回来后下一个 5 分钟会自动拉起"
            for _ in range(3): cindy.notify("2026-10-24", msg)
            self.assertEqual(len(acts), 1)                                           # same failure: not repeated
            cindy.notify("2026-10-24", "STATUS=OK"); cindy.notify("2026-10-24", msg)
            self.assertEqual(len(acts), 2)                                           # a new episode after it was OK again
        finally: cindy.record_actions = saved

if __name__ == "__main__":
    unittest.main(verbosity=1)
