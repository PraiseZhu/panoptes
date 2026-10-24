#!/usr/bin/env python3
"""lib.pi / lib.model_env: a model never sees our tokens (10-24: a manual `prfix --qa 1188` from a Claude session ran
`env | grep -i "pi_|repro|qa"` and 4 real tokens -- inside the value of __CURSOR_SANDBOX_ENV_RESTORE -- went to the model).
Local only: subprocess faked, temp files. Run: cd bin && python3 ../test/test_model_env.py"""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import os, shutil, sys, tempfile, types, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import lib  # noqa: E402

LEAKY = {"MIVO_SLACK_BOT_TOKEN": "xoxb-fake", "MIVO_FEISHU_APP_SECRET": "fake-secret", "MIVO_AUTOMERGE_TOKEN": "ghp_fake",
         "GITHUB_TOKEN": "ghp_fake2", "GH_TOKEN": "ghp_fake3", "QA_LLM_API_KEY": "sk-fake", "CLAUDE_CODE_MESSAGING_TOKEN": "t",
         "__CURSOR_SANDBOX_ENV_RESTORE": "builtin export MIVO_SLACK_BOT_TOKEN='xoxb-fake'", "CLOUD_AGENT_INJECTED_SECRET_NAMES": "A,B",
         "GIT_ASKPASS": "/x", "SAND_NOVNC_TOKEN": "n"}


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="model-env-")
        self.saved_env = dict(os.environ); self.saved = (lib.subprocess.run, lib.SECRET_FILES)
        os.environ.update(LEAKY); os.environ["DEEPSEEK_API_KEY"] = "sk-model"; os.environ["PRFIX_OUT"] = "/x"
    def tearDown(self):
        os.environ.clear(); os.environ.update(self.saved_env); lib.subprocess.run, lib.SECRET_FILES = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)
    def test_pi_passes_only_allowlisted_names(self):
        res = f"{self.tmp}/out.txt"; open(res, "w").write("model text"); seen = {}
        def fake_run(cmd, **kw):
            seen.update(kw["env"]); return types.SimpleNamespace(returncode=0, stdout=f"TASK t1\nRESULT {res}\n", stderr="")
        lib.subprocess.run = fake_run
        self.assertEqual(lib.pi("deep", self.tmp, "p", "s", skills=False), ("model text", "t1"))
        self.assertFalse(set(LEAKY) & set(seen), sorted(set(LEAKY) & set(seen)))
        self.assertFalse([k for k, v in seen.items() if "fake" in v], "no value of a dropped variable survives anywhere")
        for k in ("DEEPSEEK_API_KEY", "PATH", "HOME", "PI_TASK_DIR", "PI_BIN", "PRFIX_OUT"): self.assertIn(k, seen)
    def test_key_comes_from_the_secret_file_when_the_caller_has_none(self):
        del os.environ["DEEPSEEK_API_KEY"]
        f = f"{self.tmp}/env"; open(f, "w").write("export DEEPSEEK_API_KEY=sk-from-file\nMIVO_SLACK_BOT_TOKEN=xoxb-file\n")
        lib.SECRET_FILES = (f,)
        e = lib.model_env()
        self.assertEqual(e.get("DEEPSEEK_API_KEY"), "sk-from-file"); self.assertNotIn("MIVO_SLACK_BOT_TOKEN", e)


    def test_sandbox_hides_secret_places_and_other_processes(self):
        d = f"{self.tmp}/secretdir"; f = f"{self.tmp}/cred.json"; os.makedirs(d); open(f, "w").write("x")
        saved = (lib.SANDBOX_HIDE_DIRS, lib.SANDBOX_HIDE_FILES)
        lib.SANDBOX_HIDE_DIRS, lib.SANDBOX_HIDE_FILES = (d, f"{self.tmp}/missing"), (f,)
        try:
            p = lib.sandbox_prefix()
            if not os.path.exists("/usr/bin/bwrap"): self.assertEqual(p, []); return
            self.assertEqual(p[:4], ["/usr/bin/bwrap", "--ro-bind", "/", "/"]); self.assertIn("--unshare-pid", p); self.assertEqual(p[-1], "--")
            self.assertIn(["--tmpfs", d], [p[i:i + 2] for i in range(len(p))]); self.assertNotIn(f"{self.tmp}/missing", p)
            self.assertIn(["--ro-bind", "/dev/null", f], [p[i:i + 3] for i in range(len(p))])
            os.environ["PI_NO_SANDBOX"] = "1"; self.assertEqual(lib.sandbox_prefix(), [])
        finally: lib.SANDBOX_HIDE_DIRS, lib.SANDBOX_HIDE_FILES = saved

    def test_sandbox_is_read_only_but_for_the_call_s_own_dirs(self):   # 10-24 Q4
        import subprocess
        wd = f"{self.tmp}/wt"; os.makedirs(wd); subprocess.run(["git", "init", "-q", wd], check=True)
        tmp = f"{self.tmp}/private-tmp"; os.makedirs(tmp); other = f"{self.tmp}/other"; os.makedirs(other)
        pairs = lambda p: [p[i:i + 3] for i in range(len(p))]
        p = lib.sandbox_prefix(wd, True, (), tmp)
        if not p: return                                                       # no bwrap here
        self.assertIn(["--bind", wd, wd], pairs(p)); self.assertIn(["--bind", f"{wd}/.git", f"{wd}/.git"], pairs(p))
        self.assertIn(["--bind", tmp, "/tmp"], pairs(p)); self.assertNotIn(["--bind", "/tmp", "/tmp"], pairs(p))
        self.assertTrue(max(i for i, x in enumerate(p) if x == "--bind") < min(i for i, x in enumerate(p) if x == "--tmpfs"))   # hides last
        ro = lib.sandbox_prefix(wd, False)                                      # write=False: its cwd stays read-only, real /tmp
        self.assertNotIn(["--bind", wd, wd], pairs(ro)); self.assertIn(["--bind", "/tmp", "/tmp"], pairs(ro))
        self.assertIn(["--bind", other, other], pairs(lib.sandbox_prefix(wd, True, rw=[other])))
        if subprocess.run(p + ["true"], capture_output=True).returncode != 0: return   # bwrap present but not allowed here (CI)
        r = subprocess.run(p + ["bash", "-c", f"touch {wd}/ok; touch /tmp/ok; touch {other}/no; touch {self.tmp}/no; touch /etc/no; true"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)                             # judged by what reached the real disk:
        self.assertTrue(os.path.exists(f"{wd}/ok") and os.path.exists(f"{tmp}/ok"))   # its worktree + its private /tmp
        self.assertFalse(os.path.exists(f"{other}/no") or os.path.exists(f"{self.tmp}/no") or os.path.exists("/etc/no"))

if __name__ == "__main__":
    unittest.main(verbosity=1)
