#!/usr/bin/env python3
"""11-04 follow-ups to #200 (prfix source-base check). One test per item; each fails on main and passes here.
1 deleted base -> hard stop 'base gone'   2 outbound re-checks the source PR's current base   3 legacy action without base_ref refused
4 --base must be reachable from the source base   5 apply_fix_patch names the real target branch, not 'main'
6 rate limit on --pending-json is a skip (rc 0), not rc 1. Scratch state only, no network."""
import argparse, contextlib, os, sys, tempfile, types, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); BIN = os.path.join(os.path.dirname(HERE), "bin"); sys.path.insert(0, BIN)
sys.path.insert(0, HERE); import isolation  # noqa: E402
isolation.sandbox_state()
sys.modules.setdefault("merger", types.SimpleNamespace(FIELDS="number", blockers=lambda p: []))
import lib  # noqa: E402
import prfix as P  # noqa: E402
import outbound as O  # noqa: E402
S2 = "integration/canvas-s2"
FP = "mq1-" + "b" * 20

def fix_one(git_extra, base=None):
    calls = []
    def git(*a, cwd=None, t=600):
        calls.append(a)
        r = git_extra(a)
        if r is not None: return r
        if a[0] == "rev-parse": return 0, "f" * 40
        if a[0] == "worktree" and a[1] == "add": return 1, "stop here"
        return 0, ""
    A = argparse.Namespace(dry_run=True, base=base)
    with mock.patch.object(P, "_gh_json", side_effect=lambda args, t=None: {"baseRefName": S2} if args[:2] == ["pr", "view"] else {}), \
         mock.patch.object(P, "git", side_effect=git), mock.patch.object(P, "worktrees", return_value=[]), \
         mock.patch.object(P, "repo_lock", contextlib.nullcontext), mock.patch.object(P, "_TMPROOT", return_value=tempfile.mkdtemp()):
        out, res = P.fix_one({"qa": 1597, "sev": "P1", "fp": FP, "title": "t"}, A, "2026-11-04")
    return out, calls

def action(**kw):
    patch = os.path.join(tempfile.mkdtemp(), "x.patch")
    with open(patch, "w") as f: f.write("diff --git a/a b/a\n")
    return {"key": f"fixpr:1597:{FP}", "patch": patch, "title": "fix: t (QA #1597)", "body_must_contain": FP, "qa": 1597, **kw}

def outbound_do(a, current_base):
    cmds, ghs = [], []
    with mock.patch.object(O, "run", side_effect=lambda c, *k, **kw: cmds.append(list(c)) or ""), \
         mock.patch.object(O, "apply_fix_patch", return_value="base"), mock.patch.object(O, "fixpr_gate_reason", return_value=None), mock.patch.object(O, "fixpr_cap_reason", return_value=None), \
         mock.patch.object(O, "gh", side_effect=lambda c, *k, **kw: ghs.append(list(c)) or ({"baseRefName": current_base} if c[:2] == ["pr", "view"] else "https://github.com/x/y/pull/1\n")), \
         mock.patch.object(O, "fixpr_docs_rename", return_value="ok"):
        try: return O.h_fixpr(a)[3](), cmds, ghs
        except Exception as e: return e, cmds, ghs

class Followups(unittest.TestCase):
    def test_1_deleted_base_is_hard_stop_base_gone(self):
        out, _ = fix_one(lambda a: (128, f"fatal: couldn't find remote ref refs/heads/{S2}") if a[0] == "fetch" else None)
        self.assertEqual(out["kind"], "deterministic", out)
        self.assertIn("base gone", out["error"])

    def test_2_outbound_rechecks_source_base_before_push(self):
        r, cmds, _ = outbound_do(action(base_ref=S2), current_base="main")   # source PR retargeted after prfix wrote the action
        self.assertIsInstance(r, O.GiveUp, r)
        self.assertIn("base is now 'main'", str(r))
        self.assertFalse(any(c[:2] == ["git", "-C"] and "push" in c for c in cmds), cmds)
        self.assertFalse(any(c[:3] == ["gh", "repo", "clone"] for c in cmds), cmds)
        r, _, ghs = outbound_do(action(base_ref=S2), current_base=S2)
        self.assertNotIsInstance(r, Exception, r)

    def test_3_legacy_action_without_base_ref_is_refused(self):
        r, cmds, ghs = outbound_do(action(), current_base="main")
        self.assertIsInstance(r, O.GiveUp, r)
        self.assertIn("base_ref", str(r))
        self.assertFalse(any(c[:2] == ["pr", "create"] for c in ghs))

    def test_4_base_sha_must_be_reachable_from_source_base(self):
        out, calls = fix_one(lambda a: (1, "") if a[:2] == ("merge-base", "--is-ancestor") else None, base="c" * 40)
        self.assertIn(("merge-base", "--is-ancestor", "c" * 40, f"origin/{S2}"), calls)
        self.assertEqual(out["kind"], "deterministic", out)
        self.assertIn("不可达", out["error"])

    def test_5_apply_fix_patch_names_target_branch_not_main(self):
        fail = types.SimpleNamespace(returncode=1, stdout="", stderr="patch does not apply")
        with mock.patch.object(O.subprocess, "run", return_value=fail):
            with self.assertRaises(O.GiveUp) as e: O.apply_fix_patch("/nonexistent", "x.patch", "d" * 40, "qa-fix/x", S2)
        self.assertIn(f"applies on {S2}", str(e.exception))
        self.assertNotIn("on main", str(e.exception))
        self.assertNotIn("main", O.apply_fix_patch.__doc__)

    def test_6_pending_json_rate_limit_is_skip_rc0(self):
        with mock.patch.object(P, "gather", side_effect=lib.GhRateLimited("gh pr list: GitHub rate-limited until 12:45:51 CST (not sent)")), \
             mock.patch.object(P, "raise_oom_score", return_value=None), mock.patch.object(P, "scrub_env", return_value=None):
            self.assertEqual(P.main(["--pending-json"]), 0)

if __name__ == "__main__": unittest.main()
