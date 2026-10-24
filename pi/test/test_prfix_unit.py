#!/usr/bin/env python3
"""prfix.py unit tests — local only: no network, no model, no writes outside a temp dir.
Run: python3 test/test_prfix_unit.py   (or python3 -m pytest -q test/test_prfix_unit.py when pytest is installed)."""
import os as _os
_os.environ.setdefault('PRFIX_OWNERS', '*')
_os.environ['PRFIX_DISPATCHED'] = '1'   # runs as if prwatch launched them (the cron fallback defers to a live prwatch)   # claim/round tests use other authors; owner filter has its own test
for _k in [k for k in _os.environ if k.startswith('GIT_')]: del _os.environ[_k]   # never act on a hook's GIT_DIR (10-23)
import tempfile as _tf
_JCONF = _os.path.join(_tf.mkdtemp(prefix="prfix-test-jconf-"), "janitor.json")   # never the live conf/janitor.json (enforce=true since 10-25)
with open(_JCONF, "w") as _f: _f.write('{"enforce": false}')
_os.environ['JANITOR_CONF'] = _JCONF
import atexit as _ax, shutil as _sh; _ax.register(_sh.rmtree, _os.path.dirname(_JCONF), True)   # no temp dir left behind per run
import argparse, contextlib, re, datetime as dt, importlib.util, io, json, os, shutil, subprocess, sys, tempfile, textwrap, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
sys.path.insert(0, BIN)
import prfix  # noqa: E402

CST = prfix.CST
T0 = dt.datetime(2026, 10, 22, 12, 0, tzinfo=CST)          # report time used by most fixtures
NOW = dt.datetime(2026, 10, 23, 21, 0, tzinfo=CST)

def APPEND(p, s):
    with open(p, "a") as f: f.write(s)
def wf(p, s):
    with open(p, "w") as f: f.write(s)
def rf(p):
    with open(p) as f: return f.read()
def FP(c): return "mq1-" + (c * 20)[:20]                     # fake 20-hex fingerprints
def utc(t): return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def finding(fp, sev="P1", title="问题", file="src/a.ts", line=10): return {"fingerprint": fp, "severity": sev, "title": title, "file": file, "line": line}
def report(n, author, findings, t=T0, hist=()):
    return {"cur": (t, {"number": n, "author": author, "findings": findings}, f"PR-{n}.json"),
            "hist": [(ht, {"number": n, "author": author, "findings": hf}, f"history/PR-{n}.v{i + 1}.json") for i, (ht, hf) in enumerate(hist)]}
def pr(num, author, body="", title="t", created=NOW - dt.timedelta(hours=2), state="OPEN"):
    return {"number": num, "author": {"login": author}, "body": body, "title": title, "createdAt": utc(created), "state": state}
def ctx(**kw):
    c = {"gates": {923: "Praise 闸：#923 修复 PR 不得创建"}, "gate_warn": None, "ledger_file": None, "ledger": {}, "reports": {},
         "fix2": set(), "notes": [], "prs": [], "receipts": {}, "links": {}, "tickets": {}, "unread_repos": [],
         "owner": {"aj0928": "aj0928", "lubinyu-lab": "aj0928", "zhongxingtian-ai": "zhongxingtian-ai"},
         "repo_of": {"aj0928": "aj0928/aj0928-handoff", "lubinyu-lab": "aj0928/aj0928-handoff", "zhongxingtian-ai": "zhongxingtian-ai/handover-mivo"}}
    c.update(kw); return c
def verdicts(c, now=NOW): return {v["key"]: v for v in prfix.evaluate_all(c, now)}

class Tmp(unittest.TestCase):
    """Every test gets its own temp tree and prfix paths pointing into it."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prfix-test-")
        self.saved = {k: getattr(prfix, k) for k in ("GIT", "WT_ROOT", "NOTES", "TMPROOT", "OUT", "GATES", "QA_LOCK")}
        prfix.WT_ROOT = f"{self.tmp}/praise-pr"; prfix.NOTES = f"{self.tmp}/praise-pr/notes"; prfix.TMPROOT = f"{self.tmp}/pi-prfix"
        prfix.OUT = f"{self.tmp}/out"; prfix.GATES = f"{self.tmp}/gates.json"; prfix.QA_LOCK = f"{self.tmp}/argus-root/nightly.lock"
        os.makedirs(prfix.NOTES); os.makedirs(prfix.OUT)
        self.env = dict(os.environ)
    def tearDown(self):
        for k, v in self.saved.items(): setattr(prfix, k, v)
        os.environ.clear(); os.environ.update(self.env)
        shutil.rmtree(self.tmp, ignore_errors=True)
    def fakebin(self, name, script):
        d = f"{self.tmp}/fakebin"; os.makedirs(d, exist_ok=True)
        p = f"{d}/{name}"; wf(p, "#!/bin/bash\n" + textwrap.dedent(script)); os.chmod(p, 0o755)
        os.environ["PATH"] = d + ":" + os.environ["PATH"]

# ====================================================================== 1. rc handling (vitest / npm ci)
class TestRc(Tmp):
    def test_vitest_real_rc_and_python_tail(self):
        self.fakebin("npx", """
            for i in $(seq 1 100); do echo "line $i"; done
            echo "args: $*"
            exit ${FAKE_RC:-0}
        """)
        for rc in (0, 1, 2):
            os.environ["FAKE_RC"] = str(rc)
            got, out = prfix.vitest(self.tmp, ["src/a.test.ts", "src/b.test.ts"])
            self.assertEqual(got, rc)
            self.assertNotIn("Bad substitution", out)
            self.assertLessEqual(len(out.splitlines()), 60)                       # tail cut in Python
            self.assertIn("args: vitest run src/a.test.ts src/b.test.ts", out)    # argv, no shell
        self.assertEqual(prfix.vitest_verdict(0, "ok"), "pass")
        self.assertEqual(prfix.vitest_verdict(1, "FAIL x"), "fail")
        for rc, out in ((124, "slow"), (127, "no npx"), (1, "No test files found, exiting with code 1")):
            with self.assertRaises(prfix.Transient): prfix.vitest_verdict(rc, out)

    def test_vitest_timeout_is_124(self):
        self.fakebin("npx", "sleep 30\n")
        got, out = prfix.vitest(self.tmp, ["x.test.ts"], t=1)
        self.assertEqual(got, 124)

    def test_npm_ci_real_rc(self):
        self.fakebin("npm", 'echo "npm $*"; exit ${FAKE_RC:-0}\n')
        for rc in (0, 10, 20, 130):     # the old `echo $?` + endswith("0") called 10/20/130 a success
            os.environ["FAKE_RC"] = str(rc)
            got, out = prfix.npm_ci(self.tmp)
            self.assertEqual(got, rc)
            self.assertIn("npm ci --ignore-scripts", out)

    def test_run_never_uses_a_shell(self):
        rc, out = prfix.run(["printf", "%s", "${PIPESTATUS[0]} | tail"])
        self.assertEqual((rc, out), (0, "${PIPESTATUS[0]} | tail"))

    def test_norepro_detection(self):
        for t in ("## NOREPRO\nevidence", "NOREPRO: x", "**NOREPRO**\n", "  - NOREPRO (file:1)", "intro\n`NOREPRO`"):
            self.assertTrue(prfix.is_norepro(t), t)
        for t in ("I did not reply NOREPRO because the test fails", "ROOT CAUSE a.ts:1", ""):
            self.assertFalse(prfix.is_norepro(t), t)
        self.assertEqual(prfix.root_cause("x\nROOT CAUSE: src/a.ts:12 off by one\nmore\nROOT CAUSE **src/b.ts:3**\npara"), "src/b.ts:3")

# ====================================================================== 2. fingerprint-level claims
A, B, C, D = FP("a"), FP("b"), FP("c"), FP("d")

class TestClaims(unittest.TestCase):
    def test_1156_author_pr_after_report_claims(self):
        c = ctx(reports={1156: report(1156, "aj0928", [finding(A), finding(B, "P2")])},
                prs=[pr(1236, "aj0928", body="#1156 合并后又冒出两处问题", created=T0 + dt.timedelta(hours=28))])
        v = verdicts(c)[f"1156:{A}"]
        self.assertEqual(v["state"], "claimed"); self.assertIn("#1236", v["reason"]); self.assertIn("aj0928", v["reason"])

    def test_author_pr_rules(self):
        rep = {1156: report(1156, "aj0928", [finding(A), finding(B)])}
        cases = {
            "before the report": (pr(1100, "aj0928", body="#1156", created=T0 - dt.timedelta(hours=1)), "todo"),
            "closed unmerged": (pr(1101, "aj0928", body="#1156", state="CLOSED"), "todo"),
            "someone else": (pr(1102, "PraiseZhu", body="QA: #1156"), "todo"),
            "names another finding": (pr(1103, "aj0928", body=f"#1156 {B}"), "todo"),
            "changelog listing": (pr(1104, "aj0928", title="chore: 更新日志", body=" ".join(f"#{n}" for n in range(1150, 1160))), "todo"),
            "merged": (pr(1105, "aj0928", body="fix #1156", state="MERGED"), "claimed"),
        }
        for name, (p, want) in cases.items():
            with self.subTest(name):
                self.assertEqual(verdicts(ctx(reports=rep, prs=[p]))[f"1156:{A}"]["state"], want)

    def test_route_owner_counts_as_author_side(self):
        c = ctx(reports={943: report(943, "lubinyu-lab", [finding(A)])}, prs=[pr(1123, "aj0928", body="QA: xindong/mivo-canvas-plugin#943")])
        self.assertEqual(verdicts(c)[f"943:{A}"]["state"], "claimed")

    def test_author_pr_linking_another_handoff_issue_is_specific(self):
        url = "https://github.com/zhongxingtian-ai/handover-mivo/issues/2"
        c = ctx(reports={1124: report(1124, "zhongxingtian-ai", [finding(B, "P2")], hist=[(T0 - dt.timedelta(minutes=13), [finding(A)])])},
                fix2={1124}, links={url: (1124, B)},
                prs=[pr(1225, "zhongxingtian-ai", body=f"QA: xindong/mivo-canvas-plugin#1124\nIssue: {url}", state="MERGED")])
        vs = verdicts(c)
        self.assertEqual(vs[f"1124:{B}"]["state"], "claimed")            # the P2 the PR is about
        self.assertEqual(vs[f"1124:{A}"]["state"], "skip")               # history-only P1: listed for a human, never auto
        self.assertIn("仅见于历史报告版本", vs[f"1124:{A}"]["reason"])

    def test_943_qa_ref_without_fingerprint_does_not_claim_p1(self):
        # #1168 fixed the P2 coverage finding and says "QA: #943": it must not swallow the P1 (ledger-only row)
        led = {(943, A): {"pr": 943, "fingerprint": A, "level": "P1", "status": "open", "author": "lubinyu-lab", "summary": "取消批量生成…",
                          "fix_pr": None, "fix_prs": [], "fix_candidates_rejected": [1168, 1123], "fix_round": 0},
               (943, B): {"pr": 943, "fingerprint": B, "level": "P2", "status": "fix_merged_pending_retest", "fix_pr": 1168,
                          "fix_prs": [{"pr": 1168}], "fix_candidates_rejected": []}}
        c = ctx(reports={943: report(943, "lubinyu-lab", [finding(B, "P2")])}, ledger=led,
                prs=[pr(1168, "PraiseZhu", body="QA: #943 补覆盖", state="MERGED"), pr(1123, "aj0928", body="QA: #943", created=NOW)])
        v = verdicts(c)[f"943:{A}"]
        self.assertEqual(v["c"]["source"], "ledger")
        self.assertEqual(v["state"], "todo", v["reason"])                # #1123 is in fix_candidates_rejected, #1168 has no fingerprint
        c["receipts"] = {(943, A): ("已修 #1123", "https://github.com/aj0928/aj0928-handoff/issues/5")}
        v = verdicts(c)[f"943:{A}"]
        self.assertEqual(v["state"], "claimed"); self.assertIn("已修 #1123", v["reason"]); self.assertIn("台账已拒认 #1123", v["reason"])

    def test_1196_receipt_claims(self):
        c = ctx(reports={1196: report(1196, "zhongxingtian-ai", [finding(A)])},
                receipts={(1196, A): ("误报", "https://github.com/zhongxingtian-ai/handover-mivo/issues/3")})
        v = verdicts(c)[f"1196:{A}"]
        self.assertEqual(v["state"], "claimed"); self.assertIn("误报", v["reason"])

    def test_fingerprint_in_any_pr_body_claims(self):
        rep = {1178: report(1178, "PraiseZhu", [finding(A), finding(B)])}
        c = ctx(reports=rep, prs=[pr(1300, "someone", body=f"fixes {A}")])
        vs = verdicts(c)
        self.assertEqual(vs[f"1178:{A}"]["state"], "claimed"); self.assertEqual(vs[f"1178:{B}"]["state"], "todo")

    def test_ledger_rules(self):
        rep = {1000: report(1000, "x", [finding(A)])}
        row = lambda **k: {(1000, A): {"pr": 1000, "fingerprint": A, "level": "P1", "status": "open", "fix_pr": None, "fix_prs": [], **k}}
        self.assertEqual(verdicts(ctx(reports=rep, ledger=row(fix_pr=7, fix_prs=[{"pr": 7}], status="fix_merged_pending_retest")))[f"1000:{A}"]["state"], "claimed")
        self.assertEqual(verdicts(ctx(reports=rep, ledger=row(fix_pr=7, fix_candidates_rejected=[7])))[f"1000:{A}"]["state"], "todo")
        self.assertEqual(verdicts(ctx(reports=rep, ledger=row(concluded_reason="误报", status="concluded")))[f"1000:{A}"]["state"], "claimed")

    def test_notes_need_number_and_fp8_or_level(self):
        rep = {1174: report(1174, "x", [finding(A, "P1"), finding(B, "P2")])}
        self.assertEqual(verdicts(ctx(reports=rep, notes=["1174-p2-coverage.md"]))[f"1174:{A}"]["state"], "todo")      # level mismatch
        self.assertEqual(verdicts(ctx(reports=rep, notes=["1174-p1-provider.md"]))[f"1174:{A}"]["state"], "claimed")
        self.assertEqual(verdicts(ctx(reports=rep, notes=[f"note-1174-{A[4:12]}.md"]))[f"1174:{A}"]["state"], "claimed")
        self.assertEqual(verdicts(ctx(reports=rep, notes=["comment-1174.md", "11740-p1.md"]))[f"1174:{A}"]["state"], "todo")  # N only / other N
        self.assertEqual(verdicts(ctx(reports=rep, notes=["926-p1a-norepro.md"]))[f"1174:{A}"]["state"], "todo")

    def test_rounds(self):
        rep = {1011: report(1011, "x", [finding(A)])}
        body = f"QA: #1011\n{A}"
        reopened = {(1011, A): {"pr": 1011, "fingerprint": A, "level": "P1", "status": "open", "retest_reopened": True, "fix_pr": 1, "fix_prs": [{"pr": 1}], "fix_round": 1}}
        v = verdicts(ctx(reports=rep, ledger=reopened, prs=[pr(1, "PraiseZhu", body=body, state="MERGED")]))[f"1011:{A}"]
        self.assertEqual((v["state"], v["round"]), ("todo", 2))                                    # round 2 allowed: retest reopened
        v = verdicts(ctx(reports=rep, prs=[pr(1, "PraiseZhu", body=body, state="MERGED")]))[f"1011:{A}"]
        self.assertEqual(v["state"], "claimed")                                                    # merged, not reopened
        v = verdicts(ctx(reports=rep, prs=[pr(1, "PraiseZhu", body=body, state="CLOSED")]))[f"1011:{A}"]
        self.assertEqual(v["state"], "skip"); self.assertIn("被关掉了", v["reason"]); self.assertTrue(v["hard_stop"])   # closed unmerged: ask him (10-24)
        row = {(1011, A): {"pr": 1011, "fingerprint": A, "level": "P1", "status": "open", "fix_round": 1}}
        v = verdicts(ctx(reports=rep, ledger=row))[f"1011:{A}"]
        self.assertEqual(v["state"], "skip"); self.assertIn("不开第二轮", v["reason"])              # 1 round (ledger), no reopen
        two = [pr(1, "PraiseZhu", body=body, state="MERGED"), pr(2, "PraiseZhu", body=body, state="MERGED")]
        v = verdicts(ctx(reports=rep, ledger=reopened, prs=two))[f"1011:{A}"]
        self.assertEqual(v["state"], "skip"); self.assertTrue(v["hard_stop"]); self.assertIn("第三轮转人工", v["reason"])

    def test_gate_and_unread_repo_skip(self):
        rep = {923: report(923, "PraiseZhu", [finding(A)]), 1196: report(1196, "zhongxingtian-ai", [finding(B)])}
        vs = verdicts(ctx(reports=rep, unread_repos=["zhongxingtian-ai/handover-mivo"]))
        self.assertEqual(vs[f"923:{A}"]["state"], "skip"); self.assertIn("Praise 闸", vs[f"923:{A}"]["reason"])
        self.assertEqual(vs[f"1196:{B}"]["state"], "skip"); self.assertIn("回执没读到", vs[f"1196:{B}"]["reason"])

    def test_confirmed_p2_unless_ruled_no_fix(self):
        # 10-23（p2-rulings.md 开头）：按统一分级确认成立的 P2 要修，不再单独裁决修不修；判 不修 / 误报 / P3 / 已修 的 PR 不开单
        rep = {1124: report(1124, "x", [finding(A, "P2")]), 1125: report(1125, "x", [finding(B, "P2")])}
        vs = verdicts(ctx(reports=rep, fix2={1124}, nofix={1125}))
        self.assertIn(f"1124:{A}", vs); self.assertNotIn(f"1125:{B}", vs)
        self.assertIn(f"1125:{B}", verdicts(ctx(reports=rep, fix2=set())), "no ruling at all: a confirmed P2 is a ticket")
        self.assertEqual(prfix.fix_rulings("| 10-23 | #1124 | x | 修 | y |\n| 10-23 | #948 | P2 | 已修 | z |\n| 10-23 | #434 | p | 产品代码不修 | w |"), {1124})

    def test_docs_finding_is_a_hard_stop(self):
        rep = {1124: report(1124, "x", [finding(A, "P2", file="docs/ops/bff-deployment.md")])}
        v = verdicts(ctx(reports=rep, fix2={1124}))[f"1124:{A}"]
        self.assertEqual(v["state"], "skip"); self.assertTrue(v["hard_stop"]); self.assertIn("文档类", v["reason"])
        v = verdicts(ctx(reports=rep, fix2={1124}, prs=[pr(1225, "x", body="QA: #1124", state="MERGED")]))[f"1124:{A}"]
        self.assertEqual(v["state"], "claimed"); self.assertIsNone(v["hard_stop"])        # claimed first: no stop, no DM

    def test_history_only_docs_finding_gets_no_qa_hint(self):
        rep = {1124: report(1124, "x", [finding(B, "P2")], hist=[(T0 - dt.timedelta(minutes=13), [finding(A, file="docs/ops/bff-deployment.md")])])}
        v = verdicts(ctx(reports=rep))[f"1124:{A}"]
        self.assertEqual(v["state"], "skip"); self.assertNotIn("--qa", v["reason"]); self.assertTrue(v["hard_stop"])

    def test_ledger_only_row_gets_context_from_acceptance_review(self):
        led = {(943, A): {"pr": 943, "fingerprint": A, "level": "P1", "status": "open", "author": "lubinyu-lab", "summary": "s", "fix_prs": []}}
        reps = {943: dict(report(943, "lubinyu-lab", []), acc=[finding(A, file="cindyplugin/src/agent/generationResultImages.ts", line=58)])}
        c = prfix.collect(reps, led, set())[(943, A)]
        self.assertEqual((c["source"], c["finding"]["file"], c["finding"]["line"], c["sev"]), ("ledger", "cindyplugin/src/agent/generationResultImages.ts", 58, "P1"))

    def test_ticket_states(self):
        rep = {1178: report(1178, "PraiseZhu", [finding(A)])}
        k = f"1178:{A}"
        def st(t, prs=()): return verdicts(ctx(reports=rep, tickets={k: t}, prs=list(prs)))[k]["state"]
        self.assertEqual(st({"status": "pr_action_written", "action_key": "fixpr:1178:x"}), "claimed")
        self.assertEqual(st({"status": "pr_action_written"}, [pr(1400, "PraiseZhu", body=f"QA: #1178 {A}", state="CLOSED")]), "skip")
        self.assertEqual(st({"status": "norepro", "confirmed": True}), "claimed")                         # two models agree: closed
        self.assertEqual(st({"status": "norepro"}), "todo")                                                # (old unconfirmed ones: try again)
        self.assertEqual(st({"status": "needs_human", "last_error": "x", "harness": prfix.PRBOT_HARNESS}), "todo")   # no such stop any more
        self.assertEqual(st({"status": "parked", "next_retry_at": (NOW + dt.timedelta(hours=5)).isoformat()}), "skip")
        self.assertEqual(st({"status": "parked", "next_retry_at": (NOW - dt.timedelta(minutes=1)).isoformat()}), "todo")   # tries again itself
        self.assertEqual(st({"status": "needs_praise", "last_error": "改 .github/"}), "skip")                 # kind 5: waits for Praise
        self.assertEqual(st({"status": "failed", "transient_failures": 1, "next_retry_at": (NOW + dt.timedelta(minutes=5)).isoformat()}), "skip")
        self.assertEqual(st({"status": "failed", "transient_failures": 1, "next_retry_at": (NOW - dt.timedelta(minutes=5)).isoformat()}), "todo")
        self.assertEqual(st({"status": "running", "pid": 999999999, "started_at": NOW.isoformat()}), "skip")   # dead run -> backoff

# ====================================================================== 3. priority
class TestPriority(unittest.TestCase):
    def test_p0_p1_p2_then_earliest_deadline(self):
        rep = {
            1: report(1, "x", [finding(A, "P2")], t=T0 - dt.timedelta(days=3)),
            2: report(2, "x", [finding(B, "P1")], t=T0),
            3: report(3, "x", [finding(C, "P1")], t=T0 - dt.timedelta(hours=5)),
            4: report(4, "x", [finding(D, "P0")], t=T0 + dt.timedelta(hours=5)),
        }
        vs = prfix.evaluate_all(ctx(reports=rep, fix2={1}), NOW)
        self.assertEqual([v["c"]["qa"] for v in vs], [4, 3, 2, 1])
        self.assertEqual(prfix.next_ticket(vs)["c"]["qa"], 4)
        v3 = next(v for v in vs if v["c"]["qa"] == 3)
        self.assertEqual(v3["deadline"] - v3["first_seen"], dt.timedelta(hours=24))
        self.assertEqual(v3["first_seen"], T0 - dt.timedelta(hours=5))

    def test_first_seen_is_the_first_version_carrying_the_fingerprint(self):
        rep = {5: report(5, "x", [finding(A)], t=T0, hist=[(T0 - dt.timedelta(hours=3), [finding(A)]), (T0 - dt.timedelta(hours=9), [finding(B)])])}
        v = verdicts(ctx(reports=rep))[f"5:{A}"]
        self.assertEqual(v["first_seen"], T0 - dt.timedelta(hours=3))

    def test_pending_json_shape(self):
        rep = {1178: report(1178, "PraiseZhu", [finding(A), finding(B)]), 923: report(923, "PraiseZhu", [finding(C)])}
        rows = prfix.pending(prfix.evaluate_all(ctx(reports=rep, prs=[pr(9, "z", body=B)]), NOW))
        self.assertEqual({r["fp"] for r in rows}, {A, C})                      # B claimed -> not pending
        for r in rows:
            self.assertTrue({"qa", "fp", "sev", "first_seen", "deadline", "reason_if_skipped"} <= set(r))
            dt.datetime.fromisoformat(r["first_seen"]); dt.datetime.fromisoformat(r["deadline"])
        self.assertIsNone(next(r for r in rows if r["fp"] == A)["reason_if_skipped"])
        self.assertIn("闸", next(r for r in rows if r["fp"] == C)["reason_if_skipped"])

# ====================================================================== 4. backoff state machine
class TestBackoff(unittest.TestCase):
    def test_transient_15m_1h_4h_then_parked(self):
        tk = prfix.new_ticket({"qa": 1, "fp": A, "sev": "P1"}); waits = []
        for i in range(3):
            tk, note = prfix.apply_outcome(tk, {"status": "failed", "kind": "transient", "error": f"npm rc=1 #{i}"}, NOW)
            self.assertEqual(tk["status"], "failed"); self.assertIsNone(note)
            waits.append(dt.datetime.fromisoformat(tk["next_retry_at"]) - NOW)
        self.assertEqual(waits, [dt.timedelta(minutes=15), dt.timedelta(hours=1), dt.timedelta(hours=4)])
        tk, note = prfix.apply_outcome(tk, {"status": "failed", "kind": "transient", "error": "model rc=130"}, NOW)
        self.assertEqual((tk["status"], note, tk["park_reason"]), ("parked", None, "一直出故障"))          # 10-24 evening: no DM
        self.assertEqual(dt.datetime.fromisoformat(tk["next_retry_at"]) - NOW, dt.timedelta(hours=prfix.PARK_WAIT_H[0]))
        self.assertEqual(len(tk["history"]), 4)

    def test_deterministic_and_success_and_norepro(self):
        tk = prfix.new_ticket({"qa": 1, "fp": A, "sev": "P1"})
        t2, n2 = prfix.apply_outcome(tk, {"status": "failed", "kind": "deterministic", "error": "修了两次仍不过"}, NOW)
        self.assertEqual((t2["status"], n2, t2["fresh_retries"]), ("failed", None, 1))     # 10-24: once more from scratch first
        t2, n2 = prfix.apply_outcome(t2, {"status": "failed", "kind": "deterministic", "error": "修了两次仍不过"}, NOW)
        self.assertEqual((t2["status"], n2, t2["park_reason"]), ("parked", None, "修不动"))
        t2, _ = prfix.apply_outcome(t2, {"status": "failed", "kind": "deterministic", "error": "x"}, NOW)   # parked again: waits longer
        t2, _ = prfix.apply_outcome(t2, {"status": "failed", "kind": "deterministic", "error": "x"}, NOW)
        self.assertEqual((t2["park_count"], dt.datetime.fromisoformat(t2["next_retry_at"]) - NOW), (2, dt.timedelta(hours=prfix.PARK_WAIT_H[1])))
        t6, n6 = prfix.apply_outcome(tk, {"status": "failed", "kind": "praise", "error": "改 .github/workflows/ci.yml"}, NOW)
        self.assertEqual((t6["status"], n6), ("needs_praise", "needs_praise"))                    # kind 5: the only DM
        t3, n3 = prfix.apply_outcome({**tk, "transient_failures": 2}, {"status": "pr_action_written", "action_key": "fixpr:1:x"}, NOW)
        self.assertEqual((t3["status"], t3["transient_failures"], t3["action_key"], n3), ("pr_action_written", 0, "fixpr:1:x", None))
        t4, n4 = prfix.apply_outcome(tk, {"status": "norepro", "kind": "norepro", "error": "NOREPRO"}, NOW)
        self.assertEqual((t4["status"], n4, t4["park_reason"]), ("parked", None, "复现存疑"))   # contested: tried again later, no DM
        t5, n5 = prfix.apply_outcome(tk, {"status": "norepro", "kind": "norepro", "error": "NOREPRO", "confirmed": True}, NOW)
        self.assertEqual((t5["status"], n5, t5["confirmed"]), ("norepro", None, True))     # two models agree: no DM

    def test_next_skips_until_retry_time(self):
        rep = {1: report(1, "x", [finding(A, "P0")]), 2: report(2, "x", [finding(B, "P1")])}
        tk, _ = prfix.apply_outcome(prfix.new_ticket({"qa": 1, "fp": A, "sev": "P0"}), {"status": "failed", "kind": "transient", "error": "x"}, NOW)
        c = ctx(reports=rep, tickets={f"1:{A}": tk})
        self.assertEqual(prfix.next_ticket(prfix.evaluate_all(c, NOW))["c"]["qa"], 2)
        self.assertEqual(prfix.next_ticket(prfix.evaluate_all(c, NOW + dt.timedelta(minutes=16)))["c"]["qa"], 1)

# ====================================================================== 5. gates + run_ticket orchestration
class TestRunTicket(Tmp):
    def setUp(self):
        super().setUp()
        self.acts = []; self.calls = []
        self.orig = {k: getattr(prfix, k) for k in ("gather", "fix_one", "record_actions", "mem_gb")}
        prfix.record_actions = lambda job, day, acts: self.acts.extend(acts)
        prfix.mem_gb = lambda: 8.0
        rep = {1178: report(1178, "PraiseZhu", [finding(A), finding(B)], t=NOW - dt.timedelta(hours=2)),
               923: report(923, "PraiseZhu", [finding(C)], t=NOW - dt.timedelta(days=9))}
        self.ctx = ctx(reports=rep)
        prfix.gather = lambda: dict(self.ctx, tickets=prfix.load_tickets().get("tickets", {}))
    def tearDown(self):
        for k, v in self.orig.items(): setattr(prfix, k, v)
        super().tearDown()
    def fake_fix(self, outcome):
        def f(c, A_, d):
            self.calls.append((c["qa"], c["fp"], A_.dry_run))
            return dict(outcome), {"evidence": f"{prfix.OUT}/ev.md", "model": [{"step": "repro", "task": "t", "text": "## NOREPRO\nwhy"}],
                                   "norepro": ("模型判不可复现", "## NOREPRO\nwhy"), "base": "abc"}
        prfix.fix_one = f
    def main(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf): rc = prfix.main(list(argv))
        out = buf.getvalue(); last = out.strip().splitlines()[-1] if out.strip() else ""
        return rc, out, (json.loads(last[len("RESULT "):]) if last.startswith("RESULT ") else None)

    def test_gate_file_rules(self):
        self.assertEqual(prfix.load_gates(f"{self.tmp}/missing.json")[0], prfix.DEFAULT_GATES)
        wf(prfix.GATES, '{"no_fix_pr": {"1178": "测试闸"}}')
        self.assertEqual(prfix.load_gates()[0], {1178: "测试闸"})
        wf(prfix.GATES, "{broken")
        with self.assertRaises(prfix.GateFileError): prfix.load_gates()

    def test_qa_cannot_bypass_gate(self):
        self.fake_fix({"status": "pr_action_written", "action_key": "k"})
        rc, out, r = self.main("--qa", "923", "--fp", C)
        self.assertEqual((rc, r["status"]), (1, "gated")); self.assertEqual(self.calls, [])
        wf(prfix.GATES, '{"no_fix_pr": {"923": "Praise 闸", "1178": "测试闸"}}')
        self.ctx["gates"] = prfix.load_gates()[0]
        rc, out, r = self.main("--qa", "1178", "--fp", A)
        self.assertEqual((rc, r["status"]), (1, "gated")); self.assertEqual(self.calls, [])
        rc, out, r = self.main("--next")                                         # nothing else to do -> idle
        self.assertEqual((rc, r["status"]), (0, "idle")); self.assertEqual(self.calls, [])

    def test_gate_reread_right_before_acting(self):
        self.fake_fix({"status": "pr_action_written", "action_key": "k"})
        wf(prfix.GATES, '{"no_fix_pr": {"1178": "刚加的闸"}}')        # gather() saw no gate; the file says otherwise
        rc, out, r = self.main("--next")
        self.assertEqual((rc, r["status"]), (1, "gated")); self.assertEqual(self.calls, [])

    def test_busy_75_when_panoptes_lock_held_or_low_memory(self):
        self.fake_fix({"status": "pr_action_written", "action_key": "k"})
        old = prfix.SHARE_WITH_PANOPTES; prfix.SHARE_WITH_PANOPTES = False   # legacy mode: yield to panoptes
        held = prfix.try_lock(prfix.QA_LOCK)
        try: rc, out, r = self.main("--next")
        finally: prfix.unlock(held); prfix.SHARE_WITH_PANOPTES = old
        self.assertEqual((rc, r["status"]), (75, "busy")); self.assertIn("panoptes", r["reason"]); self.assertEqual(self.calls, [])

    def test_shares_box_with_panoptes_by_default(self):   # Praise 10-23: memory is enough, run alongside panoptes
        self.fake_fix({"status": "pr_action_written", "action_key": "k"})
        self.assertTrue(prfix.SHARE_WITH_PANOPTES)
        held = prfix.try_lock(prfix.QA_LOCK)
        try: rc, out, r = self.main("--next")
        finally: prfix.unlock(held)
        self.assertEqual((rc, r["status"]), (0, "pr_action_written"))
        prfix.mem_gb = lambda: 2.0
        rc, out, r = self.main("--next")
        self.assertEqual((rc, r["status"]), (75, "busy")); self.assertIn("MemAvailable", r["reason"])

    def test_success_writes_ticket_result_and_lock_is_released(self):
        self.fake_fix({"status": "pr_action_written", "kind": None, "action_key": f"fixpr:1178:{A}"})
        rc, out, r = self.main()                                                  # no args = --next
        self.assertEqual((rc, r["status"], r["ticket"]), (0, "pr_action_written", f"1178:{A}"))
        self.assertTrue(out.strip().splitlines()[-1].startswith("RESULT "))
        self.assertEqual(json.loads(rf(f"{prfix.OUT}/last-result.json"))["status"], "pr_action_written")
        tk = prfix.load_tickets()["tickets"][f"1178:{A}"]
        self.assertEqual((tk["status"], tk["attempts"], tk["pid"]), ("pr_action_written", 1, None))
        self.assertEqual(r["pending_after"], 1)
        lk = prfix.try_lock(prfix.QA_LOCK); self.assertIsNotNone(lk); prfix.unlock(lk)  # released
        rc, out, r = self.main("--next")                                           # one ticket per run: the other finding next
        self.assertEqual(r["ticket"], f"1178:{B}")

    def test_transient_then_backoff_then_other_ticket(self):
        self.fake_fix({"status": "failed", "kind": "transient", "error": "npm ci rc=1"})
        rc, out, r = self.main("--next")
        self.assertEqual((rc, r["status"]), (1, "failed")); self.assertTrue(r["next_retry_at"])
        rc, out, r = self.main("--next")
        self.assertEqual(r["ticket"], f"1178:{B}")                                # A is backing off
        self.assertEqual([c[1] for c in self.calls], [A, B])

    def test_deterministic_is_parked_without_dm(self):
        self.fake_fix({"status": "failed", "kind": "deterministic", "error": "修了两次，回归测试仍不过"})
        rc, out, r = self.main("--next")
        self.assertEqual((rc, r["status"]), (1, "failed")); self.assertFalse([a for a in self.acts if a["type"] == "slack.dm"])   # fresh retry first
        with prfix.tickets_locked() as T: T["tickets"][f"1178:{A}"]["next_retry_at"] = None
        rc, out, r = self.main("--next")
        self.assertEqual((rc, r["status"]), (1, "parked")); self.assertFalse([a for a in self.acts if a["type"] == "slack.dm"])
        rc, out, r = self.main("--next"); self.assertNotEqual(r.get("ticket"), f"1178:{A}")         # paused: not picked again yet

    def test_sensitive_fix_is_the_one_dm(self):
        self.fake_fix({"status": "failed", "kind": "praise", "error": "修复要改 CI 配置 / 仓库规则 / 密钥相关的文件：.github/workflows/ci.yml"})
        rc, out, r = self.main("--next")
        self.assertEqual(r["status"], "needs_praise")
        dm = [a for a in self.acts if a["type"] == "slack.dm"]
        self.assertEqual(len(dm), 1); self.assertEqual((dm[0]["key"], dm[0]["to"]), (f"needs-praise:1178:{A}", "Praise"))
        self.assertIn("--allow-sensitive", dm[0]["text"])
        for bad in ("SHADOW", "NOT SENT", "<!--"): self.assertNotIn(bad, dm[0]["text"])

    def test_norepro_note_and_dm(self):   # 10-24 evening: contested -> parked, no note, no DM; agreed -> note, closed, no DM
        self.fake_fix({"status": "norepro", "kind": "norepro", "error": "模型判不可复现（NOREPRO）"})
        rc, out, r = self.main("--next")
        self.assertEqual((rc, r["status"]), (0, "parked")); self.assertFalse(os.listdir(prfix.NOTES))
        with prfix.tickets_locked() as T: T["tickets"][f"1178:{A}"]["next_retry_at"] = None
        self.fake_fix({"status": "norepro", "kind": "norepro", "error": "模型判不可复现（NOREPRO）", "confirmed": True})
        rc, out, r = self.main("--next")
        self.assertEqual((rc, r["status"]), (0, "norepro"))
        note = f"{prfix.NOTES}/1178-p1-{A[4:12]}-norepro.md"
        self.assertTrue(os.path.exists(note)); txt = rf(note)
        self.assertIn("待人工确认", txt.splitlines()[2]); self.assertIn("## NOREPRO", txt)       # full model output kept
        self.assertFalse([a for a in self.acts if a["type"] == "slack.dm"])
        v = verdicts(dict(self.ctx, notes=os.listdir(prfix.NOTES), tickets=prfix.load_tickets()["tickets"]))
        self.assertEqual(v[f"1178:{A}"]["state"], "claimed")                       # never picked again automatically

    def test_dead_running_ticket_becomes_transient_failure(self):
        os.makedirs(prfix.OUT, exist_ok=True)
        wf(f"{prfix.OUT}/tickets.json", json.dumps({"tickets": {f"1178:{A}": {"qa": 1178, "fp": A, "sev": "P1", "status": "running", "pid": 999999999, "attempts": 1}}}))
        self.fake_fix({"status": "pr_action_written", "action_key": "k"})
        rc, out, r = self.main("--next")
        self.assertEqual(r["ticket"], f"1178:{B}")
        tk = prfix.load_tickets()["tickets"][f"1178:{A}"]
        self.assertEqual(tk["status"], "failed"); self.assertIn("中断", tk["last_error"])

    def test_hard_stop_is_parked_without_dm(self):
        body = f"QA: #1178\n{A}"
        self.ctx["prs"] = [pr(1, "PraiseZhu", body=body, state="CLOSED"), pr(2, "PraiseZhu", body=body, state="CLOSED")]
        self.fake_fix({"status": "pr_action_written", "action_key": "k"})
        self.main("--next"); self.main("--next")
        t = prfix.load_tickets()["tickets"][f"1178:{A}"]
        self.assertEqual((t["status"], t["park_reason"]), ("parked", "规则上不自动修"))
        self.assertFalse([a for a in self.acts if a["type"] == "slack.dm"])
        rc, out, r = self.main("--qa", "1178", "--fp", A)                          # --qa cannot start a 3rd round either
        self.assertEqual((rc, r["status"]), (1, "stopped"))

    def test_pending_json_is_pure_json_on_stdout(self):
        rc, out, _ = self.main("--pending-json")
        rows = json.loads(out)
        self.assertEqual(rc, 0); self.assertEqual({r["fp"] for r in rows if r["reason_if_skipped"] is None}, {A, B})
        self.assertFalse(os.path.exists(f"{prfix.OUT}/tickets.json"))             # read-only

# ====================================================================== 6. worktree cleanup (temp git repos)
def sh(*cmd, cwd=None):
    r = subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True)
    if r.returncode: raise RuntimeError(f"{cmd}: {r.stderr}")
    return r.stdout

class GitTmp(Tmp):
    def setUp(self):
        super().setUp()
        self.origin = f"{self.tmp}/origin.git"; prfix.GIT = f"{self.tmp}/repo"
        sh("git", "init", "-q", "--bare", "-b", "main", self.origin)
        seed = f"{self.tmp}/seed"; os.makedirs(f"{seed}/src"); os.makedirs(f"{seed}/.githooks")
        wf(f"{seed}/src/foo.js", "module.exports = () => 1\n")
        wf(f"{seed}/.gitignore", "node_modules/\n")
        wf(f"{seed}/package.json", '{"name":"t"}\n'); wf(f"{seed}/package-lock.json", "{}\n")
        wf(f"{seed}/.githooks/pre-push", textwrap.dedent("""\
            #!/usr/bin/env bash
            echo "[preflight] 开始本地验证(tsc -> lint)..."
            echo "[preflight] 1/2 tsc -b..."
            echo "[preflight] 2/2 lint..."
            if [ -f FAIL_LINT ]; then echo "[preflight] ❌ lint 失败"; exit 1; fi
            if [ -f FAIL_UNIT ]; then echo " FAIL  src/far.test.js > times out"; echo "[preflight] ❌ test:unit 失败"; exit 1; fi
            if [ -n "${FAKE_FLAKE_MARK:-}" ] && [ ! -f "$FAKE_FLAKE_MARK" ]; then touch "$FAKE_FLAKE_MARK"; echo "[preflight] ❌ test:unit 失败"; exit 1; fi
            echo "[preflight] ✅ 本地验证通过,允许 push"
            """)); os.chmod(f"{seed}/.githooks/pre-push", 0o755)
        g = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
        sh("git", "init", "-q", "-b", "main", seed); sh(*g, "-C", seed, "add", "-A"); sh(*g, "-C", seed, "commit", "-q", "-m", "seed")
        sh("git", "-C", seed, "push", "-q", self.origin, "main")
        sh("git", "clone", "-q", self.origin, prfix.GIT)
        os.makedirs(prfix.WT_ROOT, exist_ok=True)
    def wt(self, path, branch=None, detach=False):
        sh("git", "-C", prfix.GIT, "worktree", "add", "-q", *(["--detach", path] if detach else ["-b", branch, path]), "HEAD")

class TestCleanup(GitTmp):
    def test_drop_worktree_removes_only_ours(self):
        wd = f"{prfix.WT_ROOT}/pi-wt-5-deadbeef"; self.wt(wd, "fix/qa-5-deadbeef")
        wf(f"{wd}/scratch.test.js", "x")                     # untracked leftovers of a failed run
        prfix.drop_worktree(wd, "fix/qa-5-deadbeef")
        self.assertFalse(os.path.exists(wd)); self.assertFalse(prfix.branch_exists("fix/qa-5-deadbeef"))
        self.assertNotIn(wd, {w["path"] for w in prfix.worktrees()})
        with self.assertRaises(ValueError): prfix.drop_worktree(f"{self.tmp}/elsewhere")
        with self.assertRaises(ValueError): prfix.drop_worktree(f"{prfix.WT_ROOT}/pi-wt-6-cafebabe", "feature/x")
        wd2 = f"{prfix.WT_ROOT}/pi-wt-7-cafebabe"; self.wt(wd2, "fix/qa-7-cafebabe")
        sh("git", "-C", wd2, "push", "-q", "-u", "origin", "fix/qa-7-cafebabe")     # pushed -> branch must survive
        prfix.drop_worktree(wd2, "fix/qa-7-cafebabe")
        self.assertTrue(prfix.branch_exists("fix/qa-7-cafebabe"))

    def test_stale_scan_and_cleanup_yes(self):
        self.wt(f"{prfix.WT_ROOT}/pi-wt-5-deadbeef", "fix/qa-5-deadbeef")             # delete
        self.wt(f"{prfix.WT_ROOT}/pi-wt-6", "fix/qa-6-abcdef12")                      # legacy name: delete
        self.wt(f"{prfix.WT_ROOT}/pi-wt-7-cafebabe", "fix/qa-7-cafebabe")             # pushed: keep
        sh("git", "-C", f"{prfix.WT_ROOT}/pi-wt-7-cafebabe", "push", "-q", "origin", "fix/qa-7-cafebabe")
        self.wt(f"{prfix.WT_ROOT}/wt-892", "fix/qa-892-changelog-short-viewport")     # human worktree: ignored
        self.wt(f"{prfix.WT_ROOT}/pi-wt-8-x", "feature/x")                            # not a prfix branch: keep
        os.makedirs(prfix.TMPROOT); self.wt(f"{prfix.TMPROOT}/QA1-mq1-aaaaaaaa", detach=True)   # dry-run leftover: delete
        wf(f"{prfix.TMPROOT}/QA1-mq1-aaaaaaaa.src.diff", "x")                       # stray file: delete
        sh("git", "-C", prfix.GIT, "branch", "fix/qa-9-0badf00d")                     # orphan prfix branch: delete
        rows = {(r["path"] or r["branch"]): r for r in prfix.stale_scan(tickets={})}
        act = lambda k: rows[k]["act"]
        self.assertEqual(act(f"{prfix.WT_ROOT}/pi-wt-5-deadbeef"), "delete")
        self.assertEqual(act(f"{prfix.WT_ROOT}/pi-wt-6"), "delete")
        self.assertEqual(act(f"{prfix.WT_ROOT}/pi-wt-7-cafebabe"), "keep")
        self.assertEqual(act(f"{prfix.WT_ROOT}/pi-wt-8-x"), "keep")
        self.assertNotIn(f"{prfix.WT_ROOT}/wt-892", rows)
        self.assertEqual(act(f"{prfix.TMPROOT}/QA1-mq1-aaaaaaaa"), "delete")
        self.assertEqual(act(f"{prfix.TMPROOT}/QA1-mq1-aaaaaaaa.src.diff"), "delete")
        self.assertEqual(act("fix/qa-9-0badf00d"), "delete")
        with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(prfix.cleanup_stale(False), 0)   # list only: nothing removed
        self.assertTrue(os.path.exists(f"{prfix.WT_ROOT}/pi-wt-5-deadbeef"))
        with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(prfix.cleanup_stale(True), 0)
        left = {w["path"] for w in prfix.worktrees()}
        self.assertNotIn(f"{prfix.WT_ROOT}/pi-wt-5-deadbeef", left); self.assertNotIn(f"{prfix.WT_ROOT}/pi-wt-6", left)
        self.assertNotIn(f"{prfix.TMPROOT}/QA1-mq1-aaaaaaaa", left); self.assertEqual(os.listdir(prfix.TMPROOT), [])
        self.assertIn(f"{prfix.WT_ROOT}/pi-wt-7-cafebabe", left); self.assertIn(f"{prfix.WT_ROOT}/wt-892", left)
        for b in ("fix/qa-5-deadbeef", "fix/qa-6-abcdef12", "fix/qa-9-0badf00d"): self.assertFalse(prfix.branch_exists(b), b)
        for b in ("fix/qa-7-cafebabe", "fix/qa-892-changelog-short-viewport", "feature/x"): self.assertTrue(prfix.branch_exists(b), b)

class TestFixOneEndToEnd(GitTmp):
    """fix_one in a temp repo: fake model (writes files), fake npx/npm, the repo's own fake pre-push hook."""
    def setUp(self):
        super().setUp()
        self.fakebin("npm", "exit ${FAKE_NPM_RC:-0}\n")
        self.fakebin("npx", """
            # fake vitest: the regression test passes only when src/foo.js carries FIXED. With BREAKS_OTHER in the repo the fix
            # also breaks the old src/other.test.js (like #1188's tick.test.mjs) until that test carries OTHER_OK.
            [ "$1" = vitest ] || exit 9
            other() { if [ -f BREAKS_OTHER ] && grep -q FIXED src/foo.js && ! grep -q OTHER_OK src/other.test.js; then
                        printf '\033[31m FAIL \033[39m src/other.test.js > old behaviour\n'; return 1; fi; echo " ✓ other passed"; }
            if [ "$2" = related ]; then echo "related $*" >> "$FAKE_LOG"; other; exit $?; fi
            [ "$2" = run ] || exit 9
            case "$*" in *spy.test.js*)
                if grep -q "test('bug'" src/spy.test.js && grep -q LEAK src/spy.test.js; then echo " FAIL  src/spy.test.js > bug"; exit 1; fi
                echo " ✓ spy passed"; exit 0;; esac
            case "$*" in *other.test.js*)
                if grep -q "test('bug'" src/other.test.js && ! grep -q FIXED src/foo.js; then echo " FAIL  src/other.test.js > bug"; exit 1; fi
                other; exit $?;; esac
            if grep -q WEAK src/foo.test.js 2>/dev/null; then echo " ✓ 1 passed (weak test)"; exit 0; fi
            if grep -q FIXED src/foo.js; then echo " ✓ 1 passed"; exit 0; else printf '\033[41m FAIL \033[49m src/foo.test.js > bug\n'; exit 1; fi
        """)
        os.environ["FAKE_LOG"] = f"{self.tmp}/npx.log"
        self.orig = {k: getattr(prfix, k) for k in ("pi", "predict_pr", "record_actions", "orig_pr_diff")}
        self.acts = []; prfix.record_actions = lambda job, day, acts: self.acts.extend(acts)
        prfix.predict_pr = lambda *a, **k: 4242
        prfix.orig_pr_diff = lambda n, f=None: "diff --git a/src/foo.js b/src/foo.js\n+module.exports = () => 1"
        self.model = {}; self.prompts = []; self.sessions = []
        self.judge = "AGREE\nsrc/foo.js:1 is fine"
        self.rulings = {"R2": "UNRELATED\nsrc/far.test.js is not touched; it times out on main too", "R3": "LEGIT\nthe old case asserted the bug"}
        def fake_pi(tier, cwd, prompt, session, timeout=600, write=False, web=False, **kw):
            if tier == prfix.JUDGE_TIER:
                k = re.search(r"ruling (R\d)", prompt).group(1); self.prompts.append((f"judge-{k}", prompt))
                return (self.judge if k == "R1" else self.rulings[k]), "tj"
            step = re.match(r"\[step (\S+)\]", prompt).group(1); self.prompts.append((step, prompt)); self.sessions.append(session)
            beh = self.model.get(step, self.model.get(step.rstrip("0123456789"), "ok"))
            if callable(beh): return beh(cwd, prompt)
            if beh == "raise": raise RuntimeError("pi heavy rc=130 interrupted")
            if step.startswith("repro"):
                if beh == "norepro": return "## NOREPRO\nsrc/foo.js:1 is fine\nROOT CAUSE: none", "t1"
                if beh == "nothing": return "I looked around.", "t1"
                if beh == "weak": wf(f"{cwd}/src/foo.test.js", "test('WEAK', () => {})\n"); return "added test\nROOT CAUSE src/foo.js:1", "t1"
                if beh == "into_spy": APPEND(f"{cwd}/src/spy.test.js", "test('bug', () => {})\n"); return "added case\nROOT CAUSE src/spy.test.js:1", "t1"
                if beh == "into_existing": APPEND(f"{cwd}/src/other.test.js", "test('bug', () => {})\n"); return "added case\nROOT CAUSE src/foo.js:1", "t1"
                wf(f"{cwd}/src/foo.test.js", "test('bug', () => {})\n")
                APPEND(f"{cwd}/src/foo.js", "// stray source edit in TASK 1 (must be reverted)\n")
                return "added test\nROOT CAUSE src/foo.js:1\nTEST FILE src/foo.test.js", "t1"
            if step.startswith("fix"):
                if beh == "fair_test_edit": wf(f"{cwd}/src/other.test.js", "it('old', () => { expect(2) }) // OTHER_OK\n")
                if beh == "fix_spy":
                    wf(f"{cwd}/src/spy.test.js", rf(f"{cwd}/src/spy.test.js").replace("LEAK", "RESTORED")); return "ROOT CAUSE src/spy.test.js:1 helper leaked", "t2"
                if beh == "fair_existing": wf(f"{cwd}/src/other.test.js", "it('old', () => { expect(2) }) // OTHER_OK\ntest('bug', () => {})\n")
                if beh == "drop_bug_case": wf(f"{cwd}/src/other.test.js", "it('old', () => { expect(2) }) // OTHER_OK\n")
                if beh == "weaken_old": wf(f"{cwd}/src/other.test.js", "it.skip('old', () => { expect(2) }) // OTHER_OK\n")
                if beh != "nofix": wf(f"{cwd}/src/foo.js", "module.exports = () => 2 // FIXED\n")
                if self.model.get("repro") not in ("into_existing", "into_spy"): wf(f"{cwd}/src/foo.test.js", "test.skip('weakened', () => {})\n")      # must be restored
                return ("TEST CHANGES: src/other.test.js asserted the old constant\n\n" if beh == "fair_test_edit" else "") + \
                    "ROOT CAUSE src/foo.js:1 returned 1\nThe function returned the wrong constant.", "t2"
            if step.startswith("prepush"):
                if beh == "fix_lint" and os.path.exists(f"{cwd}/FAIL_LINT"): os.remove(f"{cwd}/FAIL_LINT")
                if beh == "fix_unit" and os.path.exists(f"{cwd}/FAIL_UNIT"): os.remove(f"{cwd}/FAIL_UNIT")
                return "ROOT CAUSE lint", "t4"
            if step.startswith("docs"):
                os.makedirs(f"{cwd}/docs/pr/4242", exist_ok=True)
                for k in ("review", "qa"): wf(f"{cwd}/docs/pr/4242/{k}.md", f"---\nschema: mivo-pr-handoff/v1\nkind: {k}\npr: 4242\n---\n# PR #4242\n")
                wf(f"{cwd}/README.extra", "docs step touched another file")                  # must be dropped
                return "docs written", "t3"
            raise AssertionError(step)
        prfix.pi = fake_pi
        self.c = {"qa": 1178, "fp": A, "sev": "P1", "title": "带错误码的派发传输故障会被误判", "source": "report", "author": "PraiseZhu",
                  "finding": finding(A, file="src/foo.js", line=1), "row": None, "round": 1}
    def tearDown(self):
        for k, v in self.orig.items(): setattr(prfix, k, v)
        super().tearDown()
    def run_fix(self, dry=False):
        with contextlib.redirect_stdout(io.StringIO()):
            return prfix.fix_one(dict(self.c), argparse.Namespace(dry_run=dry, base=None), "2026-10-23")
    wd = property(lambda self: f"{prfix.WT_ROOT}/pi-wt-1178-{A[4:12]}")
    br = f"fix/qa-1178-{A[4:12]}"

    def seed_commit(self, files):
        seed = f"{self.tmp}/seed"; g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", seed]
        for k, v in files.items(): os.makedirs(os.path.dirname(f"{seed}/{k}") or seed, exist_ok=True); wf(f"{seed}/{k}", v)
        sh(*g, "add", "-A"); sh(*g, "commit", "-q", "-m", "seed more"); sh(*g, "push", "-q", self.origin, "main")

    # ---- 10-24: the harness's checks go back to the model instead of ending the run
    def test_old_test_broken_by_the_fix_is_fed_back_and_a_fair_update_lands(self):   # #1188
        self.seed_commit({"BREAKS_OTHER": "1", "src/other.test.js": "it('old', () => { expect(1) })\n"})
        self.model["fix2"] = "fair_test_edit"
        out, res = self.run_fix()
        self.assertEqual(out["status"], "pr_action_written", out)
        fix2 = dict(self.prompts)["fix2"]
        self.assertIn("src/other.test.js", fix2); self.assertIn("TEST CHANGES", fix2)
        self.assertNotIn("\x1b", fix2)                                                   # no colour codes in what the model reads
        files = sh("git", "-C", self.wd, "show", "--name-only", "--format=", "HEAD").split()
        self.assertIn("src/other.test.js", files)
        body = rf(self.acts[-1]["patch"][:-6] + ".md")
        self.assertIn("改了已有测试的期望", body); self.assertIn("asserted the old constant", body)
        self.assertIn("test('bug'", rf(f"{self.wd}/src/foo.test.js"))                     # the new regression test stays locked

    def test_finding_in_test_code_is_fixed_in_test_code(self):   # QA1198 rerun 10-24
        self.seed_commit({"src/spy.test.js": "function runCli() { /* LEAK */ }\nit('old', () => { expect(runCli()) })\n"})
        self.c = dict(self.c, finding=finding(A, file="src/spy.test.js", line=1))
        self.model.update(repro="into_spy", fix="fix_spy")
        out, res = self.run_fix()
        self.assertEqual(out["status"], "pr_action_written", out)
        self.assertTrue(any(k == "撤掉修复：测试失败" and ok for k, ok, _ in res["steps"]))   # back to the repro version -> red
        self.assertIn("The bug is in test code", dict(self.prompts)["fix1"])
        a = self.acts[-1]; body = rf(a["patch"][:-6] + ".md")
        self.assertTrue(a["title"].startswith("fix"), a["title"]); self.assertIn("问题本身出在测试代码里", body)
        final = rf(f"{self.wd}/src/spy.test.js"); self.assertIn("RESTORED", final); self.assertIn("test('bug'", final)

    def test_regression_case_in_an_existing_file_locks_only_its_lines(self):   # QA1188 rerun 10-24
        self.seed_commit({"BREAKS_OTHER": "1", "src/other.test.js": "it('old', () => { expect(1) })\n"})
        self.model.update(repro="into_existing", fix2="drop_bug_case", fix3="fair_existing")
        out, res = self.run_fix()
        self.assertEqual(out["status"], "pr_action_written", out)
        self.assertIn("reverted", dict(self.prompts)["fix3"])                      # dropping the repro case was undone and told
        final = rf(f"{self.wd}/src/other.test.js")
        self.assertIn("test('bug'", final); self.assertIn("OTHER_OK", final)        # repro case kept, old expectation updated
        self.assertIn("src/other.test.js", rf(self.acts[-1]["patch"][:-6] + ".md").split("改了已有测试的期望")[1][:200])

    def test_weakened_old_test_is_reverted_and_the_model_told(self):
        self.seed_commit({"BREAKS_OTHER": "1", "src/other.test.js": "it('old', () => { expect(1) })\n"})
        self.model.update(fix2="weaken_old", fix3="fair_test_edit")
        out, _ = self.run_fix()
        self.assertEqual(out["status"], "pr_action_written", out)
        fix3 = dict(self.prompts)["fix3"]; self.assertIn("weaken", fix3); self.assertIn("skip", fix3)
        self.assertNotIn("it.skip", rf(f"{self.wd}/src/other.test.js"))

    def test_fix_rounds_run_out_then_deterministic(self):
        self.seed_commit({"BREAKS_OTHER": "1", "src/other.test.js": "it('old', () => { expect(1) })\n"})
        out, _ = self.run_fix(); self._assert_cleaned(out, "failed", "deterministic")
        self.assertIn(f"修了 {prfix.FIX_ROUNDS} 轮", out["error"]); self.assertIn("other.test.js", out["error"])

    def test_prepush_red_goes_back_to_the_model(self):
        self.seed_commit({"FAIL_LINT": "1"})
        self.model["prepush1-"] = "fix_lint"
        out, res = self.run_fix()
        self.assertEqual(out["status"], "pr_action_written", out)
        steps = [k for k, ok, _ in res["steps"] if k.startswith("推送前检查")]
        self.assertEqual(len(steps), 2, steps)
        self.assertIn("lint", dict(self.prompts)["prepush1-1"])
        files = sh("git", "-C", self.wd, "show", "--name-only", "--format=", "HEAD").split()
        self.assertIn("FAIL_LINT", files)                                                  # the model's fix is in the one commit
        self.assertEqual(sh("git", "-C", self.wd, "rev-list", "--count", "HEAD", "^origin/main").strip(), "1")

    def test_flaky_prepush_red_the_model_calls_unrelated_is_rerun_once(self):   # QA1198 10-24
        os.environ["FAKE_FLAKE_MARK"] = f"{self.tmp}/flaked"
        out, res = self.run_fix()
        self.assertEqual(out["status"], "pr_action_written", out)
        self.assertTrue(any(k.startswith("模型判断与本次改动无关") for k, _, _ in res["steps"]))
        self.assertEqual(len([k for k, _, _ in res["steps"] if k.startswith("推送前检查")]), 2)

    def test_norepro_second_opinion(self):   # 10-24: a NOREPRO two models agree on is closed without a DM
        self.model["repro"] = "norepro"
        out, res = self.run_fix(); self._assert_cleaned(out, "norepro"); self.assertTrue(out["confirmed"])
        self.assertIn("is fine", res["judge"])
        self.assertIn("try once more", dict(self.prompts)["repro2"])               # G1: the fixing model retries before any ruling
        self.prompts.clear(); out, res = self.run_fix()                            # same question on the same code: the verdict is reused
        self._assert_cleaned(out, "norepro"); self.assertTrue(out["confirmed"]); self.assertNotIn("judge-R1", dict(self.prompts))
        os.remove(f"{prfix.OUT}/rulings.json")                                     # as if a new ticket
        self.prompts.clear(); self.judge = "DISAGREE\nsrc/foo.js:1 returns 1, the report is right"; self.model["repro3"] = "ok"
        out, res = self.run_fix()
        self.assertEqual(out["status"], "pr_action_written", out)                  # the judge's reasoning got it reproduced
        self.assertIn("DISAGREES", dict(self.prompts)["repro3"])
        os.remove(f"{prfix.OUT}/rulings.json")
        self.prompts.clear(); self.acts.clear(); self.model["repro3"] = "norepro"
        out, res = self.run_fix(); self._assert_cleaned(out, "norepro"); self.assertFalse(out["confirmed"])   # contested: a human

    def test_ruling_r2_unrelated_prepush_failure_opens_the_draft_anyway(self):
        self.seed_commit({"FAIL_UNIT": "1"})
        out, res = self.run_fix()
        self.assertEqual(out["status"], "pr_action_written", out)
        self.assertIn("judge-R2", dict(self.prompts)); self.assertIn("src/far.test.js", dict(self.prompts)["judge-R2"])
        body = rf(self.acts[-1]["patch"][:-6] + ".md"); self.assertIn("裁决", body); self.assertIn("以 GitHub CI 为准", body)

    def test_ruling_r2_related_gets_one_more_round(self):
        self.seed_commit({"FAIL_UNIT": "1"}); self.rulings["R2"] = "RELATED\nyour change makes far.test.js slow"
        self.model["prepush-ruled-"] = "fix_unit"
        out, res = self.run_fix()
        self.assertEqual(out["status"], "pr_action_written", out)
        self.assertIn("IS caused by your change", dict(self.prompts)["prepush-ruled-1"])

    def test_ruling_r3_on_old_test_edits(self):
        self.seed_commit({"BREAKS_OTHER": "1", "src/other.test.js": "it('old', () => { expect(1) })\n"})
        self.model["fix2"] = "fair_test_edit"
        out, res = self.run_fix()
        self.assertEqual(out["status"], "pr_action_written", out)
        self.assertIn("judge-R3", dict(self.prompts)); self.assertIn("认定正当", rf(self.acts[-1]["patch"][:-6] + ".md"))
        os.remove(f"{prfix.OUT}/rulings.json")                                     # R3 is once per ticket: as if a new ticket
        self.prompts.clear(); self.acts.clear(); self.rulings["R3"] = "REJECT\nthe old case was right"
        out, res = self.run_fix(); self._assert_cleaned(out, "failed", "deterministic")      # the source alone cannot pass: a human
        self.assertIn("NOT legitimate", dict(self.prompts)["fix-ruled-1"])

    def test_one_pi_session_per_run(self):
        self.run_fix(); first = set(self.sessions)
        self.assertEqual(len(first), 1, first)
        self.sessions.clear(); import time as _t; _t.sleep(1.1); self.run_fix()
        self.assertTrue(set(self.sessions).isdisjoint(first))                               # a retry never resumes an older run

    def test_every_step_opens_with_the_repos_ci_rules_brief(self):   # 10-25 Praise: rules known before the fix, not after a red check
        self.seed_commit({"FAIL_LINT": "1"}); self.model["prepush1-"] = "fix_lint"
        out, _ = self.run_fix(); self.assertEqual(out["status"], "pr_action_written", out)
        steps = [k for k, _ in self.prompts if not k.startswith("judge-")]
        for want in ("repro", "fix1", "prepush1-1", "docs"): self.assertIn(want, steps)
        for k, p in self.prompts:
            if k.startswith("judge-"): continue
            head = p.split("\n", 2)
            self.assertEqual(head[0], f"[step {k}]")
            self.assertIn("CI rules brief: xindong/mivo-canvas-plugin", head[1], k)
            self.assertNotIn("<!--", p.split("---", 1)[0], k)                         # the source note stays in the file

    def test_repro_gets_one_more_try(self):
        self.model.update(repro="nothing", repro2="ok")
        out, _ = self.run_fix(); self.assertEqual(out["status"], "pr_action_written", out)
        self.assertIn("repro2", dict(self.prompts))
        self.prompts.clear(); self.model.update(repro="weak", repro2="ok")
        out, _ = self.run_fix(); self.assertEqual(out["status"], "pr_action_written", out)
        self.assertIn("PASS on the current code", dict(self.prompts)["repro2"])
        self.prompts.clear(); self.acts.clear(); self.model.update(repro="weak", repro2="weak")
        out, _ = self.run_fix(); self._assert_cleaned(out, "norepro")

    def test_context_carries_the_tested_prs_diff(self):
        self.run_fix(); self.assertIn("Diff of the merged PR #1178", dict(self.prompts)["repro"])

    def test_success_with_janitor_enforcing_drops_worktree_keeps_patch(self):
        wf(_JCONF, '{"enforce": true}')
        try: out, res = self.run_fix()
        finally: wf(_JCONF, '{"enforce": false}')
        self.assertEqual(out["status"], "pr_action_written", out)
        self.assertFalse(os.path.isdir(self.wd)); self.assertFalse(prfix.branch_exists(self.br))   # outbound works from its own clone
        a = self.acts[-1]
        self.assertTrue(os.path.getsize(a["patch"]) > 0)
        self.assertIn("src/foo.js", rf(a["patch"]))

    def test_success_keeps_worktree_and_writes_patch_body_action(self):
        out, res = self.run_fix()
        self.assertEqual(out["status"], "pr_action_written", out)
        self.assertTrue(os.path.isdir(self.wd)); self.assertTrue(prfix.branch_exists(self.br))
        log = sh("git", "-C", self.wd, "log", "-1", "--format=%an <%ae>|%s")
        self.assertEqual(log.split("|")[0], "Praise <zhuzan@xd.com>")
        files = sh("git", "-C", self.wd, "show", "--name-only", "--format=", "HEAD").split()
        self.assertEqual(sorted(files), ["docs/pr/4242/qa.md", "docs/pr/4242/review.md", "src/foo.js", "src/foo.test.js"])
        self.assertIn("test('bug'", rf(f"{self.wd}/src/foo.test.js"))          # weakened test was restored
        a = self.acts[-1]
        self.assertEqual((a["key"], a["type"], a["body_must_contain"], a["docs_dir_predicted"]), (f"fixpr:1178:{A}", "gh.pr.create(draft)", A, "docs/pr/4242"))
        body = rf(a["patch"][:-6] + ".md")
        for need in ("QA: #1178", A, "## 这次改了什么", "## 怎么验证的", "## 风险", "### 提交前检查", "根因：src/foo.js:1",
                     "npx vitest run src/foo.test.js", ".githooks/pre-push", "2/2 通过", "mivo-platform-evidence"):
            self.assertIn(need, body)
        for bad in ("<!--", "SHADOW", "NOT SENT"): self.assertNotIn(bad, body)
        sh("git", "-C", prfix.GIT, "apply", "--check", a["patch"])                       # the patch applies on base
        self.assertTrue(os.path.exists(res["evidence"]))

    def _assert_cleaned(self, out, status, kind=None):
        self.assertEqual(out["status"], status, out)
        if kind: self.assertEqual(out["kind"], kind)
        self.assertFalse(os.path.exists(self.wd)); self.assertFalse(prfix.branch_exists(self.br))
        self.assertNotIn(self.wd, {w["path"] for w in prfix.worktrees()})
        self.assertFalse([a for a in self.acts if a["type"].startswith("gh.pr.create")])

    def test_model_failure_is_transient_and_cleans_up(self):
        self.model["fix1"] = "raise"
        out, res = self.run_fix(); self._assert_cleaned(out, "failed", "transient")
        self.assertIn("rc=130", out["error"]); self.assertIn("rc=130", rf(res["evidence"]))
        self.assertTrue(os.path.exists(res["evidence"][:-3] + ".patch"))

    def test_npm_failure_is_transient_and_cleans_up(self):
        os.environ["FAKE_NPM_RC"] = "20"
        out, _ = self.run_fix(); self._assert_cleaned(out, "failed", "transient"); self.assertIn("rc=20", out["error"])

    def test_fix_still_red_is_deterministic_and_cleans_up(self):
        self.model["fix"] = "nofix"
        out, _ = self.run_fix(); self._assert_cleaned(out, "failed", "deterministic")

    def test_norepro_cleans_up_and_keeps_full_text(self):
        self.model["repro"] = "norepro"
        out, res = self.run_fix(); self._assert_cleaned(out, "norepro")
        self.assertIn("## NOREPRO", rf(res["evidence"]))

    def test_preflight_red_is_deterministic_no_action(self):
        self.rulings["R2"] = "RELATED\nlint fails on the changed file"
        sh("git", "-C", prfix.GIT, "fetch", "-q")
        wf(f"{self.tmp}/seed/FAIL_LINT", "1")
        g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", f"{self.tmp}/seed"]
        sh(*g, "add", "-A"); sh(*g, "commit", "-q", "-m", "lint fails"); sh(*g, "push", "-q", self.origin, "main")
        out, res = self.run_fix(); self._assert_cleaned(out, "failed", "deterministic")
        self.assertIn("lint", out["error"])

    def test_sigterm_during_fix_cleans_up(self):
        import signal, time
        self.model["fix1"] = "sigterm"
        orig_pi = prfix.pi
        def pi_term(tier, cwd, prompt, session, **kw):
            if prompt.startswith("[step fix1]"): os.kill(os.getpid(), signal.SIGTERM); time.sleep(2)
            return orig_pi(tier, cwd, prompt, session, **kw)
        prfix.pi = pi_term
        old = signal.signal(signal.SIGTERM, prfix._on_term)
        try: out, res = self.run_fix()
        finally: signal.signal(signal.SIGTERM, old)
        self._assert_cleaned(out, "failed", "transient"); self.assertIn("信号", out["error"])

    def test_leftover_of_same_ticket_no_longer_blocks(self):
        self.wt(self.wd, self.br)                                    # the old code stopped forever on "worktree exists"
        out, _ = self.run_fix()
        self.assertEqual(out["status"], "pr_action_written", out)

    def test_dry_run_detached_and_removed(self):
        out, res = self.run_fix(dry=True)
        self.assertEqual(out["status"], "dry_ok", out)
        self.assertFalse(os.path.exists(f"{prfix.TMPROOT}/QA1178-{A[:12]}")); self.assertFalse(prfix.branch_exists(self.br))
        self.assertEqual(self.acts, [])
        self.assertTrue(res["evidence"].endswith("-dry.md"))

# ====================================================================== 6b. the repo's own checkers (copied read-only from the live repo)
LIVE = "/workspace/mivo-canvas-plugin"
REPO_FILES = ("scripts/pr-handoff/check.mjs", ".github/scripts/pr-handoff-gate.cjs", "docs/pr/_template/review.md", "docs/pr/_template/qa.md",
              "agent-use/docs/pr-rules.json", "scripts/ci/platform-risk.mjs")
@unittest.skipUnless(all(os.path.exists(f"{LIVE}/{f}") for f in REPO_FILES) and shutil.which("node"), "mivo-canvas-plugin checkers not available")
class TestRepoScripts(Tmp):
    def setUp(self):
        super().setUp()
        import re
        self.wd = f"{self.tmp}/repo"; g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", self.wd]
        for f in REPO_FILES:
            os.makedirs(os.path.dirname(f"{self.wd}/{f}"), exist_ok=True); shutil.copy(f"{LIVE}/{f}", f"{self.wd}/{f}")
        os.makedirs(f"{self.wd}/cindyplugin/src/agent"); wf(f"{self.wd}/cindyplugin/src/agent/foo.ts", "export const f = () => 1\n")
        sh("git", "init", "-q", "-b", "main", self.wd); sh(*g, "add", "-A"); sh(*g, "commit", "-q", "-m", "base")
        self.base = sh("git", "-C", self.wd, "rev-parse", "HEAD").strip()
        os.makedirs(f"{self.wd}/docs/pr/4242")
        for k in ("review", "qa"):
            t = rf(f"{self.wd}/docs/pr/_template/{k}.md").replace("<PR号>", "4242")
            t = re.sub(r"<是/否>", "否", t); t = re.sub(r"<static / unit[^>]*>", "unit", t); t = re.sub(r"<PASS / FAIL[^>]*>", "PASS", t)
            t = re.sub(r"<!--.*?-->", "", t, flags=re.S); t = re.sub(r"<[^<>\n]{1,80}>", "无", t)
            wf(f"{self.wd}/docs/pr/4242/{k}.md", t)
        wf(f"{self.wd}/cindyplugin/src/agent/foo.ts", "export const f = (e) => e.pointerId ? 2 : 1\n")
        wf(f"{self.wd}/cindyplugin/src/agent/foo.test.ts", "import { f } from './foo'\ntest('x', () => {})\n")
        sh(*g, "add", "-A"); sh(*g, "commit", "-q", "-m", "fix")
        self.head = sh("git", "-C", self.wd, "rev-parse", "HEAD").strip()

    def test_handoff_check_with_repo_script(self):
        ok, out = prfix.handoff_check(self.wd, 4242, self.base)
        self.assertTrue(ok, out)
        wf(f"{self.wd}/docs/pr/4242/qa.md", rf(f"{self.wd}/docs/pr/4242/qa.md").replace("pr: 4242", "pr: 4241"))
        ok, out = prfix.handoff_check(self.wd, 4242, self.base)
        self.assertFalse(ok); self.assertIn("FRONTMATTER_INVALID", out)

    def test_platform_evidence_accepted_by_repo_evaluator(self):
        r = prfix.platform_eval(self.wd, self.base, self.head, "no block", True)
        self.assertTrue(r["requiresWindowsEvidence"]); self.assertIn("input", r["risks"])
        c = {"qa": 1178, "fp": A, "sev": "P1", "title": "t", "finding": finding(A, file="cindyplugin/src/agent/foo.ts", line=1)}
        res = {"root": "ROOT CAUSE cindyplugin/src/agent/foo.ts:1\nexpl", "pred_pr": 4242, "rc_repro": 1, "rc_revert": 1}
        pf = {"steps": ["tsc -b", "lint"], "passed": 2, "total": 2, "seconds": 61}
        body = prfix.pr_body(c, res, ["cindyplugin/src/agent/foo.test.ts"], ["cindyplugin/src/agent/foo.ts"], pf, True, None,
                             prfix.platform_block(r["risks"], ["cindyplugin/src/agent/foo.test.ts"]))
        r2 = prfix.platform_eval(self.wd, self.base, self.head, body, True)
        self.assertEqual([e for e in r2["errors"] if e != prfix.TRUSTED_PROOF_ERR], [])   # only the CI-side proof remains
        rules = json.loads(rf(f"{self.wd}/agent-use/docs/pr-rules.json"))
        for sec in rules["bugfixSections"] + rules.get("checklistSectionNames", []): self.assertIn(sec, body)

# ====================================================================== 7. outbound snippet (skipped when not shipped next to bin/)
SNIP = os.path.join(os.path.dirname(HERE), "outbound-hfixpr-snippet.py")
@unittest.skipUnless(os.path.exists(SNIP), "outbound-hfixpr-snippet.py not present")
class TestOutboundSnippet(GitTmp):
    def load(self):
        spec = importlib.util.spec_from_file_location("snip", SNIP); m = importlib.util.module_from_spec(spec)
        m.BASE = self.tmp; m.GATES = prfix.GATES; spec.loader.exec_module(m); m.GATES = prfix.GATES; return m

    def test_gate_reason(self):
        m = self.load()
        self.assertIn("923", m.fixpr_gate_reason({"key": f"fixpr:923:{A}", "body_must_contain": A}))   # file missing -> default
        wf(prfix.GATES, '{"no_fix_pr": {"1178": "闸"}}')
        self.assertEqual(m.fixpr_gate_reason({"key": f"fixpr:1178:{A}", "qa": 1178}), "闸")
        self.assertIsNone(m.fixpr_gate_reason({"key": f"fixpr:923:{A}", "body_must_contain": "QA: #923"}))
        wf(prfix.GATES, "{bad")
        self.assertIn("unreadable", m.fixpr_gate_reason({"key": f"fixpr:1:{A}"}))
        self.assertEqual(m.fixpr_real_number("https://github.com/xindong/mivo-canvas-plugin/pull/1240\n"), 1240)

    def test_docs_rename_same_branch_plain_push(self):
        m = self.load(); d = prfix.GIT; br = "qa-fix/fixpr-1178-x"
        g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", d]
        sh(*g, "checkout", "-q", "-b", br); os.makedirs(f"{d}/docs/pr/1231")
        for k in ("review", "qa"): wf(f"{d}/docs/pr/1231/{k}.md", f"---\nschema: mivo-pr-handoff/v1\nkind: {k}\npr: 1231\n---\n\n# PR #1231 交接\n")
        sh(*g, "add", "-A"); sh(*g, "commit", "-q", "-m", "fix"); sh(*g, "push", "-q", "-u", "origin", br)
        sh("git", "-C", d, "config", "user.name", "t"); sh("git", "-C", d, "config", "user.email", "t@t")
        a = {"docs_dir_predicted": "docs/pr/1231", "pred_pr": 1231}
        self.assertEqual(m.fixpr_docs_rename(d, br, a, 1231), "docs/pr/1231: prediction was right")
        msg = m.fixpr_docs_rename(d, br, a, 1240)
        self.assertIn("pushed", msg)
        remote = sh("git", "--git-dir", self.origin, "ls-tree", "-r", "--name-only", br).split()
        self.assertIn("docs/pr/1240/review.md", remote); self.assertNotIn("docs/pr/1231/review.md", remote)
        txt = sh("git", "--git-dir", self.origin, "show", f"{br}:docs/pr/1240/qa.md")
        self.assertIn("pr: 1240", txt); self.assertIn("# PR #1240", txt); self.assertNotIn("1231", txt)
        self.assertEqual(m.fixpr_docs_rename(d, br, a, 1240), "docs/pr/1240: already renamed")       # idempotent
        log = sh("git", "--git-dir", self.origin, "log", "--format=%s", br).splitlines()
        self.assertEqual(len(log), 3)   # seed, fix, rename: no rewrite of history (no force)


class TestCiBrief(Tmp):
    """10-25: each target repo's CI rules brief (conf/ci-rules/<repo>.md) heads every model step; missing -> logged, not fatal."""
    def setUp(self):
        super().setUp(); self.orig = (prfix.CI_RULES, dict(prfix._BRIEFS)); prfix._BRIEFS.clear()
    def tearDown(self):
        prfix.CI_RULES = self.orig[0]; prfix._BRIEFS.clear(); prfix._BRIEFS.update(self.orig[1]); super().tearDown()
    def test_shipped_briefs_exist_short_and_name_their_sources(self):
        for name in ("mivo-canvas-plugin", "cindy"):
            raw = rf(f"{prfix.PI_CODE}/conf/ci-rules/{name}.md")
            self.assertLessEqual(len(raw.splitlines()), 60, name); self.assertIn("Sources", raw)
        self.assertIn("makecindy/cindy", prfix.ci_brief("makecindy/cindy"))
        self.assertIn(".githooks/pre-push", prfix.ci_brief())                             # REPO = the mivo plugin repo
    def test_brief_heads_the_prompt_and_drops_comments(self):
        prfix.CI_RULES = self.tmp; wf(f"{self.tmp}/demo.md", "<!-- source: x -->\n# demo rules\n- run lint\n")
        p = prfix.step_prompt("fix1", "TASK 2: fix it", repo="org/demo")
        self.assertEqual(p, "[step fix1]\n# demo rules\n- run lint\n\n---\n\nTASK 2: fix it")
    def test_missing_brief_is_logged_and_the_prompt_goes_without_it(self):
        prfix.CI_RULES = f"{self.tmp}/nope"; buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            p = prfix.step_prompt("repro", "TASK 1", repo="org/none"); prfix.step_prompt("fix1", "TASK 2", repo="org/none")
        self.assertEqual(p, "[step repro]\nTASK 1")
        self.assertEqual(buf.getvalue().count("ci rules brief missing for none"), 1)        # once per run, not per step
    def test_every_model_step_in_prfix_goes_through_step_prompt(self):   # repro / fix / prepush / docs / revise / merge rounds
        src = rf(os.path.join(BIN, "prfix.py"))
        calls = re.findall(r"pi\(TIER, wd, ([^,]+),", src)
        self.assertEqual(len(calls), 3, calls)                                           # fix_one, revise_merge, revise_one
        self.assertTrue(all(c.startswith("step_prompt(") for c in calls), calls)

if __name__ == "__main__":
    unittest.main(verbosity=1)
