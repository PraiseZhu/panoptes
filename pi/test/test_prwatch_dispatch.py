#!/usr/bin/env python3
"""prwatch.py: memory-based parallel fix dispatch (Praise 10-24) and content-based new-finding detection (10-24 restore).
Local only: no network, no gh, no model, no writes outside a temp dir. Run: python3 test/test_prwatch_dispatch.py"""
for _k in [k for k in __import__("os").environ if k.startswith('GIT_')]: del __import__("os").environ[_k]
import datetime as dt, json, os, shutil, sys, tempfile, time, types, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import prwatch  # noqa: E402

PY = b"python3\0/workspace/panoptes/pi/bin/prfix.py\0--next\0"
WRAP = b"/bin/bash\0/workspace/panoptes/pi/bin/run.sh\0prfix\0--next\0"
PAN = b"bash\0/workspace/panoptes/run-nightly.sh\0--pr\0001188\0"
def wf(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f: f.write(s)

class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prwatch-test-")
        self.saved = {k: getattr(prwatch, k) for k in ("BASE", "RPT", "FIX_FLAG", "qlog", "act", "kick_outbound", "mem_mb", "proc_table",
                                                       "inputs_sig", "DRY")}
        self.saved_sp = (prwatch.subprocess.run, prwatch.subprocess.Popen)
        prwatch.BASE = self.tmp; prwatch.RPT = f"{self.tmp}/reports"; prwatch.FIX_FLAG = f"{self.tmp}/takeover/prfix-event"
        os.makedirs(prwatch.RPT); wf(prwatch.FIX_FLAG, "")
        self.logs, self.acts, self.kicks, self.launches = [], [], [], []
        prwatch.qlog = self.logs.append
        prwatch.act = lambda job, a: self.acts.append(a)
        prwatch.kick_outbound = lambda job, key: self.kicks.append(key)
        prwatch.inputs_sig = lambda: "sig"
        prwatch.DRY = False
        self.pending = [{"qa": 1179, "fp": "mq1-x", "sev": "P2", "reason_if_skipped": None},
                        {"qa": 1238, "fp": "mq1-y", "sev": "P2", "reason_if_skipped": None}]
        prwatch.subprocess.run = lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=json.dumps(self.pending), stderr="")
        prwatch.subprocess.Popen = lambda cmd, **k: self.launches.append(cmd)
    def tearDown(self):
        for k, v in self.saved.items(): setattr(prwatch, k, v)
        prwatch.subprocess.run, prwatch.subprocess.Popen = self.saved_sp
        shutil.rmtree(self.tmp, ignore_errors=True)
    def procs(self, table, mem):
        prwatch.proc_table = lambda: table; prwatch.mem_mb = lambda: mem
    def tickets(self, **kw): wf(f"{self.tmp}/out/praise-pr/tickets.json", json.dumps({"tickets": kw}))

class TestHeadroom(T):
    def test_running_fixes_reserve_what_they_may_still_grow(self):
        t = {10: (1, 300, PY), 11: (10, 200, b"node\0pi\0"), 20: (1, 500, PY)}       # fix A: 500 MB tree, fix B: 500 MB
        # no merge test running yet, but fixes are: one may start any minute -> its whole peak is reserved too (review 10-24)
        self.assertEqual(prwatch.headroom_mb(t, mem=11000), 11000 - 2 * (prwatch.RUN_PEAK_MB - 500) - prwatch.PANOPTES_PEAK_MB)
        self.assertEqual(prwatch.headroom_mb({}, mem=11000), 11000)                    # nothing running: the first fix needs no merge reserve
    def test_wrapper_waiting_for_memory_counts_in_full_and_ro_listings_not_at_all(self):
        t = {30: (1, 5, WRAP), 40: (1, 900, b"python3\0/x/bin/prfix.py\0--pending-json\0")}
        pys, waiting = prwatch.prfix_runs(t)
        self.assertEqual((pys, waiting), ([], [30]))
        self.assertEqual(prwatch.headroom_mb(t, mem=9000), 9000 - prwatch.RUN_PEAK_MB - prwatch.PANOPTES_PEAK_MB)
        t[31] = (30, 100, PY)                                                         # its prfix.py started: no longer waiting
        self.assertEqual(prwatch.prfix_runs(t), ([31], []))
    def test_nested_panoptes_runs_share_one_budget_counted_once(self):
        t = {50: (1, 100, b"bash\0-c\0QA_HOME=x /workspace/panoptes/run-nightly.sh --catch-up\0"), 51: (50, 400, b"node\0catchup.mjs\0"),
             52: (51, 1000, PAN), 53: (52, 1500, b"node\0vitest\0")}
        self.assertEqual(prwatch.headroom_mb(t, mem=10000), 10000 - (prwatch.PANOPTES_PEAK_MB - 3000))
    def count(self, base, mem, st=None):
        need = prwatch.ADMIT_FREE_MB + prwatch.learned(st, "fix", prwatch.RUN_PEAK_MB); t = dict(base); n = 0
        while prwatch.headroom_mb(t, mem=mem - 300 * n, st=st) >= need: n += 1; t[100 + n] = (1, 300, PY)
        return n
    def test_box_numbers(self):   # 16 GB box, ~11 GB idle, merge test ~6 GB: one fix beside a merge test
        self.assertEqual(self.count({}, 11000), 1)
        self.assertEqual(self.count({50: (1, 1400, PAN)}, 9700), 1)
        self.assertEqual(self.count({}, 24000), 6)                                    # more memory -> more fixes, no cap
        st = {"peaks": {"fix": [1100, 1200, 1150], "panoptes": [5200, 5600, 5000]}}   # learned: 1200*1.3, 5600*1.3
        self.assertEqual((prwatch.learned(st, "fix", 2560), prwatch.learned(st, "panoptes", 6144)), (1560, 7280))
        self.assertEqual(self.count({}, 13000, st), 2)                                # learned peaks decide (here 2 at a time)

class TestDispatch(T):
    def test_launches_one_more_when_memory_allows(self):
        self.procs({10: (1, 400, PY)}, 16000); self.tickets(**{"1178:a": {"status": "running", "pid": 10}})
        prwatch.dispatch_fix(st := {})
        self.assertEqual(len(self.launches), 1); self.assertTrue(st["fix_more"])
        self.assertIn("1 already fixing", self.logs[-1])
    def test_no_cap_but_one_launch_at_a_time(self):
        self.procs({10: (1, 400, PY)}, 11000); self.tickets()                       # pid 10 has not claimed yet
        prwatch.dispatch_fix({}); self.assertEqual(self.launches, [])
        self.procs({30: (1, 5, WRAP)}, 11000)                                        # run.sh still waiting to start prfix.py
        prwatch.dispatch_fix({}); self.assertEqual(self.launches, [])
    def test_waits_for_memory_and_logs_once(self):
        self.procs({}, prwatch.ADMIT_FREE_MB + prwatch.RUN_PEAK_MB - 1); self.tickets()
        st = {}; prwatch.dispatch_fix(st); prwatch.dispatch_fix(st)
        self.assertEqual(self.launches, []); self.assertEqual(sum("waits for memory" in m for m in self.logs), 1)
        prwatch.mem_mb = lambda: 11000; prwatch.dispatch_fix(st)
        self.assertEqual(len(self.launches), 1)
    def test_nothing_to_do(self):
        self.pending = [{"qa": 1, "fp": "f", "sev": "P1", "reason_if_skipped": "运行中（pid 9）"}]
        self.procs({}, 11000); self.tickets(); prwatch.dispatch_fix(st := {})
        self.assertEqual(self.launches, []); self.assertFalse(st["fix_more"])
    def test_no_flag_no_dispatch(self):
        os.remove(prwatch.FIX_FLAG); self.procs({}, 11000); self.tickets(); prwatch.dispatch_fix({})
        self.assertEqual(self.launches, [])
    def test_every_new_fix_action_is_kicked_once(self):
        now = prwatch.now_cst(); old = (now - dt.timedelta(days=3)).isoformat()
        self.tickets(**{"a": {"status": "pr_action_written", "action_key": "fixpr:1:a", "updated_at": now.isoformat()},
                        "b": {"status": "pr_action_written", "action_key": "fixpr:2:b", "updated_at": now.isoformat()},
                        "c": {"status": "pr_action_written", "action_key": "fixpr:3:c", "updated_at": old},
                        "d": {"status": "running", "pid": 1}})
        st = {}; prwatch.kick_finished(st); prwatch.kick_finished(st)
        self.assertEqual(sorted(self.kicks), ["fixpr:1:a", "fixpr:2:b"])

class TestDetectionAndPeaks(T):
    def test_editors_dry_runs_and_listings_are_not_fix_runs(self):
        t = {1: (0, 50, b"vim\0bin/prfix.py\0"), 2: (0, 5, b"less\0/workspace/panoptes/pi/bin/prfix.py\0"),
             3: (0, 300, b"python3\0/workspace/panoptes/pi/bin/prfix.py\0--dry-run\0--qa\0001\0"),
             4: (0, 5, b"/bin/bash\0/workspace/panoptes/pi/bin/run.sh\0prfix\0--list\0"), 5: (0, 5, b"grep\0prfix\0")}
        self.assertEqual(prwatch.prfix_runs(t), ([], []))
        t[6] = (0, 200, PY); self.assertEqual(prwatch.prfix_runs(t), ([6], []))
    def test_peaks_are_learned_from_finished_runs(self):
        st = {}
        for pid, rss in ((10, 900), (11, 1500), (12, 300)):                           # 300 MB = an idle exit: not recorded
            prwatch.sample_peaks(st, {pid: (1, rss, PY)}); prwatch.sample_peaks(st, {})
        self.assertEqual(st["peaks"]["fix"], [900, 1500])
        self.assertEqual(prwatch.learned(st, "fix", 2560), 2560)                       # fewer than 3 runs: default
        prwatch.sample_peaks(st, {13: (1, 1000, PY)}); prwatch.sample_peaks(st, {13: (1, 1200, PY)}); prwatch.sample_peaks(st, {})
        self.assertEqual(st["peaks"]["fix"], [900, 1500, 1200]); self.assertEqual(prwatch.learned(st, "fix", 2560), 1950)
        prwatch.sample_peaks(st, {50: (1, 2000, PAN), 51: (50, 3000, b"node\0vitest\0")}); prwatch.sample_peaks(st, {})
        self.assertEqual(st["peaks"]["panoptes"], [5000])
    def test_held_dispatch_is_logged_after_15_min(self):
        self.procs({10: (1, 400, PY)}, 16000); self.tickets()                          # pid 10 never claims a ticket
        st = {}; prwatch.dispatch_fix(st); self.assertEqual(self.logs, [])
        st["fix_picking_since"] -= 901; prwatch.dispatch_fix(st); prwatch.dispatch_fix(st)
        self.assertEqual(sum("claimed no ticket" in m for m in self.logs), 1)
        self.procs({}, 16000); prwatch.dispatch_fix(st); self.assertNotIn("fix_picking_since", st)

class TestNewFindings(T):
    def report(self, n, fps, sev="P1"):
        wf(f"{prwatch.RPT}/PR-{n}.json", json.dumps({"prs": [{"number": n, "author": "PraiseZhu",
                                                             "findings": [{"fingerprint": f, "severity": sev, "title": "t"} for f in fps]}]}))
    def test_baseline_remembers_and_sends_nothing(self):
        self.report(1, ["mq1-a"]); st = {}
        prwatch.scan_reports(st)
        self.assertEqual(self.acts, []); self.assertIn("mq1-a", st["notified_fps"]); self.assertEqual(st["sig_v"], prwatch.SIG_V)
    def test_touched_file_same_content_is_not_new(self):   # 10-24: the restore re-touched every report
        self.report(1, ["mq1-a"]); st = {}; prwatch.scan_reports(st)
        st["notified_fps"] = []                                          # even if the fingerprint was forgotten
        os.utime(f"{prwatch.RPT}/PR-1.json", (time.time() + 60, time.time() + 60))
        prwatch.scan_reports(st); self.assertEqual(self.acts, [])
    def test_real_new_finding_is_sent_once(self):
        self.report(1, ["mq1-a"]); st = {}; prwatch.scan_reports(st)
        self.report(1, ["mq1-a", "mq1-b"]); prwatch.scan_reports(st); prwatch.scan_reports(st)
        self.assertEqual(self.acts, []); self.assertIn("mq1-b", st["notified_fps"])        # 10-24: the bot's work, no DM
        self.assertTrue(any("new findings #1: 1" in l for l in self.logs))
    def test_old_format_state_rebaselines_without_dms(self):   # state written before 10-24: "mtime:size" strings, no sig_v
        self.report(1, ["mq1-a"]); self.report(2, ["mq1-b"])
        st = {"baselined": "2026-10-23T21:25:01+08:00", "reports": {"PR-1.json": "1:1", "PR-2.json": "1:1"}, "notified_fps": []}
        prwatch.scan_reports(st)
        self.assertEqual(self.acts, []); self.assertEqual(set(st["notified_fps"]), {"mq1-a", "mq1-b"})
        self.report(3, ["mq1-c"]); prwatch.scan_reports(st)
        self.assertIn("mq1-c", st["notified_fps"])
    def test_others_prs_are_not_ours(self):
        st = {}; prwatch.scan_reports(st)
        wf(f"{prwatch.RPT}/PR-5.json", json.dumps({"prs": [{"number": 5, "author": "aj0928", "findings": [{"fingerprint": "mq1-z", "severity": "P0"}]}]}))
        prwatch.scan_reports(st); self.assertEqual(self.acts, [])

class TestDraftCI(T):
    """Our own draft fix PRs (10-24): green -> ready; whole-PR gate races -> update-branch when behind main, else one rerun; a real
    red -> prfix --revise (the model fixes CI); revisions used up / switch off -> one DM saying what was already tried."""
    BODY = "QA: #1179\nmq1-x\n<!-- pi-line fixpr:1179:mq1-aaaaaaaaaaaaaaaaaaaa -->"
    def setUp(self):
        super().setUp(); self.saved_gh = prwatch.gh; self.prs = []
        prwatch.gh = lambda args, js=True: self.prs
        self.saved_rf = prwatch.REVISE_FLAG; prwatch.REVISE_FLAG = f"{self.tmp}/takeover/prfix-revise"; wf(prwatch.REVISE_FLAG, "")
        self.procs({}, 16000)
    def tearDown(self):
        prwatch.gh = self.saved_gh; prwatch.REVISE_FLAG = self.saved_rf; super().tearDown()
    def draft(self, head, checks, behind=False, body=None, branch="qa-fix/fixpr-1179-mq1-x"):
        self.prs = [{"number": 1301, "headRefOid": head, "headRefName": branch, "body": self.BODY if body is None else body,
                     "mergeStateStatus": "BEHIND" if behind else "CLEAN",
                     "statusCheckRollup": [{"name": nm, "status": "COMPLETED", "conclusion": c} for nm, c in checks]}]
    def tick(self, st):
        na, nl = len(self.acts), len(self.launches); st["drafts_at"] = 0; prwatch.check_drafts(st)
        return self.acts[na:] + self.launches[nl:]
    def test_green_goes_ready(self):
        self.draft("a" * 40, [("unit", "SUCCESS"), ("review-pr", "FAILURE")])
        self.assertEqual([x["type"] for x in self.tick({})], ["gh.pr.ready"])
    def test_only_bot_fix_prs(self):   # #1166: a session's draft with "QA: #N" is not ours
        self.draft("a" * 40, [("unit", "FAILURE")], body="QA: #1179")
        self.assertEqual(self.tick({}), [])
        self.draft("a" * 40, [("unit", "SUCCESS")], branch="fix/manual")
        self.assertEqual(self.tick({}), [])
    def test_real_red_goes_to_the_model(self):
        self.draft("a" * 40, [("unit (windows, 4)", "FAILURE"), ("pr-format-gate", "SUCCESS")])
        st = {}; got = self.tick(st)
        self.assertEqual(len(got), 1); self.assertEqual(got[0][1:], ["prfix", "--revise", "1301"])
        self.assertEqual(self.tick(st), [])                                     # once per head
        self.tickets(**{"rev:1301": {"status": "pushed", "last_head": "a" * 40, "revisions": 1}})
        self.draft("b" * 40, [("unit (windows, 4)", "FAILURE")])                # the pushed fix is red again: round 2
        self.assertEqual(self.tick(st)[0][1:], ["prfix", "--revise", "1301"])
    def test_revisions_used_up_go_back_to_the_bot_switch_off_is_the_one_dm(self):
        self.tickets(**{"rev:1301": {"status": "pushed", "last_head": "x" * 40, "revisions": 2}})
        self.draft("a" * 40, [("unit", "FAILURE")])
        self.assertEqual(self.tick({})[0][1:], ["prfix", "--revise", "1301"])   # prfix pauses + retries it itself (no DM)
        os.remove(prwatch.REVISE_FLAG); self.tickets()
        self.draft("b" * 40, [("unit", "FAILURE")])
        got = self.tick({}); self.assertIn("开关关着", got[0]["text"])

    def test_paused_or_waiting_for_praise_revise_is_left_alone(self):
        future = (prwatch.now_cst() + dt.timedelta(hours=5)).isoformat(timespec="seconds")
        self.tickets(**{"rev:1301": {"status": "parked", "revisions": 2, "head": "a" * 40, "next_retry_at": future}})
        self.draft("a" * 40, [("unit", "FAILURE")])
        self.assertEqual(self.tick({}), [])                                      # paused: prfix tries again by itself later
        self.tickets(**{"rev:1301": {"status": "parked", "revisions": 2, "head": "a" * 40, "next_retry_at": "2000-01-01T00:00:00+08:00"}})
        self.assertEqual(self.tick({})[0][1:], ["prfix", "--revise", "1301"])   # the pause is over
        self.tickets(**{"rev:1301": {"status": "needs_praise", "head": "a" * 40}})
        self.assertEqual(self.tick({}), [])                                      # kind 5: prfix asked him, nothing else
        self.draft("b" * 40, [("unit", "FAILURE")])                              # a new head (someone pushed): the model may try again
        self.assertEqual(self.tick({})[0][1:], ["prfix", "--revise", "1301"])
        self.tickets(**{"rev:1301": {"status": "running", "pid": 999999, "head": "a" * 40, "revisions": 0}})
        self.draft("a" * 40, [("unit", "FAILURE")]); st = {}
        self.assertEqual(self.tick(st)[0][1:], ["prfix", "--revise", "1301"])
        self.assertEqual(self.tick(st), [])                                      # launched again but died again: wait 30 min
        st["revise_launched"]["1301:" + "a" * 12] -= 1801
        self.assertEqual(self.tick(st)[0][1:], ["prfix", "--revise", "1301"])

    def test_dead_revise_run_is_relaunched_but_not_in_a_storm(self):
        self.tickets(**{"rev:1301": {"status": "running", "pid": 999999, "head": "a" * 40, "revisions": 0}})
        self.draft("a" * 40, [("unit", "FAILURE")]); st = {}
        self.assertEqual(self.tick(st)[0][1:], ["prfix", "--revise", "1301"])
        self.assertEqual(self.tick(st), [])                                      # launched again but died again: wait 30 min
        st["revise_launched"]["1301:" + "a" * 12] -= 1801
        self.assertEqual(self.tick(st)[0][1:], ["prfix", "--revise", "1301"])
    def test_wrapper_waiting_for_memory_counts_as_busy(self):
        self.procs({7: (1, 5, b"/bin/bash\0/x/bin/run.sh\0prfix\0--revise\0001301\0")}, 16000)
        self.draft("a" * 40, [("unit", "FAILURE")])
        self.assertEqual(self.tick({}), [])
    def test_conflict_with_main_goes_to_the_model_never_a_dm(self):   # 10-24 evening: no DM, the bot keeps at it
        self.draft("a" * 40, [], behind=False); self.prs[0]["mergeStateStatus"] = "DIRTY"
        st = {}; got = self.tick(st)
        self.assertEqual(got[0][1:], ["prfix", "--revise", "1301"])            # merge main in, model resolves (10-24)
        self.tickets(**{"rev:1301": {"status": "pushed", "last_head": "x" * 40, "merges": prwatch.MAX_MERGES}})
        self.draft("b" * 40, [], behind=False); self.prs[0]["mergeStateStatus"] = "DIRTY"
        got = self.tick(st); self.assertEqual(got[0][1:], ["prfix", "--revise", "1301"])   # rounds used up: prfix pauses it itself
        future = (prwatch.now_cst() + dt.timedelta(hours=5)).isoformat(timespec="seconds")
        self.tickets(**{"rev:1301": {"status": "parked", "head": "c" * 40, "next_retry_at": future}})
        self.draft("c" * 40, [], behind=False); self.prs[0]["mergeStateStatus"] = "DIRTY"
        self.assertEqual(self.tick({}), [])                                      # paused: nothing, no DM

    def test_gate_still_red_after_rerun_waits_and_reruns_never_a_dm(self):
        st, race = {}, [("pr-format-gate", "FAILURE"), ("unit", "SUCCESS")]
        self.draft("e" * 40, race); self.tick(st)                                # rerun
        self.assertEqual(self.tick(st), [])                                      # still red: wait, no DM
        st["race_wait"]["1301:" + "e" * 12] -= 3 * 3600 + 1
        self.assertEqual([x["type"] for x in self.tick(st)], ["gh.run.rerun-failed"])   # re-run again every 3 h
        self.procs({}, 1000)
        self.draft("a" * 40, [("unit", "FAILURE")]); st = {}
        self.assertEqual(self.tick(st), []); self.procs({}, 16000)
        self.assertEqual(self.tick(st)[0][1:], ["prfix", "--revise", "1301"])   # retried next round
        st, race = {}, [("pr-format-gate", "FAILURE"), ("unit", "SUCCESS")]
        for i, head in enumerate(("b" * 40, "c" * 40, "d" * 40)):              # main keeps moving: no lifetime cap of 2 any more
            self.draft(head, race, behind=True)
            self.assertEqual([x["type"] for x in self.tick(st)], ["gh.pr.update-branch"])
        self.draft("e" * 40, race)                                              # not behind: one rerun
        self.assertEqual([x["type"] for x in self.tick(st)], ["gh.run.rerun-failed"])

    def test_memory_short_waits(self):
        self.procs({}, 1000)
        self.draft("a" * 40, [("unit", "FAILURE")]); st = {}
        self.assertEqual(self.tick(st), []); self.procs({}, 16000)
        self.assertEqual(self.tick(st)[0][1:], ["prfix", "--revise", "1301"])   # retried next round
    def test_race_prone_red_behind_main_syncs_at_once_else_reruns(self):
        st, race = {}, [("pr-format-gate", "FAILURE"), ("unit", "SUCCESS")]
        for i, head in enumerate(("b" * 40, "c" * 40, "d" * 40)):              # main keeps moving: no lifetime cap of 2 any more
            self.draft(head, race, behind=True)
            self.assertEqual([x["type"] for x in self.tick(st)], ["gh.pr.update-branch"])
        self.draft("e" * 40, race)                                              # not behind: one rerun
        self.assertEqual([x["type"] for x in self.tick(st)], ["gh.run.rerun-failed"])
    def test_freshness_gate_and_the_verify_aggregate(self):   # #1263: pr-ci-freshness + verify red, the real one is the TLA+ spec
        st = {}
        self.draft("a" * 40, [("pr-ci-freshness", "FAILURE"), ("verify", "FAILURE"), ("unit", "SUCCESS")])
        self.assertEqual([x["type"] for x in self.tick(st)], ["gh.run.rerun-failed"])          # only gates (verify adds nothing)
        self.draft("b" * 40, [("pr-ci-freshness", "FAILURE"), ("verify", "FAILURE"), ("lifeline spec (TLA+)", "FAILURE")])
        self.assertEqual(self.tick(st)[0][1:], ["prfix", "--revise", "1301"])
    def test_still_red_after_the_ruled_rerun_never_spins(self):   # 10-25 #1271: prwatch relaunched a revise that only skipped, 14 h
        led = f"{prwatch.BASE}/out/outbound/ledger.json"; os.makedirs(os.path.dirname(led), exist_ok=True)
        wf(led, json.dumps({"rerun-ruled:1301:" + "a" * 12: {"status": "done"}}))
        self.tickets(**{"rev:1301": {"status": "pushed", "last_head": "a" * 40, "rerun_only": True, "action_key": "rerun-ruled:1301:" + "a" * 12}})
        red = [("Linux unit tests", "FAILURE"), ("pr-ci-freshness", "FAILURE")]
        saved = prwatch.behind_main
        try:
            prwatch.behind_main = lambda h: 7; st = {}; self.draft("a" * 40, red)
            got = self.tick(st); self.assertEqual([x["type"] for x in got], ["gh.pr.update-branch"])   # behind main: fresh CI on main
            self.assertEqual(self.tick(st), [])                                                       # once per head, no revise launch
            prwatch.behind_main = lambda h: 0; st = {}
            self.assertEqual(self.tick(st), [])                                                       # not behind: wait ...
            st["race_wait"]["1301:" + "a" * 12] -= 3 * 3600 + 1
            got = self.tick(st); self.assertEqual([x["type"] for x in got], ["gh.run.rerun-failed"])  # ... re-run every 3 h
            self.assertTrue(got[0]["ruled"]); self.assertIn("Linux unit tests", got[0]["names"])
            self.assertFalse([x for x in self.acts if x.get("type") == "slack.dm"])
        finally: prwatch.behind_main = saved
    def test_update_branch_daily_cap(self):
        st, race = {}, [("pr-format-gate", "FAILURE")]
        for i in range(prwatch.UB_PER_DAY): self.draft(str(i) * 40, race, behind=True); self.tick(st)
        self.draft("f" * 40, race, behind=True)
        self.assertEqual(self.tick(st), [])                                      # out of syncs today: waits (kind 3), no DM

class TestRetryDue(T):
    def test_backoff_running_out_makes_dispatch_due(self):
        self.procs({}, 16000); st = {"fix_inputs": "sig", "fix_checked_at": time.time() - 120}
        prwatch.dispatch_fix(st); self.assertEqual(self.launches, [])                     # nothing changed, rechecked 2 min ago
        past = (prwatch.now_cst() - dt.timedelta(minutes=1)).isoformat(timespec="seconds")
        self.tickets(**{"1188:mq1-a": {"status": "failed", "next_retry_at": past}})
        prwatch.inputs_sig = lambda: "sig"
        prwatch.dispatch_fix(st); self.assertEqual(len(self.launches), 1)

class TestResolved(T):
    def setUp(self):
        super().setUp(); self.saved_gh = prwatch.gh; self.view = {}
        prwatch.gh = lambda args, js=True: self.view[int(args[2])]
    def tearDown(self):
        prwatch.gh = self.saved_gh; super().tearDown()
    def test_green_follow_up_for_what_the_bot_finished_on_its_own(self):
        wf(f"{self.tmp}/out/outbound/ledger.json", json.dumps({"fixpr:1188:mq1-a:a3": {"status": "done", "result": "https://github.com/x/y/pull/1264 | ok"}}))
        self.tickets(**{"1188:mq1-a": {"qa": 1188, "sev": "P2", "title": "归档回执", "status": "pr_action_written", "action_key": "fixpr:1188:mq1-a:a3",
                                       "history": [{"status": "needs_human"}, {"status": "pr_action_written"}]},
                        "1179:mq1-b": {"qa": 1179, "status": "pr_action_written", "action_key": "k", "history": [{"status": "pr_action_written"}]}})
        self.view = {1264: {"state": "OPEN", "isDraft": False, "mergeStateStatus": "CLEAN"}, 1263: {"state": "MERGED"}, 1301: {"state": "OPEN", "isDraft": True}}
        st = {"draft_seen": {"1263:aaaa": "red", "1263:bbbb": "ready", "1301:cccc": "red"}}
        prwatch.resolved(st)
        self.assertEqual(len(self.acts), 1); txt = self.acts[0]["text"]
        self.assertIn("🟢 已恢复", txt); self.assertIn("#1264", txt); self.assertIn("已转正式", txt); self.assertIn("#1263", txt); self.assertIn("已合并", txt)
        self.assertNotIn("#1301", txt); self.assertNotIn("1179", txt)                # still a draft / never asked him
        st["resolved_at"] = 0; prwatch.resolved(st); self.assertEqual(len(self.acts), 1)   # once

class TestNoSecretsInEnv(T):
    def test_kick_gives_outbound_the_secrets_without_touching_os_environ(self):   # 10-24 security
        # a test-only key name: the real ones may be in the environment that runs the tests, and values are never printed
        k = "PRWATCH_TEST_ONLY_SECRET"; os.environ.pop(k, None)
        prwatch.kick_outbound = self.saved["kick_outbound"]
        saved = prwatch.secret_env_dict; prwatch.secret_env_dict = lambda: {k: "s3cret"}
        os.makedirs(f"{self.tmp}/out/logs", exist_ok=True); seen = {}
        prwatch.subprocess.Popen = lambda cmd, **kw: seen.update(kw.get("env") or {})
        try: prwatch.kick_outbound("praise-pr", "k1")
        finally: prwatch.secret_env_dict = saved
        self.assertTrue(seen.get(k) == "s3cret", "outbound did not get the secret")
        self.assertFalse(k in os.environ, "kick_outbound rewrote prwatch's own os.environ")

class TestKicks(T):
    def test_kind5_and_revise_actions_are_kicked_at_once(self):
        now = prwatch.now_cst().isoformat(timespec="seconds")
        self.tickets(**{"1188:mq1-a": {"qa": 1188, "fp": "mq1-a", "status": "needs_praise", "updated_at": now},
                        "1176:mq1-b": {"qa": 1176, "fp": "mq1-b", "status": "parked", "updated_at": now},
                        "rev:1301": {"pr": 1301, "fp": "mq1-cccccccccccccccccccc", "status": "pushed", "action_key": "revise:1301:abc", "updated_at": now},
                        "rev:1302": {"pr": 1302, "fp": "mq1-dddddddddddddddddddd", "head": "e" * 40, "status": "needs_praise", "updated_at": now}})
        prwatch.kick_finished({})
        self.assertEqual(sorted(self.kicks), sorted(["needs-praise:1188:mq1-a", "revise:1301:abc", "revise-needs-praise:1302:" + "e" * 12]))

    def test_daily_digest_lists_paused_work_once_a_day(self):
        self.tickets(**{"1188:mq1-a": {"qa": 1188, "fp": "mq1-a", "sev": "P2", "status": "parked", "park_reason": "修不动",
                                       "last_error": "修了 4 轮仍没过", "next_retry_at": "2026-10-25T03:00:00+08:00"},
                        "1176:mq1-b": {"qa": 1176, "status": "norepro", "confirmed": True}})
        st = {}; early = prwatch.now_cst().replace(hour=8, minute=0)
        prwatch.digest_parked(st, early); self.assertEqual(self.acts, [])                 # before 09:00
        nine = early.replace(hour=9, minute=5)
        prwatch.digest_parked(st, nine); prwatch.digest_parked(st, nine.replace(hour=15))
        self.assertEqual(len(self.acts), 1); a = self.acts[0]
        self.assertEqual((a["type"], a["key"]), ("slack.dm", f"prbot-digest:{nine.date().isoformat()}"))
        self.assertTrue(a["text"].startswith(prwatch.PN.KINDS["fyi"])); self.assertIn("#1188", a["text"]); self.assertNotIn("#1176", a["text"])
        self.tickets(); st2 = {}; prwatch.digest_parked(st2, nine); self.assertEqual(len(self.acts), 1)   # nothing paused: no message


if __name__ == "__main__":
    unittest.main(verbosity=1)
