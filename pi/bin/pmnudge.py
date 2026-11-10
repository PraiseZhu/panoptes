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
11-03 B（Mivo 测试项目经理.md「2026-11-02 Praise 新规」催办范围 1-5）：不再豁免 bot 自己的修复 PR；QA 修复 PR（draft 和 ready）另按
 DIRTY/BEHIND 超过 2h、ready 且 BLOCKED 超过 24h、head 上没有 main CI（workflow「CI」）的 check 超过 30 分钟催，每段只催一次
 （PI_STATE/out/pm/pmnudge-segments.json）；kiro（kirozeng）正文含「QA: #N」的 open PR 同样催。
 回滚：touch PI_STATE/pmnudge-b-off（每轮重读）-> 恢复 11-03 前的行为。
11-05（Jasper 审计：11-04 11:05–18:05 每轮都因 gh 限流失败）：gh pr list 失败后退避重试，最多 2 次（GH_BACKOFF_S；限流时等到
 lib 记录的重置时间，单次不超过 GH_WAIT_MAX_S）。仍失败：写告警状态 out/pm/pmnudge-gh-alert.json（watchdog 的 pmnudge:gh 项报给 Praise，
 成功一轮即清除），并在 gaps-open.json（pi 模式 = fleet-standards，否则 PI_STATE/out/pm；PI_GAPS_FILE 覆盖）按现有格式记一条，当天同 id 只更新。
用法：pmnudge.py [--dry-run]（--dry-run 只打印，不写任何文件）
"""
import sys
import jsonrec
from lib import *
from finding_policy import ruling_keys
from pm_extra import fix_refs
RULINGS = "/workspace/fleet-standards/p2-rulings.md"
RED = ("FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE")
PEND = ("", "PENDING", "QUEUED", "IN_PROGRESS", "EXPECTED", "WAITING", "REQUESTED")

def nofix_prs():
    """判「不修」/「结案…」的行 -> PR（没指纹的行）/ (PR, 指纹)（带指纹的行，只管那一条；finding_policy.ruling_keys）"""
    if not os.path.exists(RULINGS): return set()
    return ruling_keys(open(RULINGS).read(), lambda r: "不修" in r or r.startswith("结案"))

def only_nofix(refs, body, nof, p01):
    """修复 PR 只关联判「不修」的 P2：每个 QA 单号都不是 P0/P1，且要么都整 PR 判了不修，要么正文里的每个指纹都按指纹判了不修"""
    if any(r in p01 for r in refs): return False
    fps = set(re.findall(r"mq1-[0-9a-z]+", body or ""))
    return all(r in nof for r in refs) or bool(fps) and all(any((r, f) in nof for r in refs) for f in fps)

def p01_prs():
    s = set()
    for f in sorted(glob.glob(f"{QA}/ledger/daily/*.json"))[-1:]:
        for r in json.load(open(f))["rows"]:
            if r["level"] in ("P0", "P1"): s.add(r["pr"])
    return s

FP_MARK = re.compile(r"<!-- pi-line fixpr:(\d+):(mq1-[0-9a-f]{20})")   # prfix 写进正文的标记（prfix.PILINE_MARK）
FP_HEAD = re.compile(r"fixpr-(\d+)-(mq1-[0-9a-f]{20})")                   # qa-fix/fixpr-<单号>-<指纹>[-a2] 分支名

def fix_fps(p):
    """QA 单号 -> 这个 PR 修的问题指纹集合。只认 pi-line 标记和分支名（正文别处提到的 mq1- 不算）"""
    out = {}
    for q, fp in FP_MARK.findall(p.get("body") or "") + FP_HEAD.findall(p.get("headRefName") or ""): out.setdefault(int(q), set()).add(fp)
    return out

def body_qa_refs(p):
    """正文里的 QA 引用，正则与 pm_extra.fix_refs 相同；催办仍只看正文，不扫标题。"""
    return fix_refs({"body": p.get("body")})

def merged_refs():
    """(QA 单号, 指纹) -> 修掉它的已合并 PraiseZhu PR 号（最近 100 个）；没有指纹的已合并 PR 记 (单号, None)"""
    out = {}
    try:
        for p in self_pr_list(REPO, "merged", "number,body,headRefName", run=gh):
            fps = fix_fps(p)
            for r in body_qa_refs(p):
                for fp in sorted(fps.get(int(r)) or [None]): out.setdefault((int(r), fp), p["number"])
    except Exception: pass
    return out

def superseded(p, refs, fixed):
    """-> 取代它的已合并 PR 号列表（空 = 没被取代）。11-10：按问题指纹比，不按 QA 单号（#2056 mq1-d966f735 不被同单号 #2012 的
    #2063 mq1-3a05907e 取代）；open PR 某个单号没有指纹 -> 保守，不判取代（照催）"""
    fps = fix_fps(p)
    keys = [(r, fp) for r in refs for fp in sorted(fps.get(r, ()))]
    if not refs or any(r not in fps for r in refs) or not all(k in fixed and fixed[k] != p["number"] for k in keys): return []
    return sorted({fixed[k] for k in keys})

def draft_since(n, created):
    try:
        ev = gh(["api", f"repos/{REPO}/issues/{n}/timeline?per_page=100", "--paginate"])
        t = [e["created_at"] for e in ev if e.get("event") == "convert_to_draft"]
        if t: return parse_ts(max(t)), "转 draft"
    except Exception: pass
    return parse_ts(created), "创建"

STALL_H = 1.0
PROGRESS = f"{BASE}/out/pm/pmnudge-progress.json"
SEGMENTS = f"{BASE}/out/pm/pmnudge-segments.json"
KIRO = "kirozeng"
MAIN_CI = "CI"   # xindong/mivo-canvas-plugin .github/workflows/ci.yml 的 workflow 名
DIRTY_H, BLOCKED_H, NOCI_H = 2.0, 24.0, 0.5

def progress_sig(p):
    """进展指纹：head commit + 每个 check 的状态 + 评论数 + review 数（行内 review 评论也会产生一条 review）；任一变化即算有进展"""
    cs = sorted(f"{c.get('name') or c.get('context') or '?'}={(c.get('conclusion') or c.get('state') or c.get('status') or '').upper()}"
                for c in p.get("statusCheckRollup") or [])
    return json.dumps([p.get("headRefOid") or "", cs, len(p.get("comments") or []), len(p.get("reviews") or [])], ensure_ascii=False)

def last_activity(p, now):
    """11-04：新 PR / 有进展 -> 从 PR 自己的最后活动时间（updatedAt）起算，而不是脚本第一次看到它的时间（#1710/#1725 没被催）；没有 / 解析失败 / 在未来 -> now"""
    try:
        t = parse_ts(p.get("updatedAt"))   # "" / None -> None；无时区 -> 视为无效
        if t is not None and t.tzinfo is not None and t <= now: return t.isoformat()
    except Exception: pass
    return now.isoformat()

def stalls(prs, state, now):
    """返回 (本轮要催的停滞列表 [(pr, 小时数, since_iso)], 新 state)。新 PR / 指纹变了 -> 从 now 重新计时、清 nudged；
    同一段停滞已催过（nudged）-> 不再返回"""
    new, out = {}, []
    for p in prs:
        k, sig = str(p["number"]), progress_sig(p)
        old = state.get(k) or {}
        same = old.get("sig") == sig and old.get("since")
        since = old["since"] if same else last_activity(p, now)
        new[k] = {"sig": sig, "since": since, "nudged": bool(same and old.get("nudged"))}
        h = (now - parse_ts(since)).total_seconds() / 3600
        if h > STALL_H and not new[k]["nudged"]:
            out.append((p, h, since)); new[k]["nudged"] = True
    return out, new

def conditions(p):
    """这个 PR 现在满足的 B 类条件 -> {名字: (值, 阈值小时, 催办行标签, 说明)}；值变了就是新的一段"""
    ms, head = (p.get("mergeStateStatus") or "").upper(), p.get("headRefOid") or ""
    out = {}
    if ms in ("DIRTY", "BEHIND"): out["merge"] = (ms, DIRTY_H, ms.lower(), f"mergeStateStatus={ms}")
    if not p.get("isDraft") and ms == "BLOCKED": out["blocked"] = (ms, BLOCKED_H, "ready+blocked", "ready 但 BLOCKED")
    if not any(c.get("workflowName") == MAIN_CI for c in p.get("statusCheckRollup") or []):
        out["nomainci"] = (head, NOCI_H, "无 main CI", f"head {head[:9]} 上没有 main CI（workflow「{MAIN_CI}」）的 check")
    return out

def segment_alerts(prs, state, now):
    """-> (本轮要催的 [(pr, 条件, 标签, 说明, since)], 新 state)。state[pr][条件] = {val, since, nudged}：同一段只催一次，
    条件消失或值变了（换 head、DIRTY->BEHIND）重新计时"""
    out, new = [], {}
    for p in prs:
        k, old, seg = str(p["number"]), state.get(str(p["number"])) or {}, {}
        for c, (val, lim, tag, why) in conditions(p).items():
            o = old.get(c) or {}
            same = o.get("val") == val and o.get("since")
            seg[c] = {"val": val, "since": o["since"] if same else now.isoformat(), "nudged": bool(same and o.get("nudged"))}
            h = (now - parse_ts(seg[c]["since"])).total_seconds() / 3600
            if h > lim and not seg[c]["nudged"]:
                out.append((p, c, tag, f"{why} 已 {h:.1f}h（超过 {lim:g}h）", seg[c]["since"])); seg[c]["nudged"] = True
        if seg: new[k] = seg
    return out, new

FIELDS = "number,title,isDraft,createdAt,updatedAt,body,headRefName,headRefOid,comments,reviews,statusCheckRollup,mergeStateStatus"

def next_retry(now, e):
    """下一轮 cron（北京 09–22 点 :05）；限流时不早于 lib.gh 记录的重置时间（#198 的 gh-ratelimit.json，没有则只看 cron）"""
    def slot(x):   # 第一个不早于 x 的 :05
        s = x.replace(minute=5, second=0, microsecond=0)
        return s if s >= x else s + dt.timedelta(hours=1)
    t = slot(now + dt.timedelta(microseconds=1))
    try: u = gh_rl_until()
    except Exception: u = 0
    if u: t = max(t, slot(dt.datetime.fromtimestamp(u, CST)))
    while not 9 <= t.hour <= 22: t = (t + dt.timedelta(hours=1)).replace(minute=5)
    return t, u

def fail_section(d, hh, e, dry):
    msg = str(e); t, u = next_retry(now_cst(), e)
    rl = type(e).__name__ == "GhRateLimited" or bool(u) or bool(re.search(r"rate limit|HTTP 429|abuse", msg, re.I))
    why = ("GitHub 限流" + (f"（至 {dt.datetime.fromtimestamp(u, CST).strftime('%H:%M')} CST）" if u else "")) if rl else "gh 失败（非限流）"
    txt = (f"## 白天催办 {hh}（Pi 线；gh pr list 失败，本轮未催）\n- 原因：{why}：{msg[:200]}\n"
           f"- 下次重试：{t.strftime('%m-%d %H:%M')} CST\n\n")
    if dry: print(txt); return
    for pth in [f"{BASE}/out/pm/{d}.md"] + ([f"/workspace/fleet-standards/pm-daily/{d}.md"] if mode() == "pi" else []):
        old = open(pth).read() if os.path.exists(pth) else ""
        if re.search(rf"^## 白天催办 {hh[:2]}:\d\d（Pi 线；gh pr list 失败，本轮未催）", old, re.M): continue   # 同一小时只写一节
        write_atomic(pth, old + ("\n" if old and not old.endswith("\n\n") else "") + txt)

GH_RETRIES = 2                 # 11-05：首次 + 最多 2 次重试
GH_BACKOFF_S = (60, 180)       # 第 1、2 次重试前等多久（指数退避）
GH_WAIT_MAX_S = 900            # 限流时等到重置，但单次最多 15 分钟（两次合计 < 1 小时，不压下一轮 :05）
SLEEP = time.sleep             # tests stub this
ALERT = f"{BASE}/out/pm/pmnudge-gh-alert.json"
def gaps_file():
    return os.environ.get("PI_GAPS_FILE") or ("/workspace/fleet-standards/gaps-open.json" if mode() == "pi" else f"{BASE}/out/pm/gaps-open.json")

def with_retry(fn, what="gh pr list"):
    """fn() with up to GH_RETRIES retries (backoff GH_BACKOFF_S; a recorded rate-limit reset within GH_WAIT_MAX_S is waited out).
    Returns (result, errors before success); re-raises the last error after the retries, with .attempts = list of errors."""
    errs = []
    for i in range(GH_RETRIES + 1):
        try: return fn(), errs
        except RedLine: raise
        except Exception as e:
            errs.append(e)
            if i == GH_RETRIES: e.attempts = errs; raise
            wait = GH_BACKOFF_S[min(i, len(GH_BACKOFF_S) - 1)]
            try: u = gh_rl_until()
            except Exception: u = 0
            if u: wait = max(wait, min(int(u - time.time()) + 2, GH_WAIT_MAX_S))
            log(f"pmnudge: {what} 失败（第 {i + 1} 次），{wait}s 后重试：", str(e)[:200]); SLEEP(wait)

def record_gap(d, hh, errs, path=None):
    """gaps-open.json (fleet-standards/ORG.md 规则 5 的格式)：一天一条 id=pmnudge-gh-fail-<日>，同一天再失败只更新 item/evidence。"""
    path = path or gaps_file(); gid = f"pmnudge-gh-fail-{d}"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    def fn(gs):   # 11-05: jsonrec.update (same lock as stdcheck and every other gaps-open.json writer), was a private flock
        if not isinstance(gs, list): raise RuntimeError(f"{path} 不是列表，不写")
        g = next((x for x in gs if x.get("id") == gid), None)
        rounds = sorted(set((g or {}).get("failed_rounds", []) + [hh]))
        last = str(errs[-1])[:200] if errs else ""
        new = {"id": gid, "opened": (g or {}).get("opened") or now_cst().strftime("%Y-%m-%dT%H:%M+08:00"),
               "bot": "Mivo 测试项目经理", "owner": "Mivo QA 运维",
               "item": f"pmnudge 白天催办 {d} 共 {len(rounds)} 轮在 {GH_RETRIES} 次重试后仍因 gh 失败未催（{'、'.join(rounds)}）",
               "evidence_needed": f"gh 恢复后一轮催办成功（{BASE}/out/pm/{d}.md「白天催办」节为 gh 实查）；最近错误：{last}",
               "deadline": (g or {}).get("deadline") or (now_cst() + dt.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M+08:00"),
               "last_chased": (g or {}).get("last_chased"), "chase_count": (g or {}).get("chase_count", 0),
               "status": "open", "failed_rounds": rounds}
        if g: g.update(new)
        else: gs.append(new)
        return True
    jsonrec.update(path, fn, default=[])
    return gid

def close_gaps(d, hh, path=None):
    """11-05: gh is back -> close every open pmnudge-gh-fail-<day> entry (closed / closed_evidence, as the file's other entries).
    No file -> nothing (never creates one). Returns the closed ids."""
    path = path or gaps_file()
    if not os.path.exists(path): return []
    closed = []
    def fn(gs):
        if not isinstance(gs, list): return None
        for g in gs:
            if isinstance(g, dict) and str(g.get("id", "")).startswith("pmnudge-gh-fail-") and g.get("status") == "open":
                g.update(status="closed", closed=now_cst().strftime("%Y-%m-%dT%H:%M+08:00"),
                         closed_evidence=f"pmnudge {d} {hh}: gh pr list 成功，本轮催办为 gh 实查"); closed.append(g["id"])
        return True if closed else None
    jsonrec.update(path, fn)
    return closed

def raise_alert(d, hh, errs):
    """state file read by watchdog.pmnudge_gh_check -> one pmnudge:gh item (same open key while it lasts); success clears it."""
    try: a = json.load(open(ALERT))
    except Exception: a = {}
    a = {"since": a.get("since") or now_cst().isoformat(timespec="seconds"), "last": now_cst().isoformat(timespec="seconds"),
         "rounds": sorted(set(a.get("rounds", []) + [f"{d} {hh}"])), "attempts": len(errs), "error": str(errs[-1])[:300] if errs else ""}
    write_atomic(ALERT, json.dumps(a, ensure_ascii=False, indent=1))
    log(f"ALERT pmnudge: gh 在 {len(errs)} 次尝试后仍失败 -> {ALERT}（watchdog pmnudge:gh）")

def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    kind, payload = cli_preflight(argv, {"--dry-run"}, __doc__)
    if kind == "help":
        print(payload); return 0
    if kind == "error":
        print(payload, file=sys.stderr); return 2
    dry = "--dry-run" in payload[1]
    d = today(); hh = now_cst().strftime("%H:%M")
    try:
        prs, errs = with_retry(lambda: self_pr_list(REPO, "open", FIELDS, run=gh))   # task B: PraiseZhu + App PRs
        if errs: log(f"pmnudge: gh pr list 第 {len(errs) + 1} 次成功")
    except Exception as e:   # gh 挂了：不崩、不催、不动进展文件；11-04：仍写一节说明时间、原因、下次重试，每小时都有记录
        log("pmnudge: gh pr list 重试后仍失败，本轮跳过：", str(e)[:200])
        fail_section(d, hh, e, dry)
        if not dry:
            errs = getattr(e, "attempts", [e]); raise_alert(d, hh, errs)
            try: log("gaps:", record_gap(d, hh, errs))
            except Exception as ge: log("pmnudge: gaps-open.json 写入失败：", str(ge)[:200])
        return
    if not dry and os.path.exists(ALERT): os.remove(ALERT); log("pmnudge: gh 恢复，告警清除")
    if not dry:
        try:
            for gid in close_gaps(d, hh): log("pmnudge: gh 恢复，gap 关闭", gid)
        except Exception as ge: log("pmnudge: gaps-open.json 关闭失败：", str(ge)[:200])
    b = b_on()
    if b:
        try: prs += [p for p in gh(["pr", "list", "-R", REPO, "--author", KIRO, "--state", "open", "--limit", "100", "--json", FIELDS])
                     if body_qa_refs(p)]
        except Exception as e: log("pmnudge: kiro 的 PR 列表读取失败，本轮只看 PraiseZhu：", str(e)[:200])
    nof, p01 = nofix_prs(), p01_prs()
    fixed = merged_refs()   # 10-28：QA 单号已被合并的修复 PR 修掉（11-10 起按 (单号, 指纹) 比） -> 这个 draft 是重复 / 被取代的，不催（#1343 被 #1345 取代）
    qa, skip = [], []
    for p in prs:
        refs = body_qa_refs(p)
        if not refs: continue
        if re.search(r"keel|lifeline", (p.get("headRefName") or "") + " " + p["title"], re.I): skip.append(f"#{p['number']} keel/lifeline 叠加链"); continue
        if only_nofix(refs, p.get("body"), nof, p01): skip.append(f"#{p['number']} 只关联判「不修」的 P2（{refs}）"); continue
        sup = superseded(p, refs, fixed)
        if sup: skip.append(f"#{p['number']} 被已合并的 {'、'.join('#' + str(x) for x in sup)} 取代"); continue
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
        if not b and "<!-- pi-line fixpr:" in (p.get("body") or ""):   # 10-24: the bot's own fix PRs -- prwatch / prfix --revise move them, DM only if stuck
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
    try: seg_old = json.load(open(SEGMENTS))
    except Exception: seg_old = {}
    segs, seg_new = segment_alerts(qa, seg_old, now_cst()) if b else ([], seg_old)
    for p, c, tag, why, since in sorted(segs, key=lambda x: x[0]["number"]):
        n = p["number"]
        L.append(f"- 催 #{n} {tag}（QA: {', '.join('#'+str(r) for r in p['refs'])}）{why}")
        acts.append({"key": f"nudge:{n}:{c}:{since}", "type": "nudge", "to": "Praise PR", "priority": True,
                     "channel": "待 Praise 定（见 handoff 渠道方案）", "text": f"#{n} {why}",
                     "dedupe": "同一段（同条件、同 since）只催一次；条件变了重新计时"})
    if notyet: L.append("- 不催（未满 2h 或仍在跑）：" + "、".join(notyet))
    if ready: L.append(("- 已 ready，只按 dirty/behind、ready+blocked、无 main CI 催，其余归 Mac mini 盯梢：" if b else "- 已 ready，归 Mac mini 盯梢，不催：") + " ".join(ready))
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
    if b: write_atomic(SEGMENTS, json.dumps(seg_new, ensure_ascii=False, indent=1))
    record_actions("pm", d + ".nudges", acts)
if __name__ == "__main__": sys.exit(main() or 0)
