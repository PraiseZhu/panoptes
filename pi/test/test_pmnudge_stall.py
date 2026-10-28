"""python3 test/test_pmnudge_stall.py — 10-28：PraiseZhu 所有 open PR 超过 1h 无进展（新 commit / CI 状态变 / 新评论）就催。"""
import os, sys, tempfile, importlib.util, datetime as dt
os.environ.setdefault("PI_STATE", tempfile.mkdtemp())
B = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
s = importlib.util.spec_from_file_location("pmnudge", f"{B}/pmnudge.py"); M = importlib.util.module_from_spec(s); s.loader.exec_module(M)
t0 = M.now_cst(); h = lambda x: t0 + dt.timedelta(hours=x)
pr = {"number": 7, "headRefOid": "a1", "comments": [], "statusCheckRollup": [{"name": "ci", "status": "IN_PROGRESS"}]}
st, new = M.stalls([pr], {}, t0); assert st == [] and "7" in new           # 第一次见：开始计时
st, new2 = M.stalls([pr], new, h(0.9)); assert st == [] and new2 == new     # 未满 1h
st, new3 = M.stalls([pr], new2, h(1.5)); assert [x[0]["number"] for x in st] == [7] and st[0][2] == new["7"]["since"]
for change in ({"headRefOid": "b2"}, {"comments": [{}]}, {"statusCheckRollup": [{"name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"}]}):
    st, n4 = M.stalls([dict(pr, **change)], new3, h(1.5))
    assert st == [] and n4["7"]["since"] != new3["7"]["since"], change     # 有进展 -> 重新计时、不催
st, n5 = M.stalls([], new3, h(2)); assert st == [] and n5 == {}              # 关闭 / 合并的 PR 不在 open 列表 -> 丢弃
cron = open(os.path.join(os.path.dirname(B), "cron", "pi-line.cron")).read()
assert "pmnudge @09,10,11,12,13,14,15,16,17,18,19,20,21,22" in cron        # 北京 09–22 每小时
print("ok test_pmnudge_stall")
