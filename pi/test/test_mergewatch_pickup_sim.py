#!/usr/bin/env python3
"""11-04 (FINDINGS late-1609-1618): simulate cron's once-a-minute mergewatch ticks and assert a merge into integration/* or feat/*
is launched within 10 minutes, including when one closed-PR fetch fails (URLError, seen 11-03 15:27/16:46/18:45 and 11-04 00:46).
On main the failed fetch still stamped last_poll_ts, so a feat/* merge waited another full POLL_S (worst case ~20 min).
No network, nothing launched (DRY)."""
import os, sys, json, tempfile, types, unittest, importlib.util, urllib.error
from unittest import mock
os.environ.setdefault("PI_STATE", tempfile.mkdtemp())
B = os.environ.get("PI_BIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
s = importlib.util.spec_from_file_location("mergewatch_sim", f"{B}/mergewatch.py"); M = importlib.util.module_from_spec(s); s.loader.exec_module(M)
MAIN, OLD, NEW = "a" * 40, "b" * 40, "c" * 40
T0 = 1_793_700_000.0   # fake epoch; minute k of the simulation is T0 + 60*k


def simulate(base, merge_min=0, fail_fetch_at=(), minutes=25, prev_poll_age=0):
    """Run one tick per simulated minute. Returns minute (after merge) at which mergewatch would launch the PR, or None."""
    rd = tempfile.mkdtemp(); os.makedirs(f"{rd}/merged")
    st = {"main_sha": MAIN, "ref_shas": {"refs/heads/main": MAIN, "refs/heads/integration/canvas-s2": OLD},
          "merged_seen": "2026-11-02T00:00:00+00:00", "last_poll_ts": T0 - prev_poll_age}
    clock = [T0]
    for k in range(minutes):
        clock[0] = T0 + 60 * k
        merged = k >= merge_min
        integ = NEW if merged and base.startswith("integration/") else OLD
        ls = f"{MAIN}\trefs/heads/main\n{integ}\trefs/heads/integration/canvas-s2\n"
        pulls = [{"number": 1609, "merged_at": "2026-11-02T16:18:51Z", "base": {"ref": base}}] if merged else []
        lines, saved = [], {}
        def fetch(tok):
            if k in fail_fetch_at: raise urllib.error.URLError("timed out")
            return pulls
        with mock.patch.object(M, "DRY", True), mock.patch.object(M, "REPORTS", rd), mock.patch.object(M, "log", lines.append), \
             mock.patch.object(M, "load", lambda: json.loads(json.dumps(st))), mock.patch.object(M, "save", lambda x: saved.update(x)), \
             mock.patch.object(M, "running_prs", lambda: set()), mock.patch.object(M, "heartbeat", lambda m: None), \
             mock.patch.object(M, "catchup_running", lambda: True), mock.patch.object(M, "fetch_pulls", fetch), \
             mock.patch.object(M, "gh_token", lambda: "t"), mock.patch.object(M.time, "time", lambda: clock[0]), \
             mock.patch.object(M.subprocess, "run", lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout=ls, stderr="")), \
             mock.patch.dict(os.environ, {"PI_FLEET_MODE": "pi", "PI_LOCK_DIR": tempfile.mkdtemp()}):
            M.main()
        st = saved
        if any("would launch" in l and "--pr 1609" in l for l in lines):
            return k - merge_min
    return None


class Sim(unittest.TestCase):
    def report(self, name, got):
        print(f"SIM {name}: picked up {got} min after merge", file=sys.stderr)

    def test_integration_merge_picked_up_next_tick(self):
        got = simulate("integration/canvas-s2", merge_min=3); self.report("integration/canvas-s2", got)
        self.assertIsNotNone(got); self.assertLessEqual(got, 1)

    def test_integration_merge_with_failed_fetch_within_10_min(self):
        got = simulate("integration/canvas-s2", merge_min=3, fail_fetch_at={3}); self.report("integration + fetch URLError", got)
        self.assertIsNotNone(got); self.assertLessEqual(got, 10)

    def test_feat_merge_poll_only_within_10_min(self):
        got = simulate("feat/video-in-canvas", merge_min=1, prev_poll_age=0); self.report("feat/* poll only", got)
        self.assertIsNotNone(got); self.assertLessEqual(got, 10)

    def test_feat_merge_failed_poll_retries_next_minute(self):
        # poll due at merge minute+0 fails -> must retry on the next tick, not wait another POLL_S
        got = simulate("feat/video-in-canvas", merge_min=10, fail_fetch_at={10}, prev_poll_age=0); self.report("feat/* + poll URLError", got)
        self.assertIsNotNone(got); self.assertLessEqual(got, 10)
        self.assertLessEqual(got, 1)


if __name__ == "__main__":
    unittest.main()
