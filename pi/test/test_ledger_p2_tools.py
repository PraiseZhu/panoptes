#!/usr/bin/env python3
"""Step 5 C (2026-11-03): ledger + p2 tools.
build-ledger.mjs (now pi/ledger/): rows carry `severity` (was absent -> every reader got null); a third-round row (fix_round >= 3)
is in needs_you whatever its status (pm_extra 「第三轮未报出」 rule); --help prints usage and writes nothing; unknown args exit 2;
a 「结案」 P2 ruling means no row/ticket. ledger.py: --help prints only; set-fix-pr writes overrides.json (backup) and the next
rebuild shows the fix PR; the legacy-tool flag switches back to /workspace/mivo-qa/ledger/build-ledger.mjs. p2.py: --help prints
only; record defaults to 「Jasper 已裁」, accepts 结案, same-second backups both kept. finding_policy / pm read 结案 like 已修.
Scratch dirs only; gh is a fake that returns []; no network, no model. Run: python3 test/test_ledger_p2_tools.py"""
import json, os, shutil, subprocess, sys, tempfile, types, unittest, importlib, datetime as dt
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); BIN = os.path.join(os.path.dirname(HERE), "bin"); sys.path.insert(0, BIN)
sys.path.insert(0, HERE); import isolation  # noqa: E402
isolation.sandbox_state()
import lib as L  # noqa: E402
assert os.path.realpath(L.BASE) != os.path.realpath(isolation.live_state()), f"refusing to run against live state {L.BASE}"
TOOL = os.path.join(os.path.dirname(HERE), "ledger", "build-ledger.mjs")
DAY = "2026-11-03"
REPO = "xindong/mivo-canvas-plugin"
FP = "mq1-aaaaaaaaaaaaaaaaaaaa"


def report(n, login, findings=(), retest=()):
    return {"generated_at": "2026-11-02T10:00:00Z", "prs": [{"number": n, "author": {"login": login}, "findings": list(findings),
                                                              **({"retest": list(retest)} if retest else {})}]}


class Base(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ); p.start(); self.addCleanup(p.stop)
        self.t = t = tempfile.mkdtemp(prefix="ledger-p2-"); self.addCleanup(shutil.rmtree, t, True)
        self.qa = qa = f"{t}/qa"
        for d in ("reports/merged", "followup", "ledger/daily", "bin"): os.makedirs(f"{qa}/{d}", exist_ok=True)
        gh = f"{t}/bin/gh"; os.makedirs(f"{t}/bin"); open(gh, "w").write("#!/bin/sh\necho '[]'\n"); os.chmod(gh, 0o755)
        json.dump({"routes": [{"repo": "someone/handover"}]}, open(f"{t}/routes.json", "w"))
        self.rulings = f"{t}/p2-rulings.md"; open(self.rulings, "w").write("# P2 裁决记录\n")
        w = lambda n, d: json.dump(d, open(f"{qa}/reports/merged/PR-{n}.json", "w"))
        # owner PR 100: one P1, fixed by three merged fix PRs (each report retests it: pass) -> fix_round 3, status fixed
        w(100, report(100, "PraiseZhu", [{"fingerprint": FP, "severity": "P1", "source": "check", "status": "introduced", "test_backed": True, "title": "p1"}]))
        for i, n in enumerate((201, 202, 203)):
            w(n, report(n, "PraiseZhu", retest=[{"original_pr": 100, "fingerprint": FP, "result": "pass", "at": f"2026-11-0{i + 1}T00:00:00Z"}]))
        # owner PR 300: a confirmed P2 ruled 「结案」 -> no row
        w(300, report(300, "PraiseZhu", [{"fingerprint": "mq1-bbbbbbbbbbbbbbbbbbbb", "severity": "P2", "source": "check", "status": "introduced", "test_backed": True, "title": "p2"}]))

    def build(self, *args, tool=TOOL):
        env = {**os.environ, "QA_HOME": self.qa, "PATH": f"{self.t}/bin:{os.environ['PATH']}", "MIVO_QA_HANDOFF_ROUTES": f"{self.t}/routes.json",
               "MIVO_QA_P2_RULINGS": self.rulings}
        return subprocess.run(["node", tool, *args], capture_output=True, text=True, env=env, timeout=120)

    def files(self):
        return sorted(os.path.relpath(os.path.join(r, f), self.qa) for r, _, fs in os.walk(self.qa) for f in fs)


class BuildLedger(Base):
    def test_help_prints_usage_and_writes_nothing(self):
        before = self.files()
        for a in ("--help", "-h"):
            r = self.build(a)
            self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("usage: build-ledger.mjs", r.stdout)
        self.assertEqual(self.files(), before)

    def test_unknown_args_exit_2_and_write_nothing(self):
        before = self.files()
        for a in (["--bogus"], ["--dry-run", "2026-11-03", "2026-11-04"], ["tomorrow"]):
            r = self.build(*a)
            self.assertEqual(r.returncode, 2, (a, r.stdout, r.stderr)); self.assertIn("bad argument", r.stderr)
        self.assertEqual(self.files(), before)

    def test_severity_and_third_round_needs_you(self):
        open(self.rulings, "a").write("| 日期 | 报告 PR | 问题 | 裁决 | 理由 |\n|---|---|---|---|---|\n| 2026-11-03 | #300 | p2 | 结案 | 已修 |\n")
        r = self.build(DAY); self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.load(open(f"{self.qa}/ledger/daily/{DAY}.json"))
        rows = {x["key"]: x for x in doc["rows"]}
        row = rows[f"{REPO}#100:{FP}"]
        self.assertEqual((row["level"], row["severity"]), ("P1", "P1"))
        self.assertTrue(all(x.get("severity") for x in doc["rows"]))
        self.assertEqual((row["status"], row["fix_round"], row["retest"]), ("fixed", 3, "pass"))
        self.assertTrue(row["needs_human"])
        self.assertEqual(doc["summary"]["needs_you"], 1)
        ny = doc["needs_you"][0]
        self.assertEqual((ny["pr"], ny["severity"]), (100, "P1")); self.assertIn("确认结案", ny["reason"])
        self.assertNotIn(300, {x["pr"] for x in doc["rows"]}, "结案 ruling: no row / ticket")
        os.environ["PI_QA_HOME"] = self.qa
        import ledger as LG; importlib.reload(LG)
        self.assertEqual(LG.check(doc, DAY), ([], ["GitHub 查询失败：None（修复 PR 未核对，标 unknown）"]) if not doc["github_check"].get("ok") else ([], []))


    def test_jiean_ruling_excluded_before_third_round(self):
        # PM 2026-11-02 16:22 PST: #966/#1077 「结案（已修）」 must not be in needs_you even at fix_round 3
        open(self.rulings, "a").write("| 日期 | 报告 PR | 问题 | 裁决 | 理由 |\n|---|---|---|---|---|\n| 2026-11-03 | #100 | 第三轮 | 结案（已修） | 已修 |\n")
        r = self.build(DAY); self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.load(open(f"{self.qa}/ledger/daily/{DAY}.json"))
        row = {x["key"]: x for x in doc["rows"]}[f"{REPO}#100:{FP}"]
        self.assertEqual(row["fix_round"], 3); self.assertFalse(row["needs_human"])
        self.assertEqual((doc["summary"]["needs_you"], doc["needs_you"]), (0, []))

class LedgerPy(Base):
    def lg(self):
        os.environ["PI_QA_HOME"] = self.qa
        import ledger as LG
        return importlib.reload(LG)

    def test_help_prints_only(self):
        before = self.files()
        r = subprocess.run([sys.executable, os.path.join(BIN, "ledger.py"), "--help"], capture_output=True, text=True,
                           env={**os.environ, "PI_QA_HOME": self.qa, "PATH": f"{self.t}/bin:{os.environ['PATH']}"}, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("set-fix-pr", r.stdout)
        self.assertEqual(self.files(), before)
        r = subprocess.run([sys.executable, os.path.join(BIN, "ledger.py"), "--bogus"], capture_output=True, text=True,
                           env={**os.environ, "PI_QA_HOME": self.qa}, timeout=60)
        self.assertNotEqual(r.returncode, 0); self.assertEqual(self.files(), before)

    def test_tool_switch(self):
        LG = self.lg(); self.assertEqual(LG.TOOL, TOOL.replace("/pi/test/../", "/pi/"))
        self.assertEqual(os.path.realpath(LG.TOOL), os.path.realpath(TOOL))
        os.makedirs(L.BASE, exist_ok=True); open(f"{L.BASE}/ledger-tool-legacy", "w").close(); self.addCleanup(os.remove, f"{L.BASE}/ledger-tool-legacy")
        LG = self.lg(); self.assertEqual(LG.TOOL, f"{self.qa}/ledger/build-ledger.mjs")

    def test_set_fix_pr_then_rebuild(self):
        self.assertEqual(self.build(DAY).returncode, 0)
        key = f"{REPO}#100:{FP}"
        json.dump({"_doc": "x", "other": {"status": "open"}, key: {"status": "concluded", "concluded_reason": "old", "summary": "keep"}},
                  open(f"{self.qa}/ledger/overrides.json", "w"))
        LG = self.lg()
        with mock.patch.object(LG, "now_cst", lambda: dt.datetime(2026, 11, 3, 8, 0, 0, tzinfo=L.CST)):
            LG.set_fix_pr([key, "#1644", "--note", "QA #100 回归用例"]); LG.set_fix_pr([key, "1644"])
        ov = json.load(open(f"{self.qa}/ledger/overrides.json"))
        self.assertEqual(ov["other"], {"status": "open"})
        self.assertEqual({k: ov[key][k] for k in ("status", "fix_pr", "via", "summary")}, {"status": "fixed", "fix_pr": 1644, "via": "ledger.py set-fix-pr", "summary": "keep"})
        self.assertNotIn("concluded_reason", ov[key])
        baks = [f for f in os.listdir(f"{self.qa}/ledger") if f.startswith("overrides.json.bak-")]
        self.assertEqual(len(baks), 2, baks)   # same second: both backups kept
        with self.assertRaises(SystemExit): LG.set_fix_pr([f"{REPO}#999:{FP}", "1"])   # not a ledger row
        with self.assertRaises(SystemExit): LG.set_fix_pr([key, "abc"])
        self.assertEqual(self.build(DAY).returncode, 0)
        row = next(x for x in json.load(open(f"{self.qa}/ledger/daily/{DAY}.json"))["rows"] if x["key"] == key)
        self.assertEqual((row["fix_pr"], row["status"]), (1644, "fix_pr_open"))   # gh does not know #1644 -> merged unknown


class P2(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="p2-"); self.addCleanup(shutil.rmtree, self.t, True)
        import p2 as P; self.P = importlib.reload(P)
        self.rul = f"{self.t}/p2-rulings.md"; open(self.rul, "w").write("# P2 裁决记录\n\n| 日期 | 报告 PR | 问题 | 裁决 | 理由 | 证据 | 会签 | 规则状态 |\n|---|---|---|---|---|---|---|---|\n")
        self.ev = f"{self.t}/ev.md"; open(self.ev, "w").write("x")

    def test_record_status_verdict_and_backups(self):
        fixed = dt.datetime(2026, 11, 3, 8, 0, 0, tzinfo=self.P.CST)
        fake_dt = types.SimpleNamespace(datetime=types.SimpleNamespace(now=lambda tz=None: fixed))
        with mock.patch.object(self.P, "RULINGS", self.rul), mock.patch.object(self.P, "dt", fake_dt):
            self.P.record(["#966", "第三轮", "结案", "已修", self.ev, "--by", "Jasper via PM"])
            self.P.record(["1077", "第三轮", "修", "r", self.ev, "--status", "待 Praise 确认"])
            with self.assertRaises(SystemExit): self.P.record(["1", "t", "已修", "r", self.ev])
        rows = [l for l in open(self.rul) if l.startswith("| 2026-11-03")]
        self.assertTrue(rows[0].rstrip().endswith("| 结案 | 已修 | " + self.ev + " | Jasper via PM | Jasper 已裁 |"), rows[0])
        self.assertTrue(rows[1].rstrip().endswith("| 待 Praise 确认 |"), rows[1])
        baks = sorted(f for f in os.listdir(self.t) if f.startswith("p2-rulings.md.bak-"))
        self.assertEqual(baks, ["p2-rulings.md.bak-20261103-080000", "p2-rulings.md.bak-20261103-080000-1"])

    def test_help_prints_only(self):
        for a in (["--help"], ["record", "--help"], ["-h"]):
            r = subprocess.run([sys.executable, os.path.join(BIN, "p2.py"), *a], capture_output=True, text=True,
                               env={**os.environ, "PATH": "/nonexistent", "HOME": self.t}, timeout=60)   # no gh / pi-auto reachable: a real run would fail
            self.assertEqual(r.returncode, 0, (a, r.stderr)); self.assertIn("p2.py record", r.stdout)
        self.assertFalse(os.path.exists(f"{L.BASE}/out/p2"))
        r = subprocess.run([sys.executable, os.path.join(BIN, "p2.py"), "--bogus"], capture_output=True, text=True, env={**os.environ, "PATH": "/nonexistent", "HOME": self.t}, timeout=60)
        self.assertNotEqual(r.returncode, 0)

    def test_readers_treat_jiean_like_fixed(self):
        import finding_policy as F, pm
        txt = "| 2026-11-03 | #966 | t | 结案 | r |\n| 2026-11-03 | #1077 | t | 结案（已修） | r |\n| 2026-11-03 | #5 | t | 修 | r |\n"
        self.assertEqual(F.no_fix_rulings(txt), {966, 1077}); self.assertEqual(F.fix_rulings(txt), {5})
        f = f"{self.t}/r.md"; open(f, "w").write(txt)
        with mock.patch.object(pm, "RULINGS", f): self.assertEqual(pm.ruled("修"), {966, 1077, 5})


if __name__ == "__main__":
    unittest.main()
