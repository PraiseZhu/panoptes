#!/usr/bin/env python3
"""retest_runner (fail->pass evidence) + review_concurrency (daily review peak vs configured maxParallel). Mocks only:
worktree / sh / gh are injected, nothing runs git, node or the network; no files written."""
import os
for _k in [k for k in os.environ if k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))]: del os.environ[_k]
import sys, unittest, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.environ.get("PI_BIN") or os.path.join(os.path.dirname(HERE), "bin"))
import retest_runner as RR       # noqa: E402
import review_concurrency as RC  # noqa: E402


class Cases(unittest.TestCase):
    def test_cases_from_report(self):
        pr = {"failed_checks": [
            {"check_id": "unit", "test": "src/a.test.ts > keeps x", "fingerprint": "mq1-1"},
            {"check_id": "e2e-browser", "test": None, "fingerprint": "mq1-2", "evidence": {"command": "node scripts/e2e/run.mjs --layer browser"}},
            {"check_id": "bench", "test": "nodeIdsInRect p95", "fingerprint": "mq1-3"}]}
        cs = RR.cases_from_report(pr)
        self.assertEqual([(c["file"], c["name"], c["command"]) for c in cs],
                         [("src/a.test.ts", "keeps x", None), (None, None, "node scripts/e2e/run.mjs --layer browser"), (None, "nodeIdsInRect p95", None)])
        self.assertEqual([c["id"] for c in RR.cases_from_report(pr, {"mq1-2"})], ["e2e-browser"])

    def test_case_cmds_and_related(self):
        cmds = RR.case_cmds([RR.parse_case("src/a.test.ts::keeps x"), {"id": "e", "command": "echo hi"}, {"id": "empty"}])
        self.assertEqual(cmds[0], ("case:src/a.test.ts::keeps x", RR.VITEST + ["run", "src/a.test.ts", "-t", "keeps x"]))
        self.assertEqual(cmds[1], ("case:e", ["bash", "-lc", "echo hi"])); self.assertEqual(len(cmds), 2)
        self.assertEqual(RR.related_cmd(["src/a.ts", "README.md"]), ("related", RR.VITEST + ["related", "--run", "src/a.ts", "--passWithNoTests"]))
        self.assertIsNone(RR.related_cmd(["docs/x.md"]))

    def test_classify_verdict(self):
        self.assertEqual(RR.classify(0), "pass"); self.assertEqual(RR.classify(None), "error")
        self.assertEqual(RR.classify(1, " FAIL  src/a.test.ts"), "fail"); self.assertEqual(RR.classify(1, "No test files found"), "error")
        self.assertEqual(RR.verdict("fail", "pass"), "fail->pass"); self.assertEqual(RR.verdict("pass", "pass"), "not-reproduced")
        self.assertEqual(RR.verdict("pass", "fail"), "still-fails"); self.assertEqual(RR.verdict("error", "pass"), "inconclusive")


class Run(unittest.TestCase):
    def go(self, table, cases=None, changed=("src/a.ts",)):
        dropped = []
        def sh(argv, cwd): return table[(cwd, argv[2] if argv[:2] == RR.VITEST else "cmd")]
        ev = RR.run("o" * 40, "f" * 40, cases or [RR.parse_case("src/a.test.ts")], list(changed), lambda s: f"wt-{s[0]}", sh, dropped.append)
        self.assertEqual(dropped, ["wt-o", "wt-f"]); return ev

    def test_fail_to_pass_with_related_green(self):
        ev = self.go({("wt-o", "run"): (1, " FAIL  src/a.test.ts"), ("wt-f", "run"): (0, ""), ("wt-o", "related"): (1, "x"), ("wt-f", "related"): (0, "")})
        self.assertEqual(ev["result"], "pass"); self.assertEqual(ev["rows"][0]["verdict"], "fail->pass")
        self.assertEqual(ev["rows"][1], {"check": "related", "original": "fail", "fix": "pass", "verdict": "pass"})
        md = RR.summary(ev); self.assertIn("fail->pass", md); self.assertIn("| related |", md)

    def test_still_fails_and_related_regression(self):
        ev = self.go({("wt-o", "run"): (1, " FAIL "), ("wt-f", "run"): (1, " FAIL "), ("wt-o", "related"): (0, ""), ("wt-f", "related"): (0, "")})
        self.assertEqual(ev["result"], "fail")
        ev = self.go({("wt-o", "run"): (1, " FAIL "), ("wt-f", "run"): (0, ""), ("wt-o", "related"): (0, ""), ("wt-f", "related"): (1, " FAIL ")})
        self.assertEqual(ev["result"], "fail")

    def test_not_reproduced_is_inconclusive_and_no_case_blocked(self):
        ev = self.go({("wt-o", "run"): (0, ""), ("wt-f", "run"): (0, ""), ("wt-o", "related"): (0, ""), ("wt-f", "related"): (0, "")})
        self.assertEqual(ev["result"], "inconclusive")
        ev = self.go({("wt-o", "related"): (0, ""), ("wt-f", "related"): (0, "")}, cases=[{"id": "x"}])
        self.assertEqual(ev["result"], "blocked"); self.assertIn("没有可执行的原用例", RR.summary(ev))


def R(start, completed=None, updated=None): return {"run_started_at": start, "completed_at": completed, "updated_at": updated}

class Concurrency(unittest.TestCase):
    def test_completed_at_not_updated_at(self):
        rs = [R("2026-11-03T06:00:00Z", "2026-11-03T06:10:00Z", "2026-11-03T09:00:00Z"), R("2026-11-03T06:20:00Z", "2026-11-03T06:30:00Z")]
        self.assertEqual(RC.peak(rs)[0], 1)   # with updated_at the first run would overlap the second (old code: 2)
        self.assertEqual(RC.peak([R("2026-11-03T06:00:00Z", None, "2026-11-03T06:30:00Z"), R("2026-11-03T06:20:00Z", "2026-11-03T06:25:00Z")])[0], 2)
        self.assertEqual(RC.peak([R("2026-11-03T06:00:00Z", "2026-11-03T06:10:00Z"), R("2026-11-03T06:10:00Z", "2026-11-03T06:20:00Z")])[0], 1)

    def test_paginates_all(self):
        calls = []
        pages = {1: [R("2026-11-03T06:00:00Z", "2026-11-03T06:01:00Z")] * 100, 2: [R("2026-11-03T07:00:00Z", "2026-11-03T07:01:00Z")] * 30}
        def gh(path):
            calls.append(path); n = int(path.split("&page=")[1].split("&")[0]); return {"total_count": 130, "workflow_runs": pages.get(n, [])}
        runs, total, complete = RC.fetch_all_runs(gh, "o/r", "review-pr.yml", "2026-11-03..2026-11-04")
        self.assertEqual((len(runs), total, complete), (130, 130, True)); self.assertEqual(len(calls), 2)
        self.assertIn("per_page=100&page=2", calls[1])
        runs, total, complete = RC.fetch_all_runs(lambda p: None if "&page=2" in p else {"total_count": 130, "workflow_runs": pages[1]}, "o/r", "w", "c")
        self.assertEqual((len(runs), complete), (100, False))

    def test_compares_configured_max_parallel(self):
        rs = [R(f"2026-11-03T06:0{i}:00Z", "2026-11-03T06:30:00Z") for i in range(6)]
        line, p, lim = RC.concurrency_line(rs, 6, True, {"max_parallel_doc": 3, "max_parallel_configured": 10})
        self.assertEqual((p, lim), (6, 10)); self.assertIn("未超过配置 maxParallel=10", line); self.assertIn("文档默认 3，仅展示", line)
        line, _, lim = RC.concurrency_line(rs, 6, True, {"max_parallel_doc": 3})
        self.assertEqual(lim, 3); self.assertIn("超过文档默认", line)
        line, _, _ = RC.concurrency_line(rs, 130, False, {"max_parallel_configured": 10}); self.assertIn("翻页没取全：取到 6/130", line)


if __name__ == "__main__":
    unittest.main()
