#!/usr/bin/env python3
"""11-04 pi-work/headroom-1104 FINDINGS (a)(b) and the #201 rulings-path override. Local only, no processes, no network.
(a) a running nightly is counted once (its remaining growth), not growth + a whole reserved peak
(b) a priority run and a catch-up run at once are two tests (each up to its own peak), not one
#201: PRFIX_RULINGS overrides prfix's P2 rulings path (read at call time)"""
import os, sys, shutil, tempfile, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin")); sys.path.insert(0, HERE)
from isolation import sandbox_state
sandbox_state()
import prwatch, prfix  # noqa: E402

PRIO = b"bash\0/workspace/panoptes/run-nightly.sh\0--pr\0001728\0--priority\0"
CU = b"bash\0/workspace/panoptes/run-nightly.sh\0--catch-up\0"
NODE = b"node\0src/cli.mjs\0catch-up\0"
CHILD = b"bash\0/workspace/panoptes/run-nightly.sh\0--pr\0001597\0--write-merged\0"


class Headroom(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hr1104-"); self.saved = prwatch.TEST_RESERVE_OFF
        prwatch.TEST_RESERVE_OFF = f"{self.tmp}/prwatch-test-reserve-off"   # absent: reserve rule on (production)
    def tearDown(self): prwatch.TEST_RESERVE_OFF = self.saved; shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_running_nightly_is_not_counted_twice(self):
        pk = prwatch.PANOPTES_PEAK_MB
        t = {100: (1, 1400, PRIO)}
        self.assertEqual(prwatch.headroom_mb(t, mem=9700), 9700 - (pk - 1400))

    def test_b_priority_and_catchup_are_two_runs(self):
        pk = prwatch.PANOPTES_PEAK_MB
        t = {100: (1, 1000, PRIO), 200: (1, 50, CU), 201: (200, 50, NODE), 202: (201, 900, CHILD)}
        self.assertEqual(sorted(prwatch.panoptes_runs(t)), [100, 200])                  # nested catch-up child is part of run 200
        self.assertEqual(prwatch.headroom_mb(t, mem=20000), 20000 - (pk - 1000) - (pk - 1000))


class Rulings(unittest.TestCase):
    def test_prfix_rulings_env_override(self):
        p = os.path.join(tempfile.mkdtemp(), "p2-rulings.md")
        with mock.patch.dict(os.environ, {"PRFIX_RULINGS": p}), mock.patch.dict(prfix.__dict__):
            prfix.__dict__.pop("RULINGS", None)   # no assigned attribute: the env decides
            self.assertEqual(prfix.RULINGS, p)
        self.assertNotEqual(prfix.__dict__.get("RULINGS"), p)


if __name__ == "__main__": unittest.main()
