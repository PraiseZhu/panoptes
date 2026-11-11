#!/usr/bin/env python3
"""fleet_status -- read-only fleet status for bots (ORG.md rule 7, zero model). Run by pi-fallback cron every 30 min.

Writes one JSON file and one Markdown summary (default /workspace/fleet-standards/fleet-status.{json,md}; --json / --md override).
Each file is written whole to a temp file in its own directory and moved into place (lib.write_atomic), so a reader never sees half.
Every item: {section, name, state, at, path, detail}. Sections:
  runs      every `$R <job>` line of pi/cron/pi-line.cron: last run.sh start/end/rc from out/logs/<job>-<day>.log (watchdog.runs);
            every direct cron line (mergewatch, prwatch, watchdog, memguard): newest write to its quiet -cron.log, out/<job>/ or
            <qa>/<job>/ (memguard obs-*.jsonl); newest entry of each out/<dir>.
            Grok Bot routines keep no record on the box -> one "unknown" item.
  identity  /usr/local/bin/gh resolves to the box wrapper (lib.WRAPPER_NAMES) and $PI_STATE/gh-app-enabled exists, i.e. the wrapper
            answers `gh api user` itself with the App login (lib.self_identity_answer; no request, no token); which gh-app-*-enabled flags exist.
  quota     out/praise-pr/grok-usage.json (grokline.py): newest day's baseline and last sample.
  files     reports/merged/PR-*.json written today; 日报 out/daily/<data day>.md; 台账 out/ledger/<day>.json; pm-daily/<today>.md.
  locks     argus-root catch-up.lock / nightly.lock / priority.lock and /tmp/ensure-cindy.v2.lock: held (pid from /proc/locks,
            the lock itself is never taken) / free / missing; run-nightly.sh and e2e batch-run.sh processes from /proc.
Writes nothing else, never signals, never calls a model. Usage: fleet_status.py [--json PATH] [--md PATH]
"""
import sys, glob
from lib import *
import watchdog

FLEET = "/workspace/fleet-standards"
GH = "/usr/local/bin/gh"
CINDY_LOCK = "/tmp/ensure-cindy.v2.lock"   # ensure-cindy.sh:9, = cindy.LOCK
CRON_JOB = re.compile(r"\$R (\w+)")
CRON_DIRECT = re.compile(r"pi/bin/(\w+)\.py >>(\S+)")
DIRECT_STALE_MIN = 15   # direct lines run every 1-5 min
PROC_LOCK = re.compile(r"^\d+: \w+ +\w+ +\w+ +(\d+) ([0-9a-f]+:[0-9a-f]+:\d+) ", re.M)
SECTIONS = ("runs", "identity", "quota", "files", "locks")


def item(section, name, state, at=None, path=None, detail=""):
    return {"section": section, "name": name, "state": state, "at": at, "path": path, "detail": detail}


def mtime(p):
    try: return dt.datetime.fromtimestamp(os.stat(p).st_mtime, CST)
    except OSError: return None


def iso(t): return t.isoformat(timespec="seconds") if t else None


def run_items(now, cron_file, state, qa):
    try: cron = open(cron_file).read()
    except OSError as e: return [item("runs", "pi-line cron", "unknown", path=cron_file, detail=f"cron 文件读不到（{e.__class__.__name__}）")]
    out = []
    watchdog.LOGS = f"{state}/out/logs"
    for job in dict.fromkeys(CRON_JOB.findall(cron)):
        rs = watchdog.runs(job)
        if not rs:
            out.append(item("runs", job, "never", path=f"{watchdog.LOGS}/{job}-*.log", detail="近 2 天 run.sh 日志没有这条作业的记录")); continue
        start, end, rc, note = rs[-1]
        good = [r for r in rs if watchdog.ok_run(r)]
        st = "running" if end is None and not note else "skipped" if note and note.startswith("skip") else \
             "fail" if (note and "syntax-error" in note) or rc not in (0, 75) else "ok" if rc == 0 else "busy"
        out.append(item("runs", job, st, iso(start), f"{watchdog.LOGS}/{job}-{start.date()}.log",
                        f"rc={rc}" + (f"；{note}" if note else "") + f"；最近一次成功：{iso(good[-1][1]) if good else '近 2 天无'}"))
    for job, log_path in dict(CRON_DIRECT.findall(cron)).items():   # quiet logs (written on change): newest of log, out/<job>/, <qa>/<job>/
        seen = [t for t in [mtime(log_path)] + [mtime(e.path) for d in (f"{state}/out/{job}", f"{qa}/{job}") if os.path.isdir(d)
                                                for e in os.scandir(d)] if t]
        t = max(seen, default=None)
        st = "unknown" if t is None else "ok" if (now - t).total_seconds() <= DIRECT_STALE_MIN * 60 else "stale"
        out.append(item("runs", job, st, iso(t), log_path, "日志和产出都不存在" if t is None else
                        f"每 1-5 分钟的作业，日志或 out/{job}/ 最新写入在 {int((now - t).total_seconds() // 60)} 分钟前"))
    for d in sorted(glob.glob(f"{state}/out/*/")):
        name = os.path.basename(d.rstrip("/"))
        if name == "logs": continue
        newest = max((mtime(e.path) for e in os.scandir(d)), default=None, key=lambda t: t or dt.datetime.min.replace(tzinfo=CST))
        out.append(item("runs", f"out/{name}", "info", iso(newest), d.rstrip("/"), "最新产出时间（目录第一层）"))
    out.append(item("runs", "grok routines", "unknown", detail="Grok Bot 例程的运行记录不在 box 上，只能看上面各 out/ 产出时间"))
    return out


def identity_items(gh, state):
    real = os.path.realpath(gh); wrapped = os.path.basename(real) in WRAPPER_NAMES
    flags = sorted(os.path.basename(f) for f in glob.glob(f"{state}/gh-app-*enabled"))
    app = wrapped and "gh-app-enabled" in flags   # = lib.self_identity_answer: the wrapper answers `gh api user` itself
    return [item("identity", "gh wrapper", "ok" if wrapped else "fail", path=gh, detail=f"指向 {real}"),
            item("identity", "gh login", "ok" if app else "fail", path=f"{state}/gh-app-enabled",
                 detail=f"gh api user = {app_login()}（包装脚本本地应答，不发请求）" if app else "不是包装脚本或 gh-app-enabled 不在：gh 会用 PAT 身份"),
            item("identity", "gh-app flags", "info", path=state, detail="、".join(flags) or "一个都没有")]


def quota_items(state):
    p = f"{state}/out/praise-pr/grok-usage.json"
    try: days = json.load(open(p))["days"]; day = max(days); d = days[day]; ts, pct = d["samples"][-1]
    except OSError: return [item("quota", "grok usage", "unknown", path=p, detail="文件不存在")]
    except (ValueError, KeyError, TypeError, IndexError): return [item("quota", "grok usage", "unknown", path=p, detail="格式不对或当天没有样本")]
    return [item("quota", "grok usage", "ok", ts, p, f"{day} 基线 {d.get('baseline')}%，最新样本 {pct}%（共 {len(d['samples'])} 个）")]


def file_item(name, p, due, now):
    t = mtime(p)
    if t: return item("files", name, "ok", iso(t), p)
    return item("files", name, "pending" if now < due else "missing", path=p, detail=f"应在 {due:%m-%d %H:%M} 前生成")


def file_items(now, state, qa, fleet):
    today = now.date(); at = lambda d, h, m: dt.datetime.combine(d, dt.time(h, m), CST)
    ready = [t for f in glob.glob(f"{qa}/reports/merged/PR-*.json") if (t := mtime(f)) and t.date() == today]
    daily_day = today - dt.timedelta(days=1 if now >= at(today, 9, 0) else 2)       # 07:35 run (retry 08:35) writes yesterday's
    ledger_day = today if now >= at(today, 23, 50) else today - dt.timedelta(days=1)  # 23:40 run writes today's
    return [item("files", "QA 报告（今天）", "ok" if ready else "none", iso(max(ready, default=None)), f"{qa}/reports/merged",
                 f"今天写入 {len(ready)} 份 PR-*.json"),
            file_item("日报", f"{state}/out/daily/{daily_day}.md", at(daily_day + dt.timedelta(days=1), 9, 0), now),
            file_item("台账", f"{state}/out/ledger/{ledger_day}.json", at(ledger_day, 23, 50), now),
            file_item("pm-daily", f"{fleet}/pm-daily/{today}.md", at(today, 0, 0), now)]


def lock_state(path, proc_locks):
    try: s = os.stat(path)
    except OSError: return "missing", None
    key = f"{os.major(s.st_dev):02x}:{os.minor(s.st_dev):02x}:{s.st_ino}"
    pids = [pid for pid, k in PROC_LOCK.findall(proc_locks) if k == key]
    return ("held", pids[0]) if pids else ("free", None)


def procs(needle):
    """[(pid, cmdline)] of processes whose argv[0] or argv[1] ends with needle (the script itself, not a command naming it)"""
    out = []
    for p in (x for x in os.listdir("/proc") if x.isdigit()):
        try: argv = open(f"/proc/{p}/cmdline", "rb").read().decode(errors="replace").split("\0")
        except OSError: continue
        if any(a.endswith(needle) for a in argv[:2]): out.append((int(p), " ".join(argv).strip()[:160]))
    return out


def lock_items(argus, cindy_lock):
    try: pl = open("/proc/locks").read()
    except OSError: pl = None
    out = []
    for name, p in (("catch-up.lock", f"{argus}/catch-up.lock"), ("nightly.lock", f"{argus}/nightly.lock"),
                    ("priority.lock", f"{argus}/priority.lock"), ("ensure-cindy.v2.lock", cindy_lock)):
        if pl is None: out.append(item("locks", name, "unknown", path=p, detail="/proc/locks 读不到")); continue
        st, pid = lock_state(p, pl)
        out.append(item("locks", name, st, path=p, detail=f"持有进程 pid {pid}" if pid else ""))
    for name, needle in (("run-nightly", "run-nightly.sh"), ("e2e batch", "batch-run.sh")):
        ps = procs(needle)
        out.append(item("locks", name, "running" if ps else "none", detail="；".join(f"pid {p} {c}" for p, c in ps) or "无进程"))
    return out


def collect(now=None, state=BASE, qa=QA, fleet=FLEET, argus=None, gh=GH, cindy_lock=CINDY_LOCK, cron_file=None):
    now = now or now_cst()
    items = (run_items(now, cron_file or f"{PI_CODE}/cron/pi-line.cron", state, qa) + identity_items(gh, state) + quota_items(state)
             + file_items(now, state, qa, fleet) + lock_items(argus or os.environ.get("PI_ARGUS_ROOT") or f"{qa}/argus-root", cindy_lock))
    return {"generated_at": iso(now), "generator": "pi/bin/fleet_status.py", "items": items}


def render(doc):
    lines = [f"# fleet status {doc['generated_at']}", "", "只读汇总（pi/bin/fleet_status.py，每 30 分钟）。state=unknown 的项说明原因。", ""]
    for sec in SECTIONS:
        lines += [f"## {sec}", ""] + [f"- [{i['state']}] {i['name']}" + (f" @ {i['at']}" if i["at"] else "") + (f"：{i['detail']}" if i["detail"] else "")
                                       for i in doc["items"] if i["section"] == sec] + [""]
    return "\n".join(lines)


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if "-h" in a or "--help" in a: print(__doc__); return 0
    opts = dict(zip(a[::2], a[1::2]))
    if len(a) % 2 or set(opts) - {"--json", "--md"}: print(__doc__, file=sys.stderr); return 2
    doc = collect()
    md, js = render(doc), json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    write_atomic(opts.get("--json", f"{FLEET}/fleet-status.json"), js)
    write_atomic(opts.get("--md", f"{FLEET}/fleet-status.md"), md)
    return 0


if __name__ == "__main__": sys.exit(main())
