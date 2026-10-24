#!/usr/bin/env python3
"""Praise 10-24 "让 deepseek 能充分利用模型能力自主把修复 PR 推进到正式": prfix --revise (red CI on our own draft fix PR goes back to
the model), outbound's gh.pr.push-fix and patch fallback, and the smaller rules (waiting for memory does not use a retry, an action
outbound gave up on frees its ticket, the cron fallback defers to prwatch, colour-free tool output, the test-edit guard).
Local only: no network, no model, no writes outside a temp dir. Run: cd bin && python3 ../test/test_prbot_autonomy.py"""
import os as _os
_os.environ.setdefault('PRFIX_OWNERS', '*')
_os.environ['PRFIX_DISPATCHED'] = '1'
for _k in [k for k in _os.environ if k.startswith('GIT_')]: del _os.environ[_k]
import argparse, contextlib, datetime as dt, io, json, os, re, subprocess, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin")); sys.path.insert(0, HERE)
import prfix  # noqa: E402
from test_prfix_unit import Tmp, GitTmp, ctx, report, finding, pr, wf, rf, sh, NOW, A, B  # noqa: E402


class SmallRules(Tmp):
    def test_clean_strips_colour_codes(self):
        self.assertEqual(prfix.clean("\x1b[31m1 failed\x1b[39m \x1b[2m(3)\x1b[22m"), "1 failed (3)")
        rc, out = prfix.run(["printf", "\x1b[41m"]); self.assertIn("\x1b", out)   # raw for git diff / patches (review 10-24)

    def test_failed_files_from_vitest_output(self):
        out = (" \x1b[31m❯\x1b[39m scripts/loops/lifeline/tick.test.mjs (12 tests | 1 failed)\n"
               "\x1b[41m FAIL \x1b[49m scripts/loops/lifeline/tick.test.mjs > a > b\n FAIL  src/x.spec.ts > c\n ✓ src/ok.test.js (3)\n")
        self.assertEqual(prfix.failed_files(out), ["scripts/loops/lifeline/tick.test.mjs", "src/x.spec.ts"])

    def test_failing_blocks_skip_teardown_noise(self):   # QA1198 10-24
        noise = "DOMException [AbortError]: The operation was aborted.\n    at Fetch.onAbort (x.js:1:1)\n    at y (z.js:2:2)\n" * 60
        out = "[preflight] 7/7 test:unit\n" + noise + "⎯⎯ Failed Tests 1 ⎯⎯\n FAIL  a/b.test.mjs > c\nError: Test timed out in 5000ms.\n[preflight] ❌ test:unit 失败\n"
        got = prfix.failing_blocks(out, 600)
        self.assertTrue(got.startswith("⎯⎯ Failed Tests"), got[:80]); self.assertNotIn("AbortError", got); self.assertIn("timed out", got)

    def test_waiting_for_memory_does_not_use_a_retry(self):
        tk, notify = prfix.apply_outcome(prfix.new_ticket({"qa": 1, "fp": A, "sev": "P1"}), {"status": "failed", "kind": "waiting", "error": "等内存"}, NOW)
        self.assertEqual((tk["status"], tk["transient_failures"], notify), ("failed", 0, None))
        self.assertTrue(tk["next_retry_at"])

    def test_action_outbound_gave_up_on_frees_the_ticket(self):
        rep = {1178: report(1178, "PraiseZhu", [finding(A)])}
        tk = {f"1178:{A}": {"qa": 1178, "fp": A, "status": "pr_action_written", "action_key": f"fixpr:1178:{A}", "attempts": 1}}
        c = ctx(reports=rep, tickets=tk)
        v = {x["key"]: x for x in prfix.evaluate_all(c, NOW)}[f"1178:{A}"]
        self.assertEqual(v["state"], "claimed")                                    # waiting for outbound
        c["outbound"] = {f"fixpr:1178:{A}": "gave_up"}
        v = {x["key"]: x for x in prfix.evaluate_all(c, NOW)}[f"1178:{A}"]
        self.assertEqual(v["state"], "todo")                                       # fixed again, under a new key (:a2)
        tk[f"1178:{A}"]["action_key"] = f"fixpr:1178:{A}:r2:a3"                    # the round mark still parses with an attempt suffix
        c["outbound"] = {}; c["prs"] = [pr(5, "PraiseZhu", body=f"QA: #1178\n{A} r2", state="OPEN")]
        self.assertEqual({x["key"]: x for x in prfix.evaluate_all(c, NOW)}[f"1178:{A}"]["state"], "claimed")

    def test_cron_fallback_defers_to_a_live_prwatch(self):
        st = f"{self.tmp}/prwatch-state.json"; prfix.PRWATCH_STATE = st
        saved = prfix.takeover; prfix.takeover = lambda name: True
        os.environ.pop("PRFIX_DISPATCHED", None)
        try:
            wf(st, json.dumps({"last_check": prfix.now_cst().strftime("%Y-%m-%d %H:%M:%S CST")}))
            self.assertTrue(prfix.prwatch_dispatching())
            os.environ["PRFIX_DISPATCHED"] = "1"; self.assertFalse(prfix.prwatch_dispatching())       # prwatch's own launch
            os.environ.pop("PRFIX_DISPATCHED")
            wf(st, json.dumps({"last_check": (prfix.now_cst() - dt.timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S CST")}))
            self.assertFalse(prfix.prwatch_dispatching())                                            # prwatch dead: the fallback runs
        finally:
            prfix.takeover = saved; os.environ["PRFIX_DISPATCHED"] = "1"

    def test_scratch_files_never_join_the_fix(self):
        for f in ("out.txt", "debug.mjs", "src/tmp.js", "src/tmp-2.ts", "src/x.log", "notes.md", "a.rej"): self.assertTrue(prfix.scratch(f), f)
        for f in ("src/foo.js", "src/foo.test.js", "scripts/loops/a.mjs", "docs/pr/12/qa.md", "foo.test.js", "src/store/debugLogStore.ts",
                  "cindyplugin/src/__tests__/debugLog.test.ts", "src/template.ts", "src/outline.ts", "src/output-format.ts", "src/fixtures/a.txt"):
            self.assertFalse(prfix.scratch(f), f)

    def test_second_give_up_stops_for_a_human(self):
        rep = {1178: report(1178, "PraiseZhu", [finding(A)])}
        tk = {f"1178:{A}": {"qa": 1178, "fp": A, "status": "pr_action_written", "action_key": f"fixpr:1178:{A}:a2", "gave_up_count": 1}}
        c = ctx(reports=rep, tickets=tk, outbound={f"fixpr:1178:{A}:a2": "gave_up"})
        v = {x["key"]: x for x in prfix.evaluate_all(c, NOW)}[f"1178:{A}"]
        self.assertEqual(v["state"], "skip"); self.assertIn("外发两次", v["hard_stop"])

    def test_main_scrubs_the_environment_first(self):   # 10-24 security
        os.environ["PRFIX_TEST_FAKE_TOKEN"] = "x"; os.environ["SOME_APP_SECRET"] = "y"; os.environ["MIVO_FAKE_TOKEN"] = "z"
        prfix.scrub_env()
        self.assertFalse("SOME_APP_SECRET" in os.environ or "MIVO_FAKE_TOKEN" in os.environ, "secrets survived scrub_env")
        self.assertTrue("PATH" in os.environ and "PRFIX_TEST_FAKE_TOKEN" in os.environ)

    def test_test_changes_note(self):
        self.assertEqual(prfix.test_changes_note(["x", "TEST CHANGES: a.test.js asserted the old bug\n\nROOT CAUSE y"]), "a.test.js asserted the old bug")
        self.assertIsNone(prfix.test_changes_note(["TEST CHANGES: none\n"]))


class TestEditGuard(GitTmp):
    def test_weakening_is_caught_fair_updates_pass(self):
        seed = f"{self.tmp}/seed"; g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", seed]
        wf(f"{seed}/src/a.test.js", "it('x', () => { expect(f()).toBe(1); expect(g()).toBe(2) })\nit('y', () => { expect(1).toBe(1) })\n")
        sh(*g, "add", "-A"); sh(*g, "commit", "-q", "-m", "t"); base = sh(*g, "rev-parse", "HEAD").strip()
        def probs(text):
            wf(f"{seed}/src/a.test.js", text); return prfix.test_edit_problems(seed, base, "src/a.test.js")
        self.assertEqual(probs("it('x', () => { expect(f()).toBe(3); expect(g()).toBe(2) })\nit('y', () => { expect(1).toBe(1) })\n"), [])
        self.assertTrue(probs("it.skip('x', () => { expect(f()).toBe(1); expect(g()).toBe(2) })\nit('y', () => { expect(1).toBe(1) })\n"))
        self.assertTrue(probs("it('x', () => { expect(f()).toBe(1); expect(g()).toBe(2) })\n"))                    # a case removed
        self.assertTrue(probs("it('x', () => { expect(f()).toBe(1) })\nit('y', () => { expect(1).toBe(1) })\n"))  # an assertion removed
        os.remove(f"{seed}/src/a.test.js"); self.assertTrue(prfix.test_edit_problems(seed, base, "src/a.test.js"))
        self.assertEqual(prfix.test_edit_problems(seed, base, "src/new.test.js"), [])                              # new file: ours
        wf(f"{seed}/src/a.test.js", "it('x', () => { expect(f()).toBe(1); expect(g()).toBe(2) })\n// it('y', () => { expect(1).toBe(1) })\n"
                                    "it('z', () => { expect(h()).toBeDefined() })\n")
        self.assertTrue(prfix.test_edit_problems(seed, base, "src/a.test.js"))                                    # commented out + weaker matcher
        self.assertTrue(probs("it('x', () => { expect(f()).toBe(1); expect(g()).toBe(2) })\nif (process.platform !== 'win32') it('y', () => { expect(1).toBe(1) })\n"))
        self.assertTrue(probs("it('x', () => { expect(f()).toBe(1); expect(g()).toBe(2) })\nit('y', () => { if (process.platform === 'win32') return; expect(1).toBe(1) })\n"))
        self.assertEqual(probs("it('x', () => { expect(f()).toBe(1); expect(g()).toBe(process.platform === 'win32' ? 3 : 2) })\nit('y', () => { expect(1).toBe(1) })\n"), [])
        self.assertEqual(probs("it('x', () => { expect(f()).toBe(1); expect(() => g()).not.toThrow() })\nit('y', () => { expect(1).toBe(1) })\n"), [])
        self.assertTrue(prfix.config_problems(seed, base, ["vitest.config.ts"]))
        self.assertEqual(prfix.config_problems(seed, base, ["src/foo.js", "vite.config.ts", "cindyplugin/vite.config.ts"]), [])


class TestRevise(GitTmp):
    """prfix --revise N end to end in a temp repo: the PR branch exists on origin, gh is faked, the model edits the worktree."""
    BR = "qa-fix/fixpr-1178-mq1-aaaaaaaaaaaaaaaaaaaa"
    def setUp(self):
        super().setUp()
        seed = f"{self.tmp}/seed"; g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", seed]
        sh(*g, "checkout", "-q", "-b", self.BR)
        wf(f"{seed}/src/foo.js", "module.exports = () => 2 // FIXED\n"); wf(f"{seed}/src/foo.test.js", "test('bug', () => {})\n")
        wf(f"{seed}/src/bar.js", "module.exports = 'PR also changed bar'\n")
        sh(*g, "add", "-A"); sh(*g, "commit", "-q", "-m", "fix"); sh(*g, "push", "-q", self.origin, self.BR)
        self.head = sh(*g, "rev-parse", "HEAD").strip(); sh(*g, "checkout", "-q", "main")
        self.fakebin("npm", "exit 0\n")
        self.fakebin("npx", """
            [ "$1" = vitest ] || exit 9
            [ "$2" = related ] && { echo " ✓ related"; exit 0; }
            if grep -q FIXED src/foo.js; then echo " ✓ 1 passed"; exit 0; else echo " FAIL  src/foo.test.js > bug"; exit 1; fi
        """)
        self.orig = {k: getattr(prfix, k) for k in ("pi", "record_actions", "_gh_json", "failed_check_logs", "load_reports", "takeover", "mem_gb")}
        self.acts = []; prfix.record_actions = lambda job, day, acts: self.acts.extend(acts)
        prfix.failed_check_logs = lambda checks, cap=12000: "Error: expected '/' got '\\\\' (windows path)"
        prfix.load_reports = lambda: {1178: report(1178, "PraiseZhu", [finding(A, file="src/foo.js", line=1)])}
        prfix.takeover = lambda name: True; prfix.mem_gb = lambda: 8.0
        self.pr = {"number": 1301, "state": "OPEN", "isDraft": True, "author": {"login": "PraiseZhu"}, "headRefName": self.BR,
                   "headRefOid": self.head, "title": "fix: x (QA #1178)", "body": f"QA: #1178\n{A}\n<!-- pi-line fixpr:1178:{A} -->",
                   "statusCheckRollup": [{"name": "unit (windows, 2)", "conclusion": "FAILURE", "detailsUrl": "https://github.com/x/y/actions/runs/9/job/1"},
                                         {"name": "review-pr", "conclusion": "FAILURE"}]}
        prfix._gh_json = lambda args, t: self.pr
        self.model = "winfix"; self.prompts = []
        def fake_pi(tier, cwd, prompt, session, **kw):
            self.prompts.append(prompt)
            if self.model == "winfix": wf(f"{cwd}/src/foo.js", "module.exports = () => 2 // FIXED WINFIX\n")
            if self.model == "weaken": wf(f"{cwd}/src/foo.test.js", "test.skip('bug', () => {})\n")
            if self.model == "resolve": wf(f"{cwd}/src/foo.js", "module.exports = () => 2 // FIXED + main's note\n")
            return "CAUSE: path separator", "t9"
        prfix.pi = fake_pi
    def tearDown(self):
        for k, v in self.orig.items(): setattr(prfix, k, v)
        super().tearDown()
    def revise(self, dry=False):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf): rc = prfix.revise(argparse.Namespace(revise=1301, dry_run=dry))
        return rc, json.loads(buf.getvalue().strip().splitlines()[-1][7:])

    def test_red_ci_goes_to_the_model_and_comes_back_as_a_push(self):
        rc, r = self.revise()
        self.assertEqual((rc, r["status"]), (0, "pr_action_written"), r)
        a = self.acts[-1]
        self.assertEqual((a["type"], a["pr"], a["branch"], a["head"]), ("gh.pr.push-fix", 1301, self.BR, self.head))
        self.assertIn("windows path", self.prompts[0]); self.assertIn("unit (windows, 2)", self.prompts[0]); self.assertNotIn("review-pr", self.prompts[0])
        d = f"{self.tmp}/check"; sh("git", "clone", "-q", "--branch", self.BR, self.origin, d)
        sh("git", "-C", d, "apply", "--check", a["patch"])                              # applies onto the PR head
        self.assertIn("WINFIX", open(a["patch"]).read())
        self.assertEqual(re.findall(r"^diff --git a/(\S+)", open(a["patch"]).read(), re.M), ["src/foo.js"])   # review 10-24: no revert of bar.js
        sh("git", "-C", d, "apply", a["patch"]); self.assertIn("PR also changed bar", rf(f"{d}/src/bar.js"))
        t = prfix.load_tickets()["tickets"]["rev:1301"]
        self.assertEqual((t["status"], t["revisions"], t["last_head"]), ("pushed", 1, self.head))
        self.assertFalse(os.path.exists(f"{prfix.WT_ROOT}/pi-wt-rev-1301"))             # worktree always removed
        rc, r = self.revise(); self.assertEqual(r["status"], "skipped")                 # same head again: waits for the push

    def test_model_finds_nothing_to_change_is_parked_without_dm(self):   # 10-24 evening: kind 4, no DM
        self.model = "none"; inner = prfix.pi
        prfix.pi = lambda tier, *a, **k: ("RELATED\nthe PR's change breaks it", "tj") if tier == prfix.JUDGE_TIER else inner(tier, *a, **k)
        rc, r = self.revise()
        self.assertEqual(rc, 1); t = prfix.load_tickets()["tickets"]["rev:1301"]
        self.assertEqual(t["status"], "failed")                                          # once more from scratch first
        rc, r = self.revise(); self.assertEqual(r["status"], "skipped")                 # ... after its back-off, not at once
        with prfix.tickets_locked() as T: T["tickets"]["rev:1301"]["next_retry_at"] = None
        rc, r = self.revise(); t = prfix.load_tickets()["tickets"]["rev:1301"]
        self.assertEqual(t["status"], "parked"); self.assertIn("不是这个 PR 能修的", t["last_error"]); self.assertTrue(t["next_retry_at"])
        self.assertFalse([a for a in self.acts if a["type"] == "slack.dm"])

    def test_moving_a_ci_file_away_asks_praise(self):   # 10-24 review: a `git mv` out of .github/ used to show only its new name
        seed = f"{self.tmp}/seed"; g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", seed]
        sh(*g, "checkout", "-q", self.BR); os.makedirs(f"{seed}/.github/workflows"); wf(f"{seed}/.github/workflows/ci.yml", "name: CI\n")
        sh(*g, "add", "-A"); sh(*g, "commit", "-q", "-m", "ci"); sh(*g, "push", "-q", self.origin, self.BR)
        self.head = self.pr["headRefOid"] = sh(*g, "rev-parse", "HEAD").strip(); sh(*g, "checkout", "-q", "main")
        inner = prfix.pi
        def mv(tier, cwd, *a, **k):
            r = inner(tier, cwd, *a, **k)
            if os.path.exists(f"{cwd}/.github/workflows/ci.yml"): sh("git", "-C", cwd, "mv", ".github/workflows/ci.yml", "ci.yml")
            return r
        prfix.pi = mv
        rc, r = self.revise()
        self.assertFalse([a for a in self.acts if a["type"] == "gh.pr.push-fix"], r)
        t = json.dumps(prfix.load_tickets()["tickets"]["rev:1301"], ensure_ascii=False)
        self.assertIn("CI 配置 / 仓库规则 / 密钥相关的文件：.github/workflows/ci.yml", t)   # kind 5 (ask Praise), not a stray-file stop

    def test_weakening_the_regression_test_never_lands(self):
        self.model = "weaken"
        rc, r = self.revise()
        self.assertEqual(rc, 1); self.assertFalse([a for a in self.acts if a["type"] == "gh.pr.push-fix"])

    def test_revisions_cap_and_only_bot_drafts(self):
        with prfix.tickets_locked() as T: T["tickets"]["rev:1301"] = {"status": "pushed", "revisions": prfix.MAX_REVISIONS, "last_head": "x" * 40}
        rc, r = self.revise(); self.assertEqual(r["status"], "parked"); self.assertFalse([a for a in self.acts if a["type"] == "slack.dm"])
        t = prfix.load_tickets()["tickets"]["rev:1301"]; self.assertEqual(t["park_reason"], "修不动")
        rc, r = self.revise(); self.assertEqual(r["status"], "skipped")                 # paused: tried again by itself later
        with prfix.tickets_locked() as T: T["tickets"]["rev:1301"]["next_retry_at"] = "2000-01-01T00:00:00+08:00"
        rc, r = self.revise(); self.assertNotEqual(r["status"], "parked")              # after the pause: a fresh budget of rounds
        self.assertEqual(prfix.load_tickets()["tickets"]["rev:1301"]["revisions"], 1)
        self.pr["isDraft"] = False; rc, r = self.revise(); self.assertEqual(r["status"], "skipped")
        self.pr["isDraft"] = True; self.pr["body"] = "QA: #1178"; rc, r = self.revise(); self.assertEqual(r["status"], "skipped")

    def test_conflict_with_main_is_merged_and_resolved_by_the_model(self):   # 10-24
        seed = f"{self.tmp}/seed"; g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", seed]
        wf(f"{seed}/src/foo.js", "module.exports = () => 1 // main's note\n"); sh(*g, "commit", "-q", "-am", "main touches foo")
        sh(*g, "push", "-q", self.origin, "main"); main = sh(*g, "rev-parse", "HEAD").strip()
        self.pr.update(mergeStateStatus="DIRTY", statusCheckRollup=[]); self.model = "resolve"
        rc, r = self.revise()
        self.assertEqual((rc, r["status"]), (0, "pr_action_written"), r)
        a = self.acts[-1]
        self.assertEqual((a["type"], a["mode"], a["head"], a["main"]), ("gh.pr.push-fix", "merge", self.head, main))
        self.assertIn("src/foo.js", "".join(self.prompts)); self.assertIn("conflicts", self.prompts[0])
        d = f"{self.tmp}/chk"; sh("git", "clone", "-q", "--branch", self.BR, self.origin, d)       # what outbound does
        sh("git", "-C", d, "fetch", "-q", "origin", main); sh("git", "-C", d, "fetch", "-q", a["bundle"], "HEAD")
        self.assertEqual(sh("git", "-C", d, "rev-parse", "FETCH_HEAD").strip(), a["new_head"])
        self.assertEqual(sh("git", "-C", d, "rev-list", "--parents", "-n", "1", a["new_head"]).split()[1:], [self.head, main])
        self.assertIn("FIXED", sh("git", "-C", d, "show", f"{a['new_head']}:src/foo.js"))
        self.assertIn(f"Pi-Revise: {a['key']}", sh("git", "-C", d, "log", "-1", "--format=%B", a["new_head"]))
        t = prfix.load_tickets()["tickets"]["rev:1301"]; self.assertEqual((t["status"], t.get("merges"), t.get("revisions")), ("pushed", 1, 0))

    def test_unresolved_conflict_markers_stop_after_three_rounds(self):
        seed = f"{self.tmp}/seed"; g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", seed]
        wf(f"{seed}/src/foo.js", "module.exports = () => 1 // main's note\n"); sh(*g, "commit", "-q", "-am", "main touches foo")
        sh(*g, "push", "-q", self.origin, "main")
        self.pr.update(mergeStateStatus="DIRTY", statusCheckRollup=[]); self.model = "none"
        rc, r = self.revise()
        self.assertEqual(rc, 1); self.assertIn("冲突标记", prfix.load_tickets()["tickets"]["rev:1301"]["last_error"])
        self.assertFalse([x for x in self.acts if x["type"] == "gh.pr.push-fix"])

    def test_ruling_r2_on_red_ci_the_model_calls_unrelated(self):
        self.model = "none"; orig = prfix.pi
        def model(tier, cwd, prompt, session, **kw):
            if tier == prfix.JUDGE_TIER: return self.verdict, "tj"
            return orig(tier, cwd, prompt, session, **kw)
        prfix.pi = model; self.verdict = "UNRELATED\nwindows runner image broke on main too"
        try:
            rc, r = self.revise()
            self.assertEqual((rc, r["status"]), (0, "pr_action_written"), r)
            a = self.acts[-1]; self.assertEqual((a["type"], a["ruled"], a["names"]), ("gh.run.rerun-failed", True, ["unit (windows, 2)"]))
            t = prfix.load_tickets()["tickets"]["rev:1301"]; self.assertEqual((t["status"], t["rerun_only"]), ("pushed", True))
            with prfix.tickets_locked() as T: T["tickets"].pop("rev:1301")
            self.acts.clear(); self.verdict = "RELATED\nthe PR's path join breaks on windows"; self.model = "winfix"
            rc, r = self.revise(); self.assertEqual(r["status"], "pr_action_written", r)   # model said unrelated? not here: it fixed on first try
        finally: prfix.pi = orig

    def test_switch_off(self):
        prfix.takeover = lambda name: False
        rc, r = self.revise(); self.assertEqual(r["status"], "off")


BOX_MERGER = os.path.exists("/workspace/mivo-merger/app/scripts/loops/automerge/policy.json")   # box-only data (CI has none)
@unittest.skipUnless(BOX_MERGER, "needs the box's mivo-merger policy (outbound imports merger)")
class TestOutboundPush(GitTmp):
    def setUp(self):
        super().setUp()
        import outbound as OB; self.OB = OB

    def _repo_with_moved_main(self):
        seed = f"{self.tmp}/seed"; g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", seed]
        base = sh(*g, "rev-parse", "HEAD").strip()
        wf(f"{seed}/src/foo.js", "module.exports = () => 2 // FIXED\n")
        patch = f"{self.tmp}/fix.patch"; wf(patch, sh(*g, "diff", "--binary")); sh(*g, "checkout", "-q", "--", ".")
        wf(f"{seed}/src/foo.js", "module.exports = () => 1 // main moved on this very line\n")
        sh(*g, "commit", "-q", "-am", "main moves"); sh(*g, "push", "-q", self.origin, "main")
        d = f"{self.tmp}/clone"; sh("git", "clone", "-q", self.origin, d); sh("git", "-C", d, "checkout", "-q", "-b", "qa-fix/x")
        return d, patch, base

    def test_patch_that_no_longer_applies_on_main_gives_up_at_once(self):
        d, patch, base = self._repo_with_moved_main()
        with self.assertRaises(self.OB.GiveUp): self.OB.apply_fix_patch(d, patch, base, "qa-fix/x")
        self.assertEqual(sh("git", "-C", d, "status", "--porcelain").strip(), "")              # clone left clean
        seed = f"{self.tmp}/seed"; g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", seed]
        wf(f"{seed}/src/other.js", "x\n"); sh(*g, "add", "-A"); sh(*g, "commit", "-q", "-m", "unrelated")
        p2 = f"{self.tmp}/fix2.patch"; wf(f"{seed}/src/other.js", "y\n"); wf(p2, sh(*g, "diff")); sh(*g, "checkout", "-q", "--", ".")
        sh(*g, "push", "-q", self.origin, "main"); sh("git", "-C", d, "pull", "-q", "origin", "main")
        self.assertEqual(self.OB.apply_fix_patch(d, p2, base, "qa-fix/x"), "main")

    def test_push_fix_state(self):
        OB = self.OB; calls = {}
        p = {"state": "OPEN", "isDraft": True, "author": {"login": "PraiseZhu"}, "headRefName": "qa-fix/x", "headRefOid": "h1",
             "body": "<!-- pi-line fixpr:1:mq1-x -->"}
        saved = OB.gh
        def gh(args, js=True):
            if args[0] == "api": return calls.get("msg", "")
            return dict(p)
        OB.gh = gh
        try:
            a = {"key": "revise:1301:h1", "pr": 1301, "branch": "qa-fix/x", "head": "h1", "patch": "/nope"}
            state = OB.h_push_fix(a)[1]
            self.assertEqual(state(), "pending")
            p["headRefOid"] = "h2"; calls["msg"] = "fix: CI\n\nPi-Revise: revise:1301:h1"; self.assertEqual(state(), "done")
            calls["msg"] = "someone else"; self.assertEqual(state(), "stale:head moved")
            p["headRefOid"] = "h1"; p["isDraft"] = False; self.assertEqual(state(), "stale:no longer a draft")
            p["isDraft"] = True; p["body"] = "QA: #1"; self.assertEqual(state(), "stale:not our bot fix PR")
            self.assertIs(OB.handler({"type": "gh.pr.push-fix", **a})[0].startswith("gh push fix"), True)
        finally: OB.gh = saved


if __name__ == "__main__":
    unittest.main(verbosity=1)
