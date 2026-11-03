#!/usr/bin/env python3
"""11-03 integration-branch QA (watchdog): same base rule as catch-up (only probe-* skipped); a report counts only when
tested_sha == merge_sha; a non-probe merge into a non-main base with no qualifying report after 2 h is a P2 written to the
09:05 brief queue (Jasper), never a DM (not in probs); PANOPTES_BRANCH_ALERT=0 turns it off. gh / pgrep faked, no network."""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import datetime as dt, json, os, sys, tempfile, types, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import watchdog  # noqa: E402

SHA = {1601: "1" * 40, 1379: "2" * 40, 1700: "3" * 40, 1410: "4" * 40, 1701: "5" * 40}

class T(unittest.TestCase):
    def check(self, rows, reports=None, env=None):
        now = watchdog.now_cst()
        prs = [{"number": n, "mergedAt": (now - dt.timedelta(minutes=age)).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "baseRefName": b, "mergeCommit": {"oid": SHA[n]}} for n, b, age in rows]
        d = tempfile.mkdtemp(); base = tempfile.mkdtemp()
        for n, doc in (reports or {}).items(): json.dump(doc, open(f"{d}/PR-{n}.json", "w"))
        env = {"PANOPTES_MAIN_ONLY": "", **(env or {})}
        with mock.patch.object(watchdog, "gh", lambda a, js=True: prs), mock.patch.object(watchdog, "RPT", d), mock.patch.object(watchdog, "BASE", base), \
             mock.patch.object(watchdog.subprocess, "run", lambda *a, **k: types.SimpleNamespace(stdout="")), mock.patch.dict(os.environ, env):
            probs, st = {}, {}
            watchdog.merge_report_check(now, probs, st)
        q = f"{base}/out/outbound/brief-queue.jsonl"
        brief = [json.loads(l) for l in open(q)] if os.path.exists(q) else []
        return probs, st, brief

    def test_non_probe_branch_merge_is_checked_probe_is_skipped(self):
        probs, st, brief = self.check([(1601, "integration/canvas-s2", 130), (1410, "probe-x", 130)])
        self.assertEqual(st["merged_skipped"], [{"pr": 1410, "base": "probe-x"}])
        self.assertEqual([b["pr"] for b in brief], [1601])

    def test_branch_p2_after_2h_goes_to_brief_not_dm(self):
        probs, st, brief = self.check([(1601, "integration/canvas-s2", 121), (1379, "feat/video-in-canvas", 100)])
        self.assertEqual([(b["pr"], b["severity"], b["key"], b["base"]) for b in brief], [(1601, "P2", "branch-miss:1601", "integration/canvas-s2")])
        self.assertNotIn("noreport:1601", probs); self.assertNotIn("noreport:1379", probs)   # never a DM to Praise
        self.assertIn("integration/canvas-s2", brief[0]["text"])

    def test_tested_sha_must_equal_merge_sha(self):
        good = {"prs": [{"number": 1700, "verdict": "pass", "tested_sha": SHA[1700]}]}
        bad = {"prs": [{"number": 1701, "verdict": "pass", "tested_sha": "f" * 40}]}
        stale_branch = {"prs": [{"number": 1601, "verdict": "pass", "tested_sha": "f" * 40}]}
        probs, st, brief = self.check([(1700, "main", 100), (1701, "main", 100), (1601, "integration/canvas-s2", 130)], {1700: good, 1701: bad, 1601: stale_branch})
        self.assertNotIn("noreport:1700", probs); self.assertNotIn("stale:1700", probs)
        self.assertIn("stale:1701", probs)
        self.assertEqual([(b["pr"], b["gap"]) for b in brief], [(1601, "stale")])

    def test_branch_alert_switch_off(self):
        _, _, brief = self.check([(1601, "integration/canvas-s2", 130)], env={"PANOPTES_BRANCH_ALERT": "0"})
        self.assertEqual(brief, [])

    def test_main_only_rollback_keeps_b_sentinel(self):
        probs, st, brief = self.check([(1601, "integration/canvas-s2", 130)], env={"PANOPTES_MAIN_ONLY": "1"})
        self.assertEqual(st["merged_skipped"], [{"pr": 1601, "base": "integration/canvas-s2"}])
        self.assertEqual([b["pr"] for b in brief], [1601])   # B: skipped non-probe base still raises P2

if __name__ == "__main__":
    unittest.main()
