#!/usr/bin/env python3
"""prfix.py parallel runs (Praise 10-24: "并发数量看内存 不设上限") + model tier + shared PR-list cache.
Local only: no network, no model, no writes outside a temp dir. Run: python3 test/test_prfix_parallel.py"""
import os as _os
_os.environ.setdefault('PRFIX_OWNERS', '*')
_os.environ['PRFIX_DISPATCHED'] = '1'   # runs as if prwatch launched them (the cron fallback defers to a live prwatch)
for _k in [k for k in _os.environ if k.startswith('GIT_')]: del _os.environ[_k]
import argparse, contextlib, datetime as dt, io, json, os, sys, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin")); sys.path.insert(0, HERE)
import prfix  # noqa: E402
from test_prfix_unit import Tmp, GitTmp, ctx, report, finding, pr, wf, rf, FP, NOW, A, B, C  # noqa: E402  (helpers only)

def tickets(**kw): return {"tickets": kw}
def running(qa, fp, pid): return {"qa": qa, "fp": fp, "sev": "P1", "status": "running", "pid": pid, "attempts": 1}

class Base(Tmp):
    def setUp(self):
        super().setUp()
        self.acts = []; self.calls = []
        self.orig = {k: getattr(prfix, k) for k in ("gather", "fix_one", "record_actions", "mem_gb")}
        prfix.record_actions = lambda job, day, acts: self.acts.extend(acts)
        prfix.mem_gb = lambda: 8.0
        rep = {1178: report(1178, "PraiseZhu", [finding(A), finding(B)], t=NOW - dt.timedelta(hours=2)),
               1179: report(1179, "PraiseZhu", [finding(C)], t=NOW - dt.timedelta(hours=1))}
        self.ctx = ctx(reports=rep, gates={})
        self.stale = None       # gather() view of tickets.json (None = the file as it is)
        prfix.gather = lambda: dict(self.ctx, tickets=self.stale if self.stale is not None else prfix.load_tickets().get("tickets", {}))
        def fix(c, A_, d):
            self.calls.append((c["qa"], c["fp"]))
            return {"status": "pr_action_written", "kind": None, "action_key": f"fixpr:{c['qa']}:{c['fp']}"}, {"evidence": "e.md"}
        prfix.fix_one = fix
    def tearDown(self):
        for k, v in self.orig.items(): setattr(prfix, k, v)
        super().tearDown()
    def main(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf): rc = prfix.main(list(argv))
        last = buf.getvalue().strip().splitlines()[-1]
        return rc, json.loads(last[len("RESULT "):])
    def put(self, doc): wf(f"{prfix.OUT}/tickets.json", json.dumps(doc))

class TestParallelClaim(Base):
    def test_ticket_run_by_a_live_process_is_left_alone(self):
        self.put(tickets(**{f"1178:{A}": running(1178, A, os.getpid())}))   # this test process: cmdline has "prfix", alive
        rc, r = self.main("--next")
        self.assertEqual((rc, r["ticket"]), (0, f"1178:{B}"))
        self.assertEqual(self.calls, [(1178, B)])
        self.assertEqual(prfix.load_tickets()["tickets"][f"1178:{A}"]["status"], "running")        # untouched

    def test_claim_rereads_tickets_under_the_lock(self):
        # gather() saw A free; meanwhile another process claimed A. The claim must see that and take B.
        self.stale = {}
        self.put(tickets(**{f"1178:{A}": running(1178, A, os.getpid())}))
        rc, r = self.main("--next")
        self.assertEqual(r["ticket"], f"1178:{B}"); self.assertEqual(self.calls, [(1178, B)])

    def test_no_global_run_lock(self):
        held = prfix.try_lock(f"{prfix.OUT}/prfix.run.lock")                # the pre-10-24 global lock: irrelevant now
        try: rc, r = self.main("--next")
        finally: prfix.unlock(held)
        self.assertEqual((rc, r["status"]), (0, "pr_action_written"))

    def test_busy_ticket_lock_skips_to_the_next_ticket(self):
        held = prfix.try_lock(f"{prfix.OUT}/locks/1178-{A[4:12]}.lock")     # inconsistent state: lock held, file says todo
        try: rc, r = self.main("--next")
        finally: prfix.unlock(held)
        self.assertEqual(r["ticket"], f"1178:{B}")

    def test_marks_running_with_own_pid_while_fixing(self):
        seen = {}
        def fix(c, A_, d):
            seen.update(prfix.load_tickets()["tickets"][f"{c['qa']}:{c['fp']}"])
            return {"status": "pr_action_written", "kind": None, "action_key": "k"}, {"evidence": "e.md"}
        prfix.fix_one = fix
        self.main("--next")
        self.assertEqual((seen["status"], seen["pid"]), ("running", os.getpid()))
        tk = prfix.load_tickets()["tickets"][f"1178:{A}"]
        self.assertEqual((tk["status"], tk["pid"]), ("pr_action_written", None))
        lk = prfix.try_lock(f"{prfix.OUT}/locks/1178-{A[4:12]}.lock"); self.assertIsNotNone(lk); prfix.unlock(lk)   # released

    def test_qa_on_a_ticket_another_process_runs_is_busy(self):
        self.put(tickets(**{f"1178:{A}": running(1178, A, os.getpid())}))
        rc, r = self.main("--qa", "1178", "--fp", A)
        self.assertEqual((rc, r["status"]), (75, "busy")); self.assertEqual(self.calls, [])

    def test_gated_since_gather_does_not_hold_up_the_queue(self):
        wf(prfix.GATES, '{"no_fix_pr": {"1178": "刚加的闸"}}')              # gather() saw no gate; the file says otherwise
        rc, r = self.main("--next")
        self.assertEqual(r["ticket"], f"1179:{C}"); self.assertEqual(self.calls, [(1179, C)])
        self.put(tickets(**{f"1179:{C}": running(1179, C, os.getpid())}))
        rc, r = self.main("--next")                                          # nothing but gated ones left -> gated
        self.assertEqual((rc, r["status"]), (1, "gated")); self.assertEqual(len(self.calls), 1)

    def test_two_processes_started_together_never_take_the_same_ticket(self):
        def fix(c, A_, d):
            with open(f"{self.tmp}/got", "a") as f: f.write(f"{c['qa']}:{c['fp']}\n")
            time.sleep(0.5)                                                  # still fixing while the other one claims
            return {"status": "pr_action_written", "kind": None, "action_key": "k"}, {"evidence": "e.md"}
        prfix.fix_one = fix
        r, w = os.pipe(); kids = []
        for _ in range(3):
            pid = os.fork()
            if pid == 0:                                                     # child: a real prfix process (cmdline has "prfix")
                os.close(w); os.read(r, 1)
                try:
                    with contextlib.redirect_stdout(io.StringIO()): prfix.main(["--next"])
                finally: os._exit(0)
            kids.append(pid)
        os.close(r); os.write(w, b"xxx"); os.close(w)                       # start all three at once
        for k in kids: os.waitpid(k, 0)
        got = rf(f"{self.tmp}/got").split()
        self.assertEqual(sorted(got), sorted([f"1178:{A}", f"1178:{B}", f"1179:{C}"]))  # three tickets, three processes, no twice
        st = {k: v["status"] for k, v in prfix.load_tickets()["tickets"].items()}
        self.assertEqual(set(st.values()), {"pr_action_written"}, st)

class TestPredictPr(Base):
    """10-24: parallel / back-to-back tickets must not predict the same PR number (each miss = an extra docs/pr rename push)."""
    def setUp(self):
        super().setUp(); self.saved_gh = prfix.gh
        prfix.gh = lambda args, js=True: "1255\n"          # latest issue/PR number on GitHub
    def tearDown(self):
        prfix.gh = self.saved_gh; super().tearDown()
    def test_numbers_reserved_by_live_or_waiting_tickets_are_skipped(self):
        me = os.getpid()
        self.put(tickets(**{f"1179:{C}": dict(running(1179, C, me), status="pr_action_written", pred_pr=1256),
                            f"1178:{A}": dict(running(1178, A, me), pred_pr=1257),
                            f"1178:{B}": running(1178, B, me)}))
        self.assertEqual(prfix.predict_pr(f"1178:{B}"), 1258)
        self.assertEqual(prfix.load_tickets()["tickets"][f"1178:{B}"]["pred_pr"], 1258)       # its own reservation, for the next one
    def test_used_dead_or_failed_reservations_do_not_count_and_dry_run_writes_nothing(self):
        self.put(tickets(**{f"1179:{C}": dict(running(1179, C, os.getpid()), status="pr_action_written", pred_pr=1250),  # PR exists
                            f"1178:{A}": dict(running(1178, A, 999999999), pred_pr=1256),                                   # dead
                            f"1178:{B}": dict(running(1178, B, os.getpid()), status="needs_human", pred_pr=1257)}))
        self.assertEqual(prfix.predict_pr(f"1188:{A}"), 1256)
        before = prfix.load_tickets()
        self.assertEqual(prfix.predict_pr(f"1178:{B}", dry=True), 1256)
        self.assertEqual(prfix.load_tickets(), before)

class TestRound2Claim(Base):
    def test_written_round2_action_stays_claimed_until_its_own_pr_shows_up(self):   # review 10-24
        merged_r1 = pr(1300, "PraiseZhu", body=f"QA: #1178\n{A}", state="MERGED")
        row = {"status": "open", "retest_reopened": True, "fix_round": 1}
        tk = {"status": "pr_action_written", "action_key": f"fixpr:1178:{A}:r2", "updated_at": "2026-10-24T03:00:00+08:00"}
        c = dict(self.ctx, prs=[merged_r1], ledger={(1178, A): row}, tickets={f"1178:{A}": tk})
        v = {w["key"]: w for w in prfix.evaluate_all(c)}[f"1178:{A}"]
        self.assertEqual(v["state"], "claimed"); self.assertIn("等外发", v["reason"])
        c["prs"] = [merged_r1, pr(1310, "PraiseZhu", body=f"QA: #1178\n{A}\n\n{A} r2", state="OPEN")]   # outbound created round 2
        v = {w["key"]: w for w in prfix.evaluate_all(c)}[f"1178:{A}"]
        self.assertEqual(v["state"], "claimed"); self.assertIn("#1310", v["reason"])

class TestTierAndRegex(GitTmp):
    def test_model_tier_is_deep(self):
        self.assertEqual(prfix.TIER, os.environ.get("PRFIX_TIER") or "deep")
        tiers = []
        def fake_pi(tier, *a, **k):
            tiers.append(tier); raise RuntimeError("stop after the first model call")
        orig = prfix.pi, prfix.predict_pr, prfix.npm_ci
        prfix.pi = fake_pi; prfix.predict_pr = lambda *a, **k: 4242; prfix.npm_ci = lambda wd: (0, "ok")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                out, res = prfix.fix_one({"qa": 1178, "fp": A, "sev": "P1", "title": "t", "source": "report", "author": "PraiseZhu",
                                          "finding": finding(A, file="src/foo.js", line=1), "row": None, "round": 1},
                                         argparse.Namespace(dry_run=False, base=None), "2026-10-24")
        finally: prfix.pi, prfix.predict_pr, prfix.npm_ci = orig
        self.assertEqual(tiers, ["deep"]); self.assertEqual(out["kind"], "transient")
        self.assertFalse(os.path.exists(f"{prfix.WT_ROOT}/pi-wt-1178-{A[4:12]}"))   # cleaned up under the clone lock

    def test_repo_lock_is_exclusive(self):
        with prfix.repo_lock():
            self.assertIsNone(prfix.try_lock(f"{prfix.OUT}/git.lock"))
        lk = prfix.try_lock(f"{prfix.OUT}/git.lock"); self.assertIsNotNone(lk); prfix.unlock(lk)

    def test_qa_ref_variants(self):
        rq = prfix.qa_ref_re(1178)
        for s in ("QA: #1178", "QA #1178", "QA#1178", "qa: #1178", "QA：#1178", "QA: xindong/mivo-canvas-plugin#1178"):
            self.assertTrue(rq.search(s), s)
        for s in ("修复QA：#1178", "关联QA: #1178", "（QA #1178）"):                    # Chinese right before QA (review 10-24)
            self.assertTrue(rq.search(s), s)
        for s in ("QA 回访 #904/#1178/#898", "QA 回访 #904/#898/#1178", "QA #904, #1178", "QA: #1177、#1178"):   # one fix PR, several reports
            self.assertTrue(rq.search(s), s)
        for s in ("QA: #11789", "MivoQA #1178", "#1178", "QA: #1177 是另一个问题，见 #1178", "QA report PR-1179 #1178"):
            self.assertFalse(rq.search(s), s)

    def test_cleanup_skips_a_ticket_that_is_running(self):
        wd = f"{prfix.WT_ROOT}/pi-wt-5-deadbeef"; self.wt(wd, "fix/qa-5-deadbeef")
        held = prfix.try_lock(f"{prfix.OUT}/locks/5-deadbeef.lock")                   # e.g. a dry-run: never marked running
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out: prfix.cleanup_stale(True)
        finally: prfix.unlock(held)
        self.assertTrue(os.path.isdir(wd)); self.assertIn('"skipped_running": 1', out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()): prfix.cleanup_stale(True)
        self.assertFalse(os.path.isdir(wd))

class TestPrListCache(Tmp):
    def setUp(self):
        super().setUp()
        self.log = f"{self.tmp}/gh.calls"
        self.fakebin("gh", f"""
            echo "$*" >> {self.log}
            case "$*" in
              *--search*) cat {self.tmp}/upd.json ;;
              *) cat {self.tmp}/full.json ;;
            esac
        """)
        wf(f"{self.tmp}/full.json", json.dumps([{"number": 2, "body": "old"}, {"number": 1, "body": "x"}]))
        wf(f"{self.tmp}/upd.json", json.dumps([{"number": 3, "body": "new"}, {"number": 2, "body": "edited"}]))
    def calls(self): return rf(self.log).splitlines() if os.path.exists(self.log) else []

    def test_full_then_cached_then_incremental_merge(self):
        self.assertEqual([p["number"] for p in prfix.pr_list()], [2, 1]); self.assertEqual(len(self.calls()), 1)
        prfix.pr_list(); self.assertEqual(len(self.calls()), 1)                      # within the TTL: no gh call
        p = f"{prfix.OUT}/cache/prs.json"; c = json.loads(rf(p)); c["fetched_at"] -= prfix.PR_CACHE_TTL + 1; wf(p, json.dumps(c))
        prs = prfix.pr_list()
        self.assertEqual([(x["number"], x["body"]) for x in prs], [(3, "new"), (2, "edited"), (1, "x")])
        self.assertIn("--search updated:>=", self.calls()[-1]); self.assertIn("--limit 200", self.calls()[-1])

    def test_full_relist_when_old_or_page_full(self):
        prfix.pr_list()
        p = f"{prfix.OUT}/cache/prs.json"; c = json.loads(rf(p)); c["fetched_at"] -= 4000; c["full_at"] -= 4000; wf(p, json.dumps(c))
        prfix.pr_list(); self.assertIn("--limit 3000", self.calls()[-1])                # full list older than an hour
        wf(f"{self.tmp}/upd.json", json.dumps([{"number": i, "body": ""} for i in range(200)]))
        c = json.loads(rf(p)); c["fetched_at"] -= prfix.PR_CACHE_TTL + 1; wf(p, json.dumps(c))
        prfix.pr_list()
        search, full = self.calls()[-2:]                                                 # a full search page -> full list
        self.assertIn("--search", search); self.assertIn("--limit 200", search)
        self.assertNotIn("--search", full); self.assertIn("--limit 3000", full)

    def test_failed_refresh_raises(self):
        self.fakebin("gh", "echo boom >&2; exit 1\n")
        with self.assertRaises(RuntimeError): prfix.pr_list()

if __name__ == "__main__":
    unittest.main(verbosity=1)
