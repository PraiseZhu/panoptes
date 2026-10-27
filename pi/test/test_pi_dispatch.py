#!/usr/bin/env python3
"""pi_dispatch.py (fleet parallel Pi tasks, memory gate), prwatch counting dispatched tasks, pstack install + pi-noskills.sh.
Local only: no network, no model, writes only in a temp dir. Run: python3 test/test_pi_dispatch.py"""
for _k in [k for k in __import__("os").environ if k.startswith('GIT_')]: del __import__("os").environ[_k]
import json, os, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); BIN = os.path.join(os.path.dirname(HERE), "bin")
sys.path.insert(0, BIN)
import pi_dispatch as D, prwatch, pstack_sync as S  # noqa: E402

RUN1 = b"python3\0/x/pi/bin/pi_dispatch.py\0--run-one\0{}\0"
PY = b"python3\0/x/pi/bin/prfix.py\0--next\0"

class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.saved = (D.BASE, D.room_and_need, D.launch, D.LOCK)
        D.BASE = self.tmp; D.LOCK = f"{self.tmp}/lock"
    def tearDown(self): D.BASE, D.room_and_need, D.launch, D.LOCK = self.saved

    def test_specs(self):
        self.assertEqual(D.parse_spec("qa:12:abc")["args"], ["--qa", "12", "--fp", "abc"])
        self.assertEqual(D.parse_spec("revise:7")["args"], ["--revise", "7"])
        self.assertEqual(D.parse_spec("next")["args"], ["--next"])
        for bad in ("qa:", "rm -rf /", "revise:x"): self.assertRaises(ValueError, D.parse_spec, bad)

    def test_free_form_validation(self):
        self.assertEqual(D.norm_task({"prompt": "p", "cwd": self.tmp, "name": "a b"})["name"], "a-b")
        self.assertRaises(ValueError, D.norm_task, {"prompt": "p"})
        self.assertRaises(ValueError, D.norm_task, {"prompt": "p", "cwd": self.tmp, "repo": self.tmp})
        self.assertRaises(ValueError, D.norm_task, {"prompt": "", "cwd": self.tmp})
        self.assertRaises(ValueError, D.norm_task, {"prompt": "p", "cwd": self.tmp, "tier": "max"})

    def test_waits_for_memory_then_launches(self):
        rooms = iter([100, 200, 5000]); D.room_and_need = lambda t: (next(rooms), 3000)
        sleeps = []
        self.assertTrue(D.wait_admit("c", {"name": "a"}, 999, sleep=sleeps.append, clock=lambda: 0))
        self.assertEqual(len(sleeps), 2)

    def test_gives_up_after_max_wait(self):
        D.room_and_need = lambda t: (0, 3000); clock = iter(range(0, 10000, 50))
        self.assertFalse(D.wait_admit("c", {"name": "a"}, 100, sleep=lambda s: None, clock=lambda: next(clock)))

    def test_dispatch_never_launches_unadmitted(self):
        D.room_and_need = lambda t: (0, 3000); launched = []
        D.launch = lambda c, t, dry=False: launched.append(t) or "id"
        r = D.dispatch("c", [D.parse_spec("next")], max_wait=0)
        self.assertEqual(r, [("next", None)]); self.assertEqual(launched, [])

    def test_ticket_argv_reuses_prfix(self):
        self.assertEqual(D.argv_for(D.parse_spec("qa:3"), "c", "i")[1:], ["prfix", "--qa", "3"])
        a = D.argv_for(D.norm_task({"prompt": "p", "cwd": self.tmp}), "c", "i")
        self.assertEqual(a[2], "--run-one"); self.assertEqual(json.loads(a[3])["caller"], "c")

    def test_prwatch_counts_dispatched_tasks(self):
        t = {10: (1, 100, RUN1)}
        self.assertEqual(prwatch.dispatch_runs(t), [10])
        mem = 20000; st = {}
        h = prwatch.headroom_mb(t, mem=mem, st=st)
        self.assertEqual(h, mem - (prwatch.TASK_PEAK_MB - 100) - prwatch.PANOPTES_PEAK_MB)
        self.assertEqual(prwatch.headroom_mb({}, mem=mem, st=st), mem)
        self.assertEqual(prwatch.dispatch_runs({11: (1, 5, PY)}), [])

    def test_pstack_flag_dropped_only_in_frontmatter(self):
        src = "---\nname: how\ndisable-model-invocation: true\n---\nbody disable-model-invocation: true\n"
        self.assertEqual(S.enable_model_invocation(src), "---\nname: how\n---\nbody disable-model-invocation: true\n")
        self.assertEqual(S.enable_model_invocation("no frontmatter"), "no frontmatter")

    def test_pstack_build_swaps_in(self):
        src = f"{self.tmp}/src/how"; os.makedirs(src)
        open(f"{src}/SKILL.md", "w").write("---\nname: how\ndisable-model-invocation: true\n---\nx\n")
        tgt = f"{self.tmp}/skills/pstack"
        for _ in range(2): self.assertEqual(S.build(f"{self.tmp}/src", tgt, {"commit": "c"}), 1)
        self.assertNotIn("disable-model", open(f"{tgt}/how/SKILL.md").read())
        self.assertEqual(json.load(open(f"{tgt}/.pstack-source.json"))["commit"], "c")
        self.assertEqual(sorted(os.listdir(f"{self.tmp}/skills")), ["pstack"])

    def test_noskills_wrapper_passes_pstack(self):
        stub = f"{self.tmp}/pi"; open(stub, "w").write('#!/bin/bash\necho "$@"\n'); os.chmod(stub, 0o755)
        env = dict(os.environ, PI_REAL_BIN=stub, PI_PSTACK_DIR=self.tmp)
        out = subprocess.run([f"{BIN}/pi-noskills.sh", "-p", "x"], capture_output=True, text=True, env=env).stdout.split()
        self.assertEqual(out, ["--no-skills", "--skill", self.tmp, "-p", "x"])
        env["PI_PSTACK_DIR"] = f"{self.tmp}/missing"
        out = subprocess.run([f"{BIN}/pi-noskills.sh", "-p", "x"], capture_output=True, text=True, env=env).stdout.split()
        self.assertEqual(out, ["--no-skills", "-p", "x"])

if __name__ == "__main__": unittest.main()
