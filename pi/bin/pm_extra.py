"""pm.py 附加三块（2026-10-23 补）：交接仓验收、修复 PR 复测验收、前一天催办复查。只读 gh 和本地文件。
规则：Mivo 测试项目经理.md「交接仓验收」「修复 PR 的测试规则」§3 复查；daily-runbook.md B/C。"""
import json, os, re, glob, base64, difflib, datetime as dt
from lib import gh, QA, RPT, BASE, CST, parse_ts, now_cst, REPO

ROUTES = "/workspace/panoptes/report/handoff-routes.json"
SAME_FILES = [".github/ISSUE_TEMPLATE/config.yml", ".github/ISSUE_TEMPLATE/qa-finding.md", "AGENTS.md", "CLAUDE.md", "README.md", "templates/issue.md", "templates/receipt.md"]

def _file(repo, path):
    try: return base64.b64decode(gh(["api", f"repos/{repo}/contents/{path}"])["content"]).decode()
    except Exception: return None

def handoff(day, prs, p01, p2s, miss, unv):
    nxt = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
    rt = json.load(open(ROUTES))["routes"]
    route = {a: r for r in rt for a in r["authors"]}
    author = {p["number"]: (p.get("author") or {}).get("login") for p in prs}
    L = ["## 11. 交接仓验收（路由 " + ROUTES + "）"]
    issues = {}
    for r in rt:
        try: issues[r["repo"]] = gh(["issue", "list", "-R", r["repo"], "--state", "all", "--limit", "200", "--json", "number,title,state,createdAt,comments"])
        except Exception as e: issues[r["repo"]] = []; unv.append(f"交接仓 {r['repo']} issue 列表: {str(e)[:60]}")
    def find(n):
        return [(repo, i) for repo, its in issues.items() for i in its if re.search(rf"#{n}\b", i["title"])]
    # 1 issue 开对、无重复（P0/P1 必开；P2 有建议的也该开——报告无“建议”字段，列出供核）
    ok = bad = 0; rows = []
    exp = [(n, lv) for n, (lv, _, _) in p01.items()] + [(n, "P2") for n in sorted(set(x for x, _ in p2s))]
    for n, lv in exp:
        a = author.get(n); r = route.get(a)
        got = find(n); lv_got = [(repo, i) for repo, i in got if f"[{lv}]" in i["title"]]
        if not r:
            if got: bad += 1; rows.append(f"#{n} {lv}（{a} 不在路由）却开了 {', '.join(f'{x}#{i['number']}' for x, i in got)} ❌")
            continue
        wrong = [f"{x}#{i['number']}" for x, i in got if x != r["repo"]]
        if lv in ("P0", "P1"):
            good = bool(lv_got) and not wrong
            ok += good; bad += not good
            rows.append(f"#{n} {lv}（{a}→{r['repo']}）：" + (", ".join(f"{x}#{i['number']}/{i['state']}" for x, i in lv_got) or "未开") + (f"；仓错 {wrong}" if wrong else "") + (" ✓" if good else " ❌"))
            if not good: miss.append(f"交接 #{n} {lv} {('未开 issue' if not lv_got else '开错仓')}（应在 {r['repo']}）")
        else:
            rows.append(f"#{n} P2（{a}→{r['repo']}）：" + (", ".join(f"{x}#{i['number']}" for x, i in lv_got) or "未开（若有修改建议则应开，需人工核）"))
        if len(lv_got) > len(set((x, i['title']) for x, i in lv_got)): miss.append(f"交接 #{n} {lv} 重复 issue")
    L.append(f"1. P0/P1 issue 开对：{ok}/{ok+bad}" + ("" if bad == 0 else " ❌"))
    L += [f"   - {x}" for x in rows] or ["   - 数据日没有走交接仓的作者"]
    # 2 feedback.md + ledger；3 receipts.md
    for r in rt:
        fb = _file(r["repo"], f"daily/{nxt[:4]}/{nxt[5:7]}/{nxt}/feedback.md")
        rc = {d: _file(r["repo"], f"daily/{d[:4]}/{d[5:7]}/{d}/receipts.md") for d in (day, nxt)}
        L.append(f"2/3. {r['repo']}：feedback {nxt} {'有' if fb else '缺'}；receipts {day} {'有' if rc[day] else '缺'}、{nxt} {'有' if rc[nxt] else '缺'}" +
                 (f"（{nxt} 扫描 {len(re.findall(r'^## ', rc[nxt] or '', re.M))} 节）" if rc[nxt] else ""))
        if not fb: miss.append(f"交接 {r['repo']} 缺 daily/{nxt}/feedback.md")
        for d, t in rc.items():
            if not t: miss.append(f"交接 {r['repo']} 缺 {d} receipts.md")
    led = f"{QA}/ledger/daily/{day}.json"
    L.append(f"2. 台账 {os.path.basename(led)}：{'有' if os.path.exists(led) else '缺'}")
    # 4 逾期（>72h 未关）是否已提醒：receipts 或 issue 评论里出现“逾期”
    od = []
    for repo, its in issues.items():
        for i in its:
            if i["state"] == "OPEN" and re.match(r"\[P[0-2]\]", i["title"]) and (now_cst() - parse_ts(i["createdAt"])).total_seconds() > 72 * 3600:
                rc_all = "".join(_file(repo, f"daily/{d[:4]}/{d[5:7]}/{d}/receipts.md") or "" for d in (day, nxt))
                warned = bool(re.search(rf"#{i['number']}\b[^\n]*逾期|逾期[^\n]*#{i['number']}\b", rc_all)) or any("逾期" in (c.get("body") or "") for c in i.get("comments") or [])
                due = parse_ts(i["createdAt"]).astimezone(CST) + dt.timedelta(hours=72)
                scan = due.replace(hour=15, minute=30, second=0)
                if scan < due: scan += dt.timedelta(days=1)
                if not warned and now_cst() < scan:
                    od.append(f"{repo}#{i['number']}（{due.strftime('%m-%d %H:%M')} 到 72h，等 {scan.strftime('%m-%d')} 15:17 扫描提醒）"); continue
                od.append(f"{repo}#{i['number']}{'✓已提醒' if warned else '❌未提醒'}")
                if not warned: miss.append(f"交接 {repo}#{i['number']} 超 72h 未见逾期提醒")
    L.append("4. 逾期（>72h 仍开）：" + ("、".join(od) or "无（不适用）"))
    # 5 两仓规则文件一致（替换人名/账号/仓名后逐个比）
    toks = sorted({t for r in rt for t in [r["repo"], r["owner"], r["repo"].split("/")[1]] + r["authors"]}, key=len, reverse=True)
    norm = lambda s: re.sub("|".join(map(re.escape, toks)), "<X>", s or "")
    a, b = rt[0]["repo"], rt[1]["repo"]; diff = []
    for f in SAME_FILES:
        fa, fb2 = _file(a, f), _file(b, f)
        if fa is None or fb2 is None: diff.append(f"{f}(缺)")
        else:
            la, lb = norm(fa).splitlines(), norm(fb2).splitlines()
            def name_only(x, y):  # 差异段都很短（人名，如 严健/钟行天、"并抄送 @ 严健"）才算只差人名
                ops = difflib.SequenceMatcher(None, x, y).get_opcodes()
                return all(t == "equal" or max(i2 - i1, j2 - j1) <= 40 for t, i1, i2, j1, j2 in ops)
            sm = difflib.SequenceMatcher(None, la, lb, autojunk=False)
            real = []
            for t, i1, i2, j1, j2 in sm.get_opcodes():
                if t == "equal": continue
                if t == "replace" and i2 - i1 == j2 - j1 and all(name_only(x, y) for x, y in zip(la[i1:i2], lb[j1:j2])): continue
                real.append(f"{t}@{i1+1}")
            if real: diff.append(f"{f}({','.join(real[:3])})")
    L.append(f"5. 两仓规则文件一致（{len(SAME_FILES)} 个；替换账号/仓名，逐行差异只在人名这类短片段时视为一致）：" + ("✓" if not diff else "❌ " + ", ".join(diff)))
    if diff: miss.append(f"交接仓规则文件不一致：{', '.join(diff)}（人名以外的差异需人工确认）")
    return L + [""]

def retest(day, prs, miss):
    nums = {p["number"] for p in prs}
    fixes = [(p["number"], [int(x) for x in re.findall(r"QA[:：]\s*#(\d+)", p.get("body") or "")]) for p in prs]
    fixes = [(n, r) for n, r in fixes if r]
    led = sorted(glob.glob(f"{QA}/ledger/daily/*.json")); led = json.load(open(led[-1])) if led else {"rows": [], "needs_you": []}
    byfix = {}
    for r in led["rows"]:
        for f in r.get("fix_prs") or []: byfix.setdefault(f["pr"], []).append(r)
    L = ["## 12. 修复 PR 验收", f"- 数据日合并的修复 PR（正文含 QA: #N）：{len(fixes)} 个" + ("：" + " ".join(f"#{n}(QA {','.join('#'+str(x) for x in r)})" for n, r in fixes) if fixes else "")]
    rt_ok = 0; wb = 0; pend = []
    for n, refs in fixes:
        try: p = json.load(open(f"{RPT}/PR-{n}.json"))["prs"][0]
        except Exception: p = {}
        has = any(k in p for k in ("retest", "original_case_retest", "targeted_regression")) or p.get("run_mode") in ("fix-retest", "targeted")
        rt_ok += has
        rows = byfix.get(n, [])
        if rows and all(r.get("retest") not in (None, "pending") for r in rows): wb += 1
        else: pend.append(n)
    L.append(f"- 原用例复测 + 定向回归：{rt_ok}/{len(fixes)}（报告里有 retest/定向回归字段，或 run_mode=fix-retest/targeted）")
    L.append(f"- 回写原问题（台账 retest 已出结果）：{wb}/{len(fixes)}" + (f"；仍 pending/未关联：{' '.join('#'+str(x) for x in pend)}" if pend else ""))
    rounds = [r.get("fix_round") or 0 for r in led["rows"]]
    third = list({r["pr"]: r for r in led["rows"] if (r.get("fix_round") or 0) >= 3}.values())
    needs = json.dumps(led.get("needs_you") or [], ensure_ascii=False)
    unrep = [r for r in third if f"#{r['pr']}" not in needs and str(r["pr"]) not in needs]
    L.append(f"- 轮次字段：台账 {'有' if any('fix_round' in r for r in led['rows']) else '缺'}（最高 {max(rounds or [0])}）；第三轮 {len(third)} 条" + (f"，未进早报「需要你做」：{' '.join('#'+str(r['pr']) for r in unrep)} ❌" if unrep else "，均已报出或无"))
    if fixes and rt_ok < len(fixes): miss.append(f"修复 PR 复测：{len(fixes)-rt_ok}/{len(fixes)} 没走原用例复测+定向回归（运维）")
    if pend: miss.append(f"修复 PR 回写：{len(pend)} 个 retest 仍 pending（日报）")
    for r in unrep: miss.append(f"第三轮未报出：#{r['pr']} round={r.get('fix_round')}（日报）")
    return L + [""]

def prev_review(day, miss_today, od):
    """前一天催办复查：前一日的遗漏清单（Pi 的 <D-1>.json，或 Grok pm-daily 的催办表）逐条看今天是否还在。"""
    prv = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
    L = [f"## 13. 前一天（{prv}）催办复查"]
    pj = f"{od}/{prv}.json"
    if os.path.exists(pj):
        old = json.load(open(pj)).get("missing", [])
        key = lambda s: re.sub(r"\d+ 分钟|\d+/\d+|（[^）]*）", "", s).strip()
        now = {key(x) for x in miss_today}
        for x in old:
            # 当天性的 G1/G3/G5 指标是“已发生”事实，不能关闭，只记“已发生”
            if x.startswith("P2 未裁决"):
                from pm import ruled
                left = [n for n in re.findall(r"#(\d+)", x) if int(n) not in ruled("修") | ruled("不修")]
                L.append(f"- {x} → " + (f"❌ 仍未裁决：{' '.join('#'+n for n in left)}" if left else "✓ 已全部裁决"))
            elif re.match(r"G[135] ", x): L.append(f"- {x} → 已发生（不可补救，看运维/日报是否给了原因）")
            else: L.append(f"- {x} → {'❌ 未关' if key(x) in now else '✓ 已关'}")
        if not old: L.append("- 前一天无催办")
    else:
        g = f"/workspace/fleet-standards/pm-daily/{prv}.md"
        t = open(g).read() if os.path.exists(g) else ""
        m = re.search(r"## \d+\. 催办.*?\n(.*?)(\n## |\Z)", t, re.S)
        if m:
            rows = [r for r in m.group(1).splitlines() if r.startswith("| ") and not r.startswith("| 对象") and "---" not in r]
            L.append(f"- 来源 {g}（Grok 格式），{len(rows)} 条，逐条用 gh 复核待人工；涉及 PR：" + " ".join(sorted(set(re.findall(r"#\d+", m.group(1))))))
        else: L.append(f"- 未核实：没有 {pj}，{g} 里也没有催办表")
    return L + [""]
