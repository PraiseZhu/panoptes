"""devin/bin/sla-report.py 的单元测试（临时目录里构造 reports/merged/PR-N.json 夹具，结构对齐 docs/report-schema.md）"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "sla-report.py")
BASE = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
SHANGHAI = dt.timezone(dt.timedelta(hours=8))


def iso_z(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SlaReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = os.path.join(self.tmp.name, "merged")
        os.makedirs(self.dir)

    def merged_at(self, number, hours_after=None):
        return BASE + dt.timedelta(hours=number - 1000 if hours_after is None else hours_after)

    def write_report(self, number, latency_s, hours_after=None, trigger="event", **override):
        merged = self.merged_at(number, hours_after)
        started = (merged + dt.timedelta(seconds=latency_s)).astimezone(SHANGHAI).isoformat()
        pr = {"number": number, "merged_at": iso_z(merged), "test_started_at": started, "verdict": "clean"}
        pr.update(override)
        data = {"schema_version": "1.0", "prs": [pr]}
        if trigger is not None:
            data["run"] = {"trigger": trigger}
        self.write_raw(f"PR-{number}.json", data)

    def write_merged_list(self, numbers, hours_after=None):
        path = os.path.join(self.tmp.name, "merged-list.json")
        items = [{"number": n, "mergedAt": iso_z(self.merged_at(n, None if hours_after is None else hours_after[i]))}
                 for i, n in enumerate(numbers)]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f)
        return path

    def write_raw(self, name, data, sub=None):
        d = os.path.join(self.dir, sub) if sub else self.dir
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def run_sla(self, *args, reports_dir=None, env=None):
        cmd = [sys.executable, SCRIPT]
        if reports_dir is not False:
            cmd += ["--reports-dir", reports_dir or self.dir]
        return subprocess.run(cmd + list(args), capture_output=True, text=True, env=env)

    def value(self, out, label):
        m = re.search(rf"^{label}：([0-9.]+) 秒", out, re.M)
        self.assertIsNotNone(m, f"no {label} in:\n{out}")
        return float(m.group(1))

    def test_29_within_and_one_150s_passes_with_stats(self):
        latencies = [30 + i for i in range(29)] + [150]
        for i, lat in enumerate(latencies):
            self.write_report(1000 + i, lat)
        r = self.run_sla("--last", "30")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("样本数：30", r.stdout)
        self.assertEqual(self.value(r.stdout, "中位数"), 44.5)
        self.assertEqual(self.value(r.stdout, "p95"), 58.0)
        self.assertEqual(self.value(r.stdout, "最大值"), 150.0)
        self.assertIn("#1029（150 秒）", r.stdout)

    def test_two_over_120_fails_and_lists_all_slow_prs(self):
        latencies = [40] * 28 + [130, 200]
        for i, lat in enumerate(latencies):
            self.write_report(2000 + i, lat)
        r = self.run_sla("--last", "30")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.value(r.stdout, "p95"), 130.0)
        self.assertEqual(self.value(r.stdout, "最大值"), 200.0)
        self.assertIn("超过 120 秒的 PR（2 个）", r.stdout)
        self.assertIn("#2029（200 秒）", r.stdout)
        self.assertIn("#2028（130 秒）", r.stdout)

    def test_p95_exactly_120_passes(self):
        for i in range(20):
            self.write_report(3000 + i, 120)
        r = self.run_sla()
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_last_keeps_most_recently_merged(self):
        for i in range(5):
            self.write_report(4000 + i, 500, hours_after=i)
        for i in range(30):
            self.write_report(5000 + i, 20, hours_after=100 + i)
        r = self.run_sla("--last", "30")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("#400", r.stdout)
        r = self.run_sla()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("样本数：35", r.stdout)
        self.assertIn("超过 120 秒的 PR（5 个）", r.stdout)

    def test_latest_2_of_32_missing_test_started_at_exits_2_not_replaced(self):
        for i in range(30):
            self.write_report(1000 + i, 30)
        self.write_report(1030, 30, test_started_at=None)
        self.write_report(1031, 30, test_started_at=None)
        r = self.run_sla("--last", "30")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("p95", r.stdout)
        self.assertIn("#1030：PR-1030.json 缺字段 test_started_at", r.stderr)
        self.assertIn("#1031：PR-1031.json 缺字段 test_started_at", r.stderr)
        self.assertIn("2 target PR(s)", r.stderr)

    def test_bad_old_report_outside_last_window_does_not_block(self):
        self.write_report(1000, 30, test_started_at=None)
        for i in range(30):
            self.write_report(1001 + i, 30)
        r = self.run_sla("--last", "30")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("样本数：30", r.stdout)
        r = self.run_sla()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("#1000", r.stderr)

    def test_every_bad_target_is_listed_and_exits_2(self):
        for i in range(3):
            self.write_report(6000 + i, 30)
        self.write_report(6100, 30, test_started_at=None)
        self.write_report(6101, 30, merged_at=None)
        self.write_report(6102, 30, merged_at="not a time")
        self.write_report(6103, -10)
        self.write_raw("PR-6104.json", "{broken")
        self.write_raw("PR-6105.json", {"run": {"trigger": "event"}, "prs": [{"number": 1}]})
        r = self.run_sla()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("p95", r.stdout)
        self.assertIn("6 target PR(s)", r.stderr)
        self.assertIn("PR-6100.json 缺字段 test_started_at", r.stderr)
        self.assertIn("PR-6101.json 缺字段 merged_at", r.stderr)
        self.assertIn("PR-6102.json 时间格式无法解析", r.stderr)
        self.assertIn("PR-6103.json 开测早于合并", r.stderr)
        self.assertIn("PR-6104.json 无法读取 JSON", r.stderr)
        self.assertIn("PR-6105.json prs[] 里没有 #6105", r.stderr)

    def test_not_enough_targets_exits_2(self):
        for i in range(29):
            self.write_report(7000 + i, 30)
        r = self.run_sla("--last", "30")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("need 30, got 29", r.stderr)

    def test_trigger_event_by_default_excludes_backfill_replay_and_manual(self):
        for i in range(30):
            self.write_report(1000 + i, 30)
        self.write_report(1030, 900, trigger="backfill")
        self.write_report(1031, 900, trigger="replay")
        self.write_report(1032, 900, trigger=None)
        self.write_report(1033, 900, trigger="event", test_started_at=None)
        self.write_raw("PR-1034.json", {"run": {"trigger": "backfill"}, "prs": [{"number": 1034}]})
        r = self.run_sla("--last", "30")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("#1033", r.stderr)
        self.assertNotIn("#1034", r.stderr)
        os.remove(os.path.join(self.dir, "PR-1033.json"))
        r = self.run_sla("--last", "30")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("trigger=event", r.stdout)
        self.assertEqual(self.value(r.stdout, "最大值"), 30.0)
        r = self.run_sla("--last", "30", "--trigger", "backfill")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        r = self.run_sla("--last", "30", "--trigger", "all")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("#1034", r.stderr)
        os.remove(os.path.join(self.dir, "PR-1034.json"))
        r = self.run_sla("--last", "30", "--trigger", "all")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("超过 120 秒的 PR（3 个）", r.stdout)

    def test_since_filters_prs_merged_before_cutover(self):
        for i in range(5):
            self.write_report(1000 + i, 600)
        self.write_report(1005, 30, test_started_at=None)
        for i in range(30):
            self.write_report(1010 + i, 30)
        cutover = iso_z(self.merged_at(1010))
        r = self.run_sla("--since", cutover)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("样本数：30", r.stdout)
        self.assertIn(f"since={cutover}", r.stdout)
        r = self.run_sla("--since", iso_z(self.merged_at(1005)))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("#1005", r.stderr)
        self.assertEqual(self.run_sla("--since", "2026-11-01T00:00:00").returncode, 2)
        self.assertEqual(self.run_sla("--since", "yesterday").returncode, 2)

    def test_merged_list_is_the_denominator_and_missing_report_exits_2(self):
        for i in range(30):
            self.write_report(1000 + i, 30)
        r = self.run_sla("--last", "30", "--merged-list", self.write_merged_list(range(1000, 1030)))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("样本数：30", r.stdout)
        r = self.run_sla("--last", "30", "--merged-list", self.write_merged_list(range(1000, 1031)))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("#1030：缺报告 PR-1030.json", r.stderr)
        self.write_report(1030, 30, trigger="catch-up")
        r = self.run_sla("--last", "30", "--merged-list", self.write_merged_list(range(1000, 1031)))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("run.trigger 是 catch-up，要求 event", r.stderr)
        r = self.run_sla("--last", "30", "--merged-list", self.write_merged_list(range(1000, 1031)),
                         "--trigger", "all")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_bad_merged_list_exits_2(self):
        self.write_report(1000, 30)
        bad = os.path.join(self.tmp.name, "bad.json")
        for content in ("{broken", json.dumps({"number": 1000}), json.dumps([{"number": "1000"}])):
            with open(bad, "w", encoding="utf-8") as f:
                f.write(content)
            self.assertEqual(self.run_sla("--merged-list", bad).returncode, 2, content)
        self.assertEqual(self.run_sla("--merged-list", os.path.join(self.tmp.name, "nope.json")).returncode, 2)

    def test_only_top_level_pr_files_are_read(self):
        self.write_report(8000, 30)
        self.write_raw("PR-8001.json", {"prs": [{"number": 8001, "merged_at": "2026-11-01T00:00:00Z",
                                                 "test_started_at": "2026-11-01T09:00:00+08:00"}]}, sub="history")
        self.write_raw("PR-8002.v1.json", {"prs": [{"number": 8002, "merged_at": "2026-11-01T00:00:00Z",
                                                    "test_started_at": "2026-11-01T09:00:00+08:00"}]})
        r = self.run_sla()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("样本数：1", r.stdout)

    def test_empty_dir_exits_2(self):
        r = self.run_sla()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("no target PRs", r.stderr)
        r = self.run_sla("--last", "30")
        self.assertEqual(r.returncode, 2)

    def test_usage_errors_exit_2(self):
        self.write_report(9000, 30)
        self.assertEqual(self.run_sla("--last", "0").returncode, 2)
        self.assertEqual(self.run_sla("--last", "x").returncode, 2)
        self.assertEqual(self.run_sla("--bogus").returncode, 2)
        self.assertEqual(self.run_sla(reports_dir=os.path.join(self.tmp.name, "nope")).returncode, 2)

    def test_default_reports_dir_from_env(self):
        self.write_report(9100, 30)
        env = dict(os.environ, MIVO_QA_REPORTS=self.tmp.name)
        r = self.run_sla(reports_dir=False, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(self.dir, r.stdout)


if __name__ == "__main__":
    unittest.main()
