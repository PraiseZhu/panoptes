"""pi/bin/run.sh pins umask 022 before it creates anything: every Pi job (and the run-nightly.sh / test runs it starts)
inherits 0022, so the report's env_problems no longer carry "inherited umask was 0002" (argus/src/nightly.mjs:1185)."""
import os, re, shutil, stat, subprocess, tempfile, unittest

RUN = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin", "run.sh"))

class RunShUmask(unittest.TestCase):
    def test_umask_pinned_near_top(self):
        lines = open(RUN, encoding="utf-8").read().splitlines()
        idx = [i for i, l in enumerate(lines) if re.match(r"\s*umask 0?022\b", l)]
        self.assertTrue(idx, "run.sh has no umask 022")
        first_cmd = next(i for i, l in enumerate(lines) if l.strip() and not l.startswith("#") and not l.startswith("set "))
        self.assertLessEqual(idx[0], first_cmd, "umask 022 must come right after the shebang/set lines")

    def test_dirs_created_under_umask_002_are_0755(self):
        d = tempfile.mkdtemp()
        try:
            env = dict(os.environ, PI_STATE=d)
            # @99 never matches the Beijing hour: run.sh creates its log dir, then exits 0 without running a job
            p = subprocess.run(["bash", "-c", 'umask 002; exec "$0" nosuchjob @99', RUN], env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr)
            mode = stat.S_IMODE(os.stat(os.path.join(d, "out", "logs")).st_mode)
            self.assertEqual(oct(mode), oct(0o755))
        finally:
            shutil.rmtree(d)

if __name__ == "__main__":
    unittest.main()
