"""python3 test/test_pmnudge_qa_ref.py — pmnudge 解析 QA 引用与 pm_extra.QA_REF 同一口径：
认「QA: #N」和「QA: xindong/mivo-canvas-plugin#N」，拒「QA: other/repo#N」。只看正文。"""
import os, sys, json, tempfile, importlib.util, datetime as dt, io, contextlib
os.environ["PI_STATE"] = tempfile.mkdtemp()
B = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
s = importlib.util.spec_from_file_location("pmnudge", f"{B}/pmnudge.py"); M = importlib.util.module_from_spec(s); s.loader.exec_module(M)
CST = dt.timezone(dt.timedelta(hours=8))
T0 = dt.datetime(2026, 11, 10, 12, 0, tzinfo=CST)
CI = [{"workflowName": "CI", "name": "verify", "status": "COMPLETED", "conclusion": "SUCCESS"}]

def test_body_qa_refs_product_repo_and_other_repo():
    assert M.body_qa_refs({"body": "QA: xindong/mivo-canvas-plugin#1089"}) == [1089]
    assert M.body_qa_refs({"body": "QA: other/repo#1089"}) == []
    assert M.body_qa_refs({"body": "QA: #1089"}) == [1089]

def test_merged_refs_accepts_product_repo_rejects_other():
    M.gh = lambda a: [
        {"number": 1196, "headRefName": "fix/1196", "body": "QA: xindong/mivo-canvas-plugin#1089"},
        {"number": 2000, "headRefName": "fix/2000", "body": "QA: other/repo#123"},
    ] if "merged" in a else []
    f = M.merged_refs()
    assert f == {(1089, None): 1196}, f

def run_main(prs):
    def gh(a):
        if "merged" in a: return []
        if "list" in a:
            author = a[a.index("--author") + 1]
            return [p for p in prs if p.get("_author", "PraiseZhu") == author]
        return []
    M.gh = gh
    M.now_cst = lambda: T0
    M.draft_since = lambda n, c: (T0 - dt.timedelta(hours=1), "创建")
    flag = getattr(M, "B_OFF", os.path.join(os.environ["PI_STATE"], "pmnudge-b-off"))
    if os.path.exists(flag): os.remove(flag)
    sys.argv = ["pmnudge.py", "--dry-run"]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): M.main()
    return buf.getvalue()

def pr(n, body, author="PraiseZhu"):
    return {"number": n, "title": "fix: x", "isDraft": True, "createdAt": "2026-11-10T00:00:00Z",
            "body": body, "headRefName": f"qa-fix/fixpr-{n}", "headRefOid": "a" * 40,
            "comments": [], "reviews": [], "statusCheckRollup": CI, "mergeStateStatus": "CLEAN",
            "_author": author}

def test_nudge_product_repo_ref_and_skip_other_repo():
    txt = run_main([
        pr(1196, "QA: xindong/mivo-canvas-plugin#1089"),
        pr(2000, "QA: other/repo#123"),
    ])
    assert "催 #1196（QA: #1089）" in txt, txt
    assert "#2000" not in txt, txt

if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"): f()
    print("ok test_pmnudge_qa_ref")
