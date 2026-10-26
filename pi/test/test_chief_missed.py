#!/usr/bin/env python3
"""chief.md 漏项两节（Praise 2026-10-26 口径）。本地、无网络、只写临时目录。Run: python3 test/test_chief_missed.py"""
import os, sys, json, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import chief_missed as C

ROW = lambda pr, **kw: {"pr": pr, "level": "P1", "source": "code-review", "status": "fix_merged_pending_retest", "status_zh": "待复测",
                        "fix_round": 3, "retest": "pending", "retest_pr": 2000, **kw}

class Base(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp(); os.makedirs(f"{self.t}/out/logs")
        self.qa = f"{self.t}/qa"; os.makedirs(f"{self.qa}/ledger/daily"); C.QA = self.qa
        C.ROUTES = f"{self.t}/routes.json"
        json.dump({"routes": [{"repo": "o/h", "owner": "own", "authors": ["a"]}]}, open(C.ROUTES, "w"))
    def ledger(self, rows, day="2026-01-02"): json.dump({"rows": rows}, open(f"{self.qa}/ledger/daily/{day}.json", "w"))

class System(Base):
    def test_missed_and_failed_with_recovery_and_crash_dedupe(self):
        open(f"{self.t}/out/logs/watchdog-2026-01-02.log", "w").write(
            "x new=['missed:pm:1035', 'stale:foo', 'failed:daily:0835'] recovered=[] open=3\n"
            "y new=[] recovered=['missed:pm:1035'] open=2\n")
        out = C.system_section("2026-01-02", "", self.t, crash="/l/daily-2026-01-02.log")
        self.assertEqual(len(out), 2, out)
        self.assertIn("missed:pm:1035", out[0]); self.assertIn("已恢复", out[0]); self.assertIn("负责人", out[0]); self.assertIn("期限", out[0])
        self.assertIn("daily 崩溃", out[1]); self.assertIn("/l/daily-2026-01-02.log", out[1])
        self.assertFalse(any("stale:" in x for x in out))
    def test_dedupe_against_daily(self):
        open(f"{self.t}/out/logs/watchdog-2026-01-02.log", "w").write("x new=['missed:pm:1035'] recovered=[] open=1\n")
        self.assertEqual(C.system_section("2026-01-02", "pm 作业漏跑一次", self.t), [])

class Work(Base):
    MISS = ["第三轮未报出：#966 round=3（日报）", "第三轮未报出：#1077 round=3（日报）", "交接 o/h#2 超 72h 未见逾期提醒", "G3 日报 PR 数不一致"]
    ISS = staticmethod(lambda repo, n: {"state": "OPEN", "createdAt": "2026-01-01T00:00:00Z", "url": f"https://github.com/{repo}/issues/{n}", "title": "[P1] x"})
    def test_three_known_items(self):
        self.ledger([ROW(966), ROW(1077, level="P2", status="fixed", retest="pass"), ROW(1077, level="P2", fix_round=1)])
        out = C.work_section("2026-01-02", "2026-01-01", self.MISS, "", self.ISS)
        self.assertEqual(len(out), 2, out)
        self.assertTrue(any("#966" in x and "/pull/966" in x for x in out))
        self.assertFalse(any("#1077" in x for x in out), "1077 round 3 fixed + retest pass: not listed")
        h = [x for x in out if "o/h#2" in x][0]; self.assertIn("own", h); self.assertIn("issues/2", h)
    def test_unverified_source_and_closed_issue_excluded(self):
        self.ledger([ROW(966, source="ai-test")])
        closed = lambda repo, n: dict(self.ISS(repo, n), state="CLOSED")
        self.assertEqual(C.work_section("2026-01-02", "2026-01-01", self.MISS, "", closed), [])
        self.assertEqual(C.work_section("2026-01-02", "2026-01-01", self.MISS, "", lambda r, n: None), [], "gh unknown = unverified")
    def test_no_owner_not_listed(self):
        self.ledger([]); json.dump({"routes": []}, open(C.ROUTES, "w"))
        self.assertEqual(C.work_section("2026-01-02", "2026-01-01", self.MISS, "", self.ISS), [])
    def test_dedupe_against_daily(self):
        self.ledger([ROW(966)])
        self.assertEqual(C.work_section("2026-01-02", "2026-01-01", self.MISS[:1], "#966 第三轮仍未过，转人工", self.ISS), [])

class PmFallback(Base):
    def test_latest_json_not_after_data_day(self):
        od = f"{self.t}/pm"; os.makedirs(od)
        for d in ("2026-01-01", "2026-01-03"): json.dump({"missing": [d]}, open(f"{od}/{d}.json", "w"))
        self.assertEqual(C.pm_missing("2026-01-03", od), ("2026-01-01", ["2026-01-01"]))

if __name__ == "__main__": unittest.main()
