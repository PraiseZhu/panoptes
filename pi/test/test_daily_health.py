"""python3 test/test_daily_health.py — daily.py: report system_health not ok = 测试系统异常 (日报标准 §4, REPORT-STYLE 系统状态).
Pure: health_issue() + render() on synthetic items; no gh, no files written. PI_BIN overrides the code dir."""
import sys, os, importlib.util
B = os.environ.get("PI_BIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
spec = importlib.util.spec_from_file_location("daily", f"{B}/daily.py"); D = importlib.util.module_from_spec(spec); spec.loader.exec_module(D)
H = D.health_issue
assert H({"system_health": {"status": "ok"}}) is None and H({}) is None
assert H({"system_health": {"status": "degraded", "llm_errors": [{"stage": "code-review"}]}}) == "代码审查调用失败"
assert H({"system_health": {"status": "degraded", "llm_errors": [{"stage": "ai-tests"}], "quota": {"capped": ["ai-test"]}}}) == "AI 补测报错、额度用尽未补测"
assert H({"system_health": {"status": "failed", "qa_tool_failures": [{"stage": "pipeline"}]}}) == "流水线失败、测试工具故障（pipeline）"
assert H({"system_health": {"status": "degraded"}}) == "system_health=degraded"
base = dict(title="t", login="aj0928", who="严健", a={}, merged="2026-10-22T01:00:00Z", real=[], leads=[], lead_only=False, newcode=None, base_fn=None,
            hc={"verified": 0, "failed": 0, "capped": 0, "not-run": 0}, manual=0, docs=False, failed_checks=[], code_review_p3=[])
ok = {**base, "n": 1, "sev": "P3", "health": None}; sick = {**base, "n": 2, "sev": "P3", "health": "代码审查调用失败"}
t = D.render("2026-10-22", [ok], [], [], {})[0]
assert "测试系统运行正常。" in t.splitlines()[0] and "运行正常。" in t
t = D.render("2026-10-22", [ok, sick], [], [], {})[0]
assert t.splitlines()[0].endswith("测试系统有异常：1 个 PR 测试过程有异常（#2 代码审查调用失败），这些 PR 的级别只反映跑通的检查。"), t.splitlines()[0]
assert "2 个都测完了；#2 代码审查调用失败，算测试系统异常，已转运维。" in t
t = D.render("2026-10-22", [ok, sick, {**base, "n": 3, "sev": "fault"}], [3], [], {})[0]
assert "测试系统有故障：1 个 PR 没有合格的 QA 报告（#3）；另外1 个 PR 测试过程有异常（#2 代码审查调用失败）" in t.splitlines()[0]
assert "；1 个缺合格报告，算测试系统故障；#2 代码审查调用失败，算测试系统异常，已转运维。" in t
t = D.render("2026-10-22", [ok, {**base, "n": 3, "sev": "fault"}], [3], [], {})[0]
assert t.splitlines()[0].endswith("测试系统有故障：1 个 PR 没有合格的 QA 报告（#3）。")      # unchanged wording for the old case
print("ok test_daily_health")
