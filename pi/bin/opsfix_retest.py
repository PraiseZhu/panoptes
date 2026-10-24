"""opsfix.py retest — fix-PR original-case retest (Pi line, 2026-10-23). See opsfix.py docstring.
Pure helpers (pick_targets, blame_lines, line_coverage, effective_retest, apply_ledger, apply_report, handoff_actions,
select_fix_prs, settle) are unit-tested in test/test_opsfix_retest.py; the runners shell out (git/npm/vitest/pi-auto).
10-23 evening: which-retest-decides rule (fail first, then latest `at`); unmatched results logged + counted; retest scope =
fix PRs merged after the last successful run (out/ops/retest-state.json watermark), lock/low memory -> rc 75 (retry);
after the ledger write-back, handoff issue close/reopen become outbound actions (out/opsfix/<today>.actions.json)."""
import json, os, re, glob, shutil, subprocess, fcntl, hashlib, time, datetime as dt
from lib import QA, RPT, BASE, REPO, CST, now_cst, write_atomic, merged_prs, pi, log, gh, record_actions, model_env

import workroot as W
# 10-24 (plan §4.1): the bot's own mivo clone (shared with prfix, same repo lock) as object store; worktrees marked under
# PI_WORK/wt/opsfix, node_modules by lock hash in PI_WORK/cache/nm (janitor keeps the newest two and every linked one)
MIVO = W.clone_dir("mivo")
WORK = W.wt_root("opsfix")
QA_LOCK = f"{QA}/argus-root/nightly.lock"         # same flock panoptes run-nightly.sh takes (exec 9>)
STATE = f"{BASE}/out/ops/retest-state.json"       # watermark + done/retry fix PRs (see select_fix_prs / settle)
ROUTES = "/workspace/panoptes/report/handoff-routes.json"
BY = "Mivo QA 运维 (pi-line opsfix retest)"
SEV_OK = ("P0", "P1", "P2")
FP_RE = re.compile(r"mq1-[0-9a-f]{20}")
RC_RETRY = 75                                     # EX_TEMPFAIL: skipped (lock / memory); nothing consumed, next run retries
OVERLAP = dt.timedelta(hours=6)                   # re-scan before the watermark (gh search lag); done set prevents redo
MAX_ATTEMPTS = 3                                  # a fix PR whose targets stay blocked is given up after 3 runs
TRANSIENT = ("本轮模型调用已达上限",)             # blocked for budget reasons: does not count as an attempt
PERMANENT = ("没有合并报告", "原报告缺", "原 PR 的新增代码文件", "可执行新增行为 0", "没有轻量复跑")  # retry can't help

def qa_refs(body): return [int(x) for x in re.findall(r"QA[:：]\s*#(\d+)", body or "")]

def load_pr(n):
    try: return json.load(open(f"{RPT}/PR-{n}.json"))["prs"][0]
    except Exception: return None

def pick_targets(fix_body, orig_pr, orig_report):
    """Which of the source report's findings this fix PR claims to fix. Returns list of findings."""
    fs = [x for x in (orig_report or {}).get("findings", []) if x.get("severity") in SEV_OK
          and (x.get("calibration") or {}).get("to") != "误报"]
    named = set(FP_RE.findall(fix_body or ""))
    hit = [x for x in fs if x.get("fingerprint") in named]
    if hit: return hit
    body = fix_body or ""
    kinds = [k for k in ("coverage-gate", "code-review") if k in body]
    if not kinds and re.search(r"新增行覆盖|覆盖率|coverage", body, re.I): kinds = ["coverage-gate"]
    if kinds:
        hit = [x for x in fs if x.get("source") in kinds]
        if hit: return hit
    return fs

def ledger_path():
    fs = sorted(glob.glob(f"{QA}/ledger/daily/*.json")); fs = [f for f in fs if re.search(r"/\d{4}-\d{2}-\d{2}\.json$", f)]
    return fs[-1] if fs else None

def targets_for(p):
    tg = []
    for o in qa_refs(p.get("body")):
        rep = load_pr(o)
        for f in pick_targets(p.get("body"), o, rep): tg.append((o, f))
        if not rep: tg.append((o, None))
    return tg

def plan(day, only=None):
    """[(fix_pr_dict, [(orig_pr, finding)])] for fix PRs merged on `day` with QA: #N (pm_extra.retest rule)."""
    return [(p, targets_for(p)) for p in merged_prs(day) if qa_refs(p.get("body")) and not (only and p["number"] not in only)]

# ---------- scope: watermark (pure parts) ----------
def parse_at(s):
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=CST)
    except Exception: return None

def seed_state(runs_lines, today_d):
    """First run without retest-state.json: continue after the last manual/cron fix-retest recorded in
    reports/merged/manual-runs.jsonl (watermark = 00:00 CST of the day after its data day, its fix PRs = done);
    nothing recorded -> yesterday 00:00 CST (= the old 'data day = yesterday' behaviour)."""
    last = wm = None
    for ln in runs_lines:
        try:
            r = json.loads(ln)
            if r.get("kind") != "fix-retest": continue
            # watermark runs record watermark_to; day-mode runs record the data day (-> 00:00 CST of the next day)
            w = parse_at(r["watermark_to"]) if r.get("watermark_to") else dt.datetime.combine(dt.date.fromisoformat(r["day"]) + dt.timedelta(days=1), dt.time(), CST)
            if w: last, wm = r, w
        except Exception: continue
    if last:
        done = {str(n): {"at": last.get("at"), "seeded_from": "manual-runs.jsonl"} for n in last.get("fix_prs", [])}
    else:
        wm = dt.datetime.combine(today_d - dt.timedelta(days=1), dt.time(), CST); done = {}
    return {"watermark": wm.isoformat(timespec="seconds"), "done": done, "retry": {}}

def select_fix_prs(prs, state, only=None):
    """prs: merged PRs (number, mergedAt, body, ...) from at least watermark-OVERLAP. Keep fix PRs (QA: #N) that are not done
    and (merged after watermark-OVERLAP or in the retry set); `only` forces those numbers even if done. Oldest merge first,
    so retest `at` follows merge order (effective_retest's 'latest at')."""
    lo = parse_at(state.get("watermark")) - OVERLAP if state.get("watermark") else None
    done, retry = state.get("done") or {}, state.get("retry") or {}
    out = []
    for p in prs:
        n = p["number"]
        if not qa_refs(p.get("body")): continue
        if only:
            if n in only: out.append(p)
            continue
        if str(n) in done: continue
        t = parse_at(p.get("mergedAt"))
        if str(n) in retry or lo is None or (t and t >= lo): out.append(p)
    return sorted(out, key=lambda p: (p.get("mergedAt") or "", p["number"]))

def settle(state, results_by_fix, heads, stamp_iso):
    """Mark each attempted fix PR done (all targets pass/fail, or blocked for a reason retrying can't fix, or
    MAX_ATTEMPTS reached) or keep it in retry (transient block). Budget blocks (model cap) don't count as an attempt.
    Mutates and returns state; returns (done_now, retry_now) lists too."""
    done, retry = state.setdefault("done", {}), state.setdefault("retry", {})
    done_now, retry_now = [], []
    for n, rs in results_by_fix.items():
        k = str(n); counts = {x: sum(r["result"] == x for r in rs) for x in ("pass", "fail", "blocked")}
        blocked = [r for r in rs if r["result"] == "blocked"]
        transient = [r for r in blocked if not any(m in str(r.get("reason")) for m in PERMANENT)]
        budget_only = transient and all(any(m in str(r.get("reason")) for m in TRANSIENT) for r in transient)
        att = (retry.get(k) or {}).get("attempts", 0) + (0 if budget_only else 1)
        if not transient or att >= MAX_ATTEMPTS:
            done[k] = {"at": stamp_iso, "head": heads.get(n), "results": counts, **({"gave_up_after": att} if transient else {})}
            retry.pop(k, None); done_now.append(n)
        else:
            retry[k] = {"attempts": att, "last_at": stamp_iso, "why": str(transient[0].get("reason"))[:160]}; retry_now.append(n)
    return state, done_now, retry_now

def merged_since(lo):
    """Merged PRs with mergedAt >= lo (aware datetime), newest first (gh search is minute-granular; re-filtered exactly)."""
    a = lo.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    prs = gh(["pr", "list", "-R", REPO, "--state", "merged", "--limit", "300", "--search", f"merged:>={a}",
              "--json", "number,title,author,mergedAt,mergedBy,headRefOid,body"])
    return [p for p in prs if p["mergedAt"] >= a]

def load_state(path=None):
    path = path or STATE
    try: return json.load(open(path))
    except FileNotFoundError: return None
    except Exception as e: raise RuntimeError(f"{path} unreadable ({e}); fix or delete it (a broken state is never treated as empty)")

# ---------- git / worktree ----------
def sh(cmd, cwd=None, timeout=1800, env=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    return r.returncode, r.stdout, r.stderr

RUN_LOCK = "/tmp/pi-fallback-opsfix.lock"      # run.sh's job lock: held for the whole opsfix run (the marker names it)
def clone_locked():   # same clone as the parallel prfix runs -> the same metadata lock (workroot.repo_lock("mivo"))
    os.makedirs(os.path.dirname(W.lock_file("mivo.repo.lock")), exist_ok=True)
    f = open(W.lock_file("mivo.repo.lock"), "a"); fcntl.flock(f, fcntl.LOCK_EX); return f

def worktree(sha):
    d = f"{WORK}/wt-{sha[:12]}"
    W.ensure_clone("mivo")
    lk = clone_locked()
    try:
        if os.path.isdir(f"{d}/.git") or os.path.isfile(f"{d}/.git"):
            W.retake(d); return d                         # kept from an earlier run: this run's now (janitor leaves it alone)
        os.makedirs(WORK, exist_ok=True)
        sh(["git", "fetch", "-q", "origin", sha], cwd=MIVO, timeout=600)
        rc, o, e = sh(["git", "worktree", "add", "--detach", "-f", d, sha], cwd=MIVO, timeout=900)
        if rc == 0: W.mark(d, "opsfix", repo="mivo", clone=MIVO, ticket=sha[:12], base_sha=sha, lock=RUN_LOCK)
    finally: lk.close()
    if rc: raise RuntimeError(f"worktree {sha[:10]}: {e[-300:]}")
    return d

def drop_worktree(d):
    lk = clone_locked()
    try: W.trash(d); sh(["git", "worktree", "prune"], cwd=MIVO)    # rename into the trash (instant), rm -rf in the background
    finally: lk.close()

def ensure_node_modules(wt):
    lock = f"{wt}/package-lock.json"
    h = hashlib.sha256(open(lock, "rb").read()).hexdigest()[:16]
    cache = W.nm_cache(h)
    if not os.path.isdir(f"{cache}/node_modules"):
        os.makedirs(cache, exist_ok=True)
        for f in ("package.json", "package-lock.json", ".npmrc"):
            if os.path.exists(f"{wt}/{f}"): shutil.copy2(f"{wt}/{f}", cache)
        rc, o, e = sh(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--prefer-offline"], cwd=cache, timeout=1800)
        if rc: shutil.rmtree(cache, ignore_errors=True); raise RuntimeError(f"npm ci rc={rc}: {e[-400:]}")
    if not os.path.exists(f"{wt}/node_modules"): os.symlink(f"{cache}/node_modules", f"{wt}/node_modules")

def orig_commits(merge_sha, wt):
    """Commits that brought the original PR into main: squash → {merge}; merge commit → its PR-side commits."""
    rc, o, _ = sh(["git", "rev-list", "--parents", "-n1", merge_sha], cwd=wt)
    parts = o.split()
    if len(parts) <= 2: return {merge_sha}
    rc, o, _ = sh(["git", "rev-list", f"{parts[1]}..{parts[2]}"], cwd=wt)
    return set(o.split()) | {merge_sha}

def blame_lines(porcelain, commits):
    """Parse `git blame --line-porcelain`; return sorted final line numbers authored by any of `commits`."""
    out = []; cur = None
    for line in porcelain.splitlines():
        m = re.match(r"^([0-9a-f]{40}) \d+ (\d+)", line)
        if m: cur = (m.group(1), int(m.group(2))); continue
        if line.startswith("\t") and cur:
            if cur[0] in commits: out.append(cur[1])
            cur = None
    return sorted(out)

def line_coverage(file_cov, lines=None):
    """istanbul file entry → {line: hit_bool}. A line is executable if a statement starts on it; for `lines`
    (the original PR's lines) that start no statement, the innermost statement spanning the line decides."""
    res = {}; spans = []
    for k, loc in (file_cov.get("statementMap") or {}).items():
        a, b = loc["start"]["line"], loc["end"]["line"]; hit = (file_cov.get("s") or {}).get(k, 0) > 0
        res[a] = res.get(a, False) or hit; spans.append((b - a, a, b, hit))
    for ln in lines or ():
        if ln in res: continue
        inner = min((sp for sp in spans if sp[1] <= ln <= sp[2]), default=None)
        if inner: res[ln] = inner[3]
    return res

# ---------- runners ----------
def run_coverage(fix, orig_pr, f, ev_base):
    orep = load_pr(orig_pr) or {}
    merge = orep.get("merge_sha"); thr = ((f.get("evidence") or {}).get("thresholds") or {}).get("new_code", 90)
    files = [x["file"] for x in (f.get("evidence") or {}).get("new_code_files") or []] or \
            [x["file"] for x in ((orep.get("coverage_gate") or {}).get("new_code") or {}).get("files", [])]
    if not merge or not files: return "blocked", "原报告缺 merge_sha 或 new_code_files，没法定位原 PR 的新增行", {}
    head = fix["headRefOid"]; wt = worktree(head)
    files = [x for x in files if os.path.exists(f"{wt}/{x}")]
    if not files: return "blocked", "原 PR 的新增代码文件在修复 PR head 上都不存在", {}
    ensure_node_modules(wt)
    covdir = f"{ev_base}.cov"; shutil.rmtree(covdir, ignore_errors=True)
    cmd = ["node", "scripts/run-vitest.mjs", "related", "--run", *files, "--coverage.enabled", "--coverage.provider=v8",
           "--coverage.reporter=json", "--coverage.reportOnFailure", f"--coverage.reportsDirectory={covdir}", "--coverage.all=false",
           *[f"--coverage.include={x}" for x in files], "--coverage.thresholds.lines=0", "--passWithNoTests"]
    rc, o, e = sh(cmd, cwd=wt, timeout=2400, env={k: v for k, v in model_env({"CI": "1"}).items() if k != "DEEPSEEK_API_KEY"})   # 10-24: no tokens for tests
    open(f"{ev_base}.vitest.log", "w").write(o[-20000:] + "\n--- stderr ---\n" + e[-20000:])
    cf = f"{covdir}/coverage-final.json"
    if not os.path.exists(cf): return "blocked", f"vitest rc={rc} 没产出 coverage-final.json（见 {ev_base}.vitest.log）", {"vitest_rc": rc}
    cov = json.load(open(cf)); commits = orig_commits(merge, wt)
    tot = covd = 0; per = []
    for x in files:
        _, por, _ = sh(["git", "blame", "--line-porcelain", head, "--", x], cwd=wt)
        mine = set(blame_lines(por, commits))
        entry = cov.get(f"{wt}/{x}") or next((v for k, v in cov.items() if k.endswith("/" + x)), None)
        lc = line_coverage(entry, mine) if entry else {}
        ex = [l for l in mine if l in lc]; hit = [l for l in ex if lc[l]]
        tot += len(ex); covd += len(hit)
        per.append({"file": x, "orig_lines": len(mine), "executable": len(ex), "covered": len(hit),
                    "uncovered": sorted(set(ex) - set(hit))[:60], "in_coverage": bool(entry)})
    if tot == 0: return "blocked", "原 PR 留在 head 上的可执行新增行为 0（blame/覆盖映射为空），无法判定", {"files": per, "vitest_rc": rc}
    pct = round(100 * covd / tot, 2)
    det = {"files": per, "pct": pct, "threshold": thr, "covered": covd, "executable": tot, "vitest_rc": rc,
           "method": "vitest related --coverage(v8) + git blame（原 PR 合入提交）", "head": head}
    fails = re.findall(r"^ FAIL  (\S+)", o, re.M); det["failed_tests"] = sorted(set(fails))[:20]
    note = ((f"；另有 {len(set(fails))} 个相关测试文件失败（{', '.join(sorted(set(fails))[:3])}）" if fails else f"；vitest rc={rc}（无失败用例，多为未处理错误，如本机没起 :3000）")
            + "，不影响覆盖判定，见 vitest.log") if rc else ""
    return ("pass" if pct >= thr else "fail"), f"原 PR 新增行覆盖 {covd}/{tot} = {pct}%（门槛 {thr}%）{note}", det

def run_review(fix, orig_pr, f, ev_base):
    head = fix["headRefOid"]; wt = worktree(head)
    ev = f.get("evidence") or {}
    prompt = (f"你在复测一条 QA 代码审查发现，代码目录是修复 PR #{fix['number']} 的 head（{head[:10]}），只读。\n"
              f"原问题来自 PR #{orig_pr} 的 QA 报告（审查时的 sha {ev.get('reviewed_sha','?')[:10]}）：\n"
              f"- 标题：{f.get('title')}\n- 位置：{f.get('file')}:{f.get('line')} {f.get('symbol') or ''}\n"
              f"- 证据：{ev.get('evidence','')}\n- 失败场景：{ev.get('failure_scenario','')}\n- 建议：{ev.get('recommendation','')}\n\n"
              "请读当前目录里的相关代码（必要时读调用方和新增测试），判断这条失败场景在当前代码上还能不能发生。"
              "只看这一条，不要审别的。最后一行只输出一个 JSON："
              '{"result":"pass|fail|unsure","evidence":"文件:行号 + 一两句理由"}。pass=已修复不能再发生；fail=仍能发生。')
    text, task = pi("review", wt, prompt, f"opsfix-retest-{fix['number']}-{f.get('fingerprint')}", timeout=900)
    open(f"{ev_base}.review.md", "w").write(text)
    m = None
    for line in reversed(text.strip().splitlines()):
        try: m = json.loads(line.strip().strip("`")); break
        except Exception: continue
    if not isinstance(m, dict) or m.get("result") not in ("pass", "fail", "unsure"):
        return "blocked", f"审查模型输出无法解析（task {task}）", {"task": task}
    res = {"pass": "pass", "fail": "fail"}.get(m["result"], "blocked")
    return res, (m.get("evidence") or "")[:400] + ("" if res != "blocked" else "（模型判 unsure）"), {"task": task, "model": m}

def precheck(fix_n, fp, source=None):
    # coverage-gate fingerprints are generic (same fp for every PR's own coverage gap) → no fp precheck for them
    if source == "coverage-gate": return None
    rep = load_pr(fix_n)
    if rep and any(x.get("fingerprint") == fp for x in rep.get("findings", [])):
        return "fail", f"修复 PR 自己的合并报告仍有同一问题（{fp}）"
    return None

# ---------- write-back (pure) ----------
def apply_report(doc, fix_n, entries):
    pr = next(p for p in doc["prs"] if p.get("number") == fix_n)
    cur = [x for x in pr.get("retest") or [] if not any(x.get("fingerprint") == e["fingerprint"] and x.get("original_pr") == e["original_pr"] for e in entries)]
    pr["retest"] = cur + entries
    return doc

def effective_retest(rts):
    """Which retest decides a row with several fix PRs (Praise 10-23; build-ledger.mjs effectiveRetest is the same rule):
    any fail wins (the latest fail by `at`); otherwise the latest `at`. Entries without a parsable `at` (build-ledger's
    pending ones) count as oldest; ties go to the later entry in the list (fix PR number order)."""
    if not rts: return None
    fails = [x for x in rts if x.get("result") == "fail"]
    pool = fails or list(rts)
    lo = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    best = pool[0]
    for x in pool[1:]:
        if (parse_at(x.get("at")) or lo) >= (parse_at(best.get("at")) or lo): best = x
    return best

MAX_AUTO_ROUNDS = 2
STATUSES = ("fixed", "fix_merged_pending_retest", "fix_pr_open", "concluded", "open")
STZH = {"fixed": "已修复", "fix_merged_pending_retest": "修复已合并，待复测", "fix_pr_open": "修复 PR 未合并", "concluded": "有结论", "open": "未修无结论"}

def _needs_you_entry(row):
    return {"key": row["key"], "pr": row["pr"], "author": row.get("author"), "level": row.get("level"), "summary": row.get("summary"),
            "fix_round": row.get("fix_round"), "fix_prs": sorted({x["pr"] for x in row.get("retests") or []}),
            "handoff_issue": (row.get("handoff_issue") or {}).get("url"), "reason": f"已自动修 {row.get('fix_round')} 轮仍未通过复测，转人工"}

def recompute_summary(led):
    """summary + needs_you exactly as build-ledger.mjs computes them (keeps any extra summary keys)."""
    rows = led["rows"]; sm = led.setdefault("summary", {})
    counts = {k: 0 for k in STATUSES}; open_by = {k: 0 for k in ("P0", "P1", "P2", "P3", "unknown")}
    for r in rows:
        counts[r.get("status")] = counts.get(r.get("status"), 0) + 1
        if r.get("status") == "open": open_by[r.get("level") if r.get("level") in open_by else "unknown"] += 1
    rounds = sorted({r.get("fix_round") or 0 for r in rows})
    old = {x.get("key"): x for x in led.get("needs_you") or []}
    led["needs_you"] = [old.get(r["key"]) if old.get(r["key"]) and old[r["key"]].get("fix_round") == r.get("fix_round") else _needs_you_entry(r)
                        for r in rows if r.get("needs_human")]
    sm.update(total=len(rows), by_status=counts, open_by_level=open_by,
              fixed_not_merged=sum(1 for r in rows if r.get("status") == "fixed" and r.get("merged") is not True),
              fix_round={str(k): sum(1 for r in rows if (r.get("fix_round") or 0) == k) for k in rounds},
              retest={k: sum(1 for r in rows if r.get("retest") == k) for k in ("pass", "fail", "pending")},
              needs_you=len(led["needs_you"]))
    return sm

def apply_ledger(led, results, unmatched=None):
    """results: [{fix_pr, original_pr, fingerprint, result, reason, evidence, at}] → mutate ledger rows like build-ledger.
    Returns touched row keys. Results with no ledger row are appended to `unmatched` (if given) — never silently dropped."""
    touched = []
    for r in results:
        if r["result"] not in ("pass", "fail", "blocked"): continue
        row = next((x for x in led["rows"] if x.get("pr") == r["original_pr"] and x.get("fingerprint") == r["fingerprint"]), None) if r.get("fingerprint") else None
        if not row:
            if unmatched is not None:
                unmatched.append({"fix_pr": r["fix_pr"], "original_pr": r["original_pr"], "fingerprint": r.get("fingerprint"), "result": r["result"],
                                  "why": "原 PR 无合并报告（没有 fingerprint）" if not r.get("fingerprint") else "台账无此行（未通知/不在台账范围）"})
            continue
        fps = row.setdefault("fix_prs", []) or []
        if not any(x.get("pr") == r["fix_pr"] for x in fps):
            fps.append({"pr": r["fix_pr"], "merged": True, "state": "merged", "via": "qa-ref(opsfix retest)"}); row["fix_prs"] = fps
        # build-ledger vocabulary is pass|fail|pending; blocked keeps pending but records why
        res = r["result"] if r["result"] in ("pass", "fail") else "pending"
        ent = {"pr": r["fix_pr"], "result": res, "reason": r["reason"] if res != "pending" else f"复测受阻：{r['reason']}",
               "report": f"reports/merged/PR-{r['fix_pr']}.json", "evidence": r["evidence"], "by": BY, "at": r["at"]}
        rts = [x for x in row.get("retests") or [] if x.get("pr") != r["fix_pr"]] + [ent]
        rts.sort(key=lambda x: x["pr"]); row["retests"] = rts
        eff = effective_retest(rts)          # 10-23: was rts[-1] (highest fix PR number) -> a later pending hid a fail (#1089)
        row["retest"], row["retest_pr"] = eff["result"], eff["pr"]
        st = row.get("status")
        if st != "concluded": row["fix_round"] = len(rts)   # build-ledger: one retest entry per merged / reported fix PR
        # build-ledger would have called the row "fixed" once a fix PR is tied to it, so an un-overridden open row moves too;
        # concluded rows (hold / 误报 / receipt / comment / override) keep their status
        if st in ("fix_merged_pending_retest", "fixed", "fix_pr_open") or (st == "open" and not row.get("override")):
            if row["retest"] == "pass":
                row.update(status="fixed", status_zh=STZH["fixed"], retest_confirmed=True, retest_reopened=None)
                if row.get("fix_pr") is None: row.update(fix_pr=eff["pr"], merged=True)
            elif row["retest"] == "fail":
                row.update(status="open", status_zh=STZH["open"], retest_reopened=True, retest_confirmed=None, merged=None)
        row["needs_human"] = row.get("status") == "open" and (row["fix_round"] > MAX_AUTO_ROUNDS or (row["fix_round"] == MAX_AUTO_ROUNDS and row["retest"] == "fail"))
        touched.append(row["key"])
    recompute_summary(led)
    return touched

def handoff_owner_dir(repo):
    return repo.split("/")[0]

def handoff_actions(led, issue_state=None):
    """apply-fix-retests.mjs as outbound actions (runbook「修复 PR 的测试规则」2): row fixed + effective retest pass ->
    gh.issue.close (status:fixed, comment); row retest_reopened + effective retest fail -> gh.issue.reopen (status:open,
    comment, still the original author). issue_state(repo, n) -> 'OPEN'/'CLOSED'/None(unknown); a known terminal state
    skips the action like the Grok tool does. Keys carry the fix PR, so a later round gets its own action."""
    acts = []
    for r in led.get("rows", []):
        ho = r.get("handoff_issue")
        if not ho or not ho.get("repo") or not ho.get("issue"): continue
        eff = effective_retest(r.get("retests"))
        if not eff: continue
        repo, n = ho["repo"], int(ho["issue"])
        rec = f"{QA}/handoff/{handoff_owner_dir(repo)}/issues.json"
        if r.get("status") == "fixed" and eff["result"] == "pass":
            if issue_state and issue_state(repo, n) == "CLOSED": continue
            acts.append({"key": f"handoff-close:{repo}#{n}:fix{eff['pr']}", "type": "gh.issue.close", "repo": repo, "issue": n, "url": ho.get("url"),
                         "comment": f"复测通过：修复 PR #{eff['pr']} 的 QA 报告（{eff.get('report')}）显示原问题已解决，关闭。",
                         "add_labels": ["status:fixed"], "remove_labels": ["status:open"],
                         "record": {"file": rec, "set": {"status": "fixed", "fix_pr": eff["pr"]}, "stamp": "closed_at"},
                         "row": r["key"], "pr": r["pr"], "fix_pr": eff["pr"], "dedupe_against": "gh issue state (CLOSED = done)"})
        elif r.get("retest_reopened") and eff["result"] == "fail":
            if issue_state and issue_state(repo, n) == "OPEN": continue
            acts.append({"key": f"handoff-reopen:{repo}#{n}:fix{eff['pr']}", "type": "gh.issue.reopen", "repo": repo, "issue": n, "url": ho.get("url"),
                         "comment": f"复测未通过：修复 PR #{eff['pr']} 的 QA 报告仍有原问题（{r.get('fingerprint')}），重新打开，仍归 #{r['pr']} 作者 @{r.get('author')}。"
                                    f"第 {r.get('fix_round')} 轮{'，已转人工' if r.get('needs_human') else ''}。",
                         "add_labels": ["status:open"], "remove_labels": ["status:fixed"],
                         "record": {"file": rec, "set": {"status": "open", "fix_round": r.get("fix_round")}, "stamp": "reopened_at"},
                         "row": r["key"], "pr": r["pr"], "fix_pr": eff["pr"], "dedupe_against": "gh issue state (OPEN = done)"})
    return acts

def gh_issue_state(repo, n):
    try: return gh(["issue", "view", str(n), "-R", repo, "--json", "state"]).get("state")
    except Exception as e: log(f"issue state {repo}#{n} unknown ({str(e)[:80]}) -> action kept, outbound re-checks"); return None

# ---------- command ----------
def mem_mb(): return int(open("/proc/meminfo").read().split("MemAvailable:")[1].split()[0]) // 1024

def take_lock(lk, wait_s):
    """flock QA_LOCK, polling up to wait_s seconds (catch-up usually exits within seconds)."""
    t_end = time.time() + max(0, wait_s)
    while True:
        try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB); return True
        except BlockingIOError:
            if time.time() >= t_end: return False
            time.sleep(15)

def save_state(state, stamp):
    keep = stamp - dt.timedelta(days=14)            # the scan window only reaches 6 h before the watermark
    state["done"] = {k: v for k, v in (state.get("done") or {}).items() if not v.get("at") or (parse_at(v["at"]) or stamp) >= keep}
    state["updated_at"] = stamp.isoformat(timespec="seconds")
    write_atomic(STATE, json.dumps(state, ensure_ascii=False, indent=1))

def note_skip(state, why, stamp):
    state["last_skip"] = {"at": stamp.isoformat(timespec="seconds"), "why": why}; save_state(state, stamp)
    print(f"RETRY: {why} — nothing consumed (watermark unchanged); rc={RC_RETRY}")
    return RC_RETRY

def run(a):
    stamp = now_cst(); odir = f"{BASE}/out/ops/retest/{stamp:%Y-%m-%d}"
    only = set(a.pr) if a.pr else None
    state = load_state()
    if state is None:
        runs = open(f"{RPT}/manual-runs.jsonl").read().splitlines() if os.path.exists(f"{RPT}/manual-runs.jsonl") else []
        state = seed_state(runs, stamp.date()); log(f"no retest-state.json -> seeded watermark {state['watermark']} done={sorted(state['done'], key=int)}")
    if a.day:   # explicit data day (manual); done fix PRs are still recorded, the watermark is not moved
        P = sorted(plan(a.day, only), key=lambda t: (t[0].get("mergedAt") or "", t[0]["number"]))
        scope, advance = f"数据日 {a.day}", False
    else:
        if getattr(a, "since", None): state["watermark"] = a.since
        lo = parse_at(state["watermark"]) - OVERLAP
        lo = min([lo] + [t for t in (parse_at(v.get("merged_at")) for v in (state.get("retry") or {}).values()) if t])
        P = [(p, targets_for(p)) for p in select_fix_prs(merged_since(lo), state, only)]
        scope = f"{state['watermark']} 之后合并、尚未复测的修复 PR" + (f"（含重试 {sorted(state['retry'], key=int)}）" if state.get("retry") else "")
        advance = not only
    print(f"retest {scope}: {len(P)} fix PR(s) with QA: #N; mode={'EXECUTE' if a.execute else 'DRY-RUN'}")
    for p, tg in P:
        print(f"  #{p['number']} merged {p.get('mergedAt')} head {p['headRefOid'][:10]} ← " + ", ".join(
            f"#{o}:{(f or {}).get('source','无报告')}:{(f or {}).get('fingerprint','-')}" for o, f in tg))
    if not a.execute: print("DRY: nothing run, nothing written (add --execute)"); return 0
    if not P:
        if advance: state.update(watermark=stamp.isoformat(timespec="seconds"), last_run={"at": stamp.isoformat(timespec="seconds"), "fix_prs": []}); save_state(state, stamp)
        print("nothing to retest"); return 0
    if mem_mb() < 3072: return note_skip(state, f"MemAvailable {mem_mb()}MB < 3072MB", stamp)
    wait = getattr(a, "wait_lock", 1200)
    os.makedirs(odir, exist_ok=True); lk = open(QA_LOCK, "a")
    if not take_lock(lk, wait):
        lk.close(); return note_skip(state, f"QA lock {QA_LOCK} still held after {wait}s (panoptes running)", stamp)
    results = []; model_calls = 0; unmatched = []; lp = None; touched = []; acts = []
    try:
        for p, tg in P:
            n = p["number"]
            for o, f in tg:
                at = now_cst().isoformat(timespec="seconds")
                if f is None:
                    results.append(dict(fix_pr=n, original_pr=o, fingerprint=None, source=None, result="blocked",
                                        reason=f"原 PR #{o} 没有合并报告", evidence=None, at=at)); continue
                fp = f["fingerprint"]; ev_base = f"{odir}/PR{n}-{fp}"
                res = precheck(n, fp, f.get("source")); det = {}
                try:
                    if res: res, why = res
                    elif f.get("source") == "coverage-gate": res, why, det = run_coverage(p, o, f, ev_base)
                    elif f.get("source") == "code-review":
                        if model_calls >= a.max_model: res, why = "blocked", f"本轮模型调用已达上限 {a.max_model}"
                        else: model_calls += 1; res, why, det = run_review(p, o, f, ev_base)
                    else: res, why = "blocked", f"{f.get('source')} 类问题没有轻量复跑（要整步重跑：opsfix backfill --rerun --pr {n}）"
                except Exception as e: res, why = "blocked", f"复测执行出错：{str(e)[:300]}"
                rec = dict(fix_pr=n, original_pr=o, fingerprint=fp, source=f.get("source"), title=f.get("title"), result=res,
                           reason=why, evidence=f"{ev_base}.json", at=at, detail=det)
                write_atomic(f"{ev_base}.json", json.dumps(rec, ensure_ascii=False, indent=1))
                results.append(rec); log(f"#{n} ← #{o} {fp} {f.get('source')}: {res} — {why}")
        # write-back
        for n in sorted({r["fix_pr"] for r in results}):
            ents = [{"fingerprint": r["fingerprint"], "original_pr": r["original_pr"], "result": r["result"], "method": r["source"],
                     "reason": r["reason"], "evidence": r["evidence"], "at": r["at"], "by": BY}
                    for r in results if r["fix_pr"] == n and r["fingerprint"] and r["result"] in ("pass", "fail")]
            f = f"{RPT}/PR-{n}.json"
            if not ents or not os.path.exists(f): continue
            bak = f"{f}.bak-{stamp:%Y%m%d}-retest"
            if not os.path.exists(bak): shutil.copy2(f, bak)
            d = apply_report(json.load(open(f)), n, ents); write_atomic(f, json.dumps(d, ensure_ascii=False, indent=2))
        lp = ledger_path()
        if lp:
            bak = f"{lp}.bak-{stamp:%Y%m%d}-retest"
            if not os.path.exists(bak): shutil.copy2(lp, bak)
            led = json.load(open(lp)); touched = apply_ledger(led, results, unmatched)
            led["retest_writeback"] = {"at": stamp.isoformat(timespec="seconds"), "by": BY, "rows": touched,
                                       "unmatched": len(unmatched), "unmatched_items": unmatched}
            write_atomic(lp, json.dumps(led, ensure_ascii=False, indent=2))
            for u in unmatched: log(f"UNMATCHED #{u['fix_pr']} ← #{u['original_pr']} {u['fingerprint']} {u['result']}: {u['why']} — not written to the ledger")
            log(f"ledger write-back: {len(touched)} row(s), {len(unmatched)} result(s) without a ledger row")
            if not getattr(a, "no_handoff", False):
                acts = handoff_actions(led, gh_issue_state)
                record_actions("opsfix", stamp.date().isoformat(), acts)
                log(f"handoff close/reopen actions: {len(acts)} -> out/opsfix/{stamp:%Y-%m-%d}.actions.json")
        else: log(f"WARN no ledger file under {QA}/ledger/daily -> {len(results)} result(s) not written back")
        open(f"{RPT}/manual-runs.jsonl", "a").write(json.dumps({"kind": "fix-retest", "day": a.day or f"since {state['watermark']}",
            **({"watermark_to": stamp.isoformat(timespec="seconds")} if advance else {}),
            "fix_prs": sorted({r['fix_pr'] for r in results}), "results": {k: sum(r["result"] == k for r in results) for k in ("pass", "fail", "blocked")},
            "unmatched": len(unmatched), "at": stamp.isoformat(timespec="seconds"), "by": BY}, ensure_ascii=False) + "\n")
        by_fix = {}
        for r in results: by_fix.setdefault(r["fix_pr"], []).append(r)
        _, done_now, retry_now = settle(state, by_fix, {p["number"]: p.get("headRefOid") for p, _ in P}, stamp.isoformat(timespec="seconds"))
        for p, _ in P:   # remember merge time of retry entries so the next scan window still covers them
            if str(p["number"]) in state.get("retry", {}): state["retry"][str(p["number"])]["merged_at"] = p.get("mergedAt")
        if advance: state["watermark"] = stamp.isoformat(timespec="seconds")
        state["last_run"] = {"at": stamp.isoformat(timespec="seconds"), "scope": scope, "fix_prs": sorted(by_fix), "done": done_now, "retry": retry_now, "unmatched": len(unmatched)}
        state.pop("last_skip", None); save_state(state, stamp)
    finally:
        fcntl.flock(lk, fcntl.LOCK_UN); lk.close()
        if not a.keep:
            for d in glob.glob(f"{WORK}/wt-*"): drop_worktree(d)
    L = [f"# 修复 PR 原用例复测 · {scope}（{stamp:%Y-%m-%d %H:%M} 北京时间）", "",
         f"台账：{lp}（回写 {len(touched)} 行；台账无对应行 {len(unmatched)} 条）；交接 issue 动作 {len(acts)} 条", "",
         "| 修复 PR | 原 PR | 问题 | 类型 | 结果 | 依据 |", "|---|---|---|---|---|---|"]
    for r in results:
        L.append(f"| #{r['fix_pr']} | #{r['original_pr']} | {(r.get('title') or r.get('fingerprint') or '-')[:40]} | {r.get('source') or '-'} | {r['result']} | {str(r['reason']).replace('|','/')[:160]} |")
    if unmatched:
        L += ["", f"## 没写进台账的结果（{len(unmatched)} 条）"] + [f"- #{u['fix_pr']} ← #{u['original_pr']} {u['fingerprint'] or '-'} {u['result']}：{u['why']}" for u in unmatched]
    if acts:
        L += ["", "## 交接 issue 动作（outbound 执行）"] + [f"- {x['type']} {x['url'] or x['repo'] + '#' + str(x['issue'])}（{x['row'].split('#')[-1]}）" for x in acts]
    write_atomic(f"{odir}.md", "\n".join(L) + "\n"); write_atomic(f"{odir}.json", json.dumps(results, ensure_ascii=False, indent=1))
    print("\n".join(L)); return 0
