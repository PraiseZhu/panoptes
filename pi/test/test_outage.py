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
# 3) 补跑计划（10-28 通用）：cron 文件里所有 @HH 作业；原数据日、credcheck→daily→其余、按作业过期窗口、只补最后一轮、去重
p, sk = O._plan(w, T(11, 1), {})
assert [x[0] for x in p] == ["credcheck", "daily", "handoff", "followup", "roster", "jasper", "opsfix", "prretro", "dailyretro", "pmnudge", "catchup", "pm"], p
assert dict((x[0], x[1]) for x in p)["daily"] == "2026-10-25" and dict((x[0], x[1]) for x in p)["handoff"] == "2026-10-26T06:20+08:00", p
assert ("handoff", "2026-10-26T06:20+08:00") in [(x[0], x[1]) for x in p]                     # 00:05 不在窗口（02:40 起）
assert ("catchup", "2026-10-26T04:07+08:00", "过期（超过 4 小时）") in sk and ("catchup", "2026-10-26T08:07+08:00", "被后一轮覆盖") in sk and ("catchup", "2026-10-26T10:07+08:00") in [(x[0], x[2]) for x in p], sk
assert O.plan(w, T(18, 0, d=27), {}) == []                                     # 30 小时后都过期
p13 = O.plan(w, T(13, 0), {})                                                   # 13:00：pmnudge 10:05 已过 1.5h、catchup 10:07 过 4h 以内
assert "pmnudge" not in [x[0] for x in p13] and "catchup" in [x[0] for x in p13], p13
assert ("pmnudge", "2026-10-26T10:05+08:00", "过期（超过 1.5 小时）") in O._plan(w, T(13, 0), {})[1]
assert "credcheck" not in [x[0] for x in O.plan(w, T(11, 1), {"credcheck@2026-10-26T07:01+08:00": {}})]
long = {"kind": "pause", "start": T(6, 0, 25).isoformat(), "end": T(9, 0).isoformat(), "gap_min": 1, "why": []}
pl = O.plan(long, T(9, 1))
assert [x[1] for x in pl if x[0] == "daily"] == ["2026-10-24", "2026-10-25"] and [x[2] for x in pl if x[0] == "pmnudge"] == [] and [x[1] for x in pl if x[0] == "ledger"] == ["2026-10-25"], pl
assert [x[2] for x in pl if x[0] == "handoff"] == ["2026-10-26T06:20+08:00"], pl                # 只补最后一轮
# 命令：原参数（opsfix retest --execute）、followup 按槽位钟点 scan/remind、catch-up 走 run-nightly
S = O.schedule()
assert O.command("opsfix", "x", "2026-10-26T09:30+08:00", S)[1:] == ["opsfix", "retest", "--execute"]
assert O.command("followup", "x", "2026-10-26T06:27+08:00", S)[1:] == ["followup", "scan"]
assert O.command("catchup", "x", "2026-10-26T10:07+08:00", S)[1] == "--catch-up"
calls = []
O.mark_skipped(sk); O.replay(p, runner=lambda c: calls.append(c) or 0)
cs = [c[1:] for c in calls]
assert cs[:2] == [["credcheck"], ["daily", "2026-10-25"]] and ["jasper", "2026-10-26"] in cs and ["pm", "2026-10-25"] in cs and ["followup", "scan"] in cs, cs
calls.clear(); O.replay(p, runner=lambda c: calls.append(c) or 0); assert calls == []   # 同一键只补一次
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
assert "credcheck 07:01" in fyi and "handoff 06:20" in fyi.split("已补跑：")[1].split("；")[0] and "错过的作业已自动补跑" not in fyi, fyi
assert "opsfix 09:30" in fyi.split("已补跑：")[1].split("；")[0] and "需要手动处理：无" in fyi and probs[out[0]]["kind"] == "fyi", fyi   # 10-28：都补了
os.rename(O.REPLAYED(), O.REPLAYED() + ".bak"); p2 = {}; W.job_checks(T(12, 0), p2, T(0, 0, 20)); os.rename(O.REPLAYED() + ".bak", O.REPLAYED())
assert "已补跑：无；" in json.dumps(p2[out[0]], ensure_ascii=False)
# 10-27 review：只认本窗口（含日期）的补跑记录。旧日子的 done 不能把本窗口 rc=1 的算成已补跑；running = 正在补，不算需人工
RJ = json.load(open(O.REPLAYED()))
json.dump({"daily:2026-10-19": {"slot": "2026-10-20T08:35+08:00", "status": "done"}, "daily:2026-10-25": {"slot": "2026-10-26T08:35+08:00", "status": "rc=1"},
           "pm:2026-10-25": {"slot": "2026-10-26T10:35+08:00", "status": "running"}}, open(O.REPLAYED(), "w"))
p3 = {}; W.job_checks(T(12, 0), p3, T(0, 0, 20)); t3 = json.dumps(p3[out[0]], ensure_ascii=False)
assert "已补跑：无；" in t3 and "daily 08:35" in t3.split("需要手动处理：")[1] and p3[out[0]]["kind"] == "action", t3
assert "正在补跑：pm 10:35" in t3 and "pm 10:35" not in t3.split("需要手动处理：")[1], t3
json.dump(RJ, open(O.REPLAYED(), "w"))
assert not any(k in missed for k in ("missed:daily:0835", "missed:pm:1035", "missed:credcheck:0701")), missed
# 5) mergewatch 接了心跳；停机恢复本身不置 dirty（main sha 变了才置，见 test_mergewatch_resume.py）
mw = open(f"{B}/mergewatch.py").read(); assert "outage.beat()" in mw and "st['dirty'] = True" in mw
print("ok test_outage")
