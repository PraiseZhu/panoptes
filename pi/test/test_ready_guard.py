#!/usr/bin/env python3
"""ready_guard（10-29，mivo #1351）：Pi 转 ready 的两条闸，标准不变（除 review-pr 外全绿即转）。
 (a) 有人转回过草稿（时间线 convert_to_draft，不在 Pi 台账里）→ 永不再转，换 head 也不转；
 (b) 同一 head 上 review-pr 已失败 → 不转；
 正常：review-pr 没跑 / 在跑，其余全绿 → 照常转。覆盖 prwatch.check_drafts、outbound.h_ready、prretro 用到的 block_reason。
Local only: no network, no gh. Run: python3 test/test_ready_guard.py"""
import json, os, shutil, sys, tempfile, types, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="ready-guard-test-"); os.environ["PI_STATE"] = TMP
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
sys.modules.setdefault("merger", types.SimpleNamespace(FIELDS="number", blockers=lambda p: []))   # CI 上没有 mivo-merger（同 test_outbound_resend）
import ready_guard as RG, prwatch, outbound  # noqa: E402

GREEN = [{"name": "verify", "status": "COMPLETED", "conclusion": "SUCCESS"}, {"name": "unit", "status": "COMPLETED", "conclusion": "SUCCESS"}]
RP = lambda v, s="COMPLETED": {"name": "review-pr", "status": s, "conclusion": v}
DRAFTED = [{"event": "ready_for_review", "created_at": "2026-10-28T10:00:00Z"}, {"event": "convert_to_draft", "created_at": "2026-10-28T10:30:00Z"}]

def fake_gh(prs, events, views=None):
    def gh(args, js=True):
        if args[:2] == ["pr", "list"]: return prs
        if args[0] == "api" and "/timeline" in args[1]:
            return json.dumps(events) if not js else events
        if args[:2] == ["pr", "view"]: return views
        raise AssertionError(args)
    return gh

def pr(n=1351, head="a" * 40, checks=None):
    return {"number": n, "headRefOid": head, "headRefName": "qa-fix/x", "body": f"QA: #1385\n{prwatch.PILINE} x -->",
            "statusCheckRollup": GREEN if checks is None else checks, "mergeStateStatus": "CLEAN"}

class Unit(unittest.TestCase):
    def test_review_pr_failed_only_on_failure(self):
        self.assertFalse(RG.review_pr_failed(GREEN))
        self.assertFalse(RG.review_pr_failed(GREEN + [RP("", "IN_PROGRESS")]))
        self.assertFalse(RG.review_pr_failed(GREEN + [RP("SUCCESS")]))
        self.assertTrue(RG.review_pr_failed(GREEN + [RP("FAILURE")]))
        self.assertTrue(RG.review_pr_failed([{"name": "review-pr", "bucket": "fail"}]))        # gh pr checks 形式（prretro）
        self.assertFalse(RG.review_pr_failed([{"name": "review-pr", "bucket": "pending"}]))
    def test_convert_to_draft_recorded_by_pi_is_not_human(self):
        led = {"draft:1351:abc": {"status": "done", "at": "2026-10-28T18:31:00+08:00"}}       # 10:31Z
        self.assertEqual(RG.human_draft_events(1351, DRAFTED, led), [])
        self.assertEqual(RG.human_draft_events(1351, DRAFTED, {}), ["2026-10-28T10:30:00Z"])
        self.assertEqual(RG.human_draft_events(1351, DRAFTED, {"draft:1352": {"at": "2026-10-28T18:31:00+08:00"}}), ["2026-10-28T10:30:00Z"])
        # 后来又 ready 过也一样：一次人工转草稿就永久不再转
        self.assertTrue(RG.human_draft_events(1351, DRAFTED + [{"event": "ready_for_review", "created_at": "2026-10-28T11:00:00Z"}], {}))
    def test_timeline_unreadable_fails_closed(self):
        def bad(args, js=True): raise RuntimeError("HTTP 502")
        self.assertIn("timeline unreadable", RG.block_reason(bad, "r", 1, GREEN, {}))
    def test_paginated_jq_output(self):
        gh = lambda args, js=True: '[{"event":"ready_for_review","created_at":"2026-10-28T10:00:00Z"}]\n[{"event":"convert_to_draft","created_at":"2026-10-28T10:30:00Z"}]\n'
        self.assertIn("converted back to draft", RG.block_reason(gh, "r", 1351, GREEN, {}))

class Prwatch(unittest.TestCase):
    def setUp(self):
        self.saved = {k: getattr(prwatch, k) for k in ("gh", "act", "qlog", "kick_janitor", "BASE", "jload")}
        self.acts, self.logs = [], []
        prwatch.act = lambda job, a: self.acts.append(a); prwatch.qlog = self.logs.append; prwatch.kick_janitor = lambda *a: None
        prwatch.BASE = TMP
    def tearDown(self):
        for k, v in self.saved.items(): setattr(prwatch, k, v)
    def run_once(self, st, prs, events):
        prwatch.gh = fake_gh(prs, events); st["drafts_at"] = 0; prwatch.check_drafts(st)
        return [a for a in self.acts if a["type"] == "gh.pr.ready"]
    def test_normal_review_pr_absent_or_pending_readies(self):
        self.assertEqual(len(self.run_once({}, [pr()], [])), 1)
        self.acts.clear()
        self.assertEqual(len(self.run_once({}, [pr(checks=GREEN + [RP("", "IN_PROGRESS")])], [])), 1)
    def test_b_review_pr_failed_same_head_no_ready(self):
        self.assertEqual(self.run_once({}, [pr(checks=GREEN + [RP("FAILURE")])], []), [])
        self.assertTrue(any("review-pr failed" in l for l in self.logs))
    def test_a_human_draft_no_ready_even_after_new_head(self):
        st = {}
        self.assertEqual(self.run_once(st, [pr()], DRAFTED), [])
        self.assertIn("1351", st["no_ready"])
        # 新 head、timeline 这次甚至读不到之前的事件：按 PR 记下的决定仍然生效
        self.assertEqual(self.run_once(st, [pr(head="b" * 40)], []), [])
        self.assertEqual(self.run_once(st, [pr(head="c" * 40, checks=GREEN + [RP("SUCCESS")])], DRAFTED), [])

class Outbound(unittest.TestCase):
    def setUp(self): self.saved = outbound.gh
    def tearDown(self): outbound.gh = self.saved
    def state(self, checks, events, head="a" * 40):
        outbound.gh = fake_gh([], events, {"isDraft": True, "state": "OPEN", "headRefOid": head, "statusCheckRollup": checks})
        return outbound.h_ready({"pr": 1351, "head": "a" * 40})[1]()
    def test_exec_time_recheck(self):
        self.assertEqual(self.state(GREEN, []), "pending")
        self.assertEqual(self.state(GREEN + [RP("", "QUEUED")], []), "pending")
        self.assertTrue(self.state(GREEN + [RP("FAILURE")], []).startswith("stale:review-pr failed"))
        self.assertTrue(self.state(GREEN, DRAFTED).startswith("stale:converted back to draft"))

if __name__ == "__main__":
    try: unittest.main(verbosity=1)
    finally: shutil.rmtree(TMP, ignore_errors=True)
