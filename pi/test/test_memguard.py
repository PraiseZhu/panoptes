#!/usr/bin/env python3
"""python3 test/test_memguard.py -- memguard phase 1: observe only, fake /proc, temp dir, never signals anything."""
import os, sys, json, tempfile, time, signal, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import isolated  # noqa: E402

def script():   # was module level (ran at pytest collection and leaked env / frozen paths into other tests)
    T = tempfile.mkdtemp(prefix="memguard-"); os.environ.update(MEMGUARD_DIR=f"{T}/mg", MEMGUARD_PROC=f"{T}/proc", PI_STATE=f"{T}/state", HOME=f"{T}/home")
    os.makedirs(f"{T}/proc/pressure")
    open(f"{T}/proc/meminfo", "w").write("MemTotal: 16386048 kB\nMemFree: 512000 kB\nMemAvailable: 3072000 kB\nSwapTotal: 8387584 kB\nSwapFree: 4194304 kB\n")
    open(f"{T}/proc/pressure/memory", "w").write("some avg10=1.50 avg60=0.10 avg300=0.00 total=1\nfull avg10=0.25 avg60=0.00 avg300=0.00 total=1\n")
    open(f"{T}/proc/vmstat", "w").write("balloon_inflate 2000\nballoon_deflate 1000\n")
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
    import memguard as M   # noqa: E402
    killed = []; os.kill = lambda *a: killed.append(a); os.killpg = lambda *a: killed.append(a)
    t = {1: (0, 5, b"tini\0"), 10: (1, 100, b"/usr/bin/python3\0/x/bin/pi_dispatch.py\0--run-one\0judge:abc\0"),
         11: (10, 900, b"node\0pi\0"), 20: (1, 50, b"bash\0/workspace/panoptes/run-nightly.sh\0"), 21: (20, 2000, b"node\0qa\0")}
    s = M.sample(t, now=time.time())
    assert s["mem_avail_mb"] == 3000 and s["swap_free_mb"] == 4096 and s["mem_free_mb"] == 500, s
    assert s["psi"] == {"some_avg10": 1.5, "full_avg10": 0.25}, s["psi"]
    assert s["balloon_mb"] == 1000 * os.sysconf("SC_PAGE_SIZE") // 2**20
    k = {x["kind"]: x for x in s["pi_tasks"]}
    assert k["dispatch"]["rss_mb"] == 1000 and k["dispatch"]["spec"] == "judge:abc" and k["panoptes"]["rss_mb"] == 2050, s["pi_tasks"]
    assert s["top"][0]["pid"] == 21 and s["mode"] == "observe"
    # missing PSI / balloon -> None / 0, no crash
    os.remove(f"{T}/proc/pressure/memory"); os.remove(f"{T}/proc/vmstat")
    assert M.psi() == {"some_avg10": None, "full_avg10": None} and M.balloon_mb() == -1
    # loop writes N lines, rotates old files, stops on disable file
    os.makedirs(M.DIR, exist_ok=True)
    old = os.path.join(M.DIR, "obs-20000101.jsonl"); open(old, "w").write("{}\n"); os.utime(old, (0, 0))
    M.loop(max_iter=3, sleep=lambda s: None)
    lines = open(M.path_for(time.time())).read().splitlines()
    assert len(lines) == 3 and all(json.loads(l)["mode"] == "observe" for l in lines) and not os.path.exists(old)
    # single instance: second lock fails while first held
    a = M.lock(); assert a is not None and M.lock() is None; a.close(); b = M.lock(); assert b is not None; b.close()
    # rollback: disable file stops cron starts and the loop
    M.rollback(); assert os.path.exists(M.DISABLE) and M.main([]) == 0
    n = len(open(M.path_for(time.time())).read().splitlines()); M.loop(max_iter=2, sleep=lambda s: None)
    assert len(open(M.path_for(time.time())).read().splitlines()) == n
    assert not killed, killed   # observe only: never signalled anything (rollback found no live pidfile process)
    print("ok test_memguard")

class Script(unittest.TestCase):
    def test_memguard(self):
        with isolated(): script()   # own env, argv, os.kill and fresh pi/bin imports; all restored afterwards

if __name__ == "__main__":
    unittest.main()
