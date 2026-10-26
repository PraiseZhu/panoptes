"""python3 test/test_mergewatch_priority.py — 合并即测插队：mergewatch 对每个新合并的 PR 起 run-nightly.sh --priority（不等 catch-up.lock）。"""
import os, sys, json, tempfile, importlib.util
os.environ.setdefault("PI_STATE", tempfile.mkdtemp())
B = os.environ.get("PI_BIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
s = importlib.util.spec_from_file_location("mergewatch", f"{B}/mergewatch.py"); M = importlib.util.module_from_spec(s); s.loader.exec_module(M)
rd = tempfile.mkdtemp(); os.makedirs(f"{rd}/merged")
json.dump({}, open(f"{rd}/merged/PR-12.json", "w"))
pulls = [
    {"number": 14, "merged_at": "2026-10-26T16:05:00Z", "base": {"ref": "main"}},
    {"number": 13, "merged_at": "2026-10-26T16:01:00Z", "base": {"ref": "main"}},
    {"number": 12, "merged_at": "2026-10-26T16:02:00Z", "base": {"ref": "main"}},      # 已有报告
    {"number": 11, "merged_at": None, "base": {"ref": "main"}},                        # 关闭未合并
    {"number": 10, "merged_at": "2026-10-26T15:00:00Z", "base": {"ref": "main"}},      # since 之前
    {"number": 9, "merged_at": "2026-10-26T16:03:00Z", "base": {"ref": "feature"}},    # 不是 main
]
assert M.fresh_merges(pulls, "2026-10-26T15:30:00+00:00", rd) == [(13, "2026-10-27"), (14, "2026-10-27")]   # 旧的在前，上海日期
c = M.priority_cmd(13, "2026-10-27")
assert c[1] == "--priority" and c[c.index("--pr") + 1] == "13" and c[c.index("--trigger") + 1] == "event" and "--write-merged" in c and "--no-issues" in c
assert "--catch-up" not in c
# /proc 扫描：排队中的 run-nightly.sh --pr N 不重复起
pd = tempfile.mkdtemp(); os.makedirs(f"{pd}/42"); os.makedirs(f"{pd}/43"); os.makedirs(f"{pd}/self")
open(f"{pd}/42/cmdline", "wb").write(b"bash\0/workspace/panoptes/run-nightly.sh\0--priority\0--pr\0" + b"13\0")
open(f"{pd}/43/cmdline", "wb").write(b"python3\0x.py\0--pr\0" + b"99\0")
assert M.running_prs(pd) == {13}
src = open(f"{B}/mergewatch.py").read()
assert "fresh_merges(pulls, since)" in src and "if n in busy" in src
print("ok test_mergewatch_priority")
