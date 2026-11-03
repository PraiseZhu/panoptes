#!/usr/bin/env python3
"""Handoff backfill (2026-11-03, pi-work/handoff-backfill/DESIGN.md): a report written after its data day's daily run never got
its handoff issues. `handoff.py backfill` lists them for review only. Also: handoff.py plan now keys a finding like daily.py
(handoff:<PR>:<pr_fingerprint>), and p2-rulings.md rows may carry a fingerprint (per-finding ruling). Fictional data; gh mocked."""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import os, sys, json, glob, shutil, tempfile, unittest
B = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
import lib, handoff as H, finding_policy as FP
from handoff_action import handoff_issue_action

REPO = "test-org/test-handoff"
def finding(i, **kw):
    return {"severity": "P2", "source": "code-review", "verdict": "CONFIRMED", "verification": {"status": "confirmed"},
            "fingerprint": f"mq1-test000000000000000{i}", "pr_fingerprint": f"mq1-test000000000000000{i}-pr9001",
            "title": f"问题 {i}", "file": "src/a.ts", "line": i, **kw}

class Backfill(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.qa = f"{self.d}/qa"; self.st = f"{self.d}/state"
        os.makedirs(f"{self.qa}/reports/merged"); os.makedirs(f"{self.st}/out/daily"); os.makedirs(f"{self.st}/out/outbound")
        self.saved = (lib.RPT, H.RPT, H.QA, H.BASE, H.RULINGS, H.ROUTES, H.gh)
        lib.RPT = H.RPT = f"{self.qa}/reports/merged"; H.QA = self.qa; H.BASE = self.st
        H.RULINGS = f"{self.d}/p2-rulings.md"; open(H.RULINGS, "w").write("")
        H.ROUTES = f"{self.d}/routes.json"
        json.dump({"routes": [{"repo": REPO, "owner": "test-author", "authors": ["test-author"], "cc": {}}]}, open(H.ROUTES, "w"))
        self.issues = []; self.gh_fail = False; H.gh = self.fake_gh
        # data day 2026-10-28 (merged 10-28 17:07 Beijing); its daily ran 10-29 07:35 Beijing (outbound ledger)
        json.dump({"mivo-daily-report:2026-10-28": {"status": "done", "at": "2026-10-29T07:35:03+08:00"}}, open(f"{self.st}/out/outbound/ledger.json", "w"))
        self.report(9001, [finding(1)])
    def tearDown(self):
        lib.RPT, H.RPT, H.QA, H.BASE, H.RULINGS, H.ROUTES, H.gh = self.saved; shutil.rmtree(self.d, ignore_errors=True)
    def fake_gh(self, args, js=True):
        if args[:2] == ["issue", "list"]:
            if self.gh_fail: raise RuntimeError("gh: HTTP 502")
            return self.issues
        raise AssertionError(f"backfill must only read issues: {args}")
    def report(self, n, findings, gen="2026-10-29T05:54:42Z", base="main", merged="2026-10-28T09:07:42Z"):
        json.dump({"generated_at": gen, "prs": [{"number": n, "author": "test-author", "merged_at": merged, "base_ref": base,
                   "tested_sha": "abc123", "merge_sha": "abc123", "findings": findings}]}, open(f"{self.qa}/reports/merged/PR-{n}.json", "w"))
    def rows(self, **kw): return {r["key"]: r for r in H.backfill(**kw)}
    K1 = "handoff:9001:mq1-test0000000000000001-pr9001"

    def test_late_report_would_open_with_the_daily_action(self):
        r = self.rows()[self.K1]
        self.assertEqual(r["status"], "would-open"); self.assertEqual(r["repo"], REPO)
        want = handoff_issue_action("2026-10-28", {"n": 9001, "tested_sha": "abc123", "merge_sha": "abc123"}, finding(1))
        self.assertEqual(r["action"]["key"], want["key"]); self.assertEqual(r["action"]["text"], want["text"])
        self.assertEqual(r["action"]["handoff_key"], "mq1-test0000000000000001-pr9001")
    def test_report_before_daily_run_is_no_candidate(self):
        self.report(9001, [finding(1)], gen="2026-10-28T20:00:00Z")   # 10-29 04:00 Beijing < 07:35
        self.assertEqual(self.rows(), {})
    def test_daily_listed_fault_is_candidate_even_if_report_earlier(self):
        self.report(9001, [finding(1)], gen="2026-10-28T20:00:00Z")
        json.dump({"fault": [9001]}, open(f"{self.st}/out/daily/2026-10-28.json", "w"))
        os.utime(f"{self.st}/out/daily/2026-10-28.json", (1793230503, 1793230503))
        self.assertEqual(self.rows()[self.K1]["status"], "would-open")
    def test_day_without_any_daily_record_is_candidate(self):
        os.remove(f"{self.st}/out/outbound/ledger.json"); self.report(9001, [finding(1)], gen="2026-10-28T10:00:00Z")
        self.assertEqual(self.rows()[self.K1]["status"], "would-open")
    def test_non_main_base_is_out_of_scope(self):
        self.report(9001, [finding(1)], base="release/x")
        self.assertEqual([r["status"] for r in H.backfill()], ["out_of_scope(non-main)"])
    def test_rulings_apply_per_finding(self):
        self.report(9001, [finding(1), finding(2), finding(3), finding(4)])
        open(H.RULINGS, "w").write("| 日期 | 报告 PR | 指纹 | 问题 | 裁决 | 理由 |\n|---|---|---|---|---|---|\n"
                                   "| 11-03 | #9001 | mq1-test0000000000000002-pr9001 | 问题 2 | 误报 | 虚构理由 |\n"
                                   "| 11-03 | #9001 | mq1-test0000000000000003-pr9001 | 问题 3 | 已修 | 虚构理由 |\n")
        os.makedirs(f"{self.st}/out/p2")
        json.dump({"mq1-test0000000000000004-pr9001": {"ruling": "修", "asked_at": "2026-10-30T18:21+08:00"}}, open(f"{self.st}/out/p2/state.json", "w"))
        st = {k[-27:-7]: r["status"] for k, r in self.rows().items()}
        self.assertEqual(st, {"test0000000000000001": "would-open", "test0000000000000002": "skipped(no_fix)",
                              "test0000000000000003": "skipped(fixed)", "test0000000000000004": "待人工确认"})
    def test_legacy_pr_wide_ruling_skips_every_finding(self):
        open(H.RULINGS, "w").write("| 10-23 | #9001 | P2 | 不修 | 虚构理由 |\n")
        r = self.rows()[self.K1]; self.assertEqual(r["status"], "skipped(no_fix)"); self.assertIn("legacy", r["why"])
    def test_shadow_without_pr_fingerprint_uses_title_key(self):
        self.report(9001, [finding(1, pr_fingerprint=None)])
        os.makedirs(f"{self.st}/out/p2"); json.dump({"9001:问题 1": {"ruling": "不修", "asked_at": "x"}}, open(f"{self.st}/out/p2/state.json", "w"))
        self.assertEqual(self.rows()[self.K1]["status"], "待人工确认")
    def test_known_key_is_already_open(self):
        self.issues = [{"number": 3, "url": f"https://github.com/{REPO}/issues/3", "body": "<!-- mivo-qa-handoff: key=mq1-test0000000000000001-pr9001 pr=9001 -->"}]
        r = self.rows()[self.K1]; self.assertEqual(r["status"], "已开"); self.assertIn("/issues/3", r["why"])
    def test_gh_failure_is_unverified_not_would_open(self):
        self.gh_fail = True; self.assertEqual(self.rows()[self.K1]["status"], "未核实")
    def test_rerun_is_byte_identical_and_records_no_actions(self):
        H.backfill(); out = glob.glob(f"{self.st}/out/handoff/backfill-*"); a = {f: open(f, "rb").read() for f in out}
        H.backfill(); self.assertEqual(a, {f: open(f, "rb").read() for f in out}); self.assertEqual(len(out), 2)
        self.assertEqual(glob.glob(f"{self.st}/out/**/*.actions.json", recursive=True), [])
    def test_off_flag_writes_nothing(self):
        open(f"{self.st}/handoff-backfill-off", "w").close()
        self.assertIsNone(H.backfill()); self.assertFalse(os.path.exists(f"{self.st}/out/handoff"))

class PlanKey(unittest.TestCase):
    def test_plan_action_key_matches_daily(self):
        H_route = {"repo": REPO}; acts = []
        r = {"pr": {"number": 9001, "tested_sha": "abc", "findings": [finding(1)]}}
        H._plan_one(9001, r, H_route, {}, {}, [], [], acts, "2026-10-28", "test-author")
        want = handoff_issue_action("2026-10-28", {"n": 9001, "tested_sha": "abc"}, finding(1))
        self.assertEqual([a["key"] for a in acts], [want["key"]]); self.assertEqual(acts[0]["handoff_key"], want["handoff_key"])

class Rulings(unittest.TestCase):
    T = ("| 10-23 | #948 | P2 | 已修 | x |\n| 10-23 | #1124 | 文档 | 修 | x |\n| 11-03 | #1456 | 用例失败 | 不修 | 误报 | e | Jasper | Jasper 已裁 |\n"
         "| 11-03 | #9001 | mq1-test0000000000000002-pr9001 | 问题 2 | 误报 | x |\n")
    def test_pr_sets_keep_old_rows_and_read_fingerprint_rows(self):
        self.assertEqual(FP.no_fix_rulings(self.T), {948, 1456, (9001, "mq1-test0000000000000002")})   # B-fp: a 指纹 row covers that finding only
        self.assertEqual(FP.fix_rulings(self.T), {1124})
    def test_no_fix_findings(self):
        self.assertEqual(FP.no_fix_findings(self.T), {"by_fp": {"mq1-test0000000000000002-pr9001": "no_fix"}, "pr_wide": {948: "fixed", 1456: "no_fix"}})

if __name__ == "__main__": unittest.main()
