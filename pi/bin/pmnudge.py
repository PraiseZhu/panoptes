#!/usr/bin/env python3
"""Mivo 测试项目经理 · 白天催办（cron :05，北京 09–22 点每小时）。零模型、只读 gh、只写文件。
范围（Mivo 测试项目经理.md「白天催办」10-21/10-22）：PraiseZhu 名下、正文含「QA: #N」的 open PR，只催 draft：
 (a) draft 超过 2h（从最后一次转 draft 起算，没有则从创建起算）且除 review-pr 外有红；
 (b) 除 review-pr 外全绿（无 pending）仍是 draft。
不催：ready PR（归 Mac mini 盯梢）；review-pr 本身红；keel/lifeline 叠加链；只关联 p2-rulings.md 判「不修」的 P2。
输出：追加到 out/pm/<当天>.md 的「## 白天催办 HH:MM」节；动作写 out/pm/<当天>.nudges.actions.json；
20 点那轮另写 out/pm/<当天>.chief.md（给 Chief=Jasper 的汇总草稿，未发送），末尾附 chief_missed.py 的「系统漏跑」「工作逾期」两节。
10-28 加 (c) 停滞：PraiseZhu 名下所有 open PR（不限 QA 修复 PR、不限 draft）超过 1h 无进展就催；
进展 = 新 commit（head SHA 变）、CI check 状态变、新评论（评论数变）、新 review / review 评论（review 数变）。指纹存 PI_STATE/out/pm/pmnudge-progress.json；
每段停滞只催一次（nudged 标记，跨小时、跨天都不重催），有进展后清零重新计时。
用法：pmnudge.py [--dry-run]（--dry-run 只打印，不写任何文件）
"""
import sys
from lib import *
RULINGS = "/workspace/fleet-standards/p2-rulings.md"
RED = ("FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE")
PEND = ("", "PENDING", "QUEUED", "IN_PROGRESS", "EXPECTED", "WAITING", "REQUESTED")

def nofix_prs():
    s = set()
    if os.path.exists(RULINGS):
        for line in open(RULINGS):
            c = [x.strip() for x in line.strip().strip("|").split("|")]
            if len(c) >= 4 and re.fullmatch(r"#?\d+", c[1] or "") and "不修" in c[3]: s.add(int(c[1].lstrip("#")))
    return s

def p01_prs():
    s = set()
    for f in sorted(glob.glob(f"{QA}/ledger/daily/*.json"))[-1:]:
        for r in json.load(open(f))["rows"]:
            if r["level"] in ("P0", "P1"): s.add(r["pr"])
    return s

def merged_refs():
    """QA 单号 -> 修掉它的已合并 PraiseZhu PR 号（最近 100 个）"""
    out = {}
    try:
        for p in gh(["pr", "list", "-R", REPO, "--author", "PraiseZhu", "--state", "merged", "--limit", "100", "--json", "number,body"]):
            for r in re.findall(r"QA[:：]\s*#(\d+)", p.get("body") or ""): out.setdefault(int(r), p["number"])
    except Exception: pass
    return out

def superseded(p, refs, fixed):
    """open PR 的 QA 单号全都已被别的已合并 PR 修掉"""
    return bool(refs) and all(r in fixed and fixed[r] != p["number"] for r in refs)

def draft_since(n, created):
    try:
        ev = gh(["api", f"repos/{REPO}/issues/{n}/timeline?per_page=100", "--paginate"])
        t = [e["created_at"] for e in ev if e.get("event") == "convert_to_draft"]
        if t: return parse_ts(max(t)), "转 draft"
    except Exception: pass
    return parse_ts(created), "创建"

STALL_H = 1.0
PROGRESS = f"{BASE}/out/pm/pmnudge-progress.json"

def progress_sig(p):
    """进展指纹：head commit + 每个 check 的状态 + 评论数 + review 数（行内 review 评论也会产生一条 review）；任一变化即算有进展"""
    cs = sorted(f"{c.get('name') or c.get('context') or '?'}={(c.get('conclusion') or c.get('state') or c.get('status') or '').upper()}"
                for c in p.get("statusCheckRollup") or [])
    return json.dumps([p.get("headRefOid") or "", cs, len(p.get("comments") or []), len(p.get("reviews") or [])], ensure_ascii=False)

def stalls(prs, state, now):
    """返回 (本轮要催的停滞列表 [(pr, 小时数, since_iso)], 新 state)。新 PR / 指纹变了 -> 从 now 重新计时、清 nudged；
    同一段停滞已催过（nudged）-> 不再返回"""
    new, out = {}, []
    for p in prs:
        k, sig = str(p["number"]), progress_sig(p)
        old = state.get(k) or {}
        same = old.get("sig") == sig and old.get("since")
        since = old["since"] if same else now.isoformat()
        new[k] = {"sig": sig, "since": since, "nudged": bool(same and old.get("nudged"))}
        h = (now - parse_ts(since)).total_seconds() / 3600
        if h > STALL_H and not new[k]["nudged"]:
            out.append((p, h, since)); new[k]["nudged"] = True
    return out, new

def main():
    dry = "--dry-run" in sys.argv
    d = today(); hh = now_cst().strftime("%H:%M")
    try:
        prs = gh(["pr", "list", "-R", REPO, "--author", "PraiseZhu", "--state", "open", "--limit", "100",
                  "--json", "number,title,isDraft,createdAt,body,headRefName,headRefOid,comments,reviews,statusCheckRollup"])
    except Exception as e:   # gh 挂了：不崩、不催、不动进展文件（下轮再看）
        log("pmnudge: gh pr list 失败，本轮跳过：", str(e)[:200]); return
    nof, p01 = nofix_prs(), p01_prs()
    fixed = merged_refs()   # 10-28：QA 单号已被合并的修复 PR 修掉 -> 这个 draft 是重复 / 被取代的，不催（#1343 被 #1345 取代）
    qa, skip = [], []
    for p in prs:
        refs = [int(x) for x in re.findall(r"QA[:：]\s*#(\d+)", p.get("body") or "")]
        if not refs: continue
        if re.search(r"keel|lifeline", (p.get("headRefName") or "") + " " + p["title"], re.I): skip.append(f"#{p['number']} keel/lifeline 叠加链"); continue
        if all(r in nof and r not in p01 for r in refs): skip.append(f"#{p['number']} 只关联判「不修」的 P2（{refs}）"); continue
        if superseded(p, refs, fixed): skip.append(f"#{p['number']} 被已合并的 {'、'.join(sorted(set('#' + str(fixed[r]) for r in refs)))} 取代"); continue
        p["refs"] = refs; qa.append(p)
    st = lambda c: (c.get("conclusion") or c.get("state") or c.get("status") or "").upper()
    nm = lambda c: c.get("name") or c.get("context") or "?"
    L = [f"## 白天催办 {hh}（Pi 线，gh 实查；未发送）",
         f"PraiseZhu open PR {len(prs)} 个，其中 QA 修复 PR {len(qa)} 个（draft {sum(p['isDraft'] for p in qa)}）。"]
    acts, ready, notyet = [], [], []
    for p in sorted(qa, key=lambda p: p["number"]):
        n = p["number"]
        if not p["isDraft"]: ready.append(f"#{n}"); continue
        cs = [c for c in p.get("statusCheckRollup") or [] if nm(c) != "review-pr"]
        red = [nm(c) for c in cs if st(c) in RED]
        pend = [nm(c) for c in cs if c.get("status", "").upper() not in ("COMPLETED", "") or st(c) in PEND]
        since, how = draft_since(n, p["createdAt"]); age = (now_cst() - since).total_seconds() / 3600
        why = None
        if "<!-- pi-line fixpr:" in (p.get("body") or ""):   # 10-24: the bot's own fix PRs -- prwatch / prfix --revise move them, DM only if stuck
            notyet.append(f"#{n}（bot 自己的修复 PR，bot 自己推进，推不动会单独私信）"); continue
        if red and age > 2: why = f"draft {age:.1f}h（自{how}）且非 review-pr 红：{', '.join(red)}"
        elif cs and not red and not pend: why = f"除 review-pr 外全绿仍是 draft（draft {age:.1f}h）"
        if why:
            L.append(f"- 催 #{n}（QA: {', '.join('#'+str(r) for r in p['refs'])}）{why}")
            acts.append({"key": f"nudge:{d}:{n}:{'red' if red else 'green'}",
                         "type": "nudge", "to": "Praise PR", "priority": True, "channel": "待 Praise 定（见 handoff 渠道方案）",
                         "text": f"#{n} {why}", "dedupe": "同 key 当天只催一次；Grok 线 pm-daily/<日>.md 白天催办节已催过则跳过"})
        else:
            notyet.append(f"#{n}（draft {age:.1f}h，红 {len(red)}，跑中 {len(pend)}）")
    # (c) 停滞：所有 open PraiseZhu PR，>1h 无新 commit / check 状态变化 / 新评论
    try: pst = json.load(open(PROGRESS))
    except Exception: pst = {}
    stl, pst_new = stalls(prs, pst, now_cst())
    for p, h, since in sorted(stl, key=lambda x: x[0]["number"]):
        n = p["number"]; why = f"{h:.1f}h 无进展（无新 commit、CI 状态无变化、无新评论 / review）"
        L.append(f"- 催 #{n}（停滞）{why}")
        acts.append({"key": f"nudge:{n}:stall:{since}", "type": "nudge", "to": "Praise PR", "priority": True,
                     "channel": "待 Praise 定（见 handoff 渠道方案）", "text": f"#{n} {why}",
                     "dedupe": "同一段停滞（同 since）只催一次；有进展后重新计时"})
    if notyet: L.append("- 不催（未满 2h 或仍在跑）：" + "、".join(notyet))
    if ready: L.append("- 已 ready，归 Mac mini 盯梢，不催：" + " ".join(ready))
    if skip: L.append("- 范围外：" + "；".join(skip))
    if not acts: L.append("- 本轮无催办")
    txt = "\n".join(L) + "\n\n"
    chief = None
    if now_cst().hour == 20:
        pth = f"{BASE}/out/pm/{d}.md"; body = (open(pth).read() if os.path.exists(pth) else "") + txt
        rounds = re.findall(r"## 白天催办 (\d\d:\d\d)", body); n_n = len(re.findall(r"^- 催 #", body, re.M))
        prs_n = sorted(set(re.findall(r"^- 催 (#\d+)", body, re.M)))
        chief = (f"# 给 Chief（Jasper）的白天催办汇总 {d}\n\n今天 {len(rounds)} 轮（{' '.join(rounds)}），"
                 f"共催 {n_n} 次，涉及 {' '.join(prs_n) or '无'}。20:00 时 QA 修复 PR {len(qa)} 个，draft {sum(p['isDraft'] for p in qa)} 个，"
                 f"本轮需催 {len(acts)} 个。明细见 {pth}「白天催办」。\n")
        try:
            import chief_missed; chief += chief_missed.sections(d)
        except Exception as e: chief += f"\n## 漏项\n- 未核实：漏项汇总出错（{str(e)[:100]}）\n"
        acts.append({"key": f"pm-chief:{d}", "type": "SendToAgent", "to": "Jasper (Chief of Staff)", "priority": False,
                     "channel": "待 Praise 定", "text": chief})
    if dry:
        print(txt); print(chief or ""); print(json.dumps(acts, ensure_ascii=False, indent=1)); return
    pth = f"{BASE}/out/pm/{d}.md"; old = open(pth).read() if os.path.exists(pth) else ""
    write_atomic(pth, old + ("\n" if old and not old.endswith("\n\n") else "") + txt)
    if mode() == "pi":  # 切到 Pi 后与 Grok 线同一位置：fleet-standards/pm-daily/<当天>.md「白天催办」节
        canon = f"/workspace/fleet-standards/pm-daily/{d}.md"; oc = open(canon).read() if os.path.exists(canon) else ""
        write_atomic(canon, oc + ("\n" if oc and not oc.endswith("\n\n") else "") + txt)
    if chief: write_atomic(f"{BASE}/out/pm/{d}.chief.md", chief)
    write_atomic(PROGRESS, json.dumps(pst_new, ensure_ascii=False, indent=1))
    record_actions("pm", d + ".nudges", acts)
if __name__ == "__main__": main()
