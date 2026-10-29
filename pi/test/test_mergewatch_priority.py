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
assert "fresh_merges(pulls, since)" in src and "n not in busy and str(n) not in inflight" in src
# 10-29 重排：结束了没报告（失败 / 超时 / exit 76）→ 立刻重排；在跑的不重复；有报告的删掉；超过 MAX_TRIES 交给 catch-up
inf = {"13": {"day": "d13", "tries": 1}, "20": {"day": "d20", "tries": 1}, "12": {"day": "d12", "tries": 1}, "21": {"day": "d21", "tries": M.MAX_TRIES}}
assert M.requeue(inf, busy={20}, reports=rd) == [(13, "d13")]
assert set(inf) == {"13", "20"}, inf                                 # 12 有报告、21 次数用完 → 移出
assert M.requeue(inf, busy={13, 20}, reports=rd) == []              # 重排后在跑 → 不再起（幂等）
# 退避：next_at 没到不重排，到了才排
inf = {"13": {"day": "d13", "tries": 1, "next_at": 1000}}
assert M.requeue(inf, busy=set(), reports=rd, t=999) == [] and M.requeue(inf, busy=set(), reports=rd, t=1000) == [(13, "d13")]
# reports 默认值在调用时取 REPORTS（不是导入时绑定）
M.REPORTS = rd; assert M.requeue({"12": {"day": "d", "tries": 1}}, busy=set()) == []
assert M.fresh_merges(pulls, "2026-10-26T15:30:00+00:00") == [(13, "2026-10-27"), (14, "2026-10-27")]
# ls-remote 失败也照样重排：requeue 在 ls-remote 之前
assert src.index("requeue(inflight, busy)") < src.index("'ls-remote'")
os.environ["GITHUB_TOKEN"] = "t_env"; assert M.gh_token() == "t_env"   # 原凭证，不用 App
assert "ghapp" not in src and "praiseautomation.json" not in src
print("ok test_mergewatch_priority")
