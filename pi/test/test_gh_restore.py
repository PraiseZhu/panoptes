#!/usr/bin/env python3
"""11-10: pi/restore/gh-wrapper/install.sh + verify.sh, run only against temp dirs: a fake native gh, a temp wrapper path, a temp
global git config (GIT_CONFIG_GLOBAL) and a temp $PI_STATE. GH_RESTORE_SUDO is empty, so nothing escalates. No network, no token."""
import glob, os, re, subprocess, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(os.path.dirname(HERE), "restore", "gh-wrapper")
FLAGS = ("gh-app-enabled gh-app-wrapper-enabled gh-app-writes-enabled gh-app-product-enabled gh-app-product-writes-enabled "
         "gh-app-merger-enabled gh-app-mergewatch-enabled gh-app-nightly-enabled gh-app-ledgers-enabled").split()


def read(path):
    with open(path) as f: return f.read()


def script(path, body):
    with open(path, "w") as f: f.write("#!/bin/sh\n" + body + "\n")
    os.chmod(path, 0o755); return path


class Base(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="gh-restore-")
        for d in ("usr/bin", "usr/local/bin", "panoptes/pi/bin", "state", "home"): os.makedirs(os.path.join(self.t, d))
        p = lambda *a: os.path.join(self.t, *a)
        self.link, self.real, self.state = p("usr/local/bin/gh"), p("usr/bin/gh"), p("state")
        self.wrapper = script(p("panoptes/pi/bin/gh_wrapper.py"), "echo wrapper")
        self.gitcfg = p("home/.gitconfig"); open(self.gitcfg, "w").close()
        self.env = dict(PATH=os.environ["PATH"], HOME=p("home"), GIT_CONFIG_GLOBAL=self.gitcfg, GIT_CONFIG_NOSYSTEM="1",
                        GH_RESTORE_LINK=self.link, GH_RESTORE_REAL=self.real, GH_RESTORE_WRAPPER=self.wrapper,
                        PI_STATE=self.state, GH_RESTORE_SUDO="")
        for k, v in self.env.items():
            if k.startswith(("GH_RESTORE_", "PI_STATE", "GIT_CONFIG_GLOBAL")) and v: self.assertTrue(v.startswith(self.t), k)

    def tearDown(self): subprocess.run(["rm", "-rf", self.t])

    def run_sh(self, name, **extra):
        return subprocess.run(["bash", os.path.join(D, name)], env={**self.env, **extra}, capture_output=True, text=True, timeout=60)

    def git(self, *a):
        return subprocess.run(["git", "config", "--global", *a], env=self.env, capture_output=True, text=True).stdout

    def snapshot(self):
        st = {}
        for root, dirs, files in os.walk(self.t):
            for n in dirs + files:
                q = os.path.join(root, n); s = os.lstat(q)
                st[q] = (s.st_mode, s.st_ino, s.st_mtime_ns, os.readlink(q) if os.path.islink(q) else None)
        return st


class Install(Base):
    def test_native_at_link_moved_to_real_and_link_to_wrapper(self):
        script(self.link, "echo native")   # what a gh reinstall leaves: the native binary at /usr/local/bin/gh
        for f in FLAGS: open(os.path.join(self.state, f), "w").close()
        r = self.run_sh("install.sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.islink(self.link)); self.assertEqual(os.path.realpath(self.link), os.path.realpath(self.wrapper))
        self.assertEqual(read(self.real), "#!/bin/sh\necho native\n"); self.assertTrue(os.access(self.real, os.X_OK))
        self.assertEqual(self.git("--get-all", "credential.https://github.com.helper"), f"\n!{self.link} auth git-credential\n")
        self.assertEqual(self.git("--get-all", "credential.https://gist.github.com.helper"), f"\n!{self.link} auth git-credential\n")
        self.assertEqual(self.git("--get", "credential.useHttpPath"), "true\n")
        self.assertNotIn("NEED", r.stdout)

    def test_existing_real_kept_and_wrong_helper_replaced(self):
        script(self.real, "echo real"); script(self.link, "echo other-native")
        subprocess.run(["git", "config", "--global", "credential.https://github.com.helper", "store"], env=self.env, check=True)
        r = self.run_sh("install.sh")
        self.assertEqual(read(self.real), "#!/bin/sh\necho real\n")   # never overwritten
        self.assertEqual(os.path.realpath(self.link), os.path.realpath(self.wrapper))
        self.assertEqual(self.git("--get-all", "credential.https://github.com.helper"), f"\n!{self.link} auth git-credential\n")
        self.assertEqual(r.returncode, 3)   # switches missing

    def test_second_run_is_a_noop(self):
        script(self.link, "echo native")
        self.run_sh("install.sh")
        before, cfg = self.snapshot(), read(self.gitcfg)
        r = self.run_sh("install.sh")
        self.assertEqual(self.snapshot(), before); self.assertEqual(read(self.gitcfg), cfg)
        self.assertNotIn("installed", r.stdout); self.assertNotIn("set git", r.stdout)

    def test_missing_switches_reported_never_created(self):
        script(self.link, "echo native")
        open(os.path.join(self.state, "gh-app-enabled"), "w").close()
        r = self.run_sh("install.sh")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        need = [l for l in r.stdout.splitlines() if l.startswith("NEED ")]
        self.assertEqual(len(need), 8)
        self.assertEqual(sorted(os.listdir(self.state)), ["gh-app-enabled"])

    def test_switch_list_matches_the_code(self):
        root = os.path.dirname(os.path.dirname(HERE))
        used = set()
        for f in glob.glob(os.path.join(root, "pi/bin/*.py")) + glob.glob(os.path.join(root, "scripts/*.sh")) + [os.path.join(root, "run-nightly.sh")]:
            used |= set(re.findall(r"gh-app-(?:[a-z-]+-)?enabled", read(f)))
        listed = re.search(r"FLAGS=\(([^)]*)\)", read(os.path.join(D, "install.sh"))).group(1).split()
        self.assertEqual(sorted(listed), sorted(used)); self.assertEqual(sorted(FLAGS), sorted(used)); self.assertEqual(len(used), 9)

    def test_no_native_gh_fails_without_touching_link(self):
        os.symlink("/nonexistent/gh", self.link)
        r = self.run_sh("install.sh")
        self.assertEqual(r.returncode, 2); self.assertIn("no native gh", r.stderr)
        self.assertEqual(os.readlink(self.link), "/nonexistent/gh")


class Verify(Base):
    def setUp(self):
        super().setUp()
        self.helper = script(os.path.join(self.t, "helper"), 'cat >/dev/null; [ "$1" = get ] && printf "username=%s\\npassword=fake\\n" "${CRED_USER:-x-access-token}"')
        self.fake_gh = script(os.path.join(self.t, "fakegh"), 'echo "${LOGIN-praise-qa[bot]}"')
        os.symlink(self.wrapper, self.link)
        subprocess.run(["git", "config", "--global", "credential.https://github.com.helper", self.helper], env=self.env, check=True)
        subprocess.run(["git", "config", "--global", "credential.useHttpPath", "true"], env=self.env, check=True)
        self.env["GH_RESTORE_GH"] = self.fake_gh

    def test_all_pass(self):
        r = self.run_sh("verify.sh")
        self.assertEqual(r.returncode, 0, r.stdout); self.assertEqual(r.stdout.count("OK "), 3)
        self.assertNotIn("fake", r.stdout + r.stderr)   # the password is never printed

    def test_wrapper_not_linked(self):
        os.remove(self.link); script(self.link, "echo native")
        r = self.run_sh("verify.sh")
        self.assertEqual(r.returncode, 1); self.assertIn("FAIL wrapper", r.stdout); self.assertEqual(r.stdout.count("FAIL"), 1)

    def test_identity_is_a_person(self):
        r = self.run_sh("verify.sh", LOGIN="PraiseZhu")
        self.assertEqual(r.returncode, 1); self.assertIn("FAIL identity: gh api user = 'PraiseZhu'", r.stdout); self.assertEqual(r.stdout.count("FAIL"), 1)

    def test_credential_not_app(self):
        r = self.run_sh("verify.sh", CRED_USER="PraiseZhu")
        self.assertEqual(r.returncode, 1); self.assertIn("FAIL credential: username='PraiseZhu'", r.stdout)
        self.assertEqual(r.stdout.count("FAIL"), 1); self.assertNotIn("fake", r.stdout + r.stderr)


if __name__ == "__main__": unittest.main()
