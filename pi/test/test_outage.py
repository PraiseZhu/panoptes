"""python3 test/test_outage.py — 停机检测 / 补跑 / watchdog 合并告警（10-26：02:40–11:00 停摆，早间作业漏跑、报了一串 missed:*）。
只用临时 PI_STATE，不跑真作业（runner 注入）。"""
import os, sys, json, tempfile, datetime as dt, importlib.util
tmp = tempfile.mkdtemp(); os.environ.update(PI_STATE=tmp, PI_OUTAGE_WITNESS=f"{tmp}/witness", PI_ROLLBACK_MARK=f"{tmp}/ROLLBACK", PI_OUTAGE_NO_SPAWN="1")
B = os.environ.get("PI_BIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
def load(n):
    s = importlib.util.spec_from_file_location(n, f"{B}/{n}.py"); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
O = load("outage"); CST = O.CST
T = lambda h, mi=0, d=26: dt.datetime(2026, 10, d, h, mi, tzinfo=CST)
# 1) 心跳间隔判停机，不看 uptime
assert O.beat(T(2, 39)) is None and O.beat(T(2, 40)) is None
w = O.beat(T(11, 0)); assert w and w["kind"] == "pause" and w["gap_min"] == 500, w
assert O.beat(T(11, 1)) is None
# 2) 回滚：标记文件 / 时钟倒退 / seq 比见证小 → rollback，不补跑
open(f"{tmp}/ROLLBACK", "w").close(); r = O.beat(T(11, 30)); os.remove(f"{tmp}/ROLLBACK")
assert r and r["kind"] == "rollback" and O.plan(r, T(11, 31)) == [], r
assert O.classify({"ts": T(12).isoformat(), "seq": 5}, T(11), None, False)["why"] == ["clock-backwards"]
assert O.classify({"ts": T(10, 59).isoformat(), "seq": 3}, T(11), 9, False)["kind"] == "rollback"
assert O.classify({"ts": T(10, 58).isoformat(), "seq": 9}, T(11), 9, False) is None
# 3) 补跑计划：原数据日、credcheck→daily→其余、过期不补、去重
p = O.plan(w, T(11, 1), {})
assert [x[0] for x in p] == ["credcheck", "daily", "dailyretro", "pm"], p      # 10:35 pm 在窗口里；11:15 opsreview 不在
assert all(x[1] == "2026-10-25" for x in p), p
assert O.plan(w, T(18, 0, d=27), {}) == []                                     # 30 小时后都过期
assert [x[0] for x in O.plan(w, T(11, 1), {"credcheck:2026-10-25": {}, "daily:2026-10-25": {}})] == ["dailyretro", "pm"]
long = {"kind": "pause", "start": T(6, 0, 25).isoformat(), "end": T(9, 0).isoformat(), "gap_min": 1, "why": []}
assert [(x[0], x[1]) for x in O.plan(long, T(9, 1))] == [("credcheck", "2026-10-25"), ("daily", "2026-10-24"), ("daily", "2026-10-25"),
    ("dailyretro", "2026-10-24"), ("pm", "2026-10-24"), ("opsreview", "2026-10-24"), ("compare", "2026-10-24")], O.plan(long, T(9, 1))
calls = []
O.replay(p, runner=lambda c: calls.append(c) or 0)
assert [c[1:] for c in calls] == [["credcheck"], ["daily", "2026-10-25"], ["dailyretro", "2026-10-25"], ["pm", "2026-10-25"]], calls
calls.clear(); O.replay(p, runner=lambda c: calls.append(c) or 0); assert calls == []   # 同一 作业+数据日 只补一次
assert O.plan(w, T(11, 2)) == []
# 4) watchdog：同一窗口里的 missed:* 并成一条
W = load("watchdog")
W.runs = lambda job, days=2: []
W.takeover = lambda j: True
probs = {}; W.job_checks(T(12, 0), probs, T(0, 0, 20))
missed = [k for k in probs if k.startswith("missed:")]; out = [k for k in probs if k.startswith("outage:")]
assert out == [f"outage:{w['start']}", f"outage:{r['start']}"], (out, list(probs))
assert "回滚" in json.dumps(probs[out[1]], ensure_ascii=False) and "补跑" in json.dumps(probs[out[0]], ensure_ascii=False)
fyi = json.dumps(probs[out[0]], ensure_ascii=False)   # 10-27：只列真补跑了的（REPLAYED 里有的），不说"错过的已自动补跑"
assert "已补跑：credcheck 07:01、daily 08:35、dailyretro 09:56、pm 10:35" in fyi and "错过的作业已自动补跑" not in fyi, fyi
os.rename(O.REPLAYED(), O.REPLAYED() + ".bak"); p2 = {}; W.job_checks(T(12, 0), p2, T(0, 0, 20)); os.rename(O.REPLAYED() + ".bak", O.REPLAYED())
assert "没有作业被补跑" in json.dumps(p2[out[0]], ensure_ascii=False)
assert not any(k in missed for k in ("missed:daily:0835", "missed:pm:1035", "missed:credcheck:0701")), missed
# 5) mergewatch 接了心跳；停机恢复本身不置 dirty（main sha 变了才置，见 test_mergewatch_resume.py）
mw = open(f"{B}/mergewatch.py").read(); assert "outage.beat()" in mw and "st['dirty'] = True" in mw
print("ok test_outage")
