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
G1 = W.SLA_P95_MIN; W.SLA_P95_MIN = 2   # 下面的老用例按 2 分钟阈值写的，只测告警逻辑；10-30 段恢复真实阈值
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
w = probs["sla:merge-to-test"]["what"]
assert "最大 40 分钟、中位数 1 分钟（样本 12 < 20，不算 p95）" in w and "等内存门以外的排队：最大 1 分钟" in w and "p95 40" not in w, w
# 坏行：test_started_at 不是时间、gate_wait_s 不是数字 → 只跳过那一行，不崩
json.dump({"prs": [{"merged_at": iso(100), "test_started_at": "not-a-time", "gate_wait_s": 5}]}, open(f"{rd}/PR-bad1.json", "w"))
json.dump({"prs": [{"merged_at": iso(100), "test_started_at": iso(99), "gate_wait_s": "abc"}]}, open(f"{rd}/PR-bad2.json", "w"))
probs = {}; n, p = W.merge_sla_check(now, probs, rd); assert n == 13 and p == 40 and "sla:merge-to-test" in probs, (n, p)
assert "不含等内存 1 分钟" in probs["sla:merge-to-test"]["todo"] or "1 分钟" in probs["sla:merge-to-test"]["what"], probs
# 10-29：样本 ≥ 20 才报 p95
for i in range(20, 30): json.dump({"prs": [{"merged_at": iso(60 + i), "test_started_at": iso(60 + i - 1)}]}, open(f"{rd}/PR-{i}.json", "w"))
probs = {}; W.merge_sla_check(now, probs, rd); w = probs["sla:merge-to-test"]["what"]
assert "p95 40 分钟" in w and "中位数" not in w, w
# 10-30（B）：阈值 = G1 120 分钟；skipped / probe-* 基线的报告不进样本
W.SLA_P95_MIN = G1; assert W.SLA_P95_MIN == 120, W.SLA_P95_MIN
rd2 = tempfile.mkdtemp()
for i in range(6): json.dump({"prs": [{"merged_at": iso(200 + i), "test_started_at": iso(200 + i - 60)}]}, open(f"{rd2}/PR-{i}.json", "w"))
probs = {}; n, p = W.merge_sla_check(now, probs, rd2); assert n == 6 and p == 60 and not probs, (n, p, probs)   # p95 60 < 120：不告警
json.dump({"prs": [{"merged_at": iso(1000), "test_started_at": iso(30), "verdict": "skipped", "skip_reason": "non-main-base", "base_ref": "probe-1410/strict/base-t"}]}, open(f"{rd2}/PR-s1.json", "w"))
json.dump({"prs": [{"merged_at": iso(1000), "test_started_at": iso(825), "base_ref": "probe-1410/b"}]}, open(f"{rd2}/PR-s2.json", "w"))
json.dump({"prs": [{"merged_at": iso(1000), "test_started_at": iso(30), "skip_reason": "non-main-base"}]}, open(f"{rd2}/PR-s3.json", "w"))
probs = {}; n, p = W.merge_sla_check(now, probs, rd2); assert n == 6 and p == 60 and not probs, (n, p, probs)
print("ok test_mergewatch_app_sla")
