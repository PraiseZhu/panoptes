"""pm_extra.handoff 两条误报（fleet-standards/pm-daily/2026-11-02.md §14，数据日 11-02 实测）：
1. 两仓 AGENTS.md：aj0928 仓第 91 行多一句「路滨宇（lubinyu-lab）的 PR 由严健负责：…都认，」——lubinyu-lab 只在 aj0928 的路由里，
   路由本来就不同；旧代码只放过 ≤40 字的差异段 → 报「AGENTS.md(replace@91) 不一致」。人名以外的真差异仍要报。
2. 逾期未提醒：aj0928-handoff #2 #5 #6 #7、handover-mivo #1 #3 在 72h 内（或之后）都有作者「结论：…」回执，按 AGENTS.md
   「72 小时后还没有任何回执的」才通告；旧代码只看有没有「逾期」字样 → 全报「❌未提醒」。没有回执的、只有 QA 自己留言的仍要报。
只用临时目录和假 gh，不读写真实数据。"""
import sys, os, json, base64, tempfile, unittest, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import isolated, assert_inside, BIN  # noqa: E402

A, B = "aj0928/aj0928-handoff", "zhongxingtian-ai/handover-mivo"
ROUTES = {"routes": [{"repo": A, "owner": "aj0928", "authors": ["aj0928", "lubinyu-lab"], "cc": {"lubinyu-lab": ["aj0928"]}},
                     {"repo": B, "owner": "zhongxingtian-ai", "authors": ["zhongxingtian-ai"], "cc": {}}]}
# 真实两仓 AGENTS.md 的第 1/14/91/110 行（11-03 02:35Z 版本）
AG_A = ["# aj0928-handoff 协作约定",
        "1. PR 在 `xindong/mivo-canvas-plugin`，GitHub 作者是 `aj0928`，或者是由严健负责的 `lubinyu-lab`。",
        "在本 issue 下回，或者在原 PR 下回（QA 私信之后的原 PR 评论，同样首行「结论：…」）都认。路滨宇（lubinyu-lab）的 PR 由严健负责：严健的 bot（aj0928 账号）在本 issue 或原 PR 下的回执都认，写了 `QA #<原PR号>` 的修复 PR 由谁开都认。修复 PR 标题或描述写了 `QA #<原PR号>`、已合并的，不用再回执。",
        "日报发出 72 小时后还没有任何回执的，QA 方会发逾期通告：aj0928 的 PR @ 严健，lubinyu-lab 的 PR @ 路滨宇并抄送 @ 严健。每个问题只通告一次。"]
AG_B = ["# handover-mivo 协作约定",
        "1. PR 在 `xindong/mivo-canvas-plugin`，GitHub 作者是 `zhongxingtian-ai`。",
        "在本 issue 下回，或者在原 PR 下回（QA 私信之后的原 PR 评论，同样首行「结论：…」）都认。修复 PR 标题或描述写了 `QA #<原PR号>`、已合并的，不用再回执。",
        "日报发出 72 小时后还没有任何回执的，QA 方会发逾期通告：zhongxingtian-ai 的 PR @ 钟行天。每个问题只通告一次。"]
NOW = dt.datetime(2026, 11, 3, 10, 35, tzinfo=dt.timezone(dt.timedelta(hours=8)))

def c(login, body, at="2026-10-23T02:28:45Z"): return {"author": {"login": login}, "body": body, "createdAt": at}
def iss(n, comments, created="2026-10-23T00:37:56Z", state="OPEN", title=None):
    return {"number": n, "title": title or f"[P1] #11{n:02d} 问题", "state": state, "createdAt": created, "comments": comments}

class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); os.makedirs(f"{self.tmp}/qa/ledger/daily")
        self.routes = f"{self.tmp}/handoff-routes.json"; json.dump(ROUTES, open(self.routes, "w"))

    def run_handoff(self, issues, ag_a=AG_A, ag_b=AG_B):
        files = {(A, "AGENTS.md"): "\n".join(ag_a) + "\n", (B, "AGENTS.md"): "\n".join(ag_b) + "\n"}
        def gh(a, js=True, **k):
            if a[:2] == ["issue", "list"]: return issues.get(a[a.index("-R") + 1], [])
            if a[0] == "api" and "/contents/" in a[1]:
                head, path = a[1][len("repos/"):].split("/contents/"); repo = "/".join(head.split("/")[:2])
                t = files.get((repo, path), "同一份文件\n" if not path.startswith("daily/") else "## 扫描\n")
                return {"content": base64.b64encode(t.encode()).decode()}
            raise AssertionError(f"unexpected gh {a}")
        with isolated(PI_STATE=self.tmp):
            sys.path.insert(0, BIN)
            import pm_extra as X
            X.ROUTES = self.routes; X.QA = f"{self.tmp}/qa"; X.RPT = f"{self.tmp}/qa/reports/merged"
            assert_inside(self.tmp, X.ROUTES, X.QA, X.RPT)
            X.gh = gh; X.now_cst = lambda: NOW
            miss, unv = [], []
            L = X.handoff("2026-11-02", [], {}, [], miss, unv)
        return L, miss

    def test_agents_route_only_author_clause_is_consistent(self):
        L, miss = self.run_handoff({})
        line = next(x for x in L if x.startswith("5. "))
        self.assertTrue(line.endswith("✓"), line)
        self.assertFalse([m for m in miss if "规则文件" in m], miss)

    def test_agents_real_difference_still_reported(self):
        b = list(AG_B); b[2] = b[2].replace("都认。", "都认。另外，钟行天仓的回执必须同时抄送到飞书 MivoCanvas 群并附上修复提交的完整 SHA 才算数，否则一律视为没有回执。")
        L, miss = self.run_handoff({}, ag_b=b)
        self.assertIn("❌ AGENTS.md(replace@", next(x for x in L if x.startswith("5. ")))
        self.assertTrue([m for m in miss if "规则文件不一致" in m])

    def test_overdue_with_receipt_not_reported(self):
        issues = {A: [iss(7, [c("aj0928", "结论：误报，生产路径里不会出现重复\n依据：…")]),
                      iss(6, [c("aj0928", "结论：已修 #1236\n根因：…", "2026-10-26T07:33:33Z")]),   # 72h 之后才回执，也算已响应
                      iss(9, [c("PraiseZhu", "回执：已修 #1123（见 receipts.md），关闭。")]),          # 只有 QA 自己的留言 → 不算回执
                      iss(8, [])],                                                                  # 真逾期、无回执
                  B: [iss(1, [c("zhongxingtian-ai", "结论：已修 #1196\n\n修复 PR：…（已合并）")]),
                      iss(3, [c("zhongxingtian-ai", "结论：误报，Nightly 分组调用 report 时已传入 head_sha")])]}
        L, miss = self.run_handoff(issues)
        od = [m for m in miss if "逾期" in m]
        self.assertEqual(sorted(od), sorted([f"交接 {A}#9 超 72h 未见逾期提醒", f"交接 {A}#8 超 72h 未见逾期提醒"]), od)
        line = next(x for x in L if x.startswith("4. "))
        for s in (f"{A}#7已回执「误报」", f"{A}#6已回执「已修 #1236」", f"{B}#1已回执「已修 #1196」", f"{B}#3已回执「误报」",
                  f"{A}#9❌未提醒", f"{A}#8❌未提醒"):
            self.assertIn(s, line)

if __name__ == "__main__":
    unittest.main()
