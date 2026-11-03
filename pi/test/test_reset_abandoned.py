import json, os, subprocess, sys, tempfile, unittest
BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
sys.path.insert(0, BIN)
import reset_abandoned as R  # noqa: E402

GU = {"status": "gave_up", "last_error": "push declined due to emai", "fails": 5}
KEYS = ["fixpr:1512:mq1-a", "fixpr:1566:mq1-b"]
class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.p = os.path.join(self.d, "ledger.json")
        self.led = {k: dict(GU) for k in KEYS}
        self.led.update({"fixpr:1566:mq1-b:a2": dict(GU), "fixpr:1519:mq1-c": dict(GU), "daily:1": {"status": "done"}})
        json.dump(self.led, open(self.p, "w"), indent=1)
    def test_only_named_keys_and_idempotent(self):
        self.assertEqual(sorted(R.reset(self.p, KEYS)), sorted(KEYS))
        after = json.load(open(self.p))
        self.assertEqual(after, {k: v for k, v in self.led.items() if k not in KEYS})
        raw = open(self.p, "rb").read()
        self.assertEqual(R.reset(self.p, KEYS), []); self.assertEqual(open(self.p, "rb").read(), raw)
    def test_dry_run_and_non_gave_up_untouched(self):
        self.led[KEYS[0]] = {"status": "done"}; json.dump(self.led, open(self.p, "w"))
        raw = open(self.p, "rb").read()
        self.assertEqual(R.reset(self.p, KEYS + ["daily:1", "missing:1"], dry_run=True), [KEYS[1]])
        self.assertEqual(open(self.p, "rb").read(), raw)
        R.reset(self.p, KEYS + ["daily:1"])
        after = json.load(open(self.p))
        self.assertEqual(after[KEYS[0]], {"status": "done"}); self.assertEqual(after["daily:1"], {"status": "done"})
        self.assertNotIn(KEYS[1], after)
    def test_one_key_per_fingerprint(self):   # 11-03 B: two gave_up keys of one finding -> only the first is reset
        self.assertEqual(R.reset(self.p, ["fixpr:1566:mq1-b", "fixpr:1566:mq1-b:a2", "fixpr:1519:mq1-c"]), ["fixpr:1566:mq1-b", "fixpr:1519:mq1-c"])
        self.assertIn("fixpr:1566:mq1-b:a2", json.load(open(self.p)))
    def test_cli_requires_keys_and_reports_skipped(self):
        env = dict(os.environ, PI_LOCK_DIR=self.d)
        cmd = [sys.executable, os.path.join(BIN, "reset_abandoned.py"), "--ledger", self.p]
        self.assertNotEqual(subprocess.run(cmd, env=env, capture_output=True).returncode, 0)
        raw = open(self.p, "rb").read()
        r = subprocess.run(cmd + ["--dry-run", "--keys", f"{KEYS[0]},daily:1"], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1]), {"reset": [KEYS[0]], "skipped": ["daily:1"], "dry_run": True})
        self.assertEqual(open(self.p, "rb").read(), raw)

if __name__ == "__main__": unittest.main()
