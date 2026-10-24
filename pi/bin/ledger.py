#!/usr/bin/env python3
"""日报 23:30 duty (Grok routine mivo-qa-ledger, 23:40 here): rebuild the 飞书通知台账 with the Grok tool itself.
fleet-mode=pi  : REAL rebuild exactly like the Grok routine — `node /workspace/mivo-qa/ledger/build-ledger.mjs <日>` (no
                 --dry-run) writes ledger/daily/<日>.json/.md (+ its own 60-day retention). Before: existing <日>.json/.md are
                 copied to <日>.json.bak-<ts>-prerebuild / <日>.md.bak-<ts>-prerebuild. After: the summary is re-counted from
                 the rows (and the row count compared with the previous ledger); a hard failure restores the backup (or moves
                 the new file to <日>.json.bad-<ts> when there was none, so readers keep the previous day) and exits 1.
fleet-mode=grok: unchanged dry-run (--dry-run -> scratch/ledger-dry/, never ledger/daily/), diff vs Grok's ledger/daily/<日>.json.
Both: copy to out/ledger/<日>.json/.md and write out/ledger/<日>.diff.md.
Statuses are the tool's: fixed (only after retest pass) / fix_merged_pending_retest / fix_pr_open / concluded / open(=unfixed).
usage: ledger.py [日期] [--render-only] [--dry-run] [--force]
  --render-only: skip node, just diff existing out/ledger vs ledger/daily;  --dry-run: dry-run even in pi mode;
  --force: keep a real rebuild whose row count dropped >10% vs the previous ledger (other checks still apply).
Test-only env (never set by cron): PI_QA_HOME (QA dir; also QA_HOME for node), PI_LEDGER_OUT (out/ledger dir)."""
import sys, shutil
from lib import *
QA_HOME = os.environ.get("PI_QA_HOME") or QA
OUTD = os.environ.get("PI_LEDGER_OUT") or f"{BASE}/out/ledger"
TOOL = f"{QA_HOME}/ledger/build-ledger.mjs"
DAILY = f"{QA_HOME}/ledger/daily"
STATUSES = ("fixed", "fix_merged_pending_retest", "fix_pr_open", "concluded", "open")
DROP_MAX = 0.10                                    # real rebuild losing >10% of the previous ledger's rows = suspect input

def jl(p):
    try: return json.load(open(p))
    except Exception: return None

def summ(p):
    d = jl(p) or {}
    return d.get("summary", {}), {r["key"]: r.get("status") for r in d.get("rows", d.get("items", [])) if isinstance(r, dict) and "key" in r}

def prev_official(d):
    fs = sorted(f for f in glob.glob(f"{DAILY}/*.json") if re.search(r"/\d{4}-\d{2}-\d{2}\.json$", f) and os.path.basename(f)[:10] < d)
    return fs[-1] if fs else None

def check(doc, d, prev_total=None, force=False):
    """Hard errors (list) + warnings (list): summary must be exactly what the rows say (same counting as build-ledger)."""
    errs, warns = [], []
    if not isinstance(doc, dict): return ["台账 JSON 读不出"], warns
    rows = doc.get("rows"); sm = doc.get("summary") or {}
    if doc.get("schema_version") != "1.0" or doc.get("kind") != "mivo-qa-feishu-ledger": errs.append(f"schema/kind 不对：{doc.get('schema_version')}/{doc.get('kind')}")
    if doc.get("date") != d: errs.append(f"date={doc.get('date')} 不是 {d}")
    if not isinstance(rows, list): return errs + ["rows 不是列表"], warns
    keys = [r.get("key") for r in rows]
    if len(keys) != len(set(keys)): errs.append(f"行 key 重复 {len(keys) - len(set(keys))} 个")
    if sm.get("total") != len(rows): errs.append(f"summary.total={sm.get('total')} ≠ 行数 {len(rows)}")
    bs = {k: sum(1 for r in rows if r.get("status") == k) for k in STATUSES}
    if sum(bs.values()) != len(rows): errs.append(f"有 {len(rows) - sum(bs.values())} 行 status 不在 {STATUSES}")
    if {k: (sm.get("by_status") or {}).get(k, 0) for k in STATUSES} != bs: errs.append(f"by_status {sm.get('by_status')} ≠ 行统计 {bs}")
    ob = {k: 0 for k in ("P0", "P1", "P2", "P3", "unknown")}
    for r in rows:
        if r.get("status") == "open": ob[r.get("level") if r.get("level") in ob else "unknown"] += 1
    if {k: (sm.get("open_by_level") or {}).get(k, 0) for k in ob} != ob: errs.append(f"open_by_level {sm.get('open_by_level')} ≠ 行统计 {ob}")
    rt = {k: sum(1 for r in rows if r.get("retest") == k) for k in ("pass", "fail", "pending")}
    if (sm.get("retest") or {}) != rt: errs.append(f"retest {sm.get('retest')} ≠ 行统计 {rt}")
    nh = sorted(r["key"] for r in rows if r.get("needs_human"))
    if sm.get("needs_you") != len(nh) or sorted(x.get("key") for x in doc.get("needs_you") or []) != nh: errs.append(f"needs_you {sm.get('needs_you')} ≠ needs_human 行 {len(nh)}")
    if prev_total and len(rows) < prev_total * (1 - DROP_MAX):
        (warns if force else errs).append(f"行数 {len(rows)} 比上一份台账 {prev_total} 少了 {prev_total - len(rows)}（>{int(DROP_MAX*100)}%，疑似输入文件读坏）")
    if not (doc.get("github_check") or {}).get("ok"): warns.append(f"GitHub 查询失败：{(doc.get('github_check') or {}).get('error')}（修复 PR 未核对，标 unknown）")
    return errs, warns

def diff_lines(a_path, b_path, la, lb):
    sa, ra = summ(a_path); sb, rb = summ(b_path)
    L = [f"- {la}: {json.dumps((sa or {}).get('by_status'), ensure_ascii=False) if sa else '无文件'} total={(sa or {}).get('total')}",
         f"- {lb}: {json.dumps((sb or {}).get('by_status'), ensure_ascii=False) if sb else '无文件'} total={(sb or {}).get('total')}"]
    add, rem = sorted(set(rb) - set(ra)), sorted(set(ra) - set(rb))
    dif = [(k, ra.get(k), rb.get(k)) for k in sorted(set(ra) & set(rb)) if ra.get(k) != rb.get(k)]
    L += [f"- 新增行 {len(add)}：" + ("、".join(k.split("#")[-1] for k in add[:40]) or "无"),
          f"- 消失行 {len(rem)}：" + ("、".join(k.split("#")[-1] for k in rem[:40]) or "无"),
          f"- 状态变化 {len(dif)}："] + [f"  - {k.split('#')[-1]}: {x} → {y}" for k, x, y in dif[:60]]
    return L

def copy_out(src_dir, d):
    os.makedirs(OUTD, exist_ok=True)
    for ext in ("json", "md"):
        src = f"{src_dir}/{d}.{ext}"
        if os.path.exists(src): shutil.copy(src, f"{OUTD}/{d}.{ext}")

def real(d, force):
    os.makedirs(DAILY, exist_ok=True)
    stamp = now_cst().strftime("%Y%m%d-%H%M%S"); tgt = f"{DAILY}/{d}.json"
    baks = {}
    for ext in ("json", "md"):
        f = f"{DAILY}/{d}.{ext}"
        if os.path.exists(f): baks[ext] = f"{f}.bak-{stamp}-prerebuild"; shutil.copy2(f, baks[ext]); log("backup", baks[ext])
    prev = prev_official(d); prev_total = (jl(prev) or {}).get("summary", {}).get("total") if prev else None
    r = subprocess.run(["node", TOOL, d], capture_output=True, text=True, timeout=900, env={**os.environ, "QA_HOME": QA_HOME})
    log("build-ledger (real) rc", r.returncode, r.stdout.strip()[-300:], r.stderr[-300:])
    errs, warns = check(jl(tgt), d, prev_total, force) if r.returncode == 0 else ([f"build-ledger rc={r.returncode}: {r.stderr.strip()[-200:]}"], [])
    if errs:
        for ext in ("json", "md"):
            f = f"{DAILY}/{d}.{ext}"
            if ext in baks: shutil.copy2(baks[ext], f); log("restored", f, "from", baks[ext])
            elif os.path.exists(f): os.replace(f, f"{f}.bad-{stamp}"); log("quarantined", f"{f}.bad-{stamp}")
        L = [f"# 台账重建 {d} 失败（pi 线真实写入，已{'恢复备份' if baks else '隔离新文件'}）", ""] + [f"- ❌ {e}" for e in errs] + [f"- ⚠ {w}" for w in warns]
        write_atomic(f"{OUTD}/{d}.diff.md", "\n".join(L) + "\n"); log("FAIL", "; ".join(errs)); sys.exit(1)
    copy_out(DAILY, d)
    sm = (jl(tgt) or {}).get("summary", {})
    L = [f"# 台账重建 {d}（pi 线，真实写入 ledger/daily，与 Grok mivo-qa-ledger 相同命令）", "",
         f"- 命令：QA_HOME={QA_HOME} node {TOOL} {d}", f"- 备份：{', '.join(baks.values()) or '无（当天首次生成）'}",
         f"- 核对：summary 与行统计一致 ✓（total {sm.get('total')}，by_status {json.dumps(sm.get('by_status'), ensure_ascii=False)}，"
         f"retest {json.dumps(sm.get('retest'), ensure_ascii=False)}，needs_you {sm.get('needs_you')}，来源 {json.dumps(sm.get('by_via'), ensure_ascii=False)}）"]
    L += [f"- ⚠ {w}" for w in warns]
    if baks.get("json"): L += ["", "## 对比本日旧文件（重建前）"] + diff_lines(baks["json"], tgt, "重建前", "重建后")
    if prev: L += ["", f"## 对比上一份台账 {os.path.basename(prev)}"] + diff_lines(prev, tgt, os.path.basename(prev)[:10], d)
    write_atomic(f"{OUTD}/{d}.diff.md", "\n".join(L) + "\n")

def dry(d, render_only):
    if not render_only:
        r = subprocess.run(["node", TOOL, "--dry-run", d], capture_output=True, text=True, timeout=900, env={**os.environ, "QA_HOME": QA_HOME})
        log("build-ledger --dry-run rc", r.returncode, r.stderr[-300:])
        copy_out(f"{QA_HOME}/scratch/ledger-dry", d)
        if r.returncode: sys.exit(1)
    L = [f"# 台账对比 {d}（Pi dry-run vs Grok ledger/daily）"] + diff_lines(f"{DAILY}/{d}.json", f"{OUTD}/{d}.json", "Grok", "Pi")
    write_atomic(f"{OUTD}/{d}.diff.md", "\n".join(L) + "\n")

def main():
    a = [x for x in sys.argv[1:] if not x.startswith("--")]; d = a[0] if a else today()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d): sys.exit(f"bad date {d!r}")
    if mode() == "pi" and "--dry-run" not in sys.argv and "--render-only" not in sys.argv:
        log(f"fleet-mode=pi -> real rebuild of {DAILY}/{d}.json"); real(d, "--force" in sys.argv)
    else:
        log(f"fleet-mode={mode()} -> dry-run (scratch/ledger-dry)"); dry(d, "--render-only" in sys.argv)
if __name__ == "__main__": main()
