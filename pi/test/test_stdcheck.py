#!/usr/bin/env python3
"""stdcheck (11-05): every check's breach, gap idempotency, the 2-in-a-row alert, overdue escalation, retries, the flag gate and the
Jasper facts. Mocks only: gh is stubbed (no network, no real gh, no keys), every path is a temp dir, no task is really re-run."""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import json, os, shutil, sys, tempfile, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
_T = tempfile.mkdtemp(prefix="stdcheck-test-")
os.environ.setdefault("PI_STATE", f"{_T}/state")
import stdcheck as S  # noqa: E402
dt = S.dt; CST = S.CST
NOW = dt.datetime(2026, 11, 5, 12, 30, tzinfo=CST)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="stdcheck-", dir=_T); t = self.tmp
        self.prs = {"PraiseZhu/panoptes": [], S.REPO: []}; self.review = []; self.checks = {}; self.statuses = {}; self.gh_calls = []; self.runs = {j: [] for j in S.TASKS}; self.spawned = []
        ps = [mock.patch.object(S, "BASE", t), mock.patch.object(S, "OUT", f"{t}/out/stdcheck"), mock.patch.object(S, "FLAG", f"{t}/stdcheck-enabled"),
              mock.patch.object(S, "GAPS", f"{t}/gaps-open.json"), mock.patch.object(S, "REPORTS", f"{t}/reports"),
              mock.patch.object(S, "ACK_FILE", f"{t}/no-ack.json"),  # missing => today behavior; Allowlist tests override
              mock.patch.object(S, "RETRY_SLEEP_S", 0),
              mock.patch.object(S, "DRY", False), mock.patch.object(S, "gh", self.gh), mock.patch.object(S, "task_runs", self.task_runs),
              mock.patch.object(S.subprocess, "Popen", self.popen), mock.patch.object(S, "write_atomic", self.write)]
        for p in ps: p.start(); self.addCleanup(p.stop)
        os.makedirs(f"{t}/reports"); os.makedirs(f"{t}/out/logs"); os.makedirs(f"{t}/out/ledger")
        self.healthy()

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True); open(path, "w").write(text)

    def gh(self, args, js=True):
        self.gh_calls.append(args)
        if os.environ.get("_STD_GH_FAIL"): raise RuntimeError("gh pr: HTTP 502")
        if args[0] == "api":   # review_pr REST reads (product repo)
            path = args[1]; assert path.startswith(f"repos/{S.REPO}/"), path
            if "/pulls?" in path: return list(self.review)
            sha = path.split("/commits/")[1].split("/")[0]
            if "/check-runs" in path: return {"check_runs": self.checks.get(sha, [])}
            return {"statuses": self.statuses.get(sha, [])}
        repo = args[args.index("-R") + 1]
        return list(self.prs[repo]) if "--author" not in args or args[args.index("--author") + 1] == "PraiseZhu" else []

    def task_runs(self, job): return self.runs[job], lambda r: r[2] in (0, 75)
    def popen(self, cmd, **kw): self.spawned.append(cmd); return mock.Mock(pid=4242)

    def at(self, t):
        """one run at t with the time-dependent inputs (task records) healthy at t"""
        self.healthy(t); return S.run(t)

    def healthy(self, NOW=NOW):
        """every check passes at NOW"""
        for j, (slots, judge) in S.TASKS.items():
            s = S.last_slot(slots, judge, NOW); self.runs[j] = [(s, s + dt.timedelta(minutes=1), 0, None)]
        day = (S.last_slot(*S.TASKS["daily"], NOW).date() - dt.timedelta(days=1)).isoformat()
        json.dump({f"mivo-daily-report:{day}": {"status": "done"}}, open(f"{self.tmp}/out/outbound/ledger.json", "w") if os.makedirs(f"{self.tmp}/out/outbound", exist_ok=True) is None else None)
        open(f"{self.tmp}/out/ledger/{S.last_slot(*S.TASKS['ledger'], NOW).date()}.json", "w").write("{}")

    def report(self, n, merged, started, base="main"):
        z = lambda t: t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        json.dump({"run": {"started_at": z(started)}, "prs": [{"number": n, "merged_at": z(merged), "base_ref": base}]}, open(f"{self.tmp}/reports/PR-{n}.json", "w"))

    def gaps(self): return json.load(open(S.GAPS))
    def open_ids(self): return sorted(g["id"] for g in self.gaps() if g["status"] == "open")


class Checks(Base):
    def test_all_pass(self):
        r = S.run(NOW); self.assertEqual(r, {c: {} for c in S.CHECKS}); self.assertEqual(self.open_ids(), [])

    def test_qa_start_breach_from_report(self):
        self.report(1800, NOW - dt.timedelta(minutes=60), NOW - dt.timedelta(minutes=45))   # 15 min late
        self.report(1801, NOW - dt.timedelta(minutes=60), NOW - dt.timedelta(minutes=58))   # 2 min: fine
        self.assertEqual(list(S.check_qa_start(NOW)), ["PR-1800"])

    def test_qa_start_breach_from_mergewatch_log(self):
        t = lambda m: (NOW - dt.timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M:%S CST")
        open(f"{self.tmp}/out/logs/mergewatch-2026-11-05.log", "w").write(
            f"{t(30)} branch merge #1900 into integration/x\n{t(30)} branch merge #1901 into release/y\n"
            f"{t(29)} launched priority run pid 1 for PR #1901 (main abc, try 1/3)\n{t(5)} branch merge #1902 into main\n")
        self.assertEqual(sorted(S.check_qa_start(NOW)), ["PR-1900"])   # 1901 launched in 1 min, 1902 only 5 min old

    def test_qa_start_only_main_integration_release(self):
        late = lambda n, b: self.report(n, NOW - dt.timedelta(minutes=60), NOW - dt.timedelta(minutes=40), base=b)
        late(1810, "main"); late(1811, "integration/canvas-s2"); late(1812, "release/1.2"); late(1813, "feat/x"); late(1814, "probe-a")
        t = (NOW - dt.timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S CST")
        open(f"{self.tmp}/out/logs/mergewatch-2026-11-05.log", "w").write(f"{t} branch merge #1815 into feat/y\n{t} branch merge #1816 into release/2\n")
        self.assertEqual(sorted(S.check_qa_start(NOW)), ["PR-1810", "PR-1811", "PR-1812", "PR-1816"])

    def test_qa_start_pst_log_lines(self):
        t = (NOW - dt.timedelta(minutes=30)).astimezone(dt.timezone(dt.timedelta(hours=-8))).strftime("%Y-%m-%d %H:%M:%S PST")
        open(f"{self.tmp}/out/logs/mergewatch-2026-11-04.log", "w").write(f"{t} branch merge #1903 into main\n")
        self.assertIn("PR-1903", S.check_qa_start(NOW))

    def test_panoptes_prs_breach(self):
        self.prs["PraiseZhu/panoptes"] = [{"number": n} for n in range(4)]
        self.assertEqual(list(S.check_panoptes_prs(NOW)), ["open-prs"])
        self.prs["PraiseZhu/panoptes"] = [{"number": n} for n in range(3)]; self.assertEqual(S.check_panoptes_prs(NOW), {})
        self.assertNotIn("--author", self.gh_calls[0])   # all open PRs, through lib.gh (App identity when gh-app-enabled)

    def test_fix_prs_breach(self):
        fx = lambda n, **k: {"number": n, "headRefName": k.get("h", "feat/x"), "body": k.get("b", "")}
        self.prs[S.REPO] = [fx(1, h="qa-fix/1"), fx(2, b="<!-- pi-line fixpr:2 -->"), fx(3, h="fix/qa-3-abcdef12"), fx(4)]
        self.assertEqual(list(S.check_fix_prs(NOW)), ["concurrent"])
        self.prs[S.REPO] = self.prs[S.REPO][:2] + [fx(4)]; self.assertEqual(S.check_fix_prs(NOW), {})

    def test_fix_prs_drafts_count_any_author_product_repo(self):
        """Jasper: open Pi-line fix PRs in xindong/mivo-canvas-plugin, drafts included; breach only when > 2"""
        self.prs[S.REPO] = [{"number": n, "headRefName": f"qa-fix/{n}", "body": "", "isDraft": True} for n in range(3)]
        self.assertEqual(list(S.check_fix_prs(NOW)), ["concurrent"])           # 3 drafts = breach
        c = self.gh_calls[-1]; self.assertEqual(c[c.index("-R") + 1], "xindong/mivo-canvas-plugin")
        self.assertNotIn("--draft", c); self.assertNotIn("--author", c); self.assertEqual(c[c.index("--state") + 1], "open")
        self.prs[S.REPO] = self.prs[S.REPO][:2]; self.assertEqual(S.check_fix_prs(NOW), {})   # exactly 2 = pass

    def test_tasks_breach_missing_failed_and_retry_once(self):
        self.runs["pmnudge"] = []
        s = S.last_slot(*S.TASKS["ledger"], NOW); self.runs["ledger"] = [(s, s, 1, None)]
        st = {}; r = S.check_tasks(NOW, retry=S.retry_task(st, NOW))
        self.assertEqual(sorted(k.split(":")[0] for k in r), ["ledger", "pmnudge"])
        self.assertIn("missing", r[[k for k in r if k.startswith("pmnudge")][0]])
        S.check_tasks(NOW, retry=S.retry_task(st, NOW))
        self.assertEqual(self.spawned, [["bash", S.RUN_SH, "ledger"]])   # the failed task is re-run once per slot, not every hour

    def test_tasks_daily_outbound_ledger_and_ledger_output(self):
        os.unlink(f"{self.tmp}/out/outbound/ledger.json"); os.unlink(glob_one(f"{self.tmp}/out/ledger/*.json"))
        r = S.check_tasks(NOW); self.assertEqual(sorted(k.split(":")[0] for k in r), ["daily", "ledger"])

    def pr(self, n, draft=False): self.review.append({"number": n, "draft": draft, "head": {"sha": f"sha{n}"}})
    def ago(self, m): return (NOW - dt.timedelta(minutes=m)).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_review_pr_empty_queue_is_ok(self):
        self.assertEqual(S.check_review_pr(NOW), {})
        self.pr(1, draft=True); self.assertEqual(S.check_review_pr(NOW), {})               # drafts never wait on review-pr
        self.pr(2); self.checks["sha2"] = [{"name": "review-pr", "status": "completed", "started_at": self.ago(300), "completed_at": self.ago(290)}]
        self.assertEqual(S.check_review_pr(NOW), {})                                          # done long ago, nothing waiting
        self.assertEqual({c[0] for c in self.gh_calls}, {"api"})                              # REST only
        self.assertEqual(len(self.gh_calls), 1 + 1 + 3)                                         # 1 list per run + 2 per ready PR

    def test_review_pr_queue_stalled(self):
        self.pr(10); self.pr(11); self.pr(12)
        self.checks["sha10"] = [{"name": "review-pr", "status": "queued", "started_at": None, "completed_at": None}]
        self.statuses["sha11"] = [{"context": "review-pr (integration)", "state": "pending", "updated_at": self.ago(5)}]   # pending != activity
        self.checks["sha12"] = [{"name": "review-pr", "status": "completed", "started_at": self.ago(200), "completed_at": self.ago(180)},
                                {"name": "lint", "status": "completed", "started_at": self.ago(2), "completed_at": self.ago(1)}]  # not review-pr
        self.pr(13)                                                                           # no review-pr at all = missing = waiting
        r = S.check_review_pr(NOW); self.assertEqual(list(r), ["queue"]); self.assertIn("#10 #11 #13", r["queue"])
        self.checks["sha12"][0].update(completed_at=self.ago(20))                             # a recent completion anywhere -> moving
        self.assertEqual(S.check_review_pr(NOW), {})

    def test_review_pr_long_running(self):
        self.pr(20); self.pr(21)
        self.checks["sha20"] = [{"name": "review-pr", "status": "in_progress", "started_at": self.ago(75), "completed_at": None}]
        self.checks["sha21"] = [{"name": "review-pr (integration)", "status": "in_progress", "started_at": self.ago(30), "completed_at": None}]
        self.assertEqual(list(S.check_review_pr(NOW)), ["running:PR-20"])

    def test_review_pr_stall_gap_owner_jasper(self):
        self.pr(30); S.run(NOW); S.run(NOW)
        g = [x for x in self.gaps() if x["id"] == "stdcheck:review_pr:queue"]
        self.assertEqual(len(g), 1); self.assertEqual((g[0]["owner"], g[0]["status"]), ("Jasper", "open"))
        self.review = []; S.run(NOW + dt.timedelta(minutes=5)); self.assertEqual(self.open_ids(), [])

    def test_review_pr_reads_no_heartbeat_file(self):
        import builtins; real = builtins.open; opened = []
        def spy(f, *a, **k): opened.append(str(f)); return real(f, *a, **k)
        self.pr(40)
        with mock.patch.object(builtins, "open", spy), mock.patch.object(S.os, "stat", side_effect=AssertionError("stat")):
            S.check_review_pr(NOW)
        self.assertEqual(opened, []); self.assertFalse(hasattr(S, "VIGIL_HB"))
        self.assertNotIn("heartbeat", open(S.__file__).read().lower())



class Allowlist(Base):
    """acknowledged qa_start breaches + superseded fix_prs (mocks only; ack file under tmp)."""

    def ack_file(self, doc):
        p = f"{self.tmp}/stdcheck-acknowledged.json"
        json.dump(doc, open(p, "w")); return p

    def test_qa_start_allowlisted_does_not_fail_and_is_listed(self):
        self.report(1718, NOW - dt.timedelta(hours=2), NOW - dt.timedelta(hours=2) + dt.timedelta(minutes=15))  # 15 min late, merge >1h ago
        self.ack_file({"acknowledged_breaches": [{"pr": 1718, "handler": "Praise PR", "gap_id": "qa-start-1105-metric", "date": "2026-11-05"}], "superseded": []})
        with mock.patch.object(S, "ACK_FILE", f"{self.tmp}/stdcheck-acknowledged.json"):
            bad = S.check_qa_start(NOW)
        self.assertEqual(bad, {})
        ack = S.check_qa_start.last_acknowledged
        self.assertEqual(len(ack), 1)
        self.assertEqual(ack[0]["pr"], 1718)
        self.assertEqual(ack[0]["gap_id"], "qa-start-1105-metric")
        self.assertEqual(ack[0]["handler"], "Praise PR")
        self.assertIn("15 min", ack[0]["detail"])

    def test_qa_start_new_breach_still_fails(self):
        """not listed, or listed but merged <1h before run -> still fails"""
        # not listed
        self.report(1800, NOW - dt.timedelta(hours=2), NOW - dt.timedelta(hours=2) + dt.timedelta(minutes=15))
        self.ack_file({"acknowledged_breaches": [{"pr": 1718, "handler": "Praise PR", "gap_id": "x", "date": "2026-11-05"}], "superseded": []})
        with mock.patch.object(S, "ACK_FILE", f"{self.tmp}/stdcheck-acknowledged.json"):
            self.assertEqual(list(S.check_qa_start(NOW)), ["PR-1800"])
        # listed but merge <1h ago -> ignore entry, still fail, and note it
        self.report(1718, NOW - dt.timedelta(minutes=30), NOW - dt.timedelta(minutes=10))  # 20 min late, merge 30 min ago
        with mock.patch.object(S, "ACK_FILE", f"{self.tmp}/stdcheck-acknowledged.json"):
            bad = S.check_qa_start(NOW)
        self.assertIn("PR-1718", bad)
        self.assertIn("PR-1800", bad)
        ign = S.check_qa_start.last_ignored
        self.assertTrue(any(x["pr"] == 1718 and "ignored" in x for x in ign))

    def test_qa_start_missing_or_invalid_ack_file_behaves_as_today(self):
        self.report(1800, NOW - dt.timedelta(hours=2), NOW - dt.timedelta(hours=2) + dt.timedelta(minutes=15))
        with mock.patch.object(S, "ACK_FILE", f"{self.tmp}/no-such-ack.json"):
            self.assertEqual(list(S.check_qa_start(NOW)), ["PR-1800"])
        open(f"{self.tmp}/bad.json", "w").write("{not json")
        with mock.patch.object(S, "ACK_FILE", f"{self.tmp}/bad.json"):
            self.assertEqual(list(S.check_qa_start(NOW)), ["PR-1800"])

    def test_fix_prs_superseded_excluded_from_count(self):
        fx = lambda n, **k: {"number": n, "headRefName": k.get("h", f"qa-fix/{n}"), "body": k.get("b", "")}
        # 5 open fix PRs; superseding 1734 and 1621 leaves 3 -> still breach; superseding one more leaves 2 -> pass
        self.prs[S.REPO] = [fx(1762), fx(1734), fx(1727), fx(1724), fx(1621)]
        self.ack_file({"acknowledged_breaches": [], "superseded": [
            {"pr": 1734, "superseded_by": 1710, "date": "2026-11-05"},
            {"pr": 1621, "superseded_by": 1648, "date": "2026-11-05"},
        ]})
        with mock.patch.object(S, "ACK_FILE", f"{self.tmp}/stdcheck-acknowledged.json"):
            bad = S.check_fix_prs(NOW)
        self.assertEqual(list(bad), ["concurrent"])
        self.assertIn("#1762", bad["concurrent"]); self.assertIn("#1727", bad["concurrent"]); self.assertIn("#1724", bad["concurrent"])
        self.assertNotIn("#1734", bad["concurrent"]); self.assertNotIn("#1621", bad["concurrent"])
        sup = S.check_fix_prs.last_superseded
        self.assertEqual({x["pr"] for x in sup}, {1734, 1621})
        self.assertEqual({(x["pr"], x["superseded_by"]) for x in sup}, {(1734, 1710), (1621, 1648)})
        # drop one more active -> 2 active = pass, superseded still listed
        self.prs[S.REPO] = [fx(1762), fx(1734), fx(1727), fx(1621)]
        with mock.patch.object(S, "ACK_FILE", f"{self.tmp}/stdcheck-acknowledged.json"):
            self.assertEqual(S.check_fix_prs(NOW), {})
            self.assertEqual({x["pr"] for x in S.check_fix_prs.last_superseded}, {1734, 1621})

    def test_fix_prs_missing_ack_file_unchanged(self):
        self.prs[S.REPO] = [{"number": n, "headRefName": f"qa-fix/{n}", "body": ""} for n in range(3)]
        with mock.patch.object(S, "ACK_FILE", f"{self.tmp}/missing.json"):
            self.assertEqual(list(S.check_fix_prs(NOW)), ["concurrent"])




def glob_one(p): return S.glob.glob(p)[0]


class Gaps(Base):
    def test_breach_writes_gap_idempotently_and_keeps_foreign_entries(self):
        json.dump([{"id": "slack-daily-1029", "status": "open", "owner": "Jasper"}], open(S.GAPS, "w"))
        self.prs["PraiseZhu/panoptes"] = [{"number": n} for n in range(5)]
        S.run(NOW); g1 = self.gaps()
        self.at(NOW + dt.timedelta(hours=1)); g2 = self.gaps()
        self.assertEqual(len(g1), 2); self.assertEqual(len(g2), 2)
        mine = [g for g in g2 if g["id"] == "stdcheck:panoptes_prs:open-prs"][0]
        self.assertEqual((mine["owner"], mine["opened"], mine["deadline"]), ("Praise", g1[1]["opened"], g1[1]["deadline"]))
        self.assertEqual(mine["last_seen"], (NOW + dt.timedelta(hours=1)).isoformat(timespec="seconds"))
        self.assertEqual(g2[0], {"id": "slack-daily-1029", "status": "open", "owner": "Jasper"})   # not ours: untouched

    def test_gap_closes_when_check_passes_and_not_on_error(self):
        self.prs["PraiseZhu/panoptes"] = [{"number": n} for n in range(5)]; S.run(NOW)
        os.environ["_STD_GH_FAIL"] = "1"
        try: r = self.at(NOW + dt.timedelta(hours=1))
        finally: del os.environ["_STD_GH_FAIL"]
        self.assertIsNone(r["panoptes_prs"]); self.assertEqual(self.open_ids(), ["stdcheck:panoptes_prs:open-prs"])   # error != pass
        self.prs["PraiseZhu/panoptes"] = []; self.at(NOW + dt.timedelta(hours=2))
        self.assertEqual(self.open_ids(), []); self.assertEqual(self.gaps()[0]["status"], "closed")

    def test_overdue_gap_escalates_once_for_jasper(self):
        self.prs[S.REPO] = [{"number": n, "headRefName": f"qa-fix/{n}", "body": ""} for n in range(3)]
        S.run(NOW); self.assertFalse(os.path.exists(f"{S.OUT}/escalations.json"))
        late = NOW + dt.timedelta(hours=S.DEADLINE_H["fix_prs"], minutes=1)
        self.at(late); self.at(late + dt.timedelta(hours=1))
        esc = json.load(open(f"{S.OUT}/escalations.json"))
        self.assertEqual(list(esc), ["stdcheck:fix_prs:concurrent"])
        e = esc["stdcheck:fix_prs:concurrent"]; self.assertEqual((e["to"], e["owner"], e["for_brief"]), ("Jasper", "Praise PR", "2026-11-06"))
        self.assertEqual(e["escalated_at"], late.isoformat(timespec="seconds"))
        f = S.brief_facts("2026-11-06", "2026-11-05"); self.assertEqual([x["id"] for x in f["escalations"]], ["stdcheck:fix_prs:concurrent"])


class Alerts(Base):
    def test_two_consecutive_failures_alert_then_recover(self):
        self.prs["PraiseZhu/panoptes"] = [{"number": n} for n in range(5)]
        af = f"{S.OUT}/alerts/panoptes_prs.json"
        S.run(NOW); self.assertFalse(os.path.exists(af))                        # 1 failure: no alert yet
        self.at(NOW + dt.timedelta(hours=1)); a = json.load(open(af))            # 2 in a row: alert file
        self.assertEqual(a["consecutive_failures"], 2)
        self.assertEqual([x["check"] for x in S.brief_facts("2026-11-06", "2026-11-05")["alerts"]], ["panoptes_prs"])
        self.prs["PraiseZhu/panoptes"] = []; self.at(NOW + dt.timedelta(hours=2)); self.assertFalse(os.path.exists(af))

    def test_error_is_retried_then_counts_as_failure(self):
        os.environ["_STD_GH_FAIL"] = "1"
        try: S.run(NOW); self.at(NOW + dt.timedelta(hours=1))
        finally: del os.environ["_STD_GH_FAIL"]
        pan = [c for c in self.gh_calls if "PraiseZhu/panoptes" in c]
        self.assertEqual(len(pan), 2 * S.ATTEMPTS)                              # retried ATTEMPTS times per run
        a = json.load(open(f"{S.OUT}/alerts/panoptes_prs.json")); self.assertIn("HTTP 502", a["error"])

    def test_retry_recovers_within_a_run(self):
        n = [0]
        def flaky(now):
            n[0] += 1
            if n[0] < 2: raise RuntimeError("blip")
            return {}
        self.assertEqual(S.run_check("x", flaky, NOW), ({}, None)); self.assertEqual(n[0], 2)

    def test_crash_writes_alert_and_rc1(self):
        open(S.FLAG, "w").close()
        with mock.patch.object(S, "run", side_effect=ValueError("boom")):
            self.assertEqual(S.main(), 1); self.assertEqual(S.main(), 1)
        a = json.load(open(f"{S.OUT}/alerts/stdcheck.json")); self.assertEqual((a["consecutive_failures"], a["error"]), (2, "ValueError: boom"))


class Gate(Base):
    def test_no_flag_is_noop(self):
        self.assertEqual(S.main(), 0); self.assertEqual(self.gh_calls, []); self.assertFalse(os.path.exists(S.OUT))

    def test_passrate(self):
        self.prs["PraiseZhu/panoptes"] = [{"number": n} for n in range(5)]
        S.run(NOW); self.prs["PraiseZhu/panoptes"] = []; self.at(NOW + dt.timedelta(hours=1))
        p = json.load(open(f"{S.OUT}/passrate-2026-11-05.json"))
        self.assertEqual(p["runs"], 2); self.assertEqual(p["checks"]["panoptes_prs"], {"pass": 1, "fail": 1, "error": 0, "rate": 0.5})
        self.assertEqual(p["checks"]["review_pr"]["rate"], 1.0); self.assertEqual(p["overall"], 0.9)


if __name__ == "__main__":
    try: unittest.main(verbosity=2)
    finally: shutil.rmtree(_T, ignore_errors=True)
