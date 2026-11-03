#!/usr/bin/env python3
"""Conflict reminders never DM Praise (11-03 step 11): prwatch `dirty:<pr>:<head>` (slack.dm to Praise) and pmnudge
`nudge:<pr>:merge:<since>` (DIRTY/BEHIND) go to out/outbound/brief-queue.jsonl whether or not praise-dm-mute exists. Before, with
no mute file, both reached his Slack DM; with the mute on, dirty: was only "skipped". touch <state>/conflict-brief-off -> old
behaviour. Scratch state only (same harness as test_outbound_mute_brief.py), no network, no sends."""
import datetime as dt, json, os, shutil, sys, tempfile, types, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); BIN = os.path.join(os.path.dirname(HERE), "bin"); sys.path.insert(0, BIN)
sys.path.insert(0, HERE); import isolation  # noqa: E402
isolation.sandbox_state()
sys.modules.setdefault("merger", types.SimpleNamespace(FIELDS="number", blockers=lambda p: []))
import outbound as O  # noqa: E402
import lib as L  # noqa: E402
assert os.path.realpath(L.BASE) != os.path.realpath(isolation.live_state()), f"refusing to run against live state {L.BASE}"

NOW = dt.datetime(2026, 11, 3, 21, 5, 0, tzinfo=O.CST)
DAY = "2026-11-03"
DIRTY = ("prwatch", {"key": "dirty:1641:abcdef123456", "type": "slack.dm", "to": "Praise", "text": "修复 PR #1641 跟 main 冲突了"})
MERGE = ("pm", {"key": f"nudge:1641:merge:{DAY}T18:05:00+08:00", "type": "nudge", "to": "Praise PR", "text": "#1641 DIRTY 已 3h"})
CONFLICTS = [DIRTY, MERGE]


class T(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ); p.start(); self.addCleanup(p.stop)
        self.tmp = t = tempfile.mkdtemp(prefix="outbound-conflict-brief-")
        names = ("BASE", "LEDGER", "OUTBOUND_FLAG", "DISABLED_TYPES", "mode", "secret_env", "now_cst", "today", "slack", "run", "MEMINFO", "h_slack_dm")
        self.saved = {n: getattr(O, n) for n in names}
        self.saved_l = (L.PRAISE_DM_MUTE, L.BASE); L.PRAISE_DM_MUTE, L.BASE = f"{t}/praise-dm-mute", t
        O.BASE, O.LEDGER, O.OUTBOUND_FLAG, O.DISABLED_TYPES = t, f"{t}/out/outbound/ledger.json", f"{t}/outbound-enabled", f"{t}/disabled"
        for d in ("out/outbound", "out/pm", "out/prwatch"): os.makedirs(f"{t}/{d}")
        open(O.OUTBOUND_FLAG, "w").close()
        O.MEMINFO = f"{t}/meminfo"
        open(O.MEMINFO, "w").write("MemTotal: 16000000 kB\nMemAvailable: 8388608 kB\nSwapFree: 4194304 kB\n")
        O.mode, O.secret_env, O.now_cst, O.today = (lambda: "pi"), (lambda: None), (lambda: NOW), (lambda: DAY)
        self.dms = []
        def no_net(*a, **k): self.dms.append(a[:1]); raise AssertionError(f"network/send attempted: {a[:1]}")
        O.slack = O.run = no_net
        O.h_slack_dm = lambda a: ("Praise", lambda: "pending", lambda: "", lambda: no_net("slack.dm"))
        acts = {}
        for job, a in CONFLICTS: acts.setdefault(job, []).append({**a, "at": NOW.isoformat()})
        for job, a in acts.items(): json.dump({"actions": a}, open(f"{t}/out/{job}/{DAY}.actions.json", "w"), ensure_ascii=False)
    def tearDown(self):
        L.PRAISE_DM_MUTE, L.BASE = self.saved_l
        for n, v in self.saved.items(): setattr(O, n, v)
        shutil.rmtree(self.tmp, ignore_errors=True)
    def main(self, *args): sys.argv = ["outbound.py", *args]; return O.main()
    def ledger(self): return O.jload(O.LEDGER, {})
    def queue(self): return [json.loads(l) for l in open(O.BRIEF_QUEUE())] if os.path.exists(O.BRIEF_QUEUE()) else []
    def mute(self): open(L.PRAISE_DM_MUTE, "w").write("brief:\n")

    def assert_briefed(self):
        led = self.ledger()
        for _, a in CONFLICTS: self.assertEqual(led[a["key"]]["status"], "briefed", (a["key"], led.get(a["key"])))
        self.assertEqual(sorted(q["key"] for q in self.queue()), sorted(a["key"] for _, a in CONFLICTS))
        self.assertEqual(self.dms, [])

    def test_no_mute_conflict_reminders_go_to_brief_not_dm(self):
        self.assertFalse(os.path.exists(L.PRAISE_DM_MUTE))
        self.assertEqual(self.main(), 0)
        self.assert_briefed()
        self.assertIn("conflict reminder", self.ledger()[DIRTY[1]["key"]]["reason"])

    def test_mute_on_dirty_is_briefed_not_just_skipped(self):
        self.mute(); self.assertEqual(self.main(), 0)
        self.assert_briefed()

    def test_brief_route_off_still_no_dm(self):
        open(O.BRIEF_ROUTE_OFF(), "w").close()
        self.assertEqual(self.main(), 0)
        led = self.ledger()
        for _, a in CONFLICTS: self.assertEqual(led[a["key"]]["status"], "skipped", a["key"])
        self.assertEqual(self.queue(), []); self.assertEqual(self.dms, [])
        self.assertTrue(os.path.exists(f"{self.tmp}/out/outbound/muted-{DAY}.md"))

    def test_rollback_flag_restores_old_routing(self):
        self.mute(); open(O.CONFLICT_BRIEF_OFF(), "w").close()
        self.assertEqual(self.main(), 0)
        led = self.ledger()
        self.assertEqual(led[DIRTY[1]["key"]]["status"], "skipped")      # pre-fix: dirty: under the mute is only skipped
        self.assertEqual(led[MERGE[1]["key"]]["status"], "briefed")      # nudge: is BRIEF_ROUTED under the mute anyway
        self.assertEqual(self.dms, [])

    def resolved_actions(self, plain=True):   # step 11 ③: prwatch resolved() 🟢 -- resolved:dirty:<sha> (conflict) beside a plain resolved:<sha> (CI red)
        acts = [{**DIRTY[1], "at": NOW.isoformat()},
                {"key": "resolved:dirty:0123456789", "type": "slack.dm", "to": "Praise", "text": "🟢 修复 PR #1641 后来弄好了（跟 main 冲突）", "at": NOW.isoformat()},
                {"key": "resolved:abcdef0123", "type": "slack.dm", "to": "Praise", "text": "🟢 修复 PR #1650 后来弄好了（自动测试没过）", "at": NOW.isoformat()}][:3 if plain else 2]
        json.dump({"actions": acts}, open(f"{self.tmp}/out/prwatch/{DAY}.actions.json", "w"), ensure_ascii=False)

    def test_resolved_conflict_green_goes_to_brief_never_dm(self):
        self.resolved_actions(plain=False); self.assertEqual(self.main(), 0)   # no mute: before, this 🟢 was a Slack DM
        led = self.ledger(); self.assertEqual(led["resolved:dirty:0123456789"]["status"], "briefed")
        self.assertIn("conflict reminder", led["resolved:dirty:0123456789"]["reason"]); self.assertEqual(self.dms, [])

    def test_resolved_conflict_green_under_mute_briefed_not_just_skipped(self):
        self.resolved_actions(); self.mute(); self.assertEqual(self.main(), 0)
        led = self.ledger(); self.assertEqual(led["resolved:dirty:0123456789"]["status"], "briefed")
        self.assertEqual(led["resolved:abcdef0123"]["status"], "skipped")             # non-conflict 🟢: unchanged (mute -> skipped)
        self.assertIn("resolved:dirty:0123456789", [q["key"] for q in self.queue()]); self.assertEqual(self.dms, [])

    def test_resolved_conflict_green_with_brief_route_off_is_muted_not_dm(self):
        self.resolved_actions(plain=False); open(O.BRIEF_ROUTE_OFF(), "w").close(); self.assertEqual(self.main(), 0)
        self.assertEqual(self.ledger()["resolved:dirty:0123456789"]["status"], "skipped")
        self.assertEqual(self.queue(), []); self.assertEqual(self.dms, [])
        self.assertIn("resolved:dirty:0123456789", open(f"{self.tmp}/out/outbound/muted-{DAY}.md").read())

    def test_other_keys_unchanged(self):
        self.assertFalse(O.conflict_brief("nudge:1641:stall:2026-11-03T18:05:00+08:00"))
        self.assertFalse(O.conflict_brief("nudge:2026-11-03:1641:red"))
        self.assertFalse(O.conflict_brief("resolved:abc"))
        self.assertTrue(O.conflict_brief("resolved:dirty:0123456789"))
        self.assertTrue(O.conflict_brief("dirty:1641:abcdef123456"))
        self.assertTrue(O.conflict_brief("nudge:1641:merge:2026-11-03T18:05:00+08:00"))


if __name__ == "__main__": unittest.main()
