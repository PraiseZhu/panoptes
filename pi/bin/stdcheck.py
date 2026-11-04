#!/usr/bin/env python3
"""stdcheck -- hourly fleet-standards checker (zero model). Gate: $PI_STATE/stdcheck-enabled (absent = no-op, exit 0).
Rollback: rm $PI_STATE/stdcheck-enabled (re-read every run; the cron line stays and does nothing).

Checks (each one: a list of objects that breach, from files on disk plus two gh reads via lib.gh):
  first_run_fail  metric only (no threshold / alert / gap): of the qa_start merges, how many had a failed first run that was rerun
               (report run.attempt > 1, or mergewatch "try 2/3"); in the log, state.json and passrate-<day>.json "metrics"
  qa_start     a merge into main / integration/* / release/* has its QA run started within 10 min (reports/merged/PR-N.json
               prs[].merged_at vs run.started_at; mergewatch-<day>.log "launched priority run ... for PR #N" / "branch merge #N"
               for merges without a report yet -- same sources as pi-work/gaps-1105/LATENCY.md)
  panoptes_prs open PraiseZhu/panoptes PRs <= 3 (lib.gh: Praise QA App identity while gh-app-enabled, PAT otherwise)
  fix_prs      OPEN Pi-line fix PRs in xindong/mivo-canvas-plugin, drafts included (Jasper), (prwatch.PILINE marker or qa-fix/* / fix/qa-* head) <= 2
  tasks        pmnudge (Beijing 09-22 :05), daily (07:35, retry 08:35) and ledger (23:40) ran clean: run.sh records
               (watchdog.runs / ok_run), plus daily's outbound ledger key mivo-daily-report:<day> done and out/ledger/<day>.json
  review_pr    the Mac mini review-pr dispatcher is moving (Jasper ruling, 11-04): product repo via lib.gh (App identity, REST, at most
               REVIEW_MAX_PRS ready PRs). Ready open PRs waiting on review-pr (a review-pr / "review-pr (integration)" check run or status
               queued / pending / missing) while no review-pr check or status on any of them started or completed in the last 60 min
               -> stalled; a review-pr check in_progress > 60 min -> stalled; nothing waiting -> ok. Gap owner Jasper. No file is read.

Breach -> gaps-open.json (fleet-standards list, $PI_GAPS_FILE overrides; jsonrec lock) entry id stdcheck:<check>:<object>, with owner
and deadline, upserted (re-breach keeps opened/deadline, bumps last_seen); a check that ran clean closes its stdcheck entries that no
longer breach. An entry still open past its deadline -> one escalation record (out/stdcheck/escalations.json) for Jasper's 09:00 brief.
A check that errors is retried (ATTEMPTS, RETRY_SLEEP_S); a failed task run is re-run once via run.sh. A check failing (error or breach)
2 runs in a row -> out/stdcheck/alerts/<check>.json (removed on recovery) + brief. Crash of this script -> alerts/stdcheck.json, rc 1.
Daily pass rate: out/stdcheck/passrate-<Beijing day>.json. Jasper's 09:00 brief reads brief_facts(). --dry-run: print, write nothing, re-run nothing.
"""
import sys, time
from lib import *
import jsonrec

DRY = "--dry-run" in sys.argv
OUT = f"{BASE}/out/stdcheck"
FLAG = f"{BASE}/stdcheck-enabled"
GAPS = os.environ.get("PI_GAPS_FILE") or "/workspace/fleet-standards/gaps-open.json"
REPORTS = os.environ.get("PI_REPORTS_MERGED") or RPT
RUN_SH = f"{PI_CODE}/bin/run.sh"
PANOPTES = "PraiseZhu/panoptes"
QA_START_MAX_MIN = 10; LOOKBACK_H = 24; MAX_PANOPTES_PRS = 3; REVIEW_STALL_MIN = 60; REVIEW_MAX_PRS = 15
ATTEMPTS = 3; RETRY_SLEEP_S = 20; ALERT_STREAK = 2
TASKS = {  # job: (Beijing slots, judge this many minutes after the slot)
    "pmnudge": ([(h, 5) for h in range(9, 23)], 20), "daily": ([(7, 35)], 85), "ledger": ([(23, 40)], 20)}
CHECKS = ("qa_start", "panoptes_prs", "fix_prs", "tasks", "review_pr")
OWNER = {"qa_start": "Mivo QA 运维", "panoptes_prs": "Praise", "fix_prs": "Praise PR", "tasks": "Pi 线运维", "review_pr": "Jasper"}
DEADLINE_H = {"qa_start": 4, "panoptes_prs": 24, "fix_prs": 4, "tasks": 4, "review_pr": 2}
TZ_ABBR = {"CST": 8, "UTC": 0, "GMT": 0, "PST": -8, "PDT": -7, "JST": 9}
MW_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (\w+) (.*)$")
MW_LAUNCH = re.compile(r"launched priority run pid \d+ for PR #(\d+)(?:.*?try (\d+)/\d+)?")
MW_SEEN = re.compile(r"branch merge #(\d+)(?: into (\S+))?")
BASE_RX = re.compile(r"^(main|integration/.+|release/.+)$")   # merges that count for qa_start; unknown base = counted (main launches name none)


def jload(f, d):
    try: return json.load(open(f))
    except Exception: return d
def iso(t): return t.astimezone(CST).isoformat(timespec="seconds")
def save_json(path, doc):
    if DRY: print("DRY: would write", path); return
    write_atomic(path, json.dumps(doc, ensure_ascii=False, indent=1) + "\n")


# ---------------- checks: each returns {object: detail} of breaches (empty = pass); raising = error ----------------
def mergewatch_events(now):
    """{PR: {"seen": first mention, "launched": first launch}} from the last two mergewatch-<day>.log files"""
    ev = {}
    for f in sorted(glob.glob(f"{BASE}/out/logs/mergewatch-*.log"))[-2:]:
        for l in open(f, errors="replace"):
            m = MW_LINE.match(l.strip())
            if not m: continue
            off = TZ_ABBR.get(m[2])
            if off is None: continue
            t = dt.datetime.fromisoformat(m[1]).replace(tzinfo=dt.timezone(dt.timedelta(hours=off)))
            if now - t > dt.timedelta(hours=LOOKBACK_H): continue
            for rx, k in ((MW_LAUNCH, "launched"), (MW_SEEN, "seen")):
                mm = rx.search(m[3])
                if mm:
                    e = ev.setdefault(int(mm[1]), {}); e.setdefault(k, t); e.setdefault("seen", t)
                    if k == "launched": e["launches"] = max(e.get("launches", 0) + 1, int(mm[2] or 1))
                    if k == "seen" and mm[2] and mm[2] != "None": e.setdefault("base", mm[2])
    return ev

def report_times(n):
    """(merged_at, qa_started_at, base_ref) of reports/merged/PR-N.json, or None when there is no report"""
    d = jload(f"{REPORTS}/PR-{n}.json", None)
    if not isinstance(d, dict): return None
    pr = next((p for p in d.get("prs", []) if p.get("number") == n), {}) or {}
    run = d.get("run") or {}
    # 11-05: qa_start = the FIRST run's start (run.first_started_at, kept across reruns by nightly); old reports: started_at
    start = run.get("first_started_at") or run.get("started_at") or pr.get("test_started_at")
    return parse_ts(pr.get("merged_at")), parse_ts(start), pr.get("base_ref"), report_first_run_failed(run)

def report_first_run_failed(run):
    """True when the report's final run is not the first attempt (run.attempt > 1, or first_started_at earlier than started_at)"""
    if (run.get("attempt") or 1) > 1: return True
    f, s = parse_ts(run.get("first_started_at")), parse_ts(run.get("started_at"))
    return bool(f and s and s - f > dt.timedelta(seconds=60))

def qa_start_rows(now):
    """[(n, merged_or_seen, first QA start or None, first_run_failed)] for the merges qa_start counts. Start = the earliest of the
    report's first start and mergewatch's first launch (a first run that died before writing a report leaves only the launch)."""
    rows, ev = [], mergewatch_events(now)
    nums = set(ev)
    for f in glob.glob(f"{REPORTS}/PR-*.json"):
        m = re.search(r"PR-(\d+)\.json$", f)
        if m and now.timestamp() - os.path.getmtime(f) < (LOOKBACK_H + 2) * 3600: nums.add(int(m[1]))
    for n in sorted(nums):
        rt, e = report_times(n), ev.get(n, {})
        base = (rt[2] if rt else None) or e.get("base")
        if base and not BASE_RX.match(base): continue   # 11-04 acceptance #2: only main / integration/* / release/*
        merged = rt[0] if rt and rt[0] else None
        if merged and now - merged > dt.timedelta(hours=LOOKBACK_H): continue
        start = min([t for t in ((rt[1] if rt else None), e.get("launched")) if t], default=None)
        ref = merged or e.get("seen")
        if not ref: continue
        rows.append((n, ref, start, bool((rt and rt[3]) or e.get("launches", 0) > 1)))
    return rows

def check_qa_start(now):
    bad, lim = {}, dt.timedelta(minutes=QA_START_MAX_MIN)
    for n, ref, start, _ in qa_start_rows(now):
        if start and start - ref > lim: bad[f"PR-{n}"] = f"merge {cst(ref)} -> QA start {cst(start)} ({int((start - ref).total_seconds() // 60)} min > {QA_START_MAX_MIN})"
        elif not start and now - ref > lim: bad[f"PR-{n}"] = f"merged/seen {cst(ref)}, no QA start after {int((now - ref).total_seconds() // 60)} min"
    return bad

def first_run_fail(now):
    """11-05 metric (count only, no threshold, no alert): share of the qa_start merges whose first QA run failed and was rerun"""
    rows = [r for r in qa_start_rows(now) if r[2]]
    failed = [f"PR-{n}" for n, _, _, f in rows if f]
    return {"merges": len(rows), "failed": len(failed), "rate": round(len(failed) / len(rows), 3) if rows else None, "prs": failed}

def check_panoptes_prs(now):
    prs = gh(["pr", "list", "-R", PANOPTES, "--state", "open", "--limit", "100", "--json", "number"])
    return {"open-prs": f"{len(prs)} open panoptes PRs > {MAX_PANOPTES_PRS}: " + " ".join(f"#{p['number']}" for p in prs)} if len(prs) > MAX_PANOPTES_PRS else {}

PILINE = FIX_PR_MARK   # 11-05: is_fix_pr / MAX_FIX_PRS come from lib (shared with prwatch and outbound)

def check_fix_prs(now):
    """Jasper ruling (11-04): OPEN Pi-line fix PRs in xindong/mivo-canvas-plugin, drafts included, any author; breach = count > 2"""
    prs = open_fix_prs(gh)
    return {"concurrent": f"{len(prs)} Pi-line fix PRs open > {MAX_FIX_PRS}: " + " ".join(f"#{p['number']}" for p in prs)} if len(prs) > MAX_FIX_PRS else {}

def last_slot(slots, judge_min, now):
    """latest slot whose judge time (slot + judge_min) has passed, as a CST datetime"""
    c = []
    for h, mi in slots:
        s = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        while s + dt.timedelta(minutes=judge_min) > now: s -= dt.timedelta(days=1)
        c.append(s)
    return max(c)

def task_runs(job):
    import watchdog
    return watchdog.runs(job), watchdog.ok_run

def check_tasks(now, retry=None):
    bad = {}
    for job, (slots, judge) in TASKS.items():
        slot = last_slot(slots, judge, now); obj = f"{job}:{slot.strftime('%Y-%m-%d %H:%M')}"
        rs, ok = task_runs(job)
        after = [r for r in rs if r[0] >= slot - dt.timedelta(minutes=2)]
        if not after: bad[obj] = "run missing (no run.sh record after the slot)"
        elif not any(ok(r) for r in after):
            r = after[-1]; bad[obj] = f"run failed ({r[3] or 'rc=' + str(r[2])})"
            if retry: retry(job, obj)
        elif job == "daily":
            day = (slot.date() - dt.timedelta(days=1)).isoformat()
            st = (jload(f"{BASE}/out/outbound/ledger.json", {}).get(f"mivo-daily-report:{day}") or {}).get("status")
            if st not in ("done", "skipped"): bad[obj] = f"outbound ledger mivo-daily-report:{day} status={st}"
        elif job == "ledger" and not os.path.exists(f"{BASE}/out/ledger/{slot.date().isoformat()}.json"):
            bad[obj] = f"out/ledger/{slot.date().isoformat()}.json missing after a clean run"
    return bad

REVIEW_RX = re.compile(r"^review-pr(?: \(integration\))?$")
WAITING = ("queued", "pending", "requested", "waiting")
def review_state(sha):
    """review-pr entries of one head: [(kind, state, started, finished)] from REST check-runs + combined status (2 calls)"""
    out = []
    for c in (gh(["api", f"repos/{REPO}/commits/{sha}/check-runs?per_page=100"]) or {}).get("check_runs", []):
        if REVIEW_RX.match(c.get("name") or ""):
            out.append(("check", c.get("status"), parse_ts(c.get("started_at")), parse_ts(c.get("completed_at"))))
    for x in (gh(["api", f"repos/{REPO}/commits/{sha}/status?per_page=100"]) or {}).get("statuses", []):
        if REVIEW_RX.match(x.get("context") or ""):
            st = x.get("state"); t = parse_ts(x.get("updated_at") or x.get("created_at"))
            out.append(("status", st, None, None if st == "pending" else t))   # a pending status is a queue entry, not activity
    return out

def check_review_pr(now):
    """Jasper (11-04): is the Mac mini review-pr dispatcher moving? REST only, at most REVIEW_MAX_PRS ready PRs (1 + 2 per PR calls)"""
    prs = gh(["api", f"repos/{REPO}/pulls?state=open&sort=updated&direction=desc&per_page=50"]) or []
    ready = [p for p in prs if not p.get("draft")][:REVIEW_MAX_PRS]
    bad, waiting, last = {}, [], None
    lim = dt.timedelta(minutes=REVIEW_STALL_MIN)
    for p in ready:
        n, es = p["number"], review_state(p["head"]["sha"])
        if not es or any(st in WAITING for _, st, _, _ in es): waiting.append(n)
        for kind, st, started, finished in es:
            for t in (started, finished):
                if t and (last is None or t > last): last = t
            if kind == "check" and st == "in_progress" and started and now - started > lim:
                bad[f"running:PR-{n}"] = f"review-pr on #{n} in_progress since {cst(started)} ({int((now - started).total_seconds() // 60)} min > {REVIEW_STALL_MIN})"
    if waiting and (last is None or now - last > lim):
        bad["queue"] = (f"{len(waiting)} ready PRs waiting on review-pr ({' '.join(f'#{n}' for n in waiting)}), last review-pr activity "
                        f"{cst(last) if last else 'none seen'} (> {REVIEW_STALL_MIN} min)")
    return bad


# ---------------- retries, gaps, alerts, escalations, pass rate ----------------
def run_check(name, fn, now, **kw):
    """-> (breaches, None) or (None, error) after ATTEMPTS tries; a rate limit is not retried (it would be refused again)"""
    err = None
    for i in range(ATTEMPTS):
        try: return fn(now, **kw), None
        except GhRateLimited as e: return None, f"GitHub rate-limited: {str(e)[:200]}"
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:200]}"; log(f"stdcheck {name}: attempt {i + 1}/{ATTEMPTS} failed: {err}")
            if i + 1 < ATTEMPTS: time.sleep(RETRY_SLEEP_S)
    return None, err

def retry_task(st, now):
    def go(job, obj):
        done = st.setdefault("task_retries", {})
        if obj in done: return
        done[obj] = iso(now)
        if DRY: print(f"DRY: would re-run {job}"); return
        try:
            p = subprocess.Popen(["bash", RUN_SH, job], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            log(f"stdcheck: {obj} failed -> re-run {job} (pid {p.pid})")
        except Exception as e: log(f"stdcheck: re-run {job} failed to start: {e}"); done[obj] = f"spawn failed: {e}"
    return go

def gap_id(check, obj): return f"stdcheck:{check}:{obj}"

def sync_gaps(results, now):
    """results {check: breaches|None(error)}. Upsert breaches, close clean ones, mark + return the newly overdue."""
    if DRY: print("DRY: gaps", json.dumps({c: r for c, r in results.items()}, ensure_ascii=False)); return []
    overdue = []
    def fn(doc):
        by = {g.get("id"): g for g in doc if isinstance(g, dict)}
        for c, br in results.items():
            if br is None: continue
            for obj, why in br.items():
                g = by.get(gap_id(c, obj))
                if g and g.get("status") == "open": g["last_seen"] = iso(now); g["item"] = why; continue
                g = {"id": gap_id(c, obj), "source": "stdcheck", "check": c, "object": obj, "opened": iso(now), "bot": "Pi stdcheck",
                     "item": why, "owner": OWNER[c], "deadline": iso(now + dt.timedelta(hours=DEADLINE_H[c])),
                     "evidence_needed": f"stdcheck {c} passes for {obj}", "last_chased": None, "last_seen": iso(now), "status": "open"}
                if gap_id(c, obj) in by: by[gap_id(c, obj)].update(g)
                else: doc.append(g); by[g["id"]] = g
            for g in doc:
                if g.get("source") == "stdcheck" and g.get("check") == c and g.get("status") == "open" and g.get("object") not in br:
                    g.update(status="closed", closed=iso(now), closed_evidence=f"stdcheck {iso(now)}: {c} passes for {g.get('object')}")
        for g in doc:
            if g.get("source") == "stdcheck" and g.get("status") == "open" and not g.get("escalated"):
                dl = parse_ts(g.get("deadline"))
                if dl and now > dl: g["escalated"] = iso(now); overdue.append(dict(g))
        return True
    jsonrec.update(GAPS, fn, default=[])
    return overdue

def brief_day(now):
    """the 09:00 Jasper brief that will carry a record made now"""
    d = now.date()
    return (d if now.hour < 9 else d + dt.timedelta(days=1)).isoformat()

def escalate(overdue, now):
    if not overdue or DRY: return
    def fn(doc):
        for g in overdue:
            doc.setdefault(g["id"], {"id": g["id"], "check": g["check"], "object": g["object"], "owner": g["owner"], "item": g["item"],
                                     "opened": g["opened"], "deadline": g["deadline"], "escalated_at": iso(now), "for_brief": brief_day(now),
                                     "to": "Jasper"})
        return True
    os.makedirs(OUT, exist_ok=True); jsonrec.update(f"{OUT}/escalations.json", fn, default={})
    for g in overdue: log(f"stdcheck: ESCALATED {g['id']} (deadline {g['deadline']}, owner {g['owner']})")

def update_alerts(st, results, errors, now):
    streak = st.setdefault("streak", {})
    for c in list(results):
        failed = results[c] is None or bool(results[c])
        streak[c] = streak.get(c, 0) + 1 if failed else 0
        f = f"{OUT}/alerts/{c}.json"
        if streak[c] >= ALERT_STREAK:
            prev = jload(f, {})
            save_json(f, {"check": c, "consecutive_failures": streak[c], "since": prev.get("since") or iso(now), "last": iso(now),
                          "error": errors.get(c), "breaches": results[c] or {}, "owner": OWNER.get(c, "Pi 线运维"), "for_brief": brief_day(now)})
            log(f"stdcheck: ALERT {c} failed {streak[c]} runs in a row ({errors.get(c) or '; '.join((results[c] or {}).values())[:200]})")
        elif os.path.exists(f) and not DRY: os.unlink(f); log(f"stdcheck: alert {c} recovered")

def record_passrate(results, now, metrics=None):
    if DRY: return
    day = now.date().isoformat(); os.makedirs(OUT, exist_ok=True)
    def fn(doc):
        doc.setdefault("day", day); ck = doc.setdefault("checks", {})
        for c, br in results.items():
            e = ck.setdefault(c, {"pass": 0, "fail": 0, "error": 0})
            e["error" if br is None else "fail" if br else "pass"] += 1
            e["rate"] = round(e["pass"] / (e["pass"] + e["fail"] + e["error"]), 3)
        tot = [sum(e[k] for e in ck.values()) for k in ("pass", "fail", "error")]
        doc.update(runs=doc.get("runs", 0) + 1, updated=iso(now), overall=round(tot[0] / max(1, sum(tot)), 3))
        if metrics: doc["metrics"] = metrics   # latest value of each metric (count only: no threshold, no alert, no gap)
        return True
    jsonrec.update(f"{OUT}/passrate-{day}.json", fn, default={})

def brief_facts(d, y):
    """what Jasper's 09:00 brief reads: yesterday's pass rate, open alerts, escalations of gaps still open"""
    gaps = {g.get("id"): g for g in jload(GAPS, []) if isinstance(g, dict)}
    esc = [e for e in jload(f"{OUT}/escalations.json", {}).values() if (gaps.get(e["id"]) or {}).get("status") == "open"]
    return {"enabled": os.path.exists(FLAG), "passrate_yesterday": jload(f"{OUT}/passrate-{y}.json", None),
            "passrate_today": jload(f"{OUT}/passrate-{d}.json", None),
            "alerts": [jload(f, {}) for f in sorted(glob.glob(f"{OUT}/alerts/*.json"))], "escalations": esc,
            "open_gaps": sorted(k for k, g in gaps.items() if g.get("source") == "stdcheck" and g.get("status") == "open")}


def run(now=None):
    now = now or now_cst()
    st = jload(f"{OUT}/state.json", {})
    fns = {"qa_start": (check_qa_start, {}), "panoptes_prs": (check_panoptes_prs, {}), "fix_prs": (check_fix_prs, {}),
           "tasks": (check_tasks, {"retry": retry_task(st, now)}), "review_pr": (check_review_pr, {})}
    results, errors = {}, {}
    for c in CHECKS:
        fn, kw = fns[c]; results[c], err = run_check(c, fn, now, **kw)
        if err: errors[c] = err
        log(f"stdcheck {c}: " + (f"ERROR {err}" if err else f"breach {json.dumps(results[c], ensure_ascii=False)[:300]}" if results[c] else "ok"))
    try: frf = first_run_fail(now)
    except Exception as e: frf = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    log(f"stdcheck metric first_run_fail: {json.dumps(frf, ensure_ascii=False)}")
    if DRY: print("DRY: metric first_run_fail", json.dumps(frf, ensure_ascii=False))
    escalate(sync_gaps(results, now), now)
    update_alerts(st, results, errors, now)
    record_passrate(results, now, {"first_run_fail": frf})
    st.update(last_run=iso(now), errors=errors, crash_streak=0, metrics={"first_run_fail": frf})
    if os.path.exists(f"{OUT}/alerts/stdcheck.json") and not DRY: os.unlink(f"{OUT}/alerts/stdcheck.json"); log("stdcheck: crash alert recovered")
    save_json(f"{OUT}/state.json", st)
    return results

def main():
    if not os.path.exists(FLAG): print(f"stdcheck: {FLAG} absent -> no-op"); return 0
    try: run(); return 0
    except Exception as e:   # never silent: own alert file + rc 1 (run.sh records it; 2 in a row show in the brief via alerts/)
        import traceback; traceback.print_exc()
        st = jload(f"{OUT}/state.json", {}); n = st.get("crash_streak", 0) + 1; st["crash_streak"] = n
        try:
            save_json(f"{OUT}/state.json", st)
            save_json(f"{OUT}/alerts/stdcheck.json", {"check": "stdcheck", "consecutive_failures": n, "last": iso(now_cst()),
                                                     "error": f"{type(e).__name__}: {str(e)[:300]}", "owner": "Pi 线运维", "for_brief": brief_day(now_cst())})
        except Exception: traceback.print_exc()
        return 1

if __name__ == "__main__": sys.exit(main())
