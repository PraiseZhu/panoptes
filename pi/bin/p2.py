#!/usr/bin/env python3
"""Shadow P2 ruling proposals via Pi (tap/anthropic/claude-opus-5-5, read-only tools).
Never edits fleet-standards/p2-rulings.md. Writes out/p2/<date>.md + out/p2/state.json.
usage: p2.py                 shadow proposals (model calls, cron only)
       p2.py record <报告PR> <问题> <修|不修|结案> <理由> <证据路径...> [--by 会签人] [--status 规则状态]
         appends one row to p2-rulings.md (backup first, never overwriting a same-second backup); 规则状态 defaults to 「Jasper 已裁」.
         结案 = already fixed / settled, no new fix ticket (build-ledger, finding_policy, pm and pmnudge treat it like 已修).
       p2.py --help / -h     print this and exit (no model call, nothing written)"""
import sys, json, os, re, subprocess, shutil
from lib import *
TIER = "judge"  # pi-auto tier (opus-5-5/medium); Praise 2026-10-22: never pass --model/--thinking
MODEL = "pi-auto judge (tap/anthropic/claude-opus-5-5, medium)"
DAYS = int(os.environ.get("P2_DAYS", "3"))          # report dates within last N days
CAP = int(os.environ.get("P2_CAP", "6"))            # max model calls per run (cost guard)
STATE = f"{BASE}/out/p2/state.json"
SRC = "/tmp/pi-fallback-src"
GIT = "/workspace/mivo-canvas-plugin"

def collect(today):
    lo = (dt.date.fromisoformat(today) - dt.timedelta(days=DAYS)).isoformat()
    items = {}
    for f in glob.glob(f"{RPT}/PR-*.json"):
        try: d = json.load(open(f))
        except Exception: continue
        if (d.get("date") or "") < lo: continue
        for p in d.get("prs", []):
            for x in p.get("findings", []):
                if x.get("severity") != "P2": continue
                k = x.get("pr_fingerprint") or f"{p['number']}:{x.get('title')}"
                items[k] = {"key": k, "pr": p["number"], "title": x.get("title", ""), "file": x.get("file"),
                            "line": x.get("line"), "source": x.get("source"), "test_backed": x.get("test_backed"),
                            "evidence": x.get("evidence"), "report": f, "date": d.get("date"),
                            "sha": p.get("tested_sha") or p.get("merge_sha")}
    return items

def already_ruled(it, rul):
    """Grok 线按报告 PR 记裁决（一行可含多条，如「AI 测试 P2 两条」），所以按 PR 号匹配。"""
    for line in rul.splitlines():
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(c) >= 4 and c[1].lstrip("#") == str(it["pr"]): return True
    return False

def src_tree(sha):
    d = f"{SRC}/{sha[:12]}"
    if os.path.isdir(d): return d
    os.makedirs(d + ".tmp", exist_ok=True)
    a = subprocess.run(f"git -C {GIT} archive {sha} | tar -x -C {d}.tmp", shell=True, capture_output=True, text=True)
    if a.returncode != 0:
        subprocess.run(["git", "-C", GIT, "fetch", "--quiet", "origin", sha], capture_output=True)  # read-only fetch of object
        a = subprocess.run(f"git -C {GIT} archive {sha} | tar -x -C {d}.tmp", shell=True, capture_output=True, text=True)
        if a.returncode != 0: shutil.rmtree(d + ".tmp", True); raise RuntimeError(a.stderr[:200])
    subprocess.run(["chmod", "-R", "a-w", d + ".tmp"]); os.rename(d + ".tmp", d)
    return d

def ask(pr, its, cwd):
    lines = []
    for i, it in enumerate(its, 1):
        ev = json.dumps(it["evidence"], ensure_ascii=False)[:1500]
        lines.append(f"{i}. key={it['key']} title={it['title']} location={it['file']}:{it['line']} source={it['source']} test_backed={it['test_backed']}\n   evidence={ev}")
    prompt = f"""You are reviewing QA findings (severity P2) on merged PR #{pr} of xindong/mivo-canvas-plugin.
The current directory is a READ-ONLY snapshot of the repo at the tested commit. Do not modify anything, do not use the network.
QA report (read it if useful): {its[0]['report']}
For each finding below, open the cited code, decide whether it is a REAL issue worth fixing (「修」) or not (「不修」: false positive, already handled, negligible, or only coverage/test-noise).
Findings:
{chr(10).join(lines)}
Answer ONLY with a JSON array, one object per finding, keys: key, ruling ("修" or "不修"), reason (Chinese, <=120 chars, concrete), cite (list of "path:line" you actually read)."""
    env = dict(os.environ, PI_TASK_DIR=f"{BASE}/out/tasks", PI_NOTIFY_OFF="1")
    r = subprocess.run([os.path.expanduser("~/tools/pi-call/pi-auto"), TIER, cwd, prompt, "--session", f"pi-fallback-p2-{pr}",
                        "--owner", "pi-fallback-cron", "--no-write", "--no-web", "--timeout", "600"],
                       capture_output=True, text=True, env=env, timeout=700)
    m = re.search(r"^RESULT (.+)$", r.stdout, re.M)
    tid = re.search(r"^TASK (\S+)", r.stdout, re.M)
    if r.returncode != 0 or not m: raise RuntimeError(f"pi-task rc={r.returncode} {r.stdout[-300:]} {r.stderr[-300:]}")
    txt = open(m.group(1).strip()).read()
    j = re.search(r"\[.*\]", txt, re.S)
    return json.loads(j.group(0)), (tid.group(1) if tid else "?")

RULINGS = "/workspace/fleet-standards/p2-rulings.md"
VERDICTS = ("修", "不修", "结案")
PENDING = "⚠ P2 裁决规则（只修判「修」的 P2；PM 与 Praise PR 逐个核对代码和 QA 报告证据）是 2026-10-22 Jasper 转述的新规则，Praise 本人尚未确认。"

def record(argv):
    """p2.py record <报告PR> <问题> <修|不修|结案> <理由> <证据路径...> --by "PM+Praise PR" [--status 规则状态]
    PM 与 Praise PR 会签后才用：追加一行到 p2-rulings.md（日期、报告 PR、问题、裁决、理由、证据、会签、规则状态）。
    模型提议（cron）永不调用这里。"""
    by, status = "PM+Praise PR", "Jasper 已裁"
    if "--by" in argv: i = argv.index("--by"); by = argv[i + 1]; argv = argv[:i] + argv[i + 2:]
    if "--status" in argv: i = argv.index("--status"); status = argv[i + 1]; argv = argv[:i] + argv[i + 2:]
    if len(argv) < 5 or argv[2] not in VERDICTS: sys.exit("usage: p2.py record <PR> <问题> <修|不修|结案> <理由> <证据路径...> [--by 会签人] [--status 规则状态]")
    pr, title, verdict, reason, ev = int(argv[0].lstrip("#")), argv[1], argv[2], argv[3], argv[4:]
    for e in ev:
        if not os.path.exists(e.split(":")[0]) and not e.startswith(("http", "#")): sys.exit(f"证据路径不存在：{e}")
    clean = lambda x: x.replace("|", "/").replace("\n", " ")
    txt = open(RULINGS).read() if os.path.exists(RULINGS) else "# P2 裁决记录\n\n"
    if PENDING not in txt:
        txt = txt.replace("# P2 裁决记录\n", "# P2 裁决记录\n\n" + PENDING + "\n", 1)
    if "| 证据 |" not in txt:
        txt = txt.replace("| 日期 | 报告 PR | 问题 | 裁决 | 理由 |\n|---|---|---|---|---|", "| 日期 | 报告 PR | 问题 | 裁决 | 理由 | 证据 | 会签 | 规则状态 |\n|---|---|---|---|---|---|---|---|")
    if not txt.endswith("\n"): txt += "\n"
    txt += f"| {dt.datetime.now(CST).date().isoformat()} | #{pr} | {clean(title)} | {verdict} | {clean(reason)} | {'<br>'.join(map(clean, ev))} | {clean(by)} | {clean(status)} |\n"
    if os.environ.get("P2_DRY"): print(txt); return
    if os.path.exists(RULINGS):
        b = RULINGS + ".bak-" + dt.datetime.now(CST).strftime("%Y%m%d-%H%M%S"); n, i = b, 1
        while os.path.exists(n): n = f"{b}-{i}"; i += 1   # two records in one second keep both backups
        shutil.copy(RULINGS, n)
    write_atomic(RULINGS, txt)

def main():
    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]: print(__doc__); return
    if len(sys.argv) > 1 and sys.argv[1] == "record": return record(sys.argv[2:])
    if any(x.startswith("-") for x in sys.argv[1:]): sys.exit(f"unknown option(s) {sys.argv[1:]}\n{__doc__}")   # run.sh may pass a data day (ignored)
    subprocess.run(f"find {SRC} -mindepth 1 -maxdepth 1 -type d -mtime +1 -exec chmod -R u+w {{}} \\; -exec rm -rf {{}} +", shell=True)  # keep /tmp small
    today = dt.datetime.now(CST).date().isoformat()
    out = f"{BASE}/out/p2/{today}.md"
    st = json.load(open(STATE)) if os.path.exists(STATE) else {}
    rul = open("/workspace/fleet-standards/p2-rulings.md").read()
    items = collect(today)
    todo = [it for k, it in items.items() if not already_ruled(it, rul) and k not in st]
    log(f"P2 in last {DAYS}d: {len(items)}; already in p2-rulings: {sum(already_ruled(i, rul) for i in items.values())}; cached: {sum(k in st for k in items)}; to ask: {len(todo)}")
    byp = {}
    for it in todo: byp.setdefault(it["pr"], []).append(it)
    calls = 0
    for pr, its in sorted(byp.items(), reverse=True):
        if calls >= CAP: log(f"cap {CAP} reached; rest next run"); break
        try:
            # verify the finding's cited file exists at the tested sha (fact check before asking)
            cwd = src_tree(its[0]["sha"]); calls += 1
            res, tid = ask(pr, its, cwd)
            for it in its:
                o = next((x for x in res if x.get("key") == it["key"]), None)
                exists = bool(it["file"]) and os.path.exists(os.path.join(cwd, it["file"]))
                st[it["key"]] = {**{k: it[k] for k in ("pr", "title", "file", "line", "report", "date", "source", "test_backed")},
                                 "ruling": (o or {}).get("ruling", "未判"), "reason": (o or {}).get("reason", "模型未返回"),
                                 "cite": (o or {}).get("cite", []), "file_exists_at_sha": exists, "task": tid,
                                 "asked_at": dt.datetime.now(CST).isoformat(timespec="minutes"), "model": MODEL}
            log(f"#{pr}: {len(its)} ruled via {tid}")
        except Exception as e:
            log(f"#{pr}: FAILED {e}")
        write_atomic(STATE, json.dumps(st, ensure_ascii=False, indent=1))
    L = [f"<!-- SHADOW proposals by pi-fallback; model {MODEL} via pi-task --no-write --no-web; NOT written to p2-rulings.md -->",
         f"# P2 裁决提议 {today}（北京时间，待 PM / Praise PR 核对）", "", PENDING, "",
         "流程：本表是模型提议，不进 p2-rulings.md。PM 与 Praise PR 逐条对照代码（引用列）和 QA 报告（报告列）核对，会签后用 `p2.py record <PR> <问题> <修|不修> <理由> <证据路径...>` 写入 p2-rulings.md。", "",
         f"范围：最近 {DAYS} 天报告中的 P2，共 {len(items)} 条；不含已在 p2-rulings.md 中的。", "",
         "| 日期 | 报告 PR | 问题 | 裁决 | 理由 | 引用 | 报告 |", "|---|---|---|---|---|---|---|"]
    for k, it in sorted(items.items(), key=lambda kv: -kv[1]["pr"]):
        if already_ruled(it, rul): continue
        s = st.get(k)
        if not s: L.append(f"| {it['date']} | #{it['pr']} | {it['title']} | 待问 | — | {it['file']}:{it['line']} | {it['report']} |"); continue
        cite = "; ".join(s["cite"]) or f"{s['file']}:{s['line']}"
        flag = "" if s["file_exists_at_sha"] or not s["file"] else "（⚠引用文件在测试提交中不存在）"
        L.append(f"| {s['date']} | #{s['pr']} | {s['title']} | {s['ruling']} | {s['reason']}{flag} | {cite} | {s['report']} |")
    write_atomic(out, "\n".join(L) + "\n")

if __name__ == "__main__": main()
