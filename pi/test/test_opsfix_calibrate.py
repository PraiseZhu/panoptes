#!/usr/bin/env python3
"""opsfix calibrate --fingerprint, and recompute of e2e-browser:unavailable.

calibrate used to match only a.title substring, so a check finding with no title
(e.g. #2052 mq1-679cbc2876f9a2c99a34) could not be selected. recompute used to
map a leftover P3 to verdict clean even when checks[] has e2e-browser unavailable
(nightly.verdictOf would have said partially-verified).

In-memory only; never touches /workspace/mivo-qa/reports.
Run: python3 pi/test/test_opsfix_calibrate.py
"""
import io, json, os, sys, tempfile, types, unittest, importlib.util
from contextlib import redirect_stdout
from unittest import mock

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
sys.path.insert(0, os.environ.get("PI_BIN") or BIN)
spec = importlib.util.spec_from_file_location("opsfix", os.path.join(BIN, "opsfix.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

FP = "mq1-679cbc2876f9a2c99a34"
OTHER = "mq1-aa7d6097d19fb3c3f461"

def untitled_check(fp=FP, sev="P2", test="persistFlush.bench.test.ts"):
    return {"source": "check", "title": None, "id": None, "fingerprint": fp,
            "check_id": "bench", "test": test, "severity": sev, "status": "introduced"}

def titled(title, fp="mq1-titled000000000000000", sev="P1", source="code-review"):
    return {"source": source, "title": title, "id": "cr-1", "fingerprint": fp, "severity": sev}

def e2e(status):
    return {"id": "e2e-browser", "name": "浏览器端到端", "status": status, "dimension": "black-box"}

def report(pr_no=2052, verdict="review-suggested", severity="P2", findings=None, checks=None):
    pr = {"number": pr_no, "severity": severity, "verdict": verdict,
          "findings": list(findings or []), "checks": list(checks or []), "code_review": {}}
    d = {"prs": [pr], "summary": {
        "prs_by_severity": {"P0": 0, "P1": 0, "P2": 1, "P3": 0},
        "findings_by_severity": {"P0": 0, "P1": 0, "P2": 1, "P3": 0}}}
    return d, pr

def args(**kw):
    return types.SimpleNamespace(pr=kw.get("pr", 2052), match=kw.get("match"),
                                 fingerprint=kw.get("fingerprint"), to=kw.get("to", "P3"),
                                 reason=kw.get("reason", "test"), apply=kw.get("apply", False))


class FingerprintMatch(unittest.TestCase):
    def test_untitled_check_match_alone_finds_nothing(self):
        hits = m.finding_hits([untitled_check()], match="persistFlush")
        self.assertEqual(hits, [])

    def test_untitled_check_fingerprint_selects_one(self):
        fs = [untitled_check(), untitled_check(OTHER, sev="P3", test="other")]
        hits = m.finding_hits(fs, fingerprint=FP)
        self.assertEqual([x["fingerprint"] for x in hits], [FP])

    def test_foreign_fingerprint_is_empty(self):
        self.assertEqual(m.finding_hits([untitled_check()], fingerprint="mq1-deadbeefdeadbeefdead"), [])

    def test_title_match_still_works(self):
        fs = [titled("未带条件的早期返回"), untitled_check()]
        hits = m.finding_hits(fs, match="未带条件")
        self.assertEqual([x["title"] for x in hits], ["未带条件的早期返回"])

    def test_both_flags_and(self):
        fs = [untitled_check(), titled("bench", fp=FP)]
        self.assertEqual([x.get("title") for x in m.finding_hits(fs, match="bench", fingerprint=FP)], ["bench"])
        self.assertEqual(m.finding_hits(fs, match="nope", fingerprint=FP), [])

    def test_cli_fingerprint_without_match_parses(self):
        a = m.ap.parse_args(["calibrate", "--pr", "2052", "--fingerprint", FP, "--to", "P3", "--reason", "x"])
        self.assertEqual(a.fingerprint, FP)
        self.assertFalse(a.match)

    def test_cli_still_accepts_match_only(self):
        a = m.ap.parse_args(["calibrate", "--pr", "1215", "--match", "未带", "--to", "P2", "--reason", "x"])
        self.assertEqual(a.match, "未带")
        self.assertFalse(getattr(a, "fingerprint", None))

    def test_calibrate_dry_run_untitled_via_fingerprint(self):
        d, _ = report(findings=[untitled_check()], checks=[e2e("unavailable")])
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "PR-2052.json")
            with open(path, "w") as fh: json.dump(d, fh)
            buf = io.StringIO()
            with mock.patch.object(m, "RPT", tmp), redirect_stdout(buf):
                m.calibrate(args(fingerprint=FP, to="误报"))
            out = buf.getvalue()
            self.assertIn("DRY", out)
            self.assertIn(FP, out)
            self.assertIn("误报", out)
            self.assertIn("partially-verified", out)
            # original file untouched
            with open(path) as fh: self.assertEqual(json.load(fh)["prs"][0]["findings"][0]["severity"], "P2")
        finally:
            for fn in os.listdir(tmp):
                os.remove(os.path.join(tmp, fn))
            os.rmdir(tmp)

    def test_calibrate_rejects_missing_selector(self):
        with self.assertRaises(SystemExit) as e:
            m.calibrate(args(match=None, fingerprint=None))
        self.assertIn("--fingerprint", str(e.exception))
        self.assertIn("--match", str(e.exception))

    def test_calibrate_rejects_zero_or_many(self):
        d, _ = report(findings=[untitled_check(), untitled_check(FP, test="dup")])
        tmp = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmp, "PR-2052.json"), "w") as fh: json.dump(d, fh)
            with mock.patch.object(m, "RPT", tmp), redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as e:
                    m.calibrate(args(fingerprint=FP))
            self.assertIn("got 2", str(e.exception))
        finally:
            for fn in os.listdir(tmp):
                os.remove(os.path.join(tmp, fn))
            os.rmdir(tmp)


class RecomputeUnavailable(unittest.TestCase):
    def test_e2e_unavailable_is_partially_verified_not_clean(self):
        f = untitled_check()
        f["severity"] = "P3"
        f["calibration"] = {"to": "误报"}
        d, pr = report(verdict="review-suggested", severity="P2", findings=[f], checks=[e2e("unavailable")])
        m.recompute(d, pr)
        self.assertEqual((pr["severity"], pr["verdict"]), ("P3", "partially-verified"))

    def test_e2e_pass_stays_clean(self):
        f = untitled_check()
        f["severity"] = "P3"
        f["calibration"] = {"to": "误报"}
        d, pr = report(verdict="review-suggested", severity="P2", findings=[f], checks=[e2e("pass")])
        m.recompute(d, pr)
        self.assertEqual((pr["severity"], pr["verdict"]), ("P3", "clean"))

    def test_remaining_p2_stays_review_suggested(self):
        keep = untitled_check(OTHER, sev="P2", test="other")
        gone = untitled_check()
        gone["severity"] = "P3"
        gone["calibration"] = {"to": "P3"}
        d, pr = report(findings=[keep, gone], checks=[e2e("unavailable")])
        m.recompute(d, pr)
        self.assertEqual((pr["severity"], pr["verdict"]), ("P2", "review-suggested"))

    def test_existing_clean_upgraded_when_e2e_unavailable(self):
        d, pr = report(verdict="clean", severity="P3", findings=[], checks=[e2e("unavailable")])
        m.recompute(d, pr)
        self.assertEqual(pr["verdict"], "partially-verified")


if __name__ == "__main__":
    unittest.main(verbosity=2)
