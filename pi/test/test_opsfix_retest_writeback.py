#!/usr/bin/env python3
"""opsfix retest 11-05 (retest-1104 analysis): (1) blocked / gave_up results are written back to reports/merged/PR-<fix>.json
retest[] (before: only pass/fail, opsfix_retest.py write-back filter); (2) a fix PR whose source report has nothing
pick_targets can retest (#1742 ← #1490: findings=[], only pending_verification, body names no fingerprint) becomes a
blocked no-target result + watchdog alert instead of being silently dropped. All mocks, temp dirs removed."""
import os
for _k in [k for k in os.environ if k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))]: del os.environ[_k]
import sys, json, shutil, tempfile, types, unittest, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.environ.get("PI_BIN") or os.path.join(os.path.dirname(HERE), "bin"))
import opsfix_retest as R  # noqa: E402
import watchdog            # noqa: E402
import pm_extra            # noqa: E402

CST = dt.timezone(dt.timedelta(hours=8))
STAMP = dt.datetime(2026, 11, 5, 9, 30, tzinfo=CST)
PR1490 = {"findings": [], "failed_checks": [{"check_id": "e2e-browser"}], "pending_verification": [
    {"source": "code-review", "fingerprint": "mq1-c928c928c928c928c928"},
    {"source": "code-review", "fingerprint": "mq1-da5dda5dda5dda5dda5d"},
    {"source": "check", "fingerprint": "mq1-bb18bb18bb18bb18bb18"}]}


class PickTargets(unittest.TestCase):
    def test_1742_case_pick_is_empty_but_targets_for_returns_no_target(self):
        self.assertEqual(R.pick_targets("coverage", 1490, PR1490), [])
        old = R.load_pr
        try:
            R.load_pr = lambda n: PR1490 if n == 1490 else None
            tg = R.targets_for({"number": 1742, "body": "QA: #1490 coverage", "title": "fix"})
        finally: R.load_pr = old
        self.assertEqual(len(tg), 1); o, f = tg[0]
        self.assertEqual(o, 1490); self.assertTrue(f["no_target"]); self.assertEqual(f["source"], "no-target")
        self.assertIn(R.NO_TARGET, f["why"]); self.assertIn("e2e-browser", f["why"])

    def test_missing_report_still_none_and_picked_targets_unchanged(self):
        old = R.load_pr
        try:
            R.load_pr = lambda n: None
            self.assertEqual(R.targets_for({"number": 2, "body": "QA: #1"}), [(1, None)])
            rep = {"findings": [{"source": "code-review", "severity": "P1", "fingerprint": "mq1-aaaaaaaaaaaaaaaaaaaa"}]}
            R.load_pr = lambda n: rep
            tg = R.targets_for({"number": 2, "body": "QA: #1"})
            self.assertEqual([f["fingerprint"] for _, f in tg], ["mq1-aaaaaaaaaaaaaaaaaaaa"])
        finally: R.load_pr = old

    def test_no_target_is_permanent_so_settle_marks_done(self):
        st, done, retry = R.settle({}, {1742: [{"result": "blocked", "reason": R.no_target(PR1490)["why"]}]}, {1742: "h"}, "t")
        self.assertEqual(done, [1742]); self.assertNotIn("gave_up_after", st["done"]["1742"])


class Entries(unittest.TestCase):
    def rs(self):
        return [dict(fix_pr=1725, original_pr=1577, fingerprint="mq1-03fd", source="code-review", result="blocked", reason="x（模型判 unsure）", evidence="e.json", at="t"),
                dict(fix_pr=1710, original_pr=1597, fingerprint="mq1-cfac", source="check", result="blocked", reason="check 类问题没有轻量复跑", evidence="e2", at="t"),
                dict(fix_pr=1742, original_pr=1490, fingerprint=None, source="no-target", result="blocked", reason=R.NO_TARGET, evidence=None, at="t"),
                dict(fix_pr=1, original_pr=2, fingerprint="mq1-p", source="code-review", result="pass", reason="ok", evidence="e3", at="t",
                     target_source="pending_verification"),
                dict(fix_pr=9, original_pr=8, fingerprint=None, source=None, result="blocked", reason="原 PR #8 没有合并报告", evidence=None, at="t")]

    def test_blocked_gave_up_permanent_and_pass_written(self):
        done = {"1725": {"gave_up_after": 3}}
        e = R.report_entries(self.rs(), 1725, done)[0]
        self.assertEqual((e["result"], e["outcome"], e["attempts"], e["method"]), ("blocked", "gave_up", 3, "code-review"))
        e = R.report_entries(self.rs(), 1710, {})[0]; self.assertEqual(e["outcome"], "blocked_permanent")
        e = R.report_entries(self.rs(), 1742, {})[0]; self.assertEqual((e["method"], e["fingerprint"]), ("no-target", None))
        e = R.report_entries(self.rs(), 1, {})[0]
        self.assertEqual(e["result"], "pass"); self.assertNotIn("outcome", e); self.assertEqual(e["target_source"], "pending_verification")
        self.assertEqual(R.report_entries(self.rs(), 9, {}), [])   # no report at all: nothing to attach to

    def test_transient_blocked_outcome(self):
        r = [dict(fix_pr=5, original_pr=4, fingerprint="mq1-x", source="code-review", result="blocked", reason="本轮模型调用已达上限 12", evidence="e", at="t")]
        self.assertEqual(R.report_entries(r, 5, {})[0]["outcome"], "blocked")

    def test_later_pass_replaces_blocked(self):
        doc = {"prs": [{"number": 7, "retest": [{"fingerprint": "mq1-x", "original_pr": 6, "result": "blocked"}]}]}
        R.apply_report(doc, 7, [{"fingerprint": "mq1-x", "original_pr": 6, "result": "pass"}])
        self.assertEqual([x["result"] for x in doc["prs"][0]["retest"]], ["pass"])


class Alert(unittest.TestCase):
    def setUp(self): self.d = tempfile.mkdtemp(prefix="retest-alert-test-")
    def tearDown(self): shutil.rmtree(self.d, ignore_errors=True)

    def test_alert_written_kept_then_expires_and_watchdog_item(self):
        p = os.path.join(self.d, "a.json")
        r = [dict(fix_pr=1742, original_pr=1490, source="no-target", reason=R.NO_TARGET + "：x")]
        R.write_no_target_alert(r, STAMP, path=p)
        a = json.load(open(p)); self.assertIn("1742<-1490", a["items"])
        R.write_no_target_alert([], STAMP + dt.timedelta(hours=1), path=p); self.assertTrue(os.path.exists(p))
        probs = {}; watchdog.retest_no_target_check(None, probs, path=p)
        self.assertEqual(list(probs), ["retest:no-target"]); self.assertIn("1742", json.dumps(probs, ensure_ascii=False))
        R.write_no_target_alert([], STAMP + dt.timedelta(hours=80), path=p); self.assertFalse(os.path.exists(p))
        probs = {}; watchdog.retest_no_target_check(None, probs, path=p); self.assertEqual(probs, {})


class RunWriteBack(unittest.TestCase):
    """run(--execute) with every runner / gh / lock mocked: blocked + no-target land in the fix PR's report."""
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="retest-run-test-"); d = self.d
        self.saved = {k: getattr(R, k) for k in ("RPT", "BASE", "STATE", "QA_LOCK", "WORK", "NO_TARGET_ALERT", "merged_since", "mem_mb",
                      "take_lock", "run_review", "ledger_path", "record_unmatched", "now_cst", "log")}
        R.RPT = f"{d}/rpt"; R.BASE = d; R.STATE = f"{d}/state.json"; R.QA_LOCK = f"{d}/lock"; R.WORK = f"{d}/wt"
        R.NO_TARGET_ALERT = f"{d}/alert.json"; os.makedirs(R.RPT)
        rep = lambda n, **x: json.dump({"prs": [dict(number=n, **x)]}, open(f"{R.RPT}/PR-{n}.json", "w"))
        rep(1490, **PR1490); rep(1742)
        rep(1577, findings=[{"source": "code-review", "severity": "P1", "fingerprint": "mq1-03fdfd7d94b8adf1f48e", "title": "t"}]); rep(1725)
        json.dump({"watermark": "2026-11-04T09:00:00+08:00", "done": {}, "retry": {"1725": {"attempts": 2}},
                   "ref_backfill": R.REF_BACKFILL[0]}, open(R.STATE, "w"))
        prs = [{"number": 1742, "body": "QA: #1490 coverage", "title": "f", "mergedAt": "2026-11-04T12:23:31Z", "headRefOid": "3" * 40},
               {"number": 1725, "body": "QA: #1577", "title": "f", "mergedAt": "2026-11-04T05:05:36Z", "headRefOid": "6" * 40}]
        R.merged_since = lambda lo: prs; R.mem_mb = lambda: 9000; R.take_lock = lambda lk, w: True
        R.run_review = lambda *a: ("blocked", "x（模型判 unsure）", {})
        R.ledger_path = lambda rejected=None: (_ for _ in ()).throw(R.LedgerError("test: no ledger"))
        R.record_unmatched = lambda *a, **k: []; R.now_cst = lambda: STAMP; R.log = lambda *a: None

    def tearDown(self):
        for k, v in self.saved.items(): setattr(R, k, v)
        shutil.rmtree(self.d, ignore_errors=True)

    def test_run_writes_blocked_and_gave_up_and_alerts(self):
        a = types.SimpleNamespace(pr=None, day=None, since=None, execute=True, max_model=12, wait_lock=1, keep=True, no_handoff=True)
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()): R.run(a)
        rt = json.load(open(f"{R.RPT}/PR-1725.json"))["prs"][0]["retest"]
        self.assertEqual([(x["result"], x["outcome"], x.get("attempts")) for x in rt], [("blocked", "gave_up", 3)])
        rt = json.load(open(f"{R.RPT}/PR-1742.json"))["prs"][0]["retest"]
        self.assertEqual([(x["result"], x["method"], x["original_pr"]) for x in rt], [("blocked", "no-target", 1490)])
        st = json.load(open(R.STATE)); self.assertIn("1742", st["done"]); self.assertEqual(st["done"]["1725"]["gave_up_after"], 3)
        self.assertIn("1742<-1490", json.load(open(R.NO_TARGET_ALERT))["items"])


class PmExtraCount(unittest.TestCase):
    def test_blocked_only_retest_is_not_counted_as_retested(self):
        d = tempfile.mkdtemp(prefix="retest-pm-test-")
        try:
            os.makedirs(f"{d}/rpt"); os.makedirs(f"{d}/qa/ledger/daily")
            json.dump({"prs": [{"number": 5, "retest": [{"result": "blocked"}]}]}, open(f"{d}/rpt/PR-5.json", "w"))
            json.dump({"prs": [{"number": 6, "retest": [{"result": "blocked"}, {"result": "pass"}]}]}, open(f"{d}/rpt/PR-6.json", "w"))
            old = (pm_extra.RPT, pm_extra.QA); pm_extra.RPT, pm_extra.QA = f"{d}/rpt", f"{d}/qa"
            try:
                miss = []; L = pm_extra.retest("2026-11-04", [{"number": 5, "body": "QA: #1"}, {"number": 6, "body": "QA: #2"}], miss)
            finally: pm_extra.RPT, pm_extra.QA = old
            self.assertTrue(any("1/2" in m for m in miss), miss)
        finally: shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
