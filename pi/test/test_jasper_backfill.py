"""python3 test/test_jasper_backfill.py — jasper 接受日期参数；补过去的日子只写文件、不记外发（10-27 补 10-26/10-27 的 brief）。
不跑模型、不外发（pi / record_actions / write_atomic 全部注入）。"""
import os, sys, json, tempfile, types, importlib.util
tmp = tempfile.mkdtemp(); os.environ["PI_STATE"] = tmp
B = os.environ.get("PI_BIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
s = importlib.util.spec_from_file_location("jasper", f"{B}/jasper.py"); J = importlib.util.module_from_spec(s); s.loader.exec_module(J)
os.makedirs(f"{tmp}/report/slack"); json.dump({}, open(f"{tmp}/report/slack/sent.json", "w"))
J.QA = tmp; J.glob = types.SimpleNamespace(glob=lambda p: ["x"]); J.mode = lambda: "shadow"; J.today = lambda: "2026-10-27"
prompts, files, acts = [], {}, []
J.pi = lambda tier, cwd, prompt, sess, timeout=0: (prompts.append(prompt) or "brief", "t1")
J.write_atomic = lambda p, t: files.__setitem__(os.path.basename(p), t)
J.record_actions = lambda job, day, a: acts.append((day, a))
def run(*argv): prompts.clear(); files.clear(); acts.clear(); sys.argv = ["jasper.py", *argv]; J.main()
run("2026-10-26")   # 补过去：只写文件，不发 DM
assert "2026-10-26.md" in files and "2026-10-26.facts.json" in files and acts == [], (files.keys(), acts)
assert "Today 2026-10-26, data day 2026-10-25" in prompts[0], prompts
run()               # 不带参数 = 今天，照常记外发
assert "2026-10-27.md" in files and acts and acts[0][1][0]["key"] == "brief:2026-10-27", acts
assert "data day 2026-10-26" in prompts[0]
run("2026-10-27")   # 显式给今天 = 正常
assert acts and acts[0][0] == "2026-10-27"
print("ok test_jasper_backfill")
