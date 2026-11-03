"""opsreview.py:45,50 (11-03): the group post and the action's dedupe_basis still named /workspace/review-ops/daily/<日>.md as the
formal review (「正式版仍写 …（主持人）」). Since 10-29 the formal review is Pi opsreview's own out/ops/review-<日>.md
(review-ops/bin/daily_review.py R13, 11-03); review-ops/daily/ stopped at 2026-10-29.md. Temp PI_STATE only, nothing real is read."""
import os, sys, tempfile, importlib.util, types, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import isolated, assert_inside, BIN  # noqa: E402

class T(unittest.TestCase):
    def test_post_and_action_name_the_current_formal_path(self):
        tmp = tempfile.mkdtemp(); os.makedirs(f"{tmp}/out/ops")
        with isolated(PI_STATE=tmp):
            sys.path.insert(0, BIN)
            s = importlib.util.spec_from_file_location("opsreview", f"{BIN}/opsreview.py"); M = importlib.util.module_from_spec(s); s.loader.exec_module(M)
            M.BASE = tmp; assert_inside(tmp, f"{M.BASE}/out")
            acts = []
            def fake_run(cmd, **kw): open(cmd[cmd.index("--out") + 1], "w").write("合并 PR 2 个\n"); return types.SimpleNamespace(returncode=0, stderr="")
            M.subprocess = types.SimpleNamespace(run=fake_run)
            M.merged_prs = lambda day: []
            M.record_actions = lambda job, day, a: acts.extend(a)
            M.main(["opsreview.py", "2026-11-02"])
            out = f"{tmp}/out/ops/review-2026-11-02.md"
            post = open(f"{tmp}/out/ops/review-2026-11-02.post.md").read()
            self.assertIn(f"正式版就是 {out}", post)
            self.assertNotIn("/workspace/review-ops/daily/2026-11-02.md", post)
            self.assertNotIn("主持人", post)
            self.assertEqual(len(acts), 1)
            db = acts[0]["dedupe_basis"]
            self.assertIn(out, db); self.assertIn("ops-review:2026-11-02", db)
            self.assertNotIn("/workspace/review-ops/daily/2026-11-02.md exists", db)

if __name__ == "__main__":
    unittest.main()
