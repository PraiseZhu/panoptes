#!/usr/bin/env python3
"""Praise 2026-11-02: Pi fixes QA findings on kirozeng's PRs too (FIX_AUTHORS), and a DM to a FIX_AUTHORS author ends with one
"is a bot following this up" line per problem, read from the ledger / prfix tickets / p2-rulings.
touch <state>/fix-authors-praise-only -> PraiseZhu only again. Scratch state only, no network, no sends.
Run: python3 test/test_fix_authors.py"""
import json, os, shutil, sys, tempfile, types, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); BIN = os.path.join(os.path.dirname(HERE), "bin"); sys.path.insert(0, BIN)
sys.path.insert(0, HERE); import isolation  # noqa: E402
isolation.sandbox_state()
os.environ.pop("PRFIX_OWNERS", None)
sys.modules.setdefault("merger", types.SimpleNamespace(FIELDS="number", blockers=lambda p: []))
import finding_policy as P, prfix, outbound as O  # noqa: E402
import lib as L  # noqa: E402
assert os.path.realpath(L.BASE) != os.path.realpath(isolation.live_state()), f"refusing to run against live state {L.BASE}"

FP = lambda c: "mq1-" + c * 20
P2 = lambda c, **kw: {"severity": "P2", "source": "code-review", "verdict": "CONFIRMED", "verification": {"status": "confirmed"},
                      "fingerprint": FP(c), "title": f"problem {c}", **kw}

class Base(unittest.TestCase):
    def setUp(self):
        # unittest discover imports every test module before running any test: the pop above is undone by later modules'
        # import-time setdefault('PRFIX_OWNERS', '*') (test_grokline, test_prfix_unit, ...). Own env per test, restored after.
        e = mock.patch.dict(os.environ); e.start(); self.addCleanup(e.stop); os.environ.pop("PRFIX_OWNERS", None)
        self.tmp = tempfile.mkdtemp(prefix="fix-authors-")
        self.saved = (os.environ.get("PI_STATE"), O.QA, O.BASE, O.P2_RULINGS)
        os.environ["PI_STATE"] = self.tmp; O.QA = f"{self.tmp}/qa"; O.BASE = self.tmp; O.P2_RULINGS = f"{self.tmp}/p2-rulings.md"
    def tearDown(self):
        st, O.QA, O.BASE, O.P2_RULINGS = self.saved
        os.environ["PI_STATE"] = st
        shutil.rmtree(self.tmp, ignore_errors=True)
    def off(self): open(f"{self.tmp}/fix-authors-praise-only", "w").close()
    def wj(self, p, d):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f: json.dump(d, f)

class FixAuthors(Base):
    def test_default_and_rollback_flag(self):
        self.assertEqual(P.fix_authors(), ("PraiseZhu", "kirozeng"))
        self.assertEqual(prfix.owners(), {"PraiseZhu", "kirozeng"}, "prfix picks kiro's PRs by default")
        self.off()
        self.assertEqual(P.fix_authors(), ("PraiseZhu",), "flag file -> PraiseZhu only, re-read on every call")
        self.assertEqual(prfix.owners(), {"PraiseZhu"})
    def test_self_pr_detection_stays_praise_only(self):
        self.assertEqual(prfix.AUTHOR_SELF, "PraiseZhu")

class BotFollowup(Base):
    def setUp(self):
        super().setUp()
        self.wj(f"{O.QA}/ledger/daily/2026-11-02.json", {"rows": [
            {"pr": 1449, "fingerprint": FP("a"), "status": "fix_pr_open", "fix_pr": 1700},
            {"pr": 1449, "fingerprint": FP("b"), "status": "concluded", "fix_pr": None},
            {"pr": 1449, "fingerprint": FP("c"), "status": "open", "fix_pr": None},
            {"pr": 1449, "fingerprint": FP("d"), "status": "open", "fix_pr": None}]})
        self.wj(f"{self.tmp}/out/praise-pr/tickets.json", {"tickets": {f"1449:{FP('c')}": {"status": "pr_action_written"},
                                                                       f"1449:{FP('e')}": {"status": "parked"}}})
        self.pr = {"number": 1449, "author": {"login": "kirozeng"}, "findings": [P2(c) for c in "abcde"]}
    def test_four_statuses_from_real_state(self):
        b = O.bot_followup_block(1449, self.pr)
        self.assertEqual(b.splitlines(), ["bot 跟进情况：",
            "· problem a（P2）：已由 Praise PR 跟进，修复 PR #1700",
            "· problem b（P2）：判定不修",
            "· problem c（P2）：已派单待认领",
            "· problem d（P2）：暂无 bot 跟进",
            "· problem e（P2）：暂无 bot 跟进"])
    def test_p2_ruling_no_fix(self):
        with open(O.P2_RULINGS, "w") as f: f.write("| 日期 | 报告 PR | 问题 | 裁决 | 理由 |\n|---|---|---|---|---|\n| 11-02 | #1449 | x | 不修 | y |\n")
        self.assertIn("problem d（P2）：判定不修", O.bot_followup_block(1449, self.pr))
    def test_dm_end_kiro_only(self):
        saved = O.load_report
        O.load_report = lambda n: (None, {"pr": dict(self.pr, author={"login": self.author})})
        try:
            self.author = "kirozeng"; t = open(O.feishu_text({"pr": 1449, "level": "P2"})).read()
            self.assertTrue(t.rstrip().endswith("· problem e（P2）：暂无 bot 跟进"), t)
            self.assertLess(t.index("怎么告诉我处理好了"), t.index("bot 跟进情况："), "added at the very end")
            self.author = "aj0928"; self.assertNotIn("bot 跟进情况", open(O.feishu_text({"pr": 1449, "level": "P2"})).read())
            self.off(); self.author = "kirozeng"
            self.assertNotIn("bot 跟进情况", open(O.feishu_text({"pr": 1449, "level": "P2"})).read(), "rollback flag -> old DM")
        finally: O.load_report = saved

if __name__ == "__main__": unittest.main()
