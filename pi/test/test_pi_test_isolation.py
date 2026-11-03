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

    def test_import_time_env_leaks_from_other_modules_do_not_break_tests(self):
        """11-04: `python3 -m unittest discover -s test` imports every module before running any test, so each module's import-time
        os.environ edits leak into the others (pytest's conftest isolates modules, discover does not). Base e12881b: 6 failures --
        test_grokline/test_prfix_unit setdefault PRFIX_OWNERS='*' over test_fix_authors' pop; test_p2_fp_rulings re-points
        JANITOR_CONF over test_prfix_unit's; lib.BASE frozen by the first importer while later modules move PI_STATE. Replay those
        leaks after import in a child process: the affected tests must stay green."""
        bad = tempfile.mkdtemp(prefix="leaked-env-"); self.addCleanup(shutil.rmtree, bad, True)
        with open(f"{bad}/janitor.json", "w") as fh: fh.write('{"enforce": false}')
        code = f"""import os, sys, unittest; sys.path.insert(0, {HERE!r})
import test_fix_authors, test_p2_fp_rulings, test_prfix_unit
os.environ.update(PRFIX_OWNERS="*", PI_STATE={bad!r}, JANITOR_CONF={bad + '/janitor.json'!r})   # what other modules leave behind
names = ["test_fix_authors.FixAuthors.test_default_and_rollback_flag", "test_p2_fp_rulings.Policy.test_switch_restores_whole_pr",
         "test_p2_fp_rulings.P2Record.test_shadow_proposals_skip_only_the_signed_finding",
         "test_p2_fp_rulings.PmAndNudge.test_pm_counts_pr_until_every_p2_is_signed",
         "test_p2_fp_rulings.PmAndNudge.test_pmnudge_skips_only_fix_prs_for_signed_findings",
         "test_prfix_unit.TestFixOneEndToEnd.test_success_with_janitor_enforcing_drops_worktree_keeps_patch"]
r = unittest.TextTestRunner(verbosity=0).run(unittest.defaultTestLoader.loadTestsFromNames(names))
sys.exit(0 if r.wasSuccessful() and r.testsRun == len(names) else 1)"""
        p = subprocess.run([sys.executable, "-c", code], cwd=os.path.join(os.path.dirname(HERE), "bin"), capture_output=True, text=True, timeout=300)
        self.assertEqual(p.returncode, 0, p.stderr[-3000:])

    def test_mergewatch_resume_ignores_the_live_mergewatch_lock(self):
        """test_mergewatch_resume.py used the live /tmp/pi-fallback-mergewatch.lock (no PI_LOCK_DIR): while the production
        mergewatch cron held it, main() returned at once and the module failed (selftest). Hold that lock in a private mount
        namespace (bwrap binds a scratch file over the path; the live lock is never taken) and run the module: it must pass."""
        live = "/tmp/pi-fallback-mergewatch.lock"
        if not (shutil.which("bwrap") and os.path.exists(live)): self.skipTest("needs bwrap and the live lock file as a mount point (box only)")
        fake = tempfile.mkdtemp(prefix="fake-mw-lock-"); self.addCleanup(shutil.rmtree, fake, True); open(f"{fake}/lock", "w").close()
        hold = ("import fcntl, subprocess, sys; f = open(sys.argv[1], 'w'); fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); "
                "sys.exit(subprocess.run([sys.executable, sys.argv[2]]).returncode)")
        env = {k: v for k, v in os.environ.items() if k != "PI_LOCK_DIR"}
        p = subprocess.run(["bwrap", "--dev-bind", "/", "/", "--bind", f"{fake}/lock", live, sys.executable, "-c", hold, live,
                            f"{HERE}/test_mergewatch_resume.py"], cwd=os.path.join(os.path.dirname(HERE), "bin"), env=env,
                           capture_output=True, text=True, timeout=120)
        if "bwrap:" in p.stderr and p.returncode != 0 and "Traceback" not in p.stderr: self.skipTest(p.stderr[-300:])
        self.assertEqual(p.returncode, 0, p.stderr[-1500:])

if __name__ == "__main__":
    unittest.main()
