#!/usr/bin/env python3
"""B-fp (2026-11-03): P2 rulings signed per fingerprint. `p2.py record --fp` writes | 日期 | 报告 PR | 指纹 | 问题 | 裁决 | …;
such a row exempts that one finding only -- other P0-P2 of the same PR stay tickets / ledger rows / counted -- in prfix
(finding_policy.no_fix_rulings + own_pr_ticket), pm.py, pmnudge.py and build-ledger.mjs. Rows without 指纹 still cover the
whole PR. Rollback switch: the file $PI_STATE/p2-fp-rulings-off (re-read every call) -> fingerprint rows cover the whole PR again.
Scratch dirs only; no network, no model. Run: python3 test/test_p2_fp_rulings.py"""
import datetime as dt, importlib, json, os, shutil, subprocess, sys, tempfile, types, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); BIN = os.path.join(os.path.dirname(HERE), "bin"); sys.path.insert(0, BIN)
sys.path.insert(0, HERE); import isolation  # noqa: E402
isolation.sandbox_state()
os.environ.setdefault("PRFIX_OWNERS", "*")
_JC = tempfile.mkdtemp(prefix="p2fp-jconf-"); open(f"{_JC}/janitor.json", "w").write('{"enforce": false}'); os.environ["JANITOR_CONF"] = f"{_JC}/janitor.json"
import atexit; atexit.register(shutil.rmtree, _JC, True)
import lib as L  # noqa: E402
assert os.path.realpath(L.BASE) != os.path.realpath(isolation.live_state()), f"refusing to run against live state {L.BASE}"
import finding_policy as F, pm, pmnudge, prfix  # noqa: E402
TOOL = os.path.join(os.path.dirname(HERE), "ledger", "build-ledger.mjs")
A, B = "mq1-" + "a" * 20, "mq1-" + "b" * 20
REPO, DAY = "xindong/mivo-canvas-plugin", "2026-11-03"
HDR = "| 日期 | 报告 PR | 问题 | 裁决 | 理由 | 证据 | 会签 | 规则状态 |\n|---|---|---|---|---|---|---|---|\n"
ROWS = HDR + f"| 2026-11-03 | #1340 | {A}-pr1340 | spill 权限 | 不修 | r | e | PM | Jasper 已裁 |\n| 2026-11-03 | #5 | 整 PR | 不修 | r |\n"


def p2(fp): return {"fingerprint": fp, "severity": "P2", "source": "check", "status": "introduced", "test_backed": True, "title": fp[-4:]}


class Switch(unittest.TestCase):
    def setUp(self):
        # the switch is read from $PI_STATE at call time; under unittest discover lib (L.BASE) was imported by an earlier module
        # under another PI_STATE and later modules moved PI_STATE again: pin it to L.BASE for this test, restored after
        e = mock.patch.dict(os.environ, PI_STATE=L.BASE); e.start(); self.addCleanup(e.stop)
        os.makedirs(L.BASE, exist_ok=True); self.off = os.path.join(L.BASE, "p2-fp-rulings-off")
        self.addCleanup(lambda: os.path.exists(self.off) and os.remove(self.off))

    def switch_off(self): open(self.off, "w").close()


class Policy(Switch):
    def test_fp_row_exempts_only_that_finding(self):
        nf = F.no_fix_rulings(ROWS)
        self.assertEqual(nf, {5, (1340, A)})
        self.assertIsNone(F.own_pr_ticket(p2(A), 1340, set(), True, nofix=nf))
        self.assertEqual(F.own_pr_ticket(p2(B), 1340, set(), True, nofix=nf), "P2")
        self.assertIsNone(F.own_pr_ticket(p2(B), 5, set(), True, nofix=nf))   # row without 指纹: whole PR, as before

    def test_fp_row_exempts_a_p1_review_lead_but_pr_wide_row_does_not(self):
        # 11-04: 1433:mq1-9be27e51 / 1456:mq1-7a5374 are pending P1 review leads; a PR-wide (P2) row never covered them,
        # a signed fingerprint row now does -- only that finding, other P1 of the PR stay tickets.
        nf = F.no_fix_rulings(ROWS)
        p1 = lambda fp: {"fingerprint": fp, "severity_if_confirmed": "P1", "source": "code-review", "title": fp[-4:]}
        self.assertIsNone(F.own_pr_ticket(p1(A), 1340, set(), True, nofix=nf, pending=True))
        self.assertEqual(F.own_pr_ticket(p1(B), 1340, set(), True, nofix=nf, pending=True), "P1")
        self.assertEqual(F.own_pr_ticket(p1(A), 5, set(), True, nofix=nf, pending=True), "P1")   # PR-wide row stays P2-only

    def test_switch_restores_whole_pr(self):
        self.switch_off()
        nf = F.no_fix_rulings(ROWS); self.assertEqual(nf, {5, 1340})
        self.assertIsNone(F.own_pr_ticket(p2(B), 1340, set(), True, nofix=nf))

    def test_prfix_collect_keeps_other_findings_of_the_pr(self):
        p = {"number": 1340, "author": {"login": "PraiseZhu"}, "findings": [p2(A), p2(B)], "pending_verification": []}
        reps = {1340: {"cur": (dt.datetime(2026, 11, 3, tzinfo=L.CST), p, "PR-1340.json"), "hist": []}}
        led = {(1340, A): {"pr": 1340, "fingerprint": A, "level": "P2", "status": "open", "author": "PraiseZhu", "fix_prs": []}}
        got = prfix.collect(reps, led, set(), F.no_fix_rulings(ROWS))
        self.assertEqual(sorted(got), [(1340, B)])
        got = prfix.collect({}, {**led, (1340, B): dict(led[(1340, A)], fingerprint=B)}, set(), F.no_fix_rulings(ROWS))   # ledger-only rows
        self.assertEqual(sorted(got), [(1340, B)])


class P2Record(Switch):
    def setUp(self):
        super().setUp()
        self.t = tempfile.mkdtemp(prefix="p2fp-"); self.addCleanup(shutil.rmtree, self.t, True)
        import p2 as P; self.P = importlib.reload(P)
        self.rul = f"{self.t}/p2-rulings.md"; open(self.rul, "w").write("# P2 裁决记录\n\n" + HDR)
        self.ev = f"{self.t}/ev.md"; open(self.ev, "w").write("x")

    def rec(self, *a):
        fixed = dt.datetime(2026, 11, 4, 8, 0, 0, tzinfo=self.P.CST)
        with mock.patch.object(self.P, "RULINGS", self.rul), \
             mock.patch.object(self.P, "dt", types.SimpleNamespace(datetime=types.SimpleNamespace(now=lambda tz=None: fixed))):
            self.P.record(list(a))

    def test_record_fp_row_and_plain_row(self):
        self.rec("#1340", "spill 权限", "不修", "r", self.ev, "--fp", f"{A}-pr1340")
        self.rec("1340", "umask", "不修", "r", self.ev, "--fp", B)            # bare fingerprint -> written as -pr1340
        self.rec("7", "整 PR", "不修", "r", self.ev)                         # no --fp: unchanged row shape
        rows = [l.rstrip("\n") for l in open(self.rul) if l.startswith("| 2026-11-04")]
        self.assertEqual(rows[0], f"| 2026-11-04 | #1340 | {A}-pr1340 | spill 权限 | 不修 | r | {self.ev} | PM+Praise PR | Jasper 已裁 |")
        self.assertIn(f"| #1340 | {B}-pr1340 | umask | 不修 |", rows[1])
        self.assertEqual(rows[2], f"| 2026-11-04 | #7 | 整 PR | 不修 | r | {self.ev} | PM+Praise PR | Jasper 已裁 |")
        self.assertEqual(F.no_fix_rulings(open(self.rul).read()), {7, (1340, A), (1340, B)})
        for bad in (f"{A}-pr99", "nope"):
            with self.assertRaises(SystemExit): self.rec("1340", "t", "不修", "r", self.ev, "--fp", bad)

    def test_shadow_proposals_skip_only_the_signed_finding(self):
        it = lambda fp: {"pr": 1340, "fp": fp}
        self.assertTrue(self.P.already_ruled(it(A), ROWS)); self.assertFalse(self.P.already_ruled(it(B), ROWS))
        self.assertTrue(self.P.already_ruled({"pr": 5, "fp": B}, ROWS))
        self.switch_off(); self.assertTrue(self.P.already_ruled(it(B), ROWS))


class PmAndNudge(Switch):
    def test_pm_counts_pr_until_every_p2_is_signed(self):
        f = os.path.join(L.BASE, "rul.md"); open(f, "w").write(ROWS); self.addCleanup(os.remove, f)
        with mock.patch.object(pm, "RULINGS", f):
            fx, nf = pm.p2_pr_rulings({1340: {A, B}, 5: {B}}, pm.ruled("修"), pm.ruled("不修"))
            self.assertEqual((fx, nf), (set(), {5}))                         # #1340 still has an unsigned P2: counted
            fx, nf = pm.p2_pr_rulings({1340: {A}}, pm.ruled("修"), pm.ruled("不修"))
            self.assertEqual(nf, {1340, 5})                                  # every current P2 signed 不修
            self.switch_off()
            self.assertEqual(pm.p2_pr_rulings({1340: {A, B}}, pm.ruled("修"), pm.ruled("不修"))[1], {1340, 5})

    def test_pmnudge_skips_only_fix_prs_for_signed_findings(self):
        f = os.path.join(L.BASE, "rul.md"); open(f, "w").write(ROWS); self.addCleanup(os.remove, f)
        with mock.patch.object(pmnudge, "RULINGS", f):
            nof = pmnudge.nofix_prs()
            self.assertTrue(pmnudge.only_nofix([1340], f"QA: #1340 fixes {A}-pr1340", nof, set()))
            self.assertFalse(pmnudge.only_nofix([1340], f"QA: #1340 fixes {B}", nof, set()))
            self.assertFalse(pmnudge.only_nofix([1340], "QA: #1340", nof, set()))
            self.assertTrue(pmnudge.only_nofix([5], "QA: #5", nof, set())); self.assertFalse(pmnudge.only_nofix([5], "QA: #5", nof, {5}))
            self.switch_off()
            self.assertTrue(pmnudge.only_nofix([1340], f"QA: #1340 fixes {B}", pmnudge.nofix_prs(), set()))


class BuildLedger(Switch):
    def setUp(self):
        super().setUp()
        self.t = t = tempfile.mkdtemp(prefix="p2fp-ledger-"); self.addCleanup(shutil.rmtree, t, True)
        self.qa = qa = f"{t}/qa"
        for d in ("reports/merged", "followup", "ledger/daily"): os.makedirs(f"{qa}/{d}", exist_ok=True)
        os.makedirs(f"{t}/bin"); open(f"{t}/bin/gh", "w").write("#!/bin/sh\necho '[]'\n"); os.chmod(f"{t}/bin/gh", 0o755)
        json.dump({"routes": [{"repo": "someone/handover"}]}, open(f"{t}/routes.json", "w"))
        self.rul = f"{t}/p2-rulings.md"; open(self.rul, "w").write("# P2 裁决记录\n\n" + ROWS)
        json.dump({"generated_at": "2026-11-02T10:00:00Z", "prs": [{"number": 1340, "author": {"login": "PraiseZhu"}, "findings": [p2(A), p2(B)]}]},
                  open(f"{qa}/reports/merged/PR-1340.json", "w"))

    def keys(self):
        env = {**os.environ, "QA_HOME": self.qa, "PATH": f"{self.t}/bin:{os.environ['PATH']}", "MIVO_QA_HANDOFF_ROUTES": f"{self.t}/routes.json",
               "MIVO_QA_P2_RULINGS": self.rul, "PI_STATE": L.BASE}
        r = subprocess.run(["node", TOOL, DAY], capture_output=True, text=True, env=env, timeout=120); self.assertEqual(r.returncode, 0, r.stderr)
        return sorted(x["key"] for x in json.load(open(f"{self.qa}/ledger/daily/{DAY}.json"))["rows"])

    def test_fp_row_drops_only_that_row(self):
        self.assertEqual(self.keys(), [f"{REPO}#1340:{B}"])

    def test_switch_drops_the_whole_pr(self):
        self.switch_off(); self.assertEqual(self.keys(), [])

    def test_p1_fp_row_drops_that_p1_but_pr_wide_row_does_not(self):
        # 11-04 (JS ownTicket mirror of own_pr_ticket): fingerprint row exempts a P1 lead; a PR-wide row never exempts a P1.
        p1 = lambda fp: {"fingerprint": fp, "severity_if_confirmed": "P1", "source": "code-review", "title": fp[-4:]}
        json.dump({"generated_at": "2026-11-02T10:00:00Z", "prs": [{"number": 1340, "author": {"login": "PraiseZhu"}, "findings": [],
                   "pending_verification": [p1(A), p1(B)]}]}, open(f"{self.qa}/reports/merged/PR-1340.json", "w"))
        self.assertEqual(self.keys(), [f"{REPO}#1340:{B}"])
        open(self.rul, "w").write("# P2 裁决记录\n\n" + HDR + "| 2026-11-03 | #1340 | 整 PR | 不修 | r |\n")
        self.assertEqual(self.keys(), [f"{REPO}#1340:{A}", f"{REPO}#1340:{B}"])


if __name__ == "__main__":
    unittest.main()
