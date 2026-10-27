#!/usr/bin/env python3
"""python3 test/test_boxhealth_balloon.py -- 10-27: boxhealth reads the host balloon from /proc/vmstat ((inflate - deflate) pages)
and tags low-memory alerts cause=balloon|process. Fake vmstat only; never reads the real one."""
import os, sys, io, tempfile
T = tempfile.mkdtemp(prefix="boxhealth-balloon-"); os.environ.update(PI_STATE=f"{T}/state", HOME=f"{T}/home")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import boxhealth as B   # noqa: E402
def vm(text):
    B.open = lambda p, *a, **k: io.StringIO(text) if p == "/proc/vmstat" else open(p, *a, **k)
pg = os.sysconf("SC_PAGE_SIZE")
vm(f"nr_free_pages 1\nballoon_inflate {64896512}\nballoon_deflate {63323648}\nballoon_migrate 6158900\n")   # box 10-27: ~6 GB held
assert B.balloon_mb() == (64896512 - 63323648) * pg // 2**20 == (6144 if pg == 4096 else B.balloon_mb()), B.balloon_mb()
vm("balloon_inflate 10\nballoon_deflate 20\n"); assert B.balloon_mb() == 0          # never negative
vm("nr_free_pages 1\n"); assert B.balloon_mb() == 0                                    # no balloon driver -> 0
vm("balloon_inflate x\n"); assert B.balloon_mb() == -1                                 # unreadable -> -1
del B.open
assert B.mem_cause(6144, 3000) == "balloon"      # host holds 6G, processes only 3G
assert B.mem_cause(6144, 7000) == "process"      # processes explain it
assert B.mem_cause(2047, 100) == "process"       # balloon < 2G never blamed
assert B.mem_cause(-1, 0) == "process"           # unreadable vmstat -> process
print("ok test_boxhealth_balloon")
