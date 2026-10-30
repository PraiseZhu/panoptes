#!/usr/bin/env python3
"""watchdog main(): every alert recovery writes a day-log line (ticket E, 10-29: fyi sla:merge-to-test closed on a quiet round,
nothing was sent and nothing was logged). Local only: checks, send and the lock are stubbed; writes only in a temp dir."""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import fcntl, glob, json, os, shutil, sys, tempfile, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import watchdog  # noqa: E402

CHECKS = ["job_checks", "mergewatch_check", "merge_sla_check", "merge_report_check", "backup_check", "backup_rules_check",
          "panoptes_check", "infra_check", "prfix_check", "vigil_check", "outbound_health_check", "janitor_check"]


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="watchdog-recov-"); self.sent = []
        ps = [mock.patch.object(watchdog, "STATE", f"{self.tmp}/state.json"), mock.patch.object(watchdog, "LOGS", self.tmp),
              mock.patch.object(watchdog, "DRY", False), mock.patch.object(watchdog, "mode", lambda: "pi"),
              mock.patch.object(watchdog, "send", lambda *a: self.sent.append(a)), mock.patch.object(fcntl, "flock", lambda *a: None)]
        ps += [mock.patch.object(watchdog, c, lambda *a: None) for c in CHECKS]
        for p in ps: p.start(); self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_quiet_fyi_recovery_is_logged(self):
        fyi = watchdog.PN.item("fyi", "合并到测试超时（不急）", "x")
        with open(watchdog.STATE, "w") as f:
            json.dump({"open": {"sla:merge-to-test": fyi}, "last_seen": {"sla:merge-to-test": fyi}, "sent_at": 9e12}, f)
        self.assertEqual(watchdog.main(), 0)
        self.assertEqual(self.sent, [])                                   # fyi recovery: Praise is not messaged
        logs = "".join(open(p).read() for p in glob.glob(f"{self.tmp}/watchdog-*.log"))
        self.assertIn("recovered=['sla:merge-to-test']", logs)
        self.assertIn("open=0", logs)


if __name__ == "__main__": unittest.main()
