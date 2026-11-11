#!/usr/bin/env python3
"""fleet_status.py: read-only fleet status (ORG.md rule 7). Temp dirs only: no real state, no gh, no model.
Run: cd bin && python3 ../test/test_fleet_status.py"""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import fcntl, functools, json, os, shutil, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import fleet_status as fs  # noqa: E402


class T(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="fleet-status-test-")
        self.state, self.qa, self.fleet = f"{self.t}/state", f"{self.t}/qa", f"{self.t}/fleet"
        self.now = fs.now_cst().replace(microsecond=0); day = self.now.date().isoformat()
        for d in ("out/logs", "out/praise-pr", "out/daily", "out/ledger"): os.makedirs(f"{self.state}/{d}")
        os.makedirs(f"{self.qa}/argus-root"); os.makedirs(f"{self.qa}/reports/merged"); os.makedirs(f"{self.fleet}/pm-daily")
        self.cron = f"{self.t}/pi-line.cron"
        open(self.cron, "w").write(f"R=/x/run.sh\n35 * * * * box $R daily @07,08\n40 * * * * box $R ledger @23\n"
                                   f"* * * * * box /usr/bin/python3 /x/pi/bin/mergewatch.py >>{self.state}/out/logs/mergewatch-cron.log 2>&1\n")
        open(f"{self.state}/out/logs/daily-{day}.log", "w").write(
            f"=== {day} 00:00:01 CST start daily mode=pi outbound=open\n=== {day} 00:00:09 CST end daily rc=1\n")
        json.dump({"days": {day: {"baseline": 20.0, "samples": [[f"{day}T00:00:00+08:00", 21.5]]}}},
                  open(f"{self.state}/out/praise-pr/grok-usage.json", "w"))
        open(f"{self.fleet}/pm-daily/{day}.md", "w").write("# pm-daily\n")
        os.makedirs(f"{self.t}/bin"); open(f"{self.t}/bin/gh_wrapper.py", "w").close()
        os.symlink(f"{self.t}/bin/gh_wrapper.py", f"{self.t}/gh")
        open(f"{self.state}/gh-app-enabled", "w").close()
        self.collect = functools.partial(fs.collect, state=self.state, qa=self.qa, fleet=self.fleet, argus=f"{self.qa}/argus-root",
                                         gh=f"{self.t}/gh", cindy_lock=f"{self.t}/ensure-cindy.v2.lock", cron_file=self.cron)

    def tearDown(self): shutil.rmtree(self.t, ignore_errors=True)

    def by(self, doc, section, name): return next(i for i in doc["items"] if i["section"] == section and i["name"] == name)

    def test_held_lock_names_its_holder_and_is_never_taken(self):
        lk = open(f"{self.qa}/argus-root/catch-up.lock", "w"); fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        open(f"{self.qa}/argus-root/nightly.lock", "w").close()
        try: doc = self.collect(now=self.now)
        finally: lk.close()
        self.assertEqual(self.by(doc, "locks", "catch-up.lock")["state"], "held")
        self.assertIn(f"pid {os.getpid()}", self.by(doc, "locks", "catch-up.lock")["detail"])
        self.assertEqual(self.by(doc, "locks", "nightly.lock")["state"], "free")
        self.assertEqual(self.by(doc, "locks", "ensure-cindy.v2.lock")["state"], "missing")
        self.assertFalse(os.path.exists(f"{self.t}/ensure-cindy.v2.lock"))     # read-only: a missing lock is not created

    def test_runs_identity_quota_and_files(self):
        doc = self.collect(now=self.now)
        daily = self.by(doc, "runs", "daily")
        self.assertEqual((daily["state"], daily["detail"].split("；")[0]), ("fail", "rc=1"))
        self.assertEqual(self.by(doc, "runs", "ledger")["state"], "never")
        self.assertEqual(self.by(doc, "runs", "mergewatch")["state"], "unknown")   # no quiet log, no out/mergewatch/
        self.assertEqual(self.by(doc, "runs", "grok routines")["state"], "unknown")
        self.assertEqual([self.by(doc, "identity", n)["state"] for n in ("gh wrapper", "gh login")], ["ok", "ok"])
        self.assertIn(fs.app_login(), self.by(doc, "identity", "gh login")["detail"])
        os.remove(f"{self.state}/gh-app-enabled")
        self.assertEqual(self.by(self.collect(now=self.now), "identity", "gh login")["state"], "fail")
        self.assertIn("最新样本 21.5%", self.by(doc, "quota", "grok usage")["detail"])
        self.assertEqual(self.by(doc, "files", "pm-daily")["state"], "ok")
        self.assertIn(self.by(doc, "files", "日报")["state"], ("missing", "pending"))
        self.assertEqual(self.by(doc, "files", "QA 报告（今天）")["state"], "none")

    def test_missing_inputs_are_unknown_with_a_reason(self):
        os.remove(f"{self.state}/out/praise-pr/grok-usage.json"); os.remove(self.cron)
        doc = self.collect(now=self.now)
        self.assertEqual(self.by(doc, "quota", "grok usage")["state"], "unknown")
        self.assertEqual(self.by(doc, "runs", "pi-line cron")["state"], "unknown")
        self.assertTrue(all(i["detail"] for i in doc["items"] if i["state"] == "unknown"))

    def test_main_writes_both_files_and_a_failed_write_keeps_the_old_ones(self):
        js, md = f"{self.fleet}/fleet-status.json", f"{self.fleet}/fleet-status.md"
        saved = fs.collect; fs.collect = lambda: self.collect(now=self.now)
        try:
            self.assertEqual(fs.main(["--json", js, "--md", md]), 0)
            self.assertEqual(json.load(open(js))["generated_at"], self.now.isoformat())
            self.assertIn("## locks", open(md).read())
            before = (open(js).read(), open(md).read()); real = os.replace
            def boom(*a): raise OSError("disk full")
            os.replace = boom
            try: self.assertRaises(OSError, fs.main, ["--json", js, "--md", md])
            finally: os.replace = real
            self.assertEqual((open(js).read(), open(md).read()), before)
        finally: fs.collect = saved
        self.assertEqual(fs.main(["--bogus", "x"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
