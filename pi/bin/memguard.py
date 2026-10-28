#!/usr/bin/env python3
"""memguard phase 1 (observe only; design: fleet-standards/memory-protection-design.md section 5).
Every 10 s appends one JSON line to $MEMGUARD_DIR/obs-YYYYMMDD.jsonl (Beijing date, TZ pinned in code): MemAvailable / MemFree / swap,
PSI memory some/full avg10, host balloon MB, top RSS processes, and every Pi task (prfix / pi-dispatch / panoptes run) with
its process-tree RSS. NEVER signals or kills anything. Files older than KEEP_DAYS are deleted.
  memguard.py            cron entry: start the daemon unless one already holds the pidfile lock (idempotent)
  memguard.py --once     print one sample, write nothing
  memguard.py --rollback stop the daemon and touch $MEMGUARD_DIR/disable (cron then starts nothing); rm the file to re-enable
"""
import fcntl, json, os, signal, sys, time
# 10-28: box TZ drifts (seen America/Los_Angeles); ts and obs-YYYYMMDD are always Beijing time
os.environ["TZ"] = "Asia/Shanghai"
time.tzset()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prwatch   # noqa: E402  (proc_table / tree / prfix_runs / dispatch_runs / panoptes_procs)

DIR = os.environ.get("MEMGUARD_DIR", "/workspace/mivo-qa/memguard")
INTERVAL = float(os.environ.get("MEMGUARD_INTERVAL", "10"))
KEEP_DAYS = int(os.environ.get("MEMGUARD_KEEP_DAYS", "14"))
PROC = os.environ.get("MEMGUARD_PROC", "/proc")
PIDFILE, DISABLE = os.path.join(DIR, "memguard.pid"), os.path.join(DIR, "disable")


def meminfo():
    m = {}
    try:
        for l in open(f"{PROC}/meminfo"):
            k, v = l.split(":", 1); m[k] = int(v.split()[0]) // 1024
    except (OSError, ValueError): pass
    return m


def psi():
    out = {}
    try:
        for l in open(f"{PROC}/pressure/memory"):
            f = l.split(); out[f[0]] = float(dict(x.split("=") for x in f[1:])["avg10"])
    except (OSError, ValueError, KeyError, IndexError): pass
    return {"some_avg10": out.get("some"), "full_avg10": out.get("full")}


def balloon_mb():
    try:
        v = dict(l.split() for l in open(f"{PROC}/vmstat") if l.startswith("balloon_"))
        return max(0, int(v.get("balloon_inflate", 0)) - int(v.get("balloon_deflate", 0))) * os.sysconf("SC_PAGE_SIZE") // 2**20
    except (OSError, ValueError): return -1


def name(cmd):
    v = [x.decode(errors="replace") for x in cmd.split(b"\0") if x]
    return " ".join(v)[:120] if v else ""


def tasks(t):
    out = []
    pys, waiting = prwatch.prfix_runs(t)
    for p in pys: out.append({"kind": "prfix", "pid": p, "rss_mb": sum(t[q][1] for q in prwatch.tree(t, p))})
    for p in waiting: out.append({"kind": "prfix-wait", "pid": p, "rss_mb": sum(t[q][1] for q in prwatch.tree(t, p))})
    for p in prwatch.dispatch_runs(t):
        v = t[p][2].split(b"\0")
        out.append({"kind": "dispatch", "pid": p, "spec": v[3].decode(errors="replace")[:80] if len(v) > 3 else "",
                    "rss_mb": sum(t[q][1] for q in prwatch.tree(t, p))})
    pan = prwatch.panoptes_procs(t)
    if pan:
        out.append({"kind": "panoptes", "pid": min(p for p in pan if t[p][0] not in pan), "rss_mb": sum(t[p][1] for p in pan)})
    return sorted(out, key=lambda x: -x["rss_mb"])


def sample(t=None, now=None):
    t = prwatch.proc_table() if t is None else t
    m = meminfo(); top = sorted(t.items(), key=lambda kv: -kv[1][1])[:8]
    return {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)), "mode": "observe",
            "mem_total_mb": m.get("MemTotal"), "mem_avail_mb": m.get("MemAvailable"), "mem_free_mb": m.get("MemFree"),
            "swap_total_mb": m.get("SwapTotal"), "swap_free_mb": m.get("SwapFree"), "psi": psi(), "balloon_mb": balloon_mb(),
            "pi_tasks": tasks(t), "top": [{"pid": p, "rss_mb": r, "cmd": name(c)} for p, (_, r, c) in top]}


def path_for(now):
    return os.path.join(DIR, "obs-" + time.strftime("%Y%m%d", time.localtime(now)) + ".jsonl")


def rotate(now):
    for f in os.listdir(DIR):
        if f.startswith("obs-") and f.endswith(".jsonl"):
            p = os.path.join(DIR, f)
            if now - os.path.getmtime(p) > KEEP_DAYS * 86400: os.remove(p)


def write(rec, now):
    with open(path_for(now), "a") as f: f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def lock():
    """-> open pidfile holding an exclusive flock, or None when another instance holds it"""
    os.makedirs(DIR, exist_ok=True)
    f = open(PIDFILE, "a+")
    try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: f.close(); return None
    f.seek(0); f.truncate(); f.write(f"{os.getpid()}\n"); f.flush(); return f


def loop(max_iter=None, sleep=time.sleep):
    n = 0
    while max_iter is None or n < max_iter:
        if os.path.exists(DISABLE): return
        now = time.time()
        try: write(sample(now=now), now); rotate(now)
        except Exception as e: sys.stderr.write(f"memguard sample error: {e!r}\n")
        n += 1
        if max_iter is None or n < max_iter: sleep(INTERVAL)


def rollback():
    os.makedirs(DIR, exist_ok=True); open(DISABLE, "w").close()
    try:
        pid = int(open(PIDFILE).read().strip())
        if any(os.path.basename(a) == b"memguard.py" for a in open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")): os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError): pass
    print(f"memguard disabled ({DISABLE}); rm it to re-enable")


def main(argv):
    if "--once" in argv: print(json.dumps(sample(), ensure_ascii=False)); return 0
    if "--rollback" in argv: rollback(); return 0
    if os.path.exists(DISABLE): return 0
    lk = lock()
    if lk is None: return 0          # already running
    if "--foreground" not in argv:
        lk.close()                    # child re-locks after fork; detach from cron
        if os.fork(): return 0
        os.setsid()
        if os.fork(): os._exit(0)
        lk = lock()
        if lk is None: os._exit(0)
        fd = os.open(os.devnull, os.O_RDWR); os.dup2(fd, 0)
    loop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
