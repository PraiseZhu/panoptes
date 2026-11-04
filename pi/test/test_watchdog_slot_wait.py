#!/usr/bin/env python3
"""watchdog.slot_wait_check (11-05, gap qa_start:PR-1718): scripts/qa-slot.sh writes argus-root/slot-wait-alerts/<pid>.json when a
run waits > 8 min for a QA slot; watchdog turns it into one slot:wait item. Local only, temp dir."""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import json, os, sys, tempfile, time, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import watchdog  # noqa: E402


class T(unittest.TestCase):
    def setUp(self): self.d = tempfile.mkdtemp(prefix="watchdog-slot-")
    def put(self, name, age, **a):
        f = os.path.join(self.d, name); json.dump(a, open(f, "w")); t = time.time() - age; os.utime(f, (t, t)); return f
    def run_(self):
        probs = {}; watchdog.slot_wait_check(None, probs, d=self.d); return probs

    def test_no_files_no_item(self):
        self.assertEqual(self.run_(), {})

    def test_live_wait_raises_one_item(self):
        self.put("1.json", 10, id="PR-1718", kind="merge", state="waiting", waited_s=530)
        p = self.run_(); self.assertEqual(list(p), ["slot:wait"]); self.assertIn("PR-1718", json.dumps(p["slot:wait"], ensure_ascii=False))

    def test_acquired_kept_one_hour_then_dropped_and_swept_after_a_day(self):
        self.put("2.json", 1800, id="PR-1", kind="merge", state="acquired", waited_s=651)
        self.assertIn("slot:wait", self.run_())
        os.remove(os.path.join(self.d, "2.json"))
        self.put("3.json", 7200, id="PR-2", kind="merge", state="acquired", waited_s=600)
        self.assertEqual(self.run_(), {})
        old = self.put("4.json", 90000, id="PR-3", kind="merge", state="acquired", waited_s=600)
        self.run_(); self.assertFalse(os.path.exists(old))


if __name__ == "__main__":
    unittest.main()
