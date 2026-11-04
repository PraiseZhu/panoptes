#!/usr/bin/env python3
"""11-04: the Mac mini (mini/check-queue.sh + the schedules.json agent steps) runs gh as the Praise QA App via mini/praise-qa-gh.sh while
~/.config/praise-qa/gh-app-enabled exists; removing the flag falls back to the mini's own gh login. Throwaway RSA key, fake curl (it
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

    def test_mint_failure_falls_back_masked(self):
        open(f"{self.t}/curl-fail", "w").close()
        r, line = self.gh("issue", "list", "-R", "PraiseZhu/pr-qa-agent-deploy")
        self.assertTrue(line.startswith("GH_TOKEN=<gh login> ")); self.assertIn("using the gh login", r.stderr)
        self.assertNotIn("SHOULDBEMASKED", r.stderr); self.assertEqual(r.returncode, 0)
        os.remove(f"{self.t}/curl-fail"); os.remove(self.env["PRAISE_QA_KEY"])
        self.assertTrue(self.gh("issue", "list", "-R", "PraiseZhu/pr-qa-agent-deploy")[1].startswith("GH_TOKEN=<gh login> "), "no key -> gh login")

    def test_check_queue_reads_the_queue_as_the_app(self):
        def run():
            r = subprocess.run(["/bin/bash", os.path.join(MINI, "check-queue.sh")], env=self.env, capture_output=True, text=True, timeout=60)
            self.assertNotIn("PRIVATE KEY", r.stdout + r.stderr)
            return [l for l in open(f"{self.t}/gh.log").read().splitlines() if "issue list" in l][-1]
        self.assertTrue(run().startswith(f"GH_TOKEN={TOK['164262283']} ARGS=issue list -R PraiseZhu/pr-qa-agent-deploy"))
        os.remove(self.flag)
        self.assertTrue(run().startswith("GH_TOKEN=<gh login> "), "rollback -> the mini's gh login")

    def test_schedule_agent_steps_use_the_wrapper(self):
        p = json.load(open(os.path.join(MINI, "schedules.json")))["schedules"][0]["prompt"]
        for step in ("issue comment", "issue edit", "label create"): self.assertIn(f"__MINI_QA_DIR__/praise-qa-gh.sh {step}", p)
        import re; self.assertEqual(re.findall(r"(?<![\w/.-])gh (?:issue|label|api|pr) ", p), [])


if __name__ == "__main__": unittest.main()
