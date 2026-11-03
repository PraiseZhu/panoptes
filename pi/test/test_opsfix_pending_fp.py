"""opsfix retest: a fingerprint the fix PR names is also found in the source report's code-review pending_verification
(AI P1 not confirmed, e.g. #1425/#1451), tagged target_source=pending_verification; unnamed pending items are never
picked; P0–P2 counts (author_findings, as pm.py G2) are unchanged. Fixtures: real reports + real fix-PR title/body."""
import sys, os, json, copy
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("PI_BIN") or os.path.join(os.path.dirname(H), "bin"))
import opsfix_retest as R
from finding_policy import author_findings

def fx(name): return json.load(open(os.path.join(H, "fixtures", name)))
CASES = [(1438, 1425, "mq1-04444d7ca0a293b7c829"), (1465, 1451, "mq1-bfb80b8497f8aaea0b5c")]

def _pick(fix, orig):
    p, rep = fx(f"mivo-pr{fix}-title-body.json"), fx(f"mivo-report-PR{orig}-subset.json")
    return R.pick_targets(p["body"], orig, rep, p.get("title")), rep

def test_named_pending_fp_is_picked_and_tagged():
    for fix, orig, fp in CASES:
        tg, _ = _pick(fix, orig)
        assert [(x["fingerprint"], x.get("target_source"), x["source"]) for x in tg] == [(fp, "pending_verification", "code-review")], fix

def test_unnamed_pending_fp_not_picked():
    rep = fx("mivo-report-PR1451-subset.json")
    assert R.pick_targets("QA: #1451 修了别的", 1451, rep) == []            # no fp named -> nothing from pending
    assert R.pick_targets("QA: #1451 mq1-04444d7ca0a293b7c829", 1451, rep) == []   # fp not in this report
    assert all(x.get("target_source") != "pending_verification" for x in R.pick_targets("code-review", 1451, rep))

def test_fp_named_in_title_only():
    rep = fx("mivo-report-PR1425-subset.json")
    assert [x["fingerprint"] for x in R.pick_targets("QA: #1425", 1425, rep, "fix mq1-04444d7ca0a293b7c829")] == ["mq1-04444d7ca0a293b7c829"]

def test_counts_unchanged():
    for fix, orig, _ in CASES:
        rep0 = fx(f"mivo-report-PR{orig}-subset.json")
        before = [(x.get("severity"), x.get("fingerprint")) for x in author_findings(rep0) if x.get("severity") in ("P0", "P1", "P2")]
        tg, rep = _pick(fix, orig)
        after = [(x.get("severity"), x.get("fingerprint")) for x in author_findings(rep) if x.get("severity") in ("P0", "P1", "P2")]
        assert before == after and all(x["fingerprint"] not in {f for _, f in after} for x in tg), fix
        assert rep == rep0          # source report not mutated
