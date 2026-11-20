"""devin/bin/parity-diff.py 与 devin/conf/replay-set.json 的单元测试（内联夹具，结构对齐 docs/report-schema.md）"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "bin", "parity-diff.py")
REPLAY_SET = os.path.join(ROOT, "conf", "replay-set.json")
REPLAY_PRS = [2509, 2464, 2505, 2500, 2474, 2299, 2368, 2486, 2458, 2463]
DETERMINISTIC = ["install", "typecheck", "unit", "lint", "secret-scan"]
UNIT_TEST = "src/a.test.ts > a > 存量一致"


def check(cid, status="pass", **extra):
    return dict({"id": cid, "name": cid, "kind": "product", "status": status, "exit_code": 0 if status == "pass" else 1,
                 "duration_ms": 1000, "ended_at": "2026-11-18T12:50:00+08:00"}, **extra)


def failure(cid, test, status="pre-existing", failure_type="assertion", **evidence):
    return {"check_id": cid, "check_name": cid, "test": test, "failure_type": failure_type, "status": status,
            "evidence": dict({"tested_sha": "abc", "timed_out": False}, **evidence)}


def report(number=2500, **pr_extra):
    pr = {"number": number, "tested_sha": "abc", "merged_at": "2026-11-18T02:38:28Z",
          "test_started_at": "2026-11-18T10:39:04+08:00", "verdict": "clean",
          "checks": [check(c) for c in DETERMINISTIC] + [check("bench", duration_ms=50000),
                                                         check("ai-test-1", source="ai-test")],
          "failed_checks": [],
          "code_review": {"status": "passed", "ran": True}, "ai_summary": {"status": "ok"},
          "triage": {"status": "ok"}}
    pr.update(pr_extra)
    return {"schema_version": "1.0", "repo": "xindong/mivo-canvas-plugin", "system_health": {"status": "ok"},
            "prs": [pr]}


def with_unit_failures(rep, tests, status="pre-existing", **kw):
    pr = rep["prs"][0]
    next(c for c in pr["checks"] if c["id"] == "unit")["status"] = "fail"
    pr["failed_checks"] = [failure("unit", t, status, **kw) for t in tests]
    return rep


class ParityDiffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, data):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False))
        return path

    def run_diff(self, box, devin, mode="full-steps"):
        args = [sys.executable, SCRIPT]
        if box is not None:
            args += ["--box", box if isinstance(box, str) else self.write("box.json", box)]
        if devin is not None:
            args += ["--devin", devin if isinstance(devin, str) else self.write("devin.json", devin)]
        if mode is not None:
            args += ["--mode", mode]
        return subprocess.run(args, capture_output=True, text=True)

    def row(self, out, cid):
        rows = [line for line in out.splitlines() if line.startswith(f"| {cid} |")]
        self.assertEqual(len(rows), 1, f"expected one row for {cid}, got {rows} in:\n{out}")
        return rows[0].rstrip(" |").split(" | ")[-1]

    def test_same_report_self_compare_exits_0(self):
        rep = with_unit_failures(report(), [UNIT_TEST])
        path = self.write("same.json", rep)
        for mode in ("full-steps", "production"):
            r = self.run_diff(path, path, mode)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("| 检查 ID | box 结果 | Devin 结果 | 判定 |", r.stdout)
            for cid in DETERMINISTIC:
                self.assertEqual(self.row(r.stdout, cid), "same")
            self.assertNotIn("DIFF（", r.stdout)

    def test_missing_deterministic_check_is_diff_exit_1(self):
        devin = report()
        devin["prs"][0]["checks"] = [c for c in devin["prs"][0]["checks"] if c["id"] != "lint"]
        r = self.run_diff(report(), devin)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertTrue(self.row(r.stdout, "lint").startswith("DIFF"))
        self.assertEqual(self.row(r.stdout, "typecheck"), "same")

    def test_extra_deterministic_check_on_devin_is_diff(self):
        devin = report()
        devin["prs"][0]["checks"].append(check("cycles"))
        r = self.run_diff(report(), devin)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertTrue(self.row(r.stdout, "cycles").startswith("DIFF"))

    def test_different_unit_failing_test_names_is_diff_exit_1(self):
        box = with_unit_failures(report(), [UNIT_TEST])
        devin = with_unit_failures(report(), ["src/a.test.ts > a > 另一个用例"])
        r = self.run_diff(box, devin)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertTrue(self.row(r.stdout, "unit").startswith("DIFF"))

    def test_extra_failing_test_on_devin_is_diff(self):
        box = with_unit_failures(report(), [UNIT_TEST])
        devin = with_unit_failures(report(), [UNIT_TEST, "src/b.test.ts > b"])
        r = self.run_diff(box, devin)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("Devin 多失败用例", self.row(r.stdout, "unit"))

    def test_different_check_status_is_diff(self):
        devin = report()
        next(c for c in devin["prs"][0]["checks"] if c["id"] == "typecheck")["status"] = "fail"
        r = self.run_diff(report(), devin)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("box=pass Devin=fail", self.row(r.stdout, "typecheck"))

    def test_bench_duration_or_status_difference_is_allowed_bench(self):
        r = self.run_diff(report(), report())
        self.assertEqual(self.row(r.stdout, "bench"), "same")
        devin = report()
        bench = next(c for c in devin["prs"][0]["checks"] if c["id"] == "bench")
        bench["duration_ms"] = 99999
        r = self.run_diff(report(), devin)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.row(r.stdout, "bench"), "allowed:bench")
        bench["status"] = "fail"
        devin["prs"][0]["failed_checks"] = [failure("bench", None, "unverified", failure_type=None)]
        r = self.run_diff(report(), devin)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.row(r.stdout, "bench"), "allowed:bench")

    def test_ai_test_count_difference_is_allowed_model(self):
        devin = report()
        devin["prs"][0]["checks"] += [check("ai-test-2", "discarded", source="ai-test"),
                                      check("ai-test-3", "fail", source="ai-test")]
        r = self.run_diff(report(), devin)
        self.assertEqual(r.returncode, 0, r.stdout)
        for cid in ("ai-test-1", "ai-test-2", "ai-test-3"):
            self.assertEqual(self.row(r.stdout, cid), "allowed:model")

    def test_box_bench_devin_missing_is_diff_exit_1(self):
        devin = report()
        devin["prs"][0]["checks"] = [c for c in devin["prs"][0]["checks"] if c["id"] != "bench"]
        for mode in ("full-steps", "production"):
            r = self.run_diff(report(), devin, mode)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertEqual(self.row(r.stdout, "bench"), "DIFF（Devin 缺 bench）")
        r = self.run_diff(devin, report())
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual(self.row(r.stdout, "bench"), "DIFF（box 无 bench）")

    def test_production_model_content_difference_is_allowed_model(self):
        devin = report()
        pr = devin["prs"][0]
        pr["code_review"] = {"status": "passed", "ran": True, "summary": "另一段结论", "findings_count": 3}
        pr["ai_summary"] = {"summary": "不同的摘要", "risks": ["r"]}
        pr["triage"] = {"status": "ok", "dimensions": {"ui": "applicable"}}
        for mode in ("production", "full-steps"):
            r = self.run_diff(report(), devin, mode)
            self.assertEqual(r.returncode, 0, r.stdout)
            for cid in ("code_review", "ai_summary", "triage"):
                self.assertEqual(self.row(r.stdout, cid), "allowed:model")

    def test_production_missing_model_step_is_diff_exit_1(self):
        for field in ("code_review", "ai_summary", "triage"):
            for value in (None, {"status": "not-run"}, {"status": "capped", "reason": "quota"}):
                devin = report()
                devin["prs"][0][field] = value
                r = self.run_diff(report(), devin, "production")
                self.assertEqual(r.returncode, 1, f"{field}={value}\n{r.stdout}")
                self.assertTrue(self.row(r.stdout, field).startswith(f"DIFF（Devin 缺 {field}"), r.stdout)

    def test_production_failed_or_error_model_step_is_diff_exit_1(self):
        for field in ("code_review", "ai_summary", "triage"):
            for status in ("failed", "error"):
                devin = report()
                devin["prs"][0][field] = {"status": status}
                for mode in ("production", "full-steps"):
                    r = self.run_diff(report(), devin, mode)
                    self.assertEqual(r.returncode, 1, f"{mode} {field}={status}\n{r.stdout}")
                    self.assertTrue(self.row(r.stdout, field).startswith("DIFF"), r.stdout)

    def test_code_review_failed_on_both_sides_is_allowed_model(self):
        rep = report(code_review={"status": "failed", "ran": True})
        r = self.run_diff(rep, report(code_review={"status": "failed", "ran": True, "summary": "x"}), "production")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.row(r.stdout, "code_review"), "allowed:model")
        devin = report(code_review={"status": "error"})
        r = self.run_diff(report(code_review={"status": "error"}), devin, "production")
        self.assertEqual(r.returncode, 1, r.stdout)

    def test_full_steps_disabled_model_steps_are_allowed(self):
        devin = report()
        for field in ("code_review", "ai_summary", "triage"):
            del devin["prs"][0][field]
        r = self.run_diff(report(), devin, "full-steps")
        self.assertEqual(r.returncode, 0, r.stdout)
        for cid in ("code_review", "ai_summary", "triage"):
            self.assertEqual(self.row(r.stdout, cid), "allowed:full-steps-disabled")
        devin["prs"][0]["triage"] = {"status": "not-run"}
        r = self.run_diff(report(), devin, "full-steps")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.row(r.stdout, "triage"), "allowed:full-steps-disabled")
        r = self.run_diff(report(), devin, "production")
        self.assertEqual(r.returncode, 1, r.stdout)

    def test_devin_system_health_not_ok_is_diff_exit_1(self):
        for health in ({"status": "failed"}, {"status": "degraded"}, {}, None):
            devin = report()
            if health is None:
                del devin["system_health"]
            else:
                devin["system_health"] = health
            for mode in ("full-steps", "production"):
                r = self.run_diff(report(), devin, mode)
                self.assertEqual(r.returncode, 1, f"{health}\n{r.stdout}")
                self.assertTrue(self.row(r.stdout, "system_health").startswith("DIFF"), r.stdout)
        box = report()
        box["system_health"] = {"status": "degraded"}
        r = self.run_diff(box, report())
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.row(r.stdout, "system_health"), "same")

    def test_timing_metadata_is_not_compared(self):
        devin = report()
        for c in devin["prs"][0]["checks"]:
            c.update(duration_ms=1, ended_at="2026-11-20T00:00:00+08:00", shards={"count": 4})
        devin["prs"][0]["test_started_at"] = "2026-11-20T00:00:00+08:00"
        r = self.run_diff(report(), devin)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_box_flaky_devin_pass_is_allowed(self):
        box = with_unit_failures(report(), [UNIT_TEST], "flaky", failure_type="test_error")
        r = self.run_diff(box, report())
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.row(r.stdout, "unit"), "allowed:box-flaky")

    def test_box_timeout_devin_pass_is_allowed(self):
        box = report()
        next(c for c in box["prs"][0]["checks"] if c["id"] == "lint")["status"] = "timeout"
        r = self.run_diff(box, report())
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.row(r.stdout, "lint"), "allowed:box-flaky")

    def test_box_unverified_devin_pass_is_diff(self):
        box = with_unit_failures(report(), [UNIT_TEST], "unverified")
        r = self.run_diff(box, report())
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertTrue(self.row(r.stdout, "unit").startswith("DIFF"))

    def test_box_pass_devin_flaky_is_diff(self):
        devin = with_unit_failures(report(), [UNIT_TEST], "flaky", failure_type="test_error")
        r = self.run_diff(report(), devin)
        self.assertEqual(r.returncode, 1, r.stdout)

    def test_latest_main_attribution_allowed_only_in_production(self):
        box = with_unit_failures(report(), [UNIT_TEST], "introduced")
        devin = with_unit_failures(report(), [UNIT_TEST], "fixed-later", latest_main_run="pass")
        r = self.run_diff(box, devin, "production")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.row(r.stdout, "unit"), "allowed:latest-main")
        r = self.run_diff(box, devin, "full-steps")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("归因不同", self.row(r.stdout, "unit"))

    def test_other_attribution_difference_is_diff_in_production(self):
        box = with_unit_failures(report(), [UNIT_TEST], "pre-existing")
        devin = with_unit_failures(report(), [UNIT_TEST], "introduced")
        r = self.run_diff(box, devin, "production")
        self.assertEqual(r.returncode, 1, r.stdout)

    def test_different_tested_sha_is_diff(self):
        r = self.run_diff(report(), report(tested_sha="def"))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertTrue(self.row(r.stdout, "tested_sha").startswith("DIFF"))

    def test_devin_missing_tested_sha_is_diff(self):
        devin = report()
        del devin["prs"][0]["tested_sha"]
        r = self.run_diff(report(), devin)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertTrue(self.row(r.stdout, "tested_sha").startswith("DIFF"))

    def test_missing_pr_is_diff(self):
        r = self.run_diff(report(2500), report(2463))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("Devin 报告缺此 PR", r.stdout)

    def test_missing_input_file_exits_2(self):
        r = self.run_diff(os.path.join(self.tmp.name, "nope.json"), report())
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("file not found", r.stderr)
        r = self.run_diff(report(), os.path.join(self.tmp.name, "nope.json"))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_bad_json_and_bad_shape_exit_2(self):
        r = self.run_diff(self.write("bad.json", "{not json"), report())
        self.assertEqual(r.returncode, 2, r.stderr)
        r = self.run_diff(self.write("noprs.json", {"schema_version": "1.0"}), report())
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("prs[]", r.stderr)

    def test_bad_arguments_exit_2(self):
        path = self.write("r.json", report())
        self.assertEqual(self.run_diff(path, path, "fast").returncode, 2)
        self.assertEqual(self.run_diff(path, path, None).returncode, 2)
        self.assertEqual(self.run_diff(None, path).returncode, 2)


class ReplaySetTest(unittest.TestCase):
    def test_replay_set_is_valid_and_complete(self):
        with open(REPLAY_SET, encoding="utf-8") as f:
            data = json.load(f)
        entries = data["prs"]
        self.assertEqual(sorted(e["pr"] for e in entries), sorted(REPLAY_PRS))
        self.assertEqual(len(entries), len(REPLAY_PRS))
        for e in entries:
            for key in ("pr", "date", "reason"):
                self.assertIn(key, e, f"PR {e.get('pr')} missing {key}")
            self.assertIsInstance(e["pr"], int)
            self.assertRegex(e["date"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(e["reason"].strip())


if __name__ == "__main__":
    unittest.main()
