"""python3 test/test_picall_sync.py — 10-28：~/tools/pi-call 以仓库 pi/restore/pi-call 为准；有漂移报出来，sync 装仓库版并备份旧版。"""
import os, sys, tempfile, importlib.util
B = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
s = importlib.util.spec_from_file_location("picall_sync", f"{B}/picall_sync.py"); M = importlib.util.module_from_spec(s); s.loader.exec_module(M)
d = tempfile.mkdtemp(); repo, live = f"{d}/repo", f"{d}/live"; os.makedirs(repo); os.makedirs(live)
open(f"{repo}/pi-auto", "w").write("work) t=high\n"); open(f"{live}/pi-auto", "w").write("work) t=medium\n")
open(f"{repo}/pi-run", "w").write("same\n"); open(f"{live}/pi-run", "w").write("same\n"); open(f"{repo}/pi-task", "w").write("new\n")
assert M.drift(repo, live) == ["pi-auto", "pi-task"]
assert M.sync(repo, live) == ["pi-auto", "pi-task"] and M.drift(repo, live) == []
assert open(f"{live}/pi-auto").read() == "work) t=high\n" and os.access(f"{live}/pi-auto", os.X_OK)
bak = [x for x in os.listdir(d) if x.startswith("live.bak-")]; assert len(bak) == 1 and open(f"{d}/{bak[0]}/pi-auto").read() == "work) t=medium\n"
assert M.sync(repo, live) == []
# 仓库自己的启动器：只用 tap provider（~/.pi/agent/models.json 只有 tap），work 档 high
R = M.REPO; assert "work)       m=tap/deepseek/deepseek-flash;       t=high" in open(f"{R}/pi-auto").read()
assert "tap/deepseek/deepseek-flash" in open(f"{R}/pi-run").read() and 'DEFAULT_MODEL = "tap/' in open(f"{R}/pi-task.mjs").read()
print("ok test_picall_sync")
