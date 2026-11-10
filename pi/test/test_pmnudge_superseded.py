"""python3 test/test_pmnudge_superseded.py — 10-28：被已合并 PR 取代的 draft 不催；关闭的 PR 不在 open 列表里本来就不催。
11-10：按 (QA 单号, 问题指纹) 比（见 test_pmnudge_superseded_fp.py）。#1345 的分支名与标记为真实数据（QA #1245，mq1-9dcca00123650c75e909）；
#1343（fix/qa-1245-guard-count，正文无指纹）现在保守照催，不再判被 #1345 取代。"""
import os, sys, tempfile, importlib.util
os.environ.setdefault("PI_STATE", tempfile.mkdtemp())
B = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
s = importlib.util.spec_from_file_location("pmnudge", f"{B}/pmnudge.py"); M = importlib.util.module_from_spec(s); s.loader.exec_module(M)
FP = "mq1-9dcca00123650c75e909"
M.gh = lambda a: [{"number": 1345, "headRefName": f"qa-fix/fixpr-1245-{FP}", "body": f"QA: #1245\n<!-- pi-line fixpr:1245:{FP} -->\n"},
                  {"number": 1300, "body": "QA：#1200 QA: #1201"}] if "merged" in a else []
f = M.merged_refs()
assert f == {(1245, FP): 1345, (1200, None): 1300, (1201, None): 1300}, f
again = {"number": 1344, "headRefName": f"qa-fix/fixpr-1245-{FP}-a2"}
assert M.superseded(again, [1245], f) == [1345]                                     # 同指纹的另一次尝试 -> 被取代
assert not M.superseded(dict(again, number=1345), [1245], f)                          # 自己不算取代自己
assert not M.superseded({"number": 1343, "headRefName": "fix/qa-1245-guard-count", "body": "QA: #1245"}, [1245], f)  # 无指纹 -> 照催
assert not M.superseded(dict(again, body=f"<!-- pi-line fixpr:1337:mq1-{'a' * 20} -->"), [1245, 1337], f)  # 还有没修的 -> 照催
assert "--state" in open(f"{B}/pmnudge.py").read() and '"open"' in open(f"{B}/pmnudge.py").read()   # 只看 open PR
print("ok test_pmnudge_superseded")
