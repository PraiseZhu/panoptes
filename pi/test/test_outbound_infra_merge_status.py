#!/usr/bin/env python3
"""11-05 inframerge:229: a gh.infra.merge whose merge conditions are not met must not be recorded done. It is awaiting_merge
(non-terminal): later runs re-read state() -- merged -> done; closed unmerged -> closed_unmerged + an alert notice. Scratch state,
fake handler, no network. Run: cd bin && python3 ../test/test_outbound_infra_merge_status.py"""
import datetime as dt, json, os, shutil, sys, tempfile, types, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
sys.path.insert(0, HERE); import isolation  # noqa: E402
isolation.sandbox_state()
sys.modules.setdefault("merger", types.SimpleNamespace(FIELDS="number", blockers=lambda p: []))
import outbound as O  # noqa: E402
import lib as L  # noqa: E402
assert os.path.realpath(L.BASE) != os.path.realpath(isolation.live_state())

NOW = dt.datetime(2026, 11, 5, 9, 30, 0, tzinfo=O.CST)
KEY = "inframerge:inf1-x:229:118088493739"

class T(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ); p.start(); self.addCleanup(p.stop)
        for k in [k for k in os.environ if k.startswith("GIT_")]: del os.environ[k]
        self.tmp = t = tempfile.mkdtemp(prefix="outbound-im-")
        names = ("BASE", "LEDGER", "QA_SLACK", "OUTBOUND_FLAG", "GROK_SENT", "DISABLED_TYPES", "mode", "secret_env", "now_cst",
                 "today", "slack", "run", "MEMINFO", "handler")
        self.saved = {n: getattr(O, n) for n in names}
        self.saved_l = (L.PRAISE_DM_MUTE, L.BASE); L.PRAISE_DM_MUTE, L.BASE = f"{t}/praise-dm-mute", t
        O.BASE, O.LEDGER, O.QA_SLACK = t, f"{t}/out/outbound/ledger.json", f"{t}/slack"
        O.OUTBOUND_FLAG, O.GROK_SENT, O.DISABLED_TYPES = f"{t}/outbound-enabled", f"{t}/sent", f"{t}/disabled"
        for d in ("out/outbound", "out/infrafix", "slack"): os.makedirs(f"{t}/{d}")
        open(O.OUTBOUND_FLAG, "w").close()
        O.MEMINFO = f"{t}/meminfo"; open(O.MEMINFO, "w").write("MemTotal: 16000000 kB\nMemAvailable: 8388608 kB\nSwapFree: 4194304 kB\n")
        O.mode, O.secret_env, O.now_cst, O.today = (lambda: "pi"), (lambda: None), (lambda: NOW), (lambda: NOW.date().isoformat())
        O.slack = lambda *a, **k: {"ok": True, "user_id": "UBOT", "messages": [], "channel": {"is_member": True}, "user": {}}
        O.run = lambda *a, **k: "ok"
        json.dump({"actions": [{"key": KEY, "type": "gh.infra.merge", "repo": "PraiseZhu/panoptes", "sig": "inf1-x", "pr": 229, "head": "118088493739"}]},
                  open(f"{t}/out/infrafix/{NOW.date().isoformat()}.actions.json", "w"))
        self.st = "pending"; self.dos = 0; self.alerts = []
        real_item = O.PN.item
        def item(kind, title, *a, **k): self.alerts.append(title); return real_item(kind, title, *a, **k)
        p2 = mock.patch.object(O.PN, "item", item); p2.start(); self.addCleanup(p2.stop)
        def do(): self.dos += 1; return "not merged; DM muted, waiting in Jasper brief: auto_merge 不是 true"
        O.handler = lambda a: ("gh infra merge #229", (lambda: self.st), (lambda: "would"), do)
    def tearDown(self):
        L.PRAISE_DM_MUTE, L.BASE = self.saved_l
        for n, v in self.saved.items(): setattr(O, n, v)
        shutil.rmtree(self.tmp, ignore_errors=True)
    def run_out(self):
        sys.argv = ["outbound.py"]; O.main(); return O.jload(O.LEDGER, {}).get(KEY, {})

    def test_unmet_conditions_not_done_and_not_redone(self):
        self.assertEqual(self.run_out()["status"], "awaiting_merge")
        self.assertEqual(self.run_out()["status"], "awaiting_merge")   # still open: re-checked, not re-DMed
        self.assertEqual(self.dos, 1)
    def test_later_merged_is_done(self):
        self.run_out(); self.st = "done"
        self.assertEqual(self.run_out()["status"], "done"); self.assertEqual(self.dos, 1)
    def test_closed_unmerged_alerts_and_terminal(self):
        self.run_out(); self.st = "stale:closed"
        e = self.run_out(); self.assertEqual(e["status"], "closed_unmerged"); self.assertIn("closed_unmerged", O.TERMINAL)
        self.assertTrue(any("没合并就被关了" in a for a in self.alerts), self.alerts)
        n = len(self.alerts); self.run_out(); self.assertEqual(len([a for a in self.alerts[n:] if "没合并就被关了" in a]), 0)   # alert once

if __name__ == "__main__": unittest.main()
