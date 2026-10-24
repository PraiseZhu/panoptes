#!/usr/bin/env python3
"""Praise PR daily retro (Pi line, file-only).
 1. Coverage: every P0-P2 finding in reports/merged/PR-*.json -> fix PR (body 'QA: #N', author PraiseZhu)
    or a note in /workspace/praise-pr/notes/ (filename carries N); anything without either is flagged MISSING.
 2. Turn-ready: draft fix PRs by PraiseZhu whose checks except review-pr are all pass (none pending/failing)
    -> action gh.pr.ready (key ready:<PR>). Ready PRs are never touched; review-pr is ignored.
 3. Cleanup LIST ONLY: worktrees / local branches of merged fix PRs whose tip is an ancestor of the merged
    head and whose worktree is clean. Nothing is deleted.
Usage: prretro.py [--dry-run]   (--dry-run prints the report + actions to stdout, writes nothing)"""
import sys
from lib import *
AUTHOR = "PraiseZhu"; NOTES = "/workspace/praise-pr/notes"; GIT = "/workspace/mivo-canvas-plugin"
DRY = "--dry-run" in sys.argv
OKB = ("pass", "skipping")

def git(*a, cwd=GIT):
    r = subprocess.run(["git", "-C", cwd, *a], capture_output=True, text=True); return r.returncode, r.stdout.strip()

def findings():
    out = []
    for f in sorted(glob.glob(f"{RPT}/PR-*.json")):
        try: doc = json.load(open(f))
        except Exception as e: out.append({"pr": f, "err": str(e)}); continue
        for p in doc.get("prs", []):
            for x in p.get("findings", []) or []:
                if x.get("severity") in ("P0", "P1", "P2"):
                    out.append({"pr": p["number"], "sev": x["severity"], "title": (x.get("title") or x.get("check_name") or x.get("check_id") or "")[:50],
                                "fp": x.get("fingerprint"), "verdict": x.get("verdict"), "file": x.get("file")})
    return out

def note_index():
    idx = {}
    for f in os.listdir(NOTES):
        if not f.endswith(".md"): continue
        lv = (re.search(r"-(p[0-2])[a-z]?\b", f.lower()) or [None, None])[1]
        for n in re.findall(r"(?<!\d)(\d{3,4})(?!\d)", f): idx.setdefault(int(n), []).append((f, lv))
    return idx

def main():
    d = today(); L = [f"# Praise PR 复盘 {d}（Pi line{' dry-run' if DRY else ''}，mode={mode()}）"]; acts = []
    prs = gh(["pr", "list", "-R", REPO, "--author", AUTHOR, "--state", "all", "--limit", "2000",
              "--json", "number,title,state,isDraft,body,headRefName,headRefOid,mergedAt"])
    fixmap = {}
    for p in prs:
        for n in set(re.findall(r"QA[:：]\s*#(\d+)", p.get("body") or "")): fixmap.setdefault(int(n), []).append(p)
    # 1 coverage
    fs = findings(); notes = note_index(); miss = []
    led = {}; lf = (sorted(glob.glob(f"{QA}/ledger/daily/*.json")) or [None])[-1]
    if lf:
        for r in json.load(open(lf)).get("rows", []): led[(r["pr"], r.get("fingerprint"))] = r
    L += ["", f"## P0–P2 覆盖（{len(fs)} 条，来自 reports/merged/*.json）", f"台账：{os.path.basename(lf or '无')}", "| QA PR | 级别 | 问题 | 修复 PR | note | 结论 |", "|---|---|---|---|---|---|"]
    for x in sorted(fs, key=lambda x: (str(x["pr"]), x.get("sev", ""))):
        if "err" in x: L.append(f"| {x['pr']} | ? | 读失败 {x['err']} | | | ⚠ |"); continue
        fx = fixmap.get(x["pr"], []); nt = [f for f, lv in notes.get(x["pr"], []) if lv in (None, x["sev"].lower())]
        fxs = " ".join(f"#{p['number']}({p['state'].lower()}{',draft' if p['isDraft'] else ''})" for p in fx)
        lr = led.get((x["pr"], x["fp"])) or {}
        if not fx and (lr.get("fix_pr") or lr.get("concluded_reason")):  # fix by someone else / concluded, per QA ledger
            fxs = f"台账:{('#'+str(lr['fix_pr'])) if lr.get('fix_pr') else ''} {lr.get('status_zh','')} {(lr.get('concluded_reason') or '')[:30]}".strip()
        ok = bool(fx or nt or lr.get("fix_pr") or lr.get("concluded_reason"))
        if not ok: miss.append(x)
        L.append(f"| #{x['pr']} | {x['sev']} | {x['title']} | {fxs} | {' '.join(nt)} | {'✅' if ok else '❌ 缺'} |")
    L += ["", f"**缺对应（无修复 PR 也无 note）：{len(miss)}** " + ", ".join(f"#{m['pr']} {m['sev']}" for m in miss)]
    # 2 turn-ready
    L += ["", "## 转 ready 判定（只看草稿；review-pr 忽略；ready PR 不碰）"]
    for p in [p for p in prs if p["state"] == "OPEN"]:
        if not re.search(r"QA[:：]\s*#\d+", p.get("body") or ""): continue
        if not p["isDraft"]: L.append(f"- #{p['number']} 已 ready → 不碰"); continue
        try: cs = gh(["pr", "checks", str(p["number"]), "-R", REPO, "--json", "name,bucket"])
        except RuntimeError as e:
            cs = None if "no checks" in str(e) else []; L.append(f"- #{p['number']} 查检查失败：{e}") if cs == [] else None
        cs = [c for c in (cs or []) if c["name"] != "review-pr"]
        bad = [f"{c['name']}={c['bucket']}" for c in cs if c["bucket"] not in OKB]
        if cs and not bad:
            L.append(f"- #{p['number']} 草稿，{len(cs)} 项检查全绿 → 将 gh pr ready")
            acts.append({"key": f"ready:{p['number']}", "type": "gh.pr.ready", "pr": p["number"], "head": p["headRefOid"],
                         "cmd": f"gh pr ready {p['number']} --repo {REPO}", "dedupe": "gh pr view isDraft==true at exec time"})
        else: L.append(f"- #{p['number']} 草稿，未全绿：{', '.join(bad[:6]) or '无检查'}")
    # 3 cleanup (list only)
    merged = {p["headRefName"]: p for p in prs if p["state"] == "MERGED" and re.search(r"QA[:：]\s*#\d+", p.get("body") or "")}
    L += ["", "## 清理候选（只列，不删）"]
    _, wl = git("worktree", "list", "--porcelain"); wts = {}
    for blk in wl.split("\n\n"):
        w = dict(l.split(" ", 1) if " " in l else (l, "") for l in blk.splitlines())
        if "branch" in w: wts[w["branch"].removeprefix("refs/heads/")] = w["worktree"]
    _, br = git("for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads")
    for line in br.splitlines():
        b, tip = line.split()
        p = merged.get(b); wt = wts.get(b)
        if not p: L.append(f"- 保留 {b}{' @'+wt if wt else ''}：不是已合并修复 PR 的分支"); continue
        if git("cat-file", "-e", p["headRefOid"])[0]: L.append(f"- 待核 {b}：合并 head {p['headRefOid'][:9]} 本地没有对象"); continue
        anc = git("merge-base", "--is-ancestor", tip, p["headRefOid"])[0] == 0
        clean = (git("status", "--porcelain", cwd=wt)[1] == "") if wt else True
        tag = "可清理" if anc and clean else "保留"
        L.append(f"- {tag} {b} (#{p['number']}) tip祖先={anc} 干净={clean}{' worktree='+wt if wt else ''}")
    det = [w for w in re.findall(r"^worktree (.+)\nHEAD \S+\ndetached", wl, re.M)]
    L.append(f"- 分离 HEAD worktree {len(det)} 个（无分支，人工判断）：{', '.join(os.path.basename(x) for x in det)}")
    L.append(f"- /tmp/pi-prfix 旧目录：{len(glob.glob('/tmp/pi-prfix/*'))}")
    txt = "\n".join(L) + "\n"
    if DRY: print(txt); print("ACTIONS", json.dumps(acts, ensure_ascii=False, indent=1)); return
    write_atomic(f"{BASE}/out/praise-pr/{d}.retro.md", txt); record_actions("praise-pr", d, acts)
if __name__ == "__main__": main()
