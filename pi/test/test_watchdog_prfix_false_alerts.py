#!/usr/bin/env python3
"""11-04: prfix_check false alerts. prfix:nopr:<k> for tickets that already have a fix PR (grokline --finish sets pr,
finished_by=grok, action_key=None -> L.get(None)), and prfix:nopush:<k> for a revise action outbound already reports as
stale ("stale:head moved" row in out/outbound/<date>.md, outbound.py h_push_fix). Temp dir only, no gh."""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import json, os, shutil, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import watchdog  # noqa: E402

class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wd-prfix-")
        self.saved = {a: getattr(watchdog, a, None) for a in ("PRFIX_TICKETS", "LEDGER", "PRWATCH_STATE", "OUTBOUND_REPORT_DIR")}
        watchdog.PRFIX_TICKETS, watchdog.LEDGER, watchdog.PRWATCH_STATE = (f"{self.tmp}/{n}.json" for n in ("tickets", "ledger", "prwatch"))
        watchdog.OUTBOUND_REPORT_DIR = self.tmp
        self.now = watchdog.now_cst(); self.old = (self.now - watchdog.dt.timedelta(hours=5)).isoformat()
    def tearDown(self):
        for a, v in self.saved.items():
            if v is None: delattr(watchdog, a) if hasattr(watchdog, a) else None
            else: setattr(watchdog, a, v)
        shutil.rmtree(self.tmp, ignore_errors=True)
    def keys(self, tickets, ledger=None, report=""):
        json.dump({"tickets": tickets}, open(watchdog.PRFIX_TICKETS, "w")); json.dump(ledger or {}, open(watchdog.LEDGER, "w"))
        if report: open(f"{self.tmp}/{watchdog.today()}.md", "w").write(report)
        p = {}; watchdog.prfix_check(self.now, p); return sorted(k for k in p if k.startswith("prfix:"))

    def test_ticket_with_pr_no_nopr_alert(self):
        self.assertEqual(self.keys({"1597": {"qa": 1597, "status": "pr_action_written", "pr": 1682, "action_key": None, "updated_at": self.old}}), [])
    def test_finished_by_grok_no_nopr_alert(self):
        self.assertEqual(self.keys({"1553": {"qa": 1553, "status": "pr_action_written", "finished_by": "grok", "action_key": None, "updated_at": self.old}}), [])
    def test_stale_revise_no_nopush_alert(self):
        t = {"rev:1724": {"pr": 1724, "qa": 1610, "kind": "revise", "status": "pushed", "action_key": "revise:1724:913953d0f647", "updated_at": self.old}}
        rep = "| job | key | type | to | result | detail |\n|---|---|---|---|---|---|\n| praise-pr | revise:1724:913953d0f647 | gh.pr.push-fix | gh push fix onto #1724 | stale:head moved |  |\n"
        self.assertEqual(self.keys(t, report=rep), [])
    def test_empty_action_key_with_pr_no_alert(self):
        t = {"rev:7": {"pr": 7, "status": "push_action_written", "action_key": "", "updated_at": self.old}}
        self.assertEqual(self.keys(t), [])
    def test_pushed_key_not_in_ledger_no_nopush_alert(self):
        t = {"rev:1724": {"pr": 1724, "status": "pushed", "action_key": "revise:1724:913953d0f647", "updated_at": self.old}}
        self.assertEqual(self.keys(t), [])
    def test_ruled_out_no_alert(self):
        t = {"1433:mq1-9be27e51512fbd075277": {"qa": 1433, "fp": "mq1-9be27e51512fbd075277", "status": "ruled_out", "action_key": "fixpr:1433:x:a2", "updated_at": self.old}}
        self.assertEqual(self.keys(t, {"fixpr:1433:x:a2": {"status": "gave_up"}}), [])
    def test_close_ruled_then_no_alert(self):
        import prfix
        rul = f"{self.tmp}/p2-rulings.md"; open(rul, "w").write("# r\n| 日期 | 报告 PR | 指纹 | 问题 | 裁决 |\n|---|---|---|---|---|\n"
            "| 2026-11-04 | #1433 | mq1-9be27e51512fbd075277-pr1433 | e2e | 不修 | 误报 |\n")
        k = "1433:mq1-9be27e51512fbd075277"; t = {k: {"qa": 1433, "fp": "mq1-9be27e51512fbd075277", "status": "pr_action_written", "action_key": "fixpr:a2", "updated_at": self.old},
                                               "9:mq1-aaaa": {"qa": 9, "fp": "mq1-aaaa", "status": "queued"}}
        os.makedirs(f"{self.tmp}/pp"); json.dump({"tickets": t}, open(f"{self.tmp}/pp/tickets.json", "w"))
        saved = (prfix.OUT, prfix.RULINGS); prfix.OUT, prfix.RULINGS = f"{self.tmp}/pp", rul
        try:
            for _ in range(2): self.assertEqual(prfix.main(["--close-ruled", k, "--yes"]), 0)   # idempotent
        finally: prfix.OUT, prfix.RULINGS = saved
        got = json.load(open(f"{self.tmp}/pp/tickets.json"))["tickets"]
        self.assertEqual((got[k]["status"], got[k]["ruling"], got[k]["ruling_source"]), ("ruled_out", "不修", "p2-rulings.md:4"))
        self.assertEqual(got["9:mq1-aaaa"]["status"], "queued")
        self.assertEqual(self.keys(got, {"fixpr:a2": {"status": "gave_up"}}), [])
    def test_real_problems_still_alert(self):
        t = {"9": {"qa": 9, "status": "pr_action_written", "action_key": "fixpr:9", "updated_at": self.old},
             "rev:8": {"pr": 8, "status": "pushed", "action_key": "revise:8:abc", "updated_at": self.old}}
        self.assertEqual(self.keys(t, {"revise:8:abc": {"status": "failed"}}, report="| praise-pr | revise:8:abc | gh.pr.push-fix | x | failed |  |\n"), ["prfix:nopr:9", "prfix:nopush:rev:8"])

if __name__ == "__main__": unittest.main()

class CloseRuled(unittest.TestCase):
    """Round-1 acceptance of #201: --close-ruled closes one named key, can be undone, and refuses PR-only rulings on multi-ticket PRs."""
    ROWS = ("| 日期 | 报告 PR | 指纹 | 问题 | 裁决 |\n|---|---|---|---|---|\n"
            "| 2026-11-04 | #1433 | mq1-9be27e51512fbd075277-pr1433 | e2e | 不修 | 误报 |\n"
            "| 2026-11-04 | #1456 | mq1-7a5374add598af27a3cd-pr1456 | x | 不修 | 误报 |\n"
            "| 2026-11-01 | #1500 | 只写 PR 的老裁决 | 不修 | 误报 |\n")
    K = "1433:mq1-9be27e51512fbd075277"
    def setUp(self):
        import prfix; self.p = prfix
        self.tmp = tempfile.mkdtemp(prefix="prfix-ruled-"); os.makedirs(f"{self.tmp}/pp")
        open(f"{self.tmp}/r.md", "w").write(self.ROWS)
        self.T = {self.K: {"qa": 1433, "fp": "mq1-9be27e51512fbd075277", "status": "pr_action_written", "pid": 42, "next_retry_at": "x"},
                  "1456:mq1-7a5374add598af27a3cd": {"qa": 1456, "fp": "mq1-7a5374add598af27a3cd", "status": "queued"},
                  "1500:mq1-aaaa": {"qa": 1500, "fp": "mq1-aaaa", "status": "queued"}, "1500:mq1-bbbb": {"qa": 1500, "fp": "mq1-bbbb", "status": "queued"}}
        json.dump({"tickets": self.T}, open(f"{self.tmp}/pp/tickets.json", "w"))
        self.saved = (prfix.OUT, prfix.RULINGS); prfix.OUT, prfix.RULINGS = f"{self.tmp}/pp", f"{self.tmp}/r.md"
    def tearDown(self): self.p.OUT, self.p.RULINGS = self.saved; shutil.rmtree(self.tmp, ignore_errors=True)
    def got(self): return json.load(open(f"{self.tmp}/pp/tickets.json"))["tickets"]
    def run_(self, *a):
        try: return self.p.main(list(a))
        except SystemExit as e: return e.code

    def test_yes_without_key_refuses(self):
        self.assertNotEqual(self.run_("--close-ruled", "--yes"), 0)
        self.assertEqual(self.got(), self.T)
    def test_yes_with_key_closes_only_that_key(self):
        self.assertEqual(self.run_("--close-ruled", self.K, "--yes"), 0)
        g = self.got(); self.assertEqual(g[self.K]["status"], "ruled_out")
        self.assertEqual(g["1456:mq1-7a5374add598af27a3cd"]["status"], "queued")
    def test_reopen_restores_previous_status_and_fields(self):
        self.assertEqual(self.run_("--close-ruled", self.K, "--yes"), 0)
        self.assertEqual(self.run_("--reopen-ruled", self.K, "--yes"), 0)
        self.assertEqual(self.got()[self.K], self.T[self.K] | {"updated_at": self.got()[self.K]["updated_at"]})
    def test_pr_only_ruling_with_several_tickets_hard_stops(self):
        amb = {x[0]: x[3] for x in self.p.ruled_tickets({"tickets": self.T}, self.ROWS, "r.md")}
        self.assertEqual((amb["1500:mq1-aaaa"], amb["1500:mq1-bbbb"], amb[self.K]), (True, True, False))
        self.assertNotEqual(self.run_("--close-ruled", "1500:mq1-aaaa", "--yes"), 0)
        self.assertEqual(self.got(), self.T)
