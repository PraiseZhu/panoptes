#!/usr/bin/env python3
"""Unified severity on the Pi line (Praise 2026-10-23) — finding_policy, handoff receipts, daily classify, prfix candidates.
Local only: no network, no model, no writes outside a temp dir. Run: python3 test/test_unified_severity.py"""
import os as _os
_os.environ.setdefault('PRFIX_OWNERS', '*')   # claim/round tests use other authors; owner filter has its own test
for _k in [k for k in _os.environ if k.startswith('GIT_')]: del _os.environ[_k]
import os, sys, unittest, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import finding_policy as P, handoff, daily, prfix

REVIEW = lambda **kw: {"severity": "P1", "source": "code-review", "verdict": "CONFIRMED", "fingerprint": "mq1-" + "a" * 20, "title": "t", "file": "src/a.ts", **kw}

class Policy(unittest.TestCase):
    def test_new_report_findings_and_pending(self):
        self.assertTrue(P.notifiable(REVIEW(verification={"status": "confirmed"}), new_notice=True))
        self.assertFalse(P.notifiable(REVIEW(verification={"status": "undetermined"})))
        self.assertFalse(P.notifiable({"severity": "P1", "pending_kind": "review-plausible", "source": "code-review"}))
        self.assertTrue(P.notifiable({"severity": "P2", "source": "stated-acceptance"}))
    def test_old_report_fallbacks(self):
        self.assertFalse(P.notifiable(REVIEW(verdict="PLAUSIBLE")), "#1191 shape")
        self.assertTrue(P.notifiable(REVIEW()), "an old CONFIRMED P1 that was already sent keeps its follow-ups")
        self.assertFalse(P.notifiable(REVIEW(), new_notice=True), "but a new DM / issue needs the repo-wide verification first")
        self.assertTrue(P.notifiable(REVIEW(severity="P2"), new_notice=True), "a CONFIRMED review P2 needs no verification")
        for f in ({"severity": "P2", "source": "coverage-gate"}, {"severity": "P2", "source": "ai-test"}, {"severity": "P2", "source": "own-tests"},
                  {"severity": "P2", "source": "qa-error"}, {"severity": "P2", "source": "check", "status": "flaky"},
                  {"severity": "P2", "source": "check", "status": "unverified", "test_backed": True}, {"severity": "P2", "source": "check", "status": "timeout"},
                  {"severity": "P3", "source": "check", "status": "introduced"}, {"severity": "P2", "source": "check", "status": "introduced", "pre_existing": True}):
            self.assertFalse(P.notifiable(f), f)
        self.assertTrue(P.notifiable({"severity": "P2", "source": "ai-test", "verified": True}))
        self.assertTrue(P.notifiable({"severity": "P0", "source": "check", "status": "introduced", "test_backed": True}))
    def test_effective_severity_and_pending(self):
        pr = {"severity": "P1", "verdict": "needs-attention", "findings": [REVIEW(verdict="PLAUSIBLE"), {"severity": "P2", "source": "coverage-gate"}]}
        self.assertEqual(P.effective_severity(pr, True), "P3")
        self.assertEqual(len(P.pending_items(pr, True)), 2)
        self.assertEqual(P.effective_severity({"severity": "P2", "verdict": "qa-error", "findings": [{"severity": "P2", "source": "qa-error"}]}), "untested")

class AlreadySentKeepsOldFlow(unittest.TestCase):
    """Praise 2026-10-23 23:18: the unified rule only covers notices sent from 2026-10-24 00:00 Asia/Shanghai."""
    def test_cutover_instant(self):
        self.assertEqual(P.UNIFIED_SINCE, "2026-10-23T16:00:00Z", "same instant as panoptes finding-policy.mjs UNIFIED_SINCE")
        for at in ("2026-10-23T16:00:00Z", "2026-10-23T16:00:00.000Z", "2026-10-24T00:00:00+08:00", "2026-10-24T08:35:00+08:00"):
            self.assertTrue(P.sent_under_unified(at), at)
        for at in ("2026-10-23T15:59:59Z", "2026-10-23T23:59:59+08:00", "2026-10-23T00:35:00.000Z", None, "", "not a time"):
            self.assertFalse(P.sent_under_unified(at), at)
    def test_legacy_rule_is_the_rule_of_the_time(self):
        L = P.legacy_notified
        self.assertTrue(L(REVIEW(verdict="PLAUSIBLE")), "#1191 was sent on 10-23: its follow-up goes on")
        self.assertTrue(L(REVIEW()) and L({"severity": "P2", "source": "coverage-gate"}))
        self.assertTrue(L({"severity": "P2", "source": "check", "test_backed": True}))
        for f in ({"severity": "P2", "source": "ai-test", "verified": True}, {"severity": "P2", "source": "check", "status": "unverified", "test_backed": True},
                  {"severity": "P2", "source": "check", "status": "flaky", "test_backed": True}, {"severity": "P2", "source": "check"},
                  {"severity": "P1", "source": "code-review", "pre_existing": True}, REVIEW(calibration={"to": "误报"}), REVIEW(severity="P3"),
                  {"severity": "P2", "qa_status": "failed", "confirmed": False, "source": "handoff"}):
            self.assertFalse(L(f), f)

class Receipts(unittest.TestCase):
    def test_receipt_first_line_bold_colons(self):
        g = lambda b: (handoff.RECEIPT.match(b) or [None, None])[1]
        self.assertEqual(g("结论：已修 #1240"), "已修 #1240")
        self.assertEqual(g("**结论**: 不修，原因：设计如此"), "不修")
        self.assertEqual(g("**结论：误报**，触发路径不可达"), "误报")
        self.assertEqual(g("\n  结论：已修 xindong/mivo-canvas-plugin#1222"), "已修 #1222")
        self.assertEqual(g("结论：已修"), "已修")
        self.assertIsNone(handoff.RECEIPT.match("先看了一下\n结论：误报"), "the format is the first line")
        self.assertIsNone(handoff.RECEIPT.match("复核结论：误报成立"))
        self.assertEqual(handoff.parse_receipt("结论：不修，原因：这是产品设计\n详细…"), ("不修", None, "这是产品设计"))
        self.assertEqual(handoff.parse_receipt("**结论**：已修 #1300"), ("已修", 1300, None))
    def test_receipt_first_meaningful_line_synonyms_boundary(self):
        P = handoff.parse_receipt
        self.assertEqual(P("\ufeff结论：不用改，原因：设计如此"), ("不修", None, "设计如此"), "BOM (路滨宇's comments start with one)")
        self.assertEqual(P("<!-- bot -->\n结论：误报，触发不可达"), ("误报", None, "触发不可达"))
        self.assertEqual(P("@aj0928\n\n> 引用的私信\n结论：已修复 #12"), ("已修", 12, None))
        self.assertEqual(P("结论：不成立，因为…"), ("误报", None, "因为…"))
        self.assertEqual(P("结论：已改，见 #1300"), ("已修", 1300, None))
        self.assertEqual(P("结论：已修（#1300）"), ("已修", 1300, None))
        self.assertEqual(P("结论：已修 #943", 943), ("已修", None, None), "the original PR is never its own fix PR")
        self.assertEqual(P("结论：已修\n#12"), ("已修", None, None), "the reason / fix PR never runs into the next line")
        for b in ("结论：不修改这个函数会导致崩溃", "结论：误报率偏高，这条成立", "```\n结论：误报\n```\n正文", "复核结论：误报成立", "先看了一下\n结论：误报"):
            self.assertIsNone(P(b), b)
    def test_shared_receipt_cases(self):
        """Same cases as panoptes argus/tests/fixtures/receipt-cases.json (JS parseReceiptDetail); keep the copy in sync."""
        path = os.path.join(HERE, "receipt-cases.json")
        if not os.path.exists(path): self.skipTest("receipt-cases.json not copied yet")
        import json
        for c in json.load(open(path)):
            want = c.get("out"); got = handoff.parse_receipt(c["in"], c.get("pr"))
            self.assertEqual(got and {"kind": got[0], "fix_pr": got[1], "reason": got[2]}, want and {k: want.get(k) for k in ("kind", "fix_pr", "reason")}, repr(c["in"]))
    def test_qa_comments_do_not_count(self):
        i = {"comments": [{"author": {"login": "PraiseZhu"}, "body": "结论：误报，x"}, {"author": {"login": "aj0928"}, "body": "在看"}]}
        self.assertEqual(handoff.author_receipts(i), [])
        i["comments"].append({"author": {"login": "aj0928"}, "body": "结论：不修，原因：设计如此"})
        self.assertEqual(handoff.author_receipts(i), ["不修"])
    def test_qa_ref_lists_several_original_prs(self):
        multi = "fix: 修复 QA 回访 #904/#910/#898/#884 的四处问题"
        for pr in (904, 910, 898, 884): self.assertTrue(handoff.qa_ref(pr).search(multi), pr)
        for pr in (91, 9040): self.assertFalse(handoff.qa_ref(pr).search(multi), pr)
        for t_ in ("QA #2000", "QA: #2000", "qa:#2000", "QA #1, #2000", "QA 回访 #1、#2000", "QA: xindong/mivo-canvas-plugin#2000"): self.assertTrue(handoff.qa_ref(2000).search(t_), t_)
        for t_ in ("fix #2000", "QA #20001", "SQA #2000", "QA passed; see issue #2000"): self.assertFalse(handoff.qa_ref(2000).search(t_), t_)
    def test_merged_fix_pr_with_qa_ref(self):
        rows = [{"number": 2100, "title": "fix: 串号 (QA #2000)", "body": "", "mergedAt": "2026-10-23T01:00:00Z", "files": [{"path": "src/cache.ts"}]},
                {"number": 2101, "title": "QA #2000 wip", "body": "", "mergedAt": None, "files": [{"path": "src/cache.ts"}]},
                {"number": 2102, "title": "refs #2000", "body": "", "mergedAt": "2026-10-23T01:00:00Z", "files": [{"path": "src/cache.ts"}]},
                {"number": 2103, "title": "QA: #2000 docs only", "body": "", "mergedAt": "2026-10-23T01:00:00Z", "files": [{"path": "README.md"}]}]
        self.assertEqual([p["number"] for p in handoff.merged_fix_prs(2000, ["src/cache.ts"], lister=lambda q: rows)], [2100])
        self.assertEqual([p["number"] for p in handoff.merged_fix_prs(2000, [], lister=lambda q: rows)], [2100, 2103])
        self.assertIsNone(handoff.merged_fix_prs(2000, [], lister=lambda q: (_ for _ in ()).throw(RuntimeError("gh down"))))

class HandoffScan(unittest.TestCase):
    """Overdue only for QA handoff issues; a 「结论：…」 receipt on the original PR by the author or the route owner / cc answers."""
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(); self.saved = {k: getattr(handoff, k) for k in ("known_keys", "routes", "load_report", "merged_fix_prs", "now_cst", "record_actions", "BASE", "QA")}
        now = dt.datetime(2026, 10, 24, 15, 20, tzinfo=dt.timezone(dt.timedelta(hours=8)))
        old = "2026-10-19T00:37:00Z"
        lab = [{"name": "handoff"}, {"name": "P2"}]
        mk = lambda fp, n: f"<!-- mivo-qa-handoff: key={fp}-pr{n} pr={n} -->"
        self.issues = [
            {"number": 10, "state": "OPEN", "url": "https://x/10", "title": "[P2] #900 取消批量生成时可能留下孤立素材", "createdAt": old, "labels": lab, "body": mk("mq1-a", 900), "comments": []},
            {"number": 11, "state": "OPEN", "url": "https://x/11", "title": "[P2] #900 另一条问题标题足够长", "createdAt": old, "labels": lab, "body": mk("mq1-b", 900), "comments": []},
            {"number": 12, "state": "OPEN", "url": "https://x/12", "title": "登录：按画布列出已挂资产", "createdAt": old, "labels": [], "body": "随手记", "comments": []},
            {"number": 13, "state": "OPEN", "url": "https://x/13", "title": "[P1] #901 某问题", "createdAt": old, "labels": lab, "body": mk("mq1-c", 901), "comments": []}]
        rt = {"repo": "aj0928/aj0928-handoff", "owner": "aj0928", "authors": ["aj0928", "lubinyu-lab"], "cc": {"lubinyu-lab": ["aj0928"]}}
        reports = {900: {"pr": {"author": {"login": "lubinyu-lab"}, "findings": [{"fingerprint": "mq1-a", "file": "src/a.ts"}, {"fingerprint": "mq1-b", "file": "src/b.ts"}]}},
                   901: {"pr": {"author": "aj0928", "findings": [{"fingerprint": "mq1-c", "file": "src/c.ts"}]}}}
        handoff.known_keys = lambda gh_ok=True: ({}, {}, {rt["repo"]: self.issues}); handoff.routes = lambda: [rt]
        handoff.load_report = lambda n: (None, reports.get(n)); handoff.merged_fix_prs = lambda n, files=(), lister=None: []
        handoff.now_cst = lambda: now; handoff.BASE = self.tmp; handoff.QA = self.tmp
        self.acts = []; handoff.record_actions = lambda job, day, acts: self.acts.extend(acts)
    def tearDown(self):
        import shutil
        for k, v in self.saved.items(): setattr(handoff, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)
    def test_pr_receipt_by_route_owner_answers_the_named_issue(self):
        seen = {}
        def pr_receipts(n, logins, since):
            seen[n] = logins
            return [("不修", None, "设计如此", "结论：不修，原因：设计如此（src/a.ts）", "https://pr/900#c1", dt.datetime(2026, 10, 22, tzinfo=dt.timezone.utc))] if n == 900 else []
        handoff.scan(False, pr_receipts=pr_receipts)
        self.assertEqual(sorted(a["key"] for a in self.acts), ["overdue:aj0928/aj0928-handoff#11", "overdue:aj0928/aj0928-handoff#13"])
        self.assertEqual(seen[900], {"aj0928", "lubinyu-lab"}, "lubinyu-lab's PR: aj0928 (route owner / cc) answers too")
    def test_generic_pr_receipt_answers_all_but_coverage_talk_only_coverage(self):
        at = dt.datetime(2026, 10, 22, tzinfo=dt.timezone.utc)
        handoff.scan(False, pr_receipts=lambda n, l, s: [("不修", None, "x", "结论：不修，原因：都是设计", "u", at)] if n == 900 else [])
        self.assertEqual([a["key"] for a in self.acts], ["overdue:aj0928/aj0928-handoff#13"])
        self.acts.clear()
        handoff.scan(False, pr_receipts=lambda n, l, s: [("不修", None, "x", "结论：不修，原因：覆盖率不补", "u", at)] if n == 900 else [])
        self.assertEqual(sorted(a["key"] for a in self.acts), ["overdue:aj0928/aj0928-handoff#10", "overdue:aj0928/aj0928-handoff#11", "overdue:aj0928/aj0928-handoff#13"])
    def test_receipt_before_the_issue_does_not_count(self):
        handoff.scan(False, pr_receipts=lambda n, l, s: [("不修", None, "x", "结论：不修", "u", dt.datetime(2026, 10, 18, tzinfo=dt.timezone.utc))])
        self.assertEqual(len(self.acts), 3)
    def test_pr_comment_receipts_filters(self):
        rows = {"c": [{"user": {"login": "aj0928"}, "created_at": "2026-10-22T00:00:00Z", "body": "结论：误报，路径不可达", "html_url": "u1"},
                      {"user": {"login": "PraiseZhu"}, "created_at": "2026-10-22T00:00:00Z", "body": "结论：误报，x", "html_url": "u2"},
                      {"user": {"login": "someone"}, "created_at": "2026-10-22T00:00:00Z", "body": "结论：误报，x", "html_url": "u3"},
                      {"user": {"login": "aj0928"}, "created_at": "2026-10-17T00:00:00Z", "body": "结论：误报，早", "html_url": "u4"},
                      {"user": {"login": "aj0928"}, "created_at": "2026-10-22T00:00:00Z", "body": "在看", "html_url": "u5"}]}
        fetch = lambda path: rows["c"] if path.startswith("repos/xindong/mivo-canvas-plugin/issues/") else []
        got = handoff.pr_comment_receipts(900, {"aj0928", "PraiseZhu"}, dt.datetime(2026, 10, 19, tzinfo=dt.timezone.utc), fetch=fetch)
        self.assertEqual([(g[0], g[2], g[4]) for g in got], [("误报", "路径不可达", "u1")])

class Daily(unittest.TestCase):
    def test_old_report_classify(self):
        pr = {"number": 1191, "severity": "P1", "verdict": "needs-attention", "findings": [REVIEW(verdict="PLAUSIBLE"), {"severity": "P2", "source": "coverage-gate", "title": "new-code coverage 81% < 90%"}],
              "coverage_gate": {"results": {"new_code": "fail"}, "values": {"new_code": 81}}}
        c = daily.classify(1191, {"doc": {}, "pr": pr}, {})
        self.assertEqual(c["sev"], "P3"); self.assertEqual(c["real"], [])
        self.assertEqual([daily.pending_label(f) for f in c["leads"]], ["审查怀疑"], "coverage-only P2 is information, not 待核实")
        pr2 = {"number": 1174, "severity": "P1", "verdict": "needs-attention", "findings": [REVIEW(), REVIEW(severity="P2", fingerprint="mq1-" + "b" * 20)]}
        c2 = daily.classify(1174, {"doc": {}, "pr": pr2}, {})
        self.assertEqual(c2["sev"], "P2", "the unverified AI-only P1 is not sent; the CONFIRMED review P2 is")
        self.assertEqual([daily.pending_label(f) for f in c2["leads"]], ["复核没证实的 P1"])

class Prfix(unittest.TestCase):
    T0 = dt.datetime(2026, 10, 22, 12, 0, tzinfo=prfix.CST)
    NOW = dt.datetime(2026, 10, 23, 21, 0, tzinfo=prfix.CST)
    def ctx(self, findings, author="aj0928", **kw):
        c = {"gates": {}, "gate_warn": None, "ledger_file": None, "ledger": {}, "fix2": set(), "notes": [], "prs": [], "receipts": {}, "links": {}, "tickets": {},
             "unread_repos": [], "owner": {"aj0928": "aj0928"}, "repo_of": {"aj0928": "aj0928/aj0928-handoff"},
             "reports": {1000: {"cur": (self.T0, {"number": 1000, "author": author, "findings": findings}, "PR-1000.json"), "hist": []}}}
        c.update(kw); return c
    def keys(self, c): return {v["key"]: v for v in prfix.evaluate_all(c, self.NOW)}
    def test_candidates_follow_policy(self):
        fs = [REVIEW(verdict="PLAUSIBLE", fingerprint="mq1-" + "1" * 20), {"severity": "P2", "source": "coverage-gate", "fingerprint": "mq1-" + "2" * 20},
              {"severity": "P1", "source": "check", "status": "introduced", "test_backed": True, "fingerprint": "mq1-" + "3" * 20, "file": "src/a.ts"}]
        # Praise 10-23: own PR -> fix everything tested out; a PLAUSIBLE review P1 is a ticket too (prfix's repro test verifies it)
        self.assertEqual(sorted(self.keys(self.ctx(fs, author="PraiseZhu"))), [f"1000:mq1-{'1' * 20}", f"1000:mq1-{'3' * 20}"])
    def test_new_format_p2_needs_no_ruling(self):
        p2 = {"severity": "P2", "source": "code-review", "verdict": "CONFIRMED", "fingerprint": "mq1-" + "4" * 20, "file": "src/a.ts"}
        self.assertEqual(list(self.keys(self.ctx([p2], author="PraiseZhu"))), [f"1000:mq1-{'4' * 20}"], "confirmed P2 on an own PR is a ticket (no 修 ruling needed)")
        self.assertEqual(self.keys(self.ctx([p2], author="PraiseZhu", nofix={1000})), {}, "ruled 不修 / 误报 / P3 / 已修 -> no ticket")
        c = self.ctx([], author="PraiseZhu"); c["reports"][1000]["cur"][1]["pending_verification"] = [dict(p2, severity=None, severity_if_confirmed="P2", pending_kind="review-plausible")]
        self.assertEqual(self.keys(c), {}, "pending P2 -> no ticket")
    def test_only_own_prs_and_pending_items(self):
        """Praise 10-23: 别人的 PR 不修（交接仓给他们自己的 bot）；自己 PR 上待核实的 P0/P1 也修。"""
        f = {"severity": "P1", "source": "check", "status": "introduced", "test_backed": True, "fingerprint": "mq1-" + "5" * 20, "file": "src/a.ts"}
        pv = {"severity_if_confirmed": "P1", "source": "code-review", "fingerprint": "mq1-" + "6" * 20, "file": "src/b.ts", "title": "t"}
        old = _os.environ.pop("PRFIX_OWNERS", None)
        try:
            self.assertEqual(self.keys(self.ctx([f], author="aj0928")), {}, "others' PRs are never candidates")
            c = self.ctx([f], author="PraiseZhu"); c["reports"][1000]["cur"][1]["pending_verification"] = [pv]
            ks = self.keys(c)
            self.assertEqual(sorted(ks), [f"1000:{f['fingerprint']}", f"1000:{pv['fingerprint']}"])
            self.assertEqual(ks[f"1000:{pv['fingerprint']}"]["c"]["sev"], "P1")
        finally:
            if old is not None: _os.environ["PRFIX_OWNERS"] = old
    def test_dm_assignments(self):
        d = prfix.dm_assignments({"xindong/mivo-canvas-plugin#1000:mq1-aaaa": {"covered_fingerprints": ["mq1-bbbb"]}, "dm:2026-10-22:1001": {"sent_at": "t"}, "dm:2026-10-22:1002": {}, "followup-remind:x:y": {}})
        self.assertEqual(d, {"fps": {(1000, "mq1-aaaa"), (1000, "mq1-bbbb")}, "prs": {1001}})

class OwnPrTicket(unittest.TestCase):
    """Praise 2026-10-23: own PRs — every P0/P1 is a ticket (pending too); a confirmed P2 is a ticket unless p2-rulings says no fix."""
    def test_p2_rules(self):
        p2 = {"severity": "P2", "source": "code-review", "verdict": "CONFIRMED", "fingerprint": "f"}
        self.assertEqual(P.own_pr_ticket(p2, 1000, set()), "P2", "confirmed P2 -> ticket")
        self.assertIsNone(P.own_pr_ticket(p2, 1000, set(), nofix={1000}), "PR ruled 不修 / 误报 / P3 / 已修 -> no ticket")
        self.assertIsNone(P.own_pr_ticket(p2, 1000, set(), pending=True), "pending P2 -> no ticket")
        self.assertIsNone(P.own_pr_ticket(dict(p2, verdict="PLAUSIBLE"), 1000, set()), "PLAUSIBLE P2 -> no ticket")
        self.assertIsNone(P.own_pr_ticket({"severity_if_confirmed": "P2", "pending_kind": "review-plausible", "source": "code-review"}, 1000, set()))
        self.assertEqual(P.own_pr_ticket({"severity_if_confirmed": "P1", "pending_kind": "review-plausible", "source": "code-review"}, 1000, set()), "P1", "pending P1 is still a ticket")
        self.assertIsNone(P.own_pr_ticket({"severity": "P2", "source": "coverage-gate"}, 1000, set()), "coverage only as a P2 ruled 修")
        self.assertEqual(P.own_pr_ticket({"severity": "P2", "source": "coverage-gate"}, 1000, {1000}), "P2")
    def test_no_fix_rulings(self):
        t = "| 10-23 | #1216 | x | 不修 | y |\n| 10-23 | #434 | x | 产品代码不修 | y |\n| 10-23 | #948 | P2 | 已修 | #998 |\n| 10-23 | #1124 | x | 修 | y |\n| 10-24 | #1300 | x | 误报 | y |\n| 10-24 | #1301 | x | P3 | y |"
        self.assertEqual(P.no_fix_rulings(t), {1216, 434, 948, 1300, 1301})
        self.assertEqual(P.fix_rulings(t), {1124})

if __name__ == "__main__":
    unittest.main(verbosity=0)
