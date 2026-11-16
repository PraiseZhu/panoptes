#!/usr/bin/env python3
"""prretro turn-ready path (10-30 09:45: NameError jload -> rc=1). Local only: gh / git faked, dry run, no writes."""
import contextlib, io, json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import prretro

class TurnReady(unittest.TestCase):
    def test_green_draft_reads_ledger_and_reaches_block_reason(self):
        seen = {}
        prs = [{"number": 1444, "title": "fix", "state": "OPEN", "isDraft": True, "body": "QA: #1420", "headRefName": "qa-fix/x", "headRefOid": "a" * 40, "mergedAt": None}]
        def gh(args, js=True):
            if args[:2] == ["pr", "list"]: return prs
            if args[:2] == ["pr", "checks"]: return [{"name": "verify", "bucket": "pass"}, {"name": "review-pr", "bucket": "pending"}]
            raise AssertionError(args)
        def block(gh_, repo, n, cs, ledger): seen["ledger"] = ledger; return None
        saved = {k: getattr(prretro, k) for k in ("gh", "findings", "note_index", "git", "DRY", "BASE")}
        saved_rg = prretro.RG.block_reason
        d = tempfile.mkdtemp(); os.makedirs(f"{d}/out/outbound"); json.dump({"k": 1}, open(f"{d}/out/outbound/ledger.json", "w"))
        prretro.gh = gh; prretro.findings = lambda: []; prretro.note_index = lambda: {}; prretro.git = lambda *a, **k: (0, "")
        prretro.DRY = True; prretro.BASE = d; prretro.RG.block_reason = block
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf): prretro.main()
        finally:
            for k, v in saved.items(): setattr(prretro, k, v)
            prretro.RG.block_reason = saved_rg
        self.assertEqual(seen["ledger"], {"k": 1})
        self.assertIn('"type": "gh.pr.ready"', buf.getvalue())
        self.assertEqual(prretro.jload(f"{d}/missing.json", {}), {})

if __name__ == "__main__": unittest.main(verbosity=1)
