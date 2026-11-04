#!/usr/bin/env python3
"""#198 acceptance 3(e): a gh.* action that hits a GitHub rate limit (lib.GhRateLimited) must leave its ledger row exactly as it
was -- no failed / fails+1, so 5 rate-limited rounds can never turn a queued merge/comment into gave_up. Scratch state only:
temp BASE/ledger, fake handler, no network, no sends. Run: cd bin && python3 ../test/test_outbound_ratelimit.py"""
import datetime as dt, json, os, shutil, sys, tempfile, types, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
sys.path.insert(0, HERE); import isolation  # noqa: E402
isolation.sandbox_state()
sys.modules.setdefault("merger", types.SimpleNamespace(FIELDS="number", blockers=lambda p: []))
import outbound as O  # noqa: E402
import lib as L  # noqa: E402
assert os.path.realpath(L.BASE) != os.path.realpath(isolation.live_state())

NOW = dt.datetime(2026, 11, 4, 9, 30, 0, tzinfo=O.CST)
KEY = "comment:PraiseZhu/panoptes#5:x"
PREV = {"status": "failed", "fails": 4, "last_error": "502", "at": "2026-11-03T09:00:00+08:00"}   # one more failure = gave_up

class T(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ); p.start(); self.addCleanup(p.stop)
        for k in [k for k in os.environ if k.startswith("GIT_")]: del os.environ[k]
        self.tmp = t = tempfile.mkdtemp(prefix="outbound-rl-")
        names = ("BASE", "LEDGER", "QA_SLACK", "OUTBOUND_FLAG", "GROK_SENT", "DISABLED_TYPES", "mode", "secret_env", "now_cst",
                 "today", "slack", "run", "MEMINFO", "handler")
        self.saved = {n: getattr(O, n) for n in names}
        self.saved_l = (L.PRAISE_DM_MUTE, L.BASE); L.PRAISE_DM_MUTE, L.BASE = f"{t}/praise-dm-mute", t
        O.BASE, O.LEDGER, O.QA_SLACK = t, f"{t}/out/outbound/ledger.json", f"{t}/slack"
        O.OUTBOUND_FLAG, O.GROK_SENT, O.DISABLED_TYPES = f"{t}/outbound-enabled", f"{t}/sent", f"{t}/disabled"
        for d in ("out/outbound", "out/prfix", "slack"): os.makedirs(f"{t}/{d}")
        open(O.OUTBOUND_FLAG, "w").close()
        O.MEMINFO = f"{t}/meminfo"; open(O.MEMINFO, "w").write("MemTotal: 16000000 kB\nMemAvailable: 8388608 kB\nSwapFree: 4194304 kB\n")
        O.mode, O.secret_env, O.now_cst, O.today = (lambda: "pi"), (lambda: None), (lambda: NOW), (lambda: NOW.date().isoformat())
        O.slack = lambda *a, **k: {"ok": True, "user_id": "UBOT", "messages": [], "channel": {"is_member": True}, "user": {}}
        O.run = lambda *a, **k: "ok"
        json.dump({"actions": [{"key": KEY, "type": "gh.pr.comment", "repo": "PraiseZhu/panoptes", "pr": 5, "body": "x"}]},
                  open(f"{t}/out/prfix/{NOW.date().isoformat()}.actions.json", "w"))
    def tearDown(self):
        L.PRAISE_DM_MUTE, L.BASE = self.saved_l
        for n, v in self.saved.items(): setattr(O, n, v)
        shutil.rmtree(self.tmp, ignore_errors=True)
    def fake(self, state_raises=False):
        def rl(): raise L.GhRateLimited("gh pr comment: GitHub rate-limited until 09:45:45 CST (not sent)")
        O.handler = lambda a: ("PraiseZhu/panoptes#5", rl if state_raises else (lambda: "todo"), lambda: "ok", rl)
    def run_out(self, ledger):
        if ledger is not None: json.dump(ledger, open(O.LEDGER, "w"))
        sys.argv = ["outbound.py"]; O.main(); return O.jload(O.LEDGER, {})

    def test_do_rate_limited_keeps_failed_row(self):
        self.fake()
        for _ in range(3): self.assertEqual(self.run_out({KEY: PREV}).get(KEY), PREV)   # not gave_up, fails still 4
    def test_do_rate_limited_new_row_left_absent(self):
        self.fake(); self.assertNotIn(KEY, self.run_out({}))
    def test_state_rate_limited_untouched(self):
        self.fake(state_raises=True); self.assertEqual(self.run_out({KEY: PREV}).get(KEY), PREV)

if __name__ == "__main__": unittest.main()
