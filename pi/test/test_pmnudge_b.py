"""python3 test/test_pmnudge_b.py — 11-03 B（Mivo 测试项目经理.md「2026-11-02 Praise 新规」催办范围）：
bot 自己的修复 PR 不再豁免（#1634 在 09:05/10:05 被漏催）；DIRTY/BEHIND >2h、ready+BLOCKED >24h、head 上没有 main CI >30min 各催一次；
kiro 的 QA 修复 PR 同样催；touch PI_STATE/pmnudge-b-off 回到旧行为。"""
import os, sys, json, tempfile, importlib.util, datetime as dt, io, contextlib
os.environ["PI_STATE"] = tempfile.mkdtemp()
B = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
s = importlib.util.spec_from_file_location("pmnudge", f"{B}/pmnudge.py"); M = importlib.util.module_from_spec(s); s.loader.exec_module(M)
CST = dt.timezone(dt.timedelta(hours=8))
T0 = dt.datetime(2026, 11, 3, 9, 0, tzinfo=CST)
CI = [{"workflowName": "CI", "name": "verify", "status": "COMPLETED", "conclusion": "SUCCESS"}]

def pr(n, draft=True, ms="CLEAN", checks=CI, head="a" * 40, body=None, author="PraiseZhu"):
    return {"number": n, "title": f"fix: x (QA #{n - 100})", "isDraft": draft, "createdAt": "2026-11-02T00:00:00Z",
            "body": body if body is not None else f"QA: #{n - 100}\n<!-- pi-line fixpr:{n - 100}:mq1-x -->", "headRefName": f"qa-fix/fixpr-{n}",
            "headRefOid": head, "comments": [], "reviews": [], "statusCheckRollup": checks, "mergeStateStatus": ms, "_author": author}

def at(h): return T0 + dt.timedelta(hours=h)

def test_dirty_after_2h_once():
    p = [pr(1553, draft=False, ms="DIRTY")]
    out, st = M.segment_alerts(p, {}, at(0)); assert out == []
    out, st = M.segment_alerts(p, st, at(1.9)); assert out == []
    out, st = M.segment_alerts(p, st, at(2.1)); assert [(x[0]["number"], x[2]) for x in out] == [(1553, "dirty")], out
    out, st = M.segment_alerts(p, st, at(5)); assert out == []                      # 同一段只催一次
    out, st = M.segment_alerts([pr(1553, draft=False, ms="BEHIND")], st, at(5)); assert out == []   # 值变了 -> 重新计时
    out, st = M.segment_alerts([pr(1553, draft=False, ms="BEHIND")], st, at(7.1)); assert [x[2] for x in out] == ["behind"]

def test_ready_blocked_24h_draft_blocked_not():
    out, st = M.segment_alerts([pr(1621, draft=False, ms="BLOCKED"), pr(1634, draft=True, ms="BLOCKED")], {}, at(0))
    out, st = M.segment_alerts([pr(1621, draft=False, ms="BLOCKED"), pr(1634, draft=True, ms="BLOCKED")], st, at(24.5))
    assert [(x[0]["number"], x[1]) for x in out] == [(1621, "blocked")], out

def test_no_main_ci_per_head():
    other = [{"workflowName": "PR Hygiene Sentinel", "name": "pr-format-gate", "status": "COMPLETED", "conclusion": "FAILURE"}]
    out, st = M.segment_alerts([pr(1661, checks=other)], {}, at(0))
    out, st = M.segment_alerts([pr(1661, checks=other)], st, at(0.6)); assert [x[2] for x in out] == ["无 main CI"]
    out, st = M.segment_alerts([pr(1661, checks=other, head="b" * 40)], st, at(0.7)); assert out == []   # 新 head 重新计时
    out, st = M.segment_alerts([pr(1661, checks=other + CI, head="b" * 40)], st, at(2)); assert out == [] and "nomainci" not in st.get("1661", {})

def run_main(prs_by_author, state=None, off=False):
    """跑一轮 pmnudge.main（--dry-run），gh 按 --author 返回 PR；返回打印出来的文字"""
    flag = getattr(M, "B_OFF", os.path.join(os.environ["PI_STATE"], "pmnudge-b-off"))
    if off: open(flag, "w").close()
    elif os.path.exists(flag): os.remove(flag)
    if state is not None:
        seg = getattr(M, "SEGMENTS", os.path.join(os.environ["PI_STATE"], "out/pm/pmnudge-segments.json"))
        os.makedirs(os.path.dirname(seg), exist_ok=True); json.dump(state, open(seg, "w"))
    def gh(a):
        if "merged" in a: return []
        if "list" in a: return prs_by_author.get(a[a.index("--author") + 1], [])
        return []
    M.gh = gh; M.now_cst = lambda: at(1.5); M.draft_since = lambda n, c: (at(1.4), "创建")
    sys.argv = ["pmnudge.py", "--dry-run"]; buf = io.StringIO()
    with contextlib.redirect_stdout(buf): M.main()
    return buf.getvalue()

def test_own_fix_pr_not_exempt():
    # #1634 的形状（11-03 10:13 pm-daily）：bot 自己开的 draft，除 review-pr 外全绿 -> 要催；旧代码写「bot 自己推进」不催
    txt = run_main({"PraiseZhu": [pr(1634)]})
    assert "- 催 #1634（QA: #1534）除 review-pr 外全绿仍是 draft" in txt, txt
    assert "bot 自己推进" not in txt

def test_dirty_line_in_output_and_kiro_included():
    old = {"1553": {"merge": {"val": "DIRTY", "since": at(-1).isoformat(), "nudged": False}},
           "1700": {"merge": {"val": "BEHIND", "since": at(-1).isoformat(), "nudged": False}}}
    kiro = [pr(1700, draft=False, ms="BEHIND", author="kirozeng", body="QA: #1600\n"), pr(1701, draft=False, author="kirozeng", body="no ref")]
    txt = run_main({"PraiseZhu": [pr(1553, draft=False, ms="DIRTY")], "kirozeng": kiro}, state=old)
    assert "- 催 #1553 dirty（QA: #1453）mergeStateStatus=DIRTY 已 2.5h（超过 2h）" in txt, txt
    assert "- 催 #1700 behind（QA: #1600）" in txt, txt
    assert "#1701" not in txt                       # kiro 的非 QA 修复 PR 不管

def test_rollback_flag_restores_old_behaviour():
    old = {"1553": {"merge": {"val": "DIRTY", "since": at(-1).isoformat(), "nudged": False}}}
    txt = run_main({"PraiseZhu": [pr(1553, draft=False, ms="DIRTY"), pr(1634)], "kirozeng": [pr(1700, ms="BEHIND", body="QA: #1600\n")]}, state=old, off=True)
    assert "催 #1553 dirty" not in txt and "#1700" not in txt and "bot 自己推进" in txt, txt

if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"): f()
    print("ok test_pmnudge_b")
