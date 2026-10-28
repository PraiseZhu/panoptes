#!/usr/bin/env python3
"""python3 test/test_boxhealth_bigproc.py -- 10-28: memory alert at < 8G, and the hourly long-lived big-process scan
(elapsed > 30 min, RSS > 1G, not whitelisted; report only). Fake process table only; never reads the real ps."""
import os, sys, tempfile
T = tempfile.mkdtemp(prefix="boxhealth-bigproc-"); os.environ.update(PI_STATE=f"{T}/state", HOME=f"{T}/home")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import boxhealth as B   # noqa: E402
G = 4800; H = 4 * 3600
def p(pid, ppid, comm, args, rss=G, et=H): return {"pid": pid, "ppid": ppid, "rss_mb": rss, "etimes": et, "comm": comm, "args": args}
rows = [p(1, 0, "tini", "/sbin/tini", 1, H),
        p(10, 1, "sand-exit-watch", "/usr/local/bin/sand-exit-watch", 5),
        p(11, 10, "grep", "grep -r " + "x" * 300 + " /workspace/panoptes"),          # the 10-28 orphan: must be reported
        p(20, 1, "bash", "bash /workspace/panoptes/scripts/catch-up.sh", 5),
        p(21, 20, "node", "node npx", 5), p(22, 21, "node", "node /x/node_modules/.bin/vitest run"),   # vitest under catch-up
        p(30, 1, "cindy", "/usr/lib/cindy/cindy --type=renderer"),
        p(40, 1, "sand-foo", "/usr/local/bin/sand-foo"),
        p(50, 1, "earlyoom", "/usr/bin/earlyoom -m 5"),
        p(60, 1, "node", "node /opt/exec-daemon/main.js"),
        p(70, 1, "Xvfb", "Xvfb :1"),
        p(80, 1, "python3", "python3 leak.py"),                                       # fake big long-lived process
        p(81, 1, "python3", "python3 young.py", et=600),                              # too young
        p(82, 1, "python3", "python3 small.py", rss=900)]                             # too small
got = {b["pid"]: b for b in B.big_procs(rows)}
assert set(got) == {11, 80}, sorted(got)
g = got[11]; assert g["parent"] == "10:sand-exit-watch" and len(g["args"]) == 120 and g["rss_mb"] == G and g["etimes"] == H, g
assert B.MEM_LIMIT == 8192
log = f"{T}/mem.log"
def streak(avails):
    open(log, "w").write("".join(f"Mem: 1 2 3 4 5 {a}\n" for a in avails))
    real = B.subprocess.run
    B.subprocess.run = lambda c, **k: real(["cat", log], **k) if c[0] == "tail" else real(c, **k)
    try: return B.low_streak()
    finally: B.subprocess.run = real
assert streak([9000] + [4600] * 6) == 6          # 4.6G (10-28) now counts as low
assert streak([4600] * 3 + [8192] * 2) == 0      # >= 8G is fine
assert streak([2000] * 4) == 4
print("ok test_boxhealth_bigproc")
