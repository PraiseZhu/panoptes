#!/usr/bin/env python3
"""outbound + praise-dm-mute (11-02 audit): nudge / pm-retro / pm-cos / ops-review / overdue were recorded "skipped
praise-dm-mute" and reached nobody. Now they go to out/outbound/brief-queue.jsonl (jasper.py puts them in the 09:05 brief) and
the ledger says "briefed". touch <state>/outbound-brief-route-off -> old behaviour. Scratch state only, no network, no sends.
Run: python3 test/test_outbound_mute_brief.py"""
import datetime as dt, json, os, shutil, sys, tempfile, types, unittest, importlib.util
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); BIN = os.path.join(os.path.dirname(HERE), "bin"); sys.path.insert(0, BIN)
sys.path.insert(0, HERE); import isolation  # noqa: E402
isolation.sandbox_state()
sys.modules.setdefault("merger", types.SimpleNamespace(FIELDS="number", blockers=lambda p: []))
import outbound as O  # noqa: E402
import lib as L  # noqa: E402
assert os.path.realpath(L.BASE) != os.path.realpath(isolation.live_state()), f"refusing to run against live state {L.BASE}"

NOW = dt.datetime(2026, 11, 3, 9, 5, 0, tzinfo=O.CST)
DAY = "2026-11-03"
ROUTED = [("pm", {"key": f"nudge:1563:stall:{DAY}T09:05:07+08:00", "type": "nudge", "to": "Praise PR", "text": "#1563 停了 1 小时"}),
          ("pm", {"key": f"pm-retro:{DAY}", "type": "group-post", "text": "复盘"}),
          ("pm", {"key": f"pm-cos:{DAY}", "type": "SendToAgent", "to": "Jasper (Chief of Staff)", "text": "核对汇总"}),
          ("ops", {"key": f"ops-review:{DAY}", "type": "grok.group", "text": "运维复盘"}),
          ("handoff", {"key": "overdue:zhongxingtian-ai/handover-mivo#8", "type": "feishu.dm+group", "issue": "https://github.com/x/y/issues/8",
                       "age_days": 3, "owner": "zhongxingtian-ai"})]
OTHER = ("daily", {"key": f"daily-health:{DAY}", "type": "grokbot.chat-to-Praise", "text": "体检"})


class T(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ); p.start(); self.addCleanup(p.stop)
        self.tmp = t = tempfile.mkdtemp(prefix="outbound-mute-brief-")
        names = ("BASE", "LEDGER", "OUTBOUND_FLAG", "DISABLED_TYPES", "mode", "secret_env", "now_cst",
                 "today", "slack", "run", "MEMINFO", "h_slack_dm")
        self.saved = {n: getattr(O, n) for n in names}
        self.saved_l = (L.PRAISE_DM_MUTE, L.BASE); L.PRAISE_DM_MUTE, L.BASE = f"{t}/praise-dm-mute", t
        open(L.PRAISE_DM_MUTE, "w").write("# only the brief may DM Praise\nbrief:\n")
        O.BASE, O.LEDGER, O.OUTBOUND_FLAG, O.DISABLED_TYPES = t, f"{t}/out/outbound/ledger.json", f"{t}/outbound-enabled", f"{t}/disabled"
        for d in ("out/outbound", "out/pm", "out/ops", "out/handoff", "out/daily"): os.makedirs(f"{t}/{d}")
        open(O.OUTBOUND_FLAG, "w").close()
        O.MEMINFO = f"{t}/meminfo"
        open(O.MEMINFO, "w").write("MemTotal: 16000000 kB\nMemAvailable: 8388608 kB\nSwapFree: 4194304 kB\n")
        O.mode, O.secret_env, O.now_cst, O.today = (lambda: "pi"), (lambda: None), (lambda: NOW), (lambda: DAY)
        def no_net(*a, **k): raise AssertionError(f"network/send attempted: {a[:1]}")
        O.slack = O.run = no_net
        O.h_slack_dm = lambda a: ("Praise", lambda: "pending", lambda: "", lambda: no_net("slack.dm"))
        acts = {}
        for job, a in ROUTED + [OTHER]: acts.setdefault(job, []).append({**a, "at": NOW.isoformat()})
        for job, a in acts.items(): json.dump({"actions": a}, open(f"{t}/out/{job}/{DAY}.actions.json", "w"), ensure_ascii=False)
    def tearDown(self):
        L.PRAISE_DM_MUTE, L.BASE = self.saved_l
        for n, v in self.saved.items(): setattr(O, n, v)
        shutil.rmtree(self.tmp, ignore_errors=True)
    def main(self, *args): sys.argv = ["outbound.py", *args]; return O.main()
    def ledger(self): return O.jload(O.LEDGER, {})
    def queue(self): return [json.loads(l) for l in open(O.BRIEF_QUEUE())] if os.path.exists(O.BRIEF_QUEUE()) else []
    def report(self, dry=False): return open(f"{self.tmp}/out/outbound/{DAY}{'.dry-run' if dry else ''}.md").read()

    def test_muted_bot_messages_go_to_the_brief_not_skipped(self):
        self.assertEqual(self.main(), 0)
        led = self.ledger()
        for _, a in ROUTED: self.assertEqual(led[a["key"]]["status"], "briefed", a["key"])
        self.assertEqual(sorted(q["key"] for q in self.queue()), sorted(a["key"] for _, a in ROUTED))
        q = {x["key"]: x for x in self.queue()}
        self.assertEqual(q[ROUTED[0][1]["key"]]["to"], "Praise PR")
        self.assertIn("handover-mivo#8", q["overdue:zhongxingtian-ai/handover-mivo#8"]["text"])   # overdue body is rebuilt, not empty
        rep = self.report()
        for _, a in ROUTED: self.assertNotIn(f"{a['key']} | {a['type']} | Praise | skipped", rep)
        self.assertEqual([l for l in rep.splitlines() if "nudge:" in l and "skipped" in l], [])
        # anything else that would DM Praise stays muted exactly as before
        self.assertEqual(led[OTHER[1]["key"]], {**led[OTHER[1]["key"]], "status": "skipped", "reason": "praise-dm-mute"})

    def test_second_run_queues_nothing_again(self):
        self.main(); n = len(self.queue()); self.main()
        self.assertEqual(len(self.queue()), n)
        for _, a in ROUTED: self.assertNotIn(a["key"], self.report())   # 11-10: a final key is listed once, not every run

    def test_dry_run_writes_nothing(self):
        self.main("--dry-run")
        self.assertEqual(self.queue(), []); self.assertEqual(self.ledger(), {})
        self.assertIn("BRIEFED", self.report(dry=True))

    def test_off_flag_restores_old_skip(self):
        open(O.BRIEF_ROUTE_OFF(), "w").close()
        self.main()
        self.assertEqual(self.queue(), [])
        for _, a in ROUTED: self.assertEqual(self.ledger()[a["key"]]["reason"], "praise-dm-mute")

    def test_unmuted_still_relays_normally(self):
        os.remove(L.PRAISE_DM_MUTE)
        sent = []
        O.h_slack_dm = lambda a: ("Praise", lambda: "pending", lambda: "", lambda: sent.append(a["key"]) or "ok")
        self.main()
        self.assertEqual(self.queue(), [])
        self.assertTrue(sent and all(self.ledger()[a["key"]]["status"] == "done" for _, a in ROUTED))

    def test_queue_path_is_a_directory_logs_and_run_continues(self):
        os.makedirs(O.BRIEF_QUEUE())   # append -> IsADirectoryError; before the fix this crashed the whole run
        self.assertEqual(self.main(), 0)
        led = self.ledger()
        for _, a in ROUTED: self.assertNotIn(a["key"], led)   # not recorded "briefed" -> retried once the path is fixed
        self.assertEqual(led[OTHER[1]["key"]]["reason"], "praise-dm-mute")   # later actions still handled
        self.assertIn("FAILED(brief-queue)", self.report())


class Jasper(unittest.TestCase):
    def test_brief_gets_each_queued_item_once(self):
        t = tempfile.mkdtemp(prefix="jasper-brief-queue-"); self.addCleanup(shutil.rmtree, t, True)
        s = importlib.util.spec_from_file_location("jasper_q", f"{BIN}/jasper.py"); J = importlib.util.module_from_spec(s); s.loader.exec_module(J)
        J.BASE = t; os.makedirs(f"{t}/out/outbound")
        rows = [("2026-11-02T08:59:59+08:00", "nudge:old"), ("2026-11-02T09:00:00+08:00", "nudge:a"),
                ("2026-11-02T23:50:11+08:00", "pm-retro:2026-11-02"), ("2026-11-03T09:00:00+08:00", "nudge:next")]
        with open(f"{t}/out/outbound/brief-queue.jsonl", "w") as f:
            for at, k in rows: f.write(json.dumps({"at": at, "key": k, "to": "Praise PR", "text": k}) + "\n")
        m = J.muted_for_brief("2026-11-03", "2026-11-02")
        self.assertEqual((m["count"], [i["key"] for i in m["items"]]), (2, ["nudge:a", "pm-retro:2026-11-02"]))
        self.assertEqual(J.muted_for_brief("2026-11-04", "2026-11-03")["count"], 1)
        os.remove(f"{t}/out/outbound/brief-queue.jsonl"); self.assertEqual(J.muted_for_brief("2026-11-03", "2026-11-02")["count"], 0)
        self.assertIn("muted_for_brief", open(f"{BIN}/jasper.py").read().split("Write Praise's")[1])


if __name__ == "__main__": unittest.main()
