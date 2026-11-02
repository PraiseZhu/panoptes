#!/usr/bin/env python3
"""prfix raises its own oom_score_adj to +800 so the kernel / earlyoom kill a fix before a test; the off-file turns it off.
Run: cd bin && python3 ../test/test_prfix_oom.py"""
import os as _os
_os.environ.setdefault('PRFIX_OWNERS', '*')
import os, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import prfix  # noqa: E402

class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.adj = os.path.join(self.d, "oom_score_adj"); self.off = os.path.join(self.d, "off")
    def put(self, v): open(self.adj, "w").write(v)
    def test_raises_to_800(self):
        self.put("0\n"); self.assertEqual(prfix.raise_oom_score(self.adj, self.off), 800)
        self.assertEqual(open(self.adj).read(), "800")
    def test_never_lowers(self):
        self.put("900\n"); self.assertEqual(prfix.raise_oom_score(self.adj, self.off), 900)
        self.assertEqual(open(self.adj).read(), "900\n")
    def test_off_file_skips(self):
        self.put("0\n"); open(self.off, "w").close()
        self.assertIsNone(prfix.raise_oom_score(self.adj, self.off)); self.assertEqual(open(self.adj).read(), "0\n")
    def test_unwritable_does_not_raise(self):
        self.assertIsNone(prfix.raise_oom_score(os.path.join(self.d, "missing", "x"), self.off))
    def test_off_path_is_under_mivo_qa_state(self):
        self.assertEqual(prfix.OOM_ADJ_OFF, f"{prfix.BASE}/prfix-oom-adj-off")
    @unittest.skipUnless(os.path.exists("/proc/self/oom_score_adj"), "needs Linux /proc")
    def test_real_proc_child_inherits(self):
        code = ("import sys; sys.path.insert(0, %r); import prfix, subprocess; prfix.raise_oom_score(off=%r); "
                "print(subprocess.check_output(['cat', '/proc/self/oom_score_adj'], text=True).strip())") % (os.path.dirname(prfix.__file__), self.off)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=dict(os.environ, PRFIX_OWNERS="*"))
        self.assertEqual(out.stdout.strip().splitlines()[-1], "800", out.stderr)

if __name__ == "__main__": unittest.main()
