#!/usr/bin/env python3
"""outage — 停机检测和停机后补跑（Praise 10-26 批准的方案）。零模型。

心跳：mergewatch 每分钟调 beat()，写 out/outage/heartbeat.json + $PI_STATE/outage-heartbeat.json（时间、boot_id、单调递增 seq；两份取新的，重启后 out/ 没了也能判）。
boot_id 变了（重启）且间隔 > 1 分钟也算停机。外发型作业（OUT_KEYS）补跑前先对 outbound 账本和 actions done 账本，已发的不补。
判停机：看「现在 − 上次心跳」，不看 uptime（平台暂停虚拟机时 uptime 不一定归零）。间隔 > GAP_MIN 分钟 = 一次停机窗口。
分两种：
  - pause（暂停 / 停摆后恢复）：补跑。
  - rollback（主机被回滚到旧快照）：只报告，不补跑——旧状态上补跑会重复外发。判据（任一）：
    /workspace/box-ops/ROLLBACK-SUSPECTED 存在；心跳时间比现在还晚（时钟倒退）；
    心跳 seq 比 out/outage/seq-witness（每次心跳后写到 mivo-qa 之外的工作目录副本）小。
补跑（pause 才做）：
  - 只补幂等、能按数据日重跑的作业 REPLAY，用**原来的数据日**（槽位当天的前一天，北京时间）；
  - 顺序 credcheck → daily → 其余；
  - 过期的不补（槽位距现在超过 EXPIRE_H 小时）；
  - 去重键 = 作业 + 数据日（out/outage/replayed.json），同一键只补一次；作业自己的外发键（如 mivo-daily-report:<日>）本来就含数据日，outbound 已发过的不会重发；
  - 停机期间的合并：mergewatch 恢复后把 dirty 置真，catch-up 会把没报告的合并 PR 全测一遍。
watchdog 用 windows() 把同一停机窗口里的 missed:* 合成一条告警。
用法：outage.py status | outage.py replay-plan（只打印）"""
import os, sys, json, time, subprocess, datetime as dt

CST = dt.timezone(dt.timedelta(hours=8))
BASE = os.environ.get("PI_STATE") or "/workspace/mivo-qa/pi"
D = lambda: os.path.join(BASE, "out", "outage")
HB = lambda: os.path.join(D(), "heartbeat.json")
HB2 = lambda: os.path.join(BASE, "outage-heartbeat.json")   # 10-28：第二份放 $PI_STATE 根（和 outbound-enabled 同级，重启/恢复后还在；18:08 重启后 out/outage/ 是新建的，心跳丢了就判不出停机）
LEDGER = lambda: os.path.join(BASE, "out", "outbound", "ledger.json")
WIN = lambda: os.path.join(D(), "windows.json")
REPLAYED = lambda: os.path.join(D(), "replayed.json")
WITNESS = os.environ.get("PI_OUTAGE_WITNESS") or "/workspace/pi-work/locks/outage-seq-witness"
ROLLBACK_MARK = os.environ.get("PI_ROLLBACK_MARK") or "/workspace/box-ops/ROLLBACK-SUSPECTED"
RUN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run.sh")
GAP_MIN = 5
EXPIRE_H = 30
CRON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cron", "pi-line.cron")
# 10-28：通用补跑。所有 cron/pi-line.cron 里带 @HH 的定时作业都在范围里（槽位从 cron 文件解析，不再手抄）。每个作业一条策略：
#   day   = "data"（参数 = 槽位前一天）| "brief"（参数 = 槽位当天，jasper）| "slot"（参数 = 槽位当天，ledger）| None（不带日期）
#   stale = 槽位过了几小时就不补（下一轮会覆盖 / 已经没意义）
#   latest= True：同一窗口错过多轮只补最后一轮，前面的记 skipped:superseded
# 作业自己的外发去重键 / sent 账本照旧把关（outbound 已发过的不重发）；opsfix 用自己的水位线，pmnudge 当天同 key 只催一次。
# 不在下表里的 @HH 作业用 DEFAULT（不带日期、只补最后一轮、3 小时过期）。
POLICY = {
    "credcheck": dict(day=None, stale=EXPIRE_H, latest=True),
    "daily": dict(day="data", stale=EXPIRE_H), "dailyretro": dict(day="data", stale=EXPIRE_H), "pm": dict(day="data", stale=EXPIRE_H),
    "opsreview": dict(day="data", stale=EXPIRE_H), "compare": dict(day="data", stale=EXPIRE_H),
    "jasper": dict(day="brief", stale=EXPIRE_H),   # 10-27 Praise 批准：只在 brief 日期 = 今天时外发（jasper.py 把关）
    "ledger": dict(day="slot", stale=EXPIRE_H),
    "handoff": dict(day=None, stale=6, latest=True), "followup": dict(day=None, stale=8, latest=True),
    "prretro": dict(day=None, stale=12, latest=True), "p2": dict(day=None, stale=8, latest=True),
    "pmnudge": dict(day=None, stale=1.5, latest=True), "roster": dict(day=None, stale=24, latest=True),
    "opsfix": dict(day=None, stale=4, latest=True), "prfix": dict(day=None, stale=6, latest=True),
    "catchup": dict(day=None, stale=4, latest=True),
}
DEFAULT = dict(day=None, stale=3, latest=True)
# panoptes catch-up 不在 pi-line.cron 里（/etc/cron.d/panoptes-catch-up，7 * * * * + run-nightly.sh 北京偶数点门控）
EXTRA = {"catchup": ([(h, 7) for h in range(0, 24, 2)], ["/workspace/panoptes/run-nightly.sh", "--catch-up", "--trigger", "catch-up", "--wait-lock", "14400"])}
NO_REPLAY_BY_DESIGN = {}   # 10-28：都补了；保留给 watchdog（按设计不补 = replayed 里 skipped:* 的槽位）
ORDER = ["credcheck", "daily"]
# 外发型作业：补跑前先对 outbound 账本 + 作业自己的 actions done 账本，键已经发过就不补（标 skipped:已外发）
OUT_KEYS = {"daily": lambda d: [f"mivo-daily-report:{d}"], "jasper": lambda d: [f"brief:{d}"],
            "pm": lambda d: [f"pm-retro:{d}", f"pm-cos:{d}"], "opsreview": lambda d: [f"ops-review:{d}"]}
OUT_DONE = ("done", "sending", "uncertain")


def schedule(path=None):
    """cron 文件 -> {job: {"slots": [(h, m)], "args": [...]}}，只取 run.sh JOB @HH[,HH] 的行"""
    out = {}
    try: lines = open(path or CRON).read().splitlines()
    except OSError: lines = []
    for ln in lines:
        f = ln.split()
        if len(f) < 9 or ln.lstrip().startswith("#") or f[6] != "$R" or not f[8].startswith("@") or not f[0].isdigit(): continue
        job = f[7]; e = out.setdefault(job, {"slots": [], "args": f[9:]})
        e["slots"] += [(int(h), int(f[0])) for h in f[8][1:].split(",")]
    for j, (slots, cmd) in EXTRA.items(): out.setdefault(j, {"slots": slots, "cmd": cmd, "args": []})
    return out


REPLAY = {j: v["slots"] for j, v in schedule().items()}   # 兼容旧调用方


def jl(p, d):
    try: return json.load(open(p))
    except Exception: return d


def jw(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True); t = p + ".tmp"
    with open(t, "w") as f: json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(t, p)


def boot_id():
    try: return open("/proc/sys/kernel/random/boot_id").read().strip()
    except OSError: return ""


def classify(prev, now, witness_seq=None, mark=None, boot=None):
    """prev = 上次心跳 dict；返回 None（没停）或 {"kind": pause|rollback, "start", "end", "gap_min", "why"}"""
    mark = os.path.exists(ROLLBACK_MARK) if mark is None else mark
    if not prev or "ts" not in prev:   # 10-29：心跳文件缺失/损坏又有标记 → 直接报一次（否则这拍写 mark=True，之后永不报）
        return {"kind": "rollback", "start": now.isoformat(timespec="seconds"), "end": now.isoformat(timespec="seconds"), "gap_min": 0.0, "why": ["ROLLBACK-SUSPECTED (no previous heartbeat)"]} if mark else None
    last = dt.datetime.fromisoformat(prev["ts"])
    gap = (now - last).total_seconds() / 60
    why = []
    # 10-29 修：标记文件是「粘性」的（运维排查完才删），只在它新出现时报一次 rollback；上次心跳已见过标记就不再每分钟重报，
    # 否则 06:38–09:08 每个正常 1 分钟心跳都被判 rollback，期间真实的 10+ 分钟停机也被吞掉不补跑。
    if mark and not prev.get("mark"): why.append("ROLLBACK-SUSPECTED")
    if gap < -1: why.append("clock-backwards")
    if witness_seq is not None and prev.get("seq", 0) < witness_seq: why.append(f"seq {prev.get('seq')} < witness {witness_seq}")
    if why: return {"kind": "rollback", "start": prev["ts"], "end": now.isoformat(timespec="seconds"), "gap_min": round(gap, 1), "why": why}
    if gap > GAP_MIN: return {"kind": "pause", "start": prev["ts"], "end": now.isoformat(timespec="seconds"), "gap_min": round(gap, 1), "why": ["heartbeat gap"]}
    bid = boot_id() if boot is None else boot
    if gap > 1 and prev.get("boot_id") and bid and prev["boot_id"] != bid:   # 重启了：心跳文件还在、间隔不到 GAP_MIN 也算
        return {"kind": "pause", "start": prev["ts"], "end": now.isoformat(timespec="seconds"), "gap_min": round(gap, 1), "why": ["reboot (boot_id changed)"]}
    return None


def beat(now=None):
    """每分钟一次。返回新发现的停机窗口（或 None）。"""
    now = now or dt.datetime.now(CST)
    prev = max((x for x in (jl(HB(), None), jl(HB2(), None)) if x and "ts" in x), key=lambda x: x["ts"], default=None)   # 两份取新的
    try: ws = int(open(WITNESS).read().strip())
    except (OSError, ValueError): ws = None
    mark = os.path.exists(ROLLBACK_MARK)
    w = classify(prev, now, ws, mark)
    seq = max((prev or {}).get("seq", 0), ws or 0) + 1
    hb = {"ts": now.isoformat(timespec="seconds"), "boot_id": boot_id(), "seq": seq, "mark": mark}
    jw(HB(), hb); jw(HB2(), hb)
    try:
        os.makedirs(os.path.dirname(WITNESS), exist_ok=True); open(WITNESS, "w").write(str(seq))
    except OSError: pass
    if w:
        wins = jl(WIN(), []); wins.append(w); jw(WIN(), wins[-50:])
    return w


def windows(): return jl(WIN(), [])


def in_window(t, wins, kinds=("pause", "rollback")):
    """t 落在哪个停机窗口里（含窗口结束后 GRACE 内，cron 来不及跑）"""
    for w in wins:
        if w["kind"] in kinds and dt.datetime.fromisoformat(w["start"]) <= t <= dt.datetime.fromisoformat(w["end"]): return w
    return None


def _arg_day(pol, slot):
    d = slot.date()
    return {"data": (d - dt.timedelta(days=1)).isoformat(), "brief": d.isoformat(), "slot": d.isoformat()}.get(pol["day"])


def plan(w, now, replayed=None, sched=None):
    """停机窗口 w 里错过的、该补的作业：[(job, key_day, slot_iso)]，已排好序。rollback 返回 []。
    key_day = 数据日（day 作业，去重键 job:数据日）或槽位 ISO（不带日期的作业，去重键 job@槽位）。"""
    return _plan(w, now, replayed, sched)[0]


def _plan(w, now, replayed=None, sched=None):
    if not w or w["kind"] != "pause": return [], []
    replayed = replayed if replayed is not None else jl(REPLAYED(), {})
    sched = sched or schedule()
    a, b = dt.datetime.fromisoformat(w["start"]), dt.datetime.fromisoformat(w["end"])
    out, skipped = [], []
    for job, sc in sched.items():
        pol = {**DEFAULT, **POLICY.get(job, {})}
        if pol["day"] and "latest" not in POLICY.get(job, {}): pol["latest"] = False   # 带日期的作业每个数据日各补一次
        cand = []
        d = a.date()
        while d <= b.date():
            for h, mi in sc["slots"]:
                slot = dt.datetime(d.year, d.month, d.day, h, mi, tzinfo=CST)
                if a < slot <= b: cand.append(slot)
            d += dt.timedelta(days=1)
        for slot in sorted(cand):
            si = slot.isoformat(timespec="minutes")
            day = _arg_day({"day": "data"} if pol["day"] == "brief" else pol, slot)   # 去重键 = 数据日（jasper 也是，和 10-27 的记录同口径）
            key = f"{job}:{day}" if day else f"{job}@{si}"
            if key in replayed: continue
            if any(x[0] == job and x[1] == (day or si) for x in out):   # 10-28: daily @07,08 -- the 08:35 retry has the same data day
                skipped.append((job, si, "同数据日已补跑")); continue
            sent = _sent(job, _arg_day(pol, slot))
            if sent: skipped.append((job, si, f"已外发（{sent}）")); continue
            if (now - slot).total_seconds() / 3600 > pol["stale"]: skipped.append((job, si, f"过期（超过 {pol['stale']} 小时）")); continue
            out.append((job, day or si, si))
        if pol.get("latest"):
            xs = [x for x in out if x[0] == job]
            for x in xs[:-1]: skipped.append((job, x[2], "被后一轮覆盖"))
            out = [x for x in out if x[0] != job] + xs[-1:]
    rank = lambda j: ORDER.index(j) if j in ORDER else len(ORDER)
    return sorted(out, key=lambda x: (rank(x[0]), x[2])), skipped


def _sent(job, day, ledger=None):
    """外发型作业的键在 outbound 账本或 out/<job>/<day>.actions.json 里已 done → 返回那个键；否则 None"""
    if job not in OUT_KEYS or not day: return None
    ledger = jl(LEDGER(), {}) if ledger is None else ledger
    acts = {a.get("key"): a.get("status") for f in (job, "ops") for a in jl(os.path.join(BASE, "out", f, f"{day}.actions.json"), {}).get("actions", [])}
    return next((k for k in OUT_KEYS[job](day) if (ledger.get(k) or {}).get("status") in OUT_DONE or acts.get(k) == "done"), None)


def mark_skipped(skipped):
    r = jl(REPLAYED(), {})
    for job, si, why in skipped: r.setdefault(f"{job}@{si}", {"slot": si, "at": dt.datetime.now(CST).isoformat(timespec="seconds"), "status": f"skipped:{why}"})
    jw(REPLAYED(), r)


def command(job, kd, slot, sched=None):
    sc = (sched or schedule()).get(job, {"args": []}); pol = {**DEFAULT, **POLICY.get(job, {})}
    if sc.get("cmd"): return list(sc["cmd"])
    args = list(sc.get("args") or [])
    if job == "followup" and not args: args = ["scan" if dt.datetime.fromisoformat(slot).hour < 12 else "remind"]   # followup 按当前钟点选模式
    if pol["day"]: args = [_arg_day(pol, dt.datetime.fromisoformat(slot))] + args
    return [RUN, job] + args


def replay(items, runner=None, sched=None):
    """按顺序同步跑，跑之前先落盘去重键（中途断了也不会重复补）。runner 测试用。"""
    replayed = jl(REPLAYED(), {})
    for job, kd, slot in items:
        key = f"{job}@{kd}" if kd == slot else f"{job}:{kd}"
        if key in replayed: continue
        pol = {**DEFAULT, **POLICY.get(job, {})}; sent = _sent(job, _arg_day(pol, dt.datetime.fromisoformat(slot)))
        if sent:   # 计划之后才发出去的也不补
            replayed[key] = {"slot": slot, "at": dt.datetime.now(CST).isoformat(timespec="seconds"), "status": f"skipped:已外发（{sent}）"}; jw(REPLAYED(), replayed); continue
        replayed[key] = {"slot": slot, "at": dt.datetime.now(CST).isoformat(timespec="seconds"), "status": "running"}; jw(REPLAYED(), replayed)
        cmd = command(job, kd, slot, sched)
        env = dict(os.environ, QA_HOME="/workspace/mivo-qa") if job in EXTRA else None
        rc = (runner or (lambda c: subprocess.run(c, stdin=subprocess.DEVNULL, env=env).returncode))(cmd)
        replayed[key]["status"] = "done" if rc == 0 else f"rc={rc}"; jw(REPLAYED(), replayed)
    return replayed


def spawn_replay(w):
    """mergewatch 发现 pause 后调：后台起一个 `outage.py replay <窗口 start>`，不挡心跳。"""
    if os.environ.get("PI_OUTAGE_NO_SPAWN"): return None
    return subprocess.Popen([sys.executable, os.path.abspath(__file__), "replay", w["start"]], stdin=subprocess.DEVNULL,
                            stdout=open(os.path.join(BASE, "out", "logs", "outage-replay.log"), "a"), stderr=subprocess.STDOUT, start_new_session=True).pid


def main():
    a = sys.argv[1:]
    if not a or a[0] == "status":
        print(json.dumps({"heartbeat": jl(HB(), None), "windows": windows()[-5:], "replayed": jl(REPLAYED(), {})}, ensure_ascii=False, indent=1)); return 0
    now = dt.datetime.now(CST)
    if a[0] in ("replay", "replay-plan"):
        w = next((x for x in reversed(windows()) if len(a) < 2 or x["start"] == a[1]), None)
        items, skipped = _plan(w, now)
        if a[0] == "replay" and skipped: mark_skipped(skipped)
        print(f"skipped={skipped}", flush=True)
        print(f"{now.isoformat(timespec='seconds')} window={w and (w['kind'], w['start'], w['end'])} plan={items}", flush=True)
        if a[0] == "replay" and items: print(json.dumps(replay(items), ensure_ascii=False), flush=True)
        return 0
    print(__doc__); return 2


if __name__ == "__main__": sys.exit(main())
