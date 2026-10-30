#!/usr/bin/env python3
"""infrafix.py + infra_outbound.py unit tests — local only: no network, no model, no writes outside a temp dir.
Run: python3 test/test_infrafix_unit.py   (or python3 -m pytest -q test/test_infrafix_unit.py)."""
import os as _os
for _k in [k for k in _os.environ if k.startswith('GIT_')]: del _os.environ[_k]   # never act on a hook's GIT_DIR
_os.environ["PI_FLEET_MODE"] = "pi"; _os.environ["INFRA_TEST_MEM_GB"] = "8"; _os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
import argparse, contextlib, datetime as dt, io, json, os, shutil, subprocess, sys, tempfile, textwrap, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for d in ("ref", "patched", "bin"):                   # -> sys.path bin, patched, ref (patched/ + ref/ only in the staging copy)
    p = os.path.join(os.path.dirname(HERE), d)
    if os.path.isdir(p): sys.path.insert(0, p)
sys.dont_write_bytecode = True
import infrafix as X        # noqa: E402
import infra_outbound as O  # noqa: E402

CST = X.CST
NOW = dt.datetime(2026, 10, 23, 21, 0, tzinfo=CST)
def wf(p, s, mode="w"):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, mode) as f: f.write(s)
def rf(p):
    with open(p) as f: return f.read()
def utc(t): return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
def report(n, at, run, health=None, prs=None):
    h = {"status": "degraded", "qa_tool_failures": [], "env_problems": [], "llm_errors": [], "complete": True, "all_discovered_tested": True}
    h.update(health or {})
    return {"generated_at": utc(at), "run": {"id": run}, "system_health": h, "prs": prs if prs is not None else [{"number": n, "outcome": "passed"}]}
def sh(cmd, cwd=None, env=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env, shell=isinstance(cmd, str))
    if r.returncode: raise AssertionError(f"{cmd}: {r.stdout}{r.stderr}")
    return r.stdout
def occ(cat, stage, msg, at, run, source="report", **kw): return X.mk(cat, stage, msg, at, source, f"/r/{run}.json", kw.get("pr"), run, {"message": msg}, issue=kw.get("issue"))
def ctx_of(occs, **kw):
    c = {"cands": X.aggregate(occs), "runs": [], "holds": {}, "automerge": True, "warns": [], "links": {}, "wontfix": {}, "issues": [],
         "prs": [], "tickets": {}, "now": NOW, "notes": []}
    c.update(kw); return c
def states(ctx): return {v["c"]["sig8"]: v for v in X.evaluate_all(ctx, NOW)}

class Tmp(unittest.TestCase):
    PATHS = ("PANOPTES", "LIVE", "WT_ROOT", "NOTES", "OUT", "GATES", "REPORTS", "CATCHUP", "LOGDIR")
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="infrafix-test-")
        self.env = dict(os.environ)
        # this module's env, every test: another test module may have changed os.environ (PI_FLEET_MODE=grok made run_ticket "off").
        # PI_WORK_ROOT/HOME/npm cache: infra_outbound's W.mkdtemp() and npm otherwise write the real /workspace/pi-work/tmp, ~/.npm/_logs
        os.environ.update(PI_FLEET_MODE="pi", INFRA_TEST_MEM_GB="8", PI_WORK_ROOT=f"{self.tmp}/work", HOME=f"{self.tmp}/home",
                          npm_config_cache=f"{self.tmp}/home/.npm")
        self.saved = {k: getattr(X, k) for k in self.PATHS + ("record_actions", "gh", "pi", "mode")}
        X.NOTES = f"{self.tmp}/notes"; X.OUT = f"{self.tmp}/out"; X.GATES = f"{self.tmp}/gates.json"
        X.REPORTS = f"{self.tmp}/reports/merged"; X.CATCHUP = f"{self.tmp}/reports/catch-up"; X.LOGDIR = f"{self.tmp}/logs"
        X.PANOPTES = X.LIVE = f"{self.tmp}/panoptes"; X.WT_ROOT = f"{self.tmp}/panoptes/.worktrees"
        for d in (X.NOTES, X.OUT, X.REPORTS, X.CATCHUP, X.LOGDIR): os.makedirs(d, exist_ok=True)
        self.actions = []
        X.record_actions = lambda job, day, acts: self.actions.extend(acts)
        X.gh = lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected gh call"))
    def tearDown(self):
        for k, v in self.saved.items(): setattr(X, k, v)
        os.environ.clear(); os.environ.update(self.env)
        shutil.rmtree(self.tmp, ignore_errors=True)
    def fakebin(self, name, script):
        d = f"{self.tmp}/fakebin"; os.makedirs(d, exist_ok=True)
        p = f"{d}/{name}"; wf(p, "#!/bin/bash\n" + textwrap.dedent(script)); os.chmod(p, 0o755)
        os.environ["PATH"] = d + ":" + os.environ["PATH"]

# ====================================================================== 1. signatures
class TestSignature(unittest.TestCase):
    def test_normalize_variable_parts(self):
        a = X.normalize("fetch failed (UND_ERR_SOCKET other side closed) after 187710 ms")
        b = X.normalize("fetch failed (UND_ERR_SOCKET other side closed) after 23431 ms")
        self.assertEqual(a, b)
        self.assertEqual(X.normalize("Key Expiry time 2026-10-18 03:37:12.199000+00:00 run 64c655e8-093a-425b-b115-dd2e15b94c9a"),
                         X.normalize("Key Expiry time 2026-10-19 11:00:00.000000+00:00 run 11111111-093a-425b-b115-dd2e15b94c9a"))
        self.assertEqual(X.normalize("merge 0123abcd4567 has no reports/merged/PR-1138.json (PR #1138)"),
                         X.normalize("merge 89efab01cdef has no reports/merged/PR-77.json (PR #77)"))
        self.assertEqual(X.normalize("missing: apt:chromium"), X.normalize("apt:chromium"))

    def test_http_codes_kept(self):
        self.assertNotEqual(X.normalize("模型网关返回 400"), X.normalize("模型网关返回 401"))
        self.assertIn("400", X.normalize("模型网关返回 400"))
        self.assertIn("141", X.normalize("run-nightly.sh exited with code 141"))
        self.assertNotIn("299999", X.normalize('Received 299999.5'))
        self.assertEqual(X.normalize("could not install 6 dependencies"), X.normalize("could not install 3 dependencies"))

    def test_same_fault_same_sig_across_sources(self):
        msg = "AI 补测失败（模型返回的 JSON 不完整，finish_reason=length）"
        r = X.report_occs(report(1138, NOW, "run-a", {"llm_errors": [{"pr": 1138, "stage": "ai-tests", "message": msg}]}), "PR-1138.json", NOW - dt.timedelta(days=7))
        issue = {"number": 16, "state": "OPEN", "title": "[Mivo QA][system_health] 1 problem", "body": "```json mivo-qa-health\n{}\n```", "url": "u",
                 "createdAt": utc(NOW), "comments": [{"createdAt": utc(NOW), "body": f"Observed again\n\nRun 0a3a0fd8-678c-481f-a671-cb6b6c8a0385: 1 problem\n- **LLM error** (PR #1138) [ai-tests]: {msg}\n"}]}
        io_, links, _ = X.scan_issues([issue], NOW - dt.timedelta(days=7))
        self.assertEqual(r[0]["sig"], io_[0]["sig"])
        self.assertEqual(links[r[0]["sig"]], {16})
        self.assertTrue(X.SIG_RE.fullmatch(r[0]["sig"]))

    def test_catchup_message_cut(self):
        a = X.mk("qa_tool_failures", "catch-up", "catch-up gave up after 3 attempts (last: exit 1; missing)", NOW, "s", "f", 5)
        b = X.mk("qa_tool_failures", "catch-up", "catch-up gave up after 3 attempts (last: exit 1; missing); merge abcdef012345 has no matching reports/merged/PR-9.json", NOW, "s", "f", 9)
        self.assertEqual(a["sig"], b["sig"])
        self.assertEqual(a["run"], "gaveup:5")      # one give-up = one event wherever it is listed

# ====================================================================== 2. sources
class TestSources(Tmp):
    def test_reports_skip_transient_selfrepaired_and_window(self):
        since = NOW - dt.timedelta(days=7)
        d = report(1, NOW, "r1", {"qa_tool_failures": [{"stage": "janitor", "message": "x", "transient": True}],
                                  "env_problems": [{"message": "y", "self_repaired": True}],
                                  "llm_errors": [{"stage": "ai-tests", "message": "z"}]})
        self.assertEqual([o["stage"] for o in X.report_occs(d, "f", since)], ["ai-tests"])
        old = report(1, NOW - dt.timedelta(days=8), "r0", {"llm_errors": [{"stage": "ai-tests", "message": "z"}]})
        self.assertEqual(X.report_occs(old, "f", since), [])

    def test_bootstrap_failure_folds_env_problems(self):
        d = report(1, NOW, "r1", {"qa_tool_failures": [{"stage": "bootstrap", "message": "bootstrap-box.sh could not install 2 dependencies"}],
                                  "env_problems": [{"message": "apt:chromium"}], "bootstrap": {"missing_after": ["missing: apt:chromium"]}})
        os_ = X.report_occs(d, "f", NOW - dt.timedelta(days=7))
        c = X.aggregate(os_)
        env = next(v for v in c.values() if v["cat"] == "env_problems")
        self.assertEqual(env["count"], 1)                      # env_problems + missing_after of the same run = 1
        self.assertTrue(env["covered"])
        v = states(ctx_of(os_))
        self.assertEqual(v[env["sig8"]]["state"], "watch"); self.assertIn("bootstrap", v[env["sig8"]]["reason"])
        boot = next(v_ for v_ in v.values() if v_["c"]["klass"] == "bootstrap")
        self.assertEqual(boot["state"], "todo")

    def test_field_outcome_run_detectors(self):
        d = report(1, NOW, "r1", {"complete": False}, prs=[{"number": 1, "outcome": "qa-error", "outcome_reason": "runner crashed",
                                                             "code_review": {"status": "failed", "verdict": {"status": "failed"}, "summary": "s"}}])
        kinds = sorted((o["cat"], o["stage"]) for o in X.report_occs(d, "f", NOW - dt.timedelta(days=7)))
        self.assertEqual(kinds, [("field", "code-review"), ("outcome", "qa-error"), ("run", "run")])
        f_ = next(o for o in X.report_occs(d, "f", NOW - dt.timedelta(days=7)) if o["cat"] == "field")
        self.assertEqual(f_["sig"], X.CR_SIG)

    def test_code_review_ran_field_is_a_verdict_not_a_fault(self):   # panoptes #20: reports with code_review.ran
        for ran in (True, False):
            d = report(1, NOW, "r1", {}, prs=[{"number": 1, "code_review": {"status": "failed", "ran": ran, "verdict": {"status": "failed"}, "summary": "s"}}])
            self.assertEqual([o for o in X.report_occs(d, "f", NOW - dt.timedelta(days=7)) if o["cat"] == "field"], [], ran)

    def test_catchup_and_logs(self):
        wf(f"{X.CATCHUP}/state.json", json.dumps({"prs": {"5": {"number": 5, "merge_sha": "a" * 40, "attempts": 3, "last_reason": "exit 1; missing",
                                                                 "gave_up_at": NOW.isoformat()}}}))
        wf(f"{X.CATCHUP}/run-20261023T200000.json", json.dumps({"finished_at": NOW.isoformat(), "system_health": {"gave_up": [
            {"pr": 5, "stage": "catch-up", "message": "catch-up gave up after 3 attempts (last: exit 1; missing)"}]}}))
        c = X.aggregate(X.scan_catchup(NOW - dt.timedelta(days=7)))
        self.assertEqual(len(c), 1); self.assertEqual(next(iter(c.values()))["count"], 1)   # state + run file = one event
        lines = [{"t": utc(NOW), "msg": "run-nightly exit", "pid": 1, "exitCode": 0}, {"t": utc(NOW), "msg": "run-nightly exit", "pid": 2, "exitCode": 75},
                 {"t": utc(NOW), "msg": "run-nightly exit", "pid": 3, "exitCode": 141}, {"t": utc(NOW), "msg": "run-nightly exit", "pid": 4, "exitCode": 1},
                 {"t": utc(NOW - dt.timedelta(days=9)), "msg": "run-nightly exit", "pid": 5, "exitCode": 1}]
        wf(f"{X.LOGDIR}/control.log", "\n".join(json.dumps(l) for l in lines) + "\nnot json\n")
        wf(f"{X.LOGDIR}/nightly-20261023T200000.log", "[panoptes] ok\nTypeError: x.y is not a function\n    at foo\n")
        os_ = X.scan_logs(NOW - dt.timedelta(days=7))
        self.assertEqual(sorted(o["msg"] for o in os_), ["TypeError: x.y is not a function", "run-nightly.sh exited with code 1",
                                                       "run-nightly.sh exited with code 141"])
        v = states(ctx_of(os_))
        sig141 = next(k for k, x in v.items() if "141" in x["c"]["sample"])
        self.assertEqual(v[sig141]["state"], "watch"); self.assertIn("SIGPIPE", v[sig141]["reason"])

    def test_issue_block_and_wontfix(self):
        U = "11111111-2222-3333-4444-555555555555"
        block = {"schema": "mivo-qa-health/v2", "run_id": U, "items": [{"category": "qa_tool_failures", "entry": {"stage": "janitor", "message": "EACCES /tmp/x"}}]}
        iss = [{"number": 20, "state": "OPEN", "title": "[Mivo QA][system_health] 1 problem", "url": "u20", "createdAt": utc(NOW), "comments": [],
                "body": f"Mivo QA (2026-10-23, run {U}) found 1 problem\n- **QA tool failure** [janitor]: EACCES /tmp/x\n```json mivo-qa-health\n" + json.dumps(block) + "\n```"},
               {"number": 21, "state": "CLOSED", "stateReason": "NOT_PLANNED", "title": "[Mivo QA][system_health] 1 problem", "url": "u21",
                "createdAt": utc(NOW), "comments": [], "body": "- **LLM error** [pr-summary]: boom TypeError"}]
        occ_, links, wontfix = X.scan_issues(iss, NOW - dt.timedelta(days=7))
        c = X.aggregate(occ_)
        self.assertEqual([v["count"] for v in c.values()], [1])      # JSON block + body line of the same run: one run
        self.assertEqual(len(wontfix), 1)
        v = states(ctx_of(occ_ + [occ("llm_errors", "pr-summary", "boom TypeError", NOW, "r5")], wontfix=wontfix))
        self.assertTrue(any(x["state"] == "claimed" and "不修" in x["reason"] for x in v.values()))

class TestCheckLevelFaults(unittest.TestCase):
    """10-24 audit: a check failing on the BASE revision in report after report (api-probe 「按画布列出已挂资产」, 13 reports, fixed
    by hand in panoptes #18) was invisible to infrafix. Now one signature per check + test, 3 runs in 7 days, never auto-merged."""
    def rep(self, n, at, run, **fc):
        x = {"check_id": "api-probe", "check_name": "接口测试（真起服务）", "test": "登录：按画布列出已挂资产", "pre_existing": True, "flaky": False,
             "attribution": "also fails on the base revision", "fingerprint": "mq1-6fc99fc3440edd7d2d40"}
        x.update(fc)
        return report(n, at, run, prs=[{"number": n, "outcome": "passed", "failed_checks": [x]}])
    def test_three_runs_on_different_prs_make_one_ticket_flaky_or_pr_caused_never(self):
        occs = []
        for i, n in enumerate((1223, 1231, 1238)):
            occs += X.report_occs(self.rep(n, NOW - dt.timedelta(hours=10 - i), f"r{i}"), f"/r/{n}.json", NOW - dt.timedelta(days=7))
        occs += X.report_occs(self.rep(1240, NOW - dt.timedelta(hours=3), "r9", flaky=True), "/r/1240.json", NOW - dt.timedelta(days=7))
        occs += X.report_occs(self.rep(1241, NOW - dt.timedelta(hours=2), "r10", pre_existing=False), "/r/1241.json", NOW - dt.timedelta(days=7))
        cands = X.aggregate(occs)
        self.assertEqual(len(cands), 1); c = next(iter(cands.values()))
        self.assertEqual((c["cat"], c["stage"], c["count"], c["threshold"], c["prs"]), ("check", "api-probe", 3, 3, [1223, 1231, 1238]))
        self.assertEqual(states(ctx_of(occs))[c["sig8"]]["state"], "todo")
        self.assertEqual(states(ctx_of(occs[:2]))[c["sig8"]]["state"], "watch")                   # 2 runs: watch only

# ====================================================================== 3. rules, holds, claims, priority
class TestRules(unittest.TestCase):
    T1, T2 = NOW - dt.timedelta(hours=30), NOW - dt.timedelta(hours=3)
    def test_llm_output_single_is_watch_two_runs_todo(self):
        m = "AI 补测失败（模型返回的 JSON 不完整，finish_reason=length）"
        one = states(ctx_of([occ("llm_errors", "ai-tests", m, self.T2, "a"), occ("llm_errors", "ai-tests", m, self.T2, "a", source="issue", issue=16)]))
        v = next(iter(one.values())); self.assertEqual((v["state"], v["c"]["count"]), ("watch", 1))
        self.assertIn("--sig", v["reason"])
        two = states(ctx_of([occ("llm_errors", "ai-tests", m, self.T1, "a"), occ("llm_errors", "ai-tests", m, self.T2, "b")]))
        self.assertEqual(next(iter(two.values()))["state"], "todo")

    def test_classes(self):
        cases = {"fetch failed (UND_ERR_SOCKET other side closed) after 5 ms": ("code-review", "watch", "net"),
                 "模型网关返回 401 Authentication Error - Expired Key": ("pr-summary", "watch", "auth"),
                 "模型网关返回 400 litellm.BadRequestError: Unknown parameter": ("pr-summary", "todo", "deterministic"),
                 'AI 补测失败（The value of "delay" is out of range. It must be an integer. Received 299999.5）': ("ai-tests", "todo", "deterministic")}
        for m, (stage, want, klass) in cases.items():
            v = next(iter(states(ctx_of([occ("llm_errors", stage, m, self.T2, "a")])).values()))
            self.assertEqual((v["state"], v["c"]["klass"]), (want, klass), m)
        v = next(iter(states(ctx_of([occ("qa_tool_failures", "janitor", "EACCES", self.T2, "a")])).values()))
        self.assertEqual(v["state"], "todo")
        v = next(iter(states(ctx_of([occ("qa_tool_failures", "catch-up", "catch-up gave up after 3 attempts (last: lock-timeout)", self.T2, "a", pr=3)])).values()))
        self.assertEqual(v["state"], "watch")
        v = next(iter(states(ctx_of([occ("env_problems", "env", "low disk: 9 GB free", self.T2, "a")])).values()))
        self.assertEqual(v["state"], "watch")

    def test_quiet_rule(self):
        o = [occ("qa_tool_failures", "janitor", "EACCES", self.T1, "a")]
        later = [self.T1 + dt.timedelta(minutes=10 * i) for i in range(1, 9)]
        self.assertEqual(next(iter(states(ctx_of(o, runs=later[:7])).values()))["state"], "todo")
        v = next(iter(states(ctx_of(o, runs=later)).values()))
        self.assertEqual(v["state"], "watch"); self.assertIn("8 次", v["reason"])
        o2 = [occ("qa_tool_failures", "janitor", "EACCES", self.T2, "a")]          # recent (< 24 h): never quiet
        self.assertEqual(next(iter(states(ctx_of(o2, runs=[self.T2 + dt.timedelta(minutes=i) for i in range(1, 20)])).values()))["state"], "todo")

    def test_hold(self):
        o = [occ("field", "code-review", X.CR_MSG, self.T2, "a")]
        v = states(ctx_of(o, holds=dict(X.DEFAULT_HOLD)))[X.CR_SIG[5:13]]
        self.assertEqual(v["state"], "held"); self.assertIn("待 Praise 决定", v["reason"]); self.assertIn("7a960e8", v["reason"])
        s8 = o[0]["sig"][5:13]
        self.assertEqual(states(ctx_of(o, holds={s8: "等等"}))[s8]["state"], "held")       # sig8 key works too
        vs = X.evaluate_all(ctx_of([], holds=dict(X.DEFAULT_HOLD)), NOW)                  # hold without occurrence still listed
        self.assertEqual([(v["key"], v["state"]) for v in vs], [(X.CR_SIG, "held")])
        A = argparse.Namespace(sig=s8)
        v_, err = X.pick_sig(A, ctx_of(o, holds=dict(X.DEFAULT_HOLD)), X.evaluate_all(ctx_of(o, holds=dict(X.DEFAULT_HOLD)), NOW))
        self.assertIsNone(v_); self.assertEqual(err["status"], "held")

    def test_claims_and_rounds(self):
        m = "EACCES"; o = [occ("qa_tool_failures", "janitor", m, self.T2, "a")]; sig = o[0]["sig"]; s8 = sig[5:13]
        body = f"x\n{X.MARK} {sig}\n"
        self.assertEqual(states(ctx_of(o, prs=[{"number": 30, "state": "OPEN", "body": body}]))[s8]["state"], "claimed")
        self.assertEqual(states(ctx_of(o, prs=[{"number": 30, "state": "CLOSED", "body": body}]))[s8]["state"], "todo")
        self.assertEqual(states(ctx_of(o, prs=None))[s8]["state"], "skip")                  # PR list unread: never risk a double PR
        self.assertEqual(states(ctx_of(o, notes=[f"infra-{s8}-norepro.md"]))[s8]["state"], "claimed")
        # merged + deployed before the last occurrence -> recurrence -> round 2
        tk = {sig: {"status": "deployed", "deployed_at": (self.T2 - dt.timedelta(hours=1)).isoformat(), "round": 1, "pr": 30}}
        v = states(ctx_of(o, prs=[{"number": 30, "state": "MERGED", "body": body}], tickets=tk))[s8]
        self.assertEqual((v["state"], v["round"]), ("todo", 2))
        tk = {sig: {"status": "deployed", "deployed_at": (self.T2 + dt.timedelta(hours=1)).isoformat(), "round": 1, "pr": 30}}
        self.assertEqual(states(ctx_of(o, prs=[{"number": 30, "state": "MERGED", "body": body}], tickets=tk))[s8]["state"], "claimed")
        # two rounds used and it recurred again -> hard stop / needs_human
        tk = {sig: {"status": "recurred", "deployed_at": (self.T2 - dt.timedelta(hours=1)).isoformat(), "round": 2, "pr": 31}}
        prs = [{"number": 30, "state": "MERGED", "body": body}, {"number": 31, "state": "MERGED", "body": body.replace(sig, sig + " r2")}]
        v = states(ctx_of(o, prs=prs, tickets=tk))[s8]
        self.assertEqual(v["state"], "skip"); self.assertTrue(v["hard_stop"])

    def test_priority(self):
        o = [occ("llm_errors", "ai-tests", "TypeError: a", self.T2, "a"), occ("qa_tool_failures", "janitor", "EACCES", self.T1, "b"),
             occ("run", "run", "run incomplete", self.T1, "c"), occ("run", "run", "run incomplete", self.T2, "d")]
        order = [v["c"]["klass"] for v in X.evaluate_all(ctx_of(o), NOW) if v["state"] == "todo"]
        self.assertEqual(order, ["qa_tool", "deterministic", "run"])

# ====================================================================== 4. tickets: back-off
class TestBackoff(unittest.TestCase):
    def test_transient_backoff_then_needs_human(self):
        tk = X.new_ticket({"sig": "inf1-" + "a" * 20, "sig8": "a" * 8, "cat": "c", "stage": "s", "sample": "t"})
        mins = []
        for i in range(3):
            tk, n = X.apply_outcome(tk, {"status": "failed", "kind": "transient", "error": "npm"}, NOW)
            mins.append(round((X.ts(tk["next_retry_at"]) - NOW).total_seconds() / 60)); self.assertIsNone(n)
        self.assertEqual(mins, [15, 60, 240])
        tk, n = X.apply_outcome(tk, {"status": "failed", "kind": "transient", "error": "npm"}, NOW)
        self.assertEqual((tk["status"], n), ("needs_human", "needs_human"))
        tk2, n2 = X.apply_outcome(X.new_ticket({"sig": "x", "sig8": "x", "cat": "c", "stage": "s"}), {"status": "failed", "kind": "deterministic", "error": "red"}, NOW)
        self.assertEqual((tk2["status"], n2), ("needs_human", "needs_human"))

    def test_backoff_skips_until_due(self):
        o = [occ("qa_tool_failures", "janitor", "EACCES", NOW - dt.timedelta(hours=1), "a")]; sig = o[0]["sig"]
        tk = {sig: {"status": "failed", "next_retry_at": (NOW + dt.timedelta(minutes=10)).isoformat(), "transient_failures": 1, "last_error": "npm"}}
        self.assertEqual(states(ctx_of(o, tickets=tk))[sig[5:13]]["state"], "skip")
        tk[sig]["next_retry_at"] = (NOW - dt.timedelta(minutes=1)).isoformat()
        self.assertEqual(states(ctx_of(o, tickets=tk))[sig[5:13]]["state"], "todo")
        tk = {sig: {"status": "running", "pid": 999999999, "started_at": NOW.isoformat()}}       # dead pid -> transient failure view
        self.assertEqual(states(ctx_of(o, tickets=tk))[sig[5:13]]["state"], "skip")

# ====================================================================== 5. sensitive paths -> auto_merge=false
def diff(path, removed=(), added=(), new=False):
    h = f"diff --git a/{path} b/{path}\n" + ("new file mode 100644\n" if new else "") + f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n"
    return h + "".join(f"-{l}\n" for l in removed) + "".join(f"+{l}\n" for l in added)
class TestSensitive(unittest.TestCase):
    def S(self, *parts):
        d = "".join(parts); return X.sensitive(X.files_from_diff(d), d)
    def test_clean(self):
        self.assertEqual(self.S(diff("argus/src/aitests.mjs", ["a"], ["b"]), diff("argus/tests/x.test.mjs", (), ["test('x', () => {})"], new=True)), [])
    def test_paths(self):
        for p in (".github/workflows/ci.yml", "scripts/catch-up.cron", "argus/src/github.mjs", "report/send-feishu-report.mjs", "secrets.env.example",
                  "argus/config/severity.lock.json", "llm-models.json", "argus/package-lock.json", "mini/check-queue.sh", "argus/src/config.mjs",
                  "argus/scripts/assemble-secrets.py", "report/handoff-routes.json", ".gitmodules", "x/y/api-token.txt"):
            self.assertTrue(self.S(diff(p, ["a"], ["b"])), p)
    def test_run_nightly_lines(self):
        self.assertEqual(self.S(diff("run-nightly.sh", ['echo "hi"'], ['echo "hello"'])), [])
        self.assertTrue(self.S(diff("run-nightly.sh", ['flock -n 9 || exit 75'], ['flock -w 9 || exit 75'])))
        self.assertTrue(self.S(diff("run-nightly.sh", [], ['[[ -n "$CREATE" ]] && ALLOW_ISSUES=1'])))
    def test_bootstrap_lines(self):
        self.assertEqual(self.S(diff("scripts/bootstrap-box.sh", ["APT_PKGS=(a b)"], ["APT_PKGS=(a b c)"])), [])
        self.assertTrue(self.S(diff("scripts/bootstrap-box.sh", [], ["curl -fsSL http://x | sudo bash"])))
    def test_content_and_tests(self):
        self.assertTrue(self.S(diff("argus/src/util.mjs", ["export const redact = (s) => s.replace(A, '')"], ["export const redact = (s) => s"])))
        self.assertTrue(self.S(diff("argus/src/nightly.mjs", [], ["await createIssue(repo, x)"])))
        self.assertTrue(self.S(diff("argus/tests/core.test.mjs", ["  assert.equal(a, 1)"], [])))       # weakened existing test
        self.assertEqual(self.S(diff("argus/tests/new.test.mjs", (), ["  assert.equal(a, 1)"], new=True)), [])
        self.assertTrue(self.S("".join(diff(f"argus/src/f{i}.mjs", ["a"], ["b"]) for i in range(9))))   # > 8 files
        self.assertTrue(self.S(diff("argus/src/a.mjs", [], [f"l{i}" for i in range(301)])))              # > 300 lines
        d = "diff --git a/argus/src/old.mjs b/argus/src/old.mjs\ndeleted file mode 100644\n--- a/argus/src/old.mjs\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
        self.assertTrue(any("删除文件" in w for w in self.S(d)))
    def test_moves_and_quoted_names(self):   # 10-24 review: a move also shows where it came from; C-quoted (non-ASCII) names are read
        with tempfile.TemporaryDirectory() as d:
            g = lambda *a: sh(["git", "-C", d, "-c", "core.quotepath=true", "-c", "user.name=t", "-c", "user.email=t@t", *a])
            g("init", "-q", "-b", "main")
            for p, t in {".github/workflows/ci.yml": "name: CI\non: push\n", "argus/src/a b.mjs": "export const a = 1\n",
                         "argus/src/中文.mjs": "export const b = 1\n", "argus/tests/keep.test.mjs": "test('k', () => assert.ok(1))\n"}.items():
                wf(f"{d}/{p}", t)
            g("add", "-A"); g("commit", "-q", "-m", "base")
            g("mv", ".github/workflows/ci.yml", "ci.yml"); g("mv", "argus/src/a b.mjs", "argus/src/c d.mjs"); g("mv", "argus/tests/keep.test.mjs", "argus/keep.mjs")
            wf(f"{d}/argus/src/中文.mjs", "const token = process.env.X\n", "a"); g("add", "-A")
            gh_like = g("diff", "--cached", "-M", "HEAD")          # like `gh pr diff`: renames detected, non-ASCII names quoted
            self.assertIn('"a/argus/src/\\344', gh_like)
            fs = X.files_from_diff(gh_like)
            for want in [("R", "ci.yml"), ("D", ".github/workflows/ci.yml"), ("R", "argus/src/c d.mjs"), ("D", "argus/src/a b.mjs"),
                         ("R", "argus/keep.mjs"), ("D", "argus/tests/keep.test.mjs"), ("M", "argus/src/中文.mjs")]:
                self.assertIn(want, fs)
            why = X.sensitive(fs, gh_like)
            for want in ("敏感路径 .github/workflows/ci.yml", "删除测试 argus/tests/keep.test.mjs", "argus/src/中文.mjs 改到密钥"):
                self.assertTrue(any(want in w for w in why), (want, why))
            plain = g("diff", "--cached", "--no-renames", "HEAD")  # what infrafix itself reads before committing
            self.assertEqual(sorted(X.files_from_diff(plain)), sorted([("A", "ci.yml"), ("D", ".github/workflows/ci.yml"), ("A", "argus/src/c d.mjs"),
                ("D", "argus/src/a b.mjs"), ("A", "argus/keep.mjs"), ("D", "argus/tests/keep.test.mjs"), ("M", "argus/src/中文.mjs")]))
            self.assertTrue(any("中文.mjs 改到密钥" in w for w in X.sensitive(X.files_from_diff(plain), plain)))
        cp = "diff --git a/scripts/catch-up.cron b/x.cron\nsimilarity index 100%\ncopy from scripts/catch-up.cron\ncopy to x.cron\n"
        self.assertEqual(X.files_from_diff(cp), [("C", "x.cron"), ("C", "scripts/catch-up.cron")])
        self.assertTrue(any("scripts/catch-up.cron" in w for w in self.S(cp)))
        bad = "diff --git x y\n@@ -1 +1 @@\n-a\n+b\n"                     # a header it cannot read is never let through
        self.assertEqual(X.files_from_diff(bad)[0][0], "?"); self.assertTrue(any("读不出" in w for w in self.S(bad)))

# ====================================================================== 6. gates file
class TestGates(Tmp):
    def test_missing_default_and_broken(self):
        holds, warn, am = X.load_gates(); self.assertIn(X.CR_SIG, holds); self.assertFalse(am)    # no file: built-in hold, no auto-merge
        wf(X.GATES, json.dumps({"no_fix_pr": {}, "infra_hold": {"_doc": "x", "abcd1234": "r"}, "infra_automerge": False}))
        holds, warn, am = X.load_gates(); self.assertEqual(holds, {"abcd1234": "r"}); self.assertFalse(am)
        wf(X.GATES, json.dumps({"infra_hold": {}})); self.assertTrue(X.load_gates()[2])
        wf(X.GATES, "{broken")
        with self.assertRaises(X.GateFileError): X.load_gates()
        os.environ["INFRA_GATES"] = X.GATES; self.assertFalse(O.automerge_allowed())               # outbound side fails closed too
        os.remove(X.GATES); self.assertFalse(O.automerge_allowed())                                 # ... also when the file is missing

# ====================================================================== 7. worktree flow with a fake panoptes repo + fake model
PKG = '{"name":"argus","version":"0.0.0","private":true,"type":"module","scripts":{"test":"bash run-tests.sh"}}'
LOCK = '{"name":"argus","version":"0.0.0","lockfileVersion":3,"requires":true,"packages":{"":{"name":"argus","version":"0.0.0"}}}'
RUNTESTS = 'set -uo pipefail\ncd "$(dirname "$0")"\nif (( $# )); then set -- "$@"; else set -- tests/*.test.mjs; fi\nnode --test "$@"\n'
class GitTmp(Tmp):
    def setUp(self):
        super().setUp()
        origin = f"{self.tmp}/origin.git"; seed = f"{self.tmp}/seed"
        sh(["git", "init", "-q", "--bare", "-b", "main", origin])
        sh(["git", "init", "-q", "-b", "main", seed])
        wf(f"{seed}/argus/package.json", PKG); wf(f"{seed}/argus/package-lock.json", LOCK); wf(f"{seed}/argus/run-tests.sh", RUNTESTS)
        wf(f"{seed}/argus/src/clamp.mjs", "export const delay = (ms) => ms * 1.5\n")
        wf(f"{seed}/argus/tests/old.test.mjs", "import { test } from 'node:test'\nimport assert from 'node:assert/strict'\ntest('old', () => assert.ok(true))\n")
        wf(f"{seed}/.gitignore", ".worktrees/\nnode_modules/\n")
        sh("git add -A && git -c user.name=t -c user.email=t@t commit -q -m init && git push -q " + origin + " main", cwd=seed)
        sh(["git", "clone", "-q", origin, X.PANOPTES])
        self.origin = origin
        self.c = {"sig": "inf1-" + "ab12cd34" + "0" * 12, "sig8": "ab12cd34", "cat": "llm_errors", "stage": "ai-tests", "norm": "delay out of range",
                  "count": 1, "first_seen": NOW, "last_seen": NOW, "sample": 'The value of "delay" is out of range', "evidence": ["/r/x.json"],
                  "issues": {16}, "prs": [1], "occs": [], "klass": "deterministic", "files": ["argus/src/clamp.mjs:1"], "round": 1}
    def fake_pi(self, script):
        """script: {step: callable(wd) -> reply text}"""
        calls = []
        def pi(tier, cwd, prompt, session, timeout=600, write=False, web=False):
            step = session.rsplit("-", 1)[1]; calls.append((tier, step, write))
            return script[step](cwd), f"task-{step}"
        X.pi = pi; return calls
    def A(self, **kw): return argparse.Namespace(**{"dry_run": False, "base": None, "sig": None, **kw})
    def wt(self): return f"{X.WT_ROOT}/pi-infra-{self.c['sig8']}"
    def branch(self): return subprocess.run(["git", "-C", X.PANOPTES, "rev-parse", "--verify", "-q", f"refs/heads/fix/infra-{self.c['sig8']}"], capture_output=True).returncode == 0

TEST_RED = ("import { test } from 'node:test'\nimport assert from 'node:assert/strict'\nimport { delay } from '../src/clamp.mjs'\n"
            "test('delay is an integer', () => assert.ok(Number.isInteger(delay(199999))))\n")
def write_test(wd): wf(f"{wd}/argus/tests/delay.test.mjs", TEST_RED); return "ROOT CAUSE argus/src/clamp.mjs:1\nTEST FILE argus/tests/delay.test.mjs"
def write_fix(wd): wf(f"{wd}/argus/src/clamp.mjs", "export const delay = (ms) => Math.round(ms * 1.5)\n"); return "ROOT CAUSE: argus/src/clamp.mjs:1 float delay\nexplanation"

class TestFixOne(GitTmp):
    def test_success_red_green_revert_commit_action(self):
        calls = self.fake_pi({"repro": write_test, "fix1": write_fix})
        out, res = X.fix_one(dict(self.c), self.A(), "2026-10-23")
        self.assertEqual(out["status"], "pr_action_written", (out.get("error"), res["steps"]))
        self.assertTrue(out["auto_merge"], res["auto_merge_why"])
        self.assertEqual([(t, s, w) for t, s, w in calls], [("deep", "repro", True), ("deep", "fix1", True)])
        names = [k for k, ok, _ in res["steps"]]; oks = [ok for k, ok, _ in res["steps"]]
        self.assertTrue(all(oks), names)
        self.assertTrue(any("撤掉修复" in k for k in names) and any("恢复修复" in k for k in names) and any("本地 CI" in k for k in names))
        self.assertFalse(os.path.exists(self.wt()))                      # worktree gone ...
        self.assertTrue(self.branch())                                   # ... branch kept until merged
        a = self.actions[-1]
        self.assertEqual((a["type"], a["key"], a["repo"]), ("gh.infra.pr", f"infrapr:{self.c['sig']}", "PraiseZhu/panoptes"))
        self.assertIn(f"Infra-Sig: {self.c['sig']}", rf(a["body_file"]))
        p = rf(a["patch"]); self.assertIn("From: Praise <zhuzan@xd.com>", p); self.assertIn("Infra-Sig:", p)
        self.assertEqual(sorted(a["files"]), ["argus/src/clamp.mjs", "argus/tests/delay.test.mjs"])
        log = sh(["git", "-C", X.PANOPTES, "log", "-1", "--format=%an <%ae>|%B", f"fix/infra-{self.c['sig8']}"])
        self.assertTrue(log.startswith("Praise <zhuzan@xd.com>|fix(ai-tests):"))
        self.assertEqual(sh(["git", "-C", X.PANOPTES, "status", "--porcelain"]).strip(), "")     # the main checkout untouched

    def test_patch_applies_with_git_am_in_outbound(self):
        self.fake_pi({"repro": write_test, "fix1": write_fix})
        out, res = X.fix_one(dict(self.c), self.A(), "2026-10-23"); a = self.actions[-1]
        self.fakebin("gh", f'if [ "$1 $2" = "repo clone" ]; then git clone -q "{self.origin}" "$4"; exit $?; fi\necho "unexpected gh $*" >&2; exit 9\n')
        created = []
        def gh(args, js=True):
            if args[:2] == ["pr", "list"]: return [{"number": 1, "state": "OPEN", "body": f"{X.MARK} {self.c['sig']} r2", "url": "u"}]   # round-2 PR != round 1
            if args[:2] == ["repo", "view"]: return {"viewerPermission": "ADMIN", "defaultBranchRef": {"name": "main"}}
            if args[:2] == ["pr", "create"]: created.append(args); return "https://github.com/PraiseZhu/panoptes/pull/40"
            raise AssertionError(args)
        who, state, validate, do = O.handler(a, gh, lambda t, k: None)
        self.assertEqual(state(), "pending")
        r = do(); self.assertIn("pull/40", r); self.assertIn("pushed", r)
        self.assertNotIn("--draft", created[0])
        self.assertIn(f"Infra-Sig: {self.c['sig']}", created[0][created[0].index("--body") + 1])
        msg = sh(["git", "--git-dir", self.origin, "log", "-1", "--format=%an|%cn|%s", a["branch"]])
        self.assertTrue(msg.startswith("Praise|Praise|fix(ai-tests)"))
        r2 = do(); self.assertIn("remote branch reused", r2)              # interrupted after push: reuse our own branch
        sh(f"git --git-dir {self.origin} branch -f {a['branch']} main")   # someone else's branch with that name -> refuse
        with self.assertRaises(RuntimeError): do()

    def test_norepro_cleans_up(self):
        self.fake_pi({"repro": lambda wd: "NOREPRO\nthis is a gateway outage (argus/src/ai.mjs:10)"})
        out, res = X.fix_one(dict(self.c), self.A(), "2026-10-23")
        self.assertEqual(out["status"], "norepro")
        self.assertFalse(os.path.exists(self.wt())); self.assertFalse(self.branch())
        self.assertTrue(os.path.exists(res["evidence"]))

    def test_test_green_on_base_is_norepro(self):
        def green(wd):
            wf(f"{wd}/argus/tests/g.test.mjs", "import { test } from 'node:test'\ntest('fine', () => {})\n"); return "x"
        self.fake_pi({"repro": green})
        out, _ = X.fix_one(dict(self.c), self.A(), "2026-10-23")
        self.assertEqual(out["status"], "norepro"); self.assertIn("当前代码上就通过", out["error"])

    def test_model_failure_is_transient_and_cleans_up(self):
        def boom(wd): raise RuntimeError("pi rc=1")
        self.fake_pi({"repro": boom})
        out, _ = X.fix_one(dict(self.c), self.A(), "2026-10-23")
        self.assertEqual((out["status"], out["kind"]), ("failed", "transient"))
        self.assertFalse(os.path.exists(self.wt())); self.assertFalse(self.branch())
        self.assertEqual(sh(["git", "-C", X.PANOPTES, "worktree", "list"]).count("\n"), 1)

    def test_fix_not_covering_test_is_deterministic(self):
        def fix_and_cheat(wd):   # changes the source but the "fix" is not what makes the test pass: it edits the test (restored)
            wf(f"{wd}/argus/src/other.mjs", "export const x = 1\n"); wf(f"{wd}/argus/tests/delay.test.mjs", "import { test } from 'node:test'\ntest('x', () => {})\n")
            return "ROOT CAUSE x"
        self.fake_pi({"repro": write_test, "fix1": fix_and_cheat, "fix2": fix_and_cheat})
        out, _ = X.fix_one(dict(self.c), self.A(), "2026-10-23")
        self.assertEqual((out["status"], out["kind"]), ("failed", "deterministic"))
        self.assertFalse(os.path.exists(self.wt())); self.assertFalse(self.branch())

    def test_sensitive_fix_sets_auto_merge_false(self):
        def fix_ci(wd): write_fix(wd); wf(f"{wd}/.github/workflows/ci.yml", "name: CI\n"); return "ROOT CAUSE x"
        self.fake_pi({"repro": write_test, "fix1": fix_ci})
        out, res = X.fix_one(dict(self.c), self.A(), "2026-10-23")
        self.assertEqual(out["status"], "pr_action_written")
        self.assertFalse(out["auto_merge"]); self.assertTrue(any(".github" in w for w in res["auto_merge_why"]))

    def test_a_git_mv_by_the_model_shows_both_paths(self):   # 10-24 review: a staged move used to show only its new name
        def fix_mv(wd): write_fix(wd); sh(["git", "-C", wd, "mv", "argus/tests/old.test.mjs", "argus/old.mjs"]); return "ROOT CAUSE x"
        self.fake_pi({"repro": write_test, "fix1": fix_mv})
        out, res = X.fix_one(dict(self.c), self.A(), "2026-10-23")
        self.assertEqual(out["status"], "pr_action_written", (out.get("error"), res["steps"]))
        self.assertFalse(out["auto_merge"]); self.assertTrue(any("删除测试 argus/tests/old.test.mjs" in w for w in res["auto_merge_why"]), res["auto_merge_why"])

    def test_a_fix_touching_pi_is_dropped(self):   # 10-24: the Pi line lives in panoptes pi/ -- the bot never changes itself
        def fix_self(wd): write_fix(wd); wf(f"{wd}/pi/bin/prfix.py", "x = 1\n"); return "ROOT CAUSE x"
        self.fake_pi({"repro": write_test, "fix1": fix_self})
        out, res = X.fix_one(dict(self.c), self.A(), "2026-10-23")
        self.assertEqual((out["status"], out["kind"]), ("failed", "deterministic")); self.assertIn("pi/", out["error"])
        self.assertFalse([a for a in self.actions if a.get("type") == "gh.infra.pr"]); self.assertFalse(os.path.exists(self.wt()))
    def test_a_fix_moving_a_pi_file_out_is_dropped(self):   # 10-24 review: a move out of pi/ used to show only where it went
        d = f"{self.tmp}/seed2"; sh(["git", "clone", "-q", self.origin, d]); wf(f"{d}/pi/bin/keep.py", "x = 1\n")
        sh(f"git add -A && git -c user.name=t -c user.email=t@t commit -q -m pi && git push -q origin main", cwd=d)
        def fix_mv(wd): write_fix(wd); sh(["git", "-C", wd, "mv", "pi/bin/keep.py", "argus/keep.py"]); return "ROOT CAUSE x"
        self.fake_pi({"repro": write_test, "fix1": fix_mv})
        out, res = X.fix_one(dict(self.c), self.A(), "2026-10-23")
        self.assertEqual((out["status"], out["kind"]), ("failed", "deterministic"), out); self.assertIn("pi/bin/keep.py", out["error"])
        self.assertFalse([a for a in self.actions if a.get("type") == "gh.infra.pr"])
        mv = "diff --git a/pi/bin/keep.py b/argus/keep.py\nsimilarity index 100%\nrename from pi/bin/keep.py\nrename to argus/keep.py\n"
        self.assertTrue(any("敏感路径 pi/bin/keep.py" in w for w in X.sensitive(X.files_from_diff(mv), mv)))   # outbound's re-check

    def test_dry_run_leaves_nothing(self):
        self.fake_pi({"repro": write_test, "fix1": write_fix})
        out, res = X.fix_one(dict(self.c), self.A(dry_run=True), "2026-10-23")
        self.assertEqual(out["status"], "dry_ok")
        self.assertEqual(self.actions, []); self.assertFalse(self.branch())
        self.assertFalse(os.path.exists(self.wt() + "-dry"))

# ====================================================================== 8. run_ticket bookkeeping (fix_one faked)
class TestRunTicket(Tmp):
    def setUp(self):
        super().setUp(); self.saved_fix = X.fix_one
    def tearDown(self):
        X.fix_one = self.saved_fix; super().tearDown()
    def ctx(self):
        o = [occ("qa_tool_failures", "janitor", "EACCES /tmp/x", NOW - dt.timedelta(hours=1), "a")]
        c = ctx_of(o); c["tickets"] = X.load_tickets().get("tickets", {}); return c
    def go(self, outcome):
        X.fix_one = lambda c, A, d: (outcome, {"evidence": f"{X.OUT}/ev.md", "model": [{"text": "NOREPRO\nwhy"}], "norepro": ("why", "NOREPRO\nwhy"), "base": "a" * 40})
        ctx = self.ctx(); vs = X.evaluate_all(ctx, NOW)
        A = argparse.Namespace(dry_run=False, base=None, sig=None)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf): rc = X.run_ticket(A, ctx, vs)
        return rc, json.loads(buf.getvalue().strip().splitlines()[-1][len("RESULT "):]), X.load_tickets()["tickets"]
    def test_success_and_norepro_and_needs_human(self):
        rc, r, T = self.go({"status": "pr_action_written", "kind": None, "error": None, "action_key": "infrapr:x", "auto_merge": True})
        self.assertEqual((rc, r["status"], r["action_key"]), (0, "pr_action_written", "infrapr:x"))
        self.assertEqual(json.loads(rf(f"{X.OUT}/last-result.json"))["action_key"], "infrapr:x")
        sig = r["sig"]; self.assertEqual(T[sig]["status"], "pr_action_written")
        T.pop(sig); wf(f"{X.OUT}/tickets.json", json.dumps({"tickets": T}))
        rc, r, T = self.go({"status": "norepro", "kind": "norepro", "error": "不是缺陷", "action_key": None})
        self.assertEqual((rc, T[sig]["status"]), (0, "norepro"))
        self.assertTrue(os.path.exists(f"{X.NOTES}/infra-{sig[5:13]}-norepro.md"))
        self.assertEqual(self.actions[-1]["type"], "slack.dm"); self.assertIn("note", self.actions[-1]["text"])
        os.remove(f"{X.NOTES}/infra-{sig[5:13]}-norepro.md")
        with contextlib.redirect_stdout(io.StringIO()): X.main(["--reset", sig[5:13]])
        rc, r, T = self.go({"status": "failed", "kind": "deterministic", "error": "本地 CI 没过", "action_key": None})
        self.assertEqual((rc, T[sig]["status"]), (1, "needs_human")); self.assertIn("修不动了", self.actions[-1]["text"])   # 10-24 praise_notice wording

    def test_busy_when_memory_low_or_locked(self):
        os.environ["INFRA_TEST_MEM_GB"] = "1"
        rc, r, _ = self.go({"status": "pr_action_written"})
        self.assertEqual((rc, r["status"]), (75, "busy"))
        os.environ["INFRA_TEST_MEM_GB"] = "8"
        lk = X.try_lock(f"{X.OUT}/infrafix.run.lock")       # another infrafix holds the run lock
        try:
            rc, r, _ = self.go({"status": "pr_action_written"})
            self.assertEqual((rc, r["status"]), (75, "busy")); self.assertIn("另一个", r["reason"])
        finally: X.unlock(lk)

    def test_off_when_not_pi(self):
        os.environ["PI_FLEET_MODE"] = "grok"
        rc, r, _ = self.go({"status": "pr_action_written"})
        self.assertEqual((rc, r["status"]), (0, "off"))

# ====================================================================== 9. outbound: merge preconditions + issue idempotency
def pr_view(**kw):
    d = {"state": "OPEN", "headRefOid": "h" * 40, "body": "x\nInfra-Sig: inf1-" + "a" * 20, "mergeable": "MERGEABLE", "files": [], "url": "https://x/pull/7"}
    d.update(kw); return d
class FakeGh:
    def __init__(self, view, runs, diff_text=None, merged_today=0):
        self.view, self.runs, self.diff, self.calls, self.merged_today = view, runs, diff_text or diff("argus/src/a.mjs", ["a"], ["b"]), [], merged_today
    def __call__(self, args, js=True):
        self.calls.append(args)
        if args[:2] == ["pr", "view"]: return self.view
        if args[:2] == ["run", "list"]: return self.runs
        if args[:2] == ["pr", "diff"]: return self.diff
        if args[:2] == ["pr", "list"]: return [{"number": i, "body": "Infra-Sig: inf1-x"} for i in range(self.merged_today)]
        if args[:2] == ["pr", "merge"]: return "merged"
        raise AssertionError(args)
GREEN = [{"name": "CI", "status": "completed", "conclusion": "success"}]
class TestBacklogGuard(Tmp):
    """10-30: one sandbox fault failed 30 test cases -> 30 signatures -> 17 conflicting open PRs, nobody merging."""
    D = "scripts/ops/review-capacity-check"
    def occs(self, *names, run="r", d=None):
        return [occ("check", "unit", f"单元测试「{d or self.D}/{n}.test.mjs > case」在基线版本上也失败", NOW - dt.timedelta(hours=1), f"{run}{i}")
                for n in names for i in range(3)]
    def ctx(self, occs, **kw): return ctx_of(occs, **kw)
    def todo(self, ctx): return [v["c"]["sig8"] for v in X.evaluate_all(ctx, NOW) if v["state"] == "todo"]
    def sigs(self, ctx): return {c["sample"].split("/")[-1].split(".")[0]: s for s, c in ctx["cands"].items()}
    def test_one_family_opens_one_ticket(self):
        ctx = self.ctx(self.occs("launcher", "batch", "isolation"))
        self.assertEqual(len(ctx["cands"]), 3)            # the signatures stay per test case
        self.assertEqual(len(self.todo(ctx)), 1)
    def test_other_directory_is_another_family(self):
        ctx = self.ctx(self.occs("a") + self.occs("b", d="scripts/ops/other"))
        self.assertEqual(len(self.todo(ctx)), 2)
    def test_no_directory_stands_alone(self):
        o = [occ("check", "api-probe", f"探针「{n}」在基线版本上也失败", NOW - dt.timedelta(hours=1), f"r{i}") for n in ("甲", "乙") for i in range(3)]
        self.assertEqual(len(self.todo(self.ctx(o))), 2)
    def test_sibling_in_flight_waits(self):
        ctx = self.ctx(self.occs("a", "b")); s = self.sigs(ctx)
        for st in ("pr_open", "awaiting_praise", "merged"):
            ctx["tickets"] = {s["a"]: {"status": st, "pr": 7}}
            ctx["prs"] = [{"number": 7, "state": "OPEN" if st != "merged" else "MERGED", "body": f"{X.MARK} {s['a']}"}]   # a is claimed, b asks
            vs = {v["key"]: v for v in X.evaluate_all(ctx, NOW)}
            self.assertEqual(vs[s["b"]]["state"], "skip", st); self.assertIn(s["a"][5:13], vs[s["b"]]["reason"])
    def test_sibling_stopped_for_a_person_waits_until_the_window_ends(self):
        ctx = self.ctx(self.occs("a", "b")); s = self.sigs(ctx)
        ctx["tickets"] = {s["a"]: {"status": "needs_human", "updated_at": (NOW - dt.timedelta(days=1)).isoformat()}}
        self.assertEqual({v["key"]: v["state"] for v in X.evaluate_all(ctx, NOW)}[s["b"]], "skip")
        ctx["tickets"] = {s["a"]: {"status": "needs_human", "updated_at": (NOW - dt.timedelta(days=8)).isoformat()}}
        self.assertEqual({v["key"]: v["state"] for v in X.evaluate_all(ctx, NOW)}[s["b"]], "todo")
    def test_rejected_or_verified_sibling_does_not_block(self):
        ctx = self.ctx(self.occs("a", "b")); s = self.sigs(ctx)
        for st in ("rejected", "verified"):
            ctx["tickets"] = {s["a"]: {"status": st}}
            self.assertEqual({v["key"]: v["state"] for v in X.evaluate_all(ctx, NOW)}[s["b"]], "todo", st)
    def test_two_due_failed_siblings_do_not_deadlock(self):
        ctx = self.ctx(self.occs("a", "b")); s = self.sigs(ctx)
        due = {"status": "failed", "next_retry_at": (NOW - dt.timedelta(minutes=1)).isoformat()}
        ctx["tickets"] = {s["a"]: dict(due), s["b"]: dict(due)}
        self.assertEqual(len(self.todo(ctx)), 1)
        ctx["tickets"] = {s["a"]: {"status": "queued"}, s["b"]: {"status": "queued"}}
        self.assertEqual(len(self.todo(ctx)), 1)
        ctx["tickets"] = {s["a"]: {**due, "next_retry_at": (NOW + dt.timedelta(minutes=9)).isoformat()}}
        self.assertEqual(self.todo(ctx), [])
    def test_open_pr_cap_stops_every_signature(self):
        o = [occ("qa_tool_failures", "janitor", f"EACCES {n}", NOW - dt.timedelta(hours=1), "r") for n in ("a", "b")]
        pr = lambda i, st="OPEN": {"number": i, "state": st, "body": f"{X.MARK} inf1-{'0' * 19}{i}"}
        at_cap = [pr(i) for i in range(X.INFRA_OPEN_PR_CAP)]
        self.assertEqual(self.todo(self.ctx(o, prs=at_cap)), [])
        self.assertEqual(len(self.todo(self.ctx(o, prs=at_cap[1:] + [pr(9, "MERGED"), {"number": 8, "state": "OPEN", "body": "by a person"}]))), 2)
    def test_long_failure_keeps_the_node_summary(self):
        out = "ℹ tests 216\nℹ pass 215\nℹ fail 1\n" + "\n".join(f"detail {i}" for i in range(200))
        self.assertEqual(X.node_verdict(1, X.with_summary(out)), "fail")
        self.assertEqual(X.with_summary("a\nb"), "a\nb")
    def test_the_model_is_told_its_sandbox_hides_root(self):
        c = X.aggregate(self.occs("a"))
        self.assertIn("65534", X.context_text(next(iter(c.values()))))

class TestOutboundMerge(Tmp):
    def act(self, **kw):
        a = {"key": "inframerge:inf1-" + "a" * 20 + ":7:hhhh", "type": "gh.infra.merge", "repo": "PraiseZhu/panoptes", "sig": "inf1-" + "a" * 20,
             "pr": 7, "head": "h" * 40, "auto_merge": True}
        a.update(kw); return a
    def run_do(self, a, g):
        dms = []
        who, state, validate, do = O.handler(a, g, lambda t, k: dms.append((k, t)), sensitive=(X.files_from_diff, X.sensitive), now=NOW)
        return state(), (do() if state() == "pending" else None), dms
    def setUp(self):
        super().setUp(); os.environ["INFRA_GATES"] = X.GATES; wf(X.GATES, json.dumps({"infra_automerge": True}))
    def merged(self, g): return [c for c in g.calls if c[:2] == ["pr", "merge"]]
    def test_all_green_merges_with_head_match(self):
        g = FakeGh(pr_view(), GREEN); st, r, dms = self.run_do(self.act(), g)
        self.assertEqual(st, "pending"); self.assertEqual(dms, [])
        self.assertEqual(self.merged(g), [["pr", "merge", "7", "-R", "PraiseZhu/panoptes", "--squash", "--match-head-commit", "h" * 40]])
    def test_preconditions(self):
        cases = [(self.act(auto_merge=False), FakeGh(pr_view(), GREEN), "auto_merge"),
                 (self.act(), FakeGh(pr_view(), GREEN, diff(".github/workflows/ci.yml", ["a"], ["b"])), ".github"),
                 (self.act(), FakeGh(pr_view(), [{"name": "CI", "status": "completed", "conclusion": "failure"}]), "CI 未全绿"),
                 (self.act(), FakeGh(pr_view(body="no marker"), GREEN), "Infra-Sig"),
                 (self.act(), FakeGh(pr_view(), GREEN, merged_today=3), "日上限")]
        for a, g, want in cases:
            st, r, dms = self.run_do(a, g)
            self.assertEqual(self.merged(g), [], want); self.assertTrue(dms and want in dms[0][1], (want, dms))
            self.assertIn("https://x/pull/7", dms[0][1])
        wf(X.GATES, json.dumps({"infra_automerge": False}))
        g = FakeGh(pr_view(), GREEN); st, r, dms = self.run_do(self.act(), g)
        self.assertEqual(self.merged(g), []); self.assertIn("infra_automerge", dms[0][1])
    def test_a_muted_dm_is_not_recorded_as_sent(self):   # 10-30: 18 merge requests went to a muted file, the ledger said "DM sent"
        g = FakeGh(pr_view(), GREEN)
        r = O.handler(self.act(auto_merge=False), g, lambda t, k: False, sensitive=(X.files_from_diff, X.sensitive), now=NOW)[3]()
        self.assertIn("muted", r); self.assertNotIn("DM sent", r)
        r = O.handler(self.act(auto_merge=False), g, lambda t, k: True, sensitive=(X.files_from_diff, X.sensitive), now=NOW)[3]()
        self.assertIn("DM sent", r)
    def test_a_move_out_of_a_sensitive_path_blocks(self):   # 10-24 review: the PR diff's rename shows the old path too
        mv = "diff --git a/.github/workflows/ci.yml b/ci.yml\nsimilarity index 100%\nrename from .github/workflows/ci.yml\nrename to ci.yml\n"
        g = FakeGh(pr_view(), GREEN, mv); st, r, dms = self.run_do(self.act(), g)
        self.assertEqual(self.merged(g), []); self.assertTrue(dms and ".github/workflows/ci.yml" in dms[0][1], dms)
    def test_stale_states(self):
        self.assertEqual(self.run_do(self.act(), FakeGh(pr_view(), [{"name": "CI", "status": "in_progress", "conclusion": ""}]))[0], "stale:ci running")
        self.assertEqual(self.run_do(self.act(), FakeGh(pr_view(), []))[0], "stale:ci none")
        self.assertEqual(self.run_do(self.act(), FakeGh(pr_view(headRefOid="z" * 40), GREEN))[0], "stale:head moved")
        self.assertEqual(self.run_do(self.act(), FakeGh(pr_view(state="MERGED"), GREEN))[0], "done")
        self.assertTrue(O.handler(self.act(repo="xindong/mivo-canvas-plugin"), FakeGh(pr_view(), GREEN), None)[3] is not None)
        with self.assertRaises(RuntimeError): O.handler(self.act(repo="xindong/mivo-canvas-plugin"), FakeGh(pr_view(), GREEN), lambda *a: None)[3]()

class TestOutboundIssue(unittest.TestCase):
    def mk(self, state, comments, close=True):
        calls = []
        def gh(args, js=True):
            calls.append(args)
            if args[:2] == ["issue", "view"]: return {"state": state, "comments": comments}
            return "ok"
        a = {"key": "infraissue:inf1-x:16", "type": "gh.infra.issue", "repo": "PraiseZhu/pr-qa-agent-deploy", "issue": 16, "comment": "已修", "close": close}
        return O.handler(a, gh, None), calls
    def test_close_once(self):
        (who, state, validate, do), calls = self.mk("OPEN", [])
        self.assertEqual(state(), "pending"); do()
        self.assertEqual([c[:2] for c in calls if c[0] == "issue" and c[1] != "view"], [["issue", "close"]])
        self.assertIn("--comment", calls[-1]); self.assertIn("<!-- pi-line infraissue:inf1-x:16 -->", calls[-1][-1])
        (who, state, validate, do), calls = self.mk("CLOSED", [])
        self.assertEqual(state(), "done")                                  # closed by someone else: no second comment
    def test_comment_only(self):
        (who, state, validate, do), calls = self.mk("OPEN", [], close=False)
        self.assertEqual(state(), "pending"); do()
        self.assertEqual([c[:2] for c in calls if c[1] != "view"], [["issue", "comment"]])
        (who, state, validate, do), calls = self.mk("OPEN", [{"body": "x <!-- pi-line infraissue:inf1-x:16 -->"}], close=False)
        self.assertEqual(state(), "done")
    def test_wrong_repo_refused(self):
        def gh(args, js=True): return {"state": "OPEN", "comments": []}
        a = {"key": "k", "type": "gh.infra.issue", "repo": "aj0928/aj0928-handoff", "issue": 1, "comment": "c", "close": True}
        with self.assertRaises(RuntimeError): O.handler(a, gh, None)[2]()

# ====================================================================== 10. watch(): PR -> CI -> merge -> deploy -> verify
class TestWatch(Tmp):
    SIG = "inf1-" + "c" * 20
    def setUp(self):
        super().setUp()
        self.kicks = []
        os.makedirs(f"{X.LIVE}/scripts")
        sh(["git", "init", "-q", "-b", "main", X.LIVE]); wf(f"{X.LIVE}/scripts/bootstrap-box.sh", "exit ${FAKE_CHECK_RC:-0}\n")
        sh("git add -A && git -c user.name=t -c user.email=t@t commit -q -m base", cwd=X.LIVE)
        self.merge_sha = sh(["git", "-C", X.LIVE, "rev-parse", "HEAD"]).strip()
    def tickets(self, **t):
        wf(f"{X.OUT}/tickets.json", json.dumps({"tickets": {self.SIG: {"sig": self.SIG, "stage": "janitor", "round": 1, "auto_merge": True, "issues": [16], **t}}}))
    def W(self, gh, ctx=None):
        return X.watch({}, kick=lambda j, k: self.kicks.append((j, k)), now=NOW, ghf=gh, ctx_f=(lambda: ctx) if ctx else None)
    def T(self): return X.load_tickets()["tickets"][self.SIG]
    def test_pr_found_then_green_merge_action(self):
        self.tickets(status="pr_action_written")
        view = {"state": "OPEN", "headRefOid": "h" * 40, "statusCheckRollup": [{"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"}], "url": "u"}
        def gh(args, js=True):
            if args[:2] == ["pr", "list"]: return [{"number": 41, "state": "OPEN", "body": f"Infra-Sig: {self.SIG}", "url": "u"}]
            if args[:2] == ["pr", "view"]: return view
            raise AssertionError(args)
        self.W(gh)
        t = self.T(); self.assertEqual((t["status"], t["pr"], t["merge_head"]), ("merge_action_written", 41, "h" * 40))
        a = self.actions[-1]; self.assertEqual((a["type"], a["auto_merge"], a["head"]), ("gh.infra.merge", True, "h" * 40))
        self.assertEqual(self.kicks, [("infrafix", a["key"])])
        self.assertEqual(self.W(gh), [])                                  # polled < 2 min ago: nothing
    def test_ci_red_dm_once(self):
        self.tickets(status="pr_open", pr=41)
        view = {"state": "OPEN", "headRefOid": "h" * 40, "statusCheckRollup": [{"name": "test", "status": "COMPLETED", "conclusion": "FAILURE"}], "url": "u"}
        self.W(lambda a, js=True: view)
        self.assertEqual(self.T()["status"], "ci_red"); self.assertEqual(self.actions[-1]["type"], "slack.dm")
    def test_merged_then_deployed_then_verified(self):
        self.tickets(status="pr_open", pr=41)
        self.W(lambda a, js=True: {"state": "MERGED", "headRefOid": "h", "mergedAt": utc(NOW), "mergeCommit": {"oid": self.merge_sha}})
        self.assertEqual(self.T()["status"], "merged")
        o_other = occ("qa_tool_failures", "janitor", "another live fault", NOW, "x", issue=16)
        o_mine = X.mk("qa_tool_failures", "janitor", "m", NOW - dt.timedelta(hours=5), "report", "f", 1, "r0", issue=16)
        cands = X.aggregate([o_other, o_mine]); cands[self.SIG] = dict(next(iter(X.aggregate([o_mine]).values())), sig=self.SIG, issues={16})
        ctx = ctx_of([], cands=cands, tickets=X.load_tickets()["tickets"])
        X.watch({}, kick=lambda j, k: self.kicks.append((j, k)), now=NOW, ghf=lambda *a, **k: None, ctx_f=lambda: ctx)
        t = self.T(); self.assertEqual(t["status"], "deployed")
        iss = [a for a in self.actions if a["type"] == "gh.infra.issue"]
        self.assertEqual([(a["issue"], a["close"]) for a in iss], [(16, False)])   # another live fault in #16: comment only
        # first report after the deploy: clean -> verified
        wf(f"{X.REPORTS}/PR-9.json", json.dumps(report(9, NOW + dt.timedelta(hours=1), "r-after")))
        X.watch({}, now=NOW + dt.timedelta(hours=2), ghf=lambda *a, **k: None)
        self.assertEqual(self.T()["status"], "verified")
    def test_deploy_check_red_and_recurrence(self):
        self.tickets(status="merged", pr=41, merge_sha=self.merge_sha)
        os.environ["FAKE_CHECK_RC"] = "2"
        self.W(lambda *a, **k: None)
        self.assertEqual(self.T()["status"], "deploy_unhealthy"); self.assertIn("revert", self.actions[-1]["text"])
        # recurrence after deploy
        msg = "boom"; sig = X.mk("qa_tool_failures", "janitor", msg, NOW, "r", "f")["sig"]
        self.SIG = sig
        self.tickets(status="deployed", pr=41, merge_sha=self.merge_sha, deployed_at=NOW.isoformat(), pre_sigs=[sig])
        wf(f"{X.REPORTS}/PR-9.json", json.dumps(report(9, NOW + dt.timedelta(hours=1), "r2", {"qa_tool_failures": [{"stage": "janitor", "message": msg}]})))
        X.watch({}, now=NOW + dt.timedelta(hours=2), ghf=lambda *a, **k: None)
        self.assertEqual(self.T()["status"], "recurred"); self.assertIn("又出现", self.actions[-1]["text"])
    def test_last_round_recurrence_escalates_now_under_the_needs_human_key(self):
        # 10-24: at round MAX_ROUNDS no automatic round follows. evaluate() makes it a hard stop, but bookkeeping (which DMs hard stops)
        # only runs inside `infrafix --next`, and prwatch launches none while nothing is todo -> watch() escalates at once, under the
        # key bookkeeping would use, so a later --next does not DM it a second time
        msg = "boom"; sig = X.mk("qa_tool_failures", "janitor", msg, NOW, "r", "f")["sig"]; self.SIG = sig
        wf(f"{X.REPORTS}/PR-9.json", json.dumps(report(9, NOW + dt.timedelta(hours=1), "r2", {"qa_tool_failures": [{"stage": "janitor", "message": msg}]})))
        dms = []
        for rnd in (1, X.MAX_ROUNDS):
            self.tickets(status="deployed", pr=41, round=rnd, merge_sha=self.merge_sha, deployed_at=NOW.isoformat(), pre_sigs=[sig])
            X.watch({}, now=NOW + dt.timedelta(hours=2), ghf=lambda *a, **k: None)
            self.assertEqual(self.T()["status"], "recurred"); dms.append(self.actions[-1])
        auto, last = dms
        self.assertEqual(auto["key"], f"infra-recurred:{sig}:41"); self.assertTrue(auto["text"].startswith(X.PN.KINDS["auto"]), auto["text"])
        self.assertIn(f"第 2 轮，最多 {X.MAX_ROUNDS} 轮", auto["text"])
        self.assertEqual(last["key"], X.dm_action("needs_human", {"sig": sig, "sig8": sig[5:13]}, "x")["key"])
        self.assertTrue(last["text"].startswith(X.PN.KINDS["action"]), last["text"]); self.assertIn("不会再自动修", last["text"])
    def test_new_fault_after_deploy_suggests_revert(self):
        self.tickets(status="deployed", pr=41, merge_sha=self.merge_sha, deployed_at=NOW.isoformat(), pre_sigs=[])
        wf(f"{X.REPORTS}/PR-9.json", json.dumps(report(9, NOW + dt.timedelta(hours=1), "r2", {"qa_tool_failures": [{"stage": "write-merged-report", "message": "EROFS"}]})))
        X.watch({}, now=NOW + dt.timedelta(hours=2), ghf=lambda *a, **k: None)
        self.assertEqual(self.T()["status"], "deploy_unhealthy"); self.assertIn("Revert", self.actions[-1]["text"])   # 10-24 praise_notice wording
    def test_closed_unmerged_is_rejected(self):
        self.tickets(status="pr_open", pr=41)
        self.W(lambda a, js=True: {"state": "CLOSED", "headRefOid": "h"})
        self.assertEqual(self.T()["status"], "rejected")

# ====================================================================== 11. CLI read-only modes
class TestCli(Tmp):
    def test_pending_json_is_pure_json_and_writes_nothing(self):
        wf(f"{X.REPORTS}/PR-1.json", json.dumps(report(1, dt.datetime.now(CST) - dt.timedelta(hours=1), "r1",
                                                         {"qa_tool_failures": [{"stage": "janitor", "message": "EACCES"}]})))
        def gh(args, js=True): return []
        X.gh = gh
        before = sorted(os.listdir(X.OUT))
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err): rc = X.main(["--pending-json"])
        rows = json.loads(buf.getvalue())
        self.assertEqual(rc, 0)
        self.assertTrue(any(r["stage"] == "janitor" and r["reason_if_skipped"] is None for r in rows))
        self.assertTrue(any(r["sig"] == X.CR_SIG and r["state"] == "held" for r in rows))
        self.assertEqual(sorted(os.listdir(X.OUT)), before)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()): X.main(["--list"])
        self.assertIn("待 Praise 决定", buf.getvalue()); self.assertIn("可做", buf.getvalue())
    def test_list_survives_gh_failure_but_next_fails_closed(self):
        def gh(args, js=True): raise RuntimeError("gh down")
        X.gh = gh
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()): self.assertEqual(X.main(["--list"]), 0)
        self.assertIn("没读到", buf.getvalue())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()): self.assertEqual(X.main(["--next"]), 1)
        self.assertIn('"status": "error"', buf.getvalue())

# ====================================================================== 12. prwatch snippet: dispatch decision
try:
    import prwatch as P
    HAVE_P = hasattr(P, "dispatch_infra")
except Exception: HAVE_P = False
@unittest.skipUnless(HAVE_P, "prwatch.py without the infrafix snippet")
class TestPrwatchSnippet(Tmp):
    def setUp(self):
        super().setUp()
        self.ps = {k: getattr(P, k) for k in ("BASE", "PI_CODE", "INFRA_FLAG", "mem_mb", "lock_free", "subprocess", "kick_outbound")}
        P.BASE = P.PI_CODE = self.tmp; P.INFRA_FLAG = f"{self.tmp}/takeover/infrafix-event"
        P.mem_mb = lambda: 8000; P.lock_free = lambda p: True
        os.makedirs(f"{self.tmp}/out/logs", exist_ok=True)                            # run.sh / qlog create it on the box
        self.launched, self.kicked = [], []
        P.kick_outbound = lambda job, key: self.kicked.append((job, key))
        real = subprocess
        class Sub:
            PIPE, DEVNULL, STDOUT = real.PIPE, real.DEVNULL, real.STDOUT
            run = staticmethod(real.run)
            Popen = staticmethod(lambda args, **kw: self.launched.append(args))
        P.subprocess = Sub
        self.rows = [{"sig8": "aaaa1111", "cat": "qa_tool_failures", "stage": "janitor", "reason_if_skipped": None},
                     {"sig8": "bbbb2222", "cat": "field", "stage": "code-review", "reason_if_skipped": "待 Praise 决定：x"}]
        wf(f"{self.tmp}/bin/infrafix.py", f"import json; print(json.dumps({self.rows!r}))\n")
    def tearDown(self):
        for k, v in self.ps.items(): setattr(P, k, v)
        super().tearDown()
    def test_launch_only_with_flag_memory_and_work(self):
        st = {}
        P.dispatch_infra(st); self.assertEqual(self.launched, [])                      # no takeover flag: follow-up only
        wf(P.INFRA_FLAG, "")
        P.mem_mb = lambda: 2000; P.dispatch_infra(st); self.assertEqual(self.launched, [])
        P.mem_mb = lambda: 8000; P.dispatch_infra(st)
        self.assertEqual(self.launched, [[f"{self.tmp}/bin/run.sh", "infrafix", "--next"]])
        P.dispatch_infra(st); self.assertEqual(len(self.launched), 1)                 # inputs unchanged, < 15 min: not due
        wf(f"{self.tmp}/out/infrafix/last-result.json", json.dumps({"action_key": "infrapr:inf1-x"}))
        P.dispatch_infra(st); P.dispatch_infra(st)
        self.assertEqual(self.kicked, [("infrafix", "infrapr:inf1-x")])              # kicked once per action key
    def test_held_only_means_no_launch(self):
        self.rows = self.rows[1:]; wf(f"{self.tmp}/bin/infrafix.py", f"import json; print(json.dumps({self.rows!r}))\n")
        wf(P.INFRA_FLAG, ""); P.dispatch_infra({})
        self.assertEqual(self.launched, [])

if __name__ == "__main__":
    unittest.main(verbosity=1)
