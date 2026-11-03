#!/usr/bin/env python3
"""11-03 concurrency plan items 2-4 (tests first): prwatch always reserves a merge-test peak, runs at most 2 fixes and starts none
while a merge test is queued; prfix waits between steps while a merge test runs or is queued; MemAvailable < 2G for 30 s kills the
current step of a fix and the ticket goes back to the queue without using up a retry. Each has a flag-file rollback.
Local only: no network, no real processes killed, no writes outside a temp dir. Run: cd bin && python3 ../test/test_fix_yield_reserve_kill.py"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin")); sys.path.insert(0, HERE)
from isolation import sandbox_state
sandbox_state()
os.environ.setdefault("PRFIX_OWNERS", "*")
import json, shutil, tempfile, types, unittest  # noqa: E402
import prwatch, prfix  # noqa: E402

PY = b"python3\0" + os.fsencode(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin", "prfix.py")) + b"\0--next\0"
FOREIGN = b"python3\0/workspace/pi-work/other/pi/bin/prfix.py\0--next\0"
PAN = b"bash\0/workspace/panoptes/run-nightly.sh\0--pr\0001188\0"
DEAD = 4194305   # above any pid_max: never a live pid
# the box numbers in the plan (DESIGN.md §1): learned peaks 5894*1.3 = 7662 (merge test), 2158*1.3 = 2805 (fix)
ST = {"peaks": {"panoptes": [3900, 5894, 2897], "fix": [735, 2158, 1450]}}
def touch(p):
    os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "w").close()


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="yrk-")
        self.saved = {k: getattr(prwatch, k) for k in ("BASE", "TEST_RESERVE_OFF", "EMERG_KILL_OFF", "ARGUS", "mem_mb", "proc_table",
                                                       "time", "qlog", "DRY", "STATE")}
        self.saved_pf = {k: getattr(prfix, k) for k in ("mem_gb", "YIELD_OFF", "time")}
        prwatch.BASE = self.tmp; prwatch.ARGUS = f"{self.tmp}/argus"; prwatch.DRY = False; prwatch.STATE = f"{self.tmp}/state.json"
        prwatch.TEST_RESERVE_OFF = f"{self.tmp}/prwatch-test-reserve-off"; prwatch.EMERG_KILL_OFF = f"{self.tmp}/prwatch-emerg-kill-off"
        prfix.YIELD_OFF = f"{self.tmp}/prfix-yield-off"
        self.logs = []; prwatch.qlog = self.logs.append
        os.makedirs(f"{prwatch.ARGUS}/priority")
    def tearDown(self):
        for k, v in self.saved.items(): setattr(prwatch, k, v)
        for k, v in self.saved_pf.items(): setattr(prfix, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)
    def slot(self, kind, pid): touch(f"{prwatch.ARGUS}/priority/{kind}-{pid}")
    def procs(self, t, mem): prwatch.proc_table = lambda: t; prwatch.mem_mb = lambda: mem


class Reserve(T):
    def test_merge_test_peak_reserved_with_nothing_running(self):
        self.assertEqual(prwatch.headroom_mb({}, mem=11000), 11000 - prwatch.PANOPTES_PEAK_MB)
        touch(prwatch.TEST_RESERVE_OFF)                                              # rollback: the first fix needs no reserve
        self.assertEqual(prwatch.headroom_mb({}, mem=11000), 11000)

    def test_running_test_keeps_its_growth_and_the_next_one_reserved(self):
        t = {50: (1, 1400, PAN)}
        self.assertEqual(prwatch.headroom_mb(t, mem=9700), 9700 - (prwatch.PANOPTES_PEAK_MB - 1400) - prwatch.PANOPTES_PEAK_MB)

    def test_box_baseline_admits_exactly_one_fix(self):   # DESIGN.md §2.2: 12006 - 7662 = 4344 >= 1536 + 2805
        self.procs({}, 12006); self.assertIsNone(prwatch.admit_block({}, ST)[0])
        self.procs({}, 11900); self.assertIn("headroom", prwatch.admit_block({}, ST)[0])

    def test_at_most_two_fixes_even_with_memory(self):
        t = {10: (1, 300, PY), 20: (1, 300, PY)}
        self.procs(t, 64000)
        self.assertIn("max 2", prwatch.admit_block(t, ST)[0])
        touch(prwatch.TEST_RESERVE_OFF); self.assertIsNone(prwatch.admit_block(t, ST)[0])   # rollback: memory alone

    def test_no_fix_starts_while_a_merge_test_is_queued(self):
        self.procs({}, 64000)
        self.slot("req", DEAD); self.assertIsNone(prwatch.admit_block({}, ST)[0])       # a dead run's file does not count
        self.slot("req", os.getpid()); self.assertIn("queued", prwatch.admit_block({}, ST)[0])
        self.assertEqual(prwatch.launch_revise(ST, 1634, "abc"), "wait")
        touch(prwatch.TEST_RESERVE_OFF); self.assertIsNone(prwatch.admit_block({}, ST)[0])


class Yield(T):
    def test_waits_while_a_test_runs_or_is_queued_and_memory_is_below_peak_plus_3g(self):
        prfix.mem_gb = lambda: 8.0                                                      # 8192 MB >= 3G
        self.assertIsNone(prfix.mem_wait_reason())                                      # no test: go
        self.slot("conc", os.getpid())
        self.assertIn("合并测试在跑或排队", prfix.mem_wait_reason())                      # 8192 < 6144 + 3072
        prfix.mem_gb = lambda: 9.5; self.assertIsNone(prfix.mem_wait_reason())          # 9728 >= 9216
        prfix.mem_gb = lambda: 8.0; touch(prfix.YIELD_OFF); self.assertIsNone(prfix.mem_wait_reason())   # rollback

    def test_queued_test_and_held_nightly_lock_count_as_test_activity(self):
        import fcntl
        prfix.mem_gb = lambda: 8.0
        self.slot("req", os.getpid()); self.assertIsNotNone(prfix.mem_wait_reason())
        os.remove(f"{prwatch.ARGUS}/priority/req-{os.getpid()}"); self.assertIsNone(prfix.mem_wait_reason())
        with open(f"{prwatch.ARGUS}/nightly.lock", "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            self.assertIsNotNone(prfix.mem_wait_reason())                               # nightly / catch-up / on-merge lock

    def test_below_3g_still_waits_without_any_test(self):
        prfix.mem_gb = lambda: 2.0; touch(prfix.YIELD_OFF)
        self.assertIn("< 3G", prfix.mem_wait_reason())

    def test_timeout_puts_the_ticket_back_as_waiting(self):
        prfix.mem_gb = lambda: 8.0; self.slot("conc", os.getpid())
        clock = {"t": 0}
        prfix.time = types.SimpleNamespace(time=lambda: clock["t"], sleep=lambda s: clock.__setitem__("t", clock["t"] + s))
        with self.assertRaises(prfix.Waiting) as e: prfix.yield_to_panoptes()
        self.assertIn("合并测试在跑或排队", str(e.exception))
        tk, _ = prfix.apply_outcome(prfix.new_ticket({"qa": 1, "fp": "mq1-x", "sev": "P2"}),
                                    {"status": "failed", "kind": "waiting", "error": str(e.exception)}, prfix.now_cst())
        self.assertEqual(tk.get("transient_failures"), 0)                              # not a failed try


class EmergKill(T):
    def setUp(self):
        super().setUp()
        self.clock = {"t": 0}; self.mem = [1500]; self.killed = []
        prwatch.time = types.SimpleNamespace(time=lambda: self.clock["t"], sleep=lambda s: self.clock.__setitem__("t", self.clock["t"] + s))
        prwatch.mem_mb = lambda: self.mem[0]
        self.real_kill = os.kill
        os.kill = lambda pid, sig: self.killed.append((pid, sig)) if sig else self.real_kill(pid, sig)
        # fix A (pid 10, small) and fix B (pid 20: its step = vitest 21 -> worker 22); a merge test that must never be touched
        self.t = {10: (1, 100, PY), 11: (10, 200, b"node\0npm\0"), 20: (1, 100, PY), 21: (20, 1500, b"node\0vitest\0"),
                  22: (21, 900, b"node\0worker\0"), 50: (1, 3000, PAN)}
        prwatch.proc_table = lambda: self.t
    def tearDown(self):
        os.kill = self.real_kill; super().tearDown()

    def test_kills_the_biggest_fix_step_not_prfix_and_not_the_test(self):
        prwatch.emerg_kill(st := {})
        self.assertEqual(sorted({p for p, _ in self.killed}), [21, 22])
        with open(prwatch.emerg_mark(20)) as f: mark = json.load(f)
        self.assertEqual(sorted(mark["pids"]), [21, 22]); self.assertEqual(st["emerg_kills"][-1]["prfix"], 20)

    def test_foreign_path_prfix_is_never_selected(self):
        # the biggest "fix" is another checkout's prfix.py (pid 30 -> step 31): only this repo's fix B may be hit
        self.t.update({30: (1, 100, FOREIGN), 31: (30, 5000, b"node\0vitest\0")})
        prwatch.emerg_kill({})
        self.assertNotIn(31, [p for p, _ in self.killed]); self.assertEqual(sorted({p for p, _ in self.killed}), [21, 22])
        self.t = {30: (1, 100, FOREIGN), 31: (30, 5000, b"node\0vitest\0")}; self.killed.clear()
        prwatch.emerg_kill({}); self.assertEqual(self.killed, [])

    def test_sigkill_reverifies_victims(self):
        # during the SIGTERM grace 21 exits and its pid is reused by an unrelated process; 22 is gone: SIGKILL hits neither
        def sleep(s):
            self.clock["t"] += s
            if s == prwatch.EMERG_GRACE_S:
                self.t = {k: v for k, v in self.t.items() if k not in (21, 22)}; self.t[21] = (1, 50, b"sshd\0")
        prwatch.time = types.SimpleNamespace(time=lambda: self.clock["t"], sleep=sleep)
        prwatch.emerg_kill({})
        self.assertEqual([p for p, s in self.killed if s == prwatch.signal.SIGKILL], [])
        self.assertEqual(sorted(p for p, s in self.killed if s == prwatch.signal.SIGTERM), [21, 22])

    def test_memory_back_within_30s_kills_nothing(self):
        orig = prwatch.time.sleep
        def sleep(s):
            orig(s)
            if self.clock["t"] >= 20: self.mem[0] = 2600
        prwatch.time.sleep = sleep
        prwatch.emerg_kill({}); self.assertEqual(self.killed, [])

    def test_no_fix_running_or_flag_off_kills_nothing(self):
        touch(prwatch.EMERG_KILL_OFF); prwatch.emerg_kill({}); self.assertEqual(self.killed, [])
        os.remove(prwatch.EMERG_KILL_OFF); self.t = {50: (1, 3000, PAN)}; prwatch.emerg_kill({}); self.assertEqual(self.killed, [])

    def test_prfix_reads_the_mark_requeues_without_a_retry_and_stale_marks_go(self):
        touch(prwatch.emerg_mark(os.getpid()))
        with self.assertRaises(prfix.Waiting): prfix.run(["true"])
        self.assertFalse(os.path.exists(prwatch.emerg_mark(os.getpid())))
        self.assertEqual(prfix.run(["true"])[0], 0)                                    # mark consumed: next step runs
        touch(prwatch.emerg_mark(DEAD)); self.mem[0] = 9000; prwatch.emerg_kill({})
        self.assertFalse(os.path.exists(prwatch.emerg_mark(DEAD)))


if __name__ == "__main__":
    unittest.main()
