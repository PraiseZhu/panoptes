#!/usr/bin/env python3
"""知识沉淀 · 每日X热门长文配图风格收集 (Grok routine, weekdays 10:04 CST) — Pi line, gated by takeover/xstyle.
Spec parity: >=10 sets/day (set = cover + inline image of the SAME hot AI X Article, real image URLs only), >=50/week;
skip posts already in index.md; relax to 7 days / adjacent topics if short; each set dir <nn>-<handle>/cover.*, inline.*,
style.md (link, author, engagement + fetch time, palette, type/layout, composition, texture, IP strength 1-5, what to
borrow, risks vs grokbot-dark — never modify grokbot-dark); index.md appended by style genre.
Shadow/pi both write to out/knowledge/<d>.xstyle/ (pi mode: also the live library). Fridays -> xstyle_weekly.py.
Needs X_BEARER_TOKEN. Usage: xstyle.py [--dry-run] [--force]"""
from lib import *
from knowledge import x_search, style_counts, STYLE_LIB
DAY_MIN, WEEK_MIN = 10, 50
QUERIES = ['url:"x.com/i/article" (AI OR agent OR LLM) has:media -is:retweet',
           'url:"x.com/i/article" (design OR workflow OR automation) has:media -is:retweet']
def collected_ids():
    try: return set(re.findall(r"/status/(\d+)", open(f"{STYLE_LIB}/index.md").read()))
    except FileNotFoundError: return set()
def prompt(d, outdir, cands, need, pi_mode):
    return f"""你是 Praise 的风格收集员（同 Grok routine「每日X热门长文配图风格收集」）。今天 {d}，本周已收 {style_counts()[1]} 套（周目标 ≥{WEEK_MIN}）。
候选（X API 真实数据，已排除 index.md 里收过的帖子）：{json.dumps(cands, ensure_ascii=False)}
任务：收至少 {need} 套（每套=同一篇热门 AI X 长文的 1 封面 + 1 正文配图，只用候选里真实的图片 URL，curl 下载）。不够就放宽到 7 天/相邻主题。
每套写到 {outdir}/<nn>-<handle>/cover.*、inline.*、style.md；style.md 含：链接、作者、互动数+抓取时间、配色、字体/版式、构图、质感、个人 IP 标签强度 1-5、可借鉴点、与 /home/box/style-library/grokbot-dark/ 的撞车风险（只读，绝不修改 grokbot-dark）。
再写 {outdir}/index-append.md：按风格流派分组，每行 `- {d}/<nn>-<handle>　标签强度 N/5　《标题》 链接`。
{'pi 模式：完成后把同样内容复制到 ' + STYLE_LIB + '/' + d + '/ 并把 index-append 追加进 ' + STYLE_LIB + '/index.md 本周节。' if pi_mode else '影子模式：不要写风格库本身。'}
不编造链接或数字。最后一行输出：SETS=<套数>"""
def main():
    dry, force = "--dry-run" in sys.argv, "--force" in sys.argv
    if not takeover("xstyle"): log("takeover/xstyle absent -> skip"); return 0
    secret_env(); d = today(); m = mode()
    if now_cst().weekday() > 4 and not force: log("weekend -> skip"); return 0
    outdir = f"{BASE}/out/knowledge/{d}.xstyle"; n_all, n_week = style_counts()
    head = f"# X 风格收集 {d}（mode={m}）\n- 风格库累计 {n_all} 套，本周 {n_week} 套（周目标 ≥{WEEK_MIN}；日目标 ≥{DAY_MIN}）\n"
    if not os.environ.get("X_BEARER_TOKEN"):
        write_atomic(f"{BASE}/out/knowledge/{d}.xstyle.md", head + "blocked: X_BEARER_TOKEN 缺失（需 X API app bearer, tweet.read/users.read）\n")
    elif dry: write_atomic(f"{BASE}/out/knowledge/{d}.xstyle.md", head + "dry-run：未调 X/模型\n")
    else:
        have = collected_ids(); cands = []
        for days in (1, 7):   # relax window if short
            for q in QUERIES:
                try: cands += [t for t in x_search(q, days=days) if t["id"] not in have and t["media"] and t["id"] not in {c["id"] for c in cands}]
                except Exception as e: log("x_search fail", str(e)[:200])
            if len(cands) >= DAY_MIN * 2: break
        os.makedirs(outdir, exist_ok=True)   # 10-24 Q4: the sandbox is read-only but for these (the style library only in pi mode)
        txt, tid = pi("work", "/tmp", prompt(d, outdir, cands[:40], DAY_MIN, m == "pi"), f"xstyle-{d}", timeout=900, write=True, web=True,
                      rw=[outdir] + ([STYLE_LIB] if m == "pi" else []))
        got = re.search(r"SETS=(\d+)", txt); n = int(got.group(1)) if got else 0
        write_atomic(f"{BASE}/out/knowledge/{d}.xstyle.md", head + f"- task {tid}；今日 {n} 套" + ("" if n >= DAY_MIN else f"（< {DAY_MIN}，未达标）") + f"\n\n{txt}\n")
    if now_cst().weekday() == 4 or os.environ.get("XSTYLE_WEEKLY"):
        r = subprocess.run([sys.executable, f"{PI_CODE}/bin/xstyle_weekly.py"] + (["--dry-run"] if dry else []), capture_output=True, text=True)
        log("xstyle_weekly rc", r.returncode, r.stdout[-400:], r.stderr[-400:])
    return 0
if __name__ == "__main__": sys.exit(main())
