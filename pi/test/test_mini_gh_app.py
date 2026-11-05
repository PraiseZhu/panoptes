#!/usr/bin/env python3
"""11-04: the Mac mini (mini/check-queue.sh + the schedules.json agent steps) runs gh as the Praise QA App via mini/praise-qa-gh.sh while
~/.config/praise-qa/gh-app-enabled exists; removing the flag falls back to the mini's own gh login. A mint failure is an error (rc 5 + alert), never the gh login (11-05 R2). Throwaway RSA key, fake curl (it
verifies the RS256 JWT with the throwaway public key) and fake gh; nothing touches GitHub or a real key."""
import json, os, shutil, subprocess, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MINI = os.path.join(ROOT, "mini"); WRAP = os.path.join(MINI, "praise-qa-gh.sh")
TOK = {"178486896": "ghs_" + "M" * 36, "164262283": "ghs_" + "O" * 36}
FAKE_CURL = r'''#!/bin/bash
url="${@: -1}"; id="${url%/access_tokens}"; id="${id##*/}"; jwt=""
for a in "$@"; do case "$a" in "Authorization: Bearer "*) jwt="${a#Authorization: Bearer }";; esac; done
echo "$id" >>"$T/curl.log"
[ -f "$T/curl-fail" ] && { echo "HTTP 401 Bad credentials ghs_SHOULDBEMASKEDxxxxxxxxxxxxxxxxxxxxxxxxx"; exit 22; }
h="${jwt%%.*}"; rest="${jwt#*.}"; p="${rest%%.*}"; s="${rest#*.}"
pad() { local x="$1"; while [ $(( ${#x} % 4 )) -ne 0 ]; do x="$x="; done; printf '%s' "$x" | tr -- '-_' '+/'; }
pad "$s" | openssl base64 -d -A >"$T/sig.bin"
printf '%s.%s' "$h" "$p" | openssl dgst -sha256 -verify "$T/pub.pem" -signature "$T/sig.bin" >/dev/null || { echo '{"message":"bad jwt"}'; exit 0; }
pad "$p" | openssl base64 -d -A | grep -q '"iss":"5055491"' || { echo '{"message":"bad iss"}'; exit 0; }
case "$id" in 178486896) t=TOKM;; 164262283) t=TOKO;; *) t="";; esac
printf '{\n  "token": "%s",\n  "expires_at": "2099-01-01T00:00:00Z"\n}\n' "$t"
'''.replace("TOKM", TOK["178486896"]).replace("TOKO", TOK["164262283"])
FAKE_GH = '#!/bin/bash\necho "GH_TOKEN=${GH_TOKEN:-<gh login>} ARGS=$*" >>"$T/gh.log"\n[ "$1" = "--version" ] && echo "gh version 2"\nexit 0\n'


class T(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp(); t = self.t
        subprocess.run(["openssl", "genrsa", "-out", f"{t}/key.pem", "2048"], check=True, capture_output=True)
        subprocess.run(["openssl", "rsa", "-in", f"{t}/key.pem", "-pubout", "-out", f"{t}/pub.pem"], check=True, capture_output=True)
        for n, body in (("curl", FAKE_CURL), ("gh", FAKE_GH)):
            with open(f"{t}/{n}", "w") as f: f.write(body)
            os.chmod(f"{t}/{n}", 0o755)
        os.makedirs(f"{t}/cfg"); self.flag = f"{t}/cfg/gh-app-enabled"; open(self.flag, "w").close()
        self.env = {"PATH": os.environ["PATH"], "HOME": t, "T": t, "GH_BIN": f"{t}/gh", "PRAISE_QA_CURL": f"{t}/curl", "PRAISE_QA_KEY": f"{t}/key.pem",
                    "PRAISE_QA_FLAG": self.flag, "PRAISE_QA_CACHE": f"{t}/cache", "MIVO_QA_EVIDENCE_DIR": f"{t}/ev", "GITHUB_TOKEN": "ambient"}
    def tearDown(self): shutil.rmtree(self.t, ignore_errors=True)
    def gh(self, *args):
        r = subprocess.run(["/bin/bash", WRAP, *args], env=self.env, capture_output=True, text=True, timeout=60)
        self.assertNotIn("PRIVATE KEY", r.stdout + r.stderr)
        for v in TOK.values(): self.assertNotIn(v, r.stdout + r.stderr)
        return r, open(f"{self.t}/gh.log").read().splitlines()[-1]
    def curls(self):
        p = f"{self.t}/curl.log"; return open(p).read().split() if os.path.exists(p) else []

    def test_installation_per_repo(self):
        self.assertTrue(self.gh("issue", "list", "-R", "xindong/mivo-canvas-plugin")[1].startswith(f"GH_TOKEN={TOK['178486896']} "))
        self.assertTrue(self.gh("issue", "comment", "7", "-R", "PraiseZhu/pr-qa-agent-deploy", "--body", "x")[1].startswith(f"GH_TOKEN={TOK['164262283']} "))
        self.assertTrue(self.gh("api", "repos/PraiseZhu/panoptes/issues")[1].startswith(f"GH_TOKEN={TOK['164262283']} "))
        self.assertTrue(self.gh("issue", "list", "-R", "someone/else")[1].startswith("GH_TOKEN=<gh login> "), "not installed -> gh login")
        self.assertEqual(self.curls(), ["178486896", "164262283"], "one mint per installation, then the cache")
        st = os.stat(f"{self.t}/cache/inst-164262283"); self.assertEqual(st.st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(f"{self.t}/cache").st_mode & 0o777, 0o700)

    def test_rollback_demo_flag_removed_uses_gh_login(self):
        on = self.gh("issue", "list", "-R", "PraiseZhu/pr-qa-agent-deploy")[1]
        os.remove(self.flag)   # rollback: rm ~/.config/praise-qa/gh-app-enabled
        off = self.gh("issue", "list", "-R", "PraiseZhu/pr-qa-agent-deploy")[1]
        print(f"\n[rollback demo]\n  flag on : {on.replace(TOK['164262283'], 'ghs_<praise-qa[bot] token>')}\n  flag off: {off}")
        self.assertTrue(on.startswith("GH_TOKEN=ghs_")); self.assertTrue(off.startswith("GH_TOKEN=<gh login> "))

    def alert(self): return f"{self.t}/.config/praise-qa/alerts/gh_app_mint.json"
    def gh_log(self):
        p = f"{self.t}/gh.log"; return open(p).read().splitlines() if os.path.exists(p) else []
    def raw(self, *args, stdin=None):
        r = subprocess.run(["/bin/bash", WRAP, *args], env=self.env, input=stdin, capture_output=True, text=True, timeout=60)
        self.assertNotIn("PRIVATE KEY", r.stdout + r.stderr); self.assertNotIn("SHOULDBEMASKED", r.stdout + r.stderr)
        return r

    def test_mint_failure_refuses_alerts_and_never_uses_the_gh_login(self):
        """11-05 R2 (panoptes #246 rule): flag on + installed repo + mint failure -> rc 5, gh NOT run, masked line, alert; recovery clears it"""
        open(f"{self.t}/curl-fail", "w").close()
        for n in (1, 2):
            r = self.raw("issue", "list", "-R", "PraiseZhu/pr-qa-agent-deploy")
            self.assertEqual(r.returncode, 5); self.assertEqual(self.gh_log(), [], "gh (the mini's login) must not run")
            self.assertIn("not falling back to the gh login", r.stderr)
            doc = json.load(open(self.alert()))
            self.assertEqual((doc["check"], doc["consecutive_failures"], doc["repo"]), ("gh_app_mint", n, "PraiseZhu/pr-qa-agent-deploy"))
            self.assertNotIn("SHOULDBEMASKED", json.dumps(doc)); self.assertNotIn("ambient", json.dumps(doc))
        os.remove(f"{self.t}/curl-fail")
        self.assertTrue(self.gh("issue", "list", "-R", "PraiseZhu/pr-qa-agent-deploy")[1].startswith(f"GH_TOKEN={TOK['164262283']} "))
        self.assertFalse(os.path.exists(self.alert()), "a successful mint removes the alert")

    def test_missing_key_refuses_too(self):
        os.remove(self.env["PRAISE_QA_KEY"])
        r = self.raw("pr", "list", "-R", "xindong/mivo-canvas-plugin")
        self.assertEqual(r.returncode, 5); self.assertEqual(self.gh_log(), []); self.assertTrue(os.path.exists(self.alert()))

    def test_uninstalled_repo_and_flag_off_keep_the_gh_login(self):
        open(f"{self.t}/curl-fail", "w").close()
        self.assertTrue(self.gh("issue", "list", "-R", "someone/else")[1].startswith("GH_TOKEN=<gh login> "))
        os.remove(self.flag)
        r, line = self.gh("issue", "list", "-R", "PraiseZhu/pr-qa-agent-deploy")
        self.assertEqual(r.returncode, 0); self.assertTrue(line.startswith("GH_TOKEN=<gh login> ")); self.assertFalse(os.path.exists(self.alert()))

    REQ = "protocol=https\nhost=github.com\npath=xindong/mivo-canvas-plugin.git\n\n"
    def test_git_credential_get_app_token(self):
        r = self.raw("auth", "git-credential", "get", stdin=self.REQ)
        self.assertEqual(r.returncode, 0)
        self.assertIn("username=x-access-token\n", r.stdout); self.assertIn(f"password={TOK['178486896']}\n", r.stdout)
        self.assertEqual(self.gh_log(), [])

    def test_git_credential_mint_failure_gives_no_password(self):
        open(f"{self.t}/curl-fail", "w").close()
        r = self.raw("auth", "git-credential", "get", stdin=self.REQ)
        self.assertEqual(r.returncode, 5); self.assertNotIn("password", r.stdout); self.assertEqual(r.stdout, "")
        self.assertEqual(self.gh_log(), [], "the gh login's credential helper must not be asked"); self.assertTrue(os.path.exists(self.alert()))

    def test_git_credential_other_repo_store_erase(self):
        r = self.raw("auth", "git-credential", "get", stdin="protocol=https\nhost=github.com\npath=someone/else.git\n\n")
        self.assertEqual(r.returncode, 0); self.assertIn("ARGS=auth git-credential get", self.gh_log()[-1])
        for op in ("store", "erase"):
            n = len(self.gh_log()); r = self.raw("auth", "git-credential", op, stdin=self.REQ)
            self.assertEqual((r.returncode, r.stdout, len(self.gh_log())), (0, "", n))

    def test_check_queue_reads_the_queue_as_the_app(self):
        def run():
            r = subprocess.run(["/bin/bash", os.path.join(MINI, "check-queue.sh")], env=self.env, capture_output=True, text=True, timeout=60)
            self.assertNotIn("PRIVATE KEY", r.stdout + r.stderr)
            return [l for l in open(f"{self.t}/gh.log").read().splitlines() if "issue list" in l][-1]
        self.assertTrue(run().startswith(f"GH_TOKEN={TOK['164262283']} ARGS=issue list -R PraiseZhu/pr-qa-agent-deploy"))
        os.remove(self.flag)
        self.assertTrue(run().startswith("GH_TOKEN=<gh login> "), "rollback -> the mini's gh login")

    def test_check_queue_mint_failure_exits_3_without_gh(self):
        open(f"{self.t}/curl-fail", "w").close()
        r = subprocess.run(["/bin/bash", os.path.join(MINI, "check-queue.sh")], env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 3); self.assertNotIn("SHOULDBEMASKED", r.stdout + r.stderr)
        self.assertEqual([l for l in self.gh_log() if "issue list" in l], [], "queue not read with the gh login"); self.assertTrue(os.path.exists(self.alert()))

    def test_schedule_agent_steps_use_the_wrapper(self):
        p = json.load(open(os.path.join(MINI, "schedules.json")))["schedules"][0]["prompt"]
        for step in ("issue comment", "issue edit", "label create"): self.assertIn(f"__MINI_QA_DIR__/praise-qa-gh.sh {step}", p)
        import re; self.assertEqual(re.findall(r"(?<![\w/.-])gh (?:issue|label|api|pr) ", p), [])


if __name__ == "__main__": unittest.main()
