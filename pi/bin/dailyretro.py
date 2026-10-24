#!/usr/bin/env python3
"""Mivo QA 日报 09:56 复盘 (gated by takeover/dailyretro; --force for a manual file-only rerun).
Grok-line rules ported: retro reviews the report ALREADY SENT for <数据日> (report/sent/<日>/) against reports,
REPORT-STYLE.md and the Pi render/diff; it never resends, never backfills, never edits sent records, and posts nothing.
Posting to the Grok group is Grok-internal -> no action is recorded (nothing for outbound.py to execute).
usage: dailyretro.py [数据日] [--force]"""
from lib import *
def main():
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    if not takeover("dailyretro") and "--force" not in sys.argv: log("takeover/dailyretro absent -> skip"); return 0
    if mode() != "pi" and "--force" not in sys.argv: log("fleet-mode != pi -> skip (Grok line owns dailyretro)"); return 0
    day = a[0] if a else data_day([0])
    src = [f for f in [f"{QA}/report/sent/{day}/group-content.txt", f"{QA}/report/sent/{day}/run-notes.txt",
                       f"{QA}/report/REPORT-STYLE.md", f"{QA}/report/RETRO-NOTES.md", f"{BASE}/out/daily/{day}.render.md",
                       f"{BASE}/out/daily/{day}.diff", f"{PI_CODE}/conf/daily-calibrations.json", f"{BASE}/out/handoff/plan-{day}.render.md",
                       f"{BASE}/out/ledger/{day}.diff.md", f"{QA}/reports/merged"] if os.path.exists(f)]
    if not os.path.exists(src[0]) if src else True: log("no sent report for", day); return 0
    prompt = ("你是 Mivo QA 日报 bot，做数据日 " + day + " 的日报复盘（只读，不发任何消息、不补发、不回填）。读：" + ", ".join(src) +
              "。PR 报告在 reports/merged/PR-N.json。按 RETRO-NOTES.md 以往格式，中文，≤25 行：1) 已发日报的级别、@、私信是否与报告和 REPORT-STYLE 一致"
              "（逐条核对 P1/P2）；2) 漏报/误报（注意 #434 合并于北京 23:38、报告 00:01 生成、未进日报；api-probe 疑似旧探针）；3) 报告数据疑点交运维；"
              "4) 规则改动建议。不要编造；不确定写「未核实」。只输出复盘正文。")
    txt, tid = pi("work", BASE, prompt, f"dailyretro-{day}", timeout=600)
    out = f"{BASE}/out/daily/{day}.retro.md"
    if os.path.exists(out): os.replace(out, out + f".prev-{now_cst().strftime('%Y%m%d-%H%M')}")
    write_atomic(out, f"# 日报复盘 {day}（Pi line 补跑，task {tid}，{now_cst().strftime('%Y-%m-%d %H:%M')} 北京时间；只写文件，未发群）\n\n{txt}\n")
if __name__ == "__main__": sys.exit(main())
