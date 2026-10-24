"""python3 test/test_opsfix_retest.py — pure parts of opsfix retest (target pick, blame/coverage math, write-back format,
which-retest-decides rule, unmatched results, handoff close/reopen actions, watermark scope).
PI_BIN overrides the code dir (staging review); default ../bin next to this file."""
import sys, os, json, copy, datetime as dt; sys.path.insert(0, os.environ.get("PI_BIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import opsfix_retest as R

rep = {"findings": [
    {"source": "code-review", "severity": "P1", "fingerprint": "mq1-aaaaaaaaaaaaaaaaaaaa", "title": "a"},
    {"source": "coverage-gate", "severity": "P2", "fingerprint": "mq1-bbbbbbbbbbbbbbbbbbbb", "title": "cov"},
    {"source": "code-review", "severity": "P3", "fingerprint": "mq1-cccccccccccccccccccc", "title": "p3"}]}
# fingerprint named in body wins
assert [x["title"] for x in R.pick_targets("QA: #1 fixes mq1-aaaaaaaaaaaaaaaaaaaa", 1, rep)] == ["a"]
# kind named / coverage wording
assert [x["title"] for x in R.pick_targets("QA 报告 coverage-gate P2", 1, rep)] == ["cov"]
assert [x["title"] for x in R.pick_targets("补新增行覆盖", 1, rep)] == ["cov"]
# else all P0-P2 (P3 excluded)
assert [x["title"] for x in R.pick_targets("QA: #1", 1, rep)] == ["a", "cov"]
assert R.qa_refs("QA: #12\nQA：#34") == [12, 34]

A, B = "a" * 40, "b" * 40
por = f"{A} 1 1 1\nauthor x\n\tline1\n{B} 2 2 1\nauthor y\n\tline2\n{A} 3 3\n\tline3\n"
assert R.blame_lines(por, {A}) == [1, 3]
fc = {"statementMap": {"0": {"start": {"line": 1}, "end": {"line": 1}}, "1": {"start": {"line": 3}, "end": {"line": 3}},
      "2": {"start": {"line": 3}, "end": {"line": 3}}, "3": {"start": {"line": 5}, "end": {"line": 9}}, "4": {"start": {"line": 6}, "end": {"line": 7}}},
      "s": {"0": 0, "1": 0, "2": 2, "3": 1, "4": 0}}
assert R.line_coverage(fc) == {1: False, 3: True, 5: True, 6: False}
# line 7 starts no statement: innermost spanning statement (6-7, unhit) decides; line 8 → outer (5-9, hit); 20 → not executable
assert R.line_coverage(fc, [7, 8, 20]) == {1: False, 3: True, 5: True, 6: False, 7: False, 8: True}

assert R.precheck(0, "mq1-x", "coverage-gate") is None  # generic coverage fp never prechecked

# report write-back: the shape build-ledger.mjs retestOf() reads (prs[].retest[] with fingerprint/original_pr/result)
doc = {"prs": [{"number": 9, "retest": [{"fingerprint": "f1", "original_pr": 1, "result": "fail"}]}]}
R.apply_report(doc, 9, [{"fingerprint": "f1", "original_pr": 1, "result": "pass"}, {"fingerprint": "f2", "original_pr": 2, "result": "fail"}])
assert [(x["fingerprint"], x["result"]) for x in doc["prs"][0]["retest"]] == [("f1", "pass"), ("f2", "fail")]

# ledger write-back: the fields pm_extra.retest() reads (fix_prs[].pr, retest != pending)
led = {"summary": {"by_status": {"fixed": 0, "fix_merged_pending_retest": 2, "open": 0}}, "rows": [
    {"key": "k1", "pr": 1, "fingerprint": "f1", "status": "fix_merged_pending_retest", "fix_prs": [], "retests": [], "retest": None},
    {"key": "k2", "pr": 2, "fingerprint": "f2", "status": "fix_merged_pending_retest", "fix_prs": [{"pr": 5}], "retests": [{"pr": 5, "result": "pending"}], "retest": "pending"},
    {"key": "k3", "pr": 3, "fingerprint": "f3", "status": "concluded", "retest": None}]}
res = [dict(fix_pr=9, original_pr=1, fingerprint="f1", result="pass", reason="ok", evidence="e", at="t"),
       dict(fix_pr=9, original_pr=2, fingerprint="f2", result="fail", reason="still", evidence="e", at="t"),
       dict(fix_pr=9, original_pr=3, fingerprint="f3", result="blocked", reason="npm", evidence=None, at="t")]
assert R.apply_ledger(led, res) == ["k1", "k2", "k3"]
r1, r2, r3 = led["rows"]
assert r1["retest"] == "pass" and r1["status"] == "fixed" and r1["fix_prs"][0]["pr"] == 9
assert r2["retest"] == "fail" and r2["status"] == "open" and [x["pr"] for x in r2["retests"]] == [5, 9]
assert r3["retest"] == "pending" and r3["status"] == "concluded" and r3["retests"][0]["reason"].startswith("复测受阻")
assert led["summary"]["retest"] == {"pass": 1, "fail": 1, "pending": 1}
assert led["summary"]["by_status"]["fixed"] == 1 and led["summary"]["by_status"]["open"] == 1
# pm_extra's write-back counter rule
byfix = {}
for r in led["rows"]:
    for f in r.get("fix_prs") or []: byfix.setdefault(f["pr"], []).append(r)
assert not all(r.get("retest") not in (None, "pending") for r in byfix[9])  # blocked keeps fix PR #9 pending → honest

# ---- 10-23: one row, several fix PRs -> fail first, then latest `at` (was: highest fix PR number) ----
E = R.effective_retest
assert E([]) is None and E(None) is None
assert E([{"pr": 1192, "result": "fail", "at": "2026-10-23T15:43:26+08:00"}, {"pr": 1196, "result": "pending"}])["pr"] == 1192   # the #1089 case
assert E([{"pr": 5, "result": "pass", "at": "2026-10-23T09:00:00+08:00"}, {"pr": 9, "result": "pending"}])["result"] == "pass"   # no at = oldest
assert E([{"pr": 5, "result": "pending"}, {"pr": 9, "result": "pending"}])["pr"] == 9                                             # tie -> later in list
assert E([{"pr": 5, "result": "fail", "at": "2026-10-23T09:00:00+08:00"}, {"pr": 9, "result": "pass", "at": "2026-10-24T09:00:00+08:00"}])["result"] == "fail"  # fail first (Praise rule)
assert E([{"pr": 5, "result": "fail", "at": "2026-10-23T01:30:00Z"}, {"pr": 9, "result": "fail", "at": "2026-10-23T09:00:00+08:00"}])["pr"] == 5   # 01:30Z = 09:30 CST, mixed tz parsed
assert E([{"pr": 5, "result": "pass", "at": "2026-10-23T10:00:00+08:00"}, {"pr": 9, "result": "pass", "at": "2026-10-23T09:00:00+08:00"}])["pr"] == 5

# replay of the real #1089 row (ledger 10-22 before the 15:49 write-back) + the 15:49 result: must reopen
row1089 = {"key": "x#1089:mq1-f02ea35b6b7af70976b8", "pr": 1089, "fingerprint": "mq1-f02ea35b6b7af70976b8", "author": "zhongxingtian-ai", "level": "P2",
           "status": "fix_merged_pending_retest", "fix_pr": 1196, "merged": True, "fix_round": 2, "needs_human": False,
           "fix_prs": [{"pr": 1196, "merged": True, "state": "merged", "via": "followup-evidence"}, {"pr": 1192, "merged": True, "state": "merged", "via": "github-reference"}],
           "retests": [{"pr": 1192, "result": "pending"}, {"pr": 1196, "result": "pending"}], "retest": "pending",
           "handoff_issue": {"repo": "zhongxingtian-ai/handover-mivo", "issue": 1, "url": "https://github.com/zhongxingtian-ai/handover-mivo/issues/1"}}
led2 = {"summary": {"total": 1}, "needs_you": [], "rows": [row1089]}
um = []
assert R.apply_ledger(led2, [dict(fix_pr=1192, original_pr=1089, fingerprint="mq1-f02ea35b6b7af70976b8", result="fail", reason="仍能发生", evidence="e", at="2026-10-23T15:43:26+08:00")], um) == [row1089["key"]]
assert row1089["status"] == "open" and row1089["retest"] == "fail" and row1089["retest_pr"] == 1192 and row1089["retest_reopened"] is True
assert row1089["fix_round"] == 2 and row1089["needs_human"] is True and row1089["merged"] is None and um == []
assert led2["summary"]["by_status"]["open"] == 1 and led2["summary"]["retest"]["fail"] == 1 and led2["summary"]["open_by_level"]["P2"] == 1
assert [x["pr"] for x in led2["needs_you"]] == [1089] and led2["needs_you"][0]["fix_prs"] == [1192, 1196] and led2["summary"]["needs_you"] == 1
assert led2["needs_you"][0]["handoff_issue"].endswith("/issues/1")
# the old ordering would have picked #1196 pending: prove the regression is covered
assert sorted(row1089["retests"], key=lambda x: x["pr"])[-1]["result"] == "pending"

# unmatched results are reported, not dropped
um = []
led3 = {"summary": {}, "rows": [{"key": "k9", "pr": 9, "fingerprint": "f9", "status": "open", "fix_prs": [], "retests": []}]}
assert R.apply_ledger(led3, [dict(fix_pr=20, original_pr=8, fingerprint="f8", result="pass", reason="r", evidence=None, at="t"),
                             dict(fix_pr=20, original_pr=7, fingerprint=None, result="blocked", reason="原 PR #7 没有合并报告", evidence=None, at="t"),
                             dict(fix_pr=20, original_pr=6, fingerprint="f6", result="skipped", reason="", evidence=None, at="t")], um) == []
assert [(u["original_pr"], u["fingerprint"], u["result"]) for u in um] == [(8, "f8", "pass"), (7, None, "blocked")]
assert um[0]["why"].startswith("台账无此行") and um[1]["why"].startswith("原 PR 无合并报告")
assert R.apply_ledger(copy.deepcopy(led3), [dict(fix_pr=20, original_pr=8, fingerprint="f8", result="pass", reason="r", evidence=None, at="t")]) == []  # old call shape still works

# open row (no override) + pass -> fixed (build-ledger would have called it fixed once the fix PR is tied); override/concluded rows keep status
led4 = {"summary": {}, "rows": [
    {"key": "a", "pr": 1, "fingerprint": "f", "status": "open", "fix_pr": None, "merged": None, "fix_prs": [], "retests": []},
    {"key": "b", "pr": 2, "fingerprint": "f", "status": "open", "override": {"note": "人工"}, "fix_prs": [], "retests": []},
    {"key": "c", "pr": 3, "fingerprint": "f", "status": "concluded", "fix_prs": [], "retests": []},
    {"key": "d", "pr": 4, "fingerprint": "f", "status": "open", "fix_prs": [], "retests": []}]}
R.apply_ledger(led4, [dict(fix_pr=50, original_pr=n, fingerprint="f", result=res, reason="r", evidence=None, at="2026-10-24T09:30:00+08:00")
                      for n, res in ((1, "pass"), (2, "pass"), (3, "fail"), (4, "fail"))])
a_, b_, c_, d_ = led4["rows"]
assert (a_["status"], a_["retest_confirmed"], a_["fix_pr"], a_["merged"], a_["fix_round"]) == ("fixed", True, 50, True, 1)
assert b_["status"] == "open" and b_["retest"] == "pass" and c_["status"] == "concluded" and c_["retest"] == "fail"
assert d_["status"] == "open" and d_["retest_reopened"] is True and d_["needs_human"] is False
assert led4["summary"]["by_status"] == {"fixed": 1, "fix_merged_pending_retest": 0, "fix_pr_open": 0, "concluded": 1, "open": 2}
assert led4["summary"]["fix_round"] == {"0": 1, "1": 3}   # concluded rows keep their fix_round

# ---- handoff close/reopen as outbound actions (apply-fix-retests.mjs rule) ----
HO = lambda n: {"repo": "aj0928/aj0928-handoff", "issue": n, "url": f"https://github.com/aj0928/aj0928-handoff/issues/{n}"}
led5 = {"rows": [
    {"key": "r1", "pr": 11, "fingerprint": "fa", "author": "aj0928", "status": "fixed", "fix_round": 1, "handoff_issue": HO(1),
     "retests": [{"pr": 21, "result": "pass", "report": "reports/merged/PR-21.json", "at": "2026-10-24T09:30:00+08:00"}]},
    {"key": "r2", "pr": 12, "fingerprint": "fb", "author": "lubinyu-lab", "status": "open", "retest_reopened": True, "fix_round": 2, "needs_human": True,
     "handoff_issue": HO(2), "retests": [{"pr": 22, "result": "fail", "at": "2026-10-24T09:31:00+08:00"}, {"pr": 23, "result": "pending"}]},
    {"key": "r3", "pr": 13, "fingerprint": "fc", "status": "fix_merged_pending_retest", "handoff_issue": HO(3), "retests": [{"pr": 24, "result": "pending"}]},
    {"key": "r4", "pr": 14, "fingerprint": "fd", "status": "fixed", "handoff_issue": None, "retests": [{"pr": 25, "result": "pass"}]},
    {"key": "r5", "pr": 15, "fingerprint": "fe", "status": "fixed", "handoff_issue": HO(5), "retests": [{"pr": 26, "result": "pass", "report": "x"}]}]}
acts = R.handoff_actions(led5, lambda repo, n: {5: "CLOSED"}.get(n))
assert [(x["type"], x["issue"], x["key"]) for x in acts] == [("gh.issue.close", 1, "handoff-close:aj0928/aj0928-handoff#1:fix21"),
                                                             ("gh.issue.reopen", 2, "handoff-reopen:aj0928/aj0928-handoff#2:fix22")]
c0, r0 = acts
assert c0["add_labels"] == ["status:fixed"] and c0["remove_labels"] == ["status:open"] and c0["record"]["set"] == {"status": "fixed", "fix_pr": 21}
assert c0["comment"] == "复测通过：修复 PR #21 的 QA 报告（reports/merged/PR-21.json）显示原问题已解决，关闭。"
assert r0["add_labels"] == ["status:open"] and r0["record"]["stamp"] == "reopened_at" and r0["record"]["file"].endswith("/handoff/aj0928/issues.json")
assert r0["comment"] == "复测未通过：修复 PR #22 的 QA 报告仍有原问题（fb），重新打开，仍归 #12 作者 @lubinyu-lab。第 2 轮，已转人工。"
assert [(x["type"], x["issue"]) for x in R.handoff_actions(led5, lambda repo, n: "OPEN")] == [("gh.issue.close", 1), ("gh.issue.close", 5)]  # already open -> no reopen
assert len(R.handoff_actions(led5)) == 3        # issue state unknown -> keep (outbound re-checks before acting)

# ---- scope: fix PRs merged after the last successful run (watermark), retry, seed ----
CST = R.CST
runs = ['{"pr": 1, "rc": 0}', json.dumps({"kind": "fix-retest", "day": "2026-10-22", "fix_prs": [1165, 1192], "at": "2026-10-23T15:04:10+08:00"})]
st = R.seed_state(runs, dt.date(2026, 10, 24))
assert st["watermark"] == "2026-10-23T00:00:00+08:00" and sorted(st["done"]) == ["1165", "1192"] and st["retry"] == {}
assert R.seed_state([], dt.date(2026, 10, 24))["watermark"] == "2026-10-23T00:00:00+08:00"
runs2 = runs + [json.dumps({"kind": "fix-retest", "day": "since 2026-10-23T00:00:00+08:00", "watermark_to": "2026-10-24T09:30:05+08:00", "fix_prs": [1216], "at": "2026-10-24T09:30:05+08:00"}), "not json"]
st_b = R.seed_state(runs2, dt.date(2026, 10, 25)); assert st_b["watermark"] == "2026-10-24T09:30:05+08:00" and sorted(st_b["done"]) == ["1216"]
prs = [{"number": 1192, "mergedAt": "2026-10-22T08:43:35Z", "body": "QA: #1089"},          # done
       {"number": 1230, "mergedAt": "2026-10-23T03:00:00Z", "body": "QA: #1178"},          # after watermark
       {"number": 1229, "mergedAt": "2026-10-22T13:00:00Z", "body": "QA：#1100"},          # 21:00 CST 10-22: inside the 6 h overlap
       {"number": 1228, "mergedAt": "2026-10-22T05:00:00Z", "body": "QA: #1101"},          # before the overlap: skipped
       {"number": 1227, "mergedAt": "2026-10-23T02:00:00Z", "body": "no ref"}]             # not a fix PR
assert [p["number"] for p in R.select_fix_prs(prs, st)] == [1229, 1230]                    # oldest merge first
st["retry"] = {"1228": {"attempts": 1}}
assert [p["number"] for p in R.select_fix_prs(prs, st)] == [1228, 1229, 1230]
assert [p["number"] for p in R.select_fix_prs(prs, st, only={1192})] == [1192]              # --pr forces even a done one
res_ok = [{"result": "pass", "reason": "ok"}, {"result": "fail", "reason": "x"}]
res_cap = [{"result": "pass", "reason": "ok"}, {"result": "blocked", "reason": "本轮模型调用已达上限 8"}]
res_err = [{"result": "blocked", "reason": "复测执行出错：npm ci rc=1"}]
res_perm = [{"result": "blocked", "reason": "check 类问题没有轻量复跑（要整步重跑）"}]
st2 = {"done": {}, "retry": {"40": {"attempts": 2}}}
_, dn, rt = R.settle(st2, {30: res_ok, 31: res_cap, 32: res_err, 33: res_perm, 40: res_err}, {30: "h30"}, "2026-10-24T09:30:00+08:00")
assert sorted(dn) == [30, 33, 40] and sorted(rt) == [31, 32]
assert st2["done"]["30"]["results"] == {"pass": 1, "fail": 1, "blocked": 0} and st2["done"]["30"]["head"] == "h30"
assert st2["retry"]["31"]["attempts"] == 0 and st2["retry"]["32"]["attempts"] == 1 and st2["done"]["40"]["gave_up_after"] == 3
print("ok test_opsfix_retest")
