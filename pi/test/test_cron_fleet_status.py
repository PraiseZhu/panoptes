"""python3 test/test_cron_fleet_status.py — fleet_status 三段接线缺一不可（ORG.md 第 7 条：每 30 分钟出 /workspace/fleet-standards/fleet-status.json/.md）：
仓库 cron 里有这条调度、run.sh 的 LIGHT 里有这个作业名（低内存照跑）、qa-fleet.json 把它注册在 ops lane。11-16：脚本和 LIGHT 都在，
cron 少这一行 -> 摘要一直不生成、也没有 fleet_status 回执；本测试就是那次缺口的回归闸。只读仓库文件，不跑生产路径。"""
import json, os, re, sys, importlib.util
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
s = importlib.util.spec_from_file_location("cron_sync", f"{R}/pi/bin/cron_sync.py"); C = importlib.util.module_from_spec(s); s.loader.exec_module(C)
cron = open(f"{R}/pi/cron/pi-line.cron").read()

# 1) 调度：恰好一行 `$R fleet_status`，且是 */30（不是每小时、不是被注释掉）
assert not C.validate(cron), C.validate(cron)
lines = [l for l in cron.splitlines() if re.search(r"\$R\s+fleet_status\b", l)]
assert len(lines) == 1, lines
fields = lines[0].split()
assert fields[:5] == ["*/30", "*", "*", "*", "*"] and fields[6:] == ["$R", "fleet_status"], fields

# 2) 低内存也要跑：作业名必须在 run.sh 的 LIGHT 里（否则内存 <3G 时会被跳过 10 分钟然后放弃）
sh = open(f"{R}/pi/bin/run.sh").read()
light = re.search(r'^LIGHT="(.*)"$', sh, re.M).group(1).split()
assert "fleet_status" in light, light
assert os.path.exists(f"{R}/pi/bin/fleet_status.py")

# 3) 标准接线：qa-fleet.json 声明它由本 cron 调度（lane 用于回执归位）
fleet = json.load(open(f"{R}/pi/conf/qa-fleet.json"))
entries = [(lane, e) for lane, spec in fleet["lanes"].items() for e in spec.get("entries", []) if e["job"] == "fleet_status"]
assert len(entries) == 1, entries
lane, e = entries[0]
assert lane == "ops" and e["path"] == "pi/bin/fleet_status.py" and e["scheduler"] == "pi/cron/pi-line.cron", e
print("ok test_cron_fleet_status")
