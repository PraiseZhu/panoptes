"""python3 test/test_pmnudge_gapless.py — 11-04（pi-work/jasper-1104/FINDINGS.md §2）：
(1) gh pr list 失败（11-03 七轮限流）不再整节缺失：仍写一节，含时间、原因（限流 / 其他）、下次重试时间；
(2) 停滞从 PR 自己的最后活动（updatedAt）起算，不从脚本第一次看到起算（#1710/#1725 一直没被催）。"""
import os, sys, json, time, tempfile, importlib.util, datetime as dt
os.environ["PI_STATE"] = tempfile.mkdtemp(); os.environ["PI_FLEET_MODE"] = "grok"
B = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
s = importlib.util.spec_from_file_location("pmnudge", f"{B}/pmnudge.py"); M = importlib.util.module_from_spec(s); s.loader.exec_module(M)
CST = dt.timezone(dt.timedelta(hours=8))

def run_fail(err):
    def boom(*a, **k): raise err
    M.gh = boom; sys.argv = ["pmnudge.py"]; M.main()
    p = f"{M.BASE}/out/pm/{M.today()}.md"
    return open(p).read() if os.path.exists(p) else ""

def test_ratelimit_still_writes_section():
    rl = getattr(M, "GhRateLimited", RuntimeError)
    until = time.time() + 1800
    rf = getattr(M, "GH_RL_FILE", f"{os.environ['PI_STATE']}/gh-ratelimit.json"); os.makedirs(os.path.dirname(rf), exist_ok=True)
    json.dump({"since": time.time(), "until": until}, open(rf, "w"))
    txt = run_fail(rl("gh pr: GraphQL: API rate limit already exceeded for user ID 290989140"))
    assert f"## 白天催办 {M.now_cst().strftime('%H:')}" in txt, txt
    assert "原因：GitHub 限流" in txt and "下次重试：" in txt, txt
    nxt = txt.split("下次重试：")[1][:11]
    assert nxt >= dt.datetime.fromtimestamp(until, CST).strftime("%m-%d %H:%M"), txt   # 不早于限流重置
    os.remove(rf)

def test_other_failure_still_writes_section():
    p = f"{M.BASE}/out/pm/{M.today()}.md"
    if os.path.exists(p): os.remove(p)   # 同一小时只写一节（#199 验收 3）：换个干净的当天文件
    txt = run_fail(RuntimeError("gh pr: timeout after 120s"))
    assert txt.rsplit("## 白天催办", 1)[1].count("gh 失败（非限流）") == 1 and "timeout" in txt, txt

def test_stall_from_last_activity_not_first_seen():
    now = M.now_cst()
    pr = {"number": 1710, "headRefOid": "a1", "comments": [], "reviews": [], "statusCheckRollup": [],
          "updatedAt": (now - dt.timedelta(hours=3)).astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")}
    out, st = M.stalls([pr], {}, now)    # 第一次见就已 3h 没动 -> 本轮就催
    assert [x[0]["number"] for x in out] == [1710], (out, st)
    assert 2.9 < out[0][1] < 3.1

def test_fields_include_updatedAt():
    assert "updatedAt" in M.FIELDS.split(",")

if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"): f()
    print("ok test_pmnudge_gapless")
