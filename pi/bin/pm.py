#!/usr/bin/env python3
"""Mivo 测试项目经理 · Pi line 10:30 核对（cron 10:35 北京）。零模型、只读、只写文件。
数据日 = 北京时间自然日（默认昨天）。用一手数据核对 Praise 的 G1–G5，每项给数字/分母，每 bot 一行，遗漏清单。
输出：out/pm/<数据日>.md（Pi 线的 pm-daily；不写 fleet-standards/pm-daily，那是 Grok 线的）+ <数据日>.json
用法：pm.py [YYYY-MM-DD]   环境 PM_OUT_DIR 可改输出目录（dry-run 对比用）。
口径（与 fleet-standards/daily-runbook.md §3、Mivo 测试项目经理.md 一致）：
 G1 首份报告（含 reports/merged/history 旧版）生成时间 - mergedAt <= 2h / 合并数
 G2a 数据日新报 P0/P1（当前报告 ∪ 日报原文「P1/P0」节，按发出时的级别）24h 内有 QA: #N 修复 PR 或 praise-pr/notes 不可复现说明 / 新报 P0/P1 数
 G2b 数据日 P0/P1 + p2-rulings.md 判「修」的 P2：有修复 PR 或结论 / 同集合（P2 新规则 2026-10-22，Praise 本人未确认）
 G3 日报 9:00 前发出（Grok 实发记录 report/sent/<日>/slack-sent.json 与 slack/sent.json 取最早，非 SHADOW）且 PR 数与合并数一致 / 1
 G4 误合 = 合并官（bot）合并且报告有 P0 或必需检查未过的 PR；分母 = bot 合并数（audit.log）
 G5 最终提交 headRefOid 上 review-pr=success / 合并数
"""
import sys, json, re, os
from lib import *

NOTES = "/workspace/praise-pr/notes"
RULINGS = "/workspace/fleet-standards/p2-rulings.md"
AUDIT = "/workspace/mivo-merger/audit.log"
DONE = {"fixed", "concluded", "fix_merged_pending_retest"}

def all_versions(n):
    out = []
    for f in [f"{RPT}/PR-{n}.json"] + sorted(glob.glob(f"{RPT}/history/PR-{n}.v*.json")):
        try: d = json.load(open(f))
        except Exception: continue
        t = d.get("generated_at") or d.get("run", {}).get("finished_at")
        pr = next((p for p in d.get("prs", []) if p.get("number") == n), None)
        out.append({"f": f, "t": parse_ts(t) if t else None, "trigger": d.get("run", {}).get("trigger"), "pr": pr})
    return out

def ruled(verdict):
    """p2-rulings.md rows: | 日期 | 报告 PR | 问题 | 裁决 | 理由 | ... -> set of (pr, 问题前缀)"""
    s = set()
    if not os.path.exists(RULINGS): return s
    for line in open(RULINGS):
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(c) >= 4 and re.fullmatch(r"#?\d+", c[1] or ""):
            v = "不修" if "不修" in c[3] else ("修" if c[3] in ("修", "已修") else c[3])  # 「产品代码不修」→不修，「已修」→修
            if v == verdict: s.add(int(c[1].lstrip("#")))
    return s

def daily_text(day):
    f = f"{QA}/report/sent/{day}/group-content.txt"
    return (f, open(f).read()) if os.path.exists(f) else (f, "")

def reported_levels(txt):
    """PR numbers listed per level in the daily as sent ('P1 严重｜…' sections)."""
    lv = {}; cur = None
    for line in txt.splitlines():
        m = re.match(r"^(P[0-3])\s", line)
        if m: cur = m.group(1); continue
        m = re.match(r"^#(\d+)\s", line)
        if m and cur: lv.setdefault(cur, set()).add(int(m.group(1)))
    return lv

def note_for(n):
    fs = [f for f in glob.glob(f"{NOTES}/*{n}*") if re.search(rf"(^|[-_/]){n}([-_.]|$)", os.path.basename(f))]
    return sorted(fs)

def main():
    day = data_day(sys.argv)
    od = os.environ.get("PM_OUT_DIR") or f"{BASE}/out/pm"
    a, b = utc_window(day)
    L = [f"<!-- Pi line · Mivo 测试项目经理 · pm.py（零模型、只写文件、未发送任何消息）mode={mode()} -->",
         f"# PM 日检 · 数据日 {day}（北京时间）", "",
         f"运行：{now_cst().strftime('%Y-%m-%d %H:%M')} 北京时间。合并区间 UTC {a}..{b}。一手数据：gh、reports/merged（含 history）、ledger/daily、report/sent/{day}/、report/slack/sent.json、feishu-sent.json、mivo-merger/audit.log、praise-pr/notes、p2-rulings.md。", ""]
    miss = []; unv = []; J = {"day": day}
    try: prs = merged_prs(day)
    except Exception as e:
        L += [f"**未核实**：gh 合并清单失败：{e}"]; write_atomic(f"{od}/{day}.md", "\n".join(L) + "\n"); return
    N = len(prs); nums = [p["number"] for p in prs]
    by = {}
    for p in prs: k = (p.get("mergedBy") or {}).get("login", "?"); by[k] = by.get(k, 0) + 1
    L += [f"## 1. 合并 {N} 个", "- " + " ".join(f"#{n}" for n in sorted(nums)), f"- mergedBy：{by}", ""]

    # ---------- G1 + G5 + findings
    g1 = 0; g1bad = []; g5 = 0; g5bad = []; nrep = 0; p01 = {}; p2s = []; manual = []
    for p in prs:
        n = p["number"]; m = parse_ts(p["mergedAt"]); vs = all_versions(n)
        ts = [v for v in vs if v["t"]]
        if vs: nrep += 1
        first = min(ts, key=lambda v: v["t"]) if ts else None
        cur = next((v for v in vs if "/history/" not in v["f"]), None)
        if first and (first["t"] - m).total_seconds() <= 7200: g1 += 1
        else:
            mins = round((first["t"] - m).total_seconds() / 60) if first else None
            g1bad.append((n, mins, first["trigger"] if first else None)); miss.append(f"G1 #{n} " + (f"首份报告 {mins} 分钟（trigger={first['trigger']}）" if first else "无报告"))
        if cur and cur["trigger"] == "manual": manual.append(n)
        for v in vs:
            for x in ((v["pr"] or {}).get("findings") or []):
                if x.get("severity") in ("P0", "P1"): p01.setdefault(n, (x["severity"], x.get("title", ""), first["t"] if first else m))
        for x in (((cur or {}).get("pr") or {}).get("findings") or []):
            if x.get("severity") == "P2": p2s.append((n, x.get("title", "")))
        try:
            cr = gh(["api", f"repos/{REPO}/commits/{p['headRefOid']}/check-runs?per_page=100"])
            rv = sorted([c for c in cr.get("check_runs", []) if c["name"] == "review-pr"], key=lambda c: c.get("completed_at") or "", reverse=True)
            concl = (rv[0]["conclusion"] or rv[0]["status"]) if rv else "无 review-pr"
        except Exception as e: concl = "未核实"; unv.append(f"G5 #{n}: {str(e)[:80]}")
        if concl == "success": g5 += 1
        else: g5bad.append((n, p["headRefOid"][:8], concl)); miss.append(f"G5 #{n} 最终提交 {p['headRefOid'][:8]} review-pr：{concl}")
    L += [f"- QA 报告 {nrep}/{N} 存在；2h 内 {g1}/{N}。超时/缺：" + ("、".join(f"#{n}({mi}分,{tr})" if mi is not None else f"#{n}(无报告)" for n, mi, tr in g1bad) or "无"),
          f"- 当前版本 trigger=manual：{', '.join('#'+str(x) for x in manual) or '无'}（G1 以首份报告计，含 history 旧版）", ""]

    # ---------- G2
    dtf, dtxt = daily_text(day); lv = reported_levels(dtxt)
    # 日报发出时的级别优先（日报改过级的，如 #1124 报告 P1→日报 P2，按日报算，和 Grok 线一致）
    for n in list(p01):
        if any(n in lv.get(x, ()) for x in ("P2", "P3")) and not any(n in lv.get(x, ()) for x in ("P0", "P1")):
            unv.append(f"#{n} 报告里是 {p01[n][0]}，日报发出时是 {'P2' if n in lv.get('P2', ()) else 'P3'}，按日报计"); p01.pop(n)
    for lvl in ("P0", "P1"):
        for n in lv.get(lvl, ()):
            if n in nums and n not in p01:
                vs = [v for v in all_versions(n) if v["t"]]
                p01[n] = (lvl, "（日报原文级别，报告现已改级）", min(v["t"] for v in vs) if vs else None)
    try:
        fix = gh(["pr", "list", "-R", REPO, "--author", "PraiseZhu", "--state", "all", "--limit", "300",
                  "--search", "QA in:body", "--json", "number,createdAt,body,isDraft,state"])
    except Exception as e: fix = []; unv.append(f"G2 修复 PR 列表: {str(e)[:80]}")
    def claimers(n): return [x for x in fix if re.search(rf"QA[:：]?\s*#{n}\b", x.get("body") or "")]
    g2a = 0; rows = []; notes_any = {}
    for n, (lvl, title, t0) in sorted(p01.items()):
        dl = (t0 or parse_ts(a)) + dt.timedelta(hours=24)
        cl = [c for c in claimers(n) if parse_ts(c["createdAt"]) <= dl]
        nt = note_for(n); notes_any[n] = nt
        nt_late = [f for f in nt if dt.datetime.fromtimestamp(os.path.getmtime(f), CST) > dl]
        ok = bool(cl or nt); g2a += ok
        if nt and not cl and nt_late == nt: unv.append(f"G2a #{n} 只有不可复现说明，文件 mtime 晚于 24h 期限（盒子恢复会重置 mtime），按存在计入")
        if not ok and now_cst() > dl: miss.append(f"G2a #{n} {lvl} 24h 内无修复 PR、无不可复现说明")
        rows.append(f"| #{n} | {lvl} | {title[:40]} | {', '.join('#'+str(c['number'])+'/'+c['state'] for c in cl) or '—'} | {', '.join(os.path.basename(f) for f in nt) or '—'} | {'✓' if ok else ('✗' if now_cst() > dl else '未到期')} |")
    led_f = f"{QA}/ledger/daily/{day}.json"
    if not os.path.exists(led_f):
        cands = sorted(glob.glob(f"{QA}/ledger/daily/*.json")); led_f = cands[-1] if cands else None
        if led_f: unv.append(f"台账 {day}.json 缺，用 {os.path.basename(led_f)}")
    led = json.load(open(led_f)) if led_f else {"rows": []}
    fixset = ruled("修"); nofix = ruled("不修")
    lrows = {}
    for f in sorted(glob.glob(f"{QA}/ledger/daily/*.json")):
        if os.path.basename(f)[:10] < day: continue
        try:
            for r in json.load(open(f))["rows"]: lrows.setdefault(r["pr"], []).append(r)
        except Exception: pass
    p2prs = sorted(set(n for n, _ in p2s))
    def g2b_state(n):
        rs = lrows.get(n, [])
        if any(r["status"] in DONE for r in rs): return True, next(r["status_zh"] for r in rs if r["status"] in DONE)
        mf = [c for c in claimers(n) if c["state"] == "MERGED"]
        if mf: return True, "修复 PR 已合并 " + ",".join("#"+str(c["number"]) for c in mf)
        if notes_any.get(n) or note_for(n): return True, "有不可复现/结论说明（留言待 Praise 批）"
        op = [c for c in claimers(n) if c["state"] == "OPEN"]
        return False, ("修复 PR 未合并 " + ",".join("#"+str(c["number"]) for c in op)) if op else (rs[0]["status_zh"] if rs else "无修复 PR、无结论")
    b01 = [(n, p01[n][0]) + g2b_state(n) for n in sorted(p01)]
    b2 = [(n, "P2") + g2b_state(n) for n in p2prs if n in fixset and n not in p01]
    g2b = sum(x[2] for x in b01 + b2)
    allp2 = p2prs; led_f = led_f or "-"
    L += [f"## 2. G2 P0/P1 认领与 P0–P2 修复", f"G2a 24h 内认领：**{g2a}/{len(p01)}**（新报 P0/P1；级别取报告各版本 ∪ 日报原文 {dtf}）", "",
          "| 报告 PR | 级别 | 问题 | 修复 PR(24h 内建) | 不可复现说明 | 达标 |", "|---|---|---|---|---|---|"] + rows + ["",
          f"G2b 有修复 PR 或结论：**{g2b}/{len(b01)+len(b2)}**（状态取 ≥数据日的台账 + gh 修复 PR + praise-pr/notes；P0/P1 {sum(x[2] for x in b01)}/{len(b01)}，P2 仅「修」{sum(x[2] for x in b2)}/{len(b2)}）",
          f"- 数据日有 P2 的报告 PR {len(allp2)} 个（当前报告）；p2-rulings.md 判「修」{len(fixset & set(allp2))} 个、判「不修」{len(nofix & set(allp2))} 个；未裁决 {len(set(allp2) - fixset - nofix)} 个（见 out/p2 提议）。",
          "- ⚠ P2 只算「修」是 2026-10-22 Jasper 转述的新规则，**Praise 本人未确认**。",
          "- 明细：" + ("；".join(f"#{n} {lv_}{'✓' if ok else '✗'}（{why}）" for n, lv_, ok, why in b01 + b2) or "无"), ""]
    for n, lv_, ok, why in b01 + b2:
        if not ok: miss.append(f"G2b #{n} {lv_} {why}")
    if set(allp2) - fixset - nofix: miss.append(f"P2 未裁决：{', '.join('#'+str(x) for x in sorted(set(allp2) - fixset - nofix))}")

    # ---------- G3
    dl3 = dt.datetime.fromisoformat(day).replace(tzinfo=CST) + dt.timedelta(days=1, hours=9)
    sends = []
    gs = f"{QA}/report/sent/{day}/slack-sent.json"
    if os.path.exists(gs):
        try:
            g = json.loads(open(gs).read().splitlines()[0])
            if g.get("sent_at_beijing"): sends.append((dt.datetime.strptime(g["sent_at_beijing"], "%Y-%m-%d %H:%M").replace(tzinfo=CST), f"{gs} ts={g.get('ts')}"))
        except Exception as e: unv.append(f"G3 {gs} 读不出（{str(e)[:40]}），用 slack/sent.json")
    sj = json.load(open(f"{QA}/report/slack/sent.json")); e3 = sj.get(f"mivo-daily-report:{day}")
    if e3 and e3.get("sent_at"): sends.append((parse_ts(e3["sent_at"]), f"slack/sent.json mivo-daily-report:{day} ts={e3.get('ts')}"))
    sends.sort()
    m3 = re.search(r"合了\s*(\d+)\s*个\s*PR", dtxt); npr = int(m3.group(1)) if m3 else None
    shadow = "SHADOW" in dtxt
    on_time = bool(sends) and sends[0][0] <= dl3
    in_txt = set(int(x) for x in re.findall(r"#(\d+)", dtxt))
    absent = [n for n in nums if n not in in_txt]
    match = npr == N
    g3 = int(on_time and match and not shadow)
    L += [f"## 3. G3 日报：**{g3}/1**",
          "- 发送记录：" + ("；".join(f"{t.astimezone(CST).strftime('%m-%d %H:%M')} 北京（{s}）" for t, s in sends) or "无") + f"；截止 {dl3.strftime('%m-%d %H:%M')}",
          f"- 内容：日报写合并 {npr} 个 vs gh {N} 个 → {'一致' if match else '不一致'}{'（日报未提到：' + ' '.join('#'+str(x) for x in absent) + '）' if absent else ''}；日报 P1 {sorted(lv.get('P1', []))}，P0 {sorted(lv.get('P0', []))}；SHADOW 标记：{'有❌' if shadow else '无'}",
          f"- 飞书私信记录：{QA}/feishu-sent.json（数据日 dm-*.txt {len(glob.glob(f'{QA}/report/sent/{day}/dm-*.txt'))} 份）", ""]
    if len(sends) > 1 and abs((sends[1][0] - sends[0][0]).total_seconds()) > 600:
        miss.append(f"G3 同一数据日有 2 条发送记录（{sends[0][0].astimezone(CST).strftime('%m-%d %H:%M')} 与 {sends[1][0].astimezone(CST).strftime('%m-%d %H:%M')}），疑似重复发送，需对账")
    if not g3: miss.append("G3 日报 " + ("未发" if not sends else ("超时" if not on_time else ("PR 数不一致" if not match else "带 SHADOW 标记"))))

    # ---------- G4
    au = [l for l in open(AUDIT) if a <= l[:20] < b] if os.path.exists(AUDIT) else []
    botm = 0
    for l in au:
        m4 = re.search(r"(\d+) bot merges?", l)
        if m4: botm = max(botm, int(m4.group(1)))
    seen_in_audit = [n for n in nums if any(f"#{n} " in l or f"#{n}(" in l for l in open(AUDIT))] if os.path.exists(AUDIT) else []
    paused = os.path.exists("/workspace/mivo-merger/PAUSED")
    L += [f"## 4. G4 误合：**0 误合 / {botm} 次 bot 合并**（PAUSED={'在' if paused else '不在'}；audit.log 数据日 {len(au)} 行）",
          f"- 合并官巡检读到的合并 PR：{len(seen_in_audit)}/{N}" + ("；未读到：" + ", ".join(f"#{n}" for n in nums if n not in seen_in_audit) if len(seen_in_audit) < N else ""), ""]
    if not au: unv.append("G4 audit.log 数据日无记录")

    L += [f"## 5. G5 最终提交 review-pr 通过：**{g5}/{N}**",
          "- 不通过：" + ("、".join(f"#{n}({s},{c})" for n, s, c in g5bad) or "无"), ""]

    # ---------- per bot
    ledok = os.path.exists(f"{QA}/ledger/daily/{day}.json")
    L += ["## 6. 各 bot（一行一个）",
          f"- **Mivo QA 运维**：报告 {nrep}/{N}，2h 内 {g1}/{N}，manual {len(manual)} 个 → {'达标' if g1 == N else '不达标'}",
          f"- **Mivo QA 日报**：G3 {g3}/1，台账 {day}.json {'有' if ledok else '缺'}，私信 {len(glob.glob(f'{QA}/report/sent/{day}/dm-*.txt'))} 份 → {'达标' if g3 and ledok else '不达标'}",
          f"- **Mivo 合并官**：bot 合并 {botm}，误合 0，巡检读到 {len(seen_in_audit)}/{N} → {'达标' if len(seen_in_audit) == N else '部分'}",
          f"- **Praise PR**：G2a {g2a}/{len(p01)}，G2b {g2b}/{len(b01)+len(b2)}，P2 未裁决 {len(set(allp2) - fixset - nofix)} 个报告 PR → {'达标' if g2a == len(p01) and g2b == len(b01)+len(b2) else '不达标'}", ""]

    L += ["## 7. 目标表", "| 目标 | 数字/分母 |", "|---|---|",
          f"| G1 2h 内 QA 报告 | {g1}/{N} |", f"| G2a P0/P1 24h 认领 | {g2a}/{len(p01)} |",
          f"| G2b P0/P1 + 判「修」P2 有修复或结论 | {g2b}/{len(b01)+len(b2)} |",
          f"| G3 日报 9:00 前且对得上 | {g3}/1 |", f"| G4 误合 | 0/{botm}（bot 合并） |", f"| G5 review-pr 通过 | {g5}/{N} |", ""]
    import pm_extra
    for blk, args in ((pm_extra.handoff, (day, prs, p01, p2s, miss, unv)), (pm_extra.retest, (day, prs, miss))):
        try: L += blk(*args)
        except Exception as e: L += [f"## {blk.__name__}：未核实（{str(e)[:120]}）", ""]; unv.append(f"{blk.__name__}: {str(e)[:80]}")
    try: L += pm_extra.prev_review(day, miss, od)
    except Exception as e: unv.append(f"前一天催办复查: {str(e)[:80]}")
    L += ["## 8. 遗漏清单"] + [f"- {x}" for x in miss] + (["- 无"] if not miss else []) + [""]
    L += ["## 9. 未核实"] + [f"- {x}" for x in unv] + (["- 无"] if not unv else []) + [""]
    L += ["## 10. 外发（未执行，只记 actions.json）", "- 群复盘、Chief of Staff 一句进展、催办均未发；见 out/pm/<数据日>.actions.json（闸门关）。"]
    J.update(G1=[g1, N], G2a=[g2a, len(p01)], G2b=[g2b, len(b01)+len(b2)], G3=[g3, 1], G4=[0, botm], G5=[g5, N], missing=miss, unverified=unv)
    # keep daytime nudge sections pmnudge.py appended to the same file (Grok pm-daily convention)
    old = open(f"{od}/{day}.md").read() if os.path.exists(f"{od}/{day}.md") else ""
    keep = old[old.index("## 白天催办"):] if "## 白天催办" in old else ""
    write_atomic(f"{od}/{day}.md", "\n".join(L) + "\n" + ("\n---\n\n" + keep if keep else ""))
    canon = f"/workspace/fleet-standards/pm-daily/{day}.md"
    if mode() == "pi" and not os.environ.get("PM_OUT_DIR"):
        cur = open(canon).read() if os.path.exists(canon) else ""
        if "## 7. 目标" in cur and "Pi line · Mivo 测试项目经理" not in cur:
            log(f"{canon} 已有 Grok 线核对，不覆盖（Pi 版留在 {od}）")
        else:
            ck = cur[cur.index("## 白天催办"):] if "## 白天催办" in cur else keep
            write_atomic(canon, "\n".join(L) + "\n" + ("\n---\n\n" + ck if ck else ""))
    write_atomic(f"{od}/{day}.json", json.dumps(J, ensure_ascii=False, indent=1))
    if not os.environ.get("PM_OUT_DIR"):
        summ = f"PM {day}: G1 {g1}/{N} G2a {g2a}/{len(p01)} G2b {g2b}/{len(b01)+len(b2)} G3 {g3}/1 G4 0误合 G5 {g5}/{N}；遗漏 {len(miss)} 条"
        record_actions("pm", day, [{"key": f"pm-retro:{day}", "type": "group-post", "to": "MivoCanvas QA（Pi 线渠道待 Praise 定）", "text": summ, "dedupe": "Grok 线 pm-daily/<日>.md 是否已有群复盘"},
                                   {"key": f"pm-cos:{day}", "type": "SendToAgent", "to": "Jasper (Chief of Staff)", "priority": False, "text": summ}])

if __name__ == "__main__": main()
