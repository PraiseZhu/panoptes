"""python3 test/test_cron_sync.py — 10-28：/etc/cron.d/pi-fallback 以仓库 pi/cron/pi-line.cron 为准；校验不过不装，装前备份，返回 diff。"""
import os, sys, tempfile, importlib.util
os.environ["PI_CRON_NOSUDO"] = "1"
B = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
s = importlib.util.spec_from_file_location("cron_sync", f"{B}/cron_sync.py"); M = importlib.util.module_from_spec(s); s.loader.exec_module(M)
# 仓库里的真表必须能过校验
assert M.validate(open(M.REPO).read()) == [], M.validate(open(M.REPO).read())
V = M.validate
assert V("SHELL=/bin/bash\n*/5 * * * * box echo hi\n") == []
assert V("7 */2 * * mon-fri box x\n@reboot box x\n0 9 1 jan * box x\n") == []
assert V("60 * * * * box x\n") and V("* * * * box x\n") and V("*/0 * * * * box x\n") and V("5-1 * * * * box x\n")
assert V("* * * * * box x") and V("# only comments\n") and V("@sometimes box x\n")
d = tempfile.mkdtemp(); repo, live, bak = f"{d}/repo.cron", f"{d}/live", f"{d}/bak"
open(repo, "w").write("* * * * * box new\n"); open(live, "w").write("* * * * * box old\n")
r = M.sync(repo, live, bak); assert r and r[0], r
assert "-* * * * * box old" in r[2] and "+* * * * * box new" in r[2]
assert open(live).read() == "* * * * * box new\n"
b = os.listdir(bak); assert len(b) == 1 and open(f"{bak}/{b[0]}").read() == "* * * * * box old\n"
assert M.sync(repo, live, bak) is None and not M.drift(repo, live)
# 校验不过：绝不安装、不备份
open(repo, "w").write("* * * * box broken\n")
r = M.sync(repo, live, bak); assert r and not r[0] and "未安装" in r[1]
assert open(live).read() == "* * * * * box new\n" and len(os.listdir(bak)) == 1
# 线上文件不存在：直接装
os.remove(live); open(repo, "w").write("@hourly box x\n"); r = M.sync(repo, live, bak); assert r[0] and open(live).read() == "@hourly box x\n"
print("ok test_cron_sync")
