#!/usr/bin/env python3
"""outage — 停机检测和停机后补跑（Praise 10-26 批准的方案）。零模型。

心跳：mergewatch 每分钟调 beat()，写 out/outage/heartbeat.json（时间、boot_id、单调递增 seq）。
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
WIN = lambda: os.path.join(D(), "windows.json")
REPLAYED = lambda: os.path.join(D(), "replayed.json")
WITNESS = os.environ.get("PI_OUTAGE_WITNESS") or "/workspace/pi-work/locks/outage-seq-witness"
ROLLBACK_MARK = os.environ.get("PI_ROLLBACK_MARK") or "/workspace/box-ops/ROLLBACK-SUSPECTED"
RUN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run.sh")
GAP_MIN = 5
EXPIRE_H = 30
# 作业 -> [(北京时, 分)]，和 cron/pi-line.cron、watchdog.DAILY 一致；只列幂等、接受数据日参数的作业（credcheck 不带参数）
REPLAY = {"credcheck": [(7, 1)], "daily": [(8, 35)], "dailyretro": [(9, 56)], "pm": [(10, 35)], "opsreview": [(11, 15)], "compare": [(12, 50)],
          "jasper": [(9, 5)]}   # 10-27 Praise 批准：只在 brief 日期 = 今天时外发，账本已有 brief:<日> 不再发；过去的日子只写文件（jasper.py 把关）
# 故意不补的作业和原因（10-27）：watchdog 的停机告警按「已补跑 / 按设计不补（附原因）/ 需要手动处理」三组列出
NO_REPLAY_BY_DESIGN = {"handoff": "下一次 handoff 运行会覆盖", "followup": "下一次 followup 运行会覆盖", "opsenv": "下一轮运行自行恢复",
                       "merger": "下一轮运行自行恢复"}
ORDER = ["credcheck", "daily"]
NO_DAY = {"credcheck"}
BRIEF_DAY = {"jasper"}   # 参数是 brief 日期（槽位当天 = 数据日 + 1），不是数据日


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


def classify(prev, now, witness_seq=None, mark=None):
    """prev = 上次心跳 dict；返回 None（没停）或 {"kind": pause|rollback, "start", "end", "gap_min", "why"}"""
    if not prev or "ts" not in prev: return None
    last = dt.datetime.fromisoformat(prev["ts"])
    gap = (now - last).total_seconds() / 60
    mark = os.path.exists(ROLLBACK_MARK) if mark is None else mark
    why = []
    if mark: why.append("ROLLBACK-SUSPECTED")
    if gap < -1: why.append("clock-backwards")
    if witness_seq is not None and prev.get("seq", 0) < witness_seq: why.append(f"seq {prev.get('seq')} < witness {witness_seq}")
    if why: return {"kind": "rollback", "start": prev["ts"], "end": now.isoformat(timespec="seconds"), "gap_min": round(gap, 1), "why": why}
    if gap > GAP_MIN: return {"kind": "pause", "start": prev["ts"], "end": now.isoformat(timespec="seconds"), "gap_min": round(gap, 1), "why": ["heartbeat gap"]}
    return None


def beat(now=None):
    """每分钟一次。返回新发现的停机窗口（或 None）。"""
    now = now or dt.datetime.now(CST)
    prev = jl(HB(), None)
    try: ws = int(open(WITNESS).read().strip())
    except (OSError, ValueError): ws = None
    w = classify(prev, now, ws)
    seq = max((prev or {}).get("seq", 0), ws or 0) + 1
    jw(HB(), {"ts": now.isoformat(timespec="seconds"), "boot_id": boot_id(), "seq": seq})
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


def plan(w, now, replayed=None):
    """停机窗口 w 里错过的、该补的作业：[(job, data_day, slot_iso)]，已排好序。rollback 返回 []。"""
    if not w or w["kind"] != "pause": return []
    replayed = replayed if replayed is not None else jl(REPLAYED(), {})
    a, b = dt.datetime.fromisoformat(w["start"]), dt.datetime.fromisoformat(w["end"])
    out = []
    for job, slots in REPLAY.items():
        d = a.date()
        while d <= b.date():
            for h, mi in slots:
                slot = dt.datetime(d.year, d.month, d.day, h, mi, tzinfo=CST)
                if not (a < slot <= b): continue
                if (now - slot).total_seconds() / 3600 > EXPIRE_H: continue   # 过期不补
                day = (d - dt.timedelta(days=1)).isoformat()
                key = f"{job}:{day}"
                if key in replayed or any(x[0] == job and x[1] == day for x in out): continue
                out.append((job, day, slot.isoformat(timespec="minutes")))
            d += dt.timedelta(days=1)
    for j in NO_DAY:   # 不带数据日的作业（credcheck 只查当下）补最新一轮就够
        xs = [x for x in out if x[0] == j]; out = [x for x in out if x[0] != j] + xs[-1:]
    rank = lambda j: ORDER.index(j) if j in ORDER else len(ORDER)
    return sorted(out, key=lambda x: (rank(x[0]), x[2]))


def replay(items, runner=None):
    """按顺序同步跑，跑之前先落盘去重键（中途断了也不会重复补）。runner 测试用。"""
    replayed = jl(REPLAYED(), {})
    for job, day, slot in items:
        key = f"{job}:{day}"
        if key in replayed: continue
        replayed[key] = {"slot": slot, "at": dt.datetime.now(CST).isoformat(timespec="seconds"), "status": "running"}; jw(REPLAYED(), replayed)
        arg = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat() if job in BRIEF_DAY else day
        cmd = [RUN, job] + ([] if job in NO_DAY else [arg])
        rc = (runner or (lambda c: subprocess.run(c, stdin=subprocess.DEVNULL).returncode))(cmd)
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
        items = plan(w, now)
        print(f"{now.isoformat(timespec='seconds')} window={w and (w['kind'], w['start'], w['end'])} plan={items}", flush=True)
        if a[0] == "replay" and items: print(json.dumps(replay(items), ensure_ascii=False), flush=True)
        return 0
    print(__doc__); return 2


if __name__ == "__main__": sys.exit(main())
