#!/usr/bin/env python3
"""daily.py --help / unknown flags must not generate or send (11-10: daily.py --help misfired a report).
Pure cli_preflight checks + mocked daily.main; no network, no real send."""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]:
    del __import__("os").environ[_k]
import io, os, sys, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import lib as L  # noqa: E402
import daily as D  # noqa: E402

KNOWN = {"--render-only", "--diff-sent", "--resend", "--no-send"}


class CliPreflight(unittest.TestCase):
    def test_help_variants(self):
        for flag in ("--help", "-h"):
            kind, doc = L.cli_preflight(["daily.py", flag], KNOWN, "USAGE")
            self.assertEqual(kind, "help"); self.assertEqual(doc, "USAGE")

    def test_unknown_flag_is_error(self):
        kind, msg = L.cli_preflight(["daily.py", "--bogus"], KNOWN, "USAGE")
        self.assertEqual(kind, "error")
        self.assertIn("--bogus", msg); self.assertIn("USAGE", msg)
        self.assertIn("\n", msg)

    def test_known_flags_and_day_ok(self):
        kind, payload = L.cli_preflight(
            ["daily.py", "2026-11-09", "--render-only", "--no-send"], KNOWN, "USAGE")
        self.assertEqual(kind, "ok")
        pos, fl = payload
        self.assertEqual(pos, ["2026-11-09"])
        self.assertEqual(fl, {"--render-only", "--no-send"})

    def test_help_wins_over_other_flags(self):
        kind, _ = L.cli_preflight(["daily.py", "--resend", "--help"], KNOWN, "U")
        self.assertEqual(kind, "help")


class DailyMainGuard(unittest.TestCase):
    """main(--help / --unknown) must not call generate/send entry points."""

    def _assert_no_work(self, argv, expect_rc):
        calls = []

        def boom(name):
            def f(*a, **k):
                calls.append(name)
                raise AssertionError(f"{name} must not run for {argv}")
            return f

        buf_out, buf_err = io.StringIO(), io.StringIO()
        with mock.patch.object(D, "merged_prs", boom("merged_prs")), \
             mock.patch.object(D, "build", boom("build")), \
             mock.patch.object(D, "render", boom("render")), \
             mock.patch.object(D, "send_own", boom("send_own")), \
             mock.patch.object(D, "record_actions", boom("record_actions")), \
             mock.patch.object(D, "write_atomic", boom("write_atomic")), \
             mock.patch("sys.stdout", buf_out), \
             mock.patch("sys.stderr", buf_err):
            rc = D.main(argv)
        self.assertEqual(rc, expect_rc)
        self.assertEqual(calls, [])
        return buf_out.getvalue(), buf_err.getvalue()

    def test_help_exits_0_no_generate_no_send(self):
        out, err = self._assert_no_work(["daily.py", "--help"], 0)
        self.assertIn("usage: daily.py", out)
        self.assertEqual(err, "")

    def test_h_exits_0_no_generate_no_send(self):
        out, err = self._assert_no_work(["daily.py", "-h"], 0)
        self.assertIn("usage: daily.py", out)

    def test_unknown_exits_2_no_generate_no_send(self):
        out, err = self._assert_no_work(["daily.py", "--not-a-flag"], 2)
        self.assertEqual(out, "")
        self.assertIn("--not-a-flag", err)
        self.assertIn("usage: daily.py", err)


if __name__ == "__main__":
    unittest.main()
