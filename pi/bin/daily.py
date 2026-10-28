#!/usr/bin/env python3
"""Mivo QA 日报 draft (REPORT-STYLE layout, no model). Files only; never sends.
usage: daily.py [数据日] [--render-only] [--diff-sent] [--resend]
  --resend      : 补发：日报第一行以「【补发】」开头；外发 key 不变（mivo-daily-report:<日>），已发过的照样不重发
  --render-only : no gh calls if out/daily/<day>.json has the PR list; writes nothing but <day>.render.md (+ .diff)
  --diff-sent   : diff the render against Grok's sent text (report/sent/<day>/group-content.txt)
Rules: data day = previous Beijing day by merged_at; missing/invalid/non-1.0 report = QA system fault; a valid report whose
system_health.status is not ok (degraded/failed; bootstrap self-repair alone does not count, REPORT-STYLE) = QA system
anomaly: the PR keeps its level, the opening sentence / 系统状态 say 有异常 and which PR + step, and Praise is told (relay);
Levels and who is told follow the unified severity (Praise 2026-10-23; finding_policy.py, new_notice=True): @author /
DM / handoff issue only for the author problems among prs[].findings (not bots); prs[].pending_verification and, in old
reports, coverage-only P2 / AI-test leads / flaky-unverified checks / PLAUSIBLE or unverified AI-only P1 / own-tests /
qa-error / unconfirmed failed handoff cases = 待核实（QA 自查）: listed in one line, no @, no DM, no issue.
DM = notify_via_feishu true, not PraiseZhu, not bots, key dm:<day>:<pr> once.
Reconciliation: when Grok already published the day (sent/<day>/group-content.txt), PRs absent from that text are
held (carry_over), never re-sent; calibrations in conf/daily-calibrations.json."""
import sys, json, re, difflib, subprocess
from lib import *
import praise_notice as PN
from finding_policy import author_findings, pending_items, effective_severity

OWNER = "PraiseZhu"; CAL = f"{PI_CODE}/conf/daily-calibrations.json"; SENT = f"{QA}/report/sent"
GROUP_OF = lambda day: f"{SENT}/{day}/group-content.txt"

def jl(p, d=None):
    try: return json.load(open(p))
    except Exception: return d

def authors():
    m = {}
    d = jl(f"{QA}/author-map.json", {})
    for r in d.get("roster", []) + d.get("mivo_authors", []):
        if r.get("github_login"): m.setdefault(r["github_login"], {}).update({k: v for k, v in r.items() if v is not None})
    return m

def is_bot(login, a): return login.endswith("[bot]") or a.get("account_type") == "Bot"

def short_title(t):
    t = re.sub(r"^\w+(\([^)]*\))?!?:\s*", "", t)
    t = re.sub(r"\s*[—-]*\s*[\(（]?QA #\d+[\)）]?\s*$", "", t)
    return t.strip()

def sent_prs(day):
    p = GROUP_OF(day)
    if not os.path.exists(p): return None
    return {int(x) for x in re.findall(r"^#(\d+) ", open(p).read(), re.M)}

PENDING_ZH = {"review-plausible": "审查怀疑", "review-unverified": "审查待核实", "review-incomplete": "审查证据不全", "review-unknown-level": "审查等级不明",
              "review-p1-unconfirmed": "复核没证实的 P1", "review-ruled": "合并前已裁定", "check-qa": "测试结果不稳 / 没核实", "ai-test": "AI 补测线索",
              "own-tests": "改过的测试没跑到", "qa-error": "测试系统出错"}
def pending_label(f):
    if f.get("pending_kind"): return PENDING_ZH.get(f["pending_kind"], "待核实")
    src = f.get("source") or "check"
    if src == "code-review": return "审查怀疑" if str(f.get("verdict") or "").upper() == "PLAUSIBLE" else "复核没证实的 P1"
    return {"ai-test": "AI 补测线索", "coverage-gate": None, "own-tests": "改过的测试没跑到", "qa-error": "测试系统出错"}.get(src, "测试结果不稳 / 没核实")

def classify(n, r, cal):
    d, pr = r["doc"], r["pr"]
    fs = pr.get("findings") or []
    # 统一分级：作者的问题 = finding_policy（new_notice）；其余 P0–P2 和 pending_verification 都是待核实，不 @、不私信、不开 issue
    real = author_findings(pr, new_notice=True)
    leads = [f for f in pending_items(pr, new_notice=True) if pending_label(f)]   # 只差覆盖率的旧 P2 不算待核实，只在「测试覆盖情况」当信息
    if str(n) in cal.get("lead_only", {}): leads, real = leads + real, []
    sev = real[0]["severity"] if real else ("untested" if pr.get("verdict") in ("qa-error", "not-tested") or pr.get("severity") == "untested" else "P3")
    lead_only = False
    cg = pr.get("coverage_gate") or {}
    nc = (cg.get("values") or {}).get("new_code") if (cg.get("results") or {}).get("new_code") == "fail" else None
    base = (cg.get("baseline") or {}).get("functions")
    cases = (pr.get("handoff") or {}).get("cases") or []; man = (pr.get("handoff") or {}).get("manual") or []
    hc = {s: sum(1 for c in cases if c.get("qa_status") == s) for s in ("verified", "failed", "capped", "not-run")}
    # 覆盖率没拿到（results/values 为空或 status=unavailable）= 「未测」：记部分核实，不当通过
    cov_untested = bool(pr.get("coverage_gate") is not None and (not cg.get("results") or cg.get("status") == "unavailable"))
    return dict(sev=sev, cov_untested=cov_untested, verify="partial" if cov_untested else "full", real=real, leads=leads, lead_only=lead_only, newcode=nc, base_fn=base, hc=hc, manual=len(man),
                stated=[f for f in real if f.get("source") == "stated-acceptance"],
                docs=pr.get("outcome") == "docs_only", failed_checks=pr.get("failed_checks") or [],
                code_review_p3=[f for f in fs if f.get("source") == "code-review" and f.get("severity") == "P3"])

def health_issue(doc):
    """system_health of a valid report -> None (ok) or a short 中文 description of what went wrong (REPORT-STYLE 系统状态).
    bootstrap.self_repaired alone is not an anomaly; status ok is never one."""
    sh = (doc or {}).get("system_health") or {}
    st = sh.get("status")
    if st in (None, "ok"): return None
    what = []
    stages = {x.get("stage") for x in sh.get("llm_errors") or []}
    if "code-review" in stages: what.append("代码审查调用失败")
    if stages & {"ai-tests", "ai-test"}: what.append("AI 补测报错")
    if stages - {"code-review", "ai-tests", "ai-test"}: what.append("模型调用报错（" + "、".join(sorted(str(x) for x in stages - {"code-review", "ai-tests", "ai-test"})) + "）")
    if (sh.get("quota") or {}).get("capped"): what.append("额度用尽未补测")
    if sh.get("qa_tool_failures"): what.append("测试工具故障（" + "、".join(sorted({str(x.get("stage") or "?") for x in sh["qa_tool_failures"]})) + "）")
    if sh.get("env_problems"): what.append("测试环境问题")
    if st == "failed": what.insert(0, "流水线失败")
    return "、".join(what) or f"system_health={st}"

def scen(hc, manual):
    parts = []
    if hc["verified"]: parts.append(f"验过 {hc['verified']} 条")
    if hc["failed"]: parts.append(f"{hc['failed']} 条自动测试没过、待确认")
    if hc["capped"]: parts.append(f"{hc['capped']} 条额度用尽没测")
    if hc["not-run"]: parts.append(f"没能自动验证 {hc['not-run']} 条")
    if manual: parts.append(f"要人工测 {manual} 条")
    tot = sum(hc.values())
    return (tot, "，".join(parts)) if tot else (0, "")

def build(day, prs, flags):
    cal = (jl(CAL, {}) or {}).get("by_day", {}).get(day, {})
    am = authors(); sp = sent_prs(day)
    hold = {int(k): v for k, v in cal.get("hold", {}).items()}
    if sp is not None:   # Grok already published this day: its text is what the group saw
        for p in prs:
            if p["number"] not in sp: hold.setdefault(p["number"], "not in Grok's sent group-content.txt")
    items, fault, carry = [], [], []
    for p in prs:
        n = p["number"]; login = p["author"]["login"]; a = am.get(login, {})
        if n in hold: carry.append({"pr": n, "why": hold[n]}); continue
        f, r = load_report(n)
        it = {"n": n, "title": short_title(p["title"]), "login": login, "who": a.get("name") or login, "a": a,
              "merged": p["mergedAt"]}
        if not r or "_error" in r or not r.get("pr") or r["doc"].get("schema_version") != "1.0":
            fault.append(n); it["sev"] = "fault"; items.append(it); continue
        try:   # 一个 PR 的报告坏了不拖垮整份日报：这个 PR 记成故障，其余照出
            it.update(classify(n, r, cal)); it["health"] = health_issue(r["doc"])
        except Exception as e:
            log(f"classify #{n} failed: {type(e).__name__}: {e}")
            fault.append(n); it["sev"] = "fault"; it["error"] = f"{type(e).__name__}"
        items.append(it)
    return items, fault, carry, cal

def render(day, items, fault, carry, cal, resend=False):
    by = lambda s: [i for i in items if i.get("sev") == s]
    P0, P1, P2, P3 = by("P0"), by("P1"), by("P2"), by("P3")
    owner_last = lambda xs: sorted([i for i in xs if i["login"] != OWNER], key=lambda i: i["merged"]) + \
                            sorted([i for i in xs if i["login"] == OWNER], key=lambda i: -i["n"])
    P0, P1 = owner_last(P0), owner_last(P1)
    P2 = owner_last(P2)
    pend = [(i, f) for i in items for f in i.get("leads") or []]
    at = lambda i, k: f"@{i['who']}" if k in ("P0", "P1", "P2") and not is_bot(i["login"], i["a"]) else i["who"]
    mo, dd = int(day[5:7]), int(day[8:10]); L = []
    p2s = f"P2 {len(P2)} 个需要修复"
    sick = [i for i in items if i.get("health")]
    sick_s = "、".join(f"#{i['n']} {i['health']}" for i in sick)
    health = "测试系统运行正常。" if not fault and not sick else \
        (f"测试系统有故障：{len(fault)} 个 PR 没有合格的 QA 报告（{' '.join('#%d' % n for n in fault)}）" if fault else "测试系统有异常") + \
        (f"{'；另外' if fault else '：'}{len(sick)} 个 PR 测试过程有异常（{sick_s}），这些 PR 的级别只反映跑通的检查。" if sick else "。")
    L.append(("【补发】" if resend else "") + f"Mivo 播报 {mo} 月 {dd} 日（北京时间）：合了 {len(items)} 个 PR，" + ("没有 P0" if not P0 else f"P0 紧急 {len(P0)} 个要先修") +
             (f"，P1 {len(P1)} 个请作者修复" if P1 else "") + (f"，{p2s}" if P2 else "") + (f"，P3 {len(P3)} 个无需处理" if P3 else "") +
             (f"，待核实 {len(pend)} 条 QA 自查中" if pend else "") + f"。{health}\n")
    def entry(i, k):
        L.append(f"#{i['n']} {i['title']} — {at(i, k)}")
        main = [f for f in i["real"] if f.get("severity") == k]
        for f in main[:2]:
            ev = f.get("evidence") or {}
            L.append(f"· 问题：{f.get('title') or f.get('test') or f.get('check_id')}")
            tb = "测试复现" if f.get("test_backed") else ("审查结论，测试系统已复核触发路径可达（这是看代码发现的）" if (f.get("verification") or {}).get("status") == "confirmed"
                  else "审查结论，测试未复现（自动测试全部通过，这是看代码发现的）" if not i["failed_checks"] else "审查结论，测试未复现")
            L.append(f"· {tb}。")
            if isinstance(ev, dict) and ev.get("recommendation"):
                L.append(f"· 下一步：{ev['recommendation'].rstrip('。；;，, ')}，然后提修复 PR（标题或描述写「QA #{i['n']}」）；如果觉得不用改，在 PR 下留言，第一行写「结论：不修，原因…」。")
        for f in [f for f in i["real"] if f.get("severity") != k and f.get("source") == "code-review"][:1]:
            L.append(f"· 另有 1 条 {f['severity']} 审查意见：{f.get('title')}。需要修，可以排在 P1 之后。")
        if i["leads"]: L.append(f"· 另有 {len(i['leads'])} 条待核实，QA 先自己查清，不用处理。")
        t, s = scen(i["hc"], i["manual"])
        if t: L.append(f"· 作者列了 {t} 条要验的场景：{s}。")
    for k, xs, h in (("P0", P0, "P0 紧急｜要先修"), ("P1", P1, "P1 严重｜请作者修复"), ("P2", P2, "P2 一般｜需要修复")):
        if not xs: continue
        L.append(f"{h}（{len(xs)} 个）")
        for i in xs: entry(i, k)
        L.append("")
    if pend:   # 待核实（QA 自查）：一行带过，不 @、不写作者名、不写问题内容
        by = {}
        for i, f in pend: by.setdefault(i["n"], []).append(pending_label(f))
        L.append("待核实（QA 自查）")
        L.append(f"· 另有 {len(pend)} 条待核实，QA 先自己查清，不用作者处理：" + "、".join(
            f"#{n} " + "、".join(f"{lab} {cnt} 条" for lab, cnt in sorted({x: ls.count(x) for x in ls}.items())) for n, ls in sorted(by.items(), reverse=True)) + "。")
        L.append("")
    # 私信 section (Grok: 已私信通知). Actual sends from Grok sent dir; otherwise planned (after report)
    sd = f"{SENT}/{day}"; dms = []
    sent_dm = {int(m.group(1)) for f in (os.listdir(sd) if os.path.isdir(sd) else []) for m in [re.match(r"dm-(\d+)\.txt$", f)] if m}
    for i in P0 + P1 + P2:
        if not i.get("real"): continue
        if i["n"] in sent_dm: dms.append((0 if i["login"] == OWNER else 1, i, "已飞书私信" if i["login"] == OWNER else "日报发出后飞书私信"))
        elif i["a"].get("notify_via_feishu") and i["login"] != OWNER and not is_bot(i["login"], i["a"]):
            dms.append((1, i, "日报发出后飞书私信"))
    if dms:
        L.append("已私信通知")
        for _, i, s in sorted(dms, key=lambda x: x[0]): L.append(f"#{i['n']} {i['title']} → {i['who']}，{s}")
        L.append("")
    if P3:
        L.append(f"P3 提示｜无需处理（{len(P3)} 个）")
        g4 = [i for i in P3 if i.get("leads")]   # 只有待核实条目：不能写成「审查无问题」
        g1 = [i for i in P3 if i["code_review_p3"] and not i["docs"] and i not in g4]; g3 = [i for i in P3 if i["docs"] and i not in g4]
        g2 = [i for i in P3 if i not in g1 and i not in g3 and i not in g4]
        for g, note in ((g1, "测试通过，审查各有 1 条小提醒，无需处理。"), (g2, "测试通过，审查无问题，无需处理。"), (g3, "纯文档，按规则不跑测试，审查无问题，无需处理。"),
                        (g4, "有待核实的条目（见上），QA 先自己查清，作者不用处理。")):
            if not g: continue
            g.sort(key=lambda i: -i["n"]) if g is not g2 else None   # noqa
            for i in g: L.append(f"#{i['n']} {i['title']} — {i['who']}")
            L.append(f"· P3 以上 {len(g)} 个{note}" if len(g) > 1 else f"· P3 {note}")
        L.append("")
    if cal.get("corrections"):
        L.append("补测结果（之前合并的 PR）"); L += [f"· {c}" for c in cal["corrections"]]; L.append("")
    L.append("测试覆盖情况")
    bf = [i["base_fn"] for i in items if i.get("base_fn")]
    if bf: L.append(f"· 整个仓库大约 {round(max(bf))}% 的函数有单元测试覆盖，" + ("过了 80% 的门槛，不用处理。" if max(bf) >= 80 else "低于 80% 的门槛。"))
    low = [i for i in items if i.get("newcode") is not None]
    L.append("· 新写的代码有多少被测试跑到（参考线 90%，只是信息，不算问题）：" + ("；".join(f"#{i['n']} {round(i['newcode'])}%" for i in low) + "，不用处理。其余 PR 要么在 90% 以上，要么这次没改到要算覆盖率的代码。" if low else "这次都在 90% 以上或没改到要算覆盖率的代码。"))
    unt = [i for i in items if i.get("cov_untested")]
    if unt: L.append("· 覆盖率未测（没拿到覆盖率数据，算部分核实，不算通过）：" + "、".join(f"#{i['n']}" for i in unt) + "。")
    L.append("")
    docs = [i for i in items if i.get("docs")]
    L.append("系统状态")
    L.append(f"{len(items)} 个都测完了" + (f"（{'、'.join('#%d' % i['n'] for i in docs)} 纯文档按规则不跑测试）" if docs else "") +
             ("，运行正常。" if not fault and not sick else (f"；{len(fault)} 个缺合格报告，算测试系统故障" if fault else "") +
              (f"；{sick_s}，算测试系统异常，已转运维" if sick else "") + "。"))
    L.append("")
    L.append("分级说明（以审查机统一分级为准）：P0 正在发生的大范围事故；P1 受支持的使用场景里确实能碰到、会让核心功能失效或出严重数据 / 安全问题；P2 本 PR 改动里已证实的一般问题，也需要修，可排在 P1 之后；P3 不报。待核实的先由 QA 自己查清，不找作者。")
    return "\n".join(L) + "\n", P0, P1, P2, P3, dms

def send_own(day, keys, run=subprocess.run):
    """10-28: send daily's own keys right after recording them, through the normal outbound entry point (same gates: fleet-mode +
    outbound-enabled, memory floor, done-ledger, praise-dm-mute, exec lock). --job daily + one --key per own key: nothing else goes
    out. A failure never fails daily; the */10 outbound run picks up whatever is still pending."""
    if not keys: return None
    cmd = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "outbound.py"), "--job", "daily", "--days", "2"]
    for k in keys: cmd += ["--key", k]
    try:
        r = run(cmd, timeout=900); log(f"outbound (own keys {len(keys)}) rc={r.returncode}"); return r.returncode
    except Exception as e: log(f"outbound (own keys) failed: {type(e).__name__} -> left to the */10 run"); return None

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]; fl = {a for a in sys.argv[1:] if a.startswith("--")}
    day = args[0] if args else data_day([0]); ro = "--render-only" in fl
    meta = jl(f"{BASE}/out/daily/{day}.json")
    if ro and meta and meta.get("merged_prs"): prs = meta["merged_prs"]
    else: prs = merged_prs(day)
    log(f"data day {day}: {len(prs)} merged PRs in window {utc_window(day)}")
    items, fault, carry, cal = build(day, prs, fl)
    txt, P0, P1, P2, P3, dms = render(day, items, fault, carry, cal, resend="--resend" in fl)
    out = f"{BASE}/out/daily/{day}.render.md" if ro else f"{BASE}/out/daily/{day}.md"
    write_atomic(out, txt)
    if "--diff-sent" in fl and os.path.exists(GROUP_OF(day)):
        d = "".join(difflib.unified_diff(open(GROUP_OF(day)).read().splitlines(True), txt.splitlines(True), "grok-sent", "pi-render", n=0))
        write_atomic(f"{BASE}/out/daily/{day}.diff", d); log(f"diff lines: {len(d.splitlines())}")
    log(f"counts P0={len(P0)} P1={len(P1)} P2={len(P2)} P3={len(P3)} fault={len(fault)} held={[c['pr'] for c in carry]}")
    if ro: return
    acts = [{"key": f"mivo-daily-report:{day}", "type": "slack.post", "channel": "C0CAH3W61N2", "body_file": out,
             "dedupe_against": [f"{QA}/report/slack/sent.json", f"{SENT}/{day}/slack-sent.json"]}]
    for _, i, s in dms:
        if i["login"] == OWNER: continue   # owner never DM'd by Pi (Grok used --allow-owner on 10-22; not a rule)
        acts.append({"key": f"dm:{day}:{i['n']}", "type": "feishu.dm", "to": i["who"], "pr": i["n"], "level": i["sev"],
                     "dedupe_against": [f"{QA}/feishu-sent.json (key dm:{day}:{i['n']} or {REPO}#{i['n']}:*)", f"{SENT}/{day}/dm-{i['n']}.txt"]})
    routes = jl("/workspace/panoptes/report/handoff-routes.json", {}); rjs = json.dumps(routes)
    for i in P0 + P1 + P2:
        if not i.get("real") or f'"{i["login"]}"' not in rjs: continue
        for f in i["real"]:
            acts.append({"key": f"handoff:{i['n']}:{f.get('pr_fingerprint') or f.get('fingerprint')}", "type": "gh.issue.create",
                         "route_author": i["login"], "pr": i["n"], "title": f.get("title") or f.get("test"),
                         "dedupe_against": [f"{QA}/handoff/<owner>/issues.json", f"{SENT}/{day}/created.tsv"]})
    sick = [{"pr": i["n"], "what": i["health"]} for i in items if i.get("health")]
    if fault or sick:   # 日报标准 §4: 报告缺失/无效/非 1.0 或 system_health 异常 = QA 系统故障，告诉 Praise 并转运维
        acts.append({"key": f"daily-health:{day}", "type": "grokbot.chat-to-Praise", "to": "Praise", "at": now_cst().isoformat(timespec="seconds"),
                     "text": PN.text(PN.item("action", f"{day[5:].replace('-', '月')}日的 QA 日报有 PR 没测好",
                         "出日报时发现测试系统有问题：" + "；".join(
                         ([f"{len(fault)} 个 PR 没有合格的测试报告（{' '.join('#%d' % n for n in fault)}）"] if fault else []) +
                         [f"#{x['pr']} {x['what']}" for x in sick]) + "。",
                         "这些 PR 在日报里是「没测 / 测得不完整」，作者收不到它们的问题。",
                         todo=PN.ask_session(f"Mivo QA 日报（数据日 {day}）测试系统有问题：{'、'.join('#%d' % n for n in fault) or '见 out/daily/' + day + '.json 的 health'}，查原因，能补测就补测")))})
    record_actions("daily", day, acts)
    if "--no-send" not in fl: send_own(day, [a["key"] for a in acts])
    meta = {"day": day, "window_utc": utc_window(day), "merged": [p["number"] for p in prs], "merged_prs": prs,
            "reported": [i["n"] for i in items], "carry_over": carry, "fault": fault, "health": sick,
            "partially_verified": [{"pr": i["n"], "why": "coverage 未测"} for i in items if i.get("verify") == "partial"],
            "counts": {"P0": len(P0), "P1": len(P1), "P2": len(P2), "P3": len(P3)}}
    write_atomic(f"{BASE}/out/daily/{day}.json", json.dumps(meta, ensure_ascii=False, indent=1))

if __name__ == "__main__": main()
