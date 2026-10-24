#!/usr/bin/env python3
"""知识沉淀 · 每周10个选题 (Grok routine 每周10个选题, Fri 08:04 CST) — Pi line, files only.
Flow (parity with Grok spec): X hot AI Articles + hot topics of past 7 days (X API v2 when X_BEARER_TOKEN set, else
blocked + 热度=无数据) -> dedupe vs atelier articles/ + outlines/ -> 20-30 candidates scored on 6 criteria (1-5) -> 10
(>=half from this week's X hot topics; each of 4 IP tags >=1; top 3 starred) -> one-week data review for articles
published >=7 days -> out/knowledge/<d>.md, and in pi mode atelier outlines/<d>-weekly-topics.md.
Outbound (git push, summary message to Praise) -> actions.json only (never executed here).
Usage: knowledge.py [--dry-run] [--force]   (--dry-run: no model call, no atelier write; writes prompt + skeleton)"""
import urllib.request, urllib.parse
from lib import *
ATELIER = "/workspace/atelier"
STYLE_LIB = "/home/box/style-library/x-trending"
TAGS = ["AI 时代组织运转", "设计师的角色转型", "研发机制", "自动化工作流"]   # 摄影 optional
CRITERIA = ["热度", "独家素材", "观点潜力", "个人标签", "双渠道成立", "新鲜度"]
X_QUERIES = ['(AI agent OR "Claude Code" OR Codex) has:links -is:retweet lang:en url:"x.com/i/article"',
             '(AI design OR designer AI) -is:retweet', '(AI org OR engineering process OR R&D) agent -is:retweet',
             '("automated workflow" OR automation agents) -is:retweet']
N_CAND, N_PICK, MIN_X_HOT = (20, 30), 10, 5

def x_search(q, max_results=50, days=7):
    """X API v2 recent search (read-only app bearer). Returns [{id,url,author,text,metrics,created_at}]. Raises if no token."""
    tok = os.environ.get("X_BEARER_TOKEN")
    if not tok: raise RuntimeError("X_BEARER_TOKEN unset")
    start = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days, minutes=-5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    p = {"query": q, "max_results": max_results, "start_time": start, "sort_order": "relevancy",
         "tweet.fields": "public_metrics,created_at,author_id,attachments,entities,article",
         "expansions": "author_id,attachments.media_keys", "user.fields": "username",
         "media.fields": "url,preview_image_url,type"}
    for attempt in range(3):   # 429/5xx backoff 30s,60s
        try:
            req = urllib.request.Request("https://api.x.com/2/tweets/search/recent?" + urllib.parse.urlencode(p),
                                         headers={"Authorization": f"Bearer {tok}"})
            j = json.load(urllib.request.urlopen(req, timeout=30)); break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < 2: time_sleep(30 * (attempt + 1)); continue
            raise
    users = {u["id"]: u["username"] for u in j.get("includes", {}).get("users", [])}
    media = {m["media_key"]: m for m in j.get("includes", {}).get("media", [])}
    out = []
    for t in j.get("data", []):
        h = users.get(t.get("author_id"), "i")
        out.append({"id": t["id"], "url": f"https://x.com/{h}/status/{t['id']}", "author": h, "text": t.get("text", "")[:280],
                    "metrics": t.get("public_metrics", {}), "created_at": t.get("created_at"),
                    "media": [media[k].get("url") or media[k].get("preview_image_url") for k in t.get("attachments", {}).get("media_keys", []) if k in media],
                    "fetched_at": now_cst().isoformat(timespec="minutes")})
    return out
def time_sleep(s): import time; time.sleep(s)

def style_counts():
    """Count collected SETS = dated dir / <nn>-<handle> dirs (fixes old bug that counted top-level entries)."""
    sets = [p for p in glob.glob(f"{STYLE_LIB}/20??-??-??/*/") if os.path.isdir(p)]
    wk = now_cst().date().isocalendar()[:2]
    week = [p for p in sets if dt.date.fromisoformat(p.rstrip("/").split("/")[-2]).isocalendar()[:2] == wk]
    return len(sets), len(week)

def existing_titles():
    """Dedupe corpus: atelier articles/ (dir names + first heading) and outlines/ file names."""
    T = []
    for p in sorted(glob.glob(f"{ATELIER}/articles/*/*")):
        T.append(os.path.basename(p))
        for f in glob.glob(f"{p}/*.md")[:2]:
            try: h = next((l.strip("# \n") for l in open(f) if l.startswith("#")), ""); h and T.append(h)
            except Exception: pass
    T += [os.path.basename(p) for p in glob.glob(f"{ATELIER}/outlines/*.md")]
    return T

def due_reviews():
    """Articles published >=7 days ago (dated dir name YYYY-MM-DD-*) and published within the last 21 days -> need one-week data review."""
    d0 = now_cst().date(); R = []
    for p in glob.glob(f"{ATELIER}/articles/*/*"):
        m = re.match(r"(\d{4}-\d{2}-\d{2})", os.path.basename(p))
        if m and 7 <= (d0 - dt.date.fromisoformat(m.group(1))).days <= 21: R.append(p)
    return sorted(R)

def build_prompt(d, hot, blocked, titles, reviews):
    hot_txt = json.dumps(hot[:60], ensure_ascii=False) if hot else "（无：X 数据 blocked，热度一律写「无数据」，X 链接一律写「无数据（未取到，不编造）」）"
    return f"""你是 Praise 的「知识沉淀」助手。今天 {d}，产出本周 10 个选题（与 Grok 线 routine「每周10个选题」完全同规格）。
输入 1 · 本周 X 热门 AI 长文/热点（真实数据，含链接与互动数；抓取时间见 fetched_at）：{hot_txt}
输入 2 · 去重：以下 atelier 已有文章/大纲，选题不得重复或换皮：{json.dumps(titles, ensure_ascii=False)}
输入 3 · 满一周待复盘的文章目录：{json.dumps(reviews, ensure_ascii=False)}
步骤：先列 {N_CAND[0]}-{N_CAND[1]} 个候选，每个按 6 项 1-5 打分（{'/'.join(CRITERIA)}），选 10 个。
硬约束：至少 {MIN_X_HOT} 个来自本周 X 热点；四个 IP 标签（{'、'.join(TAGS)}；摄影可选）每个至少 1 个；前 3 名标 ⭐ 并写理由。
每张卡片：中文标题、英文标题、标签、开头三行（冲突/场景）、为什么值得写（含热度依据）、需要 Praise 口述补充的料、6 项打分、真实 X 链接（只能用输入 1 里的链接，没有就写「无数据」，绝不编造）。
另起一节「一周数据复盘」：对输入 3 每篇写 X 浏览/赞/收藏 vs 同周热门长文；无数据就写「无数据（需 X OAuth2 user token）」。
写作规则：文章里不引用美国的帖子/人物；不出现任何公司或项目名（绝不写 mivocanvas）；不编任何数字。输出 Markdown，文件头一行 `# 每周选题 {d}`。"""

def skeleton(d, blocked):
    L = [f"# 每周选题 {d}（Pi line dry-run 骨架，未调模型）", "", "## 候选打分表（20-30 个，6 项 1-5）",
         "| # | 中文标题 | 标签 | " + " | ".join(CRITERIA) + " | 合计 | 来源 |", "|" + "---|" * (len(CRITERIA) + 5)]
    L += [f"| {i} | （待模型） | {TAGS[i % 4]} | " + ("无数据 | " if blocked else "- | ") + "- | " * 5 + "- | " + ("无数据 |" if blocked else "X |") for i in range(1, N_CAND[0] + 1)]
    L += ["", "## 入选 10 个（⭐ 前 3）"]
    for i in range(1, N_PICK + 1):
        L += [f"### {'⭐ ' if i <= 3 else ''}{i}. （中文标题）/（English title）", f"- 标签：{TAGS[(i - 1) % 4]}", "- 开头三行：", "- 为什么值得写（热度依据）：" + ("无数据" if blocked else ""),
              "- 需要口述补充的料：", "- 打分：" + " / ".join(f"{c} -" for c in CRITERIA), "- X 链接：" + ("无数据（未取到，不编造）" if blocked else "")]
    L += ["", "## 一周数据复盘", "- " + ("无数据（需 X OAuth2 user token）" if blocked else "")]
    return "\n".join(L)

def main():
    dry, force = "--dry-run" in sys.argv, "--force" in sys.argv or os.environ.get("FORCE")
    secret_env(); d = today(); m = mode()
    n_all, n_week = style_counts()
    L = [f"# 知识沉淀 {d}（Pi line，mode={m}）", f"- 风格库 {STYLE_LIB}：累计 {n_all} 套，本周 {n_week} 套（目标 ≥50/周）"]
    if now_cst().weekday() != 4 and not force:
        L.append("- 今天不是周五，不出选题"); write_atomic(f"{BASE}/out/knowledge/{d}.md", "\n".join(L) + "\n"); return 0
    hot, blocked = [], None
    if os.environ.get("X_BEARER_TOKEN") and not dry:
        try:
            seen = set()
            for q in X_QUERIES:
                for t in x_search(q):
                    if t["id"] not in seen: seen.add(t["id"]); hot.append(t)
            hot.sort(key=lambda t: -(t["metrics"].get("impression_count", 0) or t["metrics"].get("like_count", 0) * 100))
        except Exception as e: blocked = f"X 搜索失败：{str(e)[:200]}"
    elif not os.environ.get("X_BEARER_TOKEN"): blocked = "X_BEARER_TOKEN 缺失（热帖搜索、热度依据、真实链接、一周数据复盘均 blocked）"
    else: blocked = "dry-run：未调 X API"
    titles, reviews = existing_titles(), due_reviews()
    prompt = build_prompt(d, hot, blocked, titles, reviews)
    L += [f"- X 热帖：{len(hot)} 条" + (f"；blocked: {blocked}" if blocked else ""), f"- 去重语料 {len(titles)} 条；待一周复盘 {len(reviews)} 篇"]
    write_atomic(f"{BASE}/out/knowledge/{d}.prompt.txt", prompt)
    if hot: write_atomic(f"{BASE}/out/knowledge/{d}.xhot.json", json.dumps(hot, ensure_ascii=False, indent=1))
    if dry: body = skeleton(d, True); L.append("- dry-run：未调模型，下方为结构骨架")
    else:
        try: body, tid = pi("work", ATELIER, prompt, f"pi-line-topics-{d}", timeout=600); L.append(f"- task {tid}（work tier）")
        except Exception as e: body = skeleton(d, bool(blocked)); L.append(f"- 选题模型失败：{e}；输出骨架")
    target = f"{ATELIER}/outlines/{d}-weekly-topics.md"
    out = "\n".join(L) + "\n\n---\n\n" + body.strip() + "\n"
    write_atomic(f"{BASE}/out/knowledge/{d}.md", out)
    acts = []
    if m == "pi":
        if dry: log("dry-run: would write", target)
        else: write_atomic(target, body.strip() + "\n")
        acts.append({"key": f"atelier-push:outlines:{d}", "type": "git.push.atelier", "repo": "PraiseZhu/atelier", "path": f"outlines/{d}-weekly-topics.md",
                     "precheck": "gh repo view PraiseZhu/atelier --json visibility == PRIVATE; git log origin/main -- path 无同日提交",
                     "dedupe": "origin/main 已有该文件且内容相同 -> done", "commit_msg": f"outlines: {d} weekly topics (pi line)"})
    acts.append({"key": f"knowledge-summary:{d}", "type": "grokbot.chat-to-Praise", "to": "Praise",
                 "body_file": f"out/knowledge/{d}.md", "summary": f"本周 10 个选题已生成（⭐ 前 3 见文件）；X 热帖 {len(hot)} 条" + ("；X blocked" if blocked else ""),
                 "dedupe": "Grok 线同日已发（routine 每周10个选题 运行记录）则跳过"})
    record_actions("knowledge", d, acts)
    return 0
if __name__ == "__main__": sys.exit(main())
