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
    def test_real_problems_still_alert(self):
        t = {"9": {"qa": 9, "status": "pr_action_written", "action_key": "fixpr:9", "updated_at": self.old},
             "rev:8": {"pr": 8, "status": "pushed", "action_key": "revise:8:abc", "updated_at": self.old}}
        self.assertEqual(self.keys(t, report="| praise-pr | revise:8:abc | gh.pr.push-fix | x | failed |  |\n"), ["prfix:nopr:9", "prfix:nopush:rev:8"])

if __name__ == "__main__": unittest.main()
