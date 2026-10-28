"""python3 test/test_opsreview_dryrun.py — opsreview --dry-run：不调模型、不记外发动作、不碰正式产出文件。"""
import os, sys, tempfile, importlib.util, types
tmp = tempfile.mkdtemp(); os.environ["PI_STATE"] = tmp; os.makedirs(f"{tmp}/out/ops", exist_ok=True)
B = os.environ.get("PI_BIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
s = importlib.util.spec_from_file_location("opsreview", f"{B}/opsreview.py"); M = importlib.util.module_from_spec(s); s.loader.exec_module(M)
M.BASE = tmp
calls = {"pi": 0, "actions": 0, "collector": []}
def fake_run(cmd, **kw):
    calls["collector"].append(cmd); open(cmd[cmd.index("--out") + 1], "w").write("合并 PR 2 个\n"); return types.SimpleNamespace(returncode=0, stderr="")
M.subprocess = types.SimpleNamespace(run=fake_run)
M.merged_prs = lambda day: [{"number": 1385}]
M.load_report = lambda n: ("PR-1385.json", {"pr": {"merge_sha": "abc", "findings": [{"source": "code-review", "severity": "P1", "title": "t"}]}})
M.src_tree = lambda sha: tmp
def fake_pi(*a, **k): calls["pi"] += 1; return ("t → 维持P1：x", "task1")
M.pi = fake_pi
M.record_actions = lambda *a, **k: calls.__setitem__("actions", calls["actions"] + 1)

M.main(["opsreview.py", "2026-10-27", "--dry-run"])
assert calls["pi"] == 0 and calls["actions"] == 0, calls
assert calls["collector"][0][calls["collector"][0].index("--date") + 1] == "2026-10-27"
assert os.path.exists(f"{tmp}/out/ops/review-2026-10-27.dryrun.md") and os.path.exists(f"{tmp}/out/ops/review-2026-10-27.dryrun.post.md")
assert not os.path.exists(f"{tmp}/out/ops/review-2026-10-27.md") and not os.path.exists(f"{tmp}/out/ops/review-2026-10-27.post.md")
assert "[dry-run]" in open(f"{tmp}/out/ops/review-2026-10-27.dryrun.md").read()

os.environ["PI_OPSREVIEW_DRYRUN"] = "1"; M.main(["opsreview.py", "2026-10-26"]); del os.environ["PI_OPSREVIEW_DRYRUN"]
assert calls["pi"] == 0 and calls["actions"] == 0, calls

M.main(["opsreview.py", "2026-10-27"])                       # 正常模式照旧：调模型、记一条外发
assert calls["pi"] == 1 and calls["actions"] == 1, calls
assert os.path.exists(f"{tmp}/out/ops/review-2026-10-27.md")
print("ok test_opsreview_dryrun")
