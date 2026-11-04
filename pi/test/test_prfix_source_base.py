#!/usr/bin/env python3
"""11-04: prfix opened fix PRs #1682/#1710 against main although their source PR #1597 has base integration/canvas-s2
(fix_one always fetched/branched origin/main; outbound's gh pr create had no --base). Now the fix branches from the source
PR's baseRefName, the action carries base_ref, outbound passes --base, and a mismatch is a hard stop (Deterministic, no PR).
Scratch state only, no network. Run: python3 -m pytest -q test/test_prfix_source_base.py"""
import argparse, contextlib, os, sys, tempfile, types, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); BIN = os.path.join(os.path.dirname(HERE), "bin"); sys.path.insert(0, BIN)
sys.path.insert(0, HERE); import isolation  # noqa: E402
isolation.sandbox_state()
sys.modules.setdefault("merger", types.SimpleNamespace(FIELDS="number", blockers=lambda p: []))
import prfix as P  # noqa: E402
import outbound as O  # noqa: E402
S2 = "integration/canvas-s2"
FP = "mq1-" + "a" * 20

def gh_base(b): return lambda args, t=None: {"baseRefName": b} if args[:2] == ["pr", "view"] else {}

class FixOneBase(unittest.TestCase):
    def test_fix_branches_from_source_base_not_main(self):
        calls = []
        def git(*a, cwd=None, t=600):
            calls.append(a)
            if a[0] == "rev-parse": return 0, "f" * 40
            if a[0] == "worktree" and a[1] == "add": return 1, "stop here"   # enough: the base is chosen before this
            return 0, ""
        A = argparse.Namespace(dry_run=True, base=None)
        with mock.patch.object(P, "_gh_json", side_effect=gh_base(S2)), mock.patch.object(P, "git", side_effect=git), \
             mock.patch.object(P, "worktrees", return_value=[]), mock.patch.object(P, "repo_lock", contextlib.nullcontext), \
             mock.patch.object(P, "_TMPROOT", return_value=tempfile.mkdtemp()):
            out, res = P.fix_one({"qa": 1597, "sev": "P1", "fp": FP, "title": "t"}, A, "2026-11-04")
        self.assertIn(("rev-parse", f"origin/{S2}"), calls, out)
        self.assertNotIn(("rev-parse", "origin/main"), calls)
        self.assertEqual(res.get("base_ref"), S2)

class Mismatch(unittest.TestCase):
    def test_mismatch_is_a_hard_stop(self):
        with mock.patch.object(P, "_gh_json", side_effect=gh_base(S2)):
            with self.assertRaises(P.Deterministic) as e: P.check_fix_base(1597, "main")
            self.assertIn("硬停不开 PR", str(e.exception))
            self.assertEqual(P.check_fix_base(1597, S2), S2)

class OutboundBase(unittest.TestCase):
    def test_pr_create_targets_base_ref(self):
        patch = os.path.join(tempfile.mkdtemp(), "x.patch")
        with open(patch, "w") as f: f.write("diff --git a/a b/a\n")
        a = {"key": f"fixpr:1597:{FP}", "patch": patch, "title": "fix: t (QA #1597)", "body_must_contain": FP, "base_ref": S2, "qa": 1597}
        cmds, ghs = [], []
        with mock.patch.object(O, "run", side_effect=lambda c, *k, **kw: cmds.append(list(c)) or ""), \
             mock.patch.object(O, "apply_fix_patch", return_value="main"), \
             mock.patch.object(O, "fixpr_gate_reason", return_value=None), \
             mock.patch.object(O, "gh", side_effect=lambda c, *k, **kw: ghs.append(list(c)) or ({"baseRefName": S2} if c[:2] == ["pr", "view"] else "https://github.com/x/y/pull/1\n")), \
             mock.patch.object(O, "fixpr_docs_rename", return_value="ok"):
            O.h_fixpr(a)[3]()
        create = next(c for c in ghs if c[:2] == ["pr", "create"])
        self.assertEqual(create[create.index("--base") + 1], S2, create)
        clone = next(c for c in cmds if c[:3] == ["gh", "repo", "clone"])
        self.assertEqual(clone[clone.index("--branch") + 1], S2, clone)

if __name__ == "__main__": unittest.main()
