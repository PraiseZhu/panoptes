"""python3 test/test_cron_tz.py — 两个 cron 文件都声明 CRON_TZ=Asia/Shanghai（在任何任务行之前），且仍能通过 cron_sync 语法校验；
pi-line 的任务行小时字段全是 *（北京时由 run.sh @HH 判，所以加 CRON_TZ 不改触发时刻）；catch-up 仍是 '7 */2'（北京偶数点 :07）。"""
import os, sys, importlib.util
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
s = importlib.util.spec_from_file_location("cron_sync", f"{R}/pi/bin/cron_sync.py"); C = importlib.util.module_from_spec(s); s.loader.exec_module(C)
def jobs(t): return [l.split() for l in t.splitlines() if l.strip() and not l.lstrip().startswith("#") and not C.ENV.match(l.strip())]
for f in ("pi/cron/pi-line.cron", "scripts/catch-up.cron"):
    t = open(f"{R}/{f}").read(); L = [l for l in t.splitlines() if l.strip() and not l.startswith("#")]
    assert L[0] == "CRON_TZ=Asia/Shanghai", (f, L[0])
    assert not C.validate(t.replace("@USER@", "box").replace("@QA_HOME@", "/q").replace("@PANOPTES_DIR@", "/p")), (f, C.validate(t))
assert all(j[1] == "*" for j in jobs(open(f"{R}/pi/cron/pi-line.cron").read()))
assert [j[:2] for j in jobs(open(f"{R}/scripts/catch-up.cron").read())] == [["7", "*/2"]]
print("ok test_cron_tz")
