#!/usr/bin/env python3
"""watchdog: outbound keys + ledger health (10-24: after the restore the Slack / Feishu keys were missing for 20 minutes, every
send failed, the failed actions sat in backoff so the cron rounds stayed rc=0, and nobody was told) and lib.secret_env_dict
(one reader for both secret files; watchdog's own copy had no Slack token for its bypass sender).
Local only: no network, no writes outside a temp dir. Run: cd bin && python3 ../test/test_watchdog_outbound.py"""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import json, os, shutil, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import lib, watchdog  # noqa: E402

KEYS = {"MIVO_SLACK_BOT_TOKEN": "x", "MIVO_FEISHU_APP_ID": "y", "MIVO_FEISHU_APP_SECRET": "z"}


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="watchdog-test-")
        self.saved = (watchdog.LEDGER, watchdog.secret_env_dict, lib.SECRET_FILES, {k: os.environ.get(k) for k in KEYS})
        for k in KEYS: os.environ.pop(k, None)
        watchdog.LEDGER = f"{self.tmp}/ledger.json"
        self.secrets = dict(KEYS); watchdog.secret_env_dict = lambda: dict(self.secrets)
    def tearDown(self):
        watchdog.LEDGER, watchdog.secret_env_dict, lib.SECRET_FILES, env = self.saved
        for k, v in env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)
    def ledger(self, **entries):
        with open(watchdog.LEDGER, "w") as f: json.dump(entries, f)
    def probs(self):
        p = {}; watchdog.outbound_health_check(None, p); return p

    def test_all_keys_and_a_clean_ledger_say_nothing(self):
        self.ledger(**{"a": {"status": "done"}, "b": {"status": "skipped", "last_error": "MIVO_SLACK_BOT_TOKEN not set"}})
        self.assertEqual(self.probs(), {})

    def test_missing_key_is_an_action_that_bypasses_outbound(self):
        del self.secrets["MIVO_SLACK_BOT_TOKEN"]; self.ledger()
        p = self.probs()                                       # key ends with ":outbound" -> main() sends it past outbound
        self.assertEqual(list(p), ["creds:outbound"]); self.assertEqual(p["creds:outbound"]["kind"], "action")
        self.assertIn("MIVO_SLACK_BOT_TOKEN", p["creds:outbound"]["todo"])                       # names it for the session, not him
        self.assertNotIn("MIVO_", p["creds:outbound"]["title"] + p["creds:outbound"]["what"])
        os.environ["MIVO_SLACK_BOT_TOKEN"] = "from-env"                                           # set in the environment: fine
        self.assertEqual(self.probs(), {})

    def test_key_errors_at_once_other_failures_from_the_second_try(self):
        self.ledger(**{"dm:1": {"status": "failed", "fails": 1, "last_error": "node rc=1: error MIVO_SLACK_BOT_TOKEN not set"},
                       "dm:2": {"status": "failed", "fails": 1, "last_error": "slack: invalid_auth"},
                       "gh:3": {"status": "failed", "fails": 1, "last_error": "HTTP 502 from api.github.com"},
                       "gh:4": {"status": "failed", "fails": 3, "last_error": "HTTP 502 from api.github.com"}})
        p = self.probs()
        self.assertEqual(sorted(p), ["failing:outbound:actions", "failing:outbound:creds"])
        self.assertIn("2 条", p["failing:outbound:creds"]["what"]); self.assertIn("1 条", p["failing:outbound:actions"]["title"])
        self.assertIn("gh:4", p["failing:outbound:actions"]["todo"])
        self.ledger(**{"gh:3": {"status": "failed", "fails": 1, "last_error": "HTTP 502"}})       # one transient failure: wait
        self.assertEqual(self.probs(), {})

    def test_secret_env_dict_reads_both_files_export_prefix_first_file_wins(self):
        a, b = f"{self.tmp}/a.env", f"{self.tmp}/b.env"
        with open(a, "w") as f: f.write("# comment\nexport DEEPSEEK_API_KEY=k1\nSHARED='from-a'\n")
        with open(b, "w") as f: f.write('MIVO_SLACK_BOT_TOKEN="t"\nSHARED=from-b\n\n')
        lib.SECRET_FILES = (a, b, f"{self.tmp}/missing.env")
        self.assertEqual(lib.secret_env_dict(), {"DEEPSEEK_API_KEY": "k1", "SHARED": "from-a", "MIVO_SLACK_BOT_TOKEN": "t"})
        self.assertIs(self.saved[1], lib.secret_env_dict)                                         # watchdog uses lib's reader


class TestBackupRules(unittest.TestCase):
    """check.sh (:40) FAIL lines reach Praise (10-24: check.log was written hourly and read by nobody); a refused push because the
    box looks rolled back is not 'push again'."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="watchdog-rules-"); self.saved = watchdog.CHECK_LOG; watchdog.CHECK_LOG = f"{self.tmp}/check.log"
    def tearDown(self):
        watchdog.CHECK_LOG = self.saved; shutil.rmtree(self.tmp, ignore_errors=True)
    def log(self, at, *rules):
        with open(watchdog.CHECK_LOG, "a") as f: f.write(f"# grokbot-box check {at:%Y-%m-%d %H:%M:%S} CST mode=pi\n" + "".join(r + "\n" for r in rules))
    def test_only_the_newest_block_and_only_rules_not_covered_elsewhere(self):
        now = watchdog.now_cst()
        self.log(now - watchdog.dt.timedelta(hours=2), "FAIL bot dir not backed up: old")              # older block: ignored
        self.log(now - watchdog.dt.timedelta(minutes=20), "PASS cron file", "FAIL local != origin/main", "FAIL meta head != panoptes HEAD",
                 "FAIL company code in HEAD: files/workspace/mivo-canvas-plugin/x -> stop & tell Praise", "FAIL panoptes bundle verify failed")
        p = {}; watchdog.backup_rules_check(now, p)
        kinds = sorted((v["kind"], v["title"]) for v in p.values())
        self.assertEqual(kinds, [("action", "机器备份里混进了公司代码"), ("fyi", "备份规则检查有一项没过（不急）")])
        self.assertTrue(all("盯" not in v["what"] for v in p.values()))
    def test_a_stale_check_log_is_reported(self):
        self.log(watchdog.now_cst() - watchdog.dt.timedelta(hours=4), "PASS cron file")
        p = {}; watchdog.backup_rules_check(watchdog.now_cst(), p)
        self.assertEqual(list(p), ["backup:rules-stale"])


class TestPrBotAndVigil(unittest.TestCase):
    """10-24: nothing watched the 修 PR bot line, nor the Mac mini watcher (down 21:54-03:20 unnoticed)."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="watchdog-prbot-")
        self.saved = (watchdog.PRFIX_TICKETS, watchdog.PRWATCH_STATE, watchdog.LEDGER, watchdog.gh)
        watchdog.PRFIX_TICKETS, watchdog.PRWATCH_STATE, watchdog.LEDGER = (f"{self.tmp}/{n}.json" for n in ("tickets", "prwatch", "ledger"))
        self.now = watchdog.now_cst()
    def tearDown(self):
        watchdog.PRFIX_TICKETS, watchdog.PRWATCH_STATE, watchdog.LEDGER, watchdog.gh = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)
    def put(self, path, doc):
        with open(path, "w") as f: json.dump(doc, f)
    def ago(self, h): return (self.now - watchdog.dt.timedelta(hours=h)).isoformat(timespec="seconds")
    def test_prfix_tickets_prwatch_errors_and_memory_wait(self):
        self.put(watchdog.PRFIX_TICKETS, {"tickets": {
            "1:mq1-aaaaaaaa": {"qa": 1, "status": "running", "pid": 1, "updated_at": self.ago(3.5)},   # > 3 h (10-24: longer runs with feedback rounds)
            "2:mq1-bbbbbbbb": {"qa": 2, "status": "pr_action_written", "action_key": "fixpr:2", "updated_at": self.ago(3)},
            "3:mq1-cccccccc": {"qa": 3, "status": "pr_action_written", "action_key": "fixpr:3", "updated_at": self.ago(3)},
            "4:mq1-dddddddd": {"qa": 4, "status": "needs_praise", "updated_at": self.ago(20)},
            "5:mq1-eeeeeeee": {"qa": 5, "status": "needs_praise", "updated_at": self.ago(40)},       # reminded once already: quiet
            "6:mq1-ffffffff": {"qa": 6, "status": "running", "pid": 1, "updated_at": self.ago(0.3)}}})
        self.put(watchdog.LEDGER, {"fixpr:3": {"status": "done"}, "fixpr:2": {"status": "failed", "fails": 1}})
        fresh = (self.now - watchdog.dt.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        old = (self.now - watchdog.dt.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        self.put(watchdog.PRWATCH_STATE, {"errors": {"fix": f"{fresh} boom", "drafts": f"{old} gone"}, "fix_wait_since": watchdog.time.time() - 4 * 3600,
                                          "fix_pending": 3})
        p = {}; watchdog.prfix_check(self.now, p)
        self.assertEqual(sorted(p), ["prfix:human:4:mq1-dddddddd", "prfix:memwait", "prfix:nopr:2:mq1-bbbbbbbb", "prfix:stuck:1:mq1-aaaaaaaa",
                                     "prwatch:error:fix"])
        self.assertEqual((p["prfix:human:4:mq1-dddddddd"]["kind"], p["prfix:memwait"]["kind"]), ("fyi", "fyi"))   # no "已恢复" when it ages out
        self.assertIn("3 个问题", p["prfix:memwait"]["what"])
    def test_vigil_stall_only_ready_red_and_idle_for_4h_and_one_gh_call_per_30_min(self):
        calls = []
        def gh(args, js=True):
            calls.append(args)
            red = [{"name": "review-pr", "conclusion": "FAILURE"}]
            return [{"number": 1, "title": "draft", "isDraft": True, "updatedAt": self.ago(9), "statusCheckRollup": red},
                    {"number": 2, "title": "stuck", "isDraft": False, "updatedAt": self.ago(5), "statusCheckRollup": red},
                    {"number": 3, "title": "busy", "isDraft": False, "updatedAt": self.ago(1), "statusCheckRollup": red},
                    {"number": 4, "title": "green", "isDraft": False, "updatedAt": self.ago(9), "statusCheckRollup": [{"name": "ci", "conclusion": "SUCCESS"}]}]
        watchdog.gh = gh
        st, p = {}, {}
        watchdog.vigil_check(self.now, p, st)
        self.assertEqual(list(p), ["vigil:stall"]); self.assertIn("#2", p["vigil:stall"]["what"])
        self.assertNotIn("#3", p["vigil:stall"]["what"]); self.assertNotIn("#1", p["vigil:stall"]["what"])
        p2 = {}; watchdog.vigil_check(self.now, p2, st)
        self.assertEqual((len(calls), list(p2)), (1, ["vigil:stall"]))                              # cached verdict, no second gh call


class TestHungTest(unittest.TestCase):
    """10-24: a hung panoptes run would hold the test locks for good (the 2-hourly catch-up has no per-PR timeout)."""
    def test_only_runs_older_than_the_lock_wait_plus_a_run(self):
        p = {}; watchdog.hung_test_check(p, [(watchdog.HUNG_H - 0.1, "bash /workspace/panoptes/run-nightly.sh --catch-up")])
        self.assertEqual(p, {})
        watchdog.hung_test_check(p, [(watchdog.HUNG_H + 1.2, "bash /workspace/panoptes/run-nightly.sh --pr 1264 --date d"), (0.3, "x")])
        self.assertEqual(list(p), ["panoptes:hung"]); self.assertEqual(p["panoptes:hung"]["kind"], "action")
        self.assertIn("#1264", p["panoptes:hung"]["what"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
