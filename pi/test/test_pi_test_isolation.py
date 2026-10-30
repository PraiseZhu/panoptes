#!/usr/bin/env python3
"""pi/test never reads live state (10-30): test_outbound_resend.py used to fingerprint the LIVE
/workspace/mivo-qa/pi/out/outbound/muted-<today>.md at import and assert it unchanged in tearDown, so every live outbound
append failed 33-37 tests. Now isolation.sandbox_state() points PI_STATE (lib.BASE and all derived paths) at a temp dir for
direct runs (pytest: conftest, selftest: env -i PI_STATE=tmp), and the guard watches the sandbox's file, never the live one.
PI_TEST_LIVE_STATE (test-only) names the "live" root so a fake one can be appended to during a real run."""
import datetime as dt, os, subprocess, sys, tempfile, threading, time, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import isolation  # noqa: E402

class T(unittest.TestCase):
    def test_resend_guard_never_points_at_live_state(self):
        with isolation.isolated():
            sys.modules.pop("test_outbound_resend", None)
            import test_outbound_resend as R
            live = os.path.realpath(isolation.live_state())
            self.assertFalse(os.path.realpath(R.REAL_MUTED).startswith(live + os.sep), R.REAL_MUTED)
            self.assertTrue(os.path.realpath(R.REAL_MUTED).startswith(os.path.realpath(R.L.BASE) + os.sep))
            self.assertNotEqual(os.path.realpath(R.L.BASE), live)
            sys.modules.pop("test_outbound_resend", None)

    def test_direct_run_stays_green_while_live_file_is_appended(self):
        fake_live = tempfile.mkdtemp(prefix="fake-live-state-")
        muted = f"{fake_live}/out/outbound/muted-{(dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)).date().isoformat()}.md"
        os.makedirs(os.path.dirname(muted)); open(muted, "w").write("# muted\n")
        env = {k: v for k, v in os.environ.items() if k not in ("PI_STATE", "PI_WORK", "PI_WORK_ROOT")}
        env["PI_TEST_LIVE_STATE"] = fake_live   # this run treats fake_live as the live box state
        stop = threading.Event(); n = [0]
        def appender():
            while not stop.is_set():
                with open(muted, "a") as f: f.write(f"- live outbound append {n[0]}\n")
                n[0] += 1; time.sleep(0.01)
        th = threading.Thread(target=appender); th.start()
        try:
            p = subprocess.run([sys.executable, f"{HERE}/test_outbound_resend.py"], cwd=os.path.join(os.path.dirname(HERE), "bin"),
                               env=env, capture_output=True, text=True, timeout=300)
        finally: stop.set(); th.join()
        self.assertGreater(n[0], 5)
        self.assertEqual(p.returncode, 0, p.stderr[-2000:])
        self.assertNotIn(fake_live, p.stdout + p.stderr)

if __name__ == "__main__":
    unittest.main()
