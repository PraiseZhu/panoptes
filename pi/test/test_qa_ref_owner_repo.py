"""python3 test/test_qa_ref_owner_repo.py — 11-10：fix_refs / QA_REF 只认「QA: #N」，
修复 PR #1196、#1225、#1541 正文写的是「QA: xindong/mivo-canvas-plugin#N」（#1541 用全角冒号），
opsfix_retest 因此选不上它们。现在也认产品仓的 owner/repo#N；别的仓不算。fixtures 是这三个 PR 的真实标题+正文。"""
import sys, os, json
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("PI_BIN") or os.path.join(os.path.dirname(H), "bin"))
from pm_extra import fix_refs
import opsfix_retest as R

def fx(n):
    return json.load(open(os.path.join(H, "fixtures", f"mivo-pr{n}-title-body.json")))

def refs(body, title=""):
    return fix_refs({"body": body, "title": title})

# 原来的「QA: #N」/ 全角冒号 / 无冒号，继续有效
assert refs("QA: #12") == [12]
assert refs("QA：#34") == [34]
assert refs("QA #943") == [943]
assert R.qa_refs("QA: #12\nQA：#34") == [12, 34]

# 三个修复 PR 正文里的真实写法（#1541 是全角冒号）
assert refs("QA: xindong/mivo-canvas-plugin#1089") == [1089]
assert refs("QA: xindong/mivo-canvas-plugin#1124") == [1124]
assert refs("QA：xindong/mivo-canvas-plugin#1496") == [1496]

# 完整真实正文
assert refs(fx(1196)["body"], fx(1196)["title"]) == [1089]
assert refs(fx(1225)["body"], fx(1225)["title"]) == [1124]
assert refs(fx(1541)["body"], fx(1541)["title"]) == [1496]
# qa_refs 走同一实现，select_fix_prs(--pr) 不再因为空引用跳过
assert R.qa_refs(fx(1196)["body"], fx(1196)["title"]) == [1089]
assert R.qa_refs(fx(1225)["body"], fx(1225)["title"]) == [1124]
assert R.qa_refs(fx(1541)["body"], fx(1541)["title"]) == [1496]
for n, orig in ((1196, 1089), (1225, 1124), (1541, 1496)):
    p = dict(fx(n), number=n, mergedAt="2026-10-01T00:00:00Z")
    got = R.select_fix_prs([p], {}, only={n})
    assert [x["number"] for x in got] == [n], (n, got)
    assert R.qa_refs(p["body"], p["title"]) == [orig]

# 别的仓不算；没有 QA 前缀的 owner/repo#N 也不算
assert refs("QA: other/repo#123") == []
assert refs("QA: PraiseZhu/panoptes#269") == []
assert refs("Closes xindong/mivo-canvas-plugin#1569") == []
