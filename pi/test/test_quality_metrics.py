#!/usr/bin/env python3
"""Contracts for the read-only exact-replay quality metrics."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("PI_BIN") or os.path.join(os.path.dirname(HERE), "bin"))
import quality_metrics as qm  # noqa: E402
import opsfix_retest as OR  # noqa: E402


SHA_A = "a" * 40
SHA_B = "b" * 40


def finding(fp, pr=1, base=SHA_B):
    return {"fingerprint": fp, "source": "check", "tested_sha": SHA_A, "base_sha": base,
            "run_id": "qa-run-1", "artifact_refs": ["step-1"], "environment": {"node": "24"},
            "evidence_manifest": "admission/manifest.json"}


class Metrics(unittest.TestCase):
    def docs(self):
        return [
            ("one.json", {"prs": [{"number": 1, "findings": [finding("mq1-pass")],
                                     "retest": [{"fingerprint": "mq1-pass", "original_pr": 1,
                                                  "method": "check", "result": "pass", "at": "2026-11-01T00:00:00Z",
                                                  "detail": {"exact_replay": True}}]}]}),
            ("two.json", {"prs": [{"number": 2, "findings": [finding("mq1-pass"), finding("mq1-fail", 2)],
                                     "retest": [{"fingerprint": "mq1-fail", "original_pr": 2,
                                                  "method": "exact-replay", "result": "fail", "at": "2026-11-02T00:00:00Z",
                                                  "detail": {"exact_replay": True}}]}]}),
            ("three.json", {"prs": [{"number": 3, "findings": [finding("mq1-unknown", 3, base=None)]}]}),
        ]

    def test_precision_false_positive_and_dedup(self):
        out = qm.compute_metrics(self.docs())
        self.assertEqual(out["counts"]["finding_records"], 4)
        self.assertEqual(out["counts"]["unique_findings"], 3)
        self.assertEqual(out["counts"]["duplicate_records"], 1)
        self.assertEqual(out["metrics"]["precision"], 0.5)
        self.assertEqual(out["metrics"]["false_positive"], 0.5)
        self.assertEqual(out["counts"]["unknown_denominator"], 1)
        self.assertIn("base_sha", out["unknown_findings"][0]["missing"])

    def test_same_fingerprint_across_runs_and_history_is_one_finding(self):
        fp = "mq1-history"
        docs = []
        for path, pr, run_id in (("run-1.json", 10, "qa-run-1"),
                                 ("history-1.json", 10, "qa-run-2"),
                                 ("run-2.json", 11, "qa-run-3")):
            f = finding(fp)
            f["run_id"] = run_id
            docs.append((path, {"prs": [{"number": pr, "findings": [f], "retest": [
                {"fingerprint": fp, "original_pr": pr, "method": "check", "result": "pass",
                 "at": run_id, "detail": {"exact_replay": True}}
            ]}]}))
        out = qm.compute_metrics(docs)
        self.assertEqual(out["counts"]["finding_records"], 3)
        self.assertEqual(out["counts"]["unique_findings"], 1)
        self.assertEqual(out["counts"]["duplicate_records"], 2)

    def test_missing_environment_is_unknown(self):
        doc = {"prs": [{"number": 9, "findings": [{"fingerprint": "mq1-no-env", "tested_sha": SHA_A,
                                                        "base_sha": SHA_B, "run_id": "r", "artifact_refs": ["a"]}],
                        "retest": [{"fingerprint": "mq1-no-env", "original_pr": 9, "method": "check", "result": "pass"}]}]}
        out = qm.compute_metrics([("x.json", doc)])
        self.assertIsNone(out["metrics"]["precision"])
        self.assertEqual(out["counts"]["unknown"], 1)
        self.assertIn("environment", out["unknown_findings"][0]["missing"])

    def test_load_reports_accepts_jsonl_and_ignores_unrelated_json(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "reports.jsonl"), "w") as f:
                f.write(json.dumps(self.docs()[0][1]) + "\n")
            with open(os.path.join(d, "unrelated.json"), "w") as f:
                json.dump({"hello": "world"}, f)
            loaded = qm.load_reports([d])
            self.assertEqual(len(loaded), 1)
            self.assertEqual(qm.compute_metrics(loaded)["counts"]["exact_replays"], 1)

    def test_regression_escape_reads_real_opsfix_writeback_detail(self):
        fp = "mq1-regression-e2e"
        entry = OR.report_entries([dict(fix_pr=20, original_pr=19, fingerprint=fp, source="check", result="fail",
                                        reason="related regression", evidence="e.json", at="2026-11-03T00:00:00Z",
                                        detail={"exact_replay": True, "rows": [
                                            {"check": "related", "original": "pass", "fix": "fail", "verdict": "fail"}]})], 20)[0]
        out = qm.compute_metrics([{"prs": [{"number": 19, "findings": [finding(fp, 19)], "retest": [entry]}]}])
        self.assertEqual(out["metrics"]["regression_escape"], 1.0)


if __name__ == "__main__":
    unittest.main()
