#!/usr/bin/env python3
"""11-04 accept-206 round 2, item 3a reworked. Local only, no processes, no network.
- the 'second test starts at 6 GB free' case (PM worked numbers) can no longer overcommit the box: whenever a fix is admitted beside a
  running test, running test growth + the fix + a whole concurrent second test still fit in RAM (no swap);
- the rollback flag prwatch-reserve-1104-off gives decisions identical to the 11-03 headroom_mb (source embedded below, verbatim
  from a012fe5) on the same inputs;
- with concurrent on-merge runs off (PANOPTES_ONMERGE_CONCURRENT=0: one test at a time), a running test is the only one, so no
  second peak is stacked on it."""
import itertools, os, sys, shutil, tempfile, textwrap, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin")); sys.path.insert(0, HERE)
from isolation import sandbox_state
sandbox_state()
import prwatch  # noqa: E402

BOX_MB = 16002   # this box: free -m total, no swap counted
PY = b"python3\0" + os.fsencode(os.path.join(HERE, "..", "bin", "prfix.py")) + b"\0--next\0"
PRIO = b"bash\0/workspace/panoptes/run-nightly.sh\0--pr\0001728\0--priority\0"
CU = b"bash\0/workspace/panoptes/run-nightly.sh\0--catch-up\0"
NODE = b"node\0src/cli.mjs\0catch-up\0"
CHILD = b"bash\0/workspace/panoptes/run-nightly.sh\0--pr\0001597\0--write-merged\0"
# learned peaks of the PM's worked case: int(4729 * 1.3) = 6147 (panoptes), int(2876 * 1.3) = 3738 (fix)
ST = {"peaks": {"panoptes": [4729] * 3, "fix": [2876] * 3}}

# headroom_mb as of 11-03 (git show a012fe5:pi/bin/prwatch.py), verbatim
SRC_1103 = textwrap.dedent('''
def headroom_mb(t, mem=None, st=None):
    run_pk, pan_pk = learned(st, "fix", RUN_PEAK_MB), learned(st, "panoptes", PANOPTES_PEAK_MB)
    pys, waiting = prfix_runs(t)
    grow = sum(max(0, run_pk - sum(t[q][1] for q in tree(t, p))) for p in pys) + run_pk * len(waiting)
    disp = dispatch_runs(t)   # fleet Pi tasks share the same RAM: count them up to their peak too
    grow += sum(max(0, TASK_PEAK_MB - sum(t[q][1] for q in tree(t, p))) for p in disp)
    runs = panoptes_runs(t)
    # 11-04 (headroom-1104 b): each running test up to its own peak (priority + catch-up at once = two tests, not one)
    grow += sum(max(0, pan_pk - sum(t[q][1] for q in ps)) for ps in runs.values())
    pan = bool(runs)
    if flag_on(TEST_RESERVE_OFF):                    # always room for the next merge test, whatever runs now --
        # but with nothing running the first fix is not stacked on a whole test peak (11-03: 9030 + 8687 MB > a 16 GB box)
        grow += pan_pk if (pan or pys or waiting or disp) else min(pan_pk, TEST_RESERVE_CAP_MB)
    elif not pan and (pys or waiting or disp): grow += pan_pk
    return (mem_mb() if mem is None else mem) - grow
''')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hr3a-")
        self.saved = {k: getattr(prwatch, k) for k in ("BASE", "TEST_RESERVE_OFF", "mem_mb", "tests_queued") if hasattr(prwatch, k)}
        self.saved_r = getattr(prwatch, "RESERVE_1104_OFF", None)
        prwatch.BASE = self.tmp; prwatch.TEST_RESERVE_OFF = f"{self.tmp}/prwatch-test-reserve-off"
        prwatch.RESERVE_1104_OFF = f"{self.tmp}/prwatch-reserve-1104-off"
        prwatch.tests_queued = lambda: 0
        self.env = mock.patch.dict(os.environ, {"PANOPTES_ONMERGE_CONCURRENT": "1", "PANOPTES_ONMERGE_MAX_CONC": "2"}); self.env.start()
    def tearDown(self):
        self.env.stop()
        for k, v in self.saved.items(): setattr(prwatch, k, v)
        if self.saved_r is None: prwatch.__dict__.pop("RESERVE_1104_OFF", None)
        else: prwatch.RESERVE_1104_OFF = self.saved_r
        shutil.rmtree(self.tmp, ignore_errors=True)
    def admitted(self, t, mem):
        prwatch.mem_mb = lambda: mem
        with mock.patch.object(prwatch, "jload", return_value={}):
            return prwatch.admit_block(t, dict(ST))[0] is None


class SecondTestAt6G(Base):
    def test_pm_worked_case_fix_not_admitted(self):
        self.assertFalse(self.admitted({100: (1, 1400, PRIO)}, 10500))

    def test_admitted_fix_plus_second_concurrent_test_fits_in_ram(self):
        pan_pk, fix_pk = prwatch.learned(ST, "panoptes", 0), prwatch.learned(ST, "fix", 0)
        for mem, rss in itertools.product(range(6000, 16001, 250), range(200, 6147, 300)):
            if not self.admitted({100: (1, rss, PRIO)}, mem): continue
            # worst case: running test reaches its peak, the admitted fix reaches its peak, qa-slot admits a 2nd test (>= 6 GB) that peaks
            end_free = mem - (pan_pk - rss) - fix_pk - pan_pk
            self.assertGreaterEqual(end_free, 0, f"MemAvailable {mem}, test at {rss} MB: fix admitted but end state needs {-end_free} MB more than RAM")


class Rollback1103(Base):
    def test_flag_gives_identical_decisions_to_1103_code(self):
        open(prwatch.RESERVE_1104_OFF, "w").close()
        exec(SRC_1103.replace("def headroom_mb(", "def _headroom_mb_1103_ref("), prwatch.__dict__)   # same module globals (mem_mb, flags)
        self.addCleanup(prwatch.__dict__.pop, "_headroom_mb_1103_ref", None); old = prwatch._headroom_mb_1103_ref
        shapes = [{}, {10: (1, 900, PY)}, {100: (1, 1400, PRIO)}, {100: (1, 1000, PRIO), 200: (1, 50, CU), 201: (200, 50, NODE), 202: (201, 900, CHILD)},
                  {10: (1, 900, PY), 100: (1, 3000, PRIO), 200: (1, 50, CU), 202: (200, 2500, CHILD)}, {10: (1, 300, PY), 20: (1, 2900, PY)}]
        for reserve_off, t, mem in itertools.product((False, True), shapes, (3000, 7413, 10500, 16000)):
            if reserve_off: open(prwatch.TEST_RESERVE_OFF, "w").close()
            elif os.path.exists(prwatch.TEST_RESERVE_OFF): os.remove(prwatch.TEST_RESERVE_OFF)
            for st in (None, ST):
                self.assertEqual(prwatch.headroom_mb(t, mem=mem, st=st), old(t, mem=mem, st=st), (reserve_off, t, mem, st))
                prwatch.mem_mb = lambda: mem
                with mock.patch.object(prwatch, "jload", return_value={}):
                    new = prwatch.admit_block(t, st)
                    with mock.patch.object(prwatch, "headroom_mb", old): want = prwatch.admit_block(t, st)
                self.assertEqual(new, want, (reserve_off, t, mem, st))


class OneTestAtATime(Base):
    def test_no_second_peak_when_concurrent_on_merge_is_off(self):
        pk = prwatch.learned(ST, "panoptes", 0)
        with mock.patch.dict(os.environ, {"PANOPTES_ONMERGE_CONCURRENT": "0"}):
            self.assertEqual(prwatch.headroom_mb({100: (1, 1400, PRIO)}, mem=12000, st=ST), 12000 - (pk - 1400))
        self.assertEqual(prwatch.headroom_mb({100: (1, 1400, PRIO)}, mem=12000, st=ST), 12000 - (pk - 1400) - pk)   # default: kept


if __name__ == "__main__": unittest.main()
