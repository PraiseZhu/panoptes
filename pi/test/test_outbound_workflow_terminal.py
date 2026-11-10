#!/usr/bin/env python3
"""11-10: (a) a push GitHub refuses because it changes .github/workflows/ ("refusing to allow a GitHub App to create or update
workflow", #2022: 11 failures on 11-09) is gave_up at once, never retried; other push errors keep the FAIL_GIVE_UP retries.
(b) a key already final in the ledger is listed in the run that made it final, not again every run (rerun-ruled:2030 x139).
Same scratch harness as test_outbound_mute_brief: no network, no sends. Run: python3 test/test_outbound_workflow_terminal.py"""
import json, os, subprocess, sys, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_outbound_mute_brief as B  # noqa: E402  (sandbox + fixtures)
O = B.O
REFUSED = ("To https://github.com/xindong/mivo-canvas-plugin.git\n ! [remote rejected] qa-fix/x -> qa-fix/x (refusing to allow a GitHub App "
           "to create or update workflow `.github/workflows/codeql.yml` without `workflows` permission)\nerror: failed to push some refs")
OTHER = " ! [rejected] qa-fix/x -> qa-fix/x (fetch first)\nerror: failed to push some refs"
FIX, RERUN = "fixpr:2022:mq1-" + "d" * 20, "rerun-ruled:2030:b7c8b0506d63"


class Run(unittest.TestCase):
    def call(self, err):
        r = subprocess.CompletedProcess([], 1, "", err)
        with mock.patch.object(O, "_run_route", lambda c: (c, None)), mock.patch.object(O.subprocess, "run", return_value=r):
            O.run(["git", "-C", "/x", "push", "-u", "origin", "qa-fix/x"])

    def test_workflow_refusal_is_give_up(self):
        with self.assertRaises(O.GiveUp) as e: self.call(REFUSED)
        self.assertIn("refusing to allow a GitHub App", str(e.exception))

    def test_other_push_error_stays_retryable(self):
        with self.assertRaises(RuntimeError) as e: self.call(OTHER)
        self.assertNotIsInstance(e.exception, O.GiveUp)


class Main(unittest.TestCase):
    tearDown, main, ledger, report = B.T.tearDown, B.T.main, B.T.ledger, B.T.report   # B.T's sandbox, not its tests
    def setUp(self):
        B.T.setUp(self)
        for job in ("pm", "ops", "handoff", "daily"): os.remove(f"{self.tmp}/out/{job}/{B.DAY}.actions.json")
        os.makedirs(f"{self.tmp}/out/praise-pr")
        acts = [{"key": FIX, "type": "gh.pr.create(draft)", "qa": 2022, "at": B.NOW.isoformat()},
                {"key": RERUN, "type": "gh.run.rerun-failed", "at": B.NOW.isoformat()}]
        with open(f"{self.tmp}/out/praise-pr/{B.DAY}.actions.json", "w") as f: json.dump({"actions": acts}, f)
        self.err, self.calls = REFUSED, []
        O.run = self.saved["run"]   # the real run(), with git itself faked below
        def fake_sub(cmd, **kw): self.calls.append(cmd); return subprocess.CompletedProcess(cmd, 1, "", self.err)
        for p in (mock.patch.object(O, "_run_route", lambda c: (c, None)), mock.patch.object(O.subprocess, "run", fake_sub),
                  mock.patch.object(O, "gates", lambda: {}),
                  mock.patch.object(O, "handler", lambda a: ("gh", lambda: "pending", lambda: "ok",
                      (lambda: O.run(["git", "-C", "/x", "push", "-u", "origin", "qa-fix/x"])) if a["key"] == FIX else (lambda: "rerun requested")))):
            p.start(); self.addCleanup(p.stop)

    def rows(self, key): return [l for l in self.report().splitlines() if key in l]

    def test_workflow_push_refused_gives_up_at_once(self):
        self.main()
        self.assertEqual(self.ledger()[FIX]["status"], "gave_up"); self.assertNotIn("fails", self.ledger()[FIX])
        self.assertEqual(len(self.rows(FIX)), 1); self.assertIn("GAVE_UP", self.rows(FIX)[0])
        for _ in range(3): self.main()
        self.assertEqual(len(self.calls), 1)                      # never pushed again
        self.assertEqual(self.rows(FIX), [])                      # and not listed again

    def test_other_push_error_is_retried_as_before(self):
        self.err = OTHER; self.main()
        e = self.ledger()[FIX]; self.assertEqual((e["status"], e["fails"]), ("failed", 1))
        self.assertIn("FAILED", self.rows(FIX)[0])

    def test_final_key_listed_once_over_runs(self):
        self.main()
        self.assertEqual(self.ledger()[RERUN]["status"], "done"); self.assertIn("SENT", self.rows(RERUN)[0])
        for _ in range(3):
            self.main(); self.assertEqual(self.rows(RERUN), [])
        self.assertEqual(self.ledger()[RERUN]["status"], "done")


if __name__ == "__main__": unittest.main()
