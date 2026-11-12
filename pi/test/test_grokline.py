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
        self.saved_ps = G().PRWATCH_STATE; G().PRWATCH_STATE = f"{self.tmp}/prwatch-state.json"   # never the box's live state
    def tearDown(self):
        lib.TAKEOVER = self.saved_to; G().PRWATCH_STATE = self.saved_ps; super().tearDown()
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
    def mem_block(self, on=True):
        """prwatch last dispatch waited on headroom: the only Grok-overflow trigger after Praise 11-12."""
        path = G().PRWATCH_STATE
        if on:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f: json.dump({"fix_wait": "headroom 100 MB < 2000 MB"}, f)
        elif os.path.exists(path): os.remove(path)

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
        self.assertFalse(m(True, "open", 1, 3, 0)[0])  # not memory-blocked
        self.assertFalse(m(True, "open", 2, 0, 0)[0])  # no backlog
        self.assertFalse(m(True, "open", 2, 1, 0)[0])  # many running, not memory-blocked: do not open
        self.assertFalse(m(True, "open", 5, 3, 0)[0])  # running count is not a gate
        self.assertTrue(m(True, "open", 0, 0, 1)[0])   # handed over by Pi
        self.assertFalse(m(True, "stop", 0, 0, 1)[0])

    def test_may_open_when_pi_blocked_by_memory(self):   # 11-12: Grok opens only when Pi is memory-blocked AND there is backlog
        m = G().may_open
        self.assertTrue(m(True, "open", 0, 6, 0, 0, True)[0])
        self.assertTrue(m(True, "open", 3, 6, 0, 0, True)[0])    # running count does not matter once memory-blocked
        self.assertFalse(m(True, "open", 0, 0, 0, 0, True)[0])   # no backlog
        self.assertFalse(m(True, "open", 3, 6, 0, 0, False)[0])  # many running, not blocked
        self.assertFalse(m(True, "hold", 0, 6, 0, 0, True)[0])   # usage gate unchanged
        self.assertFalse(m(True, "open", 0, 6, 0, 2, True)[0])   # GROK_CAP unchanged
        import tempfile, json as J
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f: J.dump({"fix_wait": "headroom N MB < N MB"}, f)
        self.assertTrue(G().pi_mem_blocked(f.name))
        with open(f.name, "w") as g: J.dump({"fix_wait": "N merge test(s) queued"}, g)
        self.assertFalse(G().pi_mem_blocked(f.name))
        os.remove(f.name)

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
    def test_claims_when_pi_memory_blocked_and_backlog(self):
        self.switch(); self.put(tickets(**self.both_pi_slots()))
        self.assertEqual(G().claim(5.0, now=NOW)["status"], "no")   # many running is not enough
        self.mem_block()
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
        self.switch(); self.put(tickets(**self.both_pi_slots())); self.mem_block()
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
        self.switch(); self.put(tickets(**self.both_pi_slots())); self.mem_block()
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
        self.switch(); self.put(tickets(**self.both_pi_slots())); self.mem_block()
        self.ctx["reports"][1179]["cur"][1]["author"] = "someone"
        self.assertEqual(G().claim(5.0, now=NOW)["status"], "no")
        self.ctx["reports"][1179]["cur"][1]["author"] = "kirozeng"
        self.assertEqual(G().claim(5.0, now=NOW)["ticket"], f"1179:{C}")
    def test_backlog_cleared_stops_taking_work(self):
        self.switch(); self.put(tickets(**self.both_pi_slots())); self.mem_block()
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
        self.switch(); self.put(tickets(**self.both_pi_slots())); self.mem_block(); g = G()
        with g.usage_locked() as U: g.record(U, 5.0, NOW)
        before = open(g.usage_path()).read(), open(f"{prfix.OUT}/tickets.json").read()
        r = g.gate_dry_run(now=NOW)
        self.assertEqual((r["gate"], r["may_open"], r["would_claim"]), ("open", True, f"1179:{C}"))
        self.assertEqual((open(g.usage_path()).read(), open(f"{prfix.OUT}/tickets.json").read()), before)
        open(g.usage_path(), "w").write("{not json")
        r = g.gate_dry_run(now=NOW)
        self.assertEqual((r["gate"], r["may_open"]), ("hold", False))
    def test_skip_key_pending_ruling(self):   # 11-04: 1590 merged into 1610's session, --claim must be able to leave a key alone
        self.switch(); self.put(tickets(**self.both_pi_slots())); self.mem_block(); g = G(); k = f"1179:{C}"
        r = g.gate_dry_run(now=NOW, skip=[k])
        self.assertEqual((r["would_claim"], r["skipped"], r["may_open"]), (None, [k], False))
        r = g.claim(5.0, now=NOW, skip=[k])
        self.assertEqual(r["status"], "no", r); self.assertNotIn("owner", self.tk(k))
        self.assertEqual(g.claim(5.0, now=NOW, skip=["1:mq1-" + "0" * 20])["ticket"], k)   # other keys don't block it
    def test_skip_bad_key_is_error(self):
        import io, contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out): rc = G().main(["gate", "--dry-run", "--skip", "1610"])
        self.assertEqual(rc, 1); self.assertIn("--skip", json.loads(out.getvalue())["error"])
    def test_grok_cap_two_held_blocks_claim(self):
        self.switch(); f1, f2 = "mq1-" + "a" * 20, "mq1-" + "b" * 20
        self.put(tickets(**self.both_pi_slots(), **{f"1180:{f1}": self.grok_owned(1180, f1), f"1181:{f2}": self.grok_owned(1181, f2)}))
        g = G(); r = g.claim(5.0, now=NOW)                       # C is a qualifying todo, but Grok already holds 2
        self.assertEqual(r["status"], "no", r); self.assertNotIn("owner", self.tk(f"1179:{C}"))
        self.assertFalse(g.gate_dry_run(now=NOW)["may_open"])
        self.put(tickets(**self.both_pi_slots(), **{f"1180:{f1}": self.grok_owned(1180, f1)})); self.mem_block()   # 1 held -> may claim the 2nd
        self.assertEqual(g.claim(5.0, now=NOW)["ticket"], f"1179:{C}")
    def test_gate_dry_run_parseable_bad_day_is_hold(self):
        self.switch(); self.put(tickets(**self.both_pi_slots())); g = G(); day = NOW.date().isoformat()
        for bad in ({"baseline": "x", "samples": []}, {"baseline": None, "samples": [["t", "5"]]}, {"baseline": 5.0, "samples": "no"}):
            with open(g.usage_path(), "w") as f: json.dump({"days": {day: bad}}, f)
            before = open(g.usage_path()).read()
            r = g.gate_dry_run(now=NOW)
            self.assertEqual((r["gate"], r["may_open"], r["would_claim"]), ("hold", False, None), bad)
            self.assertEqual(open(g.usage_path()).read(), before)
        with open(g.usage_path(), "w") as f: f.write('{"days": {"%s": {"baseline": NaN, "samples": []}}}' % day)
        self.assertEqual(g.gate_dry_run(now=NOW)["gate"], "hold")

class TestBadValues(Grok):
    def test_gate_rejects_bad_values(self):
        g = G().gate
        for b, c in ((float("nan"), float("nan")), (10, float("nan")), (float("nan"), 10), (5, float("inf")),
                     (float("-inf"), 5), (5, -5), (-5, -5), (5, 101)):
            self.assertEqual(g(b, c)[0], "hold", (b, c))
    def test_bad_pct_never_poisons_baseline(self):
        g = G()
        with g.usage_locked() as U:
            for bad in (float("nan"), float("inf"), -5.0): g.record(U, bad, NOW - dt.timedelta(hours=2))
            g.record(U, 5.0, NOW - dt.timedelta(hours=1)); g.record(U, float("nan"), NOW); d = g.record(U, 15.0, NOW)
        self.assertEqual(d["baseline"], 5.0); self.assertEqual([s[1] for s in d["samples"]], [5.0, 15.0])
        self.assertEqual(g.day_gate(d)[0], "stop")       # the 10-point gate still fires
    def test_claim_with_bad_pct_is_hold(self):
        self.switch(); self.put(tickets(**self.both_pi_slots())); g = G()
        for bad in (float("nan"), float("inf"), -5.0):
            r = g.claim(bad, now=NOW)
            self.assertEqual((r["status"], r["gate"]), ("no", "hold"), bad)
        self.assertNotIn("owner", self.tk(f"1179:{C}"))
        if os.path.exists(g.usage_path()):
            self.assertIsNone((json.load(open(g.usage_path()))["days"].get("2026-10-23") or {}).get("baseline"))
    def test_cli_rejects_bad_pct(self):
        g = G()
        for bad in ("nan", "inf", "-5"):
            self.assertEqual(g.main(["--record", "--pct", bad]), 1, bad)
        if os.path.exists(g.usage_path()):
            for d in json.load(open(g.usage_path()))["days"].values(): self.assertIsNone(d["baseline"])

class TestCorruptUsage(Grok):
    def corrupt(self):
        os.makedirs(prfix.OUT, exist_ok=True); open(G().usage_path(), "w").write("{not json")
    def test_switch_off_corrupt_usage_returns_tickets(self):
        self.put(tickets(**{f"1178:{A}": self.grok_owned(1178, A)})); self.corrupt()
        self.assertEqual([k for k, _ in G().sweep(now=NOW)], [f"1178:{A}"])
        self.assertEqual(self.tk(f"1178:{A}").get("owner"), None)
    def test_switch_on_corrupt_usage_is_hard_stop(self):
        self.switch(); self.corrupt()
        self.put(tickets(**{f"1178:{A}": self.grok_owned(1178, A), f"1179:{C}": self.grok_owned(1179, C)}))
        self.assertEqual(len(G().sweep(now=NOW)), 2)
        self.assertEqual((self.tk(f"1178:{A}").get("owner"), self.tk(f"1179:{C}").get("owner")), (None, None))
    def test_check_corrupt_usage_is_stop(self):
        self.switch(); self.corrupt(); self.put(tickets(**self.both_pi_slots()))
        r = G().check(now=NOW)
        self.assertEqual((r["gate"], r["may_open"]), ("stop", False))

BAD_USAGE = {   # every malformed grok-usage.json shape: today's entry (2026-10-23) or the whole file
    "str_numbers": {"days": {"2026-10-23": {"baseline": "5", "samples": [["t", "6"]], "opened": 0, "done": 0, "trips": []}}},
    "str_50": {"days": {"2026-10-23": {"baseline": 5.0, "samples": [["t", "50"]], "opened": 0, "done": 0, "trips": []}}},
    "bool_true": {"days": {"2026-10-23": {"baseline": True, "samples": [["t", True]], "opened": 0, "done": 0, "trips": []}}},
    "day_not_dict": {"days": {"2026-10-23": ["x"]}},
    "bad_samples": {"days": {"2026-10-23": {"baseline": 5.0, "samples": [5.0, ["t"]], "opened": 0, "done": 0, "trips": []}}},
    "samples_not_list": {"days": {"2026-10-23": {"baseline": 5.0, "samples": 7, "opened": 0, "done": 0, "trips": []}}},
    "days_not_dict": {"days": [1, 2]},
    "not_json": "{not json",
}

class TestMalformedUsage(Grok):
    def write_usage(self, v):
        os.makedirs(prfix.OUT, exist_ok=True)
        with open(G().usage_path(), "w") as f: f.write(v if isinstance(v, str) else json.dumps(v))
    def test_valid_pct_types(self):
        v = G().valid_pct
        for bad in ("50", True, False, None, [5], float("nan"), float("inf"), -5, 101): self.assertFalse(v(bad), bad)
        for ok in (0, 50, 50.5, 100): self.assertTrue(v(ok), ok)
        self.assertEqual(G().gate("5", "6")[0], "hold"); self.assertEqual(G().gate(True, 5)[0], "hold")
    def test_sweep_returns_tickets_switch_off_and_on(self):
        for name, bad in BAD_USAGE.items():
            for sw in (False, True):
                with self.subTest(file=name, switch=sw):
                    self.switch(sw); self.write_usage(bad)
                    self.put(tickets(**{f"1178:{A}": self.grok_owned(1178, A), f"1179:{C}": self.grok_owned(1179, C)}))
                    back = G().sweep(now=NOW)
                    self.assertEqual(sorted(k for k, _ in back), [f"1178:{A}", f"1179:{C}"])
                    self.assertEqual((self.tk(f"1178:{A}").get("owner"), self.tk(f"1179:{C}").get("owner")), (None, None))
                    G().sweep(now=NOW.replace(hour=23, minute=59))   # the day line path never crashes either
    def test_switch_on_gate_check_claim_fail_closed(self):
        for name, bad in BAD_USAGE.items():
            with self.subTest(file=name):
                self.switch(); self.write_usage(bad); self.put(tickets(**self.both_pi_slots()))
                g = G()
                r = g.check(now=NOW)
                self.assertIn(r["gate"], ("stop", "hold")); self.assertFalse(r["may_open"])
                r = g.claim(5.0, now=NOW)
                self.assertEqual(r["status"], "no"); self.assertIn(r["gate"], ("stop", "hold"))
                self.assertNotIn("owner", self.tk(f"1179:{C}"))
                d = bad.get("days") if isinstance(bad, dict) else None
                if isinstance(d, dict): self.assertIn(g.day_gate(d.get("2026-10-23"))[0], ("stop", "hold"))

CORRUPT_TICKETS = {   # 11-03 step 12: what a crash mid-write / a bad hand edit leaves in the Pi line's shared tickets.json
    "truncated": '{"tickets": {"1178:%s": {"qa": 1178, "status": "running", "owner": "gr' % A,
    "not json": "not json at all\n", "empty": "", "root list": "[]", "tickets list": '{"tickets": []}',
    "ticket not object": '{"tickets": {"1178:x": 5}}',
}

class TestCorruptTickets(Grok):
    def test_sweep_skips_and_never_rewrites(self):
        os.makedirs(prfix.OUT, exist_ok=True); p = f"{prfix.OUT}/tickets.json"
        for name, raw in CORRUPT_TICKETS.items():
            for sw in (False, True):   # switch off = "give every Grok ticket back" path, the one that writes
                with self.subTest(file=name, switch=sw):
                    self.switch(sw)
                    with open(p, "w") as f: f.write(raw)
                    m = os.stat(p).st_mtime_ns
                    self.assertEqual(G().sweep(now=NOW), [])                      # no exception, nothing handed back
                    self.assertEqual(open(p).read(), raw)                          # never overwritten (not as {} either)
                    self.assertEqual(os.stat(p).st_mtime_ns, m)
                    G().sweep(now=NOW.replace(hour=23, minute=59))
                    self.assertEqual(open(p).read(), raw)

# ---- Grok-line PR open path (#1701: Grok hand-wrote not-applicable on a cindyplugin/ fix; pr_meta never ran platform_block)
import shutil, subprocess, tempfile
from test_prfix_unit import LIVE, REPO_FILES  # noqa: E402
@unittest.skipUnless(all(os.path.exists(f"{LIVE}/{f}") for f in REPO_FILES) and shutil.which("node"), "mivo-canvas-plugin checkers not available")
class TestPrMetaPlatform(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.wd = f"{self.tmp}/repo"
        for f in REPO_FILES:
            os.makedirs(os.path.dirname(f"{self.wd}/{f}"), exist_ok=True); shutil.copy(f"{LIVE}/{f}", f"{self.wd}/{f}")
        self.w("cindyplugin/check-dist-main.mjs", "export const scan = () => []\n"); self.w("docs/x.md", "a\n")
        self.git("init", "-q", "-b", "main"); self.commit("base")
        self.base = self.git("rev-parse", "HEAD"); self.git("update-ref", "refs/remotes/origin/main", self.base)
    def tearDown(self): shutil.rmtree(self.tmp, ignore_errors=True)
    def w(self, f, t):
        os.makedirs(os.path.dirname(f"{self.wd}/{f}"), exist_ok=True); 
        with open(f"{self.wd}/{f}", "w") as fh: fh.write(t)
    def git(self, *a): return subprocess.check_output(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", self.wd, *a], text=True).strip()
    def commit(self, m): self.git("add", "-A"); self.git("commit", "-q", "-m", m)
    def cindy_fix(self):
        self.w("cindyplugin/check-dist-main.mjs", "export const scan = () => null\n")
        self.w("cindyplugin/check-dist-main.test.mjs", "import { scan } from './check-dist-main.mjs'\n"); self.commit("fix")
    def meta(self): return G().pr_meta_wd(f"1553:{C}", self.wd, "fix: x (QA #1553)", base_ref="main")

    def test_cindyplugin_fix_gets_block_that_passes(self):
        self.cindy_fix(); m = self.meta(); body = "summary\n\n" + m["pr_body_block"]
        self.assertIn("```mivo-platform-evidence", m["pr_body_block"]); self.assertIn("QA: #1553", m["pr_body_block"])
        ev = prfix.platform_eval(self.wd, self.base, self.git("rev-parse", "HEAD"), body, True)
        self.assertTrue(ev["requiresDisclosure"])
        self.assertEqual([e for e in ev["errors"] if e != prfix.TRUSTED_PROOF_ERR], [])

    def test_docs_only_no_block(self):
        self.w("docs/x.md", "b\n"); self.commit("docs"); m = self.meta()
        self.assertEqual(m["platform_block"], ""); self.assertNotIn("mivo-platform-evidence", m["pr_body_block"])

    def test_hand_written_not_applicable_rejected(self):
        self.cindy_fix()
        na = '```mivo-platform-evidence\n{"schemaVersion":1,"risks":[],"windowsStatus":"not-applicable","environment":"","evidence":[],"fallback":"","reason":"x"}\n```'
        ev = prfix.platform_eval(self.wd, self.base, self.git("rev-parse", "HEAD"), na, True)
        self.assertIn("not-applicable cannot exempt production UI or detected platform risks", ev["errors"])
        self.assertNotIn('"not-applicable"', self.meta()["pr_body_block"])

    def test_non_full_sha_fails_closed(self):
        self.cindy_fix()
        with self.assertRaises(ValueError): G().platform_meta(self.wd, self.base, "HEAD")
        with self.assertRaises(ValueError): G().platform_meta(self.wd, self.base[:8], self.git("rev-parse", "HEAD"))

    def test_cli_requires_wd_and_errors_not_empty_block(self):
        import io, contextlib as cl
        out = io.StringIO()
        with cl.redirect_stdout(out): rc = G().main(["pr-meta", "--key", f"1553:{C}"])
        self.assertEqual(rc, 1); self.assertIn("--wd", json.loads(out.getvalue())["error"])
        out = io.StringIO()   # evaluator missing -> error, never an ok without a block
        os.remove(f"{self.wd}/scripts/ci/platform-risk.mjs"); self.cindy_fix()
        with cl.redirect_stdout(out): rc = G().main(["pr-meta", "--key", f"1553:{C}", "--wd", self.wd, "--base", "main"])
        self.assertEqual(rc, 1); self.assertEqual(json.loads(out.getvalue())["status"], "error")

if __name__ == "__main__": unittest.main()
