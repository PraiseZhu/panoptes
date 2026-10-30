#!/usr/bin/env python3
"""catch-up hourly (10-30 D, ops): scripts/catch-up.cron no longer sets PANOPTES_CATCHUP_GATE=even, so the :07 tick runs every
Beijing hour. On the odd-hour ticks (the new ones) run-nightly.sh checks memory BEFORE taking any lock: MemAvailable < 6144 MB or
SwapFree < 4096 MB -> one skip line, exit 0 at once (no 30-min wait, no catch-up.lock). Even hours keep the existing gate.
PANOPTES_MEMINFO / PANOPTES_FAKE_BJ_HOUR fake the box; temp QA_HOME only."""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import os, re, shutil, subprocess, tempfile, unittest
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def meminfo(d, avail_mb, swap_mb):
    p = f"{d}/meminfo"; L = ["MemTotal: 16386000 kB", "SwapTotal: 8388604 kB"]
    if avail_mb is not None: L.append(f"MemAvailable: {avail_mb * 1024} kB")
    if swap_mb is not None: L.append(f"SwapFree: {swap_mb * 1024} kB")
    open(p, "w").write("\n".join(L) + "\n"); return p

class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); os.makedirs(f"{self.d}/argus-root"); self.addCleanup(shutil.rmtree, self.d, True)
        # as argus/tests/catch-up-lock-log.test.mjs: dummy secrets + a fake nvm so the script reaches the catch-up lock
        open(f"{self.d}/s.env", "w").write("GITHUB_TOKEN=x\nQA_LLM_BASE_URL=http://127.0.0.1:9\nQA_LLM_API_KEY=x\n")
        os.makedirs(f"{self.d}/nvm"); open(f"{self.d}/nvm/nvm.sh", "w").write("nvm() { :; }\n")
    def run_cu(self, hour, avail, swap, gate=None, trigger="catch-up", hold=True):
        env = {k: v for k, v in os.environ.items() if k != "PANOPTES_CATCHUP_GATE"}
        env.update({"QA_HOME": self.d, "PANOPTES_BOOTSTRAP": "0", "PANOPTES_TMP_SWEEP": "0", "PANOPTES_SEVERITY_PIN_CHECK": "0",
                    "QA_SECRETS_FILE": f"{self.d}/s.env", "PANOPTES_NVM_DIR": f"{self.d}/nvm", "PANOPTES_FAKE_BJ_HOUR": hour, "PANOPTES_MEMINFO": meminfo(self.d, avail, swap)})
        if gate is not None: env["PANOPTES_CATCHUP_GATE"] = gate
        cmd = [f"{R}/run-nightly.sh", "--catch-up", "--trigger", trigger]
        if hold: cmd = ["flock", f"{self.d}/argus-root/catch-up.lock"] + cmd   # held: a run that gets past the gates logs "lock held" and stops
        return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60)
    def lines(self, r): return [l for l in r.stdout.splitlines() if l.startswith("[catch-up]")]
    def test_cron_runs_every_hour_without_even_gate(self):
        jobs = [l for l in open(f"{R}/scripts/catch-up.cron").read().splitlines() if l.strip() and not l.startswith("#") and "run-nightly" in l]
        self.assertEqual(len(jobs), 1); self.assertEqual(jobs[0].split()[:5], ["7", "*", "*", "*", "*"])
        self.assertNotIn("PANOPTES_CATCHUP_GATE", jobs[0])
    def test_odd_hour_low_memory_skips_before_lock(self):
        for avail, swap in ((6143, 8000), (9000, 4095), (1000, 100)):
            r = self.run_cu("13", avail, swap)
            self.assertEqual(r.returncode, 0, r.stderr); ls = self.lines(r)
            self.assertEqual(len(ls), 1, r.stdout)
            self.assertRegex(ls[0], r"^\[catch-up\] \S+\+08:00 trigger=catch-up: Beijing hour 13 \(odd\): .*low memory.*skipped$")
            self.assertIn(f"MemAvailable {avail} MiB", ls[0]); self.assertIn(f"SwapFree {swap} MiB", ls[0])
            self.assertNotIn("catch-up.lock held", r.stdout)   # decided before the lock
    def test_odd_hour_unreadable_meminfo_is_a_skip(self):
        for avail, swap in ((None, 8000), (9000, None), (None, None)):
            r = self.run_cu("13", avail, swap)
            self.assertEqual(r.returncode, 0, r.stderr); ls = self.lines(r)
            self.assertEqual(len(ls), 1, r.stdout + r.stderr); self.assertIn("unknown", ls[0]); self.assertIn("skipped", ls[0])
            self.assertNotIn("catch-up.lock held", r.stdout)
    def test_odd_hour_enough_memory_goes_on(self):
        r = self.run_cu("13", 6144, 4096)
        self.assertNotIn("low memory", r.stdout); self.assertIn("catch-up.lock held by another catch-up", r.stdout)
    def test_even_hour_and_other_triggers_unchanged(self):
        for hour, trig in (("14", "catch-up"), ("13", "event"), ("13", "manual")):
            r = self.run_cu(hour, 1000, 100, trigger=trig)
            self.assertNotIn("low memory", r.stdout, (hour, trig)); self.assertIn("catch-up.lock held", r.stdout, (hour, trig))
        r = self.run_cu("13", 9000, 8000, gate="even")   # the old even-hour gate still works when set
        self.assertIn("is odd", r.stdout)

if __name__ == "__main__":
    unittest.main()
