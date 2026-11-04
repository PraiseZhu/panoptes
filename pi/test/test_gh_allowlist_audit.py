#!/usr/bin/env python3
"""11-05 allowlist audit: every lifeline / review-pr CALLER_ALLOWLIST entry has a recorded PAT reason (gh_wrapper.PAT_KEEP_REASONS),
and no reason is kept for a caller that already left the allowlist. Static check, no network, no token."""
import os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); BIN = os.path.join(os.path.dirname(HERE), "bin")
sys.path.insert(0, BIN); sys.path.insert(0, HERE)
import isolation  # noqa: E402
isolation.sandbox_state()
import gh_wrapper as W  # noqa: E402

SCOPE = ("lifeline-tick", "lifeline-report", "review-pr-")

class AllowlistAudit(unittest.TestCase):
    def names(self): return [n for n, _ in W.CALLER_ALLOWLIST if n.startswith(SCOPE)]
    def test_every_scoped_entry_has_reason(self):
        missing = [n for n in self.names() if not W.PAT_KEEP_REASONS.get(n)]
        self.assertEqual(missing, [])
    def test_no_stale_reason(self):
        self.assertEqual(sorted(set(W.PAT_KEEP_REASONS) - set(self.names())), [])
    def test_reasons_are_text(self):
        for n, rs in W.PAT_KEEP_REASONS.items():
            self.assertTrue(rs and all(isinstance(r, str) and r.strip() for r in rs), n)

if __name__ == "__main__": unittest.main()
