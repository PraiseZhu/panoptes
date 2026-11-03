#!/usr/bin/env python3
"""Grok line switch + usage gate (Praise-PR.md "Grok 线用量标准", concurrency plan §3/§5): claim rules in tickets.json, Pi skips
owner=grok, > 3 emergency kills a day hands a ticket to Grok, 90 min / switch off / usage stop give tickets back, the day line.
Local only. Run: cd bin && python3 ../test/test_grokline.py"""
import os as _os
_os.environ.setdefault('PRFIX_OWNERS', '*')
_os.environ['PRFIX_DISPATCHED'] = '1'
import datetime as dt, json, os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin")); sys.path.insert(0, HERE)
import lib, prfix  # noqa: E402
from test_prfix_parallel import Base, tickets, running  # noqa: E402
from test_prfix_unit import A, B, C, NOW  # noqa: E402

def G():
    import grokline   # imported per test: on main (no grokline.py) each test fails on its own
    return grokline

class Grok(Base):
    def setUp(self):
        super().setUp()
        self.saved_to = lib.TAKEOVER; lib.TAKEOVER = f"{self.tmp}/takeover"; os.makedirs(lib.TAKEOVER)
    def tearDown(self):
        lib.TAKEOVER = self.saved_to; super().tearDown()
    def switch(self, on=True):
        p = f"{lib.TAKEOVER}/prfix-grok-overflow"
        if on: open(p, "w").close()
        elif os.path.exists(p): os.remove(p)
    def tk(self, k): return prfix.load_tickets()["tickets"].get(k) or {}
    def grok_owned(self, qa, fp, started=NOW):
        return {"qa": qa, "fp": fp, "sev": "P1", "status": "running", "owner": "grok", "pid": None, "host": "cloud",
                "started_at": started.isoformat(), "progress_at": started.isoformat(), "attempts": 1}
    def both_pi_slots(self):   # A and B run on the Pi line (this test process stands in for live prfix runs)
        return {f"1178:{A}": running(1178, A, os.getpid()), f"1178:{B}": running(1178, B, os.getpid())}

class TestGate(unittest.TestCase):
    def test_thresholds(self):
        g = G().gate
        self.assertEqual(g(4.4, 12.3)[0], "open")
        self.assertEqual(g(4.4, 12.4)[0], "hold")     # +8
        self.assertEqual(g(4.4, 14.4)[0], "stop")     # +10
        self.assertEqual(g(65, 70)[0], "hold")        # week >= 70
        self.assertEqual(g(60, 70)[0], "stop")        # +10 beats week
        self.assertEqual(g(None, 5)[0], "hold")       # no record: fail closed
    def test_may_open(self):
        m = G().may_open
        self.assertFalse(m(False, "open", 2, 3, 0)[0])
        self.assertFalse(m(True, "hold", 2, 3, 0)[0])
        self.assertFalse(m(True, "open", 1, 3, 0)[0])  # a Pi slot is free
        self.assertFalse(m(True, "open", 2, 0, 0)[0])  # no backlog
        self.assertTrue(m(True, "open", 2, 1, 0)[0])
        self.assertTrue(m(True, "open", 0, 0, 1)[0])   # handed over by Pi
        self.assertFalse(m(True, "stop", 0, 0, 1)[0])

class TestPiSide(Grok):
    def test_pi_next_skips_grok_owned(self):
        self.put(tickets(**{f"1178:{A}": self.grok_owned(1178, A)}))
        rc, r = self.main("--next")
        self.assertEqual(r["ticket"], f"1178:{B}")
        t = self.tk(f"1178:{A}")
        self.assertEqual((t["status"], t["owner"], t["pid"]), ("running", "grok", None))   # not turned into a dead run
    def test_pi_qa_pick_skips_grok_owned(self):
        self.put(tickets(**{f"1178:{A}": self.grok_owned(1178, A)}))
        rc, r = self.main("--qa", "1178", "--fp", A)
        self.assertEqual(r["status"], "busy"); self.assertEqual(self.calls, [])
    def test_emerg_check_is_a_kill(self):
        import prwatch
        real = prwatch.emerg_mark; prwatch.emerg_mark = lambda pid: f"{self.tmp}/emerg-kill/{pid}"   # never the live state dir
        try:
            m = prwatch.emerg_mark(os.getpid()); os.makedirs(os.path.dirname(m), exist_ok=True); open(m, "w").close()
            with self.assertRaises(prfix.EmergKilled): prfix.emerg_check()
        finally: prwatch.emerg_mark = real
        self.assertTrue(issubclass(prfix.EmergKilled, prfix.Waiting))
    def test_more_than_3_kills_a_day_hands_over(self):
        t = prfix.new_ticket({"qa": 1178, "fp": A, "sev": "P1"})
        k = {"status": "failed", "kind": "killed", "error": "killed"}
        for i in range(3): t = prfix.apply_outcome(t, k, NOW + dt.timedelta(minutes=i))[0]
        self.assertNotIn("grok_wanted", t)
        t = prfix.apply_outcome(t, k, NOW + dt.timedelta(minutes=5))[0]
        self.assertEqual((t["kills"], t["grok_wanted"]), (4, "2026-10-23"))
        t = prfix.apply_outcome(t, k, NOW + dt.timedelta(days=1))[0]   # new Beijing day: count starts again
        self.assertEqual(t["kills"], 1)
    def test_handover_only_with_switch_on(self):
        t = dict(prfix.new_ticket({"qa": 1178, "fp": A, "sev": "P1"}), grok_wanted="2026-10-23")
        c = dict(self.ctx, tickets={f"1178:{A}": t})
        v = {x["key"]: x for x in prfix.evaluate_all(c, NOW)}[f"1178:{A}"]
        self.assertEqual(v["state"], "todo")
        self.switch()
        v = {x["key"]: x for x in prfix.evaluate_all(c, NOW)}[f"1178:{A}"]
        self.assertEqual((v["state"], v["skips"]), ("skip", [prfix.GROK_HANDOVER]))

class TestClaim(Grok):
    def test_claims_when_pi_full_and_backlog(self):
        self.switch(); self.put(tickets(**self.both_pi_slots()))
        r = G().claim(5.0, now=NOW)
        self.assertEqual((r["status"], r["ticket"]), ("claimed", f"1179:{C}"))
        t = self.tk(f"1179:{C}")
        self.assertEqual((t["status"], t["owner"], t["pid"], t["host"], t["usage_start"]), ("running", "grok", None, "cloud", 5.0))
        rc, r = self.main("--next")                       # Pi: nothing left for it
        self.assertEqual(r["status"], "idle"); self.assertEqual(self.calls, [])
    def test_no_claim_with_a_free_pi_slot_or_switch_off(self):
        self.put(tickets(**{f"1178:{A}": running(1178, A, os.getpid())}))
        self.switch(); self.assertEqual(G().claim(5.0, now=NOW)["status"], "no")
        self.put(tickets(**self.both_pi_slots())); self.switch(False)
        self.assertEqual(G().claim(5.0, now=NOW)["status"], "no")
        self.assertNotIn("owner", self.tk(f"1179:{C}"))
    def test_gate_hold_and_week_cap(self):
        self.switch(); self.put(tickets(**self.both_pi_slots()))
        g = G(); g.claim(5.0, now=NOW - dt.timedelta(hours=3))   # baseline 5, claims C
        g.give_back(f"1179:{C}", 6.0, "test", now=NOW - dt.timedelta(hours=2))
        r = g.claim(13.0, now=NOW)                               # +8
        self.assertEqual((r["status"], r["gate"]), ("no", "hold"))
        u = json.load(open(g.usage_path()))["days"]["2026-10-23"]
        self.assertEqual(u["baseline"], 5.0); self.assertFalse(u.get("pm_report_due"))
        r = g.claim(70.0, now=NOW)
        self.assertEqual(r["gate"], "stop")
        self.assertTrue(json.load(open(g.usage_path()))["days"]["2026-10-23"]["pm_report_due"])
    def test_week_70_trips_pm_report(self):
        g = G()
        with g.usage_locked() as U: g.record(U, 68.0, NOW - dt.timedelta(hours=1)); d = g.record(U, 70.5, NOW)
        self.assertEqual(g.day_gate(d)[0], "hold"); self.assertEqual(d["trips"], ["week70"]); self.assertTrue(d["pm_report_due"])
    def test_finish_hands_back_with_usage(self):
        self.switch(); self.put(tickets(**self.both_pi_slots()))
        g = G(); g.claim(5.0, now=NOW)
        r = g.finish(f"1179:{C}", 6.5, 1700, now=NOW + dt.timedelta(minutes=50))
        self.assertEqual(r["status"], "ok")
        t = self.tk(f"1179:{C}")
        self.assertEqual((t["status"], t.get("owner"), t["finished_by"], t["pr"], t["usage_start"], t["usage_end"]),
                         ("pr_action_written", None, "grok", 1700, 5.0, 6.5))
        u = json.load(open(g.usage_path()))["days"]["2026-10-23"]
        self.assertEqual((u["opened"], u["done"]), (1, 1))

class TestSweep(Grok):
    def test_90_min_without_progress_goes_back(self):
        self.switch()
        self.put(tickets(**{f"1179:{C}": self.grok_owned(1179, C, started=NOW - dt.timedelta(minutes=89))}))
        self.assertEqual(G().sweep(now=NOW), [])
        self.assertEqual(G().sweep(now=NOW + dt.timedelta(minutes=2))[0][0], f"1179:{C}")
        t = self.tk(f"1179:{C}"); self.assertEqual((t["status"], t.get("owner")), ("queued", None))
    def test_progress_keeps_it(self):
        self.switch(); self.put(tickets(**{f"1179:{C}": self.grok_owned(1179, C, started=NOW - dt.timedelta(minutes=89))}))
        G().progress(f"1179:{C}", now=NOW)
        self.assertEqual(G().sweep(now=NOW + dt.timedelta(minutes=60)), [])
    def test_switch_off_gives_back_and_pi_takes_it(self):
        self.put(tickets(**{f"1178:{A}": self.grok_owned(1178, A)}))
        self.assertEqual(len(G().sweep(now=NOW)), 1)
        rc, r = self.main("--next")
        self.assertEqual(r["ticket"], f"1178:{A}")
    def test_usage_stop_gives_back_all(self):
        self.switch(); g = G()
        self.put(tickets(**{f"1178:{A}": self.grok_owned(1178, A), f"1179:{C}": self.grok_owned(1179, C)}))
        with g.usage_locked() as U: g.record(U, 5.0, NOW - dt.timedelta(hours=1)); g.record(U, 15.0, NOW)
        self.assertEqual(len(g.sweep(now=NOW)), 2)
    def test_idle_sweep_does_not_rewrite_tickets(self):
        self.switch(); self.put(tickets(**{f"1178:{A}": running(1178, A, os.getpid())}))
        m = os.stat(f"{prfix.OUT}/tickets.json").st_mtime_ns
        G().sweep(now=NOW)
        self.assertEqual(os.stat(f"{prfix.OUT}/tickets.json").st_mtime_ns, m)
    def test_daily_line_once_from_2359(self):
        g = G()
        with g.usage_locked() as U:
            g.record(U, 5.0, NOW.replace(hour=0, minute=1)); g.record(U, 7.25, NOW, opened=2, done=1)
        g.sweep(now=NOW.replace(hour=23, minute=58)); self.assertFalse(os.path.exists(g.daily_path()))
        g.sweep(now=NOW.replace(hour=23, minute=59)); g.sweep(now=NOW + dt.timedelta(hours=4))
        self.assertEqual(open(g.daily_path()).read(), "2026-10-23 baseline=5.0 close=7.25 rise=2.25 opened=2 done=1 trips=-\n")

class TestFollowup(Grok):
    """11-03 follow-up: Pi-line PR conventions, author filter, hard cap on running work, backlog cleared, gate dry-run."""
    def test_pr_meta_is_tracked_as_pi_line(self):
        import prwatch
        m = G().pr_meta(f"1179:{C}", "fix: x (QA #1179)")
        self.assertEqual(m["branch"], f"qa-fix/fixpr-1179-{C}"[:67])
        p = {"number": 9, "state": "OPEN", "isDraft": True, "author": {"login": "PraiseZhu"}, "headRefName": m["branch"],
             "body": "summary\n\n" + m["pr_body_block"]}
        self.assertTrue(prwatch.is_bot_fixpr(p))
        self.assertEqual(prfix.revise_target(p, [])[:2], (1179, C))
        self.assertTrue(lib.has_pi_trailer(m["commit_message"]))
        self.assertIn("QA: #1179", m["pr_body_block"])
        cm = [{"sha": "abc", "commit": {"message": m["commit_message"], "committer": {"date": dt.datetime.now(dt.timezone.utc).isoformat()}}}]
        self.assertEqual(lib.foreign_recent_commits(cm), [])
    def test_claims_only_praise_or_kirozeng(self):
        self.switch(); self.put(tickets(**self.both_pi_slots()))
        self.ctx["reports"][1179]["cur"][1]["author"] = "someone"
        self.assertEqual(G().claim(5.0, now=NOW)["status"], "no")
        self.ctx["reports"][1179]["cur"][1]["author"] = "kirozeng"
        self.assertEqual(G().claim(5.0, now=NOW)["ticket"], f"1179:{C}")
    def test_backlog_cleared_stops_taking_work(self):
        self.switch(); self.put(tickets(**self.both_pi_slots()))
        g = G(); self.assertEqual(g.claim(5.0, now=NOW)["status"], "claimed")
        g.finish(f"1179:{C}", 5.5, 1700, now=NOW)
        r = g.claim(5.6, now=NOW)
        self.assertEqual((r["status"], g.queue(NOW)), ("no", ([], [])))
        self.assertEqual(r["reason"], "没有积压")
    def test_hard_cap_stops_running_ticket_on_progress(self):
        self.switch(); self.put(tickets(**{f"1179:{C}": self.grok_owned(1179, C)}))
        g = G()
        with g.usage_locked() as U: g.record(U, 5.0, NOW - dt.timedelta(hours=1)); g.record(U, 15.0, NOW)
        r = g.progress(f"1179:{C}", now=NOW)
        self.assertEqual(r["status"], "stop")
        t = self.tk(f"1179:{C}"); self.assertEqual((t["status"], t.get("owner")), ("queued", None))
    def test_gate_dry_run_reads_only_and_bad_file_is_hold(self):
        self.switch(); self.put(tickets(**self.both_pi_slots())); g = G()
        with g.usage_locked() as U: g.record(U, 5.0, NOW)
        before = open(g.usage_path()).read(), open(f"{prfix.OUT}/tickets.json").read()
        r = g.gate_dry_run(now=NOW)
        self.assertEqual((r["gate"], r["may_open"], r["would_claim"]), ("open", True, f"1179:{C}"))
        self.assertEqual((open(g.usage_path()).read(), open(f"{prfix.OUT}/tickets.json").read()), before)
        open(g.usage_path(), "w").write("{not json")
        r = g.gate_dry_run(now=NOW)
        self.assertEqual((r["gate"], r["may_open"]), ("hold", False))

if __name__ == "__main__": unittest.main()
