#!/usr/bin/env python3
"""11-16 (PraiseZhu/panoptes#303) drain switch $PI_STATE/panoptes-drain. While it exists:
  * pi/bin/run.sh exits 0 with one "排空中，跳过" line for every job outside lib.DRAIN_EXEMPT (deploy / watch / box care run on);
  * run-nightly.sh starts no QA run: nightly, event, manual and --catch-up exit 0 with one "[drain] … 排空中，跳过" line before any
    lock or fleet manifest; a child of a catch-up that was already running exits 76 (argus catch-up stops, no attempt spent);
    a run that queued for the QA slot before the switch opened exits 0 right after it gets the slot;
  * watchdog alerts once it has been open > 2 h.
Without the file nothing changes. Shell-level runs use a copy of run.sh with fake jobs, and run-nightly.sh with a temp QA_HOME
(Linux only: GNU date / flock, like test_catchup_hourly_gate)."""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import fcntl, os, re, shutil, subprocess, sys, tempfile, time, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); BIN = os.path.join(os.path.dirname(HERE), "bin"); R = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, BIN); sys.path.insert(0, HERE)
import isolation  # noqa: E402
isolation.sandbox_state()
import lib, watchdog  # noqa: E402

LINUX = sys.platform.startswith("linux")   # on Linux (CI, the box) these must run: a missing flock fails, never skips


def read(p):
    try:
        with open(p) as f: return f.read()
    except FileNotFoundError: return ""


class Contract(unittest.TestCase):
    def test_shell_copies_match_lib(self):
        run_sh = read(f"{BIN}/run.sh")
        m = re.search(r'^DRAIN_EXEMPT=" ([a-z_ ]+) "$', run_sh, re.M)
        self.assertTrue(m, "run.sh has no DRAIN_EXEMPT line")
        self.assertEqual(sorted(m.group(1).split()), sorted(lib.DRAIN_EXEMPT))
        for job in ("opsdeploy", "opsenv", "watchdog"): self.assertIn(job, lib.DRAIN_EXEMPT)
        for job in ("daily", "prfix", "outbound", "merger", "opsfix", "handoff"): self.assertNotIn(job, lib.DRAIN_EXEMPT)
        self.assertEqual(lib.drain_file(), f"{lib.PI_STATE}/panoptes-drain")
        self.assertIn('[ -e "$BASE/panoptes-drain" ]', run_sh)   # run.sh BASE = PI_STATE
        self.assertIn('DRAIN_FILE="${PI_STATE:-$QA_HOME/pi}/panoptes-drain"', read(f"{R}/run-nightly.sh"))

    def test_drain_since(self):
        d = tempfile.mkdtemp(); p = f"{d}/panoptes-drain"
        self.assertIsNone(lib.drain_since(p))
        open(p, "w").close(); os.utime(p, (1000, 1000)); self.assertEqual(lib.drain_since(p), 1000)


class Watchdog(unittest.TestCase):
    def check(self, age):
        d = tempfile.mkdtemp(); p = f"{d}/panoptes-drain"; t = time.time()
        if age is not None: open(p, "w").close(); os.utime(p, (t - age, t - age))
        probs = {}; watchdog.drain_check(None, probs, path=p, t=t); return probs

    def test_open_over_two_hours_alerts_once(self):
        self.assertEqual(self.check(None), {})
        self.assertEqual(self.check(2 * 3600 - 60), {})
        self.assertEqual(self.check(2 * 3600), {})
        p = self.check(2 * 3600 + 60)
        self.assertEqual(list(p), ["drain:open"]); self.assertEqual(p["drain:open"]["kind"], "action")
        self.assertIn("2.0 小时", p["drain:open"]["what"]); self.assertIn("立即部署", p["drain:open"]["todo"])

    def test_registered_in_main(self):
        self.assertIn("drain_check(now, probs)", read(f"{BIN}/watchdog.py").split("def main():", 1)[1])


@unittest.skipUnless(LINUX, "run.sh needs GNU date and flock (Linux CI / the box)")
class RunSh(unittest.TestCase):
    JOBS = ("drainprobe",) + tuple(j for j in lib.DRAIN_EXEMPT)

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="drain-run-"); self.addCleanup(shutil.rmtree, self.d, True)
        b = f"{self.d}/code/pi/bin"; os.makedirs(b); shutil.copy(f"{BIN}/run.sh", f"{b}/run.sh")
        open(f"{b}/lib.py", "w").write("")
        open(f"{b}/qa_standard.py", "w").write("import sys\nprint('/dev/null') if sys.argv[1] == 'start' else None\n")
        for j in self.JOBS: open(f"{b}/{j}.py", "w").write(f"print('ran {j}')\n")
        self.state = f"{self.d}/state"; os.makedirs(f"{self.state}/out/logs"); os.makedirs(f"{self.d}/home")
        self.env = {"PATH": os.environ["PATH"], "HOME": f"{self.d}/home", "PI_STATE": self.state, "PI_TEST_MEM_AVAIL_MB": "8000",
                    "PI_FLEET_MODE": "pi", "LANG": "C.UTF-8"}

    def run_job(self, job):
        r = subprocess.run(["bash", f"{self.d}/code/pi/bin/run.sh", job], env=self.env, capture_output=True, text=True, timeout=60)
        logs = "".join(read(f"{self.state}/out/logs/{f}") for f in os.listdir(f"{self.state}/out/logs") if f.startswith(job + "-"))
        return r, logs

    def test_no_switch_runs_as_before(self):
        r, log = self.run_job("drainprobe")
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("start drainprobe", log); self.assertIn("ran drainprobe", log)
        self.assertNotIn("排空中", log)

    def test_switch_skips_ordinary_jobs(self):
        open(f"{self.state}/panoptes-drain", "w").close()
        r, log = self.run_job("drainprobe")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len([l for l in log.splitlines() if "排空中，跳过" in l]), 1, log)
        self.assertRegex(log, r"=== \S+ \S+ \S+ skip drainprobe: 排空中，跳过（.*/panoptes-drain）")
        self.assertNotIn("ran drainprobe", log); self.assertNotIn("start drainprobe", log)

    def test_switch_keeps_ops_jobs(self):
        open(f"{self.state}/panoptes-drain", "w").close()
        for job in lib.DRAIN_EXEMPT:
            r, log = self.run_job(job)
            self.assertEqual(r.returncode, 0, (job, r.stderr)); self.assertNotIn("排空中", log, job)
            self.assertTrue(f"ran {job}" in log or "holds lock" in log, (job, log))   # on the box a real run may hold /tmp/pi-fallback-<job>.lock


@unittest.skipUnless(LINUX, "run-nightly.sh needs GNU date and flock (Linux CI / the box)")
class RunNightly(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="drain-rn-"); os.makedirs(f"{self.d}/argus-root"); os.makedirs(f"{self.d}/pi")
        self.addCleanup(shutil.rmtree, self.d, True)
        # as test_catchup_hourly_gate: dummy secrets + a fake nvm so the script gets as far as the locks
        open(f"{self.d}/s.env", "w").write("GITHUB_TOKEN=x\nQA_LLM_BASE_URL=http://127.0.0.1:9\nQA_LLM_API_KEY=x\n")
        os.makedirs(f"{self.d}/nvm"); open(f"{self.d}/nvm/nvm.sh", "w").write("nvm() { :; }\n")
        self.env = {k: v for k, v in os.environ.items() if k not in ("PANOPTES_CATCHUP_GATE", "PANOPTES_RUN_NIGHTLY")}
        self.env.update({"QA_HOME": self.d, "PI_STATE": f"{self.d}/pi", "PANOPTES_BOOTSTRAP": "0", "PANOPTES_TMP_SWEEP": "0",
                         "PANOPTES_SEVERITY_PIN_CHECK": "0", "QA_SECRETS_FILE": f"{self.d}/s.env", "PANOPTES_NVM_DIR": f"{self.d}/nvm",
                         "PANOPTES_FAKE_BJ_HOUR": "14"})
        self.drain = f"{self.d}/pi/panoptes-drain"

    def hold(self, name):
        f = open(f"{self.d}/argus-root/{name}", "a"); fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); self.addCleanup(f.close); return f

    def rn(self, *args, env=None):
        return subprocess.run([f"{R}/run-nightly.sh", *args], env={**self.env, **(env or {})}, capture_output=True, text=True, timeout=120)

    def drained(self, r): return [l for l in r.stdout.splitlines() if l.startswith("[drain]")]

    def test_no_switch_catch_up_unchanged(self):
        self.hold("catch-up.lock")
        r = self.rn("--catch-up")
        self.assertEqual(r.returncode, 0, r.stderr); self.assertEqual(self.drained(r), []); self.assertIn("catch-up.lock held", r.stdout)

    def test_switch_skips_catch_up_before_any_lock(self):
        open(self.drain, "w").close(); self.hold("catch-up.lock")
        r = self.rn("--catch-up")
        self.assertEqual(r.returncode, 0, r.stderr); ls = self.drained(r)
        self.assertEqual(len(ls), 1, r.stdout)
        self.assertRegex(ls[0], r"^\[drain\] \S+\+08:00 trigger=catch-up: 排空中，跳过（.*/pi/panoptes-drain），exit 0$")
        self.assertNotIn("catch-up.lock held", r.stdout)
        self.assertFalse(os.path.exists(f"{self.d}/argus-root/qa-fleet-run-manifest.json"))

    def test_switch_skips_nightly_event_and_manual(self):
        open(self.drain, "w").close(); self.hold("nightly.lock")
        for args in ((), ("--trigger", "event", "--pr", "7", "--priority"), ("--trigger", "manual", "--pr", "7")):
            r = self.rn(*args)
            self.assertEqual(r.returncode, 0, (args, r.stderr)); self.assertEqual(len(self.drained(r)), 1, (args, r.stdout))
            self.assertNotIn("another run is active", r.stderr)
        self.assertIn('"msg":"run-nightly drained"', read(f"{self.d}/argus-root/logs/control.log"))
        self.assertFalse(os.path.exists(f"{self.d}/argus-root/qa-fleet-run-manifest.json"))

    def test_child_of_running_catch_up_exits_76(self):
        open(self.drain, "w").close()
        r = self.rn("--pr", "7", "--trigger", "catch-up", "--wait-lock", "5", env={"PANOPTES_RUN_NIGHTLY": f"{R}/run-nightly.sh"})
        self.assertEqual(r.returncode, 76, r.stderr); self.assertIn("exit 76", self.drained(r)[0])

    def test_run_queued_before_the_switch_stops_after_the_slot(self):
        lk = self.hold("nightly.lock")
        err = open(f"{self.d}/stderr", "w")
        p = subprocess.Popen([f"{R}/run-nightly.sh", "--trigger", "manual", "--pr", "7", "--wait-lock", "90"], env=self.env,
                             stdout=subprocess.PIPE, stderr=err, text=True)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        for _ in range(120):
            if "waiting up to" in read(f"{self.d}/stderr") or p.poll() is not None: break
            time.sleep(0.5)
        self.assertIsNone(p.poll(), read(f"{self.d}/stderr")[-2000:])
        open(self.drain, "w").close(); lk.close()   # the switch opens while it waits; then the running job ends
        out, _ = p.communicate(timeout=100)
        self.assertEqual(p.returncode, 0, read(f"{self.d}/stderr")[-2000:])
        self.assertEqual(len([l for l in out.splitlines() if l.startswith("[drain]")]), 1, out)


if __name__ == "__main__":
    unittest.main()
