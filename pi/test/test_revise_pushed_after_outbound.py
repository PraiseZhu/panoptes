#!/usr/bin/env python3
"""11-04 (#1724, deploy-185 EVIDENCE): prfix --revise set tickets.json rev:<N> to "pushed" the moment it WROTE the gh.pr.push-fix
action, before outbound pushed anything (rev:1724 read pushed while outbound only ever logged stale). Now it writes
push_action_written and the ticket turns pushed only when outbound's ledger records that action_key as done (settle_push, at the next
--revise of that PR). Both states still mean "this head is in hand". Local only. Run: python3 test/test_revise_pushed_after_outbound.py"""
import datetime as dt, os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_revise_dirty_after_rerun as D  # noqa: E402
H, prfix = D.H, D.prfix

NOW = dt.datetime(2026, 11, 4, 5, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
KEY = "revise:1301:" + H[:12] + ":merge"

class Settle(unittest.TestCase):
    def test_written_turns_pushed_only_on_ledger_done(self):
        t = {"status": "push_action_written", "action_key": KEY, "history": []}
        self.assertEqual(prfix.settle_push(t, {}, NOW)["status"], "push_action_written")
        for st in ("stale", "gave_up", "failed", None):
            self.assertEqual(prfix.settle_push(t, {KEY: {"status": st}}, NOW)["status"], "push_action_written", st)
        u = prfix.settle_push(t, {KEY: {"status": "done"}}, NOW)
        self.assertEqual((u["status"], u["history"][-1]["status"]), ("pushed", "pushed"))
        self.assertEqual(t["status"], "push_action_written")   # input not mutated
    def test_other_states_untouched(self):
        for st in ("running", "parked", "pushed", "needs_praise"):
            self.assertEqual(prfix.settle_push({"status": st, "action_key": KEY}, {KEY: {"status": "done"}}, NOW)["status"], st)

class Revise(unittest.TestCase):
    run_revise = D.T.run_revise
    def ledger(self, d):
        real = prfix.outbound_ledger; prfix.outbound_ledger = lambda: d; self.addCleanup(setattr, prfix, "outbound_ledger", real)
    def test_writing_the_push_action_is_not_pushed(self):   # main: "pushed" right here -> fails
        self.ledger({})
        r, calls, t = self.run_revise({"status": "queued", "revisions": 0})
        self.assertEqual(calls, ["merge"], r)
        self.assertEqual((t["status"], t["action_key"], t["last_head"]), ("push_action_written", KEY, H))
    def test_written_head_is_in_hand_then_settles_after_outbound(self):
        w = {"status": "push_action_written", "action_key": KEY, "last_head": H, "merges": 1, "last_cnt": "merges", "history": []}
        self.ledger({}); r, calls, t = self.run_revise(w)   # outbound has not pushed yet: still in hand, no second merge
        self.assertEqual((calls, r["status"], t["status"]), ([], "skipped", "push_action_written"))
        self.ledger({KEY: "done"}); r, calls, t = self.run_revise(w)   # outbound_ledger() maps key -> status string
        self.assertEqual((calls, r["status"], t["status"]), ([], "skipped", "pushed"))

if __name__ == "__main__": unittest.main()
