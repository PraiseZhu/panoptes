"""python3 test/test_pmnudge_superseded.py — 10-28：被已合并 PR 取代的 draft（#1343 / #1345 修 QA #1245）不催；关闭的 PR 不在 open 列表里本来就不催。"""
import os, sys, tempfile, importlib.util
os.environ.setdefault("PI_STATE", tempfile.mkdtemp())
B = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
s = importlib.util.spec_from_file_location("pmnudge", f"{B}/pmnudge.py"); M = importlib.util.module_from_spec(s); s.loader.exec_module(M)
M.gh = lambda a: [{"number": 1345, "body": "QA: #1245\n"}, {"number": 1300, "body": "QA：#1200 QA: #1201"}] if "merged" in a else []
f = M.merged_refs()
assert f == {1245: 1345, 1200: 1300, 1201: 1300}, f
assert M.superseded({"number": 1343}, [1245], f)
assert not M.superseded({"number": 1345}, [1245], f)          # 自己不算取代自己
assert not M.superseded({"number": 1350}, [1245, 1337], f)    # 还有没修的单号 -> 照催
assert "--state" in open(f"{B}/pmnudge.py").read() and '"open"' in open(f"{B}/pmnudge.py").read()   # 只看 open PR
print("ok test_pmnudge_superseded")
