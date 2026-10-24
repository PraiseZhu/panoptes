#!/usr/bin/env python3
"""opsfix.recompute vs the Grok line's hand calibration of PR-1145 (10-22). Read-only: works on in-memory copies.
Run: python3 pi/test/test_opsfix_recompute.py"""
import json, os, sys, importlib.util
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
spec = importlib.util.spec_from_file_location("opsfix", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "opsfix.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
R = "/workspace/mivo-qa/reports/merged/"
if not os.path.exists(R + "PR-1145.json.bak-20261022-calibration"):
    print("skip: needs the box's 10-22 PR-1145 reports (box-only data)"); sys.exit(0)
old = json.load(open(R + "PR-1145.json.bak-20261022-calibration")); grok = json.load(open(R + "PR-1145.json"))
p, g = old["prs"][0], grok["prs"][0]
for x in p["findings"]:
    if x.get("severity") == "P1" and x.get("source") == "code-review": x["severity"] = "P2"; x["calibration"] = {"to": "P2"}
m.recompute(old, p)
assert (p["severity"], p["verdict"]) == (g["severity"], g["verdict"]), (p["severity"], p["verdict"])
assert p["code_review"]["gate"] == g["code_review"]["gate"], p["code_review"]["gate"]
assert p["code_review"]["status"] == g["code_review"]["status"] == "passed"
assert old["summary"]["prs_by_severity"] == grok["summary"]["prs_by_severity"]
print("ok: recompute matches Grok PR-1145 calibration (severity, verdict, gate, status, prs_by_severity)")
