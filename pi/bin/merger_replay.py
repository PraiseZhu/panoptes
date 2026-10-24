#!/usr/bin/env python3
"""Replay merger.judge() (gate_port = gate.mjs port + Pi extras) against every Grok-line judgment:
A) audit.log text judgments (10-16), B) all 877 rounds in state/audit.jsonl, exact verdict+reason (+settleFrom/path).
Snapshots start from the real testkit fixture (all-pass PR), time-shifted to the round's timestamp; real recorded
fields (at, settleFrom, path, mergeStateStatus, mode) are used wherever the log has them. Offline, read-only."""
import re, json, copy, collections, datetime as dt, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_port as G, merger as M
CST = dt.timezone(dt.timedelta(hours=8)); MM = "/workspace/mivo-merger"
FX = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test", "fixture.json"))); NOW0 = FX["NOW"]
TK = {"now", "prereviewHeartbeat", "skillShaUpdatedAt", "activationAt", "threadsStableSince", "mergeBaseDate"}
def shift(o, d):
    if isinstance(o, dict): return {k: (v + d if isinstance(v, (int, float)) and not isinstance(v, bool) and (k in TK or k.endswith("At")) else shift(v, d)) for k, v in o.items()}
    if isinstance(o, list): return [shift(x, d) for x in o]
    return o
def base(at, mode="shadow"):
    g, pr = shift(copy.deepcopy(FX["g"]), at - NOW0), shift(copy.deepcopy(FX["pr"]), at - NOW0)
    g["mode"] = mode; g["activationAt"] = None if mode == "shadow" else g["activationAt"]
    return g, pr
def cap_times(pr, sf):
    for c in pr["checkRuns"]: c["completedAt"] = min(c["completedAt"], sf) if c.get("completedAt") is not None else None
    for r in pr["reviews"]: r["submittedAt"] = min(r["submittedAt"], sf)
    for k in ("readyLabeledAt", "readyForReviewAt"): pr["timeline"][k] = min(pr["timeline"][k], sf)
    pr["local"]["threadsStableSince"] = sf
def decide(g, pr):
    """round.mjs order: global gate -> partial (not CLEAN) -> evaluate."""
    gl = G.evaluate_global(g, M.P)
    if not gl["ok"]: return gl["verdict"], gl["reasons"][0], None, {}
    if pr.get("mergeStateStatus") != "CLEAN": return "WAIT", "not-clean", None, {}
    v, r, ev = M.judge(g, pr); return v, r, ev.get("settleFrom"), ev.get("evidence") or {}
out = ["# merger.py（gate.mjs 移植）与 Grok 线判定回放", ""]
# ---- A ----
A = collections.Counter(); rowsA = []
for line in open(f"{MM}/audit.log"):
    if "would-merge" not in line: continue
    ts = line.split(" ", 1)[0]; at = int(dt.datetime.fromisoformat(ts).timestamp() * 1000)
    m = re.search(r"dry-run #(\d+) would-merge=yes", line)
    if m: wm, blocked, opn = {int(m[1])}, {}, [int(m[1])]
    else:
        opn = [int(x) for x in re.findall(r"\d+", re.search(r"merge-ready open: ([\d,]+)", line)[1])]
        w = re.search(r"would-merge: ([^;(]+)", line)[1].strip()
        wm = set(opn) if w.startswith("all") else {int(x) for x in re.findall(r"\d+", w)}
        if "those" in w:
            seg = re.search(r";\s*([\d,]+) all 8 required|behind main<=\d+ on ([\d,]+)", line)
            wm = {int(x) for x in re.findall(r"\d+", (seg[1] or seg[2]))}
        blocked = {}
        for b in re.finditer(r"(\d{3,4}) ?\(([^)]*)\)", (re.search(r"(blocked|not mergeable): (.*)", line) or [None, None, ""])[2]):
            if int(b[1]) in opn: blocked[int(b[1])] = b[2]
        for n in opn:
            if n not in wm and n not in blocked: blocked[n] = "（行内未写原因）"
    for n in sorted(wm | set(blocked)):
        g, pr = base(at, "on"); r = blocked.get(n, "")
        pr["number"] = n; pr["reviewRun"]["displayTitle"] = f"review-pr #{n} x"; pr["reviewResult"]["pr"] = n
        if "review-pr" in r:
            for c in pr["checkRuns"]:
                if c["name"] == "review-pr": c["status"] = "in_progress"
        if "draft" in r: pr["isDraft"] = True
        v, why, _, _ = decide(g, pr); ok = (v == "MERGE") == (n in wm); A[ok] += 1
        rowsA.append(f"| {ts[:16].replace('T', ' ')} | #{n} | {'MERGE' if n in wm else '非MERGE (' + r[:36] + ')'} | {v}:{why} | {'✅' if ok else '❌'} |")
out += ["## A. audit.log 判定行（10-16）", "", f"**一致 {A[True]} / {A[True] + A[False]}**", "", "| 时间(北京) | PR | Grok | merger.py | 一致 |", "|---|---|---|---|---|"] + rowsA + [""]
# ---- B ----
B = collections.Counter(); ex = collections.defaultdict(list); diffs = []
last_sample = None; nowsrc = collections.Counter()
for d in (json.loads(l) for l in open(f"{MM}/state/audit.jsonl")):
    if d["kind"] == "platform-sample": last_sample = d["at"]; continue
    if d["kind"] != "round": continue
    # g.now of the round = the platform samples written in the same round (at: g.now, GitHub Date header);
    # the round line's own `at` is written at round end. Fall back to round `at` when the round had no samples.
    at = last_sample if last_sample is not None and 0 <= d["at"] - last_sample < 5 * 60000 else d["at"]
    nowsrc["sample" if at != d["at"] else "round-at"] += 1; last_sample = None
    t = dt.datetime.fromtimestamp(at / 1000, CST).strftime("%m-%d %H:%M"); mode = d.get("mode", "shadow")
    gl = d.get("global")
    if gl:
        g, pr = base(at, mode)
        if gl["reason"] == "skill-sha-quiet": g["skillShaUpdatedAt"] = at - 30 * G.MIN
        if gl["reason"] == "main-not-green": g["mainCi"] = {"status": "in_progress"}
        v, r, _, _ = decide(g, pr); key = f"全局 {gl['verdict']}/{gl['reason']}"
        ok = (v, r) == (gl["verdict"], gl["reason"])
        B[(key, ok)] += 1; ex[(key, ok)].append(t) if len(ex[(key, ok)]) < 4 else None
        if not ok: diffs.append((t, "global", gl, (v, r)))
        continue
    # no global verdict: the real timestamp must NOT be in the maintenance window
    g0, _ = base(at, mode); mw = G.is_maintenance(at, M.P["maintenanceWindow"])
    B[("无全局门的轮（真实时间不在维护窗口）", not mw)] += 1
    if mw: diffs.append((t, "maintenance-window expected", None, None))
    for x in d["decisions"]:
        g, pr = base(at, mode); n = x["pr"]; gv, gr = x["verdict"], x["reason"]; e = x.get("evidence") or {}
        pr["number"] = n; pr["headSha"] = x["head"]; pr["reviewRun"]["displayTitle"] = f"review-pr #{n} x"
        pr["reviewResult"].update(pr=n, head=x["head"]); pr["reviewResult"]["approval"]["head"] = x["head"]
        for r_ in pr["reviews"]: r_["commitId"] = x["head"]
        sf = x.get("settleFrom")
        if sf is not None: cap_times(pr, sf)
        if gr == "need-artifact": del pr["reviewResult"]
        elif gr == "human-only-path": pr["files"] = [{"filename": e["path"], "previousFilename": None, "status": "modified"}]
        elif gr == "not-clean": pr["mergeStateStatus"] = x.get("mergeStateStatus")
        elif gr == "review-not-clean": pr["reviewResult"]["reviewClean"] = False
        elif gr == "review-head-mismatch": pr["reviews"] = []
        elif gr == "review-provenance":
            if "reviewCheckApp" in e: pr["checkRuns"] = [dict(c, appId=e["reviewCheckApp"]) if c["name"] == "review-pr" else c for c in pr["checkRuns"]]
            elif "externalId" in e: pr["checkRuns"] = [dict(c, externalId=e["externalId"]) if c["name"] == "review-pr" else c for c in pr["checkRuns"]]
            elif "resultPr" in e: pr["reviewResult"].update(pr=e["resultPr"], head=e.get("resultHead"))
            else: pr["reviewRun"]["triggeringActor"] = "unknown"   # reviewRunId evidence: run attributes did not match
        v, r, msf, mev = decide(g, pr)
        ok = (v, r) == (gv, gr) and (sf is None or msf == sf) and (gr != "human-only-path" or (mev.get("pattern") or "").replace("\\/", "/") == (e.get("pattern") or "").replace("\\/", "/"))
        key = f"{gv}/{gr}"; B[(key, ok)] += 1
        if len(ex[(key, ok)]) < 4: ex[(key, ok)].append(f"{t} #{n}")
        if not ok: diffs.append((t, n, (gv, gr, sf), (v, r, msf)))
tot = sum(B.values()); bad = sum(c for (k, ok), c in B.items() if not ok)
out += [f"g.now 取值：同轮平台采样时间 {nowsrc['sample']} 轮，轮记录时间 {nowsrc['round-at']} 轮（该轮无采样）", ""]
out += ["## B. state/audit.jsonl 877 轮（全部判定，逐条比 verdict+reason，并比 settleFrom / 命中规则）", "", f"**比对 {tot} 项，不一致 {bad}**", "",
        "| Grok 判定/原因 | 次数 | 结果 | 样例(北京) |", "|---|---|---|---|"]
for (k, ok), c in sorted(B.items(), key=lambda kv: -kv[1]): out.append(f"| {k} | {c} | {'一致' if ok else '❌不一致'} | {', '.join(ex[(k, ok)])} |")
if diffs: out += ["", "### 不一致明细"] + [f"- {x}" for x in diffs[:30]]
open(os.path.join(os.environ.get("PI_STATE") or "/workspace/mivo-qa/pi", "out", "merger", "replay-2026-10-23.md"), "w").write("\n".join(out) + "\n")
if __name__ == "__main__":
    print(f"A {A[True]}/{A[True] + A[False]}; B items={tot} diffs={bad}; now-source={dict(nowsrc)}")
    for x in diffs[:10]: print(" ", x)
