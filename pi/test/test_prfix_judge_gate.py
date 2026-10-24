#!/usr/bin/env python3
"""prfix: when the judge (Opus) may be asked at all (Praise 10-24: "不能任何事情都去问裁决"). Local only: temp dirs, git and gh faked.
Run: cd bin && python3 ../test/test_prfix_judge_gate.py"""
import os as _os
_os.environ.setdefault('PRFIX_OWNERS', '*')
for _k in [k for k in _os.environ if k.startswith('GIT_')]: del _os.environ[_k]
import os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin")); sys.path.insert(0, HERE)
import prfix  # noqa: E402
from test_prfix_unit import Tmp  # noqa: E402
def wf(p, t):
    os.makedirs(os.path.dirname(p), exist_ok=True); open(p, 'w').write(t)

C = {"qa": 1176, "fp": "mq1-aaaa", "sev": "P1", "title": "t", "finding": {"file": "scripts/a.mjs", "line": 3}}


class T(Tmp):
    def setUp(self):
        super().setUp(); self.saved_g = (prfix.git, prfix.main_red_checks, prfix.ruling)
        prfix.git = lambda *a, **k: (0, ""); prfix.main_red_checks = lambda: set(); self.asked = []
        prfix.ruling = lambda kind, c, wd, q, tag: (self.asked.append(kind) or (prfix.RULING_VERDICTS[kind][0], "judge text"))
        self.steps = []; self.step = lambda name, ok, detail: self.steps.append((name, ok))
    def tearDown(self):
        prfix.git, prfix.main_red_checks, prfix.ruling = self.saved_g; super().tearDown()
    def g(self, kind, c=C, **facts): return prfix.gated_ruling(kind, c, self.tmp, "base", "q", "tag", self.step, **facts)

    def test_same_question_on_the_same_code_is_never_asked_twice(self):   # 10-24 evening: no counts, a cache per question
        self.assertEqual(self.g("R1")[2], True)
        v, why, asked = self.g("R1")
        self.assertEqual((v, asked), ("AGREE", False)); self.assertEqual(why, "judge text")              # the earlier verdict, reused
        self.assertTrue(prfix.gated_ruling("R1", C, self.tmp, "newmain", "q", "tag", self.step)[2])      # main moved: a new question
        self.assertTrue(self.g("R2", failing_names=["lint"])[2]); self.assertFalse(self.g("R2", failing_names=["lint"])[2])
        self.assertTrue(self.g("R2", failing_names=["unit"])[2])                                         # other failing checks: new
        self.assertEqual(self.asked, ["R1", "R1", "R2", "R2"])
    def test_no_daily_limit(self):
        for i in range(12): self.assertTrue(self.g("R1", dict(C, fp=f"mq1-{i}"))[2])
    def test_a_judge_without_verdict_is_not_cached(self):
        prfix.ruling = lambda kind, c, wd, q, tag: (self.asked.append(kind) or (None, "garbled"))
        self.assertEqual(self.g("R1")[0], None); self.assertEqual(self.g("R1")[2], True)                # asked again next time
    def test_r1_file_gone_on_main_needs_no_judge(self):
        prfix.git = lambda *a, **k: (128, "fatal")
        self.assertEqual(self.g("R1")[:1] + (self.g("R1")[2],), ("AGREE", False)); self.assertEqual(self.asked, [])
    def test_r2_failure_naming_a_changed_file_is_related_main_red_is_unrelated(self):
        v, why, asked = self.g("R2", failure_text="FAIL scripts/a.test.mjs > x", changed_files=["scripts/a.mjs", "scripts/a.test.mjs"])
        self.assertEqual((v, asked), ("RELATED", False))
        prfix.main_red_checks = lambda: {"unit (windows, 4)"}
        v, why, asked = self.g("R2", failure_text="timeout in other.test.mjs", changed_files=["scripts/a.mjs"], failing_names=["unit (windows, 4)"])
        self.assertEqual((v, asked), ("UNRELATED", False))
        v, _, asked = self.g("R2", dict(C, fp="mq1-b"), failure_text="timeout in other.test.mjs", changed_files=["scripts/a.mjs"],
                             failing_names=["unit (windows, 4)", "lint"])
        self.assertEqual(asked, True)                                     # only part of it is red on main: the judge decides
    def test_r3_edit_of_an_unrelated_old_test_is_rejected_without_judge(self):
        wf(f"{self.tmp}/src/other/b.test.ts", "import { x } from './b'\n")
        v, why, asked = self.g("R3", changed_files=["scripts/a.mjs"], old_tests=["src/other/b.test.ts"])
        self.assertEqual((v, asked), ("REJECT", False))
        wf(f"{self.tmp}/tests/a.test.mjs", "import { a } from '../scripts/a.mjs'\n")
        self.assertEqual(self.g("R3", changed_files=["scripts/a.mjs"], old_tests=["tests/a.test.mjs"])[2], True)


if __name__ == "__main__":
    unittest.main(verbosity=1)
