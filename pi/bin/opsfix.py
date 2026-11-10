#!/usr/bin/env python3
"""运维 manual tools (Pi line). Default is dry-run everywhere; nothing outbound (issues off: --no-issues).
  opsfix.py backfill --from YYYY-MM-DD [--to YYYY-MM-DD] [--pr N ...] --reason "停摆补测" [--execute] [--rerun]
      lists merged PRs (CST days) lacking reports/merged/PR-N.json; --execute runs each through run-nightly.sh --pr N
      --write-merged --out /workspace/mivo-qa/reports/merged/PR-N.json (absolute), one at a time via the panoptes lock,
      skips if MemAvailable<3G; --rerun also re-tests PRs that have a report (old copy kept as history/PR-N.vK.json).
      Every run appends {pr, reason, cmd, rc} to reports/merged/manual-runs.jsonl.
      Alternative for yesterday+today: /workspace/mivo-qa/run-nightly.sh --catch-up --no-issues
  opsfix.py calibrate --pr N [--match "<finding title substring>"] [--fingerprint mq1-...] --to P2|P3|误报 --reason "..." [--apply]
      backs up PR-N.json to PR-N.json.bak-<YYYYMMDD>-calibration, then sets the finding's severity and appends a
      calibration record (pr.calibrations[]). --fingerprint selects a check finding with no title (exact mq1-…).
      --match and/or --fingerprint; both together AND. Then recomputes like the Grok line's manual calibration (PR-1145, 10-22):
      pr.severity, pr.verdict (needs-attention/review-suggested/clean, plus partially-verified when leftover is P3 but
      pending_verification is non-empty, a check is unavailable/timeout, dimensions are missing, or code_review is
      capped — same arms as nightly.verdictOf; other verdicts left + flagged), pr.test_backed / test_backed_severity
      / finding_sources (same as nightly.annotateTestBacked; 误报 findings are excluded from the PR-level aggregates),
      summary prs_by_severity/findings_by_severity/test_backed_findings_by_severity/prs_test_backed, and when no
      unrejected code-review P0/P1 remains: code_review.status/gate → passed with gate.calibrated_from and
      verdict.status passed. outcome, outcome_reason and pending_verification are left as they are (not derived
      from finding severity). --reports-dir reads/writes PR-N.json from that directory instead of production reports.
  opsfix.py retest [--day YYYY-MM-DD | --since ISO] [--pr N ...] [--execute] [--max-model 12] [--keep] [--wait-lock S] [--no-handoff]
      Fix-PR original-case retest; logic + write-back format in opsfix_retest.py. Default dry-run (plan only).
      Scope (10-23): fix PRs (body "QA: #N") merged after the last successful run — watermark + done/retry sets in
      out/ops/retest-state.json (seeded from manual-runs.jsonl on first use); --day = that data day only (watermark kept);
      --since overrides the watermark. Fix PRs whose targets stay blocked are retried (max 3 runs; model-cap blocks free).
      --execute: MemAvailable>=3G and panoptes nightly.lock (waits up to --wait-lock s, default 1200) — otherwise rc 75
      (retryable, nothing consumed). Runs ONLY each source finding's case at the fix PR head, writes
      reports/merged/PR-<fix>.json prs[0].retest[] and the latest ledger/daily/*.json (both .bak-<YYYYMMDD>-retest first;
      one row + several fix PRs: fail wins, else the latest `at`; results without a ledger row are logged and counted),
      then handoff issue close/reopen as outbound actions out/opsfix/<today>.actions.json (gh.issue.close /
      gh.issue.reopen; --no-handoff skips). Evidence in out/ops/retest/<today>/.
      Ledger = newest *complete* YYYY-MM-DD.json (hand-made / partial files are skipped and logged; none trustworthy -> rc 1,
      nothing written). Results without a row (incl. rows removed by a 不修/已修 ruling) -> ledger/retest-unmatched.jsonl.
  opsfix.py retest-replay --pr N ... [--yes] [--ledger F | --daily DIR] [--reports DIR] [--rulings F] [--unmatched-log F]
      Re-apply retest results already in reports/merged/PR-<fix>.json to the newest complete ledger (11-04 0-row write-back).
      Dry-run by default; --yes writes (.bak-<ts>-replay first); idempotent; unmatched -> ledger/retest-unmatched.jsonl."""
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
def e2e_browser_unavailable(pr):
    return any(c.get("id") == "e2e-browser" and c.get("status") == "unavailable" for c in pr.get("checks") or [])
# Mirrors argus/src/nightly.mjs:1686-1700 (TEST_BACKED_STATUSES / checkIsTestBacked / isTestBacked / annotateTestBacked).
# A failed-check finding is test-backed only for introduced / pre-existing / fixed-later (or a missing status on very
# old reports); flaky / unverified / timeout / setup-failed are not. Other sources keep the stored boolean.
TEST_BACKED_STATUSES = ("introduced", "pre-existing", "fixed-later")
def check_is_test_backed(c):
    return c.get("flaky") is not True and (c.get("status") is None or c.get("status") in TEST_BACKED_STATUSES)
def is_test_backed(f):
    if (f.get("source") or "check") == "check":
        return check_is_test_backed(f) and f.get("test_backed") is not False
    return f.get("test_backed") is True
def live_finding(x):
    return (x.get("calibration") or {}).get("to") != "误报"
def annotate_test_backed(pr):
    """Set finding.test_backed and PR-level test_backed / test_backed_severity / finding_sources.
    Same as nightly.annotateTestBacked, except findings calibrated to 误报 stay in findings[] but
    are excluded from the PR-level aggregates (the same live set recompute uses for severity)."""
    fs = pr.get("findings") or []
    for f in fs:
        if not f.get("source"): f["source"] = "check"
        f["test_backed"] = is_test_backed(f)
    backed = [f for f in fs if f.get("test_backed") and live_finding(f)]
    pr["test_backed"] = bool(backed)
    pr["test_backed_severity"] = min((f.get("severity") for f in backed if f.get("severity") in SEV), key=SEV.index, default=None)
    pr["finding_sources"] = list(dict.fromkeys(f.get("source") for f in fs if f.get("source")))
    return pr
def verdict_of(pr):
    """Mirrors nightly.verdictOf (nightly.mjs:1248-1258), ignoring findings calibrated to 误报."""
    if pr.get("outcome") == "infra_failed": return "qa-env-problem"
    live = [x for x in pr.get("findings") or [] if live_finding(x)]
    if any(x.get("severity") in ("P0", "P1") for x in live): return "needs-attention"
    if any(x.get("severity") == "P2" for x in live): return "review-suggested"
    if pr.get("pending_verification"): return "partially-verified"
    if any(c.get("status") in ("unavailable", "timeout") for c in pr.get("checks") or []): return "partially-verified"
    if pr.get("dimensions_missing"): return "partially-verified"
    if (pr.get("code_review") or {}).get("status") == "capped": return "partially-verified"
    return "clean"
def reports_merged(a=None):
    """calibrate input/output dir. --reports-dir wins; otherwise the production RPT from lib.py."""
    d = getattr(a, "reports_dir", None) if a is not None else None
    return d or RPT
def finding_hits(findings, match=None, fingerprint=None):
    """Select findings for calibrate: exact fingerprint and/or title substring. Either or both; both AND."""
    hits = list(findings or [])
    if fingerprint: hits = [x for x in hits if x.get("fingerprint") == fingerprint]
    if match: hits = [x for x in hits if match in (x.get("title") or "")]
    return hits
def recompute(d, pr):
    """PR-level fields after a finding-level calibration; returns list of notes. Pure (mutates d only).
    Recomputes severity, verdict, test_backed / test_backed_severity / finding_sources, and the
    summary counts that follow from them. Does not rewrite outcome, outcome_reason, or
    pending_verification: those are decided from check runs / QA-self-check status, not from
    finding severity (nightly.mjs:482-484, 610-626, 1248-1258)."""
    notes = []; fs = pr.get("findings", [])
    live = [x for x in fs if live_finding(x)]
    sev = min((x.get("severity") for x in live if x.get("severity") in SEV), key=SEV.index, default="P3")
    old_s, old_v = pr.get("severity"), pr.get("verdict")
    old_tbs, old_out = pr.get("test_backed_severity"), pr.get("outcome")
    old_pv = len(pr.get("pending_verification") or [])
    pr["severity"] = sev
    if old_v in ("needs-attention", "review-suggested", "clean"):
        pr["verdict"] = verdict_of(pr)
    elif old_s != sev: notes.append(f"verdict {old_v!r} not auto-mapped; check by hand")
    annotate_test_backed(pr)
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
    if "test_backed_findings_by_severity" in sm:
        c = {k: 0 for k in SEV}
        for p in d.get("prs", []):
            for x in p.get("findings", []):
                if x.get("test_backed") and live_finding(x) and x.get("severity") in c: c[x["severity"]] += 1
        sm["test_backed_findings_by_severity"] = c
    if "prs_test_backed" in sm:
        sm["prs_test_backed"] = [p.get("number") for p in d.get("prs", []) if p.get("test_backed")]
    notes.append(f"PR {old_s}/{old_v} → {pr.get('severity')}/{pr.get('verdict')}; gate {((cr.get('gate') or {}).get('status'))}")
    notes.append(f"test_backed_severity {old_tbs} → {pr.get('test_backed_severity')}")
    notes.append(f"outcome {old_out} → {pr.get('outcome')} (not recomputed)")
    notes.append(f"pending_verification {old_pv} → {len(pr.get('pending_verification') or [])} (not recomputed)")
    return notes
def calibrate(a):
    match, fp = getattr(a, "match", None) or None, getattr(a, "fingerprint", None) or None
    if not match and not fp: sys.exit("need --match and/or --fingerprint")
    f = f"{reports_merged(a)}/PR-{a.pr}.json"; d = json.load(open(f))
    pr = next(p for p in d["prs"] if p.get("number") == a.pr)
    hits = finding_hits(pr.get("findings", []), match, fp)
    label = " ".join(s for s in (repr(match) if match else "", f"fingerprint={fp}" if fp else "") if s)
    if len(hits) != 1: sys.exit(f"need exactly 1 finding matching {label}, got {len(hits)}")
    x = hits[0]; old = x.get("severity")
    print(f"#{a.pr} '{x.get('title') or x.get('fingerprint')}' {old} → {a.to}")
    if not a.apply:
        import copy; d2 = copy.deepcopy(d); p2 = next(p for p in d2["prs"] if p.get("number") == a.pr)
        x2 = finding_hits(p2.get("findings", []), match, fp)[0]
        x2["severity"] = "P3" if a.to == "误报" else a.to; x2["calibration"] = {"to": a.to}
        print("DRY (use --apply):", "; ".join(recompute(d2, p2))); return
    bak = f"{f}.bak-{now_cst():%Y%m%d}-calibration"
    if not os.path.exists(bak): shutil.copy2(f, bak); print("backup", bak)
    x["severity"] = "P3" if a.to == "误报" else a.to
    rec = {"finding": x.get("id") or x.get("title") or x.get("fingerprint"), "from": old, "to": a.to, "reason": a.reason,
           "at": now_cst().isoformat(timespec="seconds"), "by": "Mivo QA 运维 (pi-line)"}
    x["calibration"] = rec; pr.setdefault("calibrations", []).append(rec)
    for note in recompute(d, pr): print(note)
    write_atomic(f, json.dumps(d, ensure_ascii=False, indent=2))
ap = argparse.ArgumentParser(); s = ap.add_subparsers(dest="cmd", required=True)
b = s.add_parser("backfill"); b.add_argument("--from", dest="from_", required=True); b.add_argument("--to")
b.add_argument("--pr", type=int, nargs="*"); b.add_argument("--reason", required=True)
b.add_argument("--execute", action="store_true"); b.add_argument("--rerun", action="store_true")
c = s.add_parser("calibrate"); c.add_argument("--pr", type=int, required=True)
c.add_argument("--match", help="finding title substring")
c.add_argument("--fingerprint", help="finding fingerprint (mq1-...), for check findings with no title")
c.add_argument("--to", required=True, choices=["P1", "P2", "P3", "误报"]); c.add_argument("--reason", required=True); c.add_argument("--apply", action="store_true")
c.add_argument("--reports-dir", help="directory of PR-N.json (default: production reports/merged); use this to DRY a copy")
r = s.add_parser("retest"); r.add_argument("--day"); r.add_argument("--pr", type=int, nargs="*")
r.add_argument("--execute", action="store_true"); r.add_argument("--max-model", type=int, default=12); r.add_argument("--keep", action="store_true")
r.add_argument("--since", help="ISO time; overrides the watermark in out/ops/retest-state.json")
r.add_argument("--wait-lock", type=int, default=1200, help="seconds to wait for the panoptes QA lock before rc 75")
r.add_argument("--no-handoff", action="store_true", help="do not record handoff issue close/reopen actions")
rr = s.add_parser("retest-replay", help="re-apply recorded retest results (reports/merged/PR-<fix>.json) to the right ledger; dry-run unless --yes")
rr.add_argument("--pr", type=int, nargs="+", required=True); rr.add_argument("--yes", action="store_true")
rr.add_argument("--ledger", help="explicit ledger file (default: newest complete ledger/daily/YYYY-MM-DD.json)")
rr.add_argument("--daily"); rr.add_argument("--reports"); rr.add_argument("--rulings"); rr.add_argument("--unmatched-log")
if __name__ == "__main__":
    a = ap.parse_args()
    if a.cmd == "retest":
        import opsfix_retest; sys.exit(opsfix_retest.run(a))
    if a.cmd == "retest-replay":
        import opsfix_retest; sys.exit(opsfix_retest.replay(a))
    backfill(a) if a.cmd == "backfill" else calibrate(a)
