"""Line-for-line Python port of mivo-canvas-plugin scripts/loops/automerge/lib/gate.mjs (pure, no IO).
Snapshots use the same shape as collect.mjs (camelCase keys, times in ms). JS `undefined` = key absent;
JS `null` = None. Verified by differential fuzz against the real gate.mjs (bin/gate_diff.py)."""
import re, json, math
MIN = 60_000; HOUR = 60 * MIN
WRITE_MODES = {"canary", "on"}; RUN_MODES = {"shadow", "canary", "on"}
SHA40 = re.compile(r"^[0-9a-f]{40}$")
NEG, POS = -math.inf, math.inf

def result(v, r): return {"verdict": v, "reasons": [r]}
def inc(s, f): return isinstance(s.get("incomplete"), list) and f in s["incomplete"]
def nn(x, d): return d if x is None else x          # JS ??
def jstr(x): return "" if x is None else (str(x).lower() if isinstance(x, bool) else (str(int(x)) if isinstance(x, float) and x.is_integer() else str(x)))

def can_write(g, status_issue):
    return (g.get("mode") in WRITE_MODES and g.get("host") == g.get("hostId") and not g.get("pausedFile")
            and g.get("circuitOpen") is False and not inc(g, "circuit") and not inc(g, "identity")
            and g.get("statusIssue") is not None and g.get("statusIssue") == status_issue)

def sh_minute(ms):
    s = (int(ms) + 8 * HOUR) // 1000
    return (s // 60) % 1440
def to_min(hhmm): h, m = map(int, str(hhmm).split(":")); return h * 60 + m
def is_maintenance(now, w): m = sh_minute(now); return to_min(w["start"]) <= m < to_min(w["end"])

def select_latest(runs, name):
    best = None
    for r in runs:
        if r.get("name") != name: continue
        if best is None: best = r; continue
        a, b = nn(r.get("startedAt"), POS), nn(best.get("startedAt"), POS)
        if a > b or (a == b and float(r.get("id") or 0) > float(best.get("id") or 0)): best = r
    return best

def same_set(a, b): return len(a) == len(b) and all(x in set(b) for x in a)
def intersect(sets):
    if not sets: return []
    acc = list(sets[0])
    for s in sets[1:]: acc = [m for m in acc if m in s]
    return sorted(acc)

def report_head(body):
    m = re.search(r"<!-- mivo-review:report head=([0-9a-f]{40})\b", body or "")
    return m[1] if m else None
def login(x): return (x.get("author") or {}).get("login")
def is_report_comment(r, head, p):
    return login(r) == p["reviewAccount"] and r.get("state") == "COMMENTED" and jstr(r.get("body")).startswith(p["reviewBodyHeader"]) and report_head(r.get("body")) == head
def is_qual_approval(r, head, p):
    return r.get("state") == "APPROVED" and r.get("commitId") == head and login(r) in p["approvalReviewers"]
def latest_by(lst, key):
    best = None
    for x in lst:
        if best is None or (x.get(key) is not None and best.get(key) is not None and x[key] > best[key]): best = x
    return best

def select_report(pr, p):
    head = pr.get("headSha"); res = pr.get("reviewResult")
    if res is not None and res.get("outcome") == "review-only" and (res.get("approval") or {}).get("status") == "verified":
        i = jstr(nn(res["approval"].get("reviewId"), ""))
        r = next((x for x in pr["reviews"] if jstr(x.get("id")) == i), None)
        return r if r is not None and is_qual_approval(r, head, p) else None
    if res is not None and res.get("outcome") == "approval":
        return latest_by([r for r in pr["reviews"] if is_report_comment(r, head, p)], "submittedAt")
    if res is not None: return None
    return latest_by([r for r in pr["reviews"] if is_qual_approval(r, head, p) or is_report_comment(r, head, p)], "submittedAt")

SUPERSEDING = ["<!-- review-pr:findings", "<!-- mivo-review:advisory", "<!-- review-pr:gate-review"]
def has_marker(body, ms_): t = jstr(body); return any(m in t for m in ms_)

def evaluate_global(g, p):
    wb = []
    if g.get("mode") not in RUN_MODES: return {"ok": False, **result("SKIP", "mode-off")}
    if g.get("mode") not in WRITE_MODES: wb.append("mode-shadow")
    if inc(g, "identity"): return {"ok": False, **result("WAIT", "incomplete:identity")}
    if g.get("tokenLogin") != p["tokenLogin"] or not isinstance(g.get("tokenScopes"), list) or not same_set(g["tokenScopes"], p["tokenScopes"]):
        return {"ok": False, **result("STOP", "identity")}
    if g.get("host") != g.get("hostId"): wb.append("host-mismatch")
    if g.get("pausedFile"): wb.append("paused")
    if inc(g, "circuit"): return {"ok": False, **result("WAIT", "incomplete:circuit")}
    if g.get("circuitOpen"): return {"ok": False, **result("STOP", "circuit-open")}
    if is_maintenance(g["now"], p["maintenanceWindow"]): return {"ok": False, **result("WAIT", "maintenance-window")}
    if g.get("skillShaUpdatedAt") is None: return {"ok": False, **result("WAIT", "incomplete:skillSha")}
    if g["now"] - g["skillShaUpdatedAt"] < p["skillShaQuietMinutes"] * MIN: return {"ok": False, **result("WAIT", "skill-sha-quiet")}
    if g.get("prereviewHeartbeat") is None: return {"ok": False, **result("WAIT", "incomplete:prereviewHeartbeat")}
    if g["now"] - g["prereviewHeartbeat"] > p["prereviewHeartbeatMaxMinutes"] * MIN: return {"ok": False, **result("WAIT", "prereview-heartbeat-stale")}
    if inc(g, "rules") or not isinstance(g.get("requiredChecks"), list) or not isinstance(g.get("mergeMethodSets"), list):
        return {"ok": False, **result("WAIT", "incomplete:rules")}
    names = [c.get("context") for c in g["requiredChecks"]]
    drift_c = not same_set(names, p["expectedRequiredChecks"]) or len(set(names)) != len(names) or any(c.get("integrationId") != p["actionsAppId"] for c in g["requiredChecks"])
    methods = intersect(g["mergeMethodSets"])
    drift_m = len(g["mergeMethodSets"]) == 0 or not same_set(methods, p["expectedMergeMethods"])
    if drift_c or drift_m: return {"ok": False, **result("STOP", "config-drift")}
    ci = g.get("mainCi")
    if inc(g, "mainCi") or ci is None: return {"ok": False, **result("WAIT", "incomplete:mainCi")}
    if ci.get("status") != "completed": return {"ok": False, **result("WAIT", "main-not-green")}
    if ci.get("conclusion") != "success":
        failed = nn(ci.get("failedJobs"), [])
        non_flaky = [j for j in failed if j != p["mainCiAggregateJob"] and not any(j.startswith(x) for x in p["flakyMainJobPrefixes"])]
        only_agg = len(failed) > 0 and all(j == p["mainCiAggregateJob"] for j in failed)
        if g.get("mainHeadIsBotMerge") and not only_agg and (len(non_flaky) > 0 or nn(ci.get("runAttempt"), 0) >= 2):
            return {"ok": False, **result("STOP", "bot-merge-broke-main")}
        return {"ok": False, **result("WAIT", "main-not-green")}
    if not g.get("mainHead") or g.get("greenRef") != g.get("mainHead"): return {"ok": False, **result("WAIT", "main-not-green")}
    if g.get("mode") in WRITE_MODES and nn(g.get("mergesToday"), 0) >= p["maxMergesPerDay"][g["mode"]]:
        return {"ok": False, **result("WAIT", "daily-budget")}
    return {"ok": True, "writeBlockers": wb}

def evaluate(g, pr, p):
    gl = evaluate_global(g, p)
    if not gl["ok"]: return {"verdict": gl["verdict"], "reasons": gl["reasons"], "scope": "global", "settleFrom": None, "evidence": {}}
    return {**evaluate_pr(g, pr, p), "scope": "pr"}

def evaluate_pr(g, pr, p):
    head = pr.get("headSha"); ev = {"pr": pr.get("number"), "head": head}
    def done(v, r, sf=None, e=None): return {"verdict": v, "reasons": [r], "settleFrom": sf, "evidence": {**ev, **(e or {})}}
    if pr.get("state") != "OPEN" or pr.get("isDraft") or pr.get("locked") or pr.get("baseRefName") != p["base"] or pr.get("isCrossRepository"):
        return done("SKIP", "out-of-scope")
    if not SHA40.match(nn(head, "")): return done("WAIT", "incomplete:head")
    if re.search(p["releaseHeadPattern"], nn(pr.get("headRefName"), "")): return done("HUMAN", "release-head")
    labels = pr.get("labels") or []
    if any(l in p["holdLabels"] for l in labels): return done("SKIP", "hold-label")
    if g.get("mode") == "canary":
        if not (p["canaryLabel"] in labels and ((pr.get("timeline") or {}).get("canaryLabeledBy") in p["maintainers"])): return done("SKIP", "not-canary")
    report = select_report(pr, p)
    act = g.get("activationAt")
    if g.get("mode") in WRITE_MODES:
        if act is None: return done("SKIP", "before-activation")
        if report is not None and nn(report.get("submittedAt"), 0) < act: return done("SKIP", "before-activation")
    elif act is not None and report is not None and nn(report.get("submittedAt"), 0) < act: return done("SKIP", "before-activation")
    if login(pr) in p["excludeAuthors"]: return done("HUMAN", "excluded-author")
    r6 = check_paths(pr, p)
    if r6: return done(r6[0], r6[1], e=r6[2] if len(r6) > 2 else None)
    r7 = check_skip(pr, p)
    if r7: return done(*r7)
    if p["readyLabel"] not in labels: return done("SKIP", "no-ready-label")
    rc = select_latest(pr.get("checkRuns") or [], p["reviewCheckName"])
    if rc is None or rc.get("status") != "completed": return done("WAIT", "review-check-pending")
    if rc.get("appId") != p["actionsAppId"]: return done("STOP", "review-provenance", e={"reviewCheckApp": rc.get("appId")})
    if rc.get("conclusion") != "success": return done("SKIP", "review-check-failed")
    if jstr(nn(rc.get("title"), "")).startswith("受保护路径"): return done("HUMAN", "review-protected-path")
    ev["reviewCheckId"] = rc.get("id")
    r3 = check_review_run(pr, rc, p)
    if r3: return done(r3[0], r3[1], e=r3[2] if len(r3) > 2 else None)
    ev["reviewRunId"] = pr["reviewRun"].get("id")
    if "reviewResult" in pr:
        r3b = check_review_result(pr, rc, p)
        if r3b: return done(r3b[0], r3b[1], e=r3b[2] if len(r3b) > 2 else None)
    if report is None: return done("WAIT", "review-head-mismatch")
    ev["reportReviewId"] = report.get("id")
    if any(login(r) == p["reviewAccount"] and r.get("commitId") == head and r.get("submittedAt") is not None and report.get("submittedAt") is not None
           and r["submittedAt"] > report["submittedAt"] and has_marker(r.get("body"), SUPERSEDING) for r in pr["reviews"]):
        return done("WAIT", "review-head-mismatch")
    if has_changes_requested(pr["reviews"]): return done("HUMAN", "changes-requested")
    c0 = check_base_change(pr, p)
    if c0: return done(c0[0], c0[1], e=c0[2] if len(c0) > 2 else None)
    c1 = check_required(pr, p)
    if c1: return done(c1[0], c1[1], e=c1[2])
    c2 = check_others(pr, p)
    if c2: return done(c2[0], c2[1], e=c2[2] if len(c2) > 2 else None)
    if pr.get("mergeable") != "MERGEABLE" or pr.get("mergeStateStatus") != "CLEAN":
        return done("WAIT", "not-clean", e={"mergeStateStatus": pr.get("mergeStateStatus")})
    if inc(pr, "threads") or not pr.get("threads"):
        if inc(pr, "threads") or pr.get("threads") is None: return done("WAIT", "incomplete:threads")
    if any(not t.get("isResolved") for t in pr["threads"]): return done("WAIT", "threads-unresolved")
    if inc(pr, "comments"): return done("WAIT", "incomplete:comments")
    if post_report_comment(pr, report, p): return done("HUMAN", "post-report-comment")
    if pr.get("compare") is None: return done("WAIT", "incomplete:compare")
    cp = pr["compare"]
    if nn(cp.get("behindBy"), 0) > 0 and g["now"] - nn(cp.get("mergeBaseDate"), 0) > p["staleMergeBaseHours"] * HOUR: return done("HUMAN", "stale-merge-base")
    sf = settle_from(g, pr, rc, report)
    if g["now"] - sf < p["settleMinutes"] * MIN: return done("WAIT", "settling", sf)
    if "reviewResult" not in pr: return done("NEED_ARTIFACT", "need-artifact", sf)
    return done("MERGE", "all-gates-passed", sf)

def check_paths(pr, p):
    if inc(pr, "files") or pr.get("files") is None: return ("WAIT", "incomplete:files")
    if len(pr["files"]) > p["maxFilesReadable"] or nn(pr.get("changedFiles"), 0) > p["maxFilesReadable"]:
        return ("HUMAN", "human-only-path", {"tooManyFiles": pr.get("changedFiles")})
    pats = [re.compile(x) for x in p["humanOnlyPaths"]]
    for f in pr["files"]:
        for path in [x for x in (f.get("filename"), f.get("previousFilename")) if x]:
            hit = next((r for r in pats if r.search(path)), None)
            if hit: return ("HUMAN", "human-only-path", {"path": path, "pattern": hit.pattern})
    gp = p["ghostJsonPath"]
    gh = next((f for f in pr["files"] if f.get("filename") == gp or f.get("previousFilename") == gp), None)
    if gh is None: return None
    if gh.get("filename") != gp or gh.get("status") == "removed" or (gh.get("previousFilename") and gh.get("previousFilename") != gh.get("filename")):
        return ("HUMAN", "ghost-json-sensitive-field", {"ghostStatus": gh.get("status")})
    if "ghostJson" not in pr: return ("WAIT", "incomplete:ghostJson")
    gj = nn(pr["ghostJson"], {})
    b, h = gj.get("base"), gj.get("head")
    if not isinstance(b, dict) or not isinstance(h, dict): return ("HUMAN", "ghost-json-sensitive-field", {"ghostParse": "invalid"})
    keys = sorted(k for k in set(b) | set(h) if json.dumps(b.get(k), sort_keys=False, ensure_ascii=False) != json.dumps(h.get(k), sort_keys=False, ensure_ascii=False) or ((k in b) != (k in h)))
    sens = [k for k in keys if k not in p["ghostJsonAutoKeys"]]
    if sens: return ("HUMAN", "ghost-json-sensitive-field", {"ghostKeys": sens})
    return None

def check_skip(pr, p):
    c = pr.get("commits")
    if inc(pr, "commits") or c is None: return ("WAIT", "incomplete:commits")
    if nn(c.get("total"), 0) > p["maxCommitsReadable"]: return ("HUMAN", "too-many-commits")
    if len(c.get("messages") or []) != c.get("total"): return ("WAIT", "incomplete:commits")
    mk = [m.lower() for m in p["skipCiMarkers"]]
    texts = [jstr(t).lower() for t in [pr.get("title")] + list(c["messages"])]
    if any(m in t for t in texts for m in mk): return ("HUMAN", "skip-ci-marker")
    return None

RUN_URL = re.compile(r"^https://github\.com/([^/]+/[^/]+)/actions/runs/(\d+)$")
def check_review_run(pr, rc, p):
    m = RUN_URL.match(nn(rc.get("externalId"), "") or "")
    if not m or m[1] != p["repo"]: return ("STOP", "review-provenance", {"externalId": rc.get("externalId")})
    run = pr.get("reviewRun")
    if run is None: return ("WAIT", "incomplete:reviewRun")
    t = re.match(r"^review-pr #(\d+) ", nn(run.get("displayTitle"), "") or "")
    ok = (jstr(run.get("id")) == m[2] and run.get("path") == p["reviewWorkflowPath"] and run.get("event") == "workflow_dispatch"
          and run.get("triggeringActor") == p["reviewDispatcher"] and run.get("headBranch") == p["base"] and run.get("status") == "completed"
          and t is not None and int(t[1]) == pr.get("number"))
    if not ok: return ("STOP", "review-provenance", {"reviewRunId": run.get("id")})
    return None

def js_number(x):
    try: return float(x) if x is not None and str(x).strip() != "" else (0.0 if x is not None else math.nan)
    except Exception: return math.nan
def check_review_result(pr, rc, p):
    res = pr.get("reviewResult")
    if res is None: return ("HUMAN", "review-artifact-missing")
    mv = res.get("mivo") or {}
    ok = (js_number(res.get("pr")) == pr.get("number") and res.get("head") == pr.get("headSha") and mv.get("runUrl") == rc.get("externalId")
          and jstr(nn((mv.get("checkRun") or {}).get("id"), "")) == jstr(rc.get("id")))
    if not ok: return ("STOP", "review-provenance", {"resultPr": res.get("pr"), "resultHead": res.get("head")})
    if (res.get("reviewEvidence") or {}).get("baseRefName") != p["base"]:
        return ("WAIT", "review-base-mismatch", {"reviewedBase": (res.get("reviewEvidence") or {}).get("baseRefName")})
    a = res.get("approval") or {}
    bA = res.get("outcome") == "review-only" and a.get("status") == "verified" and a.get("head") == pr.get("headSha") and a.get("reviewId") is not None and a.get("reviewId") != ""
    bB = login(pr) == p["reviewAccount"] and res.get("outcome") == "approval" and a.get("status") == "blocked" and a.get("code") == "independent-approval-required"
    if res.get("reviewClean") is not True or not (bA or bB):
        return ("HUMAN", "review-not-clean", {"outcome": res.get("outcome"), "approvalStatus": a.get("status"), "approvalCode": a.get("code")})
    return None

def has_changes_requested(reviews):
    latest = {}
    for r in reviews:
        if r.get("state") not in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"): continue
        k = login(r); prev = latest.get(k, None)
        if prev is None or (r.get("submittedAt") is not None and prev.get("submittedAt") is not None and r["submittedAt"] > prev["submittedAt"]): latest[k] = r
    return any(r.get("state") == "CHANGES_REQUESTED" for r in latest.values())

def check_base_change(pr, p):
    if inc(pr, "timeline") or pr.get("timeline") is None: return ("WAIT", "incomplete:timeline")
    tb = pr["timeline"].get("baseChangedAt")
    if tb is None: return None
    for name in p["expectedRequiredChecks"]:
        c = select_latest(pr.get("checkRuns") or [], name)
        if c is None: continue
        ca = (pr.get("reviewRun") or {}).get("createdAt") if name == p["reviewCheckName"] else (pr.get("checkOrigins") or {}).get(str(c.get("id")))
        if ca is None: return ("WAIT", "incomplete:check-origin", {"check": name})
        if ca <= tb: return ("HUMAN", "base-changed", {"check": name, "baseChangedAt": tb})
    return None

def check_required(pr, p):
    for name in p["expectedRequiredChecks"]:
        c = select_latest(pr.get("checkRuns") or [], name)
        if c is None: return ("WAIT", "required-check-missing", {"check": name})
        if c.get("status") != "completed": return ("WAIT", "required-check-pending", {"check": name})
        if c.get("appId") != p["actionsAppId"]: return ("WAIT", "required-check-missing", {"check": name, "appId": c.get("appId")})
        if c.get("conclusion") != "success": return ("SKIP", "required-check-failed", {"check": name})
    return None

def check_others(pr, p):
    if inc(pr, "checkRuns"): return ("WAIT", "incomplete:checkRuns")
    names = list(dict.fromkeys(c.get("name") for c in pr.get("checkRuns") or []))
    for n in names:
        if n in p["expectedRequiredChecks"]: continue
        c = select_latest(pr["checkRuns"], n)
        if c.get("status") != "completed": return ("WAIT", "check-pending", {"check": n})
        if n in p["checksCompletedOnly"]: continue
        if c.get("conclusion") in p["failingConclusions"]: return ("WAIT", "check-failed", {"check": n})
    st = nn(pr.get("combinedStatus"), {"state": "success", "total": 0})
    if nn(st.get("total"), 0) > 0 and st.get("state") != "success": return ("WAIT", "check-pending", {"combinedStatus": st.get("state")})
    return None

MARK_RE = re.compile(r"^[a-z0-9-]+(?: [a-z0-9_-]+=[A-Za-z0-9._:-]+)*$")
def complete_marker(body, markers):
    for m in markers:
        s = body.find(m)
        while s != -1:
            cs = s + len(m); e = body.find("-->", cs)
            content = (body[cs:] if e == -1 else body[cs:e]).strip()
            if e != -1 and MARK_RE.match(content): return True
            s = body.find(m, cs)
    return False

def post_report_comment(pr, report, p):
    rs = report.get("submittedAt")
    def after(i):
        t = max(nn(i.get("createdAt"), NEG), nn(i.get("updatedAt"), NEG))
        return t > (rs if rs is not None else 0)
    def human(i):
        body = jstr(i.get("body"))
        if not body.strip(): return False
        if (i.get("author") or {}).get("isBot"): return False
        if login(i) == login(pr): return False
        if login(i) == p["reviewAccount"] and complete_marker(body, p["machineMarkers"]):
            if not body.startswith(p["reviewBodyHeader"]) or report_head(body) == pr.get("headSha"): return False
        return True
    cm = [c for c in pr.get("comments") or [] if after(c)]
    rb = [{"author": r.get("author"), "body": r.get("body"), "createdAt": r.get("submittedAt")} for r in pr["reviews"] if jstr(r.get("id")) != jstr(report.get("id"))]
    rb = [x for x in rb if after(x)]
    return any(human(x) for x in cm + rb)

def settle_from(g, pr, rc, report):
    tl = pr.get("timeline") or {}
    ts = [rc.get("completedAt"), report.get("submittedAt"), tl.get("readyLabeledAt"), tl.get("readyForReviewAt")] + [c.get("completedAt") for c in pr.get("checkRuns") or []]
    ts = [t for t in ts if isinstance(t, (int, float)) and not isinstance(t, bool)]
    ts.append(nn((pr.get("local") or {}).get("threadsStableSince"), g["now"]))
    return max(ts)
