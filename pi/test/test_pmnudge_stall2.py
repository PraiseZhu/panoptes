"""python3 test/test_pmnudge_stall2.py — #81 跟进：每段停滞只催一次（跨小时 / 跨天）、只在真催时写 .md、review 算进展、gh 失败不崩不误催、首跑 main() 写文件。"""
import os, sys, json, tempfile, importlib.util, datetime as dt, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import isolated, assert_inside  # noqa: E402

def script():   # was module level (ran at pytest collection and leaked env / frozen paths into other tests)
    os.environ["PI_STATE"] = tempfile.mkdtemp(); os.environ["PI_FLEET_MODE"] = "grok"
    B = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
    s = importlib.util.spec_from_file_location("pmnudge", f"{B}/pmnudge.py"); M = importlib.util.module_from_spec(s); s.loader.exec_module(M)
    S = os.environ["PI_STATE"]
    assert_inside(S, M.PROGRESS, M.BASE)   # never the real out/pm (it used to overwrite prod pmnudge-progress.json)
    t0 = dt.datetime(2026, 10, 28, 21, 5, tzinfo=M.CST); h = lambda x: t0 + dt.timedelta(hours=x)
    pr = {"number": 7, "title": "x", "isDraft": False, "createdAt": "2026-10-28T00:00:00Z", "body": "", "headRefName": "f",
          "headRefOid": "a1", "comments": [], "reviews": [], "statusCheckRollup": []}
    # stalls(): 跨小时、跨天只催一次；有进展后新一段可再催
    _, st = M.stalls([pr], {}, t0)
    out, st = M.stalls([pr], st, h(1.5)); assert len(out) == 1 and st["7"]["nudged"]
    for x in (2.5, 12):   # 22:35 同天下一小时；次日 09:05 跨天
        out, st = M.stalls([pr], st, h(x)); assert out == [], x
    for change in ({"reviews": [{}]}, {"comments": [{}]}):   # review / review 评论 也算进展
        o2, s2 = M.stalls([dict(pr, **change)], st, h(12)); assert o2 == [] and not s2["7"]["nudged"], change
    _, s2 = M.stalls([dict(pr, reviews=[{}])], st, h(12)); o3, s3 = M.stalls([dict(pr, reviews=[{}])], s2, h(13.5)); assert len(o3) == 1   # 新一段停滞再催一次
    # main()：时间 / gh 打桩
    clock = [t0]; M.now_cst = lambda: clock[0]; M.today = lambda: clock[0].date().isoformat()
    calls = {"fail": False}
    def fake_gh(args, js=True):
        if calls["fail"]: raise RuntimeError("gh down")
        return [dict(pr)] if args[:2] == ["pr", "list"] and "open" in args else []
    M.gh = fake_gh
    run = lambda: (sys.argv.__setitem__(slice(1, None), []), M.main())
    md = lambda d: open(f"{S}/out/pm/{d}.md").read() if os.path.exists(f"{S}/out/pm/{d}.md") else ""
    acts = lambda d: [a for a in json.load(open(f"{S}/out/pm/{d}.nudges.actions.json"))] if os.path.exists(f"{S}/out/pm/{d}.nudges.actions.json") else []
    # gh 失败：不崩、不写文件
    calls["fail"] = True; run(); assert not os.path.exists(f"{S}/out/pm"), os.listdir(S); calls["fail"] = False
    # 首跑：写 .md、actions、进展文件；不催
    run(); D = "2026-10-28"
    assert os.path.exists(f"{S}/out/pm/pmnudge-progress.json") and "白天催办" in md(D) and "催 #7" not in md(D)
    assert os.path.exists(f"{S}/out/pm/{D}.nudges.actions.json")
    clock[0] = h(1.5); run(); assert md(D).count("- 催 #7（停滞）") == 1
    clock[0] = h(2.5); run(); assert md(D).count("- 催 #7（停滞）") == 1          # 下一小时不再写、不再催
    clock[0] = h(12); run(); D2 = "2026-10-29"; assert "催 #7" not in md(D2)     # 次日 09:05 不重催
    assert not [a for a in acts(D2) if "stall" in str(a)], acts(D2)
    calls["fail"] = True; clock[0] = h(13); run(); calls["fail"] = False        # gh 失败不清 nudged
    assert json.load(open(f"{S}/out/pm/pmnudge-progress.json"))["7"]["nudged"]
    print("ok test_pmnudge_stall2")

class Script(unittest.TestCase):
    def test_pmnudge_stall2(self):
        with isolated(): script()   # own env, argv, os.kill and fresh pi/bin imports; all restored afterwards

if __name__ == "__main__":
    unittest.main()
