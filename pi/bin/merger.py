#!/usr/bin/env python3
"""合并官 (Pi line, zero-model) — Grok-line parity port.
Decision = gate_port.evaluate(): a line-for-line port of automerge/lib/gate.mjs (all global gates G0–G10 incl.
maintenance window + skill-sha-quiet, all per-PR gates P1–C7), differential-tested against the real gate.mjs.
Collection mirrors collect.mjs / round.mjs (read-only gh api). Round logic mirrors round.mjs: global gate first;
prefilter non-draft + ready label; not CLEAN -> WAIT not-clean; NEED_ARTIFACT -> fetch ≤maxArtifactFetchesPerRound
by settleFrom then re-judge; pick ≤1 MERGE by earliest settleFrom then PR number; STOP ends the round.
Pi-only extras (can only delay, never advance): conventional title; reviewDecision != CHANGES_REQUESTED; Pi daily cap 2;
Grok-line cooldown respected. Actions are recorded ONLY when the Grok line itself could write (canWrite: mode
canary/on, host binding, circuit closed, status issue) AND PAUSED is absent — so while PAUSED nothing (no merge, no
comment) is recorded. Comments only for HUMAN, one per (PR, head, reason), like deliverNotice. Never writes GitHub."""
from lib import *
import hashlib, io, zipfile, time, subprocess, urllib.parse
import gate_port as G
CST = dt.timezone(dt.timedelta(hours=8))
MM = "/workspace/mivo-merger"
POLICY_PATH = f"{MM}/app/scripts/loops/automerge/policy.json"
P = json.load(open(POLICY_PATH))
CFG = json.load(open(f"{MM}/config.json"))
PAUSED = CFG.get("pausedPath", f"{MM}/PAUSED")
REQ = P["expectedRequiredChecks"]; READY = P["readyLabel"]
PI_DAY_CAP = 2
STATE = f"{BASE}/out/merger/state.json"      # Pi copy of state.mjs prs[n] {head,fingerprint,threadsStableSince}
LEDGER = f"{BASE}/out/outbound/ledger.json"
CONV = re.compile(r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([^)]+\))?!?: .+")
FIELDS = "number,title,headRefOid,state"     # outbound.py compat (h_merge.state uses these)

def ms(s):
    if not s: return None
    try: return int(parse_ts(s).timestamp() * 1000)
    except Exception: return None
def api(path, paginate=False, q=None):
    a = ["api"] + (["--paginate"] if paginate else []) + [path] + (["-q", q] if q else [])
    return gh(a)
def api_list(path, key=None):
    """paginate; key = items key for wrapped lists (check_runs, workflow_runs, jobs, artifacts)."""
    r = subprocess.run(["gh", "api", "--paginate", path] + (["-q", f".{key}[]"] if key else ["-q", ".[]"]), capture_output=True, text=True, timeout=180)
    if r.returncode: raise RuntimeError(r.stderr.strip()[:200])
    return [json.loads(l) for l in r.stdout.splitlines() if l.strip()]
def attempt(snap, field, fn):
    try: return fn()
    except Exception as e:
        snap["incomplete"].append(field); snap.setdefault("errors", []).append({"field": field, "message": str(e)[:200]}); return None

# ---------------- global (collect.mjs collectGlobal) ----------------
def read_var(name):
    r = subprocess.run(["gh", "api", f"repos/{REPO}/actions/variables/{name}"], capture_output=True, text=True, timeout=60)
    if r.returncode:
        if "404" in r.stderr or "Not Found" in r.stderr: return {"value": None, "updatedAt": None}
        raise RuntimeError(r.stderr.strip()[:200])
    d = json.loads(r.stdout); return {"value": d.get("value"), "updatedAt": ms(d.get("updated_at"))}
def identity():
    r = subprocess.run(["gh", "api", "-i", "user"], capture_output=True, text=True, timeout=60)
    if r.returncode: raise RuntimeError("identity read failed")
    head, _, body = r.stdout.partition("\r\n\r\n") if "\r\n\r\n" in r.stdout else r.stdout.partition("\n\n")
    sc = next((l.split(":", 1)[1] for l in head.splitlines() if l.lower().startswith("x-oauth-scopes:")), None)
    return {"login": json.loads(body).get("login"), "scopes": None if sc is None else sorted(s.strip() for s in sc.split(",") if s.strip())}
def read_main_ci(main_head):
    runs = api_list(f"repos/{REPO}/actions/workflows/{P['mainCiWorkflow']}/runs?branch={P['base']}&event=push&head_sha={main_head}", "workflow_runs")
    if not runs: return {"status": "missing", "conclusion": None, "runAttempt": 0, "failedJobs": []}
    run = max(runs, key=lambda x: int(x["id"]))
    ci = {"runId": run["id"], "status": run["status"], "conclusion": run["conclusion"], "runAttempt": run.get("run_attempt"), "failedJobs": []}
    if run["status"] == "completed" and run["conclusion"] != "success":
        jobs = api_list(f"repos/{REPO}/actions/runs/{run['id']}/jobs?filter=latest", "jobs")
        ci["failedJobs"] = [j["name"] for j in jobs if j.get("conclusion") in ("failure", "timed_out", "cancelled", "startup_failure")]
    return ci
def pi_merges(ledger, day=None):
    return [k for k, v in ledger.items() if k.startswith("merge:") and v.get("status") == "done" and v.get("by") == "pi-line"
            and (day is None or str(v.get("at", "")).startswith(day))]
def collect_global():
    g = {"incomplete": [], "errors": [], "hostId": CFG.get("hostId"), "pausedFile": os.path.exists(PAUSED)}
    ident = attempt(g, "identity", identity) or {}
    g["tokenLogin"], g["tokenScopes"] = ident.get("login"), ident.get("scopes")
    v = attempt(g, "variables", lambda: {k: read_var(n) for k, n in [("mode", "MIVO_AUTOMERGE_MODE"), ("host", "MIVO_AUTOMERGE_HOST"),
        ("activation", "MIVO_AUTOMERGE_ACTIVATION_AT"), ("statusIssue", "MIVO_AUTOMERGE_STATUS_ISSUE"), ("heartbeat", "MIVO_PREREVIEW_HEARTBEAT"), ("skillSha", "MIVO_REVIEW_SKILL_SHA")]})
    g["mode"] = v["mode"]["value"] if v else None; g["host"] = v["host"]["value"] if v else None
    g["activationAt"] = ms(v["activation"]["value"]) if v else None
    g["statusIssue"] = int(v["statusIssue"]["value"]) if v and v["statusIssue"]["value"] else None
    g["prereviewHeartbeat"] = ms(v["heartbeat"]["value"]) if v else None
    g["skillShaUpdatedAt"] = v["skillSha"]["updatedAt"] if v else None
    rules = attempt(g, "rules", lambda: api_list(f"repos/{REPO}/rules/branches/{P['base']}"))
    if rules is not None:
        g["requiredChecks"] = [{"context": c.get("context"), "integrationId": c.get("integration_id")} for r in rules if r.get("type") == "required_status_checks"
                               for c in (r.get("parameters") or {}).get("required_status_checks", [])]
        g["mergeMethodSets"] = [(r.get("parameters") or {}).get("allowed_merge_methods") or ["merge", "squash", "rebase"] for r in rules if r.get("type") == "pull_request"]
    g["mainHead"] = attempt(g, "mainCi", lambda: api(f"repos/{REPO}/branches/{P['base']}")["commit"]["sha"])
    g["greenRef"] = attempt(g, "mainCi", lambda: api(f"repos/{REPO}/git/ref/heads/{P['greenRef']}")["object"]["sha"])
    if g["mainHead"]: g["mainCi"] = attempt(g, "mainCi", lambda: read_main_ci(g["mainHead"]))
    if g["statusIssue"]:
        iss = attempt(g, "circuit", lambda: api(f"repos/{REPO}/issues/{g['statusIssue']}"))
        if iss is not None: g["circuitOpen"] = any((l.get("name") if isinstance(l, dict) else l) == P["circuitLabel"] for l in iss.get("labels") or [])
    else: g["incomplete"].append("circuit")
    led = jload_(LEDGER); day = today()
    g["mergesToday"] = len(pi_merges(led, day)) + grok_merges_today(day)   # both lines share one daily budget
    bot = False
    for k in pi_merges(led)[-5:]:
        try: bot = bot or api(f"repos/{REPO}/pulls/{k.split(':')[1]}").get("merge_commit_sha") == g["mainHead"]
        except Exception: pass
    g["mainHeadIsBotMerge"] = bot
    g["now"] = int(time.time() * 1000)
    return g

# ---------------- per PR (collect.mjs collectPr + round.mjs collectWithThreads) ----------------
QT = """query($o:String!,$n:String!,$p:Int!,$c:String){repository(owner:$o,name:$n){pullRequest(number:$p){reviewThreads(first:100,after:$c){totalCount pageInfo{hasNextPage endCursor} nodes{id isResolved comments(last:1){totalCount nodes{createdAt}}}}}}}"""
def read_threads(n):
    nodes, cur, total, seen = [], None, None, set()
    while True:
        a = ["api", "graphql", "-f", f"query={QT}", "-F", "o=xindong", "-F", "n=mivo-canvas-plugin", "-F", f"p={n}"] + (["-f", f"c={cur}"] if cur else [])
        conn = gh(a)["data"]["repository"]["pullRequest"]["reviewThreads"]
        total = conn["totalCount"] if total is None else total
        for t in conn["nodes"]:
            nodes.append({"id": t["id"], "isResolved": t["isResolved"], "commentCount": t["comments"]["totalCount"],
                          "lastCommentAt": ms((t["comments"]["nodes"] or [{}])[0].get("createdAt"))})
        if not conn["pageInfo"]["hasNextPage"]: break
        if conn["pageInfo"]["endCursor"] in seen: raise RuntimeError("thread cursor repeat")
        seen.add(conn["pageInfo"]["endCursor"]); cur = conn["pageInfo"]["endCursor"]
    if len(nodes) != total: raise RuntimeError("thread count mismatch")
    return nodes
def fingerprint(ts): return "|".join(sorted(f"{t['id']}:{1 if t['isResolved'] else 0}:{t['commentCount']}:{t['lastCommentAt'] if t['lastCommentAt'] is not None else ''}" for t in ts))
def update_threads(st, n, head, upd, at):
    prev = st.get(str(n)); same = bool(prev) and prev.get("head") == head; since = None
    if upd.get("complete") and upd.get("allResolved"):
        since = prev["threadsStableSince"] if same and prev.get("fingerprint") == upd.get("fingerprint") and prev.get("threadsStableSince") is not None else at
    st[str(n)] = {**(prev if same else {}), "head": head, "fingerprint": upd.get("fingerprint") if upd.get("complete") else None, "threadsStableSince": since}
    return since
def norm_check(c):
    return {"id": c["id"], "name": c["name"], "appId": (c.get("app") or {}).get("id"), "status": c["status"], "conclusion": c.get("conclusion"),
            "startedAt": ms(c.get("started_at")), "completedAt": ms(c.get("completed_at")), "title": (c.get("output") or {}).get("title"), "externalId": c.get("external_id")}
def norm_review(r):
    return {"id": r["id"], "state": r["state"], "commitId": r.get("commit_id"), "author": {"login": (r.get("user") or {}).get("login"), "isBot": (r.get("user") or {}).get("type") == "Bot"},
            "submittedAt": ms(r.get("submitted_at")), "body": r.get("body") or ""}
def norm_comment(c):
    return {"author": {"login": (c.get("user") or {}).get("login"), "isBot": (c.get("user") or {}).get("type") == "Bot"}, "createdAt": ms(c.get("created_at")), "updatedAt": ms(c.get("updated_at")), "body": c.get("body") or ""}
def read_timeline(evs):
    tl = {"readyLabeledAt": None, "canaryLabeledAt": None, "canaryLabeledBy": None, "readyForReviewAt": None, "baseChangedAt": None}; ce = -1
    for e in evs:
        at = ms(e.get("created_at"))
        if at is None: continue
        lab = (e.get("label") or {}).get("name")
        if e.get("event") == "labeled" and lab == READY and at > (tl["readyLabeledAt"] if tl["readyLabeledAt"] is not None else -1): tl["readyLabeledAt"] = at
        if e.get("event") in ("labeled", "unlabeled") and lab == P["canaryLabel"] and at >= ce:
            ce = at; tl["canaryLabeledAt"] = at if e["event"] == "labeled" else None
            tl["canaryLabeledBy"] = (e.get("actor") or {}).get("login") if e["event"] == "labeled" else None
        if e.get("event") == "ready_for_review" and at > (tl["readyForReviewAt"] if tl["readyForReviewAt"] is not None else -1): tl["readyForReviewAt"] = at
        if e.get("event") in P["baseChangeEvents"] and at > (tl["baseChangedAt"] if tl["baseChangedAt"] is not None else -1): tl["baseChangedAt"] = at
    return tl
def read_ghost(sha):
    r = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github.raw+json", f"repos/{REPO}/contents/{P['ghostJsonPath']}?ref={sha}"], capture_output=True, text=True, timeout=60)
    if r.returncode: raise RuntimeError("ghost read failed")
    return json.loads(r.stdout)
def collect_pr(n, st, now, require_clean=True):
    try: threads = read_threads(n)
    except Exception: threads = None
    pr = {"number": n, "incomplete": [], "errors": []}
    pull = attempt(pr, "pull", lambda: api(f"repos/{REPO}/pulls/{n}"))
    if pull is None: pr.update(state="UNKNOWN", labels=[], checkRuns=[], reviews=[]); return pr
    pr.update(number=pull["number"], title=pull["title"], isDraft=pull["draft"], locked=pull["locked"],
              isCrossRepository=((pull.get("head") or {}).get("repo") or {}).get("full_name") != ((pull.get("base") or {}).get("repo") or {}).get("full_name"),
              baseRefName=pull["base"]["ref"], headRefName=pull["head"]["ref"], headSha=pull["head"]["sha"],
              author={"login": (pull.get("user") or {}).get("login"), "isBot": (pull.get("user") or {}).get("type") == "Bot"},
              labels=[l["name"] for l in pull.get("labels") or []],
              mergeable={"True": "MERGEABLE", "False": "CONFLICTING"}.get(str(pull.get("mergeable")), "UNKNOWN"),
              mergeStateStatus=str(pull.get("mergeable_state") or "unknown").upper(),
              state="MERGED" if pull.get("merged") else str(pull["state"]).upper(), changedFiles=pull.get("changed_files"))
    head = pr["headSha"]
    upd = {"fingerprint": fingerprint(threads), "allResolved": all(t["isResolved"] for t in threads), "complete": True} if threads is not None else {"complete": False}
    pr["local"] = {"threadsStableSince": update_threads(st, n, head, upd, now)}
    if require_clean and pr["mergeStateStatus"] != "CLEAN":
        pr.update(partial=True, checkRuns=[], reviews=[], threads=threads); return pr
    pr["checkRuns"] = [norm_check(c) for c in (attempt(pr, "checkRuns", lambda: api_list(f"repos/{REPO}/commits/{head}/check-runs?filter=all&per_page=100", "check_runs")) or [])]
    s = attempt(pr, "checkRuns", lambda: api(f"repos/{REPO}/commits/{head}/status"))
    pr["combinedStatus"] = {"state": s["state"], "total": s["total_count"]} if s else None
    rc = G.select_latest(pr["checkRuns"], P["reviewCheckName"])
    m = re.search(r"/actions/runs/(\d+)$", (rc or {}).get("externalId") or "")
    if m:
        run = attempt(pr, "reviewRun", lambda: api(f"repos/{REPO}/actions/runs/{m[1]}"))
        if run: pr["reviewRun"] = {"id": run["id"], "path": run.get("path"), "event": run.get("event"), "triggeringActor": (run.get("triggering_actor") or {}).get("login"),
                                   "headBranch": run.get("head_branch"), "status": run.get("status"), "displayTitle": run.get("display_title"), "createdAt": ms(run.get("created_at"))}
    pr["reviews"] = [norm_review(r) for r in (attempt(pr, "reviews", lambda: api_list(f"repos/{REPO}/pulls/{n}/reviews?per_page=100")) or [])]
    if threads is not None: pr["threads"] = threads
    else:
        pr["threads"] = attempt(pr, "threads", lambda: read_threads(n))
    rt = [r["submittedAt"] for r in pr["reviews"] if r["commitId"] == head and (r["state"] == "APPROVED" or r["author"]["login"] == P["reviewAccount"]) and r["submittedAt"] is not None]
    if rt:
        since = dt.datetime.fromtimestamp(min(rt) / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{min(rt) % 1000:03d}Z"
        ic = attempt(pr, "comments", lambda: api_list(f"repos/{REPO}/issues/{n}/comments?since={since}&per_page=100"))
        rcm = attempt(pr, "comments", lambda: api_list(f"repos/{REPO}/pulls/{n}/comments?since={since}&per_page=100"))
        pr["comments"] = [norm_comment(c) for c in (ic or []) + (rcm or [])]
    else: pr["comments"] = []
    files = attempt(pr, "files", lambda: api_list(f"repos/{REPO}/pulls/{n}/files?per_page=100"))
    if files is not None:
        pr["files"] = [{"filename": f["filename"], "previousFilename": f.get("previous_filename"), "status": f.get("status")} for f in files]
        if (pr["changedFiles"] or 0) <= P["maxFilesReadable"] and len(pr["files"]) != pr["changedFiles"]: pr["incomplete"].append("files")
    else: pr["files"] = None
    commits = attempt(pr, "commits", lambda: api_list(f"repos/{REPO}/pulls/{n}/commits?per_page=100"))
    pr["commits"] = {"total": pull.get("commits"), "messages": [(c.get("commit") or {}).get("message") or "" for c in commits]} if commits is not None else None
    cmp_ = attempt(pr, "compare", lambda: api(f"repos/{REPO}/compare/{P['base']}...{head}?per_page=1"))
    if cmp_: pr["compare"] = {"behindBy": cmp_.get("behind_by"), "mergeBaseSha": (cmp_.get("merge_base_commit") or {}).get("sha"),
                              "mergeBaseDate": ms((((cmp_.get("merge_base_commit") or {}).get("commit") or {}).get("committer") or {}).get("date"))}
    evs = attempt(pr, "timeline", lambda: api_list(f"repos/{REPO}/issues/{n}/timeline?per_page=100"))
    pr["timeline"] = read_timeline(evs) if evs is not None else None
    if any(f["filename"] == P["ghostJsonPath"] or f.get("previousFilename") == P["ghostJsonPath"] for f in pr["files"] or []) and (pr.get("compare") or {}).get("mergeBaseSha"):
        try: pr["ghostJson"] = {"base": read_ghost(pr["compare"]["mergeBaseSha"]), "head": read_ghost(head)}
        except Exception as e: pr["ghostJson"] = {"base": None, "head": None, "error": str(e)[:200]}
    if (pr.get("timeline") or {}).get("baseChangedAt") is not None:
        def origins():
            o = {}
            for name in REQ:
                if name == P["reviewCheckName"]: continue
                c = G.select_latest(pr["checkRuns"], name)
                if not c: continue
                job = api(f"repos/{REPO}/actions/jobs/{c['id']}"); run = api(f"repos/{REPO}/actions/runs/{job['run_id']}")
                o[str(c["id"])] = ms(run["created_at"])
            return o
        pr["checkOrigins"] = attempt(pr, "checkOrigins", origins)
    return pr
def fetch_review_result(pr):
    """collect.mjs fetchReviewResult: None = missing/expired; raises on read failure."""
    rid = (pr.get("reviewRun") or {}).get("id")
    if not rid: return None
    arts = api_list(f"repos/{REPO}/actions/runs/{rid}/artifacts", "artifacts")
    art = next((a for a in arts if a["name"] == f"review-pr-{pr['number']}" and not a["expired"]), None)
    if not art: return None
    z = subprocess.run(["gh", "api", f"repos/{REPO}/actions/artifacts/{art['id']}/zip"], capture_output=True, timeout=120)
    if z.returncode: raise RuntimeError("artifact download failed")
    with zipfile.ZipFile(io.BytesIO(z.stdout)) as zf:
        return json.loads(zf.read("result.json")) if "result.json" in zf.namelist() else None

# ---------------- decision ----------------
def judge(g, pr):
    """Grok gate (gate_port.evaluate) + Pi-only stricter extras. Returns (verdict, reason, evaluation)."""
    ev = G.evaluate(g, pr, P); v, r = ev["verdict"], ev["reasons"][0]
    if v == "MERGE":
        if not CONV.match(pr.get("title") or ""): return "SKIP", "pi:title-not-conventional", ev
        if (g.get("mergesToday") or 0) >= PI_DAY_CAP: return "WAIT", "pi:daily-cap-2", ev
    return v, r, ev
def grok_cooldown(n, now):
    st = grok_state()
    return (((st.get("prs") or {}).get(str(n)) or {}).get("cooldownUntil") or 0) > now

def run_round(st):
    """round.mjs runRound (read-only). Returns summary dict."""
    s = {"global": None, "decisions": [], "pick": None, "writable": False, "g": {}, "held": None, "circuit": None, "stall": None}
    try:   # round.mjs rate reserve (shared classic-token budget)
        rl = api("rate_limit")["resources"]
        if rl["core"]["remaining"] < P["rateReserve"]["core"] or rl["graphql"]["remaining"] < P["rateReserve"]["graphql"]:
            s["skipped"] = "rate-budget"; return s
    except Exception: s["skipped"] = "rate-budget"; return s
    g = collect_global(); s["g"] = g
    gl = G.evaluate_global(g, P)
    ident_blocked = not gl["ok"] and gl["reasons"][0] in ("identity", "incomplete:identity")
    by_cfg = G.can_write(g, CFG.get("statusIssue")) and not os.path.exists(PAUSED)
    s["writable"] = by_cfg and not ident_blocked
    gs = grok_state()
    if gs.get("pendingMergeRequests") and gl.get("verdict") != "STOP": s["held"] = "merge-request-pending"; s["writable"] = False; return s
    if gs.get("pendingRuleSuites"): s["held"] = "rule-suite-pending"
    if not gl["ok"]:
        s["global"] = {"verdict": gl["verdict"], "reason": gl["reasons"][0]}
        if gl["verdict"] == "STOP" and gl["reasons"][0] == "identity" and by_cfg: s["circuit"] = {"reason": "identity", "local_only": True}
        elif gl["verdict"] == "STOP" and gl["reasons"][0] != "circuit-open" and s["writable"]: s["circuit"] = {"reason": gl["reasons"][0], "local_only": False}
        if gl["reasons"][0] == "main-not-green":   # maybeNotifyStall
            st_ = st.setdefault("_stall", {}); first = (st_.get(g["mainHead"]) or {}).get("firstSeen", g["now"])
            st["_stall"] = {g["mainHead"]: {"firstSeen": first}}
            mins = (g["now"] - first) // 60000
            if s["writable"] and g.get("statusIssue") and mins >= P["mainCiStallNotifyMinutes"]: s["stall"] = {"mainHead": g["mainHead"], "minutes": mins}
        return s
    pulls = api_list(f"repos/{REPO}/pulls?state=open&base={P['base']}&sort=created&direction=asc&per_page=100")
    cands = [p for p in pulls if not p["draft"] and READY in [l["name"] for l in p.get("labels") or []]]
    deep, evals = [], []
    for c in cands:
        n = c["number"]
        if grok_cooldown(n, g["now"]): s["decisions"].append({"pr": n, "head": c["head"]["sha"], "verdict": "WAIT", "reason": "cooldown"}); continue
        pr = collect_pr(n, st, g["now"])
        if pr.get("partial"): s["decisions"].append({"pr": n, "head": pr["headSha"], "verdict": "WAIT", "reason": "not-clean", "mergeStateStatus": pr["mergeStateStatus"]}); continue
        deep.append(pr)
    evals = [(pr, judge(g, pr)) for pr in deep]
    need = sorted([x for x in evals if x[1][0] == "NEED_ARTIFACT"], key=lambda x: x[1][2]["settleFrom"])[:P["maxArtifactFetchesPerRound"]]
    for pr, _ in need:
        try: pr["reviewResult"] = fetch_review_result(pr)
        except Exception: continue
    evals = [(pr, judge(g, pr) if "reviewResult" in pr else j) for pr, j in evals]
    stop = False
    for pr, (v, r, ev) in evals:
        s["decisions"].append({"pr": pr["number"], "head": pr["headSha"], "verdict": v, "reason": r, "settleFrom": ev.get("settleFrom"), "evidence": ev.get("evidence")})
        if stop: break   # round.mjs: STOP trips the circuit and ends the decision loop
        if v == "STOP":
            stop = True
            if s["writable"] and not s["circuit"]: s["circuit"] = {"reason": r, "local_only": False, "detail": f"PR #{pr['number']}(head {pr['headSha']}):{r} {json.dumps(ev.get('evidence'))[:300]}"}
    ready = sorted([(pr, ev) for pr, (v, r, ev) in evals if v == "MERGE"], key=lambda x: (x[1]["settleFrom"], x[0]["number"]))
    if ready and not stop and s["held"] == "rule-suite-pending": s["held"] = "rule-suite-pending"
    elif ready and not stop: s["pick"] = {"number": ready[0][0]["number"], "head": ready[0][0]["headSha"], "settleFrom": ready[0][1]["settleFrom"], "mainHead": g["mainHead"]}
    s["stop"] = stop
    return s

def blockers(p):
    """outbound.py final re-check (merge.mjs finalCheckAndMerge spirit): fresh global + fresh deep collect of this PR.
    [] only when the Grok gate says MERGE, the Grok line could write, PAUSED is absent and head is unchanged."""
    try:
        n = p["number"]; g = collect_global(); st = jload_(STATE)
        pr = collect_pr(n, st, g["now"])
        if pr.get("partial"): return [f"not-clean {pr['mergeStateStatus']}"]
        v, r, ev = judge(g, pr)
        if v == "NEED_ARTIFACT":
            pr["reviewResult"] = fetch_review_result(pr); v, r, ev = judge(g, pr)
        why = [] if v == "MERGE" else [f"{v}:{r}"]
        if not G.can_write(g, CFG.get("statusIssue")): why.append(f"Grok 线不可写(mode={g.get('mode')})")
        if os.path.exists(PAUSED): why.append("PAUSED")
        if p.get("headRefOid") and p["headRefOid"] != pr.get("headSha"): why.append("head moved")
        if grok_cooldown(n, g["now"]): why.append("cooldown")
        gs = grok_state()
        if gs.get("pendingMergeRequests") or gs.get("pendingRuleSuites"): why.append("Grok 线有待确认合并/rule-suite")
        rec = [a for f in sorted(glob.glob(f"{BASE}/out/merger/*.actions.json")) for a in jload_(f).get("actions", [])
               if a.get("type", "").startswith("gh.pr.merge") and a.get("pr") == n and a.get("head") == pr.get("headSha")]
        if not rec or rec[-1].get("mainHead") != g.get("mainHead"): why.append("main-moved（与判定时 main 不同）")
        return why
    except Exception as e: return [f"未核实:{str(e)[:80]}"]

# ---- notify.mjs port (text + markers identical to the Grok line) ----
HUMAN_TEXT = {
  'human-only-path': ['它改了需要人合的文件{path},这类改动合并后会以 CI 或带令牌的自动化执行,首版规定由人合并。', '不需要改代码'],
  'ghost-json-sensitive-field': ['它改了插件清单 `cindyplugin/ghost.json` 里由人把关的字段{keys}(版本号、插件身份、兼容版本、挂载点、权限等)。', '不需要改代码;如果不是有意改这些字段,请改回'],
  'release-head': ['它的源分支是发布分支或 main 本身,机器人不把整条长期分支压进 main。', '请从普通分支开 PR'],
  'skip-ci-marker': ['标题或提交信息里有跳过 CI 的标记,合进 main 后 main 的检查不会运行。', '请去掉标记后重新推送'],
  'too-many-commits': ['提交数超过 250 个,GitHub 接口读不全,机器人没法逐条核对。', '可以压缩提交后重新推送,或请维护者手动合并'],
  'base-changed': ['这个 PR 改过目标分支,GitHub 不会因此重跑 CI,现在的检查结果是按旧目标分支跑的。', '请推一个新提交,或关闭后重开 PR;不要在旧的 run 上点重跑'],
  'post-report-comment': ['审查通过之后有人留言,机器人不替人判断。', '请先回复留言;确认没问题后,由维护者手动合并'],
  'stale-merge-base': ['分支落后 main 且分叉点超过 72 小时,旧 base 上的检查结果不代表和现在的 main 兼容。', '请把分支更新到最新 main,更新后会自动重审'],
  'changes-requested': ['有审查人的 Request Changes 还没撤销。', '请处理意见后请对方重新审查或撤销'],
  'review-protected-path': ['它改了受保护路径,审查结论来自人工放行。', '不需要改代码'],
  'review-not-clean': ['审查结论包显示这一轮不是干净通过(例如 Approve 被拦)。', '请看审查机在 PR 上的说明'],
  'review-artifact-missing': ['审查 run 的结论包已过期或不存在,机器人核对不了审查结论。', '可以请审查机重审,或请维护者手动合并'],
  'excluded-author': ['作者在自动合并的排除名单里。', '不需要改代码'],
}
def human_notice_body(pr, head, reason, evidence=None):
    evidence = evidence or {}
    why, action = HUMAN_TEXT.get(reason, [f"没有通过自动合并的检查({reason})。", "请看下面的机器核对段"])
    path = f"(`{evidence['path']}`)" if evidence.get("path") else ""
    keys = "(" + "、".join(f"`{k}`" for k in evidence["ghostKeys"]) + ")" if evidence.get("ghostKeys") else ""
    return "\n".join([f"这个 PR 不会被自动合并:{why.replace('{path}', path).replace('{keys}', keys)}审查通过后,请 Art Support 维护者手动合并。", "",
                      f"对作者:{action}。", "", f"<!-- mivo-automerge:notice pr={pr} head={head} reason={reason} -->"])
def delivery_marker(key): return f"<!-- mivo-automerge:delivery id={hashlib.sha256(key.encode()).hexdigest()} -->"
def stall_body(main_head, minutes):
    return "\n".join([f"main 最新提交 `{main_head[:7]}` 的 CI 已经 {minutes} 分钟没有通过,自动合并在等它变绿,不会合下一个 PR。", "",
                      f"如果失败的只是 Windows 偶发作业,请 {' '.join('@' + m for m in P['maintainers'])} 之一在 Actions 页面重跑失败的作业。", "",
                      f"<!-- mivo-automerge:stall main={main_head} -->"])
def circuit_body(reason, detail):
    return "\n".join([f"自动合并已熔断:{reason}。机器人从现在起只判不写,不会再合并任何 PR。", "", f"证据:{detail}", "",
                      f"请 {' '.join('@' + m for m in P['maintainers'])} 处理原因后,去掉本 issue 的 `automerge:circuit-open` 标签,并删除 box 上的 `/workspace/mivo-merger/PAUSED`。机器人不会自动复位。", "",
                      f"<!-- mivo-automerge:circuit reason={reason} -->"])
def grok_state(): return jload_(f"{MM}/state/state.json")
def grok_merges_today(day):
    n = 0
    try:
        for l in open(f"{MM}/state/merges.jsonl"):
            m = json.loads(l); at = m.get("at")
            if at and dt.datetime.fromtimestamp(at / 1000, CST).date().isoformat() == day: n += 1
    except FileNotFoundError: pass
    return n

def jload_(f):
    try: return json.load(open(f))
    except Exception: return {}

def main():
    d = today(); t = now_cst(); out = f"{BASE}/out/merger/{d}.md"
    st = jload_(STATE); s = run_round(st); write_atomic(STATE, json.dumps(st, indent=1))
    g = s["g"]; paused = os.path.exists(PAUSED)
    L = [f"## {t.strftime('%H:%M')} 判定（Pi 线，gate.mjs 移植；PAUSED={'在' if paused else '不在'}；Grok mode={g.get('mode')}；可写={s['writable']}）"]
    if g.get("incomplete"): L.append(f"- 读不全：{sorted(set(g['incomplete']))}")
    acts = []
    if s.get("skipped"): L.append(f"- 跳过：{s['skipped']}（额度预留）")
    if s.get("held"): L.append(f"- 暂停合并：{s['held']}（Grok 线状态）")
    if s.get("circuit"):
        c = s["circuit"]; L.append(f"- 熔断：{c['reason']}（Grok 线会{'只写本机 PAUSED' if c['local_only'] else '写 PAUSED + #1027 加标签/评论'}；Pi 线只记动作，不碰 PAUSED）")
        if not c["local_only"]:
            acts.append({"key": f"circuit:{c['reason']}:{today()}", "type": "gh.issue.circuit", "issue": CFG.get("statusIssue"), "label": P["circuitLabel"],
                         "text": circuit_body(c["reason"], c.get("detail") or f"全局门 {c['reason']}"), "also": "write local PAUSED (tripCircuit)"})
    if s.get("stall"):
        key = f"stall:{s['stall']['mainHead']}"
        if key not in (grok_state().get("notices") or {}):
            acts.append({"key": key, "type": "gh.issue.comment", "issue": CFG.get("statusIssue"), "notice_key": key, "delivery_marker": delivery_marker(key),
                         "text": stall_body(s["stall"]["mainHead"], s["stall"]["minutes"]) + "\n" + delivery_marker(key)})
    if s["global"]: L.append(f"- 全局：{s['global']['verdict']} {s['global']['reason']}（本轮不判 PR）")
    else:
        for x in s["decisions"]: L.append(f"- #{x['pr']}@{x['head'][:7]} {x['verdict']} {x['reason']}")
        L.append(f"- 选中：{s['pick']['number'] if s['pick'] else '无'}{'（有 STOP，本轮不合）' if s.get('stop') else ''}")
        if s["writable"]:
            gn = grok_state().get("notices") or {}
            for x in s["decisions"]:   # round.mjs: HUMAN notices are delivered before the merge
                if x["verdict"] == "HUMAN":
                    key = f"{x['pr']}:{x['head']}:{x['reason']}"
                    if key in gn: continue   # Grok line already delivered this notice
                    acts.append({"key": f"comment:{x['pr']}:{hashlib.sha256(key.encode()).hexdigest()[:12]}", "type": "gh.pr.comment", "pr": x["pr"],
                                 "notice_key": key, "delivery_marker": delivery_marker(key),
                                 "text": human_notice_body(x["pr"], x["head"], x["reason"], x.get("evidence")) + "\n" + delivery_marker(key) + "\n<!-- mivo-merger -->",
                                 "dedupe": "notice_key 同 Grok state.notices；PR 评论里的 delivery_marker 同 Grok deliverNotice"})
            if s["pick"] and not s.get("circuit"): acts.append({"key": f"merge:{s['pick']['number']}:{s['pick']['head']}", "type": "gh.pr.merge --squash", "pr": s["pick"]["number"],
                                       "head": s["pick"]["head"], "mainHead": s["pick"]["mainHead"],
                                       "dedupe": "outbound h_merge: MERGED=done, head moved=stale, 执行前 blockers() 全量复核 + main-moved"})
        else: L.append("- 不写动作：" + ("PAUSED" if paused else f"Grok 线当前不可写（mode={g.get('mode')}，需 canary/on + 主机/熔断/状态 issue 都对）"))
    record_actions("merger", d, acts)
    p0 = []
    for p in merged_prs(d):
        f, r = load_report(p["number"])
        if r and r.get("pr") and r["pr"].get("severity") == "P0": p0.append(p["number"])
        if not r: p0.append(f"{p['number']}(报告未出)")
    L.append(f"- 巡检：今日合并中 P0/缺报告：{p0 or '无'}")
    old = open(out).read() if os.path.exists(out) else f"# 合并官判定 {d}（Pi line）\n"
    write_atomic(out, old + "\n".join(L) + "\n")
if __name__ == "__main__": main()
