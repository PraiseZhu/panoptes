#!/usr/bin/env python3
"""运维 manual tools (Pi line). Default is dry-run everywhere; nothing outbound (issues off: --no-issues).
  opsfix.py backfill --from YYYY-MM-DD [--to YYYY-MM-DD] [--pr N ...] --reason "停摆补测" [--execute] [--rerun]
      lists merged PRs (CST days) lacking reports/merged/PR-N.json; --execute runs each through run-nightly.sh --pr N
      --write-merged --out /workspace/mivo-qa/reports/merged/PR-N.json (absolute), one at a time via the panoptes lock,
      skips if MemAvailable<3G; --rerun also re-tests PRs that have a report (old copy kept as history/PR-N.vK.json).
      Every run appends {pr, reason, cmd, rc} to reports/merged/manual-runs.jsonl.
      Alternative for yesterday+today: /workspace/mivo-qa/run-nightly.sh --catch-up --no-issues
  opsfix.py calibrate --pr N --match "<finding title substring>" --to P2|P3|误报 --reason "..." [--apply]
      backs up PR-N.json to PR-N.json.bak-<YYYYMMDD>-calibration, then sets the finding's severity and appends a
      calibration record (pr.calibrations[]). Then recomputes like the Grok line's manual calibration (PR-1145, 10-22):
      pr.severity, pr.verdict (only needs-attention/review-suggested/clean; others left + flagged), summary
      prs_by_severity/findings_by_severity, and when no unrejected code-review P0/P1 remains: code_review.status/gate
      → passed with gate.calibrated_from and verdict.status passed.
  opsfix.py retest [--day YYYY-MM-DD | --since ISO] [--pr N ...] [--execute] [--max-model 8] [--keep] [--wait-lock S] [--no-handoff]
      Fix-PR original-case retest; logic + write-back format in opsfix_retest.py. Default dry-run (plan only).
      Scope (10-23): fix PRs (body "QA: #N") merged after the last successful run — watermark + done/retry sets in
      out/ops/retest-state.json (seeded from manual-runs.jsonl on first use); --day = that data day only (watermark kept);
      --since overrides the watermark. Fix PRs whose targets stay blocked are retried (max 3 runs; model-cap blocks free).
      --execute: MemAvailable>=3G and panoptes nightly.lock (waits up to --wait-lock s, default 1200) — otherwise rc 75
      (retryable, nothing consumed). Runs ONLY each source finding's case at the fix PR head, writes
      reports/merged/PR-<fix>.json prs[0].retest[] and the latest ledger/daily/*.json (both .bak-<YYYYMMDD>-retest first;
      one row + several fix PRs: fail wins, else the latest `at`; results without a ledger row are logged and counted),
      then handoff issue close/reopen as outbound actions out/opsfix/<today>.actions.json (gh.issue.close /
      gh.issue.reopen; --no-handoff skips). Evidence in out/ops/retest/<today>/."""
import argparse, shutil
from lib import *
RUNS = f"{RPT}/manual-runs.jsonl"
def mem_ok(): return int(open("/proc/meminfo").read().split("MemAvailable:")[1].split()[0]) // 1024 >= 3072
def days(a, b):
    d = dt.date.fromisoformat(a); e = dt.date.fromisoformat(b or a)
    while d <= e: yield d.isoformat(); d += dt.timedelta(days=1)
def next_hist(n):
    ks = [int(re.search(r"\.v(\d+)\.json$", f).group(1)) for f in glob.glob(f"{RPT}/history/PR-{n}.v*.json")]
    return f"{RPT}/history/PR-{n}.v{max(ks, default=0) + 1}.json"
def backfill(a):
    todo = []
    for day in days(a.from_, a.to):
        for p in merged_prs(day):
            if a.pr and p["number"] not in a.pr: continue
            has = os.path.exists(f"{RPT}/PR-{p['number']}.json")
            if not has or a.rerun: todo.append((p["number"], day, has))
    print(f"{len(todo)} PR(s): " + " ".join(f"#{n}({d}{',有报告' if h else ''})" for n, d, h in todo))
    for n, day, has in todo:
        out = f"{RPT}/PR-{n}.json"
        cmd = [f"{QA}/run-nightly.sh", "--pr", str(n), "--date", day, "--write-merged", "--wait-lock", "14400",
               "--no-issues", "--trigger", "manual", "--out", out]
        if not a.execute: print("DRY", " ".join(cmd)); continue
        if not mem_ok(): print(f"skip #{n}: MemAvailable < 3G"); continue
        if has: h = next_hist(n); os.makedirs(os.path.dirname(h), exist_ok=True); shutil.copy2(out, h); print("kept", h)
        rc = subprocess.run(cmd, env={**os.environ, "QA_HOME": QA}).returncode
        open(RUNS, "a").write(json.dumps({"pr": n, "day": day, "reason": a.reason, "cmd": " ".join(cmd), "rc": rc,
                                          "at": now_cst().isoformat(timespec="seconds"), "by": "pi-line opsfix"}, ensure_ascii=False) + "\n")
SEV = ["P0", "P1", "P2", "P3"]
VERDICT = {"P1": "needs-attention", "P2": "review-suggested"}
def recompute(d, pr):
    """PR-level fields after a finding-level calibration; returns list of notes. Pure (mutates d only)."""
    notes = []; fs = pr.get("findings", [])
    live = [x for x in fs if not (x.get("calibration") or {}).get("to") == "误报"]
    sev = min((x.get("severity") for x in live if x.get("severity") in SEV), key=SEV.index, default="P3")
    old_s, old_v = pr.get("severity"), pr.get("verdict")
    pr["severity"] = sev
    if old_v in ("needs-attention", "review-suggested", "clean"):
        others = [x for x in live if x.get("source") != "code-review" and x.get("severity") in ("P0", "P1", "P2")]
        pr["verdict"] = VERDICT.get(sev) or ("clean" if not others and sev == "P3" else old_v)
    elif old_s != sev: notes.append(f"verdict {old_v!r} not auto-mapped; check by hand")
    cr = pr.get("code_review") or {}
    blocking = [x for x in fs if x.get("source") == "code-review" and x.get("severity") in ("P0", "P1")
                and x.get("verdict") != "REJECTED" and (x.get("calibration") or {}).get("to") != "误报"]
    g = cr.get("gate")
    if isinstance(g, dict) and g.get("status") == "failed" and not blocking:
        cr["gate"] = {**g, "status": "passed", "blockingFindings": 0, "problems": [p for p in g.get("problems", []) if p != "code-review-status-failed"],
                      "calibrated_from": {"status": g.get("status"), "blockingFindings": g.get("blockingFindings")}}
        if cr.get("status") == "failed": cr["status"] = "passed"
        if isinstance(cr.get("verdict"), dict): cr["verdict"]["status"] = "passed"
    sm = d.get("summary") or {}
    if "prs_by_severity" in sm:
        c = {k: 0 for k in SEV}; c["untested"] = sm["prs_by_severity"].get("untested", 0)
        for p in d.get("prs", []):
            if p.get("severity") in SEV: c[p["severity"]] += 1
        sm["prs_by_severity"] = c
    if "findings_by_severity" in sm:
        c = {k: 0 for k in SEV}
        for p in d.get("prs", []):
            for x in p.get("findings", []):
                if x.get("severity") in c: c[x["severity"]] += 1
        sm["findings_by_severity"] = c
    notes.append(f"PR {old_s}/{old_v} → {pr.get('severity')}/{pr.get('verdict')}; gate {((cr.get('gate') or {}).get('status'))}")
    return notes
def calibrate(a):
    f = f"{RPT}/PR-{a.pr}.json"; d = json.load(open(f))
    pr = next(p for p in d["prs"] if p.get("number") == a.pr)
    hits = [x for x in pr.get("findings", []) if a.match in (x.get("title") or "")]
    if len(hits) != 1: sys.exit(f"need exactly 1 finding matching {a.match!r}, got {len(hits)}")
    x = hits[0]; old = x.get("severity")
    print(f"#{a.pr} '{x.get('title')}' {old} → {a.to}")
    if not a.apply:
        import copy; d2 = copy.deepcopy(d); p2 = next(p for p in d2["prs"] if p.get("number") == a.pr)
        x2 = next(y for y in p2["findings"] if a.match in (y.get("title") or ""))
        x2["severity"] = "P3" if a.to == "误报" else a.to; x2["calibration"] = {"to": a.to}
        print("DRY (use --apply):", "; ".join(recompute(d2, p2))); return
    bak = f"{f}.bak-{now_cst():%Y%m%d}-calibration"
    if not os.path.exists(bak): shutil.copy2(f, bak); print("backup", bak)
    x["severity"] = "P3" if a.to == "误报" else a.to
    rec = {"finding": x.get("id") or x.get("title"), "from": old, "to": a.to, "reason": a.reason,
           "at": now_cst().isoformat(timespec="seconds"), "by": "Mivo QA 运维 (pi-line)"}
    x["calibration"] = rec; pr.setdefault("calibrations", []).append(rec)
    for note in recompute(d, pr): print(note)
    write_atomic(f, json.dumps(d, ensure_ascii=False, indent=2))
ap = argparse.ArgumentParser(); s = ap.add_subparsers(dest="cmd", required=True)
b = s.add_parser("backfill"); b.add_argument("--from", dest="from_", required=True); b.add_argument("--to")
b.add_argument("--pr", type=int, nargs="*"); b.add_argument("--reason", required=True)
b.add_argument("--execute", action="store_true"); b.add_argument("--rerun", action="store_true")
c = s.add_parser("calibrate"); c.add_argument("--pr", type=int, required=True); c.add_argument("--match", required=True)
c.add_argument("--to", required=True, choices=["P1", "P2", "P3", "误报"]); c.add_argument("--reason", required=True); c.add_argument("--apply", action="store_true")
r = s.add_parser("retest"); r.add_argument("--day"); r.add_argument("--pr", type=int, nargs="*")
r.add_argument("--execute", action="store_true"); r.add_argument("--max-model", type=int, default=8); r.add_argument("--keep", action="store_true")
r.add_argument("--since", help="ISO time; overrides the watermark in out/ops/retest-state.json")
r.add_argument("--wait-lock", type=int, default=1200, help="seconds to wait for the panoptes QA lock before rc 75")
r.add_argument("--no-handoff", action="store_true", help="do not record handoff issue close/reopen actions")
if __name__ == "__main__":
    a = ap.parse_args()
    if a.cmd == "retest":
        import opsfix_retest; sys.exit(opsfix_retest.run(a))
    backfill(a) if a.cmd == "backfill" else calibrate(a)
