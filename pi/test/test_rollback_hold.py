#!/usr/bin/env python3
"""10-28 ROLLBACK-SUSPECTED 外发暂停：标记出现 / 持续 / 删掉 三段，分别验 outage 补跑（外发型作业跳过、不落 replayed、删标记后重起）
和 outbound（只出计划、不发、不动账本；删标记后下一轮照发）。临时 PI_STATE + 临时标记路径，假 runner / 假 Slack，不联网、不外发。
Run: python3 test/test_rollback_hold.py"""
import os, sys, json, tempfile, unittest, datetime as dt
tmp = tempfile.mkdtemp(prefix="rollback-hold-"); MARK = f"{tmp}/ROLLBACK-SUSPECTED"
os.environ.update(PI_STATE=tmp, PI_OUTAGE_WITNESS=f"{tmp}/witness", PI_ROLLBACK_MARK=MARK, PI_OUTAGE_NO_SPAWN="1")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import rollback_hold as RH, outage as OG  # noqa: E402
import test_outbound_resend as TR  # noqa: E402  (scratch outbound fixture: fake Slack + fake sender)
CST = OG.CST
T = lambda h, mi=0, d=28: dt.datetime(2026, 10, d, h, mi, tzinfo=CST)
on = lambda: open(MARK, "w").close()
off = lambda: os.path.exists(MARK) and os.remove(MARK)


class Outage(unittest.TestCase):
    W = {"kind": "pause", "start": T(8, 0).isoformat(), "end": T(12, 0).isoformat(), "gap_min": 240, "why": ["heartbeat gap"]}
    def setUp(self):
        off()
        for p in (OG.REPLAYED(), OG.HELD(), OG.WIN()):
            if os.path.exists(p): os.remove(p)
        OG.jw(OG.WIN(), [self.W])
    def test_shared_helper_and_classification(self):
        self.assertEqual(RH.mark_path(), MARK); self.assertFalse(RH.active()); on(); self.assertTrue(RH.active())
        for j in ("daily", "pm", "opsreview", "followup", "jasper", "pmnudge", "handoff", "prretro", "prfix", "opsfix", "catchup", "boxcheck-unknown"):
            self.assertTrue(OG.sends(j), j)
        for j in ("credcheck", "dailyretro", "compare", "ledger", "roster", "p2"): self.assertFalse(OG.sends(j), j)
        self.assertTrue(all(j in OG.POLICY for j in OG.SENDS))   # no typo: every send job is a known job
    def test_marker_appears_persists_removed(self):
        now = T(12, 1)
        items, sk = OG._plan(self.W, now, {}); jobs = {x[0] for x in items}
        self.assertTrue({"daily", "pm", "opsreview"} <= jobs)                       # no marker: senders planned as before
        on()   # marker appears (after the pause window was classified)
        items, sk = OG._plan(self.W, now, {}); jobs = {x[0] for x in items}
        self.assertFalse(jobs & OG.SENDS, jobs); self.assertIn("dailyretro", jobs)    # non-senders still re-run
        held = {x[0] for x in sk if x[2] == OG.HOLD_WHY}; self.assertTrue({"daily", "pm", "opsreview"} <= held, sk)
        OG.mark_skipped(sk); r = OG.jl(OG.REPLAYED(), {})
        self.assertFalse(any(v["status"].startswith(f"skipped:{OG.HOLD_WHY}") for v in r.values()), r)   # held != skipped forever
        calls = []; OG.replay(items + [("pm", "2026-10-27", T(10, 35).isoformat(timespec="minutes"))], runner=lambda c: calls.append(c) or 0, w=self.W)
        self.assertNotIn("pm", [c[1] for c in calls]); self.assertIn("dailyretro", [c[1] for c in calls])  # replay re-checks marker
        OG._hold(self.W, list(held))
        # marker persists: still held, resume does nothing
        items2, _ = OG._plan(self.W, now, OG.jl(OG.REPLAYED(), {})); self.assertFalse({x[0] for x in items2} & OG.SENDS)
        spawned = []; self.assertEqual(OG.resume_held(spawn=spawned.append), []); self.assertEqual(spawned, [])
        # marker removed: resume re-spawns the window once, senders are planned again and run
        off()
        self.assertEqual(OG.resume_held(spawn=spawned.append), [self.W["start"]]); self.assertEqual(spawned, [self.W])
        self.assertEqual(OG.resume_held(spawn=spawned.append), []); self.assertEqual(len(spawned), 1)
        items3, _ = OG._plan(self.W, now, OG.jl(OG.REPLAYED(), {})); self.assertTrue({"daily", "pm", "opsreview"} <= {x[0] for x in items3}, items3)
        calls.clear(); OG.replay(items3, runner=lambda c: calls.append(c) or 0); self.assertIn("pm", [c[1] for c in calls])
    def test_replay_cli_records_held(self):
        on(); sys.argv = ["outage.py", "replay", self.W["start"]]; OG.main()
        self.assertIn("pm", OG.jl(OG.HELD(), {})[self.W["start"]]["jobs"])


class Outbound(TR.T):
    def test_hold_plan_only_then_resume(self):
        K = "mivo-daily-report:2026-10-26"; self.history = []
        self.daily(TR.BODY.replace("10 月 25 日", "10 月 26 日"), "2026-10-26"); self.ledger({"x": {"status": "done"}})
        on()
        try:
            for _ in range(2):   # marker appears, then persists across runs
                self.assertEqual(self.main(), 0)
                self.assertEqual(self.sends, []); self.assertEqual(self.ledger(), {"x": {"status": "done"}})
            rep = open(f"{self.tmp}/out/outbound/{TR.NOW.date().isoformat()}.dry-run.md").read()
            self.assertIn("rollback-hold", rep); self.assertIn(f"| {K} | slack.post", rep); self.assertIn("would-do", rep)
        finally: off()
        self.assertEqual(self.main(), 0)   # marker removed -> sends resume
        self.assertEqual([k for k, _ in self.sends], [K]); self.assertEqual(self.ledger()[K]["status"], "done")


if __name__ == "__main__":
    s = unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(Outage), Outbound("test_hold_plan_only_then_resume")])
    r = unittest.TextTestRunner(verbosity=2).run(s); off(); sys.exit(0 if r.wasSuccessful() else 1)
