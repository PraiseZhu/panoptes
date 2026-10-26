#!/usr/bin/env python3
"""outbound --resend-key (10-26 Mivo QA 日报): the 2026-10-25 daily (body 「【补发】Mivo 播报 …」) could not be re-sent -- the
daily head check refused the 【补发】 prefix, --days 2 never scanned 10-25, and the channel-history check took the original
10-25 message as "already sent"; overdue:aj0928/aj0928-handoff#6 was skipped as "stale relay (9h old)". Scratch state only:
temp BASE/ledger/sent.json, fake Slack and fake sender. No network, no sends. Run: python3 test/test_outbound_resend.py"""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import datetime as dt, json, os, shutil, sys, tempfile, types, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
sys.modules.setdefault("merger", types.SimpleNamespace(FIELDS="number", blockers=lambda p: []))
import outbound as O  # noqa: E402

NOW = dt.datetime(2026, 10, 27, 0, 50, 2, tzinfo=O.CST)            # 09:50 PDT 10-26, when the relay was skipped
DAILY, OVERDUE = "mivo-daily-report:2026-10-25", "overdue:aj0928/aj0928-handoff#6"
BODY = "Mivo 播报 10 月 25 日（北京时间）：合了 15 个 PR，没有 P0。\n\nP2 一般｜需要修复\n"


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = t = tempfile.mkdtemp(prefix="outbound-resend-")
        names = ("BASE", "LEDGER", "QA_SLACK", "OUTBOUND_FLAG", "GROK_SENT", "DISABLED_TYPES", "mode", "secret_env", "now_cst",
                 "today", "slack", "run")
        self.saved = {n: getattr(O, n) for n in names}
        O.BASE, O.LEDGER, O.QA_SLACK = t, f"{t}/out/outbound/ledger.json", f"{t}/slack"
        O.OUTBOUND_FLAG, O.GROK_SENT, O.DISABLED_TYPES = f"{t}/outbound-enabled", f"{t}/sent", f"{t}/disabled"
        for d in ("out/outbound", "out/daily", "out/handoff", "slack"): os.makedirs(f"{t}/{d}")
        open(O.OUTBOUND_FLAG, "w").close()
        O.mode, O.secret_env, O.now_cst, O.today = (lambda: "pi"), (lambda: None), (lambda: NOW), (lambda: NOW.date().isoformat())
        self.sends = []; self.history = ["Mivo 播报 10 月 25 日（北京时间）：原来那条"]   # the original 10-25 message is in the channel
        def slack(method, params=None, post=False):
            if method == "auth.test": return {"ok": True, "user_id": "UBOT", "user": "mivo"}
            if method == "conversations.history": return {"messages": [{"user": "UBOT", "text": x, "ts": "1.0"} for x in self.history]}
            if method == "conversations.info": return {"channel": {"name": "mivo-qa", "is_member": True}}
            if method == "users.info": return {"user": {"real_name": "Praise"}}
            raise AssertionError(method)
        def run(cmd, **kw):                                              # fake send-slack-report.mjs: dedupes on sent.json like it
            k = cmd[cmd.index("--dedupe-key") + 1]; s = O.jload(f"{O.QA_SLACK}/sent.json", {})
            if k in s: return f"skip already sent {k}"
            self.sends.append((k, open(cmd[3]).read())); s[k] = {"ts": "1"}; open(f"{O.QA_SLACK}/sent.json", "w").write(json.dumps(s))
            return f"sent {k}"
        O.slack, O.run = slack, run
        self.daily("【补发】" + BODY)
        self.actions("handoff", "2026-10-26", [{"key": OVERDUE, "type": "feishu.dm+group", "issue": "https://github.com/aj0928/aj0928-handoff/issues/6",
                                               "age_days": 3, "owner": "aj0928", "at": "2026-10-26T15:20:05+08:00"}])
    def tearDown(self):
        for n, v in self.saved.items(): setattr(O, n, v)
        shutil.rmtree(self.tmp, ignore_errors=True)
    def actions(self, job, day, acts):
        json.dump({"actions": acts}, open(f"{self.tmp}/out/{job}/{day}.actions.json", "w"), ensure_ascii=False)
    def daily(self, body, day="2026-10-25"):
        f = f"{self.tmp}/out/daily/{day}.md"; open(f, "w").write(body)
        self.actions("daily", day, [{"key": f"mivo-daily-report:{day}", "type": "slack.post", "channel": O.SLACK_CHANNEL, "body_file": f}])
    def ledger(self, d=None):
        if d is not None: json.dump(d, open(O.LEDGER, "w"))
        return O.jload(O.LEDGER, {})
    def main(self, *args):
        sys.argv = ["outbound.py", *args]; return O.main()

    # ---- the reported failure, reproduced on the normal path (unchanged guards) ----
    def test_normal_run_never_scans_10_25_and_skips_the_stale_relay(self):
        self.assertEqual(self.main(), 0)
        self.assertNotIn(DAILY, self.ledger()); self.assertEqual(self.sends, [])
        self.assertTrue(self.ledger()[OVERDUE]["reason"].startswith("stale relay (9h old"))
    def test_normal_path_history_check_still_applies(self):
        self.daily("【补发】" + BODY.replace("10 月 25 日", "10 月 26 日"), "2026-10-26"); self.history = ["Mivo 播报 10 月 26 日（北京时间）：x"]
        self.main("--key", "mivo-daily-report:2026-10-26")
        self.assertEqual(self.ledger()["mivo-daily-report:2026-10-26"]["by"], "target-state"); self.assertEqual(self.sends, [])

    # ---- head check ----
    def test_daily_head_accepts_bufa_and_still_refuses_other_bodies(self):
        self.assertTrue(O.DAILY_HEAD_RE.match("【补发】" + BODY)); self.assertTrue(O.DAILY_HEAD_RE.match(BODY))
        for bad in ("补发 " + BODY, "【重发】" + BODY, "hello\n" + BODY, "【补发】Mivo 播报 10 月 25 日：x"):
            self.assertFalse(O.DAILY_HEAD_RE.match(bad), bad)
        f = f"{self.tmp}/bad.md"; open(f, "w").write("【补发】hello")
        with self.assertRaisesRegex(RuntimeError, "does not start like"):
            O.h_slack_post({"key": DAILY, "type": "slack.post", "body_file": f})[2]()

    # ---- --resend-key ----
    def test_resend_daily_sends_once_despite_history_then_is_a_noop(self):
        self.assertEqual(self.main("--resend-key", DAILY, "--date", "2026-10-25"), 0)
        self.assertEqual([k for k, _ in self.sends], [DAILY]); self.assertTrue(self.sends[0][1].startswith("【补发】Mivo 播报 10 月 25 日"))
        e = self.ledger()[DAILY]; self.assertEqual((e["status"], e["by"]), ("done", "resend"))
        self.assertEqual(self.main("--resend-key", DAILY), 0); self.assertEqual(len(self.sends), 1)
        self.assertEqual(self.main(), 0); self.assertEqual(len(self.sends), 1)
    def test_resend_touches_only_that_key(self):
        self.ledger({OVERDUE: {"status": "failed", "fails": 1, "at": "2026-01-01T00:00:00+08:00"}})
        self.main("--resend-key", DAILY)
        self.assertEqual(self.ledger()[OVERDUE]["status"], "failed"); self.assertEqual([k for k, _ in self.sends], [DAILY])
    def test_resend_daily_without_bufa_prefix_is_still_blocked_by_history(self):
        self.daily(BODY); self.main("--resend-key", DAILY)
        self.assertEqual(self.sends, []); self.assertEqual(self.ledger()[DAILY]["by"], "target-state")
    def test_resend_daily_already_in_sent_json_is_not_resent(self):
        json.dump({DAILY: {"ts": "9"}}, open(f"{O.QA_SLACK}/sent.json", "w")); self.main("--resend-key", DAILY)
        self.assertEqual(self.sends, []); self.assertEqual(self.ledger()[DAILY]["status"], "done")
    def test_resend_overdue_relay_overrides_a_stale_skip_once(self):
        self.ledger({OVERDUE: {"status": "skipped", "reason": "stale relay (9h old; relay enabled 10-23)", "at": NOW.isoformat()}})
        self.assertEqual(self.main("--resend-key", OVERDUE), 0)
        self.assertEqual([k for k, _ in self.sends], [f"relay:{OVERDUE}"]); self.assertIn("aj0928/aj0928-handoff#6", self.sends[0][1])
        self.assertEqual((self.ledger()[OVERDUE]["status"], self.ledger()[OVERDUE]["by"]), ("done", "resend"))
        self.main("--resend-key", OVERDUE); self.main(); self.assertEqual(len(self.sends), 1)
    def test_resend_refuses_terminal_ledger_states(self):
        for st in ({"status": "done"}, {"status": "uncertain"}, {"status": "sending"}, {"status": "gave_up"},
                   {"status": "skipped", "reason": "gated: x"}, {"status": "skipped", "reason": "relay: empty body"}):
            self.ledger({OVERDUE: st, DAILY: st})
            for k in (OVERDUE, DAILY): self.assertEqual(self.main("--resend-key", k), 0)
            self.assertEqual(self.sends, [], st); self.assertEqual(self.ledger()[OVERDUE], st)
    def test_resend_dry_run_sends_nothing_and_keeps_the_ledger(self):
        led = {OVERDUE: {"status": "skipped", "reason": "stale relay (9h old)"}}; self.ledger(led)
        for k in (DAILY, OVERDUE): self.assertEqual(self.main("--dry-run", "--resend-key", k), 0)
        self.assertEqual(self.sends, []); self.assertEqual(self.ledger(), led)
        self.assertTrue(os.path.exists(f"{self.tmp}/out/outbound/2026-10-27.resend-{DAILY.replace(':', '_')}.dry-run.md"))
    def test_resend_unknown_key_or_wrong_date_fails(self):
        self.assertEqual(self.main("--resend-key", "mivo-daily-report:2026-10-01"), 1)
        self.assertEqual(self.main("--resend-key", DAILY, "--date", "2026-10-26"), 1); self.assertEqual(self.sends, [])
    def test_resend_key_in_two_days_needs_date(self):
        self.actions("handoff", "2026-10-25", [{"key": OVERDUE, "type": "feishu.dm+group", "text": "x"}])
        self.assertEqual(self.main("--resend-key", OVERDUE), 1); self.assertEqual(self.sends, [])
        self.assertEqual(self.main("--resend-key", OVERDUE, "--date", "2026-10-26"), 0); self.assertEqual(len(self.sends), 1)
    def test_date_without_resend_key_is_an_error(self):
        with self.assertRaises(SystemExit): self.main("--date", "2026-10-25")


if __name__ == "__main__": unittest.main()
