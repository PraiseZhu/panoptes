"""python3 test/test_no_create_issues.py — catch-up 不许直接开 issue（--create-issues 绕过外发闸，Praise 10-26）。
catch-up cron 源文件和 mergewatch 的启动命令都不能带 --create-issues / ARGUS_ALLOW_ISSUES。"""
import os
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
cron = open(f"{R}/scripts/catch-up.cron").read(); mw = open(f"{R}/pi/bin/mergewatch.py").read()
for name, s in (("scripts/catch-up.cron", cron), ("pi/bin/mergewatch.py", mw)):
    assert "--create-issues" not in s.replace("no --create-issues", ""), name
    assert "ARGUS_ALLOW_ISSUES" not in s, name
assert "--catch-up" in cron and "'--catch-up'" in mw
print("ok test_no_create_issues")
