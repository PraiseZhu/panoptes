#!/usr/bin/env python3
"""知识沉淀 · 风格周选 Top 5 (Grok: x-trending-style-weekly-pick, Fridays at tail of 风格收集) — Pi line, zero-model.
Reads this ISO week's sets under the style library (+ today's out/ sets), counts per day/genre, picks top 5 by
IP 标签强度 -> engagement (bookmarks+likes) with genre diversity (<=2 per genre), writes weekly-YYYY-Www.md + side-by-side
collages (cover|inline) + overview into out/knowledge/weekly-YYYY-Www/. Outbound (atelier push, Friday summary) -> actions.json.
Usage: xstyle_weekly.py [--dry-run] [--week YYYY-Www]"""
from lib import *
from knowledge import STYLE_LIB
from PIL import Image
GENRE_CAP, TOP = 2, 5
def genres():
    """set path -> genre, from index.md '### <genre>' headings."""
    G, cur = {}, None
    try:
        for l in open(f"{STYLE_LIB}/index.md"):
            if l.startswith("### "): cur = l[4:].strip()
            m = re.match(r"- (20\d\d-\d\d-\d\d/[^\s　]+)", l)
            if m and cur: G[m.group(1)] = cur
    except FileNotFoundError: pass
    return G
def parse(p):
    t = open(f"{p}/style.md").read() if os.path.exists(f"{p}/style.md") else ""
    num = lambda k: int((re.search(k + r"\s*([\d,]+)", t) or [0, "0"])[1].replace(",", "") or 0)
    ip = re.search(r"标签强度[^\d]*(\d)", t)
    return {"rel": "/".join(p.rstrip("/").split("/")[-2:]), "path": p, "ip": int(ip.group(1)) if ip else 0,
            "likes": num("点赞"), "bm": num("收藏"), "link": (re.search(r"https://x\.com/\S+/status/\d+", t) or [""])[0],
            "title": (re.search(r"标题：(.+)", t) or ["", ""])[1].strip(), "handle": p.rstrip("/").split("-", 1)[-1].split("/")[-1].split("-", 1)[-1]}
def collage(s, dst):
    ims = [Image.open(f) .convert("RGB") for k in ("cover", "inline") for f in glob.glob(f"{s['path']}/{k}.*")[:1]]
    if not ims: return False
    h = 552; ims = [i.resize((max(1, int(i.width * h / i.height)), h)) for i in ims]
    c = Image.new("RGB", (sum(i.width for i in ims) + 20 * (len(ims) - 1), h), "white"); x = 0
    for i in ims: c.paste(i, (x, 0)); x += i.width + 20
    os.makedirs(os.path.dirname(dst), exist_ok=True); c.save(dst, quality=85); return True
def main():
    dry = "--dry-run" in sys.argv
    wk = sys.argv[sys.argv.index("--week") + 1] if "--week" in sys.argv else "%d-W%02d" % now_cst().date().isocalendar()[:2]
    y, w = map(int, wk.split("-W"))
    days = [dt.date.fromisocalendar(y, w, i).isoformat() for i in range(1, 6)]
    sets = [parse(p) for d in days for p in sorted(glob.glob(f"{STYLE_LIB}/{d}/*/") + glob.glob(f"{BASE}/out/knowledge/{d}.xstyle/*/"))]
    G = genres(); per_day = {d: sum(1 for s in sets if f"/{d}" in s["path"] or f"{d}.xstyle" in s["path"]) for d in days}
    per_genre = {}
    for s in sets: s["genre"] = G.get(s["rel"], "未归类"); per_genre[s["genre"]] = per_genre.get(s["genre"], 0) + 1
    ranked = sorted(sets, key=lambda s: (-s["ip"], -(s["bm"] + s["likes"])))
    top, used = [], {}
    for s in ranked:
        if used.get(s["genre"], 0) >= GENRE_CAP: continue
        top.append(s); used[s["genre"]] = used.get(s["genre"], 0) + 1
        if len(top) == TOP: break
    od = f"{BASE}/out/knowledge/weekly-{wk}"
    L = [f"# {wk} 风格周选（Top 5）（Pi line{' dry-run' if dry else ''}，零模型规则选）", "",
         f"本周库：{len(sets)} 套（目标 50，{'已达标' if len(sets) >= 50 else '未达标'}）。" + "，".join(f"{d[5:]} {n} 套" for d, n in per_day.items()) + "。",
         f"评选规则：个人标签强度 → 互动（收藏+点赞）→ 流派多样（同流派最多 {GENRE_CAP}）；与 grokbot-dark 撞车风险见各 style.md。", "", "## 本周各流派数量"]
    L += [f"- {g}：{n}" for g, n in sorted(per_genre.items(), key=lambda x: -x[1])]
    for i, s in enumerate(top, 1):
        img = f"{od}/top5/{i}-{s['handle']}.jpg"; ok = (not dry) and collage(s, img)
        L += ["", f"## {i}. {s['genre']} · @{s['handle']}", f"- 路径：`{s['rel']}`", f"- 标题：{s['title'] or '（见 style.md）'}", f"- 链接：{s['link'] or '无数据'}",
              f"- 为何入选：标签 {s['ip']}/5；赞 {s['likes']} / 收藏 {s['bm']}", f"- 并排图：`{img if ok else '(dry-run 未生成)'}`"]
    if not top: L += ["", "- 本周无可选套（X_BEARER_TOKEN 缺失时 Pi 线无新收集）"]
    write_atomic(f"{od}.md", "\n".join(L) + "\n")
    if top and not dry:
        tiles = [Image.open(f) for f in sorted(glob.glob(f"{od}/top5/*.jpg"))]
        if tiles:
            W = max(t.width for t in tiles); ov = Image.new("RGB", (W, sum(t.height for t in tiles) + 20 * len(tiles)), "white"); yy = 0
            for t in tiles: ov.paste(t, (0, yy)); yy += t.height + 20
            ov.save(f"{od}/top5/overview.jpg", quality=80)
    d = today(); acts = [{"key": f"xstyle-weekly-summary:{wk}", "type": "grokbot.chat-to-Praise", "to": "Praise", "body_file": f"out/knowledge/weekly-{wk}.md",
             "summary": f"{wk} 风格库 {len(sets)} 套，Top 5 见文件", "dedupe": "Grok 线同周已发周选（routine 每日X热门长文配图风格收集 周五记录）则跳过"}]
    if mode() == "pi":
        acts.append({"key": f"atelier-push:style-library:{wk}", "type": "git.push.atelier", "repo": "PraiseZhu/atelier", "path": "style-library/",
                     "precheck": "gh repo view PraiseZhu/atelier --json visibility == PRIVATE", "src": [STYLE_LIB, f"{od}.md", od],
                     "dedupe": "origin/main 已含 weekly-" + wk + ".md 且相同 -> done"})
    record_actions("knowledge", d, acts)
    return 0
if __name__ == "__main__": sys.exit(main())
