"""python3 test/test_mergewatch_app_sla.py — mergewatch 不用 praiseautomation App（10-26 撤回，回原凭证）；
合并 → 开测 p95 ≤ 2 分钟的 SLA 告警。不联网、不碰真私钥（PI_GH_APP_DIR 指到临时目录）。"""
import os, sys, json, tempfile, subprocess, datetime as dt, importlib.util
tmp = tempfile.mkdtemp(); os.environ["PI_GH_APP_DIR"] = tmp; os.environ.pop("PI_GH_APP_ID", None); os.environ.pop("PI_GH_APP_INSTALLATION_ID", None)
os.environ.setdefault("PI_STATE", tempfile.mkdtemp())
B = os.environ.get("PI_BIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
def load(n):
    s = importlib.util.spec_from_file_location(n, f"{B}/{n}.py"); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
assert not os.path.exists(f"{B}/ghapp.py") and "ghapp" not in open(f"{B}/mergewatch.py").read()   # 10-26: App 不再用，回原凭证
# SLA
W = load("watchdog"); now = dt.datetime(2026, 10, 26, 12, tzinfo=dt.timezone.utc)
iso = lambda m: (now - dt.timedelta(minutes=m)).isoformat()
fast = [(iso(60 + i), iso(60 + i - 1)) for i in range(10)]
assert W.merge_sla(fast, now) == (10, 1.0)
slow = fast + [(iso(300), iso(300 - 40))] * 2
n, p = W.merge_sla(slow, now); assert n == 12 and p == 40, (n, p)
assert W.merge_sla([(iso(60 * 30), iso(60 * 29))], now) == (0, None)        # 24 小时以前的不算
rd = tempfile.mkdtemp()
for i, (m, s) in enumerate(slow): json.dump({"prs": [{"merged_at": m, "test_started_at": s}]}, open(f"{rd}/PR-{i}.json", "w"))
probs = {}; W.merge_sla_check(now, probs, rd); assert "sla:merge-to-test" in probs
probs = {}; W.merge_sla_check(now, probs, tempfile.mkdtemp()); assert not probs
# 10-27：gate_wait_s 单独算（#1315 等内存门 30 分钟）：总 p95 还是 40，不含等内存的排队 p95 降到 1
for i in (10, 11): json.dump({"prs": [{"merged_at": slow[i][0], "test_started_at": slow[i][1], "gate_wait_s": 39 * 60}]}, open(f"{rd}/PR-{i}.json", "w"))
probs = {}; assert W.merge_sla_check(now, probs, rd) == (12, 40)
assert "等内存门以外的排队 p95 是 1 分钟" in probs["sla:merge-to-test"]["what"], probs
print("ok test_mergewatch_app_sla")
