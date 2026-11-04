#!/usr/bin/env python3
"""stdcheck qa_start (11-05): a failed first QA run followed by a rerun is measured from the FIRST run's start, and counted in the
first_run_fail metric (count only: no gap, no alert). Fixtures modelled on PR-1740 (report keeps run.first_started_at) and PR-1788
(an old report without it, the first launch only in the mergewatch log). Mocks only, temp dirs (Base from test_stdcheck)."""
import json, os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from test_stdcheck import Base, S, dt, CST  # noqa: E402

NOW = dt.datetime(2026, 11, 5, 3, 30, tzinfo=CST)
Z = lambda t: t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
T = lambda h, m, s=0: dt.datetime(2026, 11, 5, h, m, s, tzinfo=CST)


class FirstRun(Base):
    def rep(self, n, merged, started, first=None, attempt=None, base="main"):
        run = {"started_at": Z(started)}
        if first: run["first_started_at"] = Z(first)
        if attempt: run["attempt"] = attempt
        json.dump({"run": run, "prs": [{"number": n, "merged_at": Z(merged), "base_ref": base, "test_started_at": Z(started)}]},
                  open(f"{self.tmp}/reports/PR-{n}.json", "w"))

    def log(self, text): open(f"{self.tmp}/out/logs/mergewatch-2026-11-05.log", "a").write(text)

    def pr1740(self):   # merged 01:53:22, first run 01:54:06 (exit 3), rerun 02:27:03 overwrote the report
        self.rep(1740, T(1, 53, 22), T(2, 27, 3), first=T(1, 54, 6), attempt=2, base="integration/canvas-s2")

    def pr1788(self):   # merged 02:31:40, first run died (ENOSPC) before any report; rerun 02:44:03 wrote it (old format)
        self.rep(1788, T(2, 31, 40), T(2, 44, 3))
        self.log("2026-11-05 02:32:04 CST launched priority run pid 3798080 for PR #1788 (main 09e90e86, try 1/3)\n"
                 "2026-11-05 02:44:01 CST launched priority run pid 4026005 for PR #1788 (main 677759f8, try 2/3)\n")

    def test_1740_rerun_report_measured_from_first_start(self):
        self.pr1740(); self.assertEqual(S.check_qa_start(NOW), {})   # main: 33 min breach (rerun start)

    def test_1788_first_launch_from_mergewatch_when_report_lacks_it(self):
        self.pr1788(); self.assertEqual(S.check_qa_start(NOW), {})   # main: 12 min breach

    def test_old_report_falls_back_to_started_at(self):
        self.rep(1718, T(1, 0), T(1, 11, 8)); self.assertEqual(list(S.check_qa_start(NOW)), ["PR-1718"])   # real queue wait still breaches

    def test_first_run_fail_metric(self):
        self.pr1740(); self.pr1788(); self.rep(1718, T(1, 0), T(1, 11, 8))
        self.assertEqual(S.first_run_fail(NOW), {"merges": 3, "failed": 2, "rate": 0.667, "prs": ["PR-1740", "PR-1788"]})

    def test_first_run_fail_is_reported_not_alerted(self):
        self.pr1740(); self.pr1788()
        for i in range(3): self.at(NOW + dt.timedelta(hours=i))
        pr = json.load(open(f"{S.OUT}/passrate-{NOW.date()}.json"))
        self.assertEqual(pr["metrics"]["first_run_fail"]["failed"], 2)
        self.assertEqual(json.load(open(f"{S.OUT}/state.json"))["metrics"]["first_run_fail"]["prs"], ["PR-1740", "PR-1788"])
        self.assertEqual(pr["checks"]["qa_start"]["fail"], 0)
        self.assertFalse(os.path.exists(f"{S.OUT}/alerts/first_run_fail.json")); self.assertEqual(self.open_ids(), [])
        self.assertNotIn("first_run_fail", S.CHECKS)


if __name__ == "__main__": unittest.main()
