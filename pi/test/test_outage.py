"""python3 test/test_outage.py — 停机检测 / 补跑 / watchdog 合并告警（10-26：02:40–11:00 停摆，早间作业漏跑、报了一串 missed:*）。
只用临时 PI_STATE，不跑真作业（runner 注入）。"""
import os, sys, json, tempfile, datetime as dt, importlib.util


def test_outage():
    tmp = tempfile.mkdtemp(); os.environ.update(PI_STATE=tmp, PI_OUTAGE_WITNESS=f"{tmp}/witness", PI_ROLLBACK_MARK=f"{tmp}/ROLLBACK", PI_OUTAGE_NO_SPAWN="1")
    B = os.environ.get("PI_BIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
    def load(n):
        s = importlib.util.spec_from_file_location(n, f"{B}/{n}.py"); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
    O = load("outage"); CST = O.CST; sys.modules["outage"] = O   # watchdog 里 import outage 要拿到这份（pytest 同进程里别的测试可能已经按别的 PI_STATE 导入过）
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
    # 6) 10-28 18:08 重启、18:21 cron 才回来：out/outage/heartbeat.json 丢了，$PI_STATE 根上那份还在 → 照样判出停机
    O.jw(O.HB(), {"ts": T(17, 58, 28).isoformat(), "boot_id": "b1", "seq": 50}); O.jw(O.HB2(), {"ts": T(18, 7, 28).isoformat(), "boot_id": "b1", "seq": 51})
    os.remove(O.HB()); open(f"{tmp}/witness", "w").write("51")
    g = O.beat(T(18, 21, 28)); assert g and g["kind"] == "pause" and g["start"] == T(18, 7, 28).isoformat() and g["gap_min"] == 14, g
    assert O.jl(O.HB(), {}).get("seq") == 52 == O.jl(O.HB2(), {}).get("seq")          # 两份都写回
    # 心跳文件都在、只是进程重启（boot_id 变了），间隔 3 分钟 < GAP_MIN 也算；同一次开机连着跳 3 分钟不算
    hb = {"ts": T(18, 18, 28).isoformat(), "boot_id": "old", "seq": 9}
    assert O.classify(hb, T(18, 21, 28), None, False, "new")["why"] == ["reboot (boot_id changed)"]
    assert O.classify(hb, T(18, 21, 28), None, False, "old") is None and O.classify({**hb, "ts": T(18, 21, 28).isoformat()}, T(18, 21, 28), None, False, "new") is None
    # 平常每分钟一跳：不报停机
    O.jw(O.HB2(), {}); os.remove(O.HB())
    assert all(O.beat(T(19, i, 28)) is None for i in range(30))
    # 错过的槽位：10–18h 窗口里 pmnudge 每小时一槽，只补最后一轮（18:05），10–17 点记被覆盖；pm 10:35 按原数据日
    gw = {"kind": "pause", "start": T(9, 59, 28).isoformat(), "end": T(18, 21, 28).isoformat(), "gap_min": 1, "why": []}
    pp, ss = O._plan(gw, T(18, 22, 28), {})
    assert [x[2] for x in pp if x[0] == "pmnudge"] == ["2026-10-28T18:05+08:00"], pp
    assert sorted(x[1] for x in ss if x[0] == "pmnudge") == [f"2026-10-28T{h}:05+08:00" for h in range(10, 18)], ss
    assert [x[1] for x in pp if x[0] == "pm"] == ["2026-10-27"], pp                     # pm 10:35 原数据日
    # 外发型作业：outbound 账本已 done（或 actions 账本 done）→ 不补；没发过的照补
    O.jw(O.LEDGER(), {"pm-cos:2026-10-27": {"status": "done"}})
    pp, ss = O._plan(gw, T(18, 22, 28), {})
    assert "pm" not in [x[0] for x in pp] and ("pm", "2026-10-28T10:35+08:00", "已外发（pm-cos:2026-10-27）") in ss, ss
    O.jw(os.path.join(tmp, "out", "daily", "2026-10-27.actions.json"), {"actions": [{"key": "mivo-daily-report:2026-10-27", "status": "done"}]})
    calls.clear(); rr = O.replay([("daily", "2026-10-27", "2026-10-28T08:35+08:00"), ("opsreview", "2026-10-27", "2026-10-28T09:00+08:00")], runner=lambda c: calls.append(c) or 0)
    assert [c[1] for c in calls] == ["opsreview"] and rr["daily:2026-10-27"]["status"].startswith("skipped:已外发"), (calls, rr)
    os.remove(O.LEDGER())
    # 5) mergewatch 接了心跳；停机恢复本身不置 dirty（main sha 变了才置，见 test_mergewatch_resume.py）
    mw = open(f"{B}/mergewatch.py").read(); assert "outage.beat()" in mw and "st['dirty'] = True" in mw
    print("ok test_outage")

    # 10-29：ROLLBACK-SUSPECTED 标记一直在（06:38–09:08）时，正常 1 分钟心跳不能每分钟报 rollback
    M = lambda h, m, s=0: dt.datetime(2026, 10, 29, h, m, s, tzinfo=O.CST)
    assert O.classify({"ts": M(6, 38, 1).isoformat(), "seq": 1}, M(6, 39, 1), None, True, "b")["why"] == ["ROLLBACK-SUSPECTED"]   # 标记新出现：报一次
    assert O.classify({"ts": M(8, 20, 1).isoformat(), "seq": 1, "mark": True}, M(8, 21, 1), None, True, "b") is None   # 标记已见过 + 1 分钟心跳：不报
    assert O.classify({"ts": M(8, 20, 1).isoformat(), "seq": 1, "mark": False}, M(8, 21, 1), None, False, "b") is None   # 普通 1 分钟心跳
    assert O.classify({"ts": M(6, 42, 2).isoformat(), "seq": 1, "mark": True}, M(6, 52, 26), None, True, "b")["kind"] == "pause"   # 标记期间的真停机照样判 pause
    assert O.classify({"ts": M(8, 21, 1).isoformat(), "seq": 1, "mark": True}, M(8, 19, 1), None, True, "b")["why"] == ["clock-backwards"]   # 时钟倒退照报
    assert O.classify({"ts": M(8, 21, 1).isoformat(), "seq": 1, "mark": False}, M(8, 19, 1), None, False, "b")["kind"] == "rollback"
    w = O.classify({"ts": M(9, 42, 11).isoformat(), "seq": 1, "mark": False}, M(9, 47, 40), None, False, "b")
    assert w["kind"] == "pause" and w["gap_min"] == 5.5 and w["why"] == ["heartbeat gap"]   # 09:47 的 5.5 分钟心跳间隔照旧
    print("test_outage 10-29 ok")
    # beat() 端到端：标记文件持续存在，只在第一拍报 rollback
    for p in (O.HB(), O.HB2(), O.WITNESS):
        if os.path.exists(p): os.remove(p)
    O.beat(M(8, 0)); open(O.ROLLBACK_MARK, "w").close()
    ks = [(O.beat(M(8, i)) or {}).get("kind") for i in range(1, 6)]
    os.remove(O.ROLLBACK_MARK)
    assert ks == ["rollback", None, None, None, None], ks
    assert O.beat(M(8, 6)) is None and (O.beat(M(8, 4)) or {}).get("kind") == "rollback"   # 标记删了正常；时钟倒退照报
    print("test_outage beat ok")

    # 10-29 验收：心跳文件缺失 / 损坏 + 标记已在 → 首拍报一次 rollback，之后不重报；没标记 → 照旧 None
    for bad in (None, "{corrupt"):
        for p in (O.HB(), O.HB2(), O.WITNESS):
            if os.path.exists(p): os.remove(p)
        if bad:
            for p in (O.HB(), O.HB2()): open(p, "w").write(bad)
        open(O.ROLLBACK_MARK, "w").close()
        ks = [(O.beat(M(9, i)) or {}).get("kind") for i in range(1, 4)]
        os.remove(O.ROLLBACK_MARK)
        assert ks == ["rollback", None, None], (bad, ks)
        assert O.in_window(M(9, 1), O.windows())   # 窗口能被 watchdog 正常解析
    assert O.classify(None, M(9, 0), None, False) is None and O.classify({}, M(9, 0), None, False) is None
    print("test_outage no-prev ok")


if __name__ == "__main__":
    test_outage()
