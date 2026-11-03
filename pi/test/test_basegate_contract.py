#!/usr/bin/env python3
"""11-03 integration-branch QA: pi/bin/basegate.py and argus/src/config.mjs (shouldTestBase) run the same case file
(argus/tests/fixtures/skip-base-cases.json), so mergewatch / watchdog (Python) and catch-up / nightly (JS) cannot drift apart again."""
import json, os, sys, tempfile, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE)); sys.path.insert(0, os.path.join(ROOT, "pi", "bin"))
import basegate  # noqa: E402
CASES = json.load(open(os.path.join(ROOT, "argus", "tests", "fixtures", "skip-base-cases.json")))["cases"]

class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.env = mock.patch.dict(os.environ, {"PI_STATE": self.d}); self.env.start()
        os.environ.pop("PANOPTES_MAIN_ONLY", None)
    def tearDown(self): self.env.stop()

    def test_contract_cases(self):
        for c in CASES:
            with self.subTest(c=c):
                self.assertEqual(basegate.matches_skip(c["base"], c["globs"]), c["skip"])
                self.assertEqual(basegate.should_test(c["base"], c["globs"], mainonly=False), c["test"])
                self.assertEqual(basegate.should_test(c["base"], c["globs"], mainonly=True), c["test_main_only"])

    def test_registry_is_the_source(self):
        self.assertEqual(basegate.skip_globs(), ["probe-*"])   # argus/config/registry.json
        p = os.path.join(self.d, "reg.json"); json.dump({"skipBaseBranches": ["probe-*", "feat/*"]}, open(p, "w"))
        self.assertEqual(basegate.skip_globs(p), ["probe-*", "feat/*"])
        self.assertEqual(basegate.skip_globs(os.path.join(self.d, "nope.json")), ["probe-*"])

    def test_main_only_env_and_flag_file_reread_each_call(self):
        self.assertTrue(basegate.should_test("integration/canvas-s2"))
        with mock.patch.dict(os.environ, {"PANOPTES_MAIN_ONLY": "1"}):
            self.assertFalse(basegate.should_test("integration/canvas-s2")); self.assertTrue(basegate.should_test("main"))
        open(os.path.join(self.d, "panoptes-main-only"), "w").close()
        self.assertFalse(basegate.should_test("feat/video-in-canvas"))
        os.remove(os.path.join(self.d, "panoptes-main-only"))
        self.assertTrue(basegate.should_test("feat/video-in-canvas"))

if __name__ == "__main__":
    unittest.main()
