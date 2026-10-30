#!/usr/bin/env python3
"""watchdog noreport (10-30 D): only merged PRs whose baseRefName is main can be "merged but not tested"; other bases are
skipped (panoptes does not test them), never alerted. gh and pgrep are faked; no network."""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import datetime as dt, os, sys, tempfile, types, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import watchdog  # noqa: E402

class T(unittest.TestCase):
    def test_non_main_base_is_skipped(self):
        now = watchdog.now_cst(); old = (now - dt.timedelta(hours=3)).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        prs = [{"number": 11, "mergedAt": old, "baseRefName": "main"}, {"number": 12, "mergedAt": old, "baseRefName": "probe-x"}]
        seen = []
        def gh(args, js=True): seen.append(args); return prs
        with tempfile.TemporaryDirectory() as d, mock.patch.object(watchdog, "gh", gh), mock.patch.object(watchdog, "RPT", d), \
             mock.patch.object(watchdog.subprocess, "run", lambda *a, **k: types.SimpleNamespace(stdout="")):
            probs, st = {}, {}
            watchdog.merge_report_check(now, probs, st)
        self.assertIn("noreport:11", probs); self.assertNotIn("noreport:12", probs)
        self.assertIn("baseRefName", seen[0][seen[0].index("--json") + 1])
        self.assertEqual(st.get("merged_skipped"), [{"pr": 12, "base": "probe-x"}])

if __name__ == "__main__":
    unittest.main()
