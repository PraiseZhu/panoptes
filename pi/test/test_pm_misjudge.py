"""pm.py / pm_extra 三处误判（fleet-standards/pm-daily/2026-11-02.md §14，数据日 11-02 实测）：
1. G5：合进 integration/* 的 PR，检查名是「review-pr (integration)」，旧代码只认「review-pr」→ 记成「无 review-pr」。
2. 修复 PR 验收：只认正文「QA: #N」，漏了标题「QA #N」（#1440 QA #1409、#1559 QA #1478）；正文不带冒号的 #1585 不算。
3. 第三轮：fix_round=3 但台账 retest=pass、status=fixed（#966、#1077）已修好，旧代码仍报「第三轮未报出」。
只用临时目录，不读写真实数据。"""
import sys, os, json, tempfile, importlib, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import isolated, assert_inside, BIN  # noqa: E402

def load(tmp):
    sys.path.insert(0, BIN)
    import pm, pm_extra
    pm_extra.QA = f"{tmp}/qa"; pm_extra.RPT = f"{tmp}/qa/reports/merged"
    assert_inside(tmp, pm_extra.QA, pm_extra.RPT)
    return pm, pm_extra

def row(pr, fp, status, retest, rnd):
    return {"pr": pr, "fingerprint": fp, "status": status, "retest": retest, "fix_round": rnd, "fix_prs": []}

class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(f"{self.tmp}/qa/ledger/daily"); os.makedirs(f"{self.tmp}/qa/reports/merged")

    def test_g5_integration_review_pr_counts(self):
        with isolated(PI_STATE=self.tmp):
            pm, _ = load(self.tmp)
            self.assertTrue(pm.is_review_pr("review-pr"))
            self.assertTrue(pm.is_review_pr("review-pr (integration)"))   # #1577 等 7 个 integration/canvas-s2 PR
            self.assertFalse(pm.is_review_pr("review-pr-lite"))
            self.assertFalse(pm.is_review_pr("pr-format-gate (title/body template)"))

    def _retest(self, prs, rows, needs=None):
        json.dump({"rows": rows, "needs_you": needs or []}, open(f"{self.tmp}/qa/ledger/daily/2026-11-02.json", "w"))
        with isolated(PI_STATE=self.tmp):
            _, X = load(self.tmp)
            miss = []
            return X.retest("2026-11-02", prs, miss), miss

    def test_fix_pr_title_qa_ref(self):
        prs = [{"number": 1440, "title": "fix(fixtures): 保留输出产物，避免清理误删 (QA #1409)", "body": "改动说明"},
               {"number": 1559, "title": "fix: 另存等待期间同路径替换的文件仍会被删除 (QA #1478)", "body": ""},
               {"number": 1585, "title": "chore: 更新日志补扫 2026-11-01", "body": "补扫 QA #1491 QA #1492"},
               {"number": 1600, "title": "feat: x", "body": "QA: #1500"}]
        L, _ = self._retest(prs, [])
        line = next(x for x in L if x.startswith("- 数据日合并的修复 PR"))
        self.assertIn("3 个", line)
        for s in ("#1440(QA #1409)", "#1559(QA #1478)", "#1600(QA #1500)"): self.assertIn(s, line)
        self.assertNotIn("#1585", line)

    def test_third_round_fixed_not_reported(self):
        rows = [row(966, "mq1-a", "fixed", "pass", 3), row(966, "mq1-b", "fixed", "pass", 3),
                row(1077, "mq1-c", "fixed", "pass", 3), row(1089, "mq1-d", "open", "fail", 2),
                row(2001, "mq1-e", "open", "fail", 3)]
        L, miss = self._retest([], rows)
        self.assertEqual([m for m in miss if "第三轮" in m], ["第三轮未报出：#2001 round=3（日报）"])
        line = next(x for x in L if x.startswith("- 轮次字段"))
        self.assertIn("第三轮 1 条", line); self.assertNotIn("#966", line); self.assertNotIn("#1077", line)
        L, miss = self._retest([], rows, needs=[{"pr": 2001}])     # 已进「需要你做」就不报
        self.assertFalse([m for m in miss if "第三轮" in m])

if __name__ == "__main__":
    unittest.main()
