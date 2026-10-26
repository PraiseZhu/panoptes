"""python3 test/test_daily_pr1166.py — 10-25 日报没出：PR-1166 的报告 coverage_gate 是 status=unavailable、results/values/baseline=null。
回归：classify 不崩；覆盖率记「未测」= 部分核实（不算通过）；一个 PR 的报告坏了只把它记成故障，其余照出；handoff.plan 同样逐 PR 兜住。
有真实报告（/workspace/mivo-qa/reports/merged/PR-1166.json）时再用真数据跑一遍；没有就只用下面摘出来的字段形状。"""
import sys, os, json, importlib.util
B = os.environ.get("PI_BIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
def load(name):
    spec = importlib.util.spec_from_file_location(name, f"{B}/{name}.py"); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
D = load("daily")
CG_1166 = {"step": "unit", "thresholds": {"new_code": 90}, "gated_metrics": ["new_code"], "baseline": None, "values": None, "results": None,
           "status": "unavailable", "pass": None, "new_code": None}
pr = {"number": 1166, "severity": "P2", "verdict": "review-suggested", "findings": [], "pending_verification": [], "coverage_gate": CG_1166,
      "handoff": {"present": True, "cases": [{"id": "Q1", "qa_status": "verified"}]}}
c = D.classify(1166, {"doc": {}, "pr": pr}, {})
assert c["cov_untested"] is True and c["verify"] == "partial" and c["newcode"] is None, c
ok = D.classify(1, {"doc": {}, "pr": {**pr, "coverage_gate": {"status": "ok", "results": {"new_code": "pass"}, "values": {"new_code": 95}}}}, {})
assert ok["cov_untested"] is False and ok["verify"] == "full"
real = "/workspace/mivo-qa/reports/merged/PR-1166.json"
if os.path.exists(real):
    d = json.load(open(real)); rp = next(p for p in d["prs"] if p.get("number") == 1166)
    rc = D.classify(1166, {"doc": d, "pr": rp}, {}); assert rc["verify"] == "partial", rc["verify"]
    print("ok real PR-1166 report")
# 渲染：覆盖率未测要写出来，不能混进「都在 90% 以上」
base = dict(title="t", login="x", who="x", a={}, merged="2026-10-25T01:00:00Z", leads=[], lead_only=False, base_fn=None, manual=0, docs=False,
            failed_checks=[], code_review_p3=[], health=None, n=1166)
t = D.render("2026-10-25", [{**base, **c}], [], [], {})[0]
assert "覆盖率未测" in t and "#1166" in t.split("覆盖率未测")[1].splitlines()[0], t
# 逐 PR 兜异常：#2 的 classify 崩了只记故障，#1166 照常出
orig = D.classify
D.classify = lambda n, r, cal: (_ for _ in ()).throw(ValueError("boom")) if n == 2 else orig(n, r, cal)
D.load_report = lambda n: (None, {"doc": {"schema_version": "1.0"}, "pr": pr})
D.authors = lambda: {}; D.sent_prs = lambda day: None
prs = [{"number": n, "author": {"login": "x"}, "title": "t", "mergedAt": "2026-10-25T01:00:00Z"} for n in (1166, 2)]
items, fault, carry, cal = D.build("2026-10-25", prs, set())
assert fault == [2] and [i["sev"] for i in items] == ["P3", "fault"], (fault, items)
H = load("handoff")
rows, pend, acts = [], [], []
H._plan_one(1166, {"pr": pr}, {"repo": "o/r"}, {}, {}, rows, pend, acts)
try: H._plan_one(2, {"pr": {"findings": None, "pending_verification": 5}}, {"repo": "o/r"}, {}, {}, rows, pend, acts); raise AssertionError("expected TypeError")
except TypeError: pass   # plan() 的循环用 try 包住每一单（见 handoff.plan），这里确认坏报告确实会抛
import inspect; assert "_plan_one(n, r, rt" in inspect.getsource(H.plan) and "except Exception" in inspect.getsource(H.plan)
print("ok test_daily_pr1166")
# --resend：第一行写「补发」，外发 key 不变
t2 = D.render("2026-10-25", [{**base, **c}], [], [], {}, resend=True)[0]
assert t2.splitlines()[0].startswith("【补发】Mivo 播报 10 月 25 日"), t2.splitlines()[0]
assert not D.render("2026-10-25", [{**base, **c}], [], [], {})[0].startswith("【补发】")
src = inspect.getsource(D.main); assert 'resend="--resend" in fl' in src and '"key": f"mivo-daily-report:{day}"' in src
print("ok test_daily resend")
# run.sh 把作业后面的参数原样交给作业（run.sh daily 2026-10-25 --resend）
assert 'python3 "$CODE/bin/$JOB.py" "$@"' in open(f"{B}/run.sh").read()
