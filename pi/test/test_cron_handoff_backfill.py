"""python3 test/test_cron_handoff_backfill.py — 11-03：handoff backfill 进 cron，紧挨 handoff 扫描行；
同一个回滚开关 handoff-backfill-off 存在时 cron 不调 run.sh；不在扫描的分钟上（run.sh 按 job 名 flock，同分钟会被 skip）。"""
import os, subprocess, tempfile, importlib.util
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
s = importlib.util.spec_from_file_location("cron_sync", f"{R}/pi/bin/cron_sync.py"); C = importlib.util.module_from_spec(s); s.loader.exec_module(C)
t = open(f"{R}/pi/cron/pi-line.cron").read()
assert not C.validate(t)
jobs = [l for l in t.splitlines() if l.strip() and not l.lstrip().startswith("#") and not C.ENV.match(l.strip())]
scan = [i for i, l in enumerate(jobs) if l.split()[6:8] == ["$R", "handoff"] and "backfill" not in l]
bf = [i for i, l in enumerate(jobs) if "handoff" in l and l.rstrip().endswith(" backfill")]
assert scan and len(bf) == 1, (scan, bf)
assert bf[0] == max(scan) + 1, "backfill 行要紧挨 handoff 扫描行"
line = jobs[bf[0]]; f = line.split()
FLAG = "/workspace/mivo-qa/pi/handoff-backfill-off"
assert FLAG in line and f[0] not in {jobs[i].split()[0] for i in scan}, line
cmd = " ".join(f[6:])
# 真跑 cron 命令：$R 换成桩、开关路径换成临时文件
d = tempfile.mkdtemp(); stub, rec, flag = f"{d}/run.sh", f"{d}/called", f"{d}/off"
open(stub, "w").write(f'#!/bin/bash\necho "$@" >> {rec}\n'); os.chmod(stub, 0o755)
def run(): subprocess.run(["bash", "-c", cmd.replace(FLAG, flag)], env={**os.environ, "R": stub}, check=False)
open(flag, "w").close(); run(); assert not os.path.exists(rec), "开关在时不能调 run.sh"
os.remove(flag); run(); assert open(rec).read().split() == ["handoff", "@09", "backfill"], open(rec).read()
print("ok test_cron_handoff_backfill")
