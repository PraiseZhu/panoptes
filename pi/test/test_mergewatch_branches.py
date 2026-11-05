#!/usr/bin/env python3
"""11-03 integration-branch QA (mergewatch): any base except registry.skipBaseBranches (probe-*) is tested; integration/* and
release/* heads are watched by ls-remote; every 10 minutes the closed-PR list (per_page=100) is polled even when no ref moved,
so merges into feat/* are found; main is launched first; PANOPTES_MAIN_ONLY=1 restores main only. No network, nothing launched."""
import os, sys, json, tempfile, time, types, unittest, importlib.util
from unittest import mock
os.environ.setdefault("PI_STATE", tempfile.mkdtemp())
B = os.environ.get("PI_BIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
s = importlib.util.spec_from_file_location("mergewatch_b", f"{B}/mergewatch.py"); M = importlib.util.module_from_spec(s); s.loader.exec_module(M)
MAIN = "a" * 40

def pull(n, base, at="2026-11-02T09:00:00Z"): return {"number": n, "merged_at": at, "base": {"ref": base}}


class T(unittest.TestCase):
    def setUp(self):
        self.rd = tempfile.mkdtemp(); os.makedirs(f"{self.rd}/merged"); M.REPORTS = self.rd
        os.environ.pop("PANOPTES_MAIN_ONLY", None)

    def tick(self, st, ls_out, pulls, env=None, dry=True):
        """one main() tick in DRY mode; returns (state after, log lines, ls-remote argv, fetch count)"""
        lines, argv, fetched, saved = [], [], [], {}
        def run(cmd, **k):
            argv.append(cmd); return types.SimpleNamespace(returncode=0, stdout=ls_out, stderr="")
        def fetch(tok): fetched.append(1); return pulls
        with mock.patch.object(M, "DRY", dry), mock.patch.object(M, "log", lines.append), mock.patch.object(M, "load", lambda: json.loads(json.dumps(st))), \
             mock.patch.object(M, "save", lambda x: saved.update(x)), mock.patch.object(M, "running_prs", lambda: set()), mock.patch.object(M, "heartbeat", lambda m: None), \
             mock.patch.object(M, "catchup_running", lambda: True), mock.patch.object(M, "fetch_pulls", fetch), mock.patch.object(M.subprocess, "run", run), \
             mock.patch.dict(os.environ, {"PI_FLEET_MODE": "pi", "GITHUB_TOKEN": "t", "PI_LOCK_DIR": tempfile.mkdtemp(), **(env or {})}):
            M.main()
        return saved, lines, argv[0], len(fetched)

    def test_fresh_merges_tests_every_base_but_probe(self):
        pulls = [pull(1379, "feat/video-in-canvas"), pull(1601, "integration/canvas-s2", "2026-11-02T09:01:00Z"), pull(1410, "probe-1410/status/base")]
        self.assertEqual([n for n, _ in M.fresh_merges(pulls, "2026-11-02T08:00:00+00:00")], [1379, 1601])

    def test_fresh_merges_main_first(self):
        pulls = [pull(1601, "integration/canvas-s2", "2026-11-02T08:00:00Z"), pull(1700, "main", "2026-11-02T09:00:00Z"), pull(1379, "feat/x", "2026-11-02T08:30:00Z")]
        self.assertEqual([n for n, _ in M.fresh_merges(pulls, "2026-11-02T07:00:00+00:00")], [1700, 1601, 1379])

    def test_requeue_main_first(self):
        inf = {"1601": {"day": "d", "tries": 1, "base": "integration/canvas-s2"}, "1700": {"day": "d", "tries": 1, "base": "main"}}
        self.assertEqual([n for n, _ in M.requeue(inf, busy=set(), reports=self.rd, t=time.time())], [1700, 1601])

    def test_per_page_100(self):
        self.assertIn("per_page=100", M.API_PULLS)

    def test_poll_every_10_min_without_ref_move_finds_feat_merge(self):
        ls = f"{MAIN}\trefs/heads/main\n"
        since = "2026-11-02T08:00:00+00:00"
        st = {"main_sha": MAIN, "ref_shas": {"refs/heads/main": MAIN}, "merged_seen": since, "last_poll_ts": time.time() - 601}
        saved, lines, _, n = self.tick(st, ls, [pull(1379, "feat/video-in-canvas")])
        self.assertEqual(n, 1)
        self.assertTrue(any("would launch" in l and "--pr 1379" in l for l in lines), lines)
        # inside the 10 minutes and nothing moved: no REST call
        st["last_poll_ts"] = time.time() - 60
        _, lines, _, n = self.tick(st, ls, [pull(1379, "feat/video-in-canvas")])
        self.assertEqual(n, 0)

    def test_integration_ref_move_marks_dirty(self):
        old, new = "b" * 40, "c" * 40
        st = {"main_sha": MAIN, "ref_shas": {"refs/heads/main": MAIN, "refs/heads/integration/canvas-s2": old}, "last_poll_ts": time.time()}
        saved, lines, argv, _ = self.tick(st, f"{MAIN}\trefs/heads/main\n{new}\trefs/heads/integration/canvas-s2\n", [])
        self.assertIn("refs/heads/integration/*", argv); self.assertIn("refs/heads/release/*", argv)
        self.assertTrue(saved.get("dirty"))
        self.assertTrue(any("ref moved integration/canvas-s2" in l for l in lines), lines)
        self.assertEqual(saved["main_sha"], MAIN)   # old field kept

    def test_branch_merge_launch_logs_real_base_and_merge_sha(self):
        """11-05 #1740: an integration/* merge was logged `main 6a427967`; label = base + merge sha, kept on the requeue (try 2)"""
        old, new, msha = "b" * 40, "c" * 40, "87a87859" + "d" * 32
        st = {"main_sha": MAIN, "ref_shas": {"refs/heads/main": MAIN, "refs/heads/integration/foo": old}, "merged_seen": "2026-11-02T08:00:00+00:00", "last_poll_ts": time.time()}
        launched = []
        def launch(cmd, sha, env=None, base="main"): launched.append((base, sha)); return types.SimpleNamespace(pid=7)
        p = dict(pull(1740, "integration/foo"), merge_commit_sha=msha)
        with mock.patch.object(M, "launch", launch):
            saved, lines, _, _ = self.tick(st, f"{MAIN}\trefs/heads/main\n{new}\trefs/heads/integration/foo\n", [p], dry=False)
            self.assertTrue(any("branch merge #1740 into integration/foo" in l for l in lines), lines)
            self.assertIn("launched priority run pid 7 for PR #1740 (integration/foo 87a87859, try 1/3)", lines)
            self.assertEqual(launched, [("integration/foo", msha)])
            saved["inflight"]["1740"]["next_at"] = 0   # try 1 died: requeue
            with mock.patch.object(M, "requeue", lambda inf, busy: [(1740, "d")]):
                _, lines, _, _ = self.tick(saved, f"{MAIN}\trefs/heads/main\n{new}\trefs/heads/integration/foo\n", [], dry=False)
            self.assertIn("launched priority run pid 7 for PR #1740 (integration/foo 87a87859, try 2/3)", lines)
            self.assertNotIn(MAIN[:8], " ".join(l for l in lines if "launched" in l))

    def test_main_only_rollback(self):
        pulls = [pull(1379, "feat/video-in-canvas"), pull(1700, "main")]
        with mock.patch.dict(os.environ, {"PANOPTES_MAIN_ONLY": "1"}):
            self.assertEqual([n for n, _ in M.fresh_merges(pulls, "2026-11-02T08:00:00+00:00")], [1700])
        st = {"main_sha": MAIN, "last_poll_ts": time.time()}
        _, _, argv, _ = self.tick(st, f"{MAIN}\trefs/heads/main\n", [], env={"PANOPTES_MAIN_ONLY": "1"})
        self.assertEqual(argv[-1], "refs/heads/main"); self.assertNotIn("refs/heads/integration/*", argv)

if __name__ == "__main__":
    unittest.main()
