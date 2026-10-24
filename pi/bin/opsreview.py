#!/usr/bin/env python3
"""运维 daily review (Grok: 10:57; Pi cron 11:15 CST, clear of even-hour :07 catch-up) daily review: run read-only collector into out/, then 'review' tier (gpt-6-luna, same as panoptes)
re-checks the data day's P1 code-review findings against code (calibration candidates). Cap 4 calls."""
import sys
from lib import *
from p2 import src_tree
def main():
    day = data_day(sys.argv); out = f"{BASE}/out/ops/review-{day}.md"
    r = subprocess.run(["python3", "/workspace/review-ops/bin/daily_review.py", "--date", day, "--out", out],
                       capture_output=True, text=True, timeout=900)
    log("collector rc", r.returncode, r.stderr[-200:])
    if not os.path.exists(out): open(out, "w").write(f"# 复盘 {day}\ncollector 失败 rc={r.returncode}\n")
    cal = ["", "## Pi line 校准（review tier，影子）"]; n = 0
    for p in merged_prs(day):
        f, rr = load_report(p["number"])
        if not rr or not rr.get("pr"): continue
        fs = [x for x in rr["pr"].get("findings", []) if x.get("source") == "code-review" and
              ("P1" in (x.get("severity"), x.get("llm_severity")) or "P0" in (x.get("severity"), x.get("llm_severity")))]
        if not fs or n >= int(os.environ.get("OPS_CAP", "4")): continue
        n += 1
        try:
            cwd = src_tree(rr["pr"].get("tested_sha") or rr["pr"]["merge_sha"])
            q = "\n".join(f"- {x.get('title')} @ {x.get('file')}:{x.get('line')} evidence={json.dumps(x.get('evidence'),ensure_ascii=False)[:1200]}" for x in fs)
            txt, tid = pi("review", cwd, f"""QA calibration for merged PR #{p['number']} of xindong/mivo-canvas-plugin. cwd is a read-only snapshot at the tested commit; do not modify files or use network.
For each code-review finding below, read the cited code and say whether the P1 stands or should be downgraded (P2/P3/误报). Reply in Chinese, one line per finding:
<title> → 维持P1|降为P2|降为P3|误报：<reason with path:line>
Findings:
{q}""", f"pi-line-opsreview-{p['number']}", timeout=600)
            cal.append(f"### #{p['number']}（{f}，task {tid}）\n{txt.strip()}")
        except Exception as e: cal.append(f"### #{p['number']} 失败：{e}")
    cal.append("\n> 校准只是提议：落盘需人工核实后用 `bin/opsfix.py calibrate ... --apply`（先留 .bak）；本脚本从不改 reports/merged/。")
    open(out, "a").write("\n".join(cal) + "\n"); log("appended calibration", n)
    # group post (Grok line posted conclusion+path to the「MivoCanvas QA」Grok group): file + action only, no executor
    body = open(out).read(); head = next((l for l in body.splitlines() if l.startswith("合并 PR")), "")
    post = (f"【运维复盘 {day}（Pi line）】{head}\n" f"产出对齐/准确度/修复跟进见 {out}\n"
            f"P1 校准提议 {n} 条（未落盘，待人工核实）。正式版仍写 /workspace/review-ops/daily/{day}.md（主持人）。")
    pp = f"{BASE}/out/ops/review-{day}.post.md"; write_atomic(pp, post + "\n")
    record_actions("ops", day, [{"key": f"ops-review:{day}", "type": "grok.group", "to": "MivoCanvas QA",
        "text_file": pp, "dedupe_basis": f"/workspace/review-ops/daily/{day}.md exists => Grok line already reviewed; Grok group has no API (not executable)"}])
if __name__ == "__main__": main()
