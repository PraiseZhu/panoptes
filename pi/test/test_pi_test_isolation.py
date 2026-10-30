#!/usr/bin/env python3
"""pi/test never reads live state (10-30): test_outbound_resend.py used to fingerprint the LIVE
/workspace/mivo-qa/pi/out/outbound/muted-<today>.md at import and assert it unchanged in tearDown, so every live outbound
append failed 33-37 tests. Now isolation.sandbox_state() points PI_STATE (lib.BASE and all derived paths) at a temp dir for
direct runs (pytest: conftest, selftest: env -i PI_STATE=tmp), and the guard watches the sandbox's file, never the live one.
PI_TEST_LIVE_STATE (test-only) names the "live" root so a fake one can be appended to during a real run."""
import datetime as dt, os, shutil, subprocess, sys, tempfile, threading, time, unittest
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
        """#142 follow-up: run test_outbound_resend.py in a private mount namespace where the REAL live path
        /workspace/mivo-qa/pi is a fake dir (bwrap --bind; the live dir itself is never touched), while a thread keeps appending
        to <fake>/out/outbound/muted-<today>.md -- what live outbound does. Pre-#142 the module fingerprinted that live path at
        import and tearDown failed; now it must stay green. PI_TEST_RESEND_FILE (test-only) runs another copy, e.g. the
        pre-#142 file, to show the failure."""
        live = "/workspace/mivo-qa/pi"
        target = os.environ.get("PI_TEST_RESEND_FILE") or f"{HERE}/test_outbound_resend.py"
        if not (shutil.which("bwrap") and os.path.isdir(live)): self.skipTest("needs bwrap and the live state dir as a mount point (box only)")
        fake = tempfile.mkdtemp(prefix="fake-live-state-")
        muted = f"{fake}/out/outbound/muted-{(dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)).date().isoformat()}.md"
        os.makedirs(os.path.dirname(muted)); open(muted, "w").write("# muted\n")
        probe = subprocess.run(["bwrap", "--dev-bind", "/", "/", "--bind", fake, live, "true"], capture_output=True)
        if probe.returncode != 0: self.skipTest("bwrap cannot create a mount namespace here")
        env = {k: v for k, v in os.environ.items() if k not in ("PI_STATE", "PI_WORK", "PI_WORK_ROOT", "PI_TEST_LIVE_STATE")}
        stop = threading.Event(); n = [0]
        def appender():
            while not stop.is_set():
                with open(muted, "a") as f: f.write(f"- live outbound append {n[0]}\n")
                n[0] += 1; time.sleep(0.005)
        th = threading.Thread(target=appender); th.start()
        try:
            p = subprocess.run(["bwrap", "--dev-bind", "/", "/", "--bind", fake, live, sys.executable, target],
                               cwd=os.path.join(os.path.dirname(HERE), "bin"), env=env, capture_output=True, text=True, timeout=300)
        finally: stop.set(); th.join(); shutil.rmtree(fake, ignore_errors=True)
        self.assertGreater(n[0], 5)
        self.assertEqual(p.returncode, 0, p.stderr[-1500:])

if __name__ == "__main__":
    unittest.main()
