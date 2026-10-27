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
J.BASE = tmp; os.makedirs(f"{tmp}/out/jasper"); os.makedirs(f"{tmp}/out/outbound")
def rec(job, day, a):   # like lib.record_actions: the pending action lands in out/jasper/<day>.actions.json
    acts.append((day, a)); json.dump({"actions": a}, open(f"{tmp}/out/jasper/{day}.actions.json", "w"))
J.record_actions = rec
def run(*argv): prompts.clear(); files.clear(); acts.clear(); sys.argv = ["jasper.py", *argv]; J.main()
run("2026-10-26")   # 补过去：只写文件，不发 DM
assert "2026-10-26.md" in files and "2026-10-26.facts.json" in files and acts == [], (files.keys(), acts)
assert "Today 2026-10-26, data day 2026-10-25" in prompts[0], prompts
run("2026-10-26"); run("2026-10-26"); assert acts == []   # (c) 过去的日子补跑两次也只写文件
run()               # 不带参数 = 今天，照常记外发
assert "2026-10-27.md" in files and acts and acts[0][1][0]["key"] == "brief:2026-10-27", acts
assert "data day 2026-10-26" in prompts[0]
run("2026-10-27")   # 显式给今天 = 正常
assert acts == []   # 已经记过 brief:2026-10-27（上面不带参数那次）→ 不再记
# 10-27 jasper 进 REPLAY：outage 补今天这轮 = run.sh jasper <今天>
def reset(): [os.remove(f"{tmp}/out/{x}") for x in ("jasper/2026-10-27.actions.json", "outbound/ledger.json") if os.path.exists(f"{tmp}/out/{x}")]
reset(); run("2026-10-27"); assert len(acts) == 1                                  # (a) brief 日期 = 今天 → 外发
sent = 1; run("2026-10-27"); sent += len(acts); assert sent == 1, acts           # 同一天补跑两次：只发一次
reset(); json.dump({"brief:2026-10-27": {"status": "done"}}, open(f"{tmp}/out/outbound/ledger.json", "w"))
run("2026-10-27"); assert acts == [] and files == {} and prompts == [], (acts, files)   # (b) 账本里已有 brief:<日> → 不调模型、不写文件、不发
# 用真 write_atomic 再验一次：已发过的 .md / .facts.json 内容不变
J.write_atomic = lambda p, t: open(p, "w").write(t)
for f, t in (("2026-10-27.md", "已发出的 brief\n"), ("2026-10-27.facts.json", "{}")): open(f"{tmp}/out/jasper/{f}", "w").write(t)
run("2026-10-27"); assert open(f"{tmp}/out/jasper/2026-10-27.md").read() == "已发出的 brief\n" and open(f"{tmp}/out/jasper/2026-10-27.facts.json").read() == "{}" and prompts == []
reset(); open(f"{tmp}/out/jasper/2026-10-27.actions.json", "w").write(json.dumps({"actions": [{"key": "brief:2026-10-27"}]}))
run("2026-10-27"); assert open(f"{tmp}/out/jasper/2026-10-27.md").read() == "已发出的 brief\n" and prompts == [] and acts == []   # 待发清单里有也一样
print("ok test_jasper_backfill")
