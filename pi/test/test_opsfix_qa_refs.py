"""opsfix retest qa_refs: 正文 QA: #N / QA 来源：#N、标题 (QA #N) 都算修复 PR；纯 #N 不算。fixtures 是 mivo #1440/#1559 的真实标题+正文。"""
import sys, os, json
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("PI_BIN") or os.path.join(os.path.dirname(H), "bin"))
import opsfix_retest as R

def fx(n): return json.load(open(os.path.join(H, "fixtures", f"mivo-pr{n}-title-body.json")))

def test_body_colon_still_works(): assert R.qa_refs("QA: #12\nQA：#34") == [12, 34]
def test_body_laiyuan(): assert R.qa_refs("QA 来源：#1409。历史修复标记") == [1409]
def test_title_paren(): assert R.qa_refs("", "fix: x (QA #1478)") == [1478]
def test_negatives():
    for b in ("plain #123", "fixes #12", "Closes xindong/mivo-canvas-plugin#1569"):
        assert R.qa_refs(b) == [], b
    assert R.qa_refs("", "fix #12") == []
def test_real_1440():
    d = fx(1440); assert R.qa_refs(d["body"], d["title"]) == [1409]
    assert R.qa_refs(d["body"]) == [1409]          # body alone (QA 来源) suffices
def test_real_1559():
    d = fx(1559); assert R.qa_refs(d["body"], d["title"]) == [1478]
def test_select_uses_title():
    p = dict(fx(1559), number=1559, mergedAt="2026-11-02T03:00:00Z")
    assert [x["number"] for x in R.select_fix_prs([p], {}, only={1559})] == [1559]

def test_body_no_colon_and_dui():
    assert R.qa_refs("QA #943") == [943] and R.qa_refs("QA 对 #1449") == [1449]
def test_not_word_suffix(): assert R.qa_refs("FAQA #12") == []
def test_real_1627():
    d = fx(1627); assert R.qa_refs(d["body"], d["title"]) == [943]
def test_real_1677():
    d = fx(1677); assert R.qa_refs(d["body"], d["title"]) == [1449]
def test_real_changelog_1585_not_fix():
    d = fx(1585); assert R.qa_refs(d["body"], d["title"]) == []

import datetime as dt
DONE16 = [1345, 1351, 1392, 1394, 1409, 1443, 1444, 1473, 1476, 1497, 1522, 1620, 1623, 1640, 1644, 1660, 1661]
def _prs():
    out = [dict(fx(n), number=n, mergedAt=m) for n, m in ((1440, "2026-11-02T05:53:45Z"), (1559, "2026-11-02T15:48:26Z"),
           (1627, "2026-11-03T05:21:03Z"), (1677, "2026-11-03T12:15:18Z"), (1585, "2026-11-02T01:00:00Z"))]
    return out + [{"number": n, "title": "fix", "body": "QA: #1", "mergedAt": "2026-10-31T00:00:00Z"} for n in DONE16]
STATE = {"watermark": "2026-11-03T20:30:01+08:00", "done": {str(n): {} for n in DONE16}, "retry": {}}
def test_backfill_window_picks_missed_not_done():
    since = R.parse_at(R.REF_BACKFILL[1])
    got = [p["number"] for p in R.select_fix_prs(_prs(), STATE, since=since)]
    assert got == [1440, 1559, 1627, 1677]
def test_no_backfill_window_misses_them():   # normal watermark window (tag already recorded)
    assert [p["number"] for p in R.select_fix_prs(_prs(), STATE)] == [1677]   # only 1677 is inside the 6h OVERLAP
