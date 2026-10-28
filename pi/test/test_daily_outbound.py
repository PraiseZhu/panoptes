#!/usr/bin/env python3
"""daily -> outbound (10-28): a box pause pushed the daily report send past 09:00, so daily now runs at 07:35 Beijing (08:35 = retry)
and, right after recording its actions, calls outbound.py once for its own keys only (same gates and done-ledger). Scratch state
only (the outbound_resend harness: temp BASE/ledger, fake Slack and sender). No network, no sends. Run: python3 test/test_daily_outbound.py"""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import os, re, sys, types, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import test_outbound_resend as R  # noqa: E402  (harness only; its tests are not collected here)
import daily as D  # noqa: E402

DAY = "2026-10-26"; OWN = [f"mivo-daily-report:{DAY}", f"dm:{DAY}:101"]; OTHER = "overdue:aj0928/aj0928-handoff#6"


class SendOwn(unittest.TestCase):
    def test_command_is_outbound_job_daily_with_only_own_keys(self):
        calls = []
        rc = D.send_own(DAY, OWN, run=lambda cmd, **kw: calls.append(cmd) or types.SimpleNamespace(returncode=0))
        self.assertEqual(rc, 0); self.assertEqual(len(calls), 1); c = calls[0]
        self.assertTrue(c[1].endswith("/bin/outbound.py")); self.assertEqual(c[c.index("--job") + 1], "daily")
        self.assertEqual([c[i + 1] for i, x in enumerate(c) if x == "--key"], OWN)
        for bad in ("--resend-key", "--dry-run"): self.assertNotIn(bad, c)
    def test_no_keys_no_call_and_failure_never_raises(self):
        self.assertIsNone(D.send_own(DAY, [], run=lambda *a, **k: self.fail("called")))
        def boom(*a, **k): raise OSError("x")
        self.assertIsNone(D.send_own(DAY, OWN, run=boom))


Harness = type("Harness", (unittest.TestCase,), {k: v for k, v in vars(R.T).items() if not k.startswith("test_")})   # setUp/tearDown only


class Scoped(Harness):   # the real outbound.main with the args send_own builds
    def setUp(self):
        super().setUp(); self.history = []
        self.daily(R.BODY.replace("10 月 25 日", "10 月 26 日"), DAY)
    def own_args(self, keys):
        got = []; D.send_own(DAY, keys, run=lambda cmd, **kw: got.append(cmd) or types.SimpleNamespace(returncode=0)); return got[0][2:]
    def test_sends_only_daily_key_not_other_jobs(self):
        self.assertEqual(self.main(*self.own_args([OWN[0]])), 0)
        self.assertEqual([k for k, _ in self.sends], [OWN[0]]); self.assertNotIn(OTHER, self.ledger())
    def test_key_in_ledger_is_not_resent(self):
        self.ledger({OWN[0]: {"status": "done", "at": "2026-10-27T07:36:00+08:00"}})
        self.assertEqual(self.main(*self.own_args([OWN[0]])), 0); self.assertEqual(self.sends, [])
    def test_retry_run_is_idempotent(self):
        for _ in range(2): self.main(*self.own_args([OWN[0]]))
        self.assertEqual(len(self.sends), 1); self.assertEqual(self.ledger()[OWN[0]]["status"], "done")
    def test_gates_still_apply(self):
        self.mem(1000, 4096); self.main(*self.own_args([OWN[0]])); self.assertEqual(self.sends, [])   # memory floor
        self.mem(8192, 4096); os.remove(R.O.OUTBOUND_FLAG); self.main(*self.own_args([OWN[0]])); self.assertEqual(self.sends, [])


class Cron(unittest.TestCase):
    def test_daily_cron_line(self):
        src = open(os.path.join(os.path.dirname(HERE), "cron", "pi-line.cron")).read()
        lines = [l for l in src.splitlines() if re.search(r"\$R daily\b", l) and not l.startswith("#")]
        self.assertEqual(lines, ["35 * * * * box $R daily @07,08"])
        import watchdog as WD; self.assertEqual(WD.DAILY["daily"], [(7, 35), (8, 35)])


if __name__ == "__main__": unittest.main()
