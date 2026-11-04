#!/usr/bin/env python3
"""11-04 (pi-work/ratelimit-1104/FINDINGS.md): PraiseZhu's GraphQL quota ran out; lib.gh kept sending doomed calls every minute
and prwatch counted each as a failure. Now a primary/secondary rate limit records its reset in lib.GH_RL_FILE, later calls are not
sent until then, prwatch skips its GitHub steps without touching the error streak (#196), and watchdog raises one gh:ratelimit item
after GH_RL_ALERT_H hours. A fake `gh` on PATH, temp dirs only, no network. Run: cd bin && python3 ../test/test_gh_ratelimit.py"""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import json, os, shutil, stat, sys, tempfile, time, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import lib, prwatch, watchdog  # noqa: E402

FAKE = """#!/bin/sh
echo "$*" >> "$GHF_DIR/calls"
if [ "$1 $2" = "api rate_limit" ]; then echo "{\\"resources\\": {\\"graphql\\": {\\"remaining\\": 0, \\"reset\\": $GHF_RESET}}}"; exit 0; fi
if [ -n "$GHF_ERR" ]; then printf '%s\\n' "$GHF_ERR" >&2; exit 1; fi
echo '[{"number": 1}]'
"""
PRIMARY = "GraphQL: API rate limit exceeded for user ID 290989140."
SECONDARY = "HTTP 403: You have exceeded a secondary rate limit. Please wait a few minutes before you try again. Retry-After: 120"
READ = ["pr", "list", "-R", "o/r", "--json", "number"]

class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="gh-rl-"); f = f"{self.d}/gh"
        with open(f, "w") as fh: fh.write(FAKE)
        os.chmod(f, os.stat(f).st_mode | stat.S_IEXEC)
        self.env = {k: os.environ.get(k) for k in ("PATH", "GHF_DIR", "GHF_ERR", "GHF_RESET")}
        self.reset = int(time.time()) + 1800
        os.environ.update(PATH=f"{self.d}:{os.environ['PATH']}", GHF_DIR=self.d, GHF_ERR="", GHF_RESET=str(self.reset))
        self.saved = (getattr(lib, "GH_RL_FILE", None), lib.time, prwatch.qlog, prwatch.ERR_ALERT_ONCE)
        lib.GH_RL_FILE = self.f = f"{self.d}/gh-ratelimit.json"
        lib.time = type("T", (), {"sleep": staticmethod(lambda s: None), "time": staticmethod(time.time)})
        prwatch.ERR_ALERT_ONCE = f"{self.d}/no-flag"; self.logs = []; prwatch.qlog = self.logs.append
        if hasattr(prwatch, "_rl_logged"): prwatch._rl_logged.clear()
    def tearDown(self):
        for k, v in self.env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        f, lib.time, prwatch.qlog, prwatch.ERR_ALERT_ONCE = self.saved
        if f is None: delattr(lib, "GH_RL_FILE")
        else: lib.GH_RL_FILE = f
        shutil.rmtree(self.d, ignore_errors=True)
    def calls(self):
        try: return [l for l in open(f"{self.d}/calls").read().splitlines() if not l.startswith("api rate_limit")]
        except FileNotFoundError: return []

    def test_primary_limit_records_reset_and_stops_sending(self):
        os.environ["GHF_ERR"] = PRIMARY
        with self.assertRaises(RuntimeError): lib.gh(READ)
        self.assertEqual(json.load(open(self.f))["until"], self.reset)       # reset read from rate_limit
        with self.assertRaises(RuntimeError) as cm: lib.gh(READ)
        self.assertIn("rate-limited", str(cm.exception))
        self.assertEqual(len(self.calls()), 1)                               # the 2nd call never reached GitHub
    def test_secondary_limit_uses_retry_after(self):
        os.environ["GHF_ERR"] = SECONDARY
        with self.assertRaises(RuntimeError): lib.gh(READ)
        self.assertAlmostEqual(json.load(open(self.f))["until"], time.time() + 120, delta=5)
    def test_x_ratelimit_reset_header(self):
        os.environ["GHF_ERR"] = f"HTTP 429: Too Many Requests\nX-RateLimit-Reset: {self.reset}"
        with self.assertRaises(RuntimeError): lib.gh(READ)
        self.assertEqual(json.load(open(self.f))["until"], self.reset)
    def test_after_reset_success_clears_file(self):
        json.dump({"since": time.time() - 99, "until": time.time() - 1}, open(self.f, "w"))
        self.assertEqual(lib.gh(READ), [{"number": 1}]); self.assertFalse(os.path.exists(self.f))
    def test_other_errors_not_rate_limit(self):
        os.environ["GHF_ERR"] = "GraphQL: Could not resolve to a Repository"
        with self.assertRaises(RuntimeError): lib.gh(READ)
        self.assertFalse(os.path.exists(self.f))
    def test_prwatch_skip_is_not_a_failure(self):
        json.dump({"since": time.time(), "until": time.time() + 600}, open(self.f, "w"))
        st = {"error_counts": {"drafts": 2}}; ran = []
        for name in ("drafts", "resolved", "emerg"): prwatch.run_step(st, name, lambda s, n=name: ran.append(n))
        self.assertEqual(ran, ["emerg"])                                     # GitHub steps skipped, local ones still run
        self.assertEqual(st["error_counts"], {"drafts": 2})                 # streak neither grown nor reset
        self.assertNotIn("errors", st); self.assertNotIn("error_alerts", st)
        self.assertEqual(len([l for l in self.logs if "rate-limited" in l]), 1)   # one line per run
    def test_prwatch_limit_hit_mid_step_not_counted(self):
        os.environ["GHF_ERR"] = PRIMARY; st = {}
        for _ in range(3): prwatch.run_step(st, "grok", lambda s: lib.gh(READ))
        self.assertFalse((st.get("error_counts") or {}).get("grok")); self.assertNotIn("error_alerts", st)
    def test_watchdog_alerts_once_after_2h(self):
        now = watchdog.now_cst()
        json.dump({"since": time.time() - 3600, "until": time.time() + 600}, open(self.f, "w"))
        p = {}; watchdog.gh_ratelimit_check(now, p); self.assertNotIn("gh:ratelimit", p)
        json.dump({"since": time.time() - 2.5 * 3600, "until": time.time() + 600}, open(self.f, "w"))
        p = {}; watchdog.gh_ratelimit_check(now, p); self.assertIn("gh:ratelimit", p)   # one stable key: main sends it once
        json.dump({"since": time.time() - 9 * 3600, "until": time.time() - 1}, open(self.f, "w"))
        p = {}; watchdog.gh_ratelimit_check(now, p); self.assertNotIn("gh:ratelimit", p) # reset passed -> recovers

    # ---- #198 acceptance 3(a) ----
    def test_huge_until_clamped(self):
        os.environ["GHF_ERR"] = "HTTP 403: secondary rate limit\nRetry-After: 1000000000"
        with self.assertRaises(RuntimeError): lib.gh(READ)
        self.assertLessEqual(lib.gh_rl_until(), time.time() + 2 * 3600 + 5)
        json.dump({"since": time.time(), "until": time.time() + 1e9}, open(self.f, "w"))   # already on disk: ignored, calls go out
        self.assertEqual(lib.gh_rl_until(), 0)
        os.environ["GHF_ERR"] = ""; self.assertEqual(lib.gh(READ), [{"number": 1}]); self.assertFalse(os.path.exists(self.f))
    def test_expired_until_sends_and_clears(self):
        json.dump({"since": time.time() - 9999, "until": time.time() - 5}, open(self.f, "w"))
        self.assertEqual(lib.gh_rl_until(), 0)
        self.assertEqual(lib.gh(READ), [{"number": 1}]); self.assertEqual(len(self.calls()), 1); self.assertFalse(os.path.exists(self.f))
    def test_corrupt_state_ignored(self):
        for bad in ({"until": "x"}, [1, 2], {"until": time.time() + 600, "since": "y"}):
            json.dump(bad, open(self.f, "w"))
            self.assertEqual(lib.gh_rl_until(), 0)
            self.assertEqual(lib.gh(READ), [{"number": 1}])
            p = {}; watchdog.gh_ratelimit_check(watchdog.now_cst(), p); self.assertEqual(p, {})
    def test_pr_number_429_is_not_a_rate_limit(self):
        for err in ("GraphQL: Could not resolve to a PullRequest with the number of 429. (repository.pullRequest)",
                    "pull request #429 is not mergeable"):
            os.environ["GHF_ERR"] = err
            with self.assertRaises(RuntimeError): lib.gh(READ)
            self.assertFalse(os.path.exists(self.f), err)
    def test_http_429_status_is_a_rate_limit(self):
        os.environ["GHF_ERR"] = "HTTP 429: Too Many Requests (https://api.github.com/graphql)"
        with self.assertRaises(RuntimeError): lib.gh(READ)
        self.assertTrue(os.path.exists(self.f))

if __name__ == "__main__": unittest.main()
