"""python3 test/test_pmnudge_accept199.py — #199 验收第 1 轮（pi-work/accept-199/ACCEPTANCE.md）：
(1) updatedAt 为 "" / null / 无时区 不崩，回退 now；(2) 限流重置时间向上取到 :05 cron 节点（09–22）；(3) 同一小时多次失败只写一节。"""
import os, sys, tempfile, importlib.util, datetime as dt
os.environ["PI_STATE"] = tempfile.mkdtemp(); os.environ["PI_FLEET_MODE"] = "grok"
B = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
s = importlib.util.spec_from_file_location("pmnudge", f"{B}/pmnudge.py"); M = importlib.util.module_from_spec(s); s.loader.exec_module(M)
CST = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime(2026, 11, 4, 9, 30, tzinfo=CST)

def test_bad_updatedAt_falls_back_to_now():
    for v in ("", None, "2026-11-04T01:00:00", "garbage"):
        assert M.last_activity({"updatedAt": v}, NOW) == NOW.isoformat(), v
        out, st = M.stalls([{"number": 1, "updatedAt": v}], {}, NOW); assert out == [], v

def test_reset_rounds_up_to_cron_slot():
    reset = dt.datetime(2026, 11, 4, 10, 6, 43, tzinfo=CST).timestamp()
    M.gh_rl_until = lambda: reset
    t, _ = M.next_retry(NOW, None); assert t == dt.datetime(2026, 11, 4, 11, 5, tzinfo=CST), t
    M.gh_rl_until = lambda: dt.datetime(2026, 11, 4, 22, 30, tzinfo=CST).timestamp()
    t, _ = M.next_retry(NOW, None); assert t == dt.datetime(2026, 11, 5, 9, 5, tzinfo=CST), t
    M.gh_rl_until = lambda: dt.datetime(2026, 11, 4, 10, 5, tzinfo=CST).timestamp()
    t, _ = M.next_retry(NOW, None); assert t == dt.datetime(2026, 11, 4, 10, 5, tzinfo=CST), t   # 正好 :05 -> 就是这一轮
    M.gh_rl_until = lambda: 0

def test_same_hour_failure_written_once():
    M.now_cst = lambda: NOW
    M.fail_section("2026-11-04", "09:05", RuntimeError("x"), False)
    M.fail_section("2026-11-04", "09:41", RuntimeError("y"), False)
    txt = open(f"{M.BASE}/out/pm/2026-11-04.md").read()
    assert txt.count("本轮未催") == 1, txt

if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"): f()
    print("ok test_pmnudge_accept199")
