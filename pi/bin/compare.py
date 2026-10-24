#!/usr/bin/env python3
"""Diff each Pi-line output against the Grok line's real output for the same data day -> out/compare/<date>.md"""
import sys
from lib import *
def rd(p):
    try: return open(p).read()
    except Exception: return None
def prset(t): return set(re.findall(r"^#(\d+) ", t or "", re.M))
def head_counts(t):
    m = re.search(r"合了 (\d+) 个 PR.*?P1 (\d+) 个.*?P2 (\d+) 个.*?P3 (\d+) 个", t or "")
    return m.groups() if m else None
def goals(t):
    g = {}
    for k in ("G1", "G2a", "G2b", "G3", "G5"):
        m = re.search(rf"{k}[^|\n]*?[|：:][^0-9\n]*?(\d+)\s*/\s*(\d+)", t or "")
        if m: g[k] = f"{m.group(1)}/{m.group(2)}"
    return g
def main():
    day = data_day(sys.argv); run_day = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
    L = [f"# Pi line vs Grok line — 数据日 {day}（运行日 {run_day}，北京时间）", "", "| 职责 | Pi 产出 | Grok 产出 | 结果 |", "|---|---|---|---|"]
    # 日报
    pi_d = rd(f"{BASE}/out/daily/{day}.md")
    gdir = f"{QA}/report/sent/{day}"; gtxt = None
    for f in sorted(glob.glob(f"{gdir}/*.txt")):
        t = rd(f)
        if t and "Mivo 播报" in t: gtxt, gf = t, f; break
    if pi_d and gtxt:
        a, b = prset(pi_d), prset(gtxt); ca, cb = head_counts(pi_d), head_counts(gtxt)
        res = f"PR 集合{'一致' if a == b else f'不一致 Pi多{sorted(a-b)} Grok多{sorted(b-a)}'}；计数(合/P1/P2/P3) Pi {ca} vs Grok {cb}"
        # per-level membership diff
        def lv(t):
            out = {}; cur = None
            for l in t.splitlines():
                m = re.match(r"^(P[0-3]) ", l)
                if m: cur = m.group(1); continue
                if l.startswith(("已私信", "未测试", "缺 QA")): cur = None
                n = re.match(r"^#(\d+) ", l)
                if n and cur: out[n.group(1)] = cur
            return out
        la, lb = lv(pi_d), lv(gtxt); diff = [f"#{k}:Pi {la.get(k)}/Grok {lb.get(k)}" for k in sorted(set(la) | set(lb)) if la.get(k) != lb.get(k)]
        if diff: res += "；级别不同：" + "、".join(diff)
        L.append(f"| 日报 | out/daily/{day}.md | {os.path.relpath(gf, QA)} | {res} |")
    else: L.append(f"| 日报 | {'有' if pi_d else '缺'} | {'有' if gtxt else '缺（未发或非文本存档）'} | 无法比较 |")
    # PM
    pp, gp = rd(f"{BASE}/out/pm/{day}.md"), rd(f"/workspace/fleet-standards/pm-daily/{day}.md")
    if pp and gp:
        ga, gb = goals(pp), goals(gp)
        L.append(f"| PM G1–G5 | out/pm/{day}.md | fleet-standards/pm-daily/{day}.md | " + "；".join(f"{k} Pi {ga.get(k,'-')} / Grok {gb.get(k,'-')}{'' if ga.get(k)==gb.get(k) else ' ≠'}" for k in ("G1","G2a","G2b","G3","G5")) + " |")
    else: L.append(f"| PM G1–G5 | {'有' if pp else '缺'} | {'有' if gp else '缺'} | 无法比较 |")
    # 运维复盘
    po, go = rd(f"{BASE}/out/ops/review-{day}.md"), rd(f"/workspace/review-ops/daily/{day}.md")
    if po and go:
        pc = re.findall(r"→\s*(维持P1|降为P2|降为P3|误报)", po); gc = re.findall(r"校准为 (P\d|P\d 误报)", go)
        L.append(f"| 运维复盘 | out/ops/review-{day}.md | review-ops/daily/{day}.md | Pi 校准结论 {len(pc)} 条 {pc}；Grok 校准 {len(gc)} 条 {gc}（逐条人工对照） |")
    else: L.append(f"| 运维复盘 | {'有' if po else '缺'} | {'有' if go else '缺'} | 无法比较 |")
    # 台账
    pl, gl = f"{BASE}/out/ledger/{run_day}.json", f"{QA}/ledger/daily/{run_day}.json"
    pl = pl if os.path.exists(pl) else f"{BASE}/out/ledger/{day}.json"; gl = gl if os.path.exists(gl) else f"{QA}/ledger/daily/{day}.json"
    if os.path.exists(pl) and os.path.exists(gl):
        a, b = json.load(open(pl))["summary"], json.load(open(gl))["summary"]
        bs = "一致" if a["by_status"] == b["by_status"] else f"Pi {a['by_status']} vs Grok {b['by_status']}"
        L.append(f"| 台账 | {os.path.relpath(pl, BASE)} | {os.path.relpath(gl, QA)} | total {a['total']}/{b['total']}；by_status {bs} |")
    else: L.append("| 台账 | " + ("有" if os.path.exists(pl) else "缺") + " | " + ("有" if os.path.exists(gl) else "缺") + " | 无法比较 |")
    # 交接回执
    ph = rd(f"{BASE}/out/handoff/{run_day}.md") or rd(f"{BASE}/out/handoff/{day}.md")
    gr = rd(f"{QA}/handoff/repo/daily/{day[:4]}/{day[5:7]}/{day}/receipts.md")
    if ph and gr is not None:
        pa = set(re.findall(r"#(\d+) 回执", ph)); ga_ = set(re.findall(r"#(\d+)", gr))
        L.append(f"| 交接回执(aj0928) | out/handoff | handoff/repo/daily/.../{day}/receipts.md | Pi 识别回执 issue {sorted(pa)}；Grok receipts 提到的编号 {sorted(ga_)[:15]} |")
    else: L.append(f"| 交接回执 | {'有' if ph else '缺'} | {'有' if gr is not None else '缺'} | 无法比较 |")
    # P2 裁决
    st = json.load(open(f"{BASE}/out/p2/state.json")) if os.path.exists(f"{BASE}/out/p2/state.json") else {}
    rul = rd("/workspace/fleet-standards/p2-rulings.md") or ""
    rows = re.findall(r"^\|\s*[\d-]+\s*\|\s*#?(\d+)\s*\|([^|]*)\|\s*(修|不修)\s*\|", rul, re.M)
    agree = dis = 0; dl = []
    for pr, title, r in rows:
        for v in st.values():
            if str(v["pr"]) == pr and (v["title"][:10] in title or title.strip()[:10] in v["title"]):
                if v["ruling"] == r: agree += 1
                else: dis += 1; dl.append(f"#{pr} Pi {v['ruling']}/Grok {r}")
    L.append(f"| P2 裁决 | out/p2/state.json（{len(st)} 条） | p2-rulings.md（{len(rows)} 条） | 同题一致 {agree}，不一致 {dis} {dl} |")
    # 合并官
    pm_ = rd(f"{BASE}/out/merger/{run_day}.md") or ""; au = [l for l in (rd("/workspace/mivo-merger/audit.log") or "").splitlines() if l.startswith(utc_window(day)[0][:10]) or l.startswith(utc_window(day)[1][:10])]
    L.append(f"| 合并官 | out/merger（合格 {len(re.findall('✅', pm_))} 次） | audit.log 当日 {len(au)} 行（Grok 线影子轮询已停，仅巡检） | 双方均 0 合并即一致 |")
    for k, p in (("Praise PR 复盘/修复", "praise-pr"), ("Jasper 简报", "jasper"), ("机器类核查", "box"), ("知识沉淀", "knowledge")):
        fs = sorted(glob.glob(f"{BASE}/out/{p}/{run_day}*")); L.append(f"| {k} | {len(fs)} 个文件 | 无盘上可比产出（Grok 线输出在聊天/routine 记录中） | 人工抽查 |")
    write_atomic(f"{BASE}/out/compare/{day}.md", "\n".join(L) + "\n")
if __name__ == "__main__": main()
