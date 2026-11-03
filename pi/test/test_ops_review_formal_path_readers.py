"""jasper.py:36 and compare.py:51 (11-03): both still read /workspace/review-ops/daily/ as the 运维 daily review. Since 10-29 the formal
review is Pi opsreview's $PI_STATE/out/ops/review-<day>.md (review-ops/bin/daily_review.py:22-24, R13); review-ops/daily/ stopped at
2026-10-29.md (legacy hand-written fallback). Same convention as #176 (opsreview.py). Temp PI_STATE only: every /workspace/ path the
modules read is redirected into the temp dir, nothing real is read or written, no model, no outbound."""
import glob as _glob, json, os, sys, tempfile, importlib.util, types, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import isolated, BIN  # noqa: E402

DAY, RUN = "2026-11-02", "2026-11-03"

def load(name):
    sys.path.insert(0, BIN)
    s = importlib.util.spec_from_file_location(name, f"{BIN}/{name}.py"); M = importlib.util.module_from_spec(s); s.loader.exec_module(M); return M

class T(unittest.TestCase):
    def setUp(self):
        self.tmp = t = tempfile.mkdtemp(prefix="ops-formal-readers-")
        os.makedirs(f"{t}/out/ops"); os.makedirs(f"{t}/ws/review-ops/daily")
        for f in (f"review-{DAY}.md", f"review-{DAY}.post.md", f"review-{DAY}.dryrun.md", "review-2026-11-01.md", f"review-{DAY}.json"):
            open(f"{t}/out/ops/{f}", "w").write("# 复盘\n")
        open(f"{t}/ws/review-ops/daily/2026-10-29.md", "w").write("legacy\n")   # the legacy dir's last file, as on the box
    def redirect(self, p):   # /workspace/... -> <tmp>/ws/... (hermetic: CI has no /workspace, the box's real files are never read)
        return p.replace("/workspace/", f"{self.tmp}/ws/") if p.startswith("/workspace/") else p

    def test_jasper_review_ops_latest_is_formal_path(self):
        t = self.tmp
        with isolated(PI_STATE=t):
            J = load("jasper"); J.BASE = J.QA = t
            os.makedirs(f"{t}/report/slack"); json.dump({}, open(f"{t}/report/slack/sent.json", "w"))
            os.makedirs(f"{t}/ledger/daily"); open(f"{t}/ledger/daily/{DAY}.json", "w").write("{}")
            J.glob = types.SimpleNamespace(glob=lambda p: _glob.glob(self.redirect(p)))
            J.mode, J.today = (lambda: "shadow"), (lambda: RUN)
            files = {}
            J.write_atomic = lambda p, x: files.__setitem__(os.path.basename(p), x)
            J.pi = lambda *a, **k: ("brief", "t1"); J.record_actions = lambda *a: None
            sys.argv = ["jasper.py", RUN]; J.main()
            facts = json.loads(files[f"{RUN}.facts.json"])
            self.assertEqual(facts["Mivo QA 运维"]["review_ops_latest"], f"{t}/out/ops/review-{DAY}.md")
            os.remove(f"{t}/out/ops/review-{DAY}.md"); os.remove(f"{t}/out/ops/review-2026-11-01.md"); files.clear()   # only .post/.dryrun/.json left
            J.main(); self.assertIsNone(json.loads(files[f"{RUN}.facts.json"])["Mivo QA 运维"]["review_ops_latest"])   # none: None, no IndexError

    def test_jasper_prompt_does_not_call_formal_review_stale(self):
        # #183 gap 2 (11-03): the pi-mode rule still listed "review-ops" among the Grok-line fields "expected to be stale", and the
        # shadow-mode rule called every $PI_STATE path shadow -- both told the model to discount the formal out/ops/review-<day>.md (R13)
        t = self.tmp
        with isolated(PI_STATE=t):
            J = load("jasper"); J.BASE = J.QA = t
            os.makedirs(f"{t}/report/slack"); json.dump({}, open(f"{t}/report/slack/sent.json", "w"))
            os.makedirs(f"{t}/ledger/daily"); open(f"{t}/ledger/daily/{DAY}.json", "w").write("{}")
            J.glob = types.SimpleNamespace(glob=lambda p: _glob.glob(self.redirect(p)))
            J.age = lambda p: None; J.panoptes_backlog = lambda tk: {}; J.today = lambda: RUN
            J.write_atomic = lambda p, x: None; J.record_actions = lambda *a: None
            os.makedirs(f"{t}/ws/grokbot-box-check"); open(f"{t}/ws/grokbot-box-check/sync.log", "w").close()   # read only if the real one exists
            J.jload_ = lambda f, d: d; J.open = lambda f, *a, **k: open(self.redirect(f), *a, **k)
            prompts = []; J.pi = lambda tier, cwd, prompt, *a, **k: (prompts.append(prompt), ("brief", "t1"))[1]
            for m in ("pi", "shadow"):
                J.mode = lambda m=m: m; sys.argv = ["jasper.py", RUN]; J.main()
                rule = prompts[-1].split("Never guess;")[1]
                self.assertNotIn("review-ops, ", rule, m)
                self.assertIn("out/ops/review-<day>.md", rule, m); self.assertIn("review_ops_latest", rule, m)

    def test_compare_uses_formal_review_when_no_legacy_file(self):
        t = self.tmp
        with isolated(PI_STATE=t):
            C = load("compare"); C.BASE = C.QA = t
            rd = C.rd; C.rd = lambda p: rd(self.redirect(p))
            C.glob = types.SimpleNamespace(glob=lambda p: _glob.glob(self.redirect(p)))
            out = {}; C.write_atomic = lambda p, x: out.__setitem__(p, x)
            sys.argv = ["compare.py", DAY]; C.main()
            row = next(l for l in out[f"{t}/out/compare/{DAY}.md"].splitlines() if l.startswith("| 运维复盘"))
            self.assertIn(f"out/ops/review-{DAY}.md", row); self.assertIn("正式版在", row); self.assertNotIn("无法比较", row)
            open(f"{t}/ws/review-ops/daily/{DAY}.md", "w").write("校准为 P2\n")   # a legacy hand-written file still gets compared
            C.main()
            row = next(l for l in out[f"{t}/out/compare/{DAY}.md"].splitlines() if l.startswith("| 运维复盘"))
            self.assertIn(f"review-ops/daily/{DAY}.md", row); self.assertIn("Grok 校准 1 条", row)

if __name__ == "__main__":
    unittest.main()
