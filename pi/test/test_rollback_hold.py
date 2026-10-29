#!/usr/bin/env python3
"""10-28 ROLLBACK-SUSPECTED 外发暂停：标记出现 / 持续 / 删掉 三段，分别验 outage 补跑（外发型作业跳过、不落 replayed、删标记后重起）
和 outbound（只出计划、不发、不动账本；删标记后下一轮照发）。临时 PI_STATE + 临时标记路径，假 runner / 假 Slack，不联网、不外发。
Run: python3 test/test_rollback_hold.py"""
import os, sys, json, tempfile, unittest, datetime as dt
tmp = tempfile.mkdtemp(prefix="rollback-hold-"); MARK = f"{tmp}/ROLLBACK-SUSPECTED"
os.environ.update(PI_STATE=tmp, PI_OUTAGE_WITNESS=f"{tmp}/witness", PI_ROLLBACK_MARK=MARK, PI_OUTAGE_NO_SPAWN="1")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import importlib.util, rollback_hold as RH  # noqa: E402
# own copy of outage bound to THIS file's PI_STATE (under pytest another test may already have imported "outage" with its state dir)
_s = importlib.util.spec_from_file_location("outage", os.path.join(os.path.dirname(HERE), "bin", "outage.py")); OG = importlib.util.module_from_spec(_s); _s.loader.exec_module(OG)
sys.modules["outage"] = OG
import test_outbound_resend as TR  # noqa: E402  (scratch outbound fixture: fake Slack + fake sender)
CST = OG.CST
T = lambda h, mi=0, d=28: dt.datetime(2026, 10, d, h, mi, tzinfo=CST)
on = lambda: open(MARK, "w").close()
off = lambda: os.path.exists(MARK) and os.remove(MARK)


def _worker(q):
    try: q.put(len(OG.resume_held(spawn=lambda w: None, now=T(12, 30))))
    except Exception: q.put(-1)


class Outage(unittest.TestCase):
    W = {"kind": "pause", "start": T(8, 0).isoformat(), "end": T(12, 0).isoformat(), "gap_min": 240, "why": ["heartbeat gap"]}
    def setUp(self):
        os.environ["PI_ROLLBACK_MARK"] = MARK   # pytest: another test module may have pointed it elsewhere
        off()
        for p in (OG.REPLAYED(), OG.HELD(), OG.WIN(), OG.HOLD_STATE(), OG.RESUMED(), OG.HELD_PROBLEMS()):
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
        # marker removed: resume only after >= 15 min continuously absent, then exactly once
        off(); R = lambda t: OG.resume_held(spawn=spawned.append, now=t)
        self.assertEqual(R(T(13, 0)), []); self.assertEqual(R(T(13, 14)), []); self.assertEqual(spawned, [])
        self.assertEqual(R(T(13, 15)), [self.W["start"]]); self.assertEqual(spawned, [self.W])
        self.assertEqual(R(T(13, 40)), []); self.assertEqual(len(spawned), 1)
        items3, _ = OG._plan(self.W, now, OG.jl(OG.REPLAYED(), {})); self.assertTrue({"daily", "pm", "opsreview"} <= {x[0] for x in items3}, items3)
        calls.clear(); OG.replay(items3, runner=lambda c: calls.append(c) or 0); self.assertIn("pm", [c[1] for c in calls])
    def test_debounce_reset_and_summary(self):
        OG._hold(self.W, ["pm"]); spawned = []; R = lambda t: OG.resume_held(spawn=spawned.append, now=t)
        self.assertEqual(R(T(13, 0)), []); self.assertEqual(R(T(13, 10)), [])            # absent 10 min < 15
        on(); self.assertEqual(R(T(13, 12)), []); self.assertIsNone(OG.jl(OG.HOLD_STATE(), {}).get("absent_since"))   # reappears -> reset
        off(); self.assertEqual(R(T(13, 13)), []); self.assertEqual(R(T(13, 20)), [])     # 7 min since the reset, old start ignored
        self.assertEqual(spawned, [])
        self.assertEqual(R(T(13, 28)), [self.W["start"]]); self.assertEqual(R(T(13, 50)), []); self.assertEqual(spawned, [self.W])
        rs = OG.jl(OG.RESUMED(), []); self.assertEqual(rs[-1]["windows"], {self.W["start"]: ["pm"]})
        import watchdog as WD; WD.outage = OG   # summary (Praise digest via watchdog notices) lists the resumed window + jobs
        WD.runs = lambda job, days=2: []; WD.takeover = lambda j: True
        p = {}; WD.job_checks(T(14, 0), p, T(0, 0)); k = [x for x in p if x.startswith("outage:resumed:")]
        self.assertEqual(len(k), 1, list(p)); self.assertIn("10-28 08:00 起的窗口（pm）", p[k[0]]["what"]); self.assertEqual(p[k[0]]["kind"], "fyi")
    def test_corrupt_and_evicted_are_reported(self):
        open(OG.HELD(), "w").write("{bad"); spawned = []
        OG.resume_held(spawn=spawned.append, now=T(13, 0)); OG.resume_held(spawn=spawned.append, now=T(13, 30))
        ps = OG.jl(OG.HELD_PROBLEMS(), []); self.assertTrue(any("held.json 读不了" in x["what"] for x in ps), ps)
        OG._hold({**self.W, "start": T(1, 0).isoformat()}, ["daily"])   # window not in windows.json
        OG.resume_held(spawn=spawned.append, now=T(14, 0)); OG.resume_held(spawn=spawned.append, now=T(14, 20))
        self.assertEqual(spawned, []); self.assertTrue(any("不在 windows.json" in x["what"] and "daily" in x["what"] for x in OG.jl(OG.HELD_PROBLEMS(), [])))
        import watchdog as WD; WD.outage = OG
        WD.runs = lambda job, days=2: []; WD.takeover = lambda j: True
        p = {}; WD.job_checks(T(15, 0), p, T(0, 0)); self.assertEqual(sum(1 for x in p if x.startswith("outage:held-lost:") and p[x]["kind"] == "action"), 2, list(p))
    def test_concurrent_resume_exactly_once(self):
        import multiprocessing as mp
        for _ in range(20):
            self.setUp(); OG._hold(self.W, ["daily"]); OG.jw(OG.HOLD_STATE(), {"absent_since": T(12, 0).isoformat()})
            q = mp.Queue(); ps = [mp.Process(target=_worker, args=(q,)) for _ in range(6)]
            [p.start() for p in ps]; [p.join(30) for p in ps]
            res = [q.get(timeout=5) for _ in ps]
            self.assertNotIn(-1, res); self.assertEqual(sum(res), 1, res)
            self.assertEqual([p.exitcode for p in ps], [0] * 6); self.assertEqual(len(OG.jl(OG.RESUMED(), [])), 1)
    def test_replay_cli_records_held(self):
        on(); sys.argv = ["outage.py", "replay", self.W["start"]]
        ran = []; real = OG.replay; OG.replay = lambda items, **k: ran.extend(items) or {}   # never run real jobs from a test
        try: OG.main()
        finally: OG.replay = real
        self.assertFalse({x[0] for x in ran} & OG.SENDS, ran)
        self.assertIn("pm", OG.jl(OG.HELD(), {})[self.W["start"]]["jobs"])


class Outbound(TR.T):
    def setUp(self):
        os.environ["PI_ROLLBACK_MARK"] = MARK; super().setUp()
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
