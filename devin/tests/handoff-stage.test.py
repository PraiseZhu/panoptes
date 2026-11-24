"""Handoff staging and backfill CLI tests, without sending anything."""

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import ModuleType
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pi/bin"))
with patch.dict(sys.modules, {"merger": ModuleType("merger")}):
    import outbound
from lib import today

# Minimal, redacted excerpts of the 11-09 backup and its 11-08 daily/ledger/scan inputs.
DAY = "2026-11-09"
DATA_DAY = "2026-11-08"
REPO = "zhongxingtian-ai/handover-mivo"
OTHER = "aj0928/aj0928-handoff"
TITLE = "生命周期测试只检查源码字符串"
FP = "mq1-1c8c2e3aa027782b045f"
DAILY = f"mivo-daily-report:{DATA_DAY}"
DM = f"dm:{DATA_DAY}:2010"
KEY = f"handoff:2010:{FP}-pr2010"
ISSUE_URL = f"https://github.com/{REPO}/issues/11"
SHA = "0" * 40
ACTION = {
    "key": KEY, "type": "gh.issue.create", "pr": 2010, "title": f"[P2] {TITLE}",
    "text": f"QA 日报（数据日 {DATA_DAY}）在 PR #2010 上测出的问题，请处理。\n\n"
            f"- 级别：P2\n- 问题：{TITLE}\n- 位置：src/lifecycle.test.ts:34\n"
            f"- 测试的合并提交：{SHA}\n- 指纹：{FP}（本 PR：{FP}-pr2010）",
    "labels": ["handoff"], "handoff_key": f"{FP}-pr2010", "route_author": "zhongxingtian-ai",
}
SCAN = f"""# 交接仓回执扫描 {DAY}（Pi line，不写交接仓）

## 扫描 00:05 北京时间（Pi line，只读）
- {OTHER}#7 CLOSED 回执「误报」
- {REPO}#10 OPEN 回执「误报」
  - 注意：https://github.com/{REPO}/issues/10 不在本地 issues.json（Grok 未回写）
- {REPO}#9 OPEN 回执「已修 #1541」
- {REPO}: 9 个 issue，带格式回执 8 个，满 3 天无回执 0 个

## 扫描 06:20 北京时间（Pi line，只读）
- {REPO}#10 OPEN 回执「误报」
- {REPO}: 9 个 issue，带格式回执 8 个，满 3 天无回执 0 个
"""


class HandoffStageTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.qa = Path(directory.name).resolve()
        self.pi = self.qa / "pi"
        self.dest = self.qa / f"handoff/staging/handover-mivo/daily/2026/11/{DAY}"
        self.ledger = {
            DAILY: {"status": "done", "at": "2026-11-09T07:35:04+08:00"},
            DM: {"status": "done", "at": "2026-11-09T07:35:06+08:00", "result": "redacted"},
            KEY: {"status": "done", "at": "2026-11-09T07:35:09+08:00", "result": ISSUE_URL},
        }
        self.write("out/outbound/ledger.json", self.ledger)
        self.write(f"out/daily/{DATA_DAY}.actions.json", {"actions": [ACTION]})
        self.write(f"out/daily/{DATA_DAY}.json", {"fault": [], "health": [{"pr": 2010}],
                   "merged_prs": [{"number": 2010, "author": {"login": "zhongxingtian-ai"}, "title": "接通查看器"},
                                  {"number": 2026, "author": {"login": "aj0928"}, "title": "测试修正"}]})
        self.write(f"out/daily/{DATA_DAY}.md", f"""Mivo 播报 11 月 8 日（北京时间）：测试系统有异常。

P2 一般｜需要修复（1 个）
#2010 接通查看器 — @钟行天
· 问题：{TITLE}
· 审查结论，测试未复现。
· 下一步：增加入口生命周期测试，然后提修复 PR（标题或描述写「QA #2010」）。

P3 提示｜无需处理（1 个）
#2026 测试修正 — 邵申泽
""")
        self.write(f"out/handoff/{DAY}.md", SCAN)

    def write(self, name, value):
        path = self.pi / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def cli(self, *args):
        output = subprocess.run([sys.executable, str(ROOT / "devin/bin/handoff-stage.py"), *args],
                                env={**os.environ, "QA_HOME": str(self.qa), "PI_STATE": str(self.pi)},
                                capture_output=True, text=True)
        self.assertEqual(output.returncode, 0, output.stderr)

    def stage(self):
        self.cli("stage", "--day", DAY)

    def test_sc1_fixture_structure_and_outbound_paths(self):
        self.stage()
        with patch.object(outbound, "QA", str(self.qa)):
            self.assertEqual(outbound.handoff_staging_dir(REPO, DAY), str(self.dest))
            for name in outbound.HANDOFF_FILES:
                path = self.dest / name
                self.assertTrue(path.is_file())
                self.assertEqual(outbound.staged_path(path), str(path))
        feedback = (self.dest / "feedback.md").read_text(encoding="utf-8")
        self.assertTrue(feedback.startswith(f"# {DAY} 发到本仓的问题\n\n来源："))
        self.assertIn(f"pi/out/daily/{DATA_DAY}.md", feedback)
        self.assertIn(f"`{DAILY}`，done，2026-11-09 07:35:04", feedback)
        self.assertIn(f"## #11 {TITLE}（P2，@zhongxingtian-ai 钟行天）", feedback)
        self.assertEqual([line[2:].split("：")[0] for line in feedback.splitlines() if line.startswith("- ")],
                         ["问题", "位置", "测试的合并提交", "指纹", "来源", "下一步", "回执截止", "通知", "交接 issue"])
        for text in (f"- 问题：{TITLE}", "- 位置：src/lifecycle.test.ts:34", f"- 测试的合并提交：{SHA}",
                     f"- 指纹：{FP}（本 PR：{FP}-pr2010）", "- 来源：审查结论，测试未复现。", "- 下一步：增加入口生命周期测试",
                     "- 回执截止：2026-11-12 07:35", f"`{DM}`，done", f"{ISSUE_URL}（ledger `{KEY}`，done"):
            self.assertIn(text, feedback)
        self.assertIn("1 个测试过程有异常（日报 health）", feedback.splitlines()[-1])
        self.assertEqual((self.dest / "receipts.md").read_text(encoding="utf-8"),
                         f"# {DAY} 回执\n\n来源：pi/out/handoff/{DAY}.md（Pi line 只读扫描）。\n\n"
                         "## 00:05 扫描（北京时间）\n- #10 OPEN 回执「误报」\n- #9 OPEN 回执「已修 #1541」\n"
                         "- 合计：9 个 issue，带格式回执 8 个，满 3 天无回执 0 个。\n\n"
                         "## 06:20 扫描（北京时间）\n- #10 OPEN 回执「误报」\n"
                         "- 合计：9 个 issue，带格式回执 8 个，满 3 天无回执 0 个。\n")
        no_findings = (self.qa / f"handoff/staging/aj0928-handoff/daily/2026/11/{DAY}/feedback.md").read_text(encoding="utf-8")
        self.assertIn("本次没有发到本仓的问题。\n- #2026 测试修正（P3）", no_findings)
        self.assertNotIn("#2010", no_findings)

    def test_sc2_only_terminal_handoff_and_done_report_allow_feedback(self):
        for key, status in ((DAILY, "pending"), (KEY, "pending")):
            with self.subTest(key=key):
                self.write("out/outbound/ledger.json", {**self.ledger, key: {"status": status}})
                self.stage()
                self.assertFalse((self.dest / "feedback.md").exists())
                self.assertTrue((self.dest / "receipts.md").is_file())
        for status in ("blocked", "refused"):
            with self.subTest(status=status):
                self.write("out/outbound/ledger.json", {**self.ledger, KEY: {"status": status}})
                self.stage()
                path = self.dest / "feedback.md"
                self.assertIn(f"`{KEY}`，{status}", path.read_text(encoding="utf-8"))
                path.unlink()

    def test_sc3_apply_only_would_open_with_optional_pr_filter(self):
        rows = []
        for pr, status in enumerate(("would-open", "待人工确认", "已开", "out_of_scope", "would-open"), 1340):
            key = f"handoff:{pr}:{FP}-pr{pr}"
            action = {**ACTION, "repo": REPO, "pr": pr, "key": key, "handoff_key": f"{FP}-pr{pr}"}
            rows.append({"pr": pr, "repo": REPO, "key": key, "status": status, "action": action})
        day = today()
        self.write(f"out/handoff/backfill-{day}.json", {"rows": rows})
        path = self.pi / f"out/handoff-backfill/{day}.actions.json"
        self.cli("backfill-apply")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["actions"],
                         [rows[0]["action"], rows[4]["action"]])
        self.cli("backfill-apply", "--pr", "1341", "--pr", "1344")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["actions"], [rows[4]["action"]])


if __name__ == "__main__":
    unittest.main()
