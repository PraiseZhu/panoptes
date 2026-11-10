#!/usr/bin/env python3
"""Mivo QA 日报 handoff duty (Grok line: handoff-routes.json, aj0928-handoff + handover-mivo). Files only; gh read-only.
usage: handoff.py [scan]                  receipts scan (00:05/06:20/15:20/18:35); 15:20 adds overdue actions
       handoff.py plan <数据日> [--render-only] [--diff-sent]   issues the daily report WOULD open for that day
       handoff.py backfill [--since <数据日>] [--pr N ...]   review list of handoff issues missed because the report came
                                 after that day's daily run (writes out/handoff/backfill-<今天>.{md,json} only; never records
                                 actions, never sends; off while $PI_STATE/handoff-backfill-off exists)
Rules ported: route by author via handoff-routes.json (cc for lubinyu-lab -> aj0928); open one issue per finding
(key = pr_fingerprint "<fp>-pr<N>") for the author problems of the unified severity (finding_policy.notifiable,
new_notice=True: never 待核实 items, coverage-only, AI-test leads, P3, pre_existing, flaky/unverified, qa-error, PLAUSIBLE or
unverified AI-only P1); author handoff cases dedupe by PR+case id ("handoff:<PR>:<case>");
a failed author case with confirmed!=true is 待确认: listed, NO issue. Dedupe sources: handoff/<owner>/issues.json ∪
report/sent/*/created.tsv ∪ open/closed issues' "mivo-qa-handoff: key=" marker (gh read).
Receipts (one format since 2026-10-23, same as panoptes report/handoff-receipts.mjs): first meaningful line
结论：已修 #N / 结论：不修，原因… / 结论：误报，原因…  (「结论」may be bold, : or ：; synonyms such as 不用改 / 不成立 fold in);
QA's own comments (PraiseZhu) never count. Answered too: a merged fix PR whose title/body says "QA #<原PR号>"
(已修), and from 2026-10-24 a receipt on the original PR by its author or the route owner / cc. Overdue (15:20, 3 days
without an answer) only for QA handoff issues: the mivo-qa-handoff marker and the handoff label."""
import sys, glob
from lib import *
from finding_policy import notifiable, no_fix_findings
from handoff_action import handoff_issue_action
ROUTES = "/workspace/panoptes/report/handoff-routes.json"
# Author receipt -- one format since 2026-10-23, same rule as panoptes report/handoff-receipts.mjs parseReceiptDetail
# (shared cases: test/receipt-cases.json = panoptes argus/tests/fixtures/receipt-cases.json). The receipt is the first
# meaningful line: HTML comments and a BOM are dropped; blank lines, code blocks, quote lines (>) and @mention-only lines are
# skipped. 「结论」 may be bold, : or ：; the conclusion word (synonyms folded) must end at a line end, a space, *, # or
# punctuation, so 「结论：不修改…」 / 「结论：误报率…」 are not receipts. Without a 「结论：」 line, a watcher-bot post (mivo-watcher-receipt
# marker) counts by its fixed wording; bot status posts (task=blocked-notice / auto-close, vigil-handoff) never count.
RECEIPT_KINDS = {"已修复": "已修", "已修": "已修", "已改": "已修", "误报": "误报", "不成立": "误报",
                 "不需要改": "不修", "不用改": "不修", "无需修改": "不修", "不修": "不修", "不改": "不修"}
RECEIPT_LINE = re.compile(r"^(?:\*\*)?结论(?:\*\*)?[ \t]*[:：][ \t]*(?:\*\*)?[ \t]*("
                          + "|".join(sorted(RECEIPT_KINDS, key=len, reverse=True)) + r")(?=$|[\s*#，,、:：;；。.!！(（])(.*)$")
FIX_REF = re.compile(r"^[\s*（(，,、:：]*(?:(?:见|PR)[\s*（(，,、:：]*)*(?:[\w.-]+/[\w.-]+)?#([0-9]+)[）)]?", re.I)
REASON_LEAD = re.compile(r"^[\s*，,、:：;；。.!！（()）]*(?:原因[ \t]*[:：])?[\s*]*")
BOT_STATUS = re.compile(r"<!--\s*(?:vigil-handoff\b|mivo-watcher-receipt\b[^>]*\btask=(?:blocked-notice|auto-close)\b)")
WATCHER_RECEIPT = re.compile(r"<!--\s*mivo-watcher-receipt\b")
LINE_SPLIT = re.compile(r"\r\n|\r|\n|\u2028|\u2029")

def receipt_line(body):
    """The first meaningful line of a comment (see above), or None."""
    fence = False
    for line in LINE_SPLIT.split(re.sub(r"<!--.*?-->", "", str(body or ""), flags=re.S).replace("\ufeff", "")):
        t = line.strip()
        if t.startswith(("```", "~~~")): fence = not fence; continue
        if fence or not t or t.startswith(">") or re.fullmatch(r"(?:@[\w.-]+[\s,，、]*)+", t): continue
        return t
    return None

def watcher_receipt(body):
    """The watcher bot's fixed wording (only in a post carrying the mivo-watcher-receipt marker and no 「结论：」 line)."""
    b = str(body or "")
    if not WATCHER_RECEIPT.search(b): return None
    if "确认此意见成立并已修复" in b or re.search(r"要不要改代码(?:\*\*)?\s*[:：]\s*已改", b): return "已修"
    if re.search(r"在当前\s*head\s*上不成立", b, re.I): return "误报"
    if re.search(r"要不要改代码(?:\*\*)?\s*[:：]\s*(?:本轮)?(?:不改|不用|不需要)", b): return "不修"
    return None

def parse_receipt(body, pr=None):
    """-> (kind, fix_pr, reason) or None. kind ∈ 已修 / 不修 / 误报; fix_pr only for 已修, never the original PR `pr`.
    Bot status posts (task=blocked-notice / auto-close, vigil-handoff) are never receipts."""
    if BOT_STATUS.search(str(body or "")): return None
    m = RECEIPT_LINE.match(receipt_line(body) or "")
    if not m:
        w = watcher_receipt(body)
        return (w, None, None) if w else None
    kind, rest, fix = RECEIPT_KINDS[m.group(1)], m.group(2), None
    if kind == "已修":
        f = FIX_REF.match(rest)
        if f:
            rest = rest[f.end():]
            if pr is None or int(f.group(1)) != int(pr): fix = int(f.group(1))
    reason = REASON_LEAD.sub("", rest).replace("**", "").strip()
    return kind, fix, reason[:300] or None

class _ReceiptText:
    def __init__(self, text): self.text = text
    def group(self, i=0): return self.text
    def __getitem__(self, i): return self.text

class _Receipt:
    """Compat for prfix.parse_receipts and older callers: RECEIPT.match(body) -> an object whose .group(1) is
    "已修 #N" | "已修" | "不修" | "误报" (parse_receipt), or None."""
    def match(self, body):
        r = parse_receipt(body)
        return _ReceiptText(f"{r[0]} #{r[1]}" if r[0] == "已修" and r[1] else r[0]) if r else None
RECEIPT = _Receipt()
MARK = re.compile(r"mivo-qa-handoff:\s*key=(\S+)\s+pr=(\d+)")
QA_LOGINS = {x.strip() for x in os.environ.get("MIVO_QA_HANDOFF_QA_LOGINS", "PraiseZhu").split(",") if x.strip()}
# 「QA」后面可以夹最多 8 个非 # 的字，然后是一串原 PR 号（/ , ， 、 & 和 隔开）：一个修复 PR 对应多份 QA 报告时写成
# 「QA 回访 #904/#910/#898/#884」（2026-10-24 #918）。「QA」前不能是字母。同 panoptes report/handoff-receipts.mjs qaRefRe。
# 每个号只认裸 #N 或 xindong/mivo-canvas-plugin#N，别的仓的 owner/repo#N 不算。
_QA_ONE = r"(?:xindong/mivo-canvas-plugin)?#"
def qa_ref(n): return re.compile(rf"(?<![A-Za-z])QA[^#\n]{{0,8}}?(?:{_QA_ONE}[0-9]+(?![0-9])\s*[/,，、&和]\s*)*{_QA_ONE}{int(n)}(?![0-9])", re.I)

def author_receipts(issue):
    """Receipt texts (RECEIPT group 1) of an issue's comments, oldest first; QA's own comments never count."""
    out = []
    for c in issue.get("comments") or []:
        if ((c.get("author") or {}).get("login") or (c.get("user") or {}).get("login")) in QA_LOGINS: continue
        m = RECEIPT.match(c.get("body") or "")
        if m: out.append(m.group(1))
    return out

def merged_fix_prs(n, files=(), lister=None):
    """Merged PRs of the target repo whose title/body says "QA #n" (and, when the finding has files, touch one of them)."""
    try: rows = (lister or (lambda q: gh(["pr", "list", "-R", REPO, "--state", "merged", "--limit", "50", "--search", q, "--json", "number,title,body,mergedAt,files"])))(f"{int(n)} in:title,body")
    except Exception as e: log("gh fix-pr search fail", n, e); return None
    re_ = qa_ref(n)
    return [p for p in rows if p.get("mergedAt") and p.get("number") != int(n) and re_.search(f"{p.get('title') or ''}\n{p.get('body') or ''}")
            and (not files or any((x.get("path") if isinstance(x, dict) else x) in files for x in p.get("files") or []))]

def routes(): return json.load(open(ROUTES))["routes"]
def route_for(login): return next((r for r in routes() if login in r.get("authors", [])), None)

def known_keys(gh_ok=True):
    keys, src = {}, {}
    for f in glob.glob(f"{QA}/handoff/*/issues.json"):
        for i in json.load(open(f)).get("issues", []): keys[i["key"]] = i.get("url"); src[i["key"]] = os.path.relpath(f, QA)
    for f in glob.glob(f"{QA}/report/sent/*/created.tsv"):
        for ln in open(f):
            c = ln.rstrip("\n").split("\t")
            if len(c) >= 5: keys.setdefault(c[2], c[4]); src.setdefault(c[2], os.path.relpath(f, QA))
    gh_issues = {}
    if gh_ok:
        for rt in routes():
            try: iss = gh(["issue", "list", "-R", rt["repo"], "--state", "all", "--limit", "300", "--json", "number,state,body,url,title,createdAt,comments,labels"])
            except Exception as e: log("gh read fail", rt["repo"], e); continue
            gh_issues[rt["repo"]] = iss
            for i in iss:
                m = MARK.search(i.get("body") or "")
                if m: keys.setdefault(m.group(1), i["url"]); src.setdefault(m.group(1), f"gh:{rt['repo']}#{i['number']}")
    return keys, src, gh_issues

def _plan_one(n, r, rt, keys, src, rows, pend, acts, day=None, login=None):
    pr = r["pr"]
    for x in pr.get("pending_verification") or []:
        pend.append((n, x.get("check_id") or x.get("fingerprint"), f"待核实（{x.get('kind_zh') or x.get('pending_kind')}），不开 issue"))
    for x in pr.get("findings") or []:
        if x.get("severity") not in ("P0", "P1", "P2"): continue
        if not notifiable(x, new_notice=True): pend.append((n, x.get("check_id") or x.get("fingerprint"), "统一分级下不算作者的问题（待核实 / 线索 / 只差覆盖率等），不开 issue")); continue
        k = x.get("pr_fingerprint") or f"{x.get('fingerprint')}-pr{n}"
        st = f"已开 {keys[k]}（{src[k]}）" if k in keys else "would-open"
        rows.append((n, x["severity"], k, rt["repo"], st, x.get("title")))
        if k not in keys:   # same action (key handoff:<PR>:<pr_fingerprint>, body, marker) as daily.py; before 11-03 the key was the bare fingerprint
            acts.append({**handoff_issue_action(day, {"n": n, "tested_sha": pr.get("tested_sha"), "merge_sha": pr.get("merge_sha")}, x),
                         "repo": rt["repo"], "route_author": login, "level": x["severity"], "dedupe_against": "issues.json ∪ sent/*/created.tsv ∪ gh marker"})
    for c in (pr.get("handoff") or {}).get("cases") or []:
        if c.get("qa_status") == "failed" and c.get("confirmed") is not True:
            pend.append((n, c.get("key") or f"handoff:{n}:{c.get('id')}", "作者用例自动测试没过、未确认 -> 待确认，不开 issue"))

def plan(day, ro, diff):
    meta = json.load(open(f"{BASE}/out/daily/{day}.json")) if os.path.exists(f"{BASE}/out/daily/{day}.json") else {}
    prs = meta.get("merged_prs") or merged_prs(day)
    held = {c["pr"] for c in meta.get("carry_over", [])}
    held |= {int(k) for k in (json.load(open(f"{PI_CODE}/conf/daily-calibrations.json")).get("by_day", {}).get(day, {}).get("hold", {}))}
    g = f"{QA}/report/sent/{day}/group-content.txt"
    if os.path.exists(g):   # Grok published this day: PRs not in its text are carry_over, never opened retroactively
        sp = {int(x) for x in re.findall(r"^#(\d+) ", open(g).read(), re.M)}; held |= {p["number"] for p in prs if p["number"] not in sp}
    keys, src, _ = known_keys()
    rows, pend, acts = [], [], []
    for p in prs:
        n, login = p["number"], p["author"]["login"]; rt = route_for(login)
        if not rt or n in held: continue
        f, r = load_report(n)
        if not r or not r.get("pr"): continue
        try: _plan_one(n, r, rt, keys, src, rows, pend, acts, day, login)
        except Exception as e:   # 一个 PR 的报告坏了只跳过它，记成待核实
            log(f"plan #{n} failed: {type(e).__name__}: {e}"); pend.append((n, "report-error", f"报告解析出错（{type(e).__name__}），不开 issue"))
    L = [f"# 交接 issue 计划 数据日 {day}（Pi line，只写文件）", "", "| PR | 级别 | key | 仓 | 状态 | 标题 |", "|---|---|---|---|---|---|"]
    L += [f"| #{a} | {b} | {c} | {d} | {e} | {f} |" for a, b, c, d, e, f in rows]
    L += ["", "待确认（不开 issue）："] + [f"- #{a} {b}：{c}" for a, b, c in pend] + (["- 无"] if not pend else [])
    if held: L += ["", f"carry_over（日报未发，不开）：{sorted(held)}"]
    if diff:
        g = f"{QA}/report/sent/{day}/created.tsv"; gk = {ln.split("\t")[2] for ln in open(g)} if os.path.exists(g) else set()
        pk = {r[2] for r in rows}
        L += ["", "## 对比 Grok sent/created.tsv", f"- Grok 开了 {len(gk)}，Pi 计划 {len(pk)}；只在 Grok：{sorted(gk-pk) or '无'}；只在 Pi：{sorted(pk-gk) or '无'}"]
    out = f"{BASE}/out/handoff/plan-{day}{'.render' if ro else ''}.md"; write_atomic(out, "\n".join(L) + "\n")
    if not ro: record_actions("handoff", day, acts)

# ---- backfill (2026-11-03, /workspace/pi-work/handoff-backfill/DESIGN.md): a report written after its data day's daily run
# never got its handoff issues (daily.py only looks at one day once; #1340: report 10-29 13:54, daily 10-29 07:35).
BACKFILL_OFF = lambda: f"{BASE}/handoff-backfill-off"
P2_STATE = lambda: f"{BASE}/out/p2/state.json"
RULINGS = "/workspace/fleet-standards/p2-rulings.md"

def jload(p, d):
    try: return json.load(open(p))
    except (OSError, ValueError): return d

def daily_run_time(day):
    """When the daily report for data day `day` last ran -> (aware datetime, source) or (None, None).
    out/daily/<day>.json mtime (kept from 10-29), else the outbound ledger time of mivo-daily-report:<day>."""
    p = f"{BASE}/out/daily/{day}.json"
    if os.path.exists(p): return dt.datetime.fromtimestamp(os.path.getmtime(p), CST), "out/daily mtime"
    at = (jload(f"{BASE}/out/outbound/ledger.json", {}).get(f"mivo-daily-report:{day}") or {}).get("at")
    return (parse_ts(at), "outbound ledger") if at else (None, None)

def report_time(f, doc):
    t = parse_ts(doc.get("generated_at")) if doc.get("generated_at") else None
    return (t, "generated_at") if t else (dt.datetime.fromtimestamp(os.path.getmtime(f), CST), "文件 mtime（报告没有 generated_at）")

def shadow_ruling(st, n, x):
    """Pi's model proposal in out/p2/state.json (key = pr_fingerprint, else "<PR>:<title>", as p2.py writes it), or None."""
    return st.get(x.get("pr_fingerprint") or "") or st.get(f"{n}:{x.get('title')}")

def backfill(since=None, only=(), gh_ok=True):
    """Review list only: which handoff issues the daily report would have opened for PRs whose report came after that day's
    daily run (or whose day had no run, or which the daily listed as fault / health). Rulings per finding: a signed ruling
    with the fingerprint, else an old PR-wide one, skips; a shadow proposal (out/p2/state.json) alone is 待人工确认, never
    opened. Only PRs merged into main (or with no base recorded). Writes out/handoff/backfill-<today>.{md,json}; nothing else."""
    if os.path.exists(BACKFILL_OFF()): log(f"backfill off ({BACKFILL_OFF()} exists)"); return None
    rul = no_fix_findings(open(RULINGS).read() if os.path.exists(RULINGS) else "")
    st = jload(P2_STATE(), {})
    keys, src, gi = known_keys(gh_ok)
    gh_failed = {rt["repo"] for rt in routes()} - set(gi)
    rows, no_route = [], set()
    for f in sorted(glob.glob(f"{RPT}/PR-*.json")):
        m = re.search(r"PR-(\d+)\.json$", f)
        if not m: continue
        n = int(m.group(1))
        if only and n not in only: continue
        _, r = load_report(n)
        pr = (r or {}).get("pr")
        if not pr or not pr.get("merged_at"): continue
        day = parse_ts(pr["merged_at"]).astimezone(CST).date().isoformat()
        if since and day < since: continue
        gen, gsrc = report_time(f, r["doc"]); ran, rsrc = daily_run_time(day)
        meta = jload(f"{BASE}/out/daily/{day}.json", {}) or {}
        sick = n in (meta.get("fault") or []) or n in {h.get("pr") for h in meta.get("health") or []}
        if ran and gen <= ran and not sick: continue
        when = f"报告 {gen.astimezone(CST).strftime('%m-%d %H:%M')}（{gsrc}）vs 日报 " + (f"{ran.astimezone(CST).strftime('%m-%d %H:%M')}（{rsrc}）" if ran else "无记录") + ("；日报列为 fault / health" if sick else "")
        login = pr.get("author"); login = login.get("login") if isinstance(login, dict) else login
        rt = route_for(login)
        if not rt: no_route.add(n); continue
        base = {"day": day, "pr": n, "repo": rt["repo"], "when": when}
        if (pr.get("base_ref") or "main") != "main":
            rows.append({**base, "level": "—", "key": "—", "loc": "—", "status": "out_of_scope(non-main)", "why": f"合入 {pr['base_ref']}，不是 main"}); continue
        held = n in {c.get("pr") for c in meta.get("carry_over") or []} or str(n) in (jload(f"{PI_CODE}/conf/daily-calibrations.json", {}).get("by_day", {}).get(day, {}).get("hold") or {})
        for x in pr.get("findings") or []:
            if x.get("severity") not in ("P0", "P1", "P2"): continue
            fp = x.get("fingerprint") or ""; pfp = x.get("pr_fingerprint") or f"{fp}-pr{n}"
            loc = (x.get("file") or "未知") + (f":{x['line']}" if x.get("file") and x.get("line") is not None else "")
            row = {**base, "level": x["severity"], "key": f"handoff:{n}:{pfp}", "loc": loc, "title": x.get("title")}
            sh = shadow_ruling(st, n, x)
            if held: row.update(status="carry_over", why="日报没发这个 PR（carry_over / hold）")
            elif not notifiable(x, new_notice=True): row.update(status="不可通知", why="统一分级下不算作者的问题")
            elif pfp in rul["by_fp"]: row.update(status=f"skipped({rul['by_fp'][pfp]})", why="p2-rulings.md 签字（按指纹）")
            elif n in rul["pr_wide"]: row.update(status=f"skipped({rul['pr_wide'][n]})", why="p2-rulings.md 签字（legacy，按 PR）")
            elif sh: row.update(status="待人工确认", why=f"只有影子裁决「{sh.get('ruling')}」（{sh.get('asked_at')}），要 PM 和 Praise PR 用 p2.py record 签字", asked_at=sh.get("asked_at"))
            elif pfp in keys or (fp and fp in keys): k = pfp if pfp in keys else fp; row.update(status="已开", why=f"{keys[k]}（{src[k]}）")
            elif rt["repo"] in gh_failed: row.update(status="未核实", why="gh 读取交接仓失败，不当作 would-open")
            else:
                row.update(status="would-open", why="报告晚于日报，没开过")
                row["action"] = {**handoff_issue_action(day, {"n": n, "tested_sha": pr.get("tested_sha"), "merge_sha": pr.get("merge_sha")}, x),
                                 "repo": rt["repo"], "route_author": login, "level": x["severity"]}
            rows.append(row)
    rows.sort(key=lambda x: (x["day"], x["pr"], x["key"]))
    wait = [x for x in rows if x["status"] == "待人工确认"]
    summary = {s: sum(1 for x in rows if x["status"] == s) for s in sorted({x["status"] for x in rows})}
    L = ["# 交接 issue 补开审核清单（Pi line，只写文件，不开 issue）", "", f"范围：数据日 ≥ {since or '全部'}" + (f"，PR {sorted(only)}" if only else "") +
         "；报告晚于该数据日日报（或那天没有日报记录、或日报列为 fault / health）的 PR，只算合入 main 的。", "",
         "统计：" + ("，".join(f"{k} {v}" for k, v in summary.items()) or "无候选") + (f"；没有交接路由的 PR {len(no_route)} 个" if no_route else ""), ""]
    if wait: L += [f"待人工确认 {len(wait)} 条，最早影子裁决 {min(str(x.get('asked_at')) for x in wait)}。", ""]
    L += ["| 数据日 | PR | 级别 | key | 目标仓 | 位置 | 报告时间 vs 日报时间 | 状态 | 原因 |", "|---|---|---|---|---|---|---|---|---|"]
    L += [f"| {x['day']} | #{x['pr']} | {x['level']} | {x['key']} | {x['repo']} | {x['loc']} | {x['when']} | {x['status']} | {x['why']} |" for x in rows]
    out = f"{BASE}/out/handoff/backfill-{today()}"
    write_atomic(out + ".md", "\n".join(L) + "\n")
    write_atomic(out + ".json", json.dumps({"since": since, "prs": sorted(only), "summary": summary, "no_route": sorted(no_route), "rows": rows}, ensure_ascii=False, indent=1) + "\n")
    return rows

COVERAGE_RE = re.compile(r"覆盖率|coverage", re.I)

def gh_paged(path):
    return [json.loads(l) for l in gh(["api", "--paginate", "--jq", ".[] | tojson", path], js=False).splitlines() if l.strip()]

def pr_comment_receipts(n, logins, since, fetch=None):
    """「结论：…」 receipts on the original PR #n (comments, review bodies, review comments) by one of `logins` after `since`:
    from 2026-10-24 the handoff convention also accepts a receipt on the original PR. -> [(kind, fix_pr, reason, text, url, at)]"""
    out = []
    for path in (f"repos/{REPO}/issues/{n}/comments?per_page=100", f"repos/{REPO}/pulls/{n}/reviews?per_page=100", f"repos/{REPO}/pulls/{n}/comments?per_page=100"):
        for c in (fetch or gh_paged)(path):
            who, at = (c.get("user") or {}).get("login"), parse_ts(c.get("created_at") or c.get("submitted_at"))
            if who not in logins or who in QA_LOGINS or not at or at <= since: continue
            r = parse_receipt(c.get("body"), n)
            if r: out.append((*r, f"{c.get('body') or ''}\n{c.get('path') or ''}", c.get("html_url"), at))
    return out

def receipt_applies(text, issue, peers):
    """A PR receipt answers a handoff issue when it names it (issue URL, the finding's file, or the start of its title) or names
    none of the PR's other open handoff issues; a generic receipt that talks about coverage only answers coverage issues."""
    names = lambda x: x["url"] in text or any(f and f in text for f in x["files"]) or (len(x["title"]) >= 6 and x["title"][:10] in text)
    if names(issue): return True
    if any(names(x) for x in peers if x is not issue): return False
    return not COVERAGE_RE.search(text) or bool(COVERAGE_RE.search(issue["title"]))

def scan(ro, pr_receipts=pr_comment_receipts):
    d = today(); keys, src, gi = known_keys()
    L = [f"## 扫描 {now_cst().strftime('%H:%M')} 北京时间（Pi line，只读）"]; acts = []
    local = {i["url"]: i for f in glob.glob(f"{QA}/handoff/*/issues.json") for i in json.load(open(f)).get("issues", [])}
    for rt in routes():
        iss = gi.get(rt["repo"])
        if iss is None: L.append(f"- {rt['repo']}: 未核实（gh 读取失败）"); continue
        rc, due = 0, []
        for i in iss:
            rec = author_receipts(i)
            mk = MARK.search(i.get("body") or "")
            # only QA handoff issues (marker + handoff label) are tracked; other issues in the repo never go overdue
            if not (mk and "handoff" in {(x.get("name") if isinstance(x, dict) else x) for x in i.get("labels") or []}):
                if i["state"] == "OPEN": L.append(f"- 跳过 {i['url']}（不是 QA 交接 issue：没有 mivo-qa-handoff 标记或 handoff 标签）")
                continue
            n = int(mk.group(2)); f, r = load_report(n)
            fp = mk.group(1)[:-len(f"-pr{n}")] if mk.group(1).endswith(f"-pr{n}") else mk.group(1)
            files = [x.get("file") for x in (((r or {}).get("pr") or {}).get("findings") or []) if x.get("fingerprint") == fp and x.get("file")]
            if not rec and i["state"] == "OPEN":   # 统一格式：写了「QA #原PR号」的修复 PR 合并了 → 已修，issue 收起（不计逾期）
                fixes = merged_fix_prs(n, files)
                if fixes: rec = [f"已修 #{fixes[0]['number']}（修复 PR 写了 QA #{n}，已合并）"]
            if rec: rc += 1; L.append(f"- {rt['repo']}#{i['number']} {i['state']} 回执「{rec[-1]}」")
            if i["url"] not in local: L.append(f"  - 注意：{i['url']} 不在本地 issues.json（Grok 未回写）")
            if i["state"] == "OPEN" and not rec and (now_cst() - parse_ts(i["createdAt"])).days >= 3:
                au = (((r or {}).get("pr") or {}).get("author") or {})
                au = au.get("login") if isinstance(au, dict) else au
                due.append({"issue": i, "n": n, "url": i["url"], "files": files, "author": au, "since": parse_ts(i["createdAt"]),
                            "title": re.sub(r"^\[P\d\]\s*#\d+\s*", "", i.get("title") or "").strip()})
        # a 「结论：…」 receipt on the original PR by its author or the route owner / cc also answers the issue (convention 10-24)
        by_pr = {}
        for x in due: by_pr.setdefault(x["n"], []).append(x)
        for n, xs in by_pr.items():
            logins = {rt["owner"]} | {a for x in xs if x["author"] for a in [x["author"], *rt.get("cc", {}).get(x["author"], [])]}
            try: prs = pr_receipts(n, logins, min(x["since"] for x in xs))
            except Exception as e: log("gh pr receipts fail", n, e); prs = []
            for x in xs:
                hit = next((p for p in prs if p[5] > x["since"] and receipt_applies(p[3], x, xs)), None)
                if hit: rc += 1; x["receipt"] = hit; L.append(f"- {rt['repo']}#{x['issue']['number']} OPEN 原 PR 下回执「{hit[0]}」{hit[4] or ''}")
        for x in due:
            if x.get("receipt"): continue
            acts.append({"key": f"overdue:{rt['repo']}#{x['issue']['number']}", "type": "feishu.dm+group", "issue": x["url"],
                         "age_days": (now_cst() - x["since"]).days, "owner": rt["owner"],
                         "text": overdue_text(rt, x), "cc": rt.get("cc", {}), "dedupe_against": f"{QA}/followup/state.json"})
        L.append(f"- {rt['repo']}: {len(iss)} 个 issue，带格式回执 {rc} 个，满 3 天无回执 {sum(1 for a in acts if rt['repo'] in a['key'])} 个")
    p = f"{BASE}/out/handoff/{d}{'.render' if ro else ''}.md"
    old = open(p).read() if os.path.exists(p) and not ro else f"# 交接仓回执扫描 {d}（Pi line，不写交接仓）\n"
    write_atomic(p, old + "\n" + "\n".join(L) + "\n")
    if not ro and now_cst().hour == 15: record_actions("handoff", d, acts)

def overdue_text(rt, x):
    """relay body for an overdue handoff issue (10-26: the action had no text -> outbound skipped it as「relay: empty body」)"""
    days = (now_cst() - x["since"]).days
    return (f"交接 issue {rt['repo']}#{x['issue']['number']}（原 PR #{x['n']}{'：' + x['title'] if x.get('title') else ''}）"
            f"开了 {days} 天还没有回执，负责人 {x.get('author') or rt['owner']}。\n{x['url']}")

def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    kind, payload = cli_preflight(argv, {"--render-only", "--diff-sent", "--since", "--pr"}, __doc__)
    if kind == "help":
        print(payload); return 0
    if kind == "error":
        print(payload, file=sys.stderr); return 2
    a, fl = payload
    if a and a[0] == "plan": plan(a[1] if len(a) > 1 else data_day([0]), "--render-only" in fl, "--diff-sent" in fl)
    elif a and a[0] == "backfill":
        v = argv[2:]; since = v[v.index("--since") + 1] if "--since" in v else None
        backfill(since, {int(v[i + 1]) for i, x in enumerate(v) if x == "--pr"})
    else: scan("--render-only" in fl)
if __name__ == "__main__": sys.exit(main() or 0)
