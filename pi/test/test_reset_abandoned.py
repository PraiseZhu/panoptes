import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import reset_abandoned as R  # noqa: E402

GU = {"status": "gave_up", "last_error": "push declined due to emai", "fails": 5}
class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.p = os.path.join(self.d, "ledger.json")
        self.led = {k: dict(GU) for k in R.ALLOWLIST}
        self.led.update({"fixpr:1512:mq1-x": dict(GU), "revise:1559:other": dict(GU), "daily:1": {"status": "done"}})
        json.dump(self.led, open(self.p, "w"), indent=1)
    def test_only_allowlist_and_idempotent(self):
        self.assertEqual(sorted(R.reset(self.p)), sorted(R.ALLOWLIST))
        after = json.load(open(self.p))
        self.assertEqual(after, {k: v for k, v in self.led.items() if k not in R.ALLOWLIST})
        raw = open(self.p, "rb").read()
        self.assertEqual(R.reset(self.p), []); self.assertEqual(open(self.p, "rb").read(), raw)
    def test_dry_run_and_non_gave_up_untouched(self):
        self.led[R.ALLOWLIST[0]] = {"status": "done"}; json.dump(self.led, open(self.p, "w"))
        raw = open(self.p, "rb").read()
        self.assertEqual(len(R.reset(self.p, dry_run=True)), 2); self.assertEqual(open(self.p, "rb").read(), raw)
        R.reset(self.p); self.assertEqual(json.load(open(self.p))[R.ALLOWLIST[0]], {"status": "done"})

if __name__ == "__main__": unittest.main()
