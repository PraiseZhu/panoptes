#!/usr/bin/env python3
"""watchdog vigil_check, one key per stuck PR (11-03, /workspace/pi-work/vigil-stall-1103/FINDINGS.md): the single key vigil:stall
stayed open when its PR set changed {1627} -> {1621}, so nothing was logged as recovered or new. Same harness as
test_watchdog_recovery_log.py: other checks, send and the lock are stubbed; gh is faked; writes only in a temp dir."""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import fcntl, glob, json, os, shutil, sys, tempfile, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import watchdog  # noqa: E402

OTHER = ["job_checks", "mergewatch_check", "merge_sla_check", "merge_report_check", "backup_check", "backup_rules_check",
         "panoptes_check", "infra_check", "prfix_check", "outbound_health_check", "janitor_check"]


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="watchdog-vigil-"); self.sent = []; self.stuck = []; self.clock = [1_000_000.0]
        ps = [mock.patch.object(watchdog, "STATE", f"{self.tmp}/state.json"), mock.patch.object(watchdog, "LOGS", self.tmp),
              mock.patch.object(watchdog, "DRY", False), mock.patch.object(watchdog, "mode", lambda: "pi"),
              mock.patch.object(watchdog, "send", lambda *a: self.sent.append(a)), mock.patch.object(fcntl, "flock", lambda *a: None),
              mock.patch.object(watchdog, "gh", self.gh), mock.patch.object(watchdog.time, "time", lambda: self.clock[0])]
        ps += [mock.patch.object(watchdog, c, lambda *a: None) for c in OTHER]
        for p in ps: p.start(); self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def gh(self, args, js=True):
        old = (watchdog.now_cst() - watchdog.dt.timedelta(hours=6)).isoformat()
        return [{"number": n, "title": f"pr {n}", "isDraft": False, "updatedAt": old,
                 "statusCheckRollup": [{"name": "review-pr", "conclusion": "FAILURE"}]} for n in self.stuck]

    def run_once(self):
        self.clock[0] += 1801                     # past the 30-min gh cache: every run re-reads the (fake) PR list
        self.assertEqual(watchdog.main(), 0)
        return json.load(open(watchdog.STATE))

    def logs(self): return "".join(open(p).read() for p in sorted(glob.glob(f"{self.tmp}/watchdog-*.log")))

    def test_pr_set_change_logs_recovered_and_new(self):
        self.stuck = [1627]; self.run_once(); st = self.run_once()      # 2 runs: past the debounce
        self.assertEqual([k for k in st["open"] if k.startswith("vigil")], ["vigil:stall:1627"])
        self.assertIn("new=['vigil:stall:1627']", self.logs())
        self.stuck = [1621]; self.run_once(); st = self.run_once()
        self.assertIn("recovered=['vigil:stall:1627']", self.logs())
        self.assertIn("new=['vigil:stall:1621']", self.logs())
        self.assertEqual([k for k in st["open"] if k.startswith("vigil")], ["vigil:stall:1621"])
        self.assertTrue(any("现在好了" in a[0] and "#1627" in a[0] for a in self.sent))   # Praise is told 1627 recovered

    def seed_legacy(self, **extra):
        old = watchdog.PN.item("action", "Mac mini 盯梢那边有 PR 卡了很久", "1 个已转正式的 PR…#1621", todo=["x"])
        with open(watchdog.STATE, "w") as f:      # pre-upgrade state.json, fresh 30-min cache holding the old key
            json.dump({"open": {"vigil:stall": old}, "last_seen": {"vigil:stall": old}, "vigil_probs": {"vigil:stall": old},
                       "vigil_checked_at": self.clock[0], "sent_at": 9e12, **extra}, f)

    def test_legacy_aggregate_key_dropped_silently_after_upgrade(self):
        self.seed_legacy(); self.stuck = [1621]
        self.assertEqual(watchdog.main(), 0); st = json.load(open(watchdog.STATE))   # same 30-min window: legacy cache is not reused
        self.assertNotIn("'vigil:stall'", self.logs())           # never logged as recovered
        self.assertIn("new=['vigil:stall:1621']", self.logs())    # already debounced under the old key: reported in the same run
        for part in ("open", "last_seen", "vigil_probs"): self.assertNotIn("vigil:stall", st[part])
        self.assertIn("vigil:stall:1621", st["open"])
        self.assertFalse(any("现在好了" in a[0] for a in self.sent))
        self.stuck = []; st = self.run_once()
        self.assertIn("recovered=['vigil:stall:1621']", self.logs())
        self.assertFalse([k for k in st["open"] if k.startswith("vigil")])

    def test_legacy_key_never_reported_recovered_with_current_stalls(self):
        """11-03 acceptance: live state still had vigil:stall; one message said 🔴 #1648/#1621 and 「已恢复…不用管了」."""
        self.seed_legacy(sent_at=0); self.stuck = [1621, 1648]
        for _ in range(3): st = self.run_once()
        out = "\n".join(a[0] for a in self.sent)
        self.assertIn("#1648", out); self.assertIn("#1621", out)
        self.assertNotIn("已恢复", out); self.assertNotIn("现在好了", out)
        self.assertNotIn("'vigil:stall'", self.logs())
        for part in ("open", "last_seen", "vigil_probs"): self.assertNotIn("vigil:stall", st[part])

if __name__ == "__main__": unittest.main()
