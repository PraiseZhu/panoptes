#!/usr/bin/env python3
"""Praise PR bot, infra line: fixes faults of the QA system itself (PraiseZhu/panoptes), deep tier (deepseek-v4.1-flash, thinking max; INFRA_TIER overrides), ONE ticket per run.
(2026-10-23, Praise: 「他还需要修复自动化测试本身的故障问题」「只要内存够就立马修」.) Same conventions as prfix.py.

A ticket = one fault signature  sig = inf1-<20 hex> = sha1(category | stage | normalised message); sig8 = its first 8 hex.
  normalise: drop "missing: ", timestamps / dates / uuids / hex shas / /tmp paths / PR numbers / numbers (3-digit HTTP-like
  codes 100-599 are kept), collapse spaces, lower-case. Catch-up give-ups are cut after "(last: …)".
Sources (all zero-model, read-only; window = last 7 days):
  1 reports/merged/PR-*.json (+ history/PR-*.vK.json, reports/catch-up/PR-*.json): system_health.qa_tool_failures /
    env_problems / llm_errors (entries panoptes itself marks transient / self_repaired are skipped), bootstrap.missing_after,
    complete=false, all_discovered_tested=false, prs[].outcome qa-error / not-tested / error, and the known field defect
    prs[].code_review.status=failed although the review ran -- only in reports without code_review.ran (fixed by panoptes #20,
    10-23; with ran, status=failed is the review's verdict). Built-in hold kept only for a missing gates.json infra_hold.
  2 reports/catch-up/state.json + run-*.json: gave_up.
  3 gh (read): PraiseZhu/pr-qa-agent-deploy open qa-auto [system_health] issues — body JSON items + "Observed again"
    comment lines (deduped against reports by run id); closed not_planned / wontfix -> the signature counts as 不修.
  4 argus-root/logs: control.log "run-nightly exit" with exit code not 0/75 and level=error lines; nightly-/catch-up-*.log
    lines that start with a JS error class. Signal exits (>=128) are listed only.
  One occurrence = one panoptes run (deduped by run id); count = distinct runs in the window.
Rules (when a signature becomes work):
  qa_tool_failures (incl. bootstrap could-not-install, catch-up gave up), outcome qa-error, deterministic llm_errors
  (HTTP 400 / BadRequest / Unknown parameter / TypeError / RangeError / "must be" / out of range) -> 1 run is enough.
  llm output problems (finish_reason=length, truncated / empty JSON) and everything else -> >= 2 runs in 7 days
  (a single one is treated as transient and listed as 观察中).
  llm network (fetch failed, socket, 429/5xx, timeout) and auth (401/403, expired key, missing secret), env problems about
  disk / secrets, catch-up give-ups by lock timeout, signal exits -> never auto-fixed (gateway / ops, not panoptes code):
  listed with the reason. env_problems inside a run whose bootstrap failed are folded into the bootstrap ticket.
  conf/gates.json "infra_hold" {sig or sig8: reason} -> 待 Praise 决定, never picked (also --sig).
  Claimed: a PraiseZhu/panoptes PR whose body has "Infra-Sig: <sig>" (open; or merged and not recurred after deploy),
  notes/infra-<sig8>-norepro.md, a pending action, an issue closed as not planned. Recurrence after a deployed fix ->
  round 2; a third round -> needs_human.
Per ticket (fleet-mode=pi; MemAvailable >= 3G before every heavy step, wait <= 30 min then transient back-off; does NOT take
panoptes' locks — Praise: memory is enough to run side by side; never touches the /workspace/panoptes checkout itself):
  git fetch; worktree /workspace/panoptes/.worktrees/pi-infra-<sig8> on fix/infra-<sig8> from origin/main; npm ci (argus)
  1 Pi (heavy) writes ONLY a failing test in argus/tests -> it must FAIL (NOREPRO / it passes -> note + Slack DM, no PR;
    env/bootstrap faults may answer UNTESTABLE -> fix without a test, never auto-merged)
  2 Pi writes the minimal fix (one retry) -> test PASS; source reverted -> FAIL; restored -> PASS
  3 the CI steps of .github/workflows/ci.yml locally (bash -n / sh -n / shellcheck if installed / node --check /
    py_compile / npm ci / npm test) must pass
  4 local commit (repo identity Praise <zhuzan@xd.com>, = panoptes repo config and history), git format-patch,
    PR body, action gh.infra.pr (outbound: push branch + non-draft PR). auto_merge=true only with a red-then-green test +
    green local CI + no sensitive path (SENSITIVE_* below) + small diff + no deleted assertions in existing tests.
  Anything but success removes the worktree + branch in `finally`; on success the worktree goes, the branch stays until
  the PR is merged or closed (watch() deletes it).
After the PR (zero model, prwatch calls watch() every minute, gh at most every 2 min): PR found -> pr_open; CI all
  completed: green -> gh.infra.merge action (outbound merges only if auto_merge, infra_automerge != false and CI green
  again, otherwise DMs Praise the link); red -> DM once per head. Merged -> wait for opsenv's ff-only deploy (HEAD of
  /workspace/panoptes contains the merge commit) -> scripts/bootstrap-box.sh --check: red -> DM + suggest git revert;
  green -> comment on the linked pr-qa-agent-deploy issue(s) (close when no other live signature is in it; panoptes reopens
  a fixed-closed issue itself on recurrence) -> deployed. The first report generated after the deploy: signature again ->
  recurred (DM, round 2), a new qa_tool_failures signature -> DM + suggest git revert, else verified.
Tickets: out/infrafix/tickets.json (flock + atomic). Transient failures back off 15m / 1h / 4h, the 4th -> needs_human + DM.
Exit: 0 handled / idle; 75 busy (memory, another infrafix); 1 failed / needs_human / held / error. Last line RESULT {json},
  also out/infrafix/last-result.json.
Usage: infrafix.py [--next] | --sig SIG | --list | --pending-json | --reset SIG   [--dry-run] [--base SHA]
  --reset SIG: forget the ticket (needs_human / rejected / norepro after you deleted the note) so the rules decide afresh.
  --list / --pending-json: read-only (local files + gh reads, zero model, no writes).
Env (tests / rehearsal only): INFRA_GIT INFRA_LIVE INFRA_WT_ROOT INFRA_NOTES INFRA_OUT INFRA_GATES INFRA_RPT INFRA_CATCHUP
  INFRA_LOGS INFRA_TEST_MEM_GB INFRA_GIT_NAME INFRA_GIT_EMAIL."""
import sys, shutil, argparse, fcntl, time, contextlib, hashlib, signal, tempfile
from lib import *
import lib
import praise_notice as PN
os.environ["PATH"] = "/usr/local/bin:" + os.environ.get("PATH", "")

_E = os.environ.get
TIER = _E("INFRA_TIER") or "deep"   # pi-auto deep = deepseek-v4.1-flash, thinking max: same as prfix (Praise 10-24 for the PR bot; was heavy = high)
PANOPTES = _E("INFRA_GIT") or "/workspace/panoptes"            # git dir we branch from (worktrees only)
LIVE = _E("INFRA_LIVE") or "/workspace/panoptes"               # deployed checkout: read-only here (deploy check)
WT_ROOT = _E("INFRA_WT_ROOT") or f"{PANOPTES}/.worktrees"
NOTES = _E("INFRA_NOTES") or "/workspace/praise-pr/notes"
OUT = _E("INFRA_OUT") or f"{BASE}/out/infrafix"
GATES = _E("INFRA_GATES") or f"{PI_CODE}/conf/gates.json"
REPORTS = _E("INFRA_RPT") or RPT
CATCHUP = _E("INFRA_CATCHUP") or f"{QA}/reports/catch-up"
LOGDIR = _E("INFRA_LOGS") or f"{QA}/argus-root/logs"
PANOPTES_REPO = "PraiseZhu/panoptes"
ISSUE_REPO = "PraiseZhu/pr-qa-agent-deploy"
JOB = "infrafix"
GIT_ID = ["-c", f"user.name={_E('INFRA_GIT_NAME') or 'Praise'}", "-c", f"user.email={_E('INFRA_GIT_EMAIL') or 'zhuzan@xd.com'}"]
WINDOW_D = 7
MIN_MEM_GB = 3
BUSY = 75
BACKOFF_MIN = (15, 60, 240)
MAX_ROUNDS = 2
QUIET_RUNS, QUIET_H = 8, 24          # >= 8 later panoptes runs over >= 24 h without it -> probably fixed / transient: watch only
POLL_S = 110                         # gh polls in watch(): at most every ~2 min
MARK = "Infra-Sig:"
SIG_RE = re.compile(r"inf1-[0-9a-f]{20}")
BRANCH_RE = re.compile(r"^fix/infra-([0-9a-f]{8})$")
WT_RE = re.compile(r"^pi-infra-[0-9a-f]{8}(?:-dry)?$")
NOREPRO_RE = re.compile(r"(?m)^[\s#>*_`-]*NOREPRO\b")
UNTESTABLE_RE = re.compile(r"(?m)^[\s#>*_`-]*UNTESTABLE\b")
CR_MSG = "code_review.status=failed although the review ran (blocking finding written as run failure)"
CR_HOLD = "code_review.status=failed 语义，两版修法 7a960e8（status=completed）/ c8b7bc4（加 ran 字段）二选一（pi-line handoff/Mivo QA 运维.md G1）"
DEFAULT_STAGE = {"qa_tool_failures": "qa-tool", "env_problems": "env", "llm_errors": "llm"}
HEALTH_LABEL = {"QA tool failure": "qa_tool_failures", "environment problem": "env_problems", "LLM error": "llm_errors"}
KNOWN_FILES = {}   # sig -> files, for known defects whose message is not a literal in the code (filled below)
STAGE_FILES = {"ai-tests": "argus/src/aitests.mjs", "code-review": "argus/src/mingqa.mjs", "pr-summary": "argus/src/ai.mjs",
               "bootstrap": "scripts/bootstrap-box.sh", "catch-up": "argus/src/catchup.mjs", "janitor": "argus/src/nightly.mjs",
               "run-nightly": "run-nightly.sh", "run": "argus/src/nightly.mjs", "env": "scripts/bootstrap-box.sh"}

class Transient(Exception): """npm / network / model rc / timeout / memory: back off and retry"""
class Deterministic(Exception): """the fix itself is not good enough: needs a human"""
class GateFileError(Exception): pass
class Interrupted(Transient): """SIGTERM while a ticket runs"""
def _on_term(signum, frame): raise Interrupted(f"收到信号 {signum}，本单中断（已清理）")
class NoRepro(Exception):
    def __init__(self, why, text=""): super().__init__(why); self.text = text

# ================================================================ helpers
def run(cmd, cwd=None, t=1800, env=None, inp=None):
    """argv only (never a shell). (rc, stdout+stderr); timeout -> 124, missing binary -> 127."""
    kw = {"input": inp} if inp is not None else {"stdin": subprocess.DEVNULL}
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=t, env=env, **kw)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired as e:
        o = e.stdout or ""
        if isinstance(o, bytes): o = o.decode("utf-8", "replace")
        return 124, f"{o}\n[infrafix] timeout after {t}s"
    except FileNotFoundError as e:
        return 127, f"[infrafix] {e}"
def _read(p, mode="r"):
    with open(p, mode) as f: return f.read()
def _write(p, data, mode="w"):
    with open(p, mode) as f: f.write(data)
def _rjson(p): return json.loads(_read(p))
def tail(s, n=60): return "\n".join((s or "").rstrip().splitlines()[-n:])
def git(*a, cwd=None, t=600): return run(["git", *a], cwd=cwd or PANOPTES, t=t)
def sig8(sig): return (sig or "")[5:13]
def iso(t): return t.astimezone(CST).isoformat(timespec="seconds") if t else None
def ts(s):
    if not s: return None
    if isinstance(s, dt.datetime): return s if s.tzinfo else s.replace(tzinfo=dt.timezone.utc)
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except Exception: return None
def mem_gb():
    ov = _E("INFRA_TEST_MEM_GB")
    if ov: return float(ov)
    for l in _read("/proc/meminfo").splitlines():
        if l.startswith("MemAvailable"): return int(l.split()[1]) / 1048576
    return 0.0
def pid_alive(pid):
    try: return bool(pid) and "infrafix" in _read(f"/proc/{int(pid)}/cmdline", "rb").decode("utf-8", "replace")
    except Exception: return False
def try_lock(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    f = open(path, "a")
    try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); return f
    except BlockingIOError: f.close(); return None
def unlock(f):
    if f:
        try: fcntl.flock(f, fcntl.LOCK_UN)
        finally: f.close()
def wait_mem():
    """before every heavy step: MemAvailable >= 3G; wait up to 30 min, then a transient failure (back-off)."""
    t0 = time.time()
    while mem_gb() < MIN_MEM_GB:
        if time.time() - t0 > 1800: raise Transient(f"等内存超过 30 分钟（MemAvailable {mem_gb():.1f}G < {MIN_MEM_GB}G）")
        time.sleep(30)
SECRETISH = re.compile(r"(TOKEN|SECRET|PASSWORD|API_KEY|_KEY$|^QA_|^ARGUS_|^PANOPTES_|^MIVO_|^DEEPSEEK|^GH_|^GITHUB_|^GIT_)")
def clean_env(extra=None):
    """CI-like env for test runs: no secrets, no QA_HOME/ARGUS_* (tests must never see the live box's data or keys). Starts from
    lib.model_env() (10-24: an allowlist -- __CURSOR_SANDBOX_ENV_RESTORE carries every session token in its VALUE, a name
    denylist let it through), minus DEEPSEEK_API_KEY and anything SECRETISH."""
    env = {k: v for k, v in model_env().items() if not SECRETISH.search(k)}
    env.update({"PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1", "CI": "1"}); env.update(extra or {})
    return env

# ================================================================ signatures
_HTTP = re.compile(r"(?<![\w.])([1-5]\d\d)(?![\w.])")
def normalize(msg):
    s = re.sub(r"^\s*missing:\s*", "", str(msg or ""))
    s = re.sub(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d(?::\d\d(?:\.\d+)?)?(?:Z|[+-]\d\d:?\d\d)?", "<t>", s)
    s = re.sub(r"\b\d{4}-\d\d-\d\d\b", "<d>", s)
    s = re.sub(r"\b\d{8}T\d{6}\b", "<t>", s)
    s = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<id>", s, flags=re.I)
    s = re.sub(r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,64}\b", "<h>", s, flags=re.I)
    s = re.sub(r"(/tmp|/var/tmp)/[\w.-]+", r"\1/<tmp>", s)
    s = re.sub(r"(?:PR\s*)?#\d+", "#<n>", s)
    s = re.sub(r"PR-\d+", "PR-<n>", s)
    s = _HTTP.sub(lambda m: "\x00" + "".join(chr(97 + int(ch)) for ch in m.group(1)) + "\x00", s)   # keep 400/401/5xx: as letters
    s = re.sub(r"\d+(?:\.\d+)?", "<n>", s)
    s = re.sub(r"\x00([a-j]+)\x00", lambda m: "".join(str(ord(ch) - 97) for ch in m.group(1)), s)
    s = re.sub(r"\s+", " ", s).strip().rstrip("。.;；").lower()
    return s[:240]
def sig_of(cat, stage, norm): return "inf1-" + hashlib.sha1(f"{cat}|{stage}|{norm}".encode()).hexdigest()[:20]
CR_SIG = sig_of("field", "code-review", normalize(CR_MSG))
DEFAULT_HOLD = {CR_SIG: CR_HOLD}
KNOWN_FILES[CR_SIG] = ["argus/src/mingqa.mjs:154（toCodeReviewDoc 把结论写进 status）", "argus/src/nightly.mjs:752（原样抄进 code_review.status）"]

def mk(cat, stage, msg, at, source, evidence, pr=None, run=None, raw=None, issue=None):
    msg = str(msg or "")
    stage = stage or DEFAULT_STAGE.get(cat, cat)
    if stage == "catch-up": msg = re.sub(r"\);.*$", ")", msg, flags=re.S)       # recentGiveUps() adds "; merge … has no …"
    norm = normalize(msg)
    o = {"sig": sig_of(cat, stage, norm), "cat": cat, "stage": stage, "norm": norm, "msg": msg[:400], "at": at, "source": source,
         "evidence": evidence, "pr": pr, "run": run or f"{evidence}@{iso(at)}", "raw": raw, "covered": False}
    if stage == "catch-up" and pr: o["run"] = f"gaveup:{pr}"                       # one give-up = one event, wherever listed
    if issue: o["issue"] = issue
    return o

# ================================================================ sources (read-only)
def report_files():
    fs = [f for f in glob.glob(f"{REPORTS}/PR-*.json") if re.search(r"/PR-\d+\.json$", f)]
    fs += [f for f in glob.glob(f"{REPORTS}/history/PR-*.v*.json") if re.search(r"/PR-\d+\.v\d+\.json$", f)]
    fs += [f for f in glob.glob(f"{CATCHUP}/PR-*.json") if re.search(r"/PR-\d+\.json$", f)]
    return sorted(fs)

def report_time(d): return ts(d.get("generated_at") or (d.get("run") or {}).get("finished_at"))
def run_key(d, f, at): return (d.get("run") or {}).get("id") or f"{os.path.basename(f)}@{iso(at)}"
def report_occs(d, f, since):
    at = report_time(d)
    if not at or at < since: return []
    runid = run_key(d, f, at)
    sh = d.get("system_health") or {}
    prs = d.get("prs") or []
    prn = next((p.get("number") for p in prs), None)
    out, boot = [], False
    for cat in ("qa_tool_failures", "env_problems", "llm_errors"):
        for e in sh.get(cat) or []:
            if not isinstance(e, dict): e = {"message": str(e)}
            if e.get("transient") is True or e.get("self_repaired") is True: continue
            st = e.get("stage") or DEFAULT_STAGE[cat]
            if cat == "qa_tool_failures" and st == "bootstrap": boot = True
            out.append(mk(cat, st, e.get("message"), at, "report", f, e.get("pr") or prn, runid, e))
    for m in (sh.get("bootstrap") or {}).get("missing_after") or []:
        out.append(mk("env_problems", "env", m, at, "report", f, prn, runid, {"message": m, "from": "bootstrap.missing_after"}))
    if boot:
        for o in out:
            if o["cat"] == "env_problems": o["covered"] = True
    if sh.get("complete") is False:
        out.append(mk("run", "run", "run incomplete (system_health.complete=false)", at, "report", f, prn, runid, {"notes": sh.get("notes")}))
    if sh.get("all_discovered_tested") is False:
        out.append(mk("run", "run", "not every discovered PR was tested (all_discovered_tested=false)", at, "report", f, prn, runid, {"notes": sh.get("notes")}))
    for p in prs:
        oc = p.get("outcome")
        if oc in ("qa-error", "not-tested", "error"):
            out.append(mk("outcome", oc, f"PR outcome {oc}: {p.get('outcome_reason') or ''}", at, "report", f, p.get("number"), runid,
                          {"outcome": oc, "outcome_reason": p.get("outcome_reason")}))
        for fc in p.get("failed_checks") or []:
            # 10-24 (audit): a check that fails on the BASE revision PR after PR is a fault of the test tool (api-probe
            # 「按画布列出已挂资产」 in 13 reports, fixed by hand in panoptes #18) or a broken main -- either way nobody acted on it.
            # One signature per check + test; rule(): 3 runs in 7 days; never auto-merged (the fix may change what a probe expects).
            if not isinstance(fc, dict) or fc.get("pre_existing") is not True or fc.get("flaky"): continue
            cid = fc.get("check_id") or "check"
            out.append(mk("check", cid, f"{fc.get('check_name') or cid}「{fc.get('test') or ''}」在基线版本上也失败", at, "report", f,
                          p.get("number"), runid, {"check_id": cid, "test": fc.get("test"), "attribution": fc.get("attribution"),
                                                   "fingerprint": fc.get("fingerprint")}))
        cr = p.get("code_review") or {}
        # panoptes #20 (10-23) added code_review.ran: with it, status=failed + ran=true is the review's blocking verdict (not a
        # defect) and ran=false is a run that did not happen. Only reports from before #20 (no ran field) carry this field fault.
        if cr.get("status") == "failed" and "ran" not in cr and (cr.get("verdict") or cr.get("summary")):
            out.append(mk("field", "code-review", CR_MSG, at, "report", f, p.get("number"), runid,
                          {"code_review.status": "failed", "gate": cr.get("gate")}))
    return out

def scan_reports(since, files=None, runs=None):
    """occurrences; runs (dict, optional) collects {run id: time} of EVERY run in the window (for the quiet rule)."""
    out = []
    for f in (report_files() if files is None else files):
        try:
            if os.path.getmtime(f) < since.timestamp(): continue     # cannot hold a run newer than its own mtime
            d = _rjson(f)
        except Exception: continue
        at = report_time(d)
        if runs is not None and at and at >= since: runs[run_key(d, f, at)] = at
        out += report_occs(d, f, since)
    return out

def scan_catchup(since):
    out = []
    p = f"{CATCHUP}/state.json"
    try: st = _rjson(p)
    except Exception: st = {}
    for x in (st.get("prs") or {}).values():
        at = ts(x.get("gave_up_at"))
        if at and at >= since:
            out.append(mk("qa_tool_failures", "catch-up", f"catch-up gave up after {x.get('attempts')} attempts (last: {x.get('last_reason') or 'unknown'})",
                          at, "catch-up-state", p, x.get("number"), None, x))
    for f in sorted(glob.glob(f"{CATCHUP}/run-*.json")):
        try:
            if os.path.getmtime(f) < since.timestamp(): continue
            d = _rjson(f)
        except Exception: continue
        at = ts(d.get("finished_at") or d.get("started_at"))
        if not at or at < since: continue
        for g in (d.get("system_health") or {}).get("gave_up") or []:
            out.append(mk("qa_tool_failures", "catch-up", g.get("message"), at, "catch-up-run", f, g.get("pr"), None, g))
    return out

HEALTH_LINE = re.compile(r"^- \*\*(QA tool failure|environment problem|LLM error)\*\*(?: \(PR #(\d+)\))?(?: \[([^\]]+)\])?: (.+)$", re.M)
HEALTH_BLOCK = re.compile(r"```json mivo-qa-health\s*\n(.*?)\n```", re.S)
def is_health_issue(i): return "```json mivo-qa-health" in (i.get("body") or "") or (i.get("title") or "").startswith("[Mivo QA][system_health]")
def scan_issues(issues, since):
    """-> (occurrences in open health issues within the window, links {sig: {issue numbers}}, wontfix {sig: issue})"""
    occ, links, wontfix = [], {}, {}
    for i in issues or []:
        if not is_health_issue(i): continue
        n, url, state = i.get("number"), i.get("url"), (i.get("state") or "").upper()
        labels = {(l.get("name") if isinstance(l, dict) else str(l)).lower() for l in i.get("labels") or []}
        nope = state == "CLOSED" and ((i.get("stateReason") or "").upper() == "NOT_PLANNED" or labels & {"wontfix", "won't fix", "wont-fix"})
        found = []
        m = HEALTH_BLOCK.search(i.get("body") or "")
        if m:
            try:
                b = json.loads(m.group(1))
                items = b.get("items") or ([{"category": b.get("category"), "entry": b.get("entry")}] if b.get("entry") else [])
                for it in items:
                    e = it.get("entry") or {}; cat = it.get("category")
                    if cat in DEFAULT_STAGE:
                        found.append(mk(cat, e.get("stage"), e.get("message"), ts(i.get("createdAt")), "issue", url, e.get("pr"), b.get("run_id"), e, issue=n))
            except ValueError: pass
        texts = [(i.get("createdAt"), i.get("body") or "", None)] + [(c.get("createdAt"), c.get("body") or "", c.get("url")) for c in i.get("comments") or []]
        for at, body, curl in texts:
            rm = re.search(r"\b(?:Run|run) ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", body)
            for lab, pr, stage, msg in HEALTH_LINE.findall(body):
                found.append(mk(HEALTH_LABEL[lab], stage or None, msg.strip(), ts(at), "issue", curl or url, int(pr) if pr else None,
                                rm.group(1) if rm else None, {"line": msg.strip()[:300]}, issue=n))
        for o in found:
            links.setdefault(o["sig"], set()).add(n)
            if nope: wontfix[o["sig"]] = n
            elif state == "OPEN" and o["at"] and o["at"] >= since: occ.append(o)
    return occ, links, wontfix

JS_ERR = re.compile(r"^(?:Uncaught )?((?:Type|Range|Reference|Syntax|)Error)(?: \[[\w-]+\])?: (.{3,200})$", re.M)
def scan_logs(since, tail_bytes=8 << 20):
    out = []
    p = f"{LOGDIR}/control.log"
    try:
        with open(p, "rb") as f:
            f.seek(0, 2); size = f.tell(); f.seek(max(0, size - tail_bytes)); lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError: lines = []
    for l in lines:
        if not l.startswith("{"): continue
        try: j = json.loads(l)
        except ValueError: continue
        at = ts(j.get("t"))
        if not at or at < since: continue
        if j.get("msg") == "run-nightly exit" and j.get("exitCode") not in (0, 75, None):
            out.append(mk("log", "run-nightly", f"run-nightly.sh exited with code {j.get('exitCode')}", at, "log", p, None, f"exit:{j.get('pid')}:{j.get('t')}",
                          {"exitCode": j.get("exitCode"), "pid": j.get("pid")}))
        elif j.get("level") in ("error", "fatal"):
            out.append(mk("log", j.get("step") or "control", f"{j.get('msg')}: {j.get('error') or j.get('err') or ''}", at, "log", p, None,
                          f"ctl:{j.get('jobId')}:{j.get('t')}", {k: j.get(k) for k in ("msg", "step", "error", "jobId")}))
    for f in sorted(glob.glob(f"{LOGDIR}/nightly-*.log") + glob.glob(f"{LOGDIR}/catch-up-*.log")):
        try:
            mt = os.path.getmtime(f)
            if mt < since.timestamp(): continue
            text = _read(f)
        except (OSError, UnicodeDecodeError): continue
        for m in JS_ERR.finditer(text):
            out.append(mk("log", "nightly-log", m.group(0), dt.datetime.fromtimestamp(mt, CST), "log", f, None, f"log:{os.path.basename(f)}", {"line": m.group(0)}))
    return out

def locate(msg, stage, ref="origin/main"):
    """panoptes files that probably produce this message (git grep on origin/main: read-only, never the work tree)."""
    segs = [s.strip() for s in re.split(r"[（）()，,:：;；\"'`\[\]{}<>]|\d+", msg or "")]
    segs = [s for s in segs if len(re.findall(r"[\u4e00-\u9fff]", s)) >= 4 or len(s) >= 12]
    hits = []
    for s in sorted(set(segs), key=len, reverse=True)[:4]:
        rc, o = git("grep", "-n", "-F", "-e", s, ref, "--", "argus/src", "scripts", "report", "run-nightly.sh", "mini", t=30)
        for l in o.splitlines()[:3]:
            if l.startswith(ref + ":"):
                parts = l[len(ref) + 1:].split(":", 2)
                if len(parts) >= 2 and parts[1].isdigit(): hits.append(f"{parts[0]}:{parts[1]}")
    if not hits and stage in STAGE_FILES: hits = [f"{STAGE_FILES[stage]}（按阶段推断）"]
    return list(dict.fromkeys(hits))[:5]

def files_for(c):
    if c["sig"] in KNOWN_FILES: return KNOWN_FILES[c["sig"]]
    return locate(c.get("sample"), c["stage"]) if c.get("count") else []

# ================================================================ rules (pure)
NET_RE = re.compile(r"fetch failed|und_err|econnreset|econnrefused|etimedout|enotfound|eai_again|socket|network|timed? ?out\b|超时|(?<!\d)(?:429|5\d\d)(?!\d)|rate.?limit|overloaded|unavailable|bad gateway", re.I)
AUTH_RE = re.compile(r"(?<!\d)40[13](?!\d)|authenticat|expired key|api.?key|unauthori|forbidden|missing secret|secrets? file", re.I)
DET_RE = re.compile(r"(?<!\d)400(?!\d)|badrequest|bad request|unknown parameter|invalid_request|typeerror|rangeerror|referenceerror|syntaxerror|is not a function|cannot read prop|must be an? |out of range|is not defined", re.I)
OUTPUT_RE = re.compile(r"finish_reason|json 不完整|unexpected end of json|空内容|empty content|not valid json", re.I)
OPS_ENV_RE = re.compile(r"low disk|secret|unreadable|api.?key", re.I)
PRIO = {"qa_tool": 0, "bootstrap": 0, "outcome": 0, "deterministic": 1, "field": 1, "repeat": 2, "run": 3, "log": 3}

def rule(c):
    """-> (klass, threshold runs in 7 days or None = never auto, why)"""
    cat, st, n = c["cat"], c["stage"], c["norm"]
    if cat == "qa_tool_failures":
        if st == "catch-up" and "lock-timeout" in n: return "ops", None, "补测等锁超时放弃（盒子忙），属运维排队问题，不改代码"
        return ("bootstrap" if st == "bootstrap" else "qa_tool"), 1, "QA 工具故障：出现即成单"
    if cat == "llm_errors":
        if AUTH_RE.search(n): return "auth", None, "模型网关鉴权/密钥问题（401/403/过期），归运维换 key，不改代码"
        if DET_RE.search(n): return "deterministic", 1, "确定性的模型调用错误（400/参数/类型错误），出现即成单"
        if OUTPUT_RE.search(n): return "repeat", 2, "模型输出不完整：7 天内同签名 ≥2 次运行才成单（单次按瞬时）"
        if NET_RE.search(n): return "net", None, "网络/网关瞬时错误，panoptes 已有重试，不自动修"
        return "repeat", 2, "模型调用错误：7 天内同签名 ≥2 次运行才成单"
    if cat == "env_problems":
        if OPS_ENV_RE.search(n): return "ops", None, "磁盘/密钥类环境问题，归运维，不改代码"
        return "repeat", 2, "环境问题：bootstrap 之后仍缺、7 天内 ≥2 次运行才成单"
    if cat == "outcome": return ("outcome", 1, "PR 测试 qa-error：出现即成单") if st in ("qa-error", "error") else ("repeat", 2, "PR 未测：7 天内 ≥2 次才成单")
    if cat == "field": return "field", 1, "报告字段语义缺陷"
    if cat == "check": return "check", 3, "同一检查项 7 天内 ≥3 次测试都在基线版本上失败：测试工具（探针 / 用例）有问题，或主线本身坏了"
    if cat == "run": return "run", 2, "运行不完整：7 天内 ≥2 次才成单"
    if cat == "log":
        m = re.search(r"exited with code (\d+)", n)
        if m and int(m.group(1)) >= 128: return "ops", None, f"run-nightly 被信号结束（{m.group(1)}={'SIGPIPE' if m.group(1) == '141' else 'SIGTERM' if m.group(1) == '143' else 'signal'}），多为人工中断/换机，不自动修"
        return "log", 2, "日志里的异常：7 天内 ≥2 次运行才成单"
    return "repeat", 2, "7 天内 ≥2 次才成单"

def aggregate(occs):
    """occurrences -> {sig: candidate}; one count per panoptes run."""
    by = {}
    for o in occs:
        c = by.setdefault(o["sig"], {"sig": o["sig"], "sig8": sig8(o["sig"]), "cat": o["cat"], "stage": o["stage"], "norm": o["norm"],
                                     "occ": {}, "sources": set(), "issues": set()})
        k = o["run"]
        prev = c["occ"].get(k)
        if not prev or (prev["source"] == "issue" and o["source"] != "issue"): c["occ"][k] = o   # prefer the report copy
        c["sources"].add(o["source"])
        if o.get("issue"): c["issues"].add(o["issue"])
    for c in by.values():
        os_ = sorted(c["occ"].values(), key=lambda o: o["at"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
        c["occs"] = os_; c["count"] = len(os_)
        c["first_seen"] = os_[0]["at"] if os_ else None; c["last_seen"] = os_[-1]["at"] if os_ else None
        c["sample"] = os_[-1]["msg"] if os_ else ""
        c["covered"] = bool(os_) and all(o["covered"] for o in os_)
        c["prs"] = sorted({o["pr"] for o in os_ if o.get("pr")})
        c["evidence"] = list(dict.fromkeys(o["evidence"] for o in reversed(os_) if o.get("evidence")))[:6]
        c["klass"], c["threshold"], c["rule"] = rule(c)
        del c["occ"]
    return by

def load_gates(path=None):
    """-> (holds {sig|sig8: reason}, warn, automerge_allowed). Unreadable file -> GateFileError (fail closed)."""
    path = path or GATES
    if not os.path.exists(path): return dict(DEFAULT_HOLD), f"{path} 不存在，暂用内置 infra_hold", False
    try:
        g = _rjson(path)
        h = g.get("infra_hold")
        if h is None: return dict(DEFAULT_HOLD), "gates.json 没有 infra_hold，暂用内置（code_review 语义待定）", g.get("infra_automerge") is not False
        if not isinstance(h, dict): raise ValueError("infra_hold 不是对象")
        return {str(k): str(v) for k, v in h.items() if not str(k).startswith("_")}, None, g.get("infra_automerge") is not False
    except Exception as e:
        raise GateFileError(f"闸名单 {path} 读不出（{e}），拒绝运行")
def hold_of(sig, holds): return holds.get(sig) or holds.get(sig8(sig))

def load_tickets():
    p = f"{OUT}/tickets.json"
    if not os.path.exists(p): return {"tickets": {}}
    return _rjson(p)                    # unreadable -> exception -> fail closed
@contextlib.contextmanager
def tickets_locked():
    os.makedirs(OUT, exist_ok=True)
    lk = open(f"{OUT}/tickets.json.lock", "w"); fcntl.flock(lk, fcntl.LOCK_EX)
    try:
        doc = load_tickets(); doc.setdefault("tickets", {})
        yield doc
        doc["updated_at"] = now_cst().isoformat(timespec="seconds")
        write_atomic(f"{OUT}/tickets.json", json.dumps(doc, ensure_ascii=False, indent=1, default=str))
    finally:
        fcntl.flock(lk, fcntl.LOCK_UN); lk.close()

def gather(strict=True, now=None):
    """Everything the decision needs, read-only. strict: a gh read failure raises (--next must not double-PR)."""
    now = now or now_cst(); since = now - dt.timedelta(days=WINDOW_D)
    holds, warn, automerge = load_gates()
    warns = [warn] if warn else []
    runs = {}
    occs = scan_reports(since, runs=runs) + scan_catchup(since) + scan_logs(since)
    try:
        issues = gh(["issue", "list", "-R", ISSUE_REPO, "--label", "qa-auto", "--state", "all", "--limit", "200",
                     "--json", "number,state,stateReason,title,url,labels,body,comments,createdAt"])
    except Exception as e:
        if strict: raise
        issues = []; warns.append(f"pr-qa-agent-deploy issue 没读到：{str(e)[:120]}")
    iocc, links, wontfix = scan_issues(issues, since)
    try:
        prs = gh(["pr", "list", "-R", PANOPTES_REPO, "--state", "all", "--limit", "300", "--json", "number,state,title,body,headRefName,createdAt,mergedAt,url"])
    except Exception as e:
        if strict: raise
        prs = None; warns.append(f"panoptes PR 列表没读到（认领未核实）：{str(e)[:120]}")
    cands = aggregate(occs + iocc)
    for s, c in cands.items(): c["issues"] |= links.get(s, set())
    return {"cands": cands, "runs": sorted(runs.values()), "holds": holds, "automerge": automerge, "warns": warns, "links": links, "wontfix": wontfix,
            "issues": issues, "prs": prs, "tickets": load_tickets().get("tickets", {}), "now": now,
            "notes": sorted(os.listdir(NOTES)) if os.path.isdir(NOTES) else []}

# ================================================================ evaluate (pure)
def apply_outcome(tk, out, now):
    """-> (ticket, notify 'needs_human' | 'norepro' | None)"""
    tk = dict(tk); st, kind, err = out.get("status"), out.get("kind"), out.get("error")
    notify = None; stamp = now.isoformat(timespec="seconds")
    if st == "pr_action_written":
        tk.update(status=st, action_key=out.get("action_key"), auto_merge=out.get("auto_merge"), last_error=None, next_retry_at=None, transient_failures=0)
    elif st == "norepro":
        tk.update(status="norepro", last_error=err, next_retry_at=None); notify = "norepro"
    elif kind == "deterministic":
        tk.update(status="needs_human", last_error=err, next_retry_at=None); notify = "needs_human"
    else:
        tf = int(tk.get("transient_failures") or 0) + 1; tk["transient_failures"] = tf
        if tf > len(BACKOFF_MIN):
            tk.update(status="needs_human", last_error=f"瞬时失败 {tf} 次仍未成功，最后一次：{err}", next_retry_at=None); notify = "needs_human"
        else:
            tk.update(status="failed", last_error=err, next_retry_at=(now + dt.timedelta(minutes=BACKOFF_MIN[tf - 1])).isoformat(timespec="seconds"))
    tk["pid"] = None; tk["updated_at"] = stamp
    tk["history"] = (list(tk.get("history") or []) + [{"at": stamp, "status": tk["status"], "error": (err or "")[:200]}])[-20:]
    return tk, notify

def ticket_view(t, now):
    if not t: return None
    t = dict(t)
    if t.get("status") == "running" and not pid_alive(t.get("pid")):
        t = apply_outcome(t, {"status": "failed", "kind": "transient", "error": "上次运行中断（进程已不在）"}, now)[0]
    return t

def new_ticket(c):
    return {"sig": c["sig"], "sig8": c["sig8"], "cat": c["cat"], "stage": c["stage"], "title": (c.get("sample") or "")[:100],
            "status": "queued", "attempts": 0, "transient_failures": 0, "next_retry_at": None, "last_error": None, "action_key": None,
            "round": 1, "history": []}

DONE_ST = ("deployed", "verified")
HUMAN_ST = ("needs_human", "deploy_unhealthy", "ci_red")
def evaluate(c, ctx, now=None):
    now = now or ctx.get("now") or now_cst()
    sig = c["sig"]; claims, skips, nh, limit, watch = [], [], None, None, None
    hold = hold_of(sig, ctx["holds"])
    marker = lambda p: f"{MARK} {sig}" in (p.get("body") or "")
    prs = [p for p in (ctx["prs"] or []) if marker(p)]
    tk = ticket_view(ctx["tickets"].get(sig), now) or {}
    st = tk.get("status")
    fixed_at = ts(tk.get("deployed_at")) if st in DONE_ST + ("recurred",) else None
    recurred = bool(fixed_at and c.get("last_seen") and c["last_seen"] > fixed_at)
    rounds = max(len([p for p in prs if p.get("state") in ("OPEN", "MERGED")]), int(tk.get("round") or 1) if st in DONE_ST + ("recurred",) else 0)
    open_ = [p for p in prs if p.get("state") == "OPEN"]; merged = [p for p in prs if p.get("state") == "MERGED"]
    if ctx["prs"] is None: skips.append("panoptes PR 列表没读到，认领状态未核实，先不做")
    if open_: claims.append(f"infra PR 进行中：{' '.join('#' + str(p['number']) for p in open_)}")
    elif merged and not recurred:
        claims.append(f"已合并 {' '.join('#' + str(p['number']) for p in merged)}" + ("，已部署" if fixed_at else "，等部署/核查"))
    if st == "pr_action_written" and not prs: claims.append(f"已写开 PR 动作 {tk.get('action_key')}（{tk.get('updated_at')}），等外发")
    elif st == "norepro": claims.append(f"Pi 判不可复现，note 待人工确认（{tk.get('updated_at')}）")
    elif st == "rejected": claims.append(f"PR #{tk.get('pr')} 被关闭未合并（视为 Praise 不要这版修法）")
    elif st in DONE_ST and not recurred and not merged: claims.append(f"已修复（{st}，PR #{tk.get('pr')}）")
    if f"infra-{c['sig8']}-norepro.md" in ctx.get("notes", []): claims.append(f"已有 note：infra-{c['sig8']}-norepro.md")
    if sig in ctx.get("wontfix", {}): claims.append(f"{ISSUE_REPO}#{ctx['wontfix'][sig]} 已按不修关闭")
    if hold: skips.insert(0, f"待 Praise 决定：{hold}")
    if not claims and not hold:
        if c.get("covered"): watch = "同一次运行里 bootstrap 安装失败，并入 bootstrap 单"
        elif c["threshold"] is None: watch = c["rule"]
        elif quiet(c, ctx, now):
            q = quiet(c, ctx, now)
            watch = f"最近一次之后又跑了 {q} 次 panoptes 都没再出现（≥{QUIET_RUNS} 次且 ≥{QUIET_H} 小时），视为已修复/瞬时" + (f"；可能已由 {c['fixed_hint']} 修复" if c.get("fixed_hint") else "") + f"；要强制修：infrafix.py --sig {c['sig8']}"
        elif c["count"] < c["threshold"]:
            watch = (f"7 天内只 {c['count']} 次运行出现，按瞬时观察（规则：{c['rule']}）；再出现自动成单，"
                     f"要立即修：infrafix.py --sig {c['sig8']}")
        if recurred and rounds >= MAX_ROUNDS: limit = f"已修 {rounds} 轮（部署后仍复发），第三轮转人工"; nh = limit
    if st in HUMAN_ST: nh = nh or f"{st}：{tk.get('last_error')}"
    if nh and not claims: skips.append(nh)
    if st == "failed" and tk.get("next_retry_at") and (ts(tk["next_retry_at"]) or now) > now:
        skips.append(f"退避中，{cst(tk['next_retry_at'])} 北京后重试（已失败 {tk.get('transient_failures', 0)} 次：{(tk.get('last_error') or '')[:80]}）")
    if st == "running" and pid_alive(tk.get("pid")): skips.append(f"运行中（pid {tk.get('pid')}，{tk.get('started_at')} 开始）")
    state = "claimed" if claims else "held" if hold else "skip" if skips else "watch" if watch else "todo"
    reason = (claims or skips or [watch])[0]
    return {"key": sig, "c": c, "state": state, "reason": reason, "claims": claims, "skips": skips, "watch": watch,
            "round": (rounds + 1) if recurred else 1, "needs_human": nh if not claims else None, "hard_stop": limit if not claims else None,
            "held": bool(hold)}

def quiet(c, ctx, now):
    """number of later panoptes runs when the signature has been quiet long enough (else 0). Not for recurrences."""
    last = c.get("last_seen")
    if not last or now - last < dt.timedelta(hours=QUIET_H): return 0
    n = sum(1 for t in ctx.get("runs") or [] if t > last)
    return n if n >= QUIET_RUNS else 0

def fixed_hint(c, ref="origin/main"):
    """panoptes commits after the last occurrence that touched the located files (read-only git log)."""
    fs = [re.sub(r"[:（].*$", "", f) for f in c.get("files") or []]
    if not fs or not c.get("last_seen"): return None
    rc, o = git("log", f"--since={c['last_seen'].isoformat()}", "--format=%h %s", ref, "--", *dict.fromkeys(fs), t=30)
    ls = [l for l in o.splitlines() if l.strip()] if rc == 0 else []
    return "；".join(l[:70] for l in ls[:2]) or None

def prio(v):
    c = v["c"]; last = c.get("last_seen") or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    return (PRIO.get(c["klass"], 5), -last.timestamp(), -c["count"], c["sig"])

def evaluate_all(ctx, now=None):
    vs = [evaluate(c, ctx, now) for c in ctx["cands"].values()]
    seen = {v["key"] for v in vs}
    for k, why in ctx["holds"].items():           # holds without an occurrence in the window still show up
        if SIG_RE.fullmatch(k) and k not in seen:
            c = {"sig": k, "sig8": sig8(k), "cat": "?", "stage": "?", "norm": "", "count": 0, "first_seen": None, "last_seen": None,
                 "sample": "（7 天内没有出现）", "sources": set(), "issues": set(), "prs": [], "evidence": [], "klass": "hold",
                 "threshold": None, "rule": "", "covered": False, "occs": []}
            vs.append({"key": k, "c": c, "state": "held", "reason": f"待 Praise 决定：{why}", "claims": [], "skips": [why], "watch": None,
                       "round": 1, "needs_human": None, "hard_stop": None, "held": True})
    return sorted(vs, key=prio)

def next_ticket(vs): return next((v for v in vs if v["state"] == "todo"), None)

def row(v):
    c = v["c"]
    return {"sig": c["sig"], "sig8": c["sig8"], "cat": c["cat"], "stage": c["stage"], "klass": c["klass"], "count": c["count"],
            "first_seen": iso(c["first_seen"]), "last_seen": iso(c["last_seen"]), "state": v["state"],
            "reason_if_skipped": None if v["state"] == "todo" else v["reason"], "title": (c.get("sample") or "")[:100],
            "sources": sorted(c["sources"]), "issues": sorted(c["issues"]), "prs": c["prs"][-8:], "evidence": c["evidence"][:3],
            "files": c.get("files") or [], "round": v["round"]}
def pending(vs): return [row(v) for v in vs if v["state"] != "claimed"]

# ================================================================ sensitive paths (auto_merge=false)
SENSITIVE_GLOBS = [
    "pi/**", ".github/**", "scripts/catch-up.cron", ".gitmodules", "ming-qa", "ming-qa/**", "mini/**",
    "secrets.env*", "**/*secret*", "**/*.env", "**/*.env.*", "**/*credential*", "**/*token*", "**/*.pem", "**/*.key",
    "argus/scripts/assemble-secrets.py", "argus/src/config.mjs",                       # secrets loading / env mapping
    "argus/src/github.mjs", "argus/src/feishu.mjs", "argus/src/publish.mjs",          # GitHub writes, Feishu, publishing
    "report/send-*.mjs", "scripts/send-feishu-dm.mjs", "report/allowed-chats*", "report/handoff-routes.json", "scripts/refresh-roster.py",
    "argus/config/severity.md", "argus/config/severity.lock.json", "scripts/check-severity-pin.mjs",   # pinned copy of Review-PR
    "llm-models.json", "argus/src/quota.mjs",                                          # model routing / spend caps
    "argus/package.json", "argus/package-lock.json",                                   # dependencies (supply chain)
]
LINE_RULES = {   # file -> changed lines that make it sensitive
    "run-nightly.sh": r"flock|\.lock\b|WAIT_LOCK|wait-lock|ISSUE|create-issues|no-issues|ALLOW_ISSUES|exec [0-9]>",
    "scripts/bootstrap-box.sh": r"sudo|SUDO|curl|wget|SHASUMS|sha256|gpg|apt-key|sources\.list|CRON_FILE|render_cron|catch-up\.cron|/etc/|chmod|chown|CANONICAL|is_canonical",
}
CONTENT_RE = re.compile(r"redact|secret|api[_-]?key|token|passw|authorization|bearer|ARGUS_ALLOW_ISSUES|createIssue|commentIssue|reopenIssue|"
                        r"gh\s+(?:issue|pr)\s+(?:create|comment|close|merge|edit)|--create-issues", re.I)
MAX_LINES, MAX_FILES = 300, 8
def _glob_re(g):
    out, i = "", 0
    while i < len(g):
        if g.startswith("**/", i): out += "(?:.*/)?"; i += 3
        elif g.startswith("**", i): out += ".*"; i += 2
        elif g[i] == "*": out += "[^/]*"; i += 1
        else: out += re.escape(g[i]); i += 1
    return re.compile(out + r"\Z")
_SENS = [(g, _glob_re(g)) for g in SENSITIVE_GLOBS]
def is_test_path(f): return f.startswith("argus/tests/")
def files_from_diff(diff):
    """[(status, path)] from a unified git diff (A new / D deleted / R renamed / M otherwise) — for re-checks on a PR diff."""
    out, cur = [], None
    for l in (diff or "").splitlines():
        m = re.match(r"diff --git a/(.+?) b/(.+)$", l)
        if m:
            if cur: out.append(cur)
            cur = ["M", m.group(2)]; continue
        if cur and l.startswith("new file mode"): cur[0] = "A"
        elif cur and l.startswith("deleted file mode"): cur[0] = "D"
        elif cur and l.startswith("rename from"): cur[0] = "R"
    if cur: out.append(cur)
    return [tuple(x) for x in out]
def sensitive(diff_files, diff_text):
    """diff_files: [(status, path)], diff_text: unified diff. -> reasons (empty = auto-merge allowed as far as paths go)."""
    why = []
    for stt, f in diff_files:
        g = next((g for g, r in _SENS if r.match(f)), None)
        if g: why.append(f"敏感路径 {f}（{g}）")
        if stt.startswith("D") and not is_test_path(f): why.append(f"删除文件 {f}")
        if stt.startswith("D") and is_test_path(f): why.append(f"删除测试 {f}")
    cur = None; added = removed = 0; seen = set()
    for l in diff_text.splitlines():
        if l.startswith("diff --git "):
            m = re.match(r"diff --git a/(.+?) b/(.+)$", l); cur = m.group(2) if m else None; continue
        if l.startswith(("+++", "---")) or not cur: continue
        if l.startswith("+") or l.startswith("-"):
            body = l[1:]
            if l.startswith("+"): added += 1
            else: removed += 1
            if cur in LINE_RULES and re.search(LINE_RULES[cur], body) and (cur, "line") not in seen:
                why.append(f"{cur} 改到锁/issue/安装敏感行：{body.strip()[:80]}"); seen.add((cur, "line"))
            if not is_test_path(cur) and CONTENT_RE.search(body) and (cur, "content") not in seen:
                why.append(f"{cur} 改到密钥/外发相关代码：{body.strip()[:80]}"); seen.add((cur, "content"))
            if is_test_path(cur) and l.startswith("-") and re.search(r"assert|\btest\(|\bit\(", body) and (cur, "weak") not in seen:
                existing = not any(s.startswith("A") and f == cur for s, f in diff_files)
                if existing: why.append(f"删改了已有测试的断言：{cur}"); seen.add((cur, "weak"))
    if added + removed > MAX_LINES: why.append(f"改动 {added + removed} 行 > {MAX_LINES}，不算最小修复")
    if len(diff_files) > MAX_FILES: why.append(f"改了 {len(diff_files)} 个文件 > {MAX_FILES}")
    return why

# ================================================================ worktree hygiene
def worktrees():
    _, o = git("worktree", "list", "--porcelain"); out = []
    for blk in o.strip().split("\n\n"):
        w = {}
        for l in blk.splitlines():
            k, _, v = l.partition(" "); w[k] = v
        if "worktree" in w: out.append({"path": os.path.abspath(w["worktree"]), "branch": w.get("branch", "").removeprefix("refs/heads/") or None})
    return out
def branch_exists(b): return git("rev-parse", "--verify", "-q", f"refs/heads/{b}")[0] == 0
def has_upstream(b): return git("config", "--get", f"branch.{b}.remote")[0] == 0
def ours_dir(wd):
    wd = os.path.abspath(wd)
    return os.path.dirname(wd) == os.path.abspath(WT_ROOT) and bool(WT_RE.match(os.path.basename(wd)))
def drop_worktree(wd, branch=None):
    """Remove a worktree / local branch this script created. Refuses anything not shaped pi-infra-<sig8> / fix/infra-<sig8>,
    and branches with an upstream. Never touches the main checkout."""
    msg = []
    if wd:
        wd = os.path.abspath(wd)
        if not ours_dir(wd): raise ValueError(f"refuse to remove {wd}: not an infrafix worktree")
        if wd in {w["path"] for w in worktrees()}:
            rc, o = git("worktree", "remove", "--force", "--force", wd); msg.append(f"worktree remove rc={rc}{'' if rc == 0 else ' ' + tail(o, 2)}")
        if os.path.isdir(wd) and wd not in {w["path"] for w in worktrees()}:
            shutil.rmtree(wd, ignore_errors=True); msg.append("rm -rf 残留目录")
        git("worktree", "prune")
    if branch:
        if not BRANCH_RE.match(branch): raise ValueError(f"refuse to delete branch {branch}")
        if branch_exists(branch):
            if has_upstream(branch): msg.append(f"保留 {branch}：有 upstream")
            else:
                rc, o = git("branch", "-D", branch); msg.append(f"branch -D {branch} rc={rc}")
    return msg

def changed(wd):
    run(["git", "add", "-N", "--", "."], cwd=wd)
    _, o = run(["git", "diff", "--name-only", "-z"], cwd=wd)
    return [f for f in o.split("\0") if f and not f.startswith("argus/node_modules/") and f != "argus/package-lock.json"]
def runnable(fs): return [f for f in fs if re.match(r"^argus/tests/[^/]+\.test\.mjs$", f)]
def snapshot(wd, files): return {f: (_read(os.path.join(wd, f), "rb") if os.path.isfile(os.path.join(wd, f)) else None) for f in files}
def to_base(wd, base, files):
    for f in files:
        if git("cat-file", "-e", f"{base}:{f}", cwd=wd)[0] == 0: git("checkout", base, "--", f, cwd=wd)
        else:
            git("rm", "-q", "--cached", "--ignore-unmatch", "--", f, cwd=wd)
            if os.path.isfile(os.path.join(wd, f)): os.remove(os.path.join(wd, f))
def put_back(wd, snap):
    for f, b in snap.items():
        p = os.path.join(wd, f)
        if b is None:
            if os.path.isfile(p): os.remove(p)
        else: os.makedirs(os.path.dirname(p), exist_ok=True); _write(p, b, "wb")

# ================================================================ tests in the worktree
def node_tests(wd, files, t=600):
    """targeted panoptes tests: argus/run-tests.sh <files> (node --test). -> (rc, tail)"""
    wait_mem()
    rel = [f[len("argus/"):] for f in files]
    rc, out = run(["timeout", "-k", "20", str(t), "bash", "run-tests.sh", *rel], cwd=f"{wd}/argus", t=t + 60, env=clean_env())
    return rc, tail(out, 60)
def node_verdict(rc, out):
    if rc == 124: raise Transient(f"测试超时：{tail(out, 3)}")
    if rc == 127: raise Transient(f"测试起不来：{tail(out, 3)}")
    # spec reporter ("ℹ tests N") or TAP ("# tests N"): node picks by environment (CI runners print TAP)
    mt = re.search(r"^(?:ℹ|#) tests (\d+)", out or "", re.M); mf = re.search(r"^(?:ℹ|#) fail (\d+)", out or "", re.M)
    if not mt: raise Transient(f"node --test 没有输出摘要（没跑起来？）：{tail(out, 3)}")
    if int(mt.group(1)) == 0: raise Transient("没有跑到任何测试")
    return "pass" if rc == 0 and mf and int(mf.group(1)) == 0 else "fail"
def npm_ci(wd):
    wait_mem()
    rc, out = run(["npm", "ci", "--ignore-scripts", "--prefer-offline", "--no-audit", "--no-fund"], cwd=f"{wd}/argus", t=1200, env=clean_env())
    return rc, tail(out, 20)
def ci_local(wd):
    """The steps of .github/workflows/ci.yml, run locally (CI on the PR head stays the authority for merging)."""
    wait_mem()
    steps, notes = [], []
    pyc = tempfile.mkdtemp(prefix="pi-infrafix-pyc-")
    env = clean_env({"PYTHONPYCACHEPREFIX": pyc})
    def st(name, cmd, cwd=wd, t=300):
        t0 = time.time(); rc, o = run(cmd, cwd=cwd, t=t, env=env); steps.append({"step": name, "rc": rc, "s": round(time.time() - t0), "tail": tail(o, 15)})
        return rc
    for f, sh in (("run-nightly.sh", "bash"), ("mini/check-queue.sh", "bash"), ("scripts/chromium-linux.sh", "sh")):
        if os.path.exists(f"{wd}/{f}"): st(f"{sh} -n {f}", [sh, "-n", f])
    sc = [f for f in ("run-nightly.sh", "mini/check-queue.sh", "scripts/chromium-linux.sh") if os.path.exists(f"{wd}/{f}")]
    if shutil.which("shellcheck") and sc: st("shellcheck", ["shellcheck"] + sc)   # no files: nothing to check (shellcheck alone exits 1)
    else: notes.append("本机没装 shellcheck，这一步只在 CI 上跑")
    for f in sorted(glob.glob(f"{wd}/scripts/*.mjs") + glob.glob(f"{wd}/report/*.mjs")): st(f"node --check {os.path.relpath(f, wd)}", ["node", "--check", f])
    if os.path.exists(f"{wd}/report/allowed-chats.example.json"):
        st("allowed-chats.example.json", ["node", "-e", 'JSON.parse(require("fs").readFileSync("report/allowed-chats.example.json","utf8"))'])
    pys = sorted(glob.glob(f"{wd}/scripts/*.py"))
    if pys: st("py_compile scripts/*.py", ["python3", "-m", "py_compile", *pys])
    st("npm ci", ["npm", "ci", "--ignore-scripts", "--prefer-offline", "--no-audit", "--no-fund"], cwd=f"{wd}/argus", t=1200)
    wait_mem()
    st("npm test", ["timeout", "-k", "20", "900", "npm", "test"], cwd=f"{wd}/argus", t=960)
    shutil.rmtree(pyc, ignore_errors=True)
    bad = [s for s in steps if s["rc"] != 0]
    tests = next((s for s in steps if s["step"] == "npm test"), {})
    m = re.search(r"(?:ℹ|#) pass (\d+)", tests.get("tail") or ""); f_ = re.search(r"(?:ℹ|#) fail (\d+)", tests.get("tail") or "")
    return {"ok": not bad, "steps": steps, "failed": [s["step"] for s in bad], "timeout": any(s["rc"] == 124 for s in bad), "notes": notes,
            "pass": int(m.group(1)) if m else None, "fail": int(f_.group(1)) if f_ else None, "seconds": sum(s["s"] for s in steps)}

# ================================================================ one ticket
def context_text(c):
    ex = [o for o in reversed(c.get("occs") or [])][:3]
    raw = json.dumps([o.get("raw") for o in ex], ensure_ascii=False, default=str)[:2500]
    return (f"panoptes (Mivo QA pipeline, repo PraiseZhu/panoptes) has a fault in its OWN machinery. Signature {c['sig']}\n"
            f"category {c['cat']} / stage {c['stage']} / seen in {c['count']} panoptes runs in the last 7 days "
            f"(first {cst(c['first_seen'])}, last {cst(c['last_seen'])} Beijing)\n"
            f"normalised message: {c['norm']}\nexamples:\n" + "\n".join(f"- [{o['source']}] PR #{o.get('pr')}: {o['msg'][:300]}" for o in ex) +
            f"\nevidence files (read-only, you may open them): {', '.join(c['evidence'][:4])}\nraw entries: {raw}\n"
            f"likely code: {', '.join(c.get('files') or []) or 'unknown — search argus/src, scripts/, report/, run-nightly.sh'}\n"
            "Tests: node:test files in argus/tests/*.test.mjs; run one with `cd argus && bash run-tests.sh tests/<file>.test.mjs` "
            "(the whole suite takes ~10 s). External things (model gateway, gh, apt, network, clock) are always faked/injected in tests — "
            "see tests/fake-gh.mjs, tests/bootstrap-apt-lock.test.mjs, tests/llm-network-retry.test.mjs.")
RULES = ("Rules: no git commit/push/stash/reset/checkout, no network, edit only inside cwd; never modify /workspace/panoptes (the live "
         "checkout), /workspace/mivo-qa (reading the evidence files above is fine), /etc, crontabs or any secrets file; never run "
         "run-nightly.sh or bootstrap-box.sh for real; never kill processes; run only targeted `bash run-tests.sh tests/<file>` "
         "from argus/ (no watch mode).")

def fix_one(c, A, d):
    """-> (outcome, res). outcome status: pr_action_written | dry_ok | norepro | failed (kind transient|deterministic)."""
    s8 = c["sig8"]; rnd = int(c.get("round") or 1)
    branch = f"fix/infra-{s8}"
    wd = f"{WT_ROOT}/pi-infra-{s8}{'-dry' if A.dry_run else ''}"
    res = {"sig": c["sig"], "steps": [], "model": [], "base": None, "wd": wd, "branch": None if A.dry_run else branch, "round": rnd,
           "untestable": False, "auto_merge": False, "auto_merge_why": []}
    out = {"status": "failed", "kind": "transient", "error": None, "action_key": None}
    created = False; ev = f"{OUT}/{d}-infra-{s8}{'-dry' if A.dry_run else ''}"
    def step(k, ok, text=""): res["steps"].append((k, ok, text)); log(k, "OK" if ok else "FAIL")
    log("pick", c["sig"], c["cat"], c["stage"], c.get("sample", "")[:80], f"round {rnd}", "dry-run" if A.dry_run else "real")
    try:
        wait_mem()
        if os.path.exists(wd) or (not A.dry_run and branch_exists(branch)):
            log("leftover of this ticket ->", drop_worktree(wd if os.path.exists(wd) or wd in {w["path"] for w in worktrees()} else None,
                                                             None if A.dry_run else branch))
        rc, o = git("fetch", "-q", "origin", "main", t=300)
        if rc: raise Transient(f"git fetch 失败 rc={rc}：{tail(o, 2)}")
        base = A.base or git("rev-parse", "origin/main")[1].strip()
        rc, o = git("rev-parse", "--verify", base + "^{commit}"); base = o.strip()
        if rc or not re.fullmatch(r"[0-9a-f]{40}", base): raise Transient(f"base 解析失败：{tail(o, 2)}")
        res["base"] = base
        os.makedirs(WT_ROOT, exist_ok=True)
        rc, o = git("worktree", "add", "-q", *(["--detach", wd, base] if A.dry_run else ["-b", branch, wd, base]))
        if rc: raise Transient(f"worktree add 失败：{tail(o, 3)}")
        created = True
        rc, o = npm_ci(wd); step("npm ci（argus）", rc == 0, f"rc={rc}\n{o}")
        if rc: raise Transient(f"npm ci rc={rc}")
        ctx = context_text(c)
        envish = c["klass"] == "bootstrap" or c["cat"] == "env_problems"
        def ask(k, prompt):
            wait_mem()
            try: text, task = pi(TIER, wd, prompt, f"pi-line-infrafix-{s8}-r{rnd}-{k}", timeout=2400, write=True)
            except Exception as e: raise Transient(f"模型 {k} 调用失败：{str(e)[:300]}")
            res["model"].append({"step": k, "task": task, "text": text}); return text
        # 1 reproduce
        t1 = ask("repro", f"{ctx}\n\nTASK 1 (reproduce only): add a regression test in argus/tests/ (new *.test.mjs file or a new test in the "
                 "closest existing file) that FAILS on the current code because of this fault, and would pass once panoptes handles it "
                 "correctly. Do NOT change non-test files. If this is not a panoptes defect (gateway outage, expired key, box/ops problem, "
                 "already handled correctly), change nothing and start your reply with a line `NOREPRO`, followed by the evidence (file:line)."
                 + (" This is an environment/bootstrap fault: the fix belongs in scripts/bootstrap-box.sh install logic; if no automated test "
                    "is possible, change nothing and start your reply with `UNTESTABLE` + why (the fix will then never be auto-merged)." if envish else "")
                 + f" {RULES}\nEnd with: ROOT CAUSE file:line, TEST FILE path, the test names.")
        res["repro_text"] = t1
        ch = changed(wd); tests = [f for f in ch if is_test_path(f)]; src = [f for f in ch if not is_test_path(f)]
        if src: to_base(wd, base, src)
        if NOREPRO_RE.search(t1):
            if tests: to_base(wd, base, tests)
            step("repro", False, "模型回 NOREPRO"); raise NoRepro("模型判不是 panoptes 缺陷 / 不可复现（NOREPRO）", t1)
        runs_ = runnable(tests)
        if envish and UNTESTABLE_RE.search(t1) and not runs_:
            res["untestable"] = True; step("repro：环境类，模型判无法写自动化测试（UNTESTABLE）", True, t1[-1500:])
        else:
            if not runs_: step("repro", False, "模型没写测试，也没回 NOREPRO"); raise Transient("模型没写回归测试，也没回 NOREPRO")
            rc, o = node_tests(wd, runs_); v = node_verdict(rc, o); step("repro：测试在 base 上失败", v == "fail", f"rc={rc}\n{o}")
            res["rc_repro"] = rc
            if v == "pass": raise NoRepro("模型写的测试在当前代码上就通过（没复现）", t1 + f"\n\n--- tests rc={rc} ---\n{o}")
        test_snap = snapshot(wd, tests)
        # 2 minimal fix (one retry); the tests are restored before every verdict
        last, v = "", "fail"
        for attempt in (1, 2):
            t2 = ask(f"fix{attempt}", f"{ctx}\n\nTASK 2: " + (f"the failing test(s) {runs_} reproduce the fault. " if runs_ else "no automated test is possible (UNTESTABLE). ")
                     + "Make the MINIMAL fix in panoptes (smallest diff, existing style, no refactor; do not edit or weaken tests — they are restored "
                     "before verification; do not touch .github/, scripts/catch-up.cron, secrets, issue/lock logic unless the fault is exactly there). "
                     f"{RULES}\n" + (f"Previous attempt still failed:\n{last[-1500:]}\n" if last else "") + "End with ROOT CAUSE file:line and a one-paragraph explanation.")
            put_back(wd, test_snap)
            if not runs_: break
            rc, o = node_tests(wd, runs_); v = node_verdict(rc, o); last = o
            if v == "pass": break
        res["root"] = t2
        src = [f for f in changed(wd) if not is_test_path(f)]
        if not src: raise Deterministic("修复没改任何非测试文件")
        if runs_:
            step("fix：测试通过", v == "pass", f"rc={rc}\n{o}")
            if v != "pass": raise Deterministic("修了两次，复现测试仍不过")
            snap = snapshot(wd, src)
            to_base(wd, base, src); rc2, o2 = node_tests(wd, runs_); v2 = node_verdict(rc2, o2); step("撤掉修复：测试失败", v2 == "fail", f"rc={rc2}\n{o2}")
            put_back(wd, snap); rc3, o3 = node_tests(wd, runs_); v3 = node_verdict(rc3, o3); step("恢复修复：测试通过", v3 == "pass", f"rc={rc3}\n{o3}")
            if v2 != "fail": raise Deterministic("撤掉修复后测试仍通过：测试没测到这次修复")
            if v3 != "pass": raise Deterministic("恢复修复后测试不过")
        # 3 the CI steps locally
        ci = ci_local(wd); res["ci"] = ci
        step(f"本地 CI 同步骤（npm test {ci['pass']} 过 / {ci['fail']} 失败，{ci['seconds']}s）", ci["ok"],
             "\n".join(f"{s['step']}: rc={s['rc']} {s['s']}s" + (f"\n{s['tail']}" if s["rc"] else "") for s in ci["steps"]))
        if ci["timeout"]: raise Transient("本地 CI 超时")
        if not ci["ok"]: raise Deterministic(f"本地 CI 没过：{', '.join(ci['failed'][:4])}")
        # 4 commit + auto-merge decision + action
        files = changed(wd)
        git("add", "-A", "--", *files, cwd=wd)
        _, ns = git("diff", "--cached", "--name-status", "-z", base, cwd=wd)
        tok = [x for x in ns.split("\0") if x]; dfiles = []; i = 0
        while i < len(tok):
            s_ = tok[i]; i += 1
            if s_[:1] in ("R", "C"): dfiles.append((s_, tok[i + 1])); i += 2
            else: dfiles.append((s_, tok[i])); i += 1
        _, dtext = git("diff", "--cached", base, cwd=wd)
        # 10-24 (Pi line merged into panoptes pi/): the bot never changes itself -- a fix that touches pi/ is dropped, a human looks
        own = sorted({f for _, f in dfiles if f == "pi" or f.startswith("pi/")})
        if own: raise Deterministic(f"修复改到了 pi/（Pi 线自己的代码，infrafix 不许改）：{', '.join(own[:4])}")
        why = sensitive(dfiles, dtext)
        if res["untestable"]: why.insert(0, "环境类修复没有复现测试（UNTESTABLE）")
        if not runs_: why.append("没有先红后绿的复现测试")
        if c.get("cat") == "check": why.append("检查项级故障：修法可能是改探针或用例的预期，要人确认不是在掩盖主线上的真问题")
        res["auto_merge"] = not why; res["auto_merge_why"] = why or ["有先红后绿的复现测试、本地 CI 全绿、没碰敏感路径、改动小"]
        tests = [f for _, f in dfiles if is_test_path(f)]; src = [f for _, f in dfiles if not is_test_path(f)]
        short = re.sub(r"\s+", " ", c.get("sample") or c["norm"])[:60]
        title = f"fix({c['stage']}): {short} (infra {s8})"; res["title"] = title
        marker = f"{MARK} {c['sig']}" + (f" r{rnd}" if rnd > 1 else "")
        body = pr_body(c, res, tests, src, ci, marker)
        if A.dry_run:
            write_atomic(ev + ".pr.md", body); git("reset", "-q", cwd=wd)
            out = {"status": "dry_ok", "kind": None, "error": None, "action_key": None, "auto_merge": res["auto_merge"]}
        else:
            rc, o = git(*GIT_ID, "commit", "-q", "-m", title, "-m", f"{marker}\n关联：{', '.join(f'{ISSUE_REPO}#{n}' for n in sorted(c['issues'])) or '无 issue'}", cwd=wd)
            step("本地提交", rc == 0, o)
            if rc: raise Deterministic(f"本地提交失败：{tail(o, 3)}")
            head = git("rev-parse", "HEAD", cwd=wd)[1].strip()
            _, patch = git("format-patch", "-1", "--stdout", "--binary", head, cwd=wd)
            stem = f"{OUT}/{d}-infra-{s8}{'-r' + str(rnd) if rnd > 1 else ''}"     # evidence uses <stem>.patch / .md: keep apart
            pfile = stem + ".pr.patch"
            write_atomic(pfile, patch); write_atomic(stem + ".pr.md", body)
            key = f"infrapr:{c['sig']}" + (f":r{rnd}" if rnd > 1 else "")
            record_actions(JOB, d, [{"key": key, "type": "gh.infra.pr", "repo": PANOPTES_REPO, "sig": c["sig"], "round": rnd, "marker": marker,
                "patch": pfile, "body_file": stem + ".pr.md", "title": title, "branch": branch + (f"-r{rnd}" if rnd > 1 else ""),
                "base": base, "local_head": head, "auto_merge": res["auto_merge"], "auto_merge_why": res["auto_merge_why"],
                "issues": sorted(c["issues"]), "files": [f for _, f in dfiles],
                "note": "outbound: clone, git am this patch on origin/main, push the branch, gh pr create (not draft); idempotent on the marker"}])
            out = {"status": "pr_action_written", "kind": None, "error": None, "action_key": key, "auto_merge": res["auto_merge"]}
    except NoRepro as e:
        out = {"status": "norepro", "kind": "norepro", "error": str(e), "action_key": None}; res["norepro"] = (str(e), e.text)
    except Transient as e:
        out = {"status": "failed", "kind": "transient", "error": str(e), "action_key": None}
    except Deterministic as e:
        out = {"status": "failed", "kind": "deterministic", "error": str(e), "action_key": None}
    except Exception as e:
        out = {"status": "failed", "kind": "transient", "error": f"内部错误：{e!r}"[:300], "action_key": None}
    finally:
        try:
            if created and os.path.isdir(wd):
                run(["git", "add", "-N", "--", "."], cwd=wd)
                _, diff = git("diff", "--binary", res["base"] or "HEAD", "--", ".", ":(exclude)argus/package-lock.json", cwd=wd)
                write_atomic(ev + ".patch", diff)
            write_atomic(ev + ".md", evidence_md(c, res, out, wd))
        except Exception as e: log("evidence write failed", e)
        if created:   # success: the worktree goes, the branch (with the commit) stays until the PR is merged/closed
            try: log("cleanup", drop_worktree(wd, None if (A.dry_run or out["status"] == "pr_action_written") else branch))
            except Exception as e: log("cleanup failed", e)
        elif not A.dry_run and out["status"] != "pr_action_written" and branch_exists(branch) and not has_upstream(branch):
            try: log("cleanup branch", drop_worktree(None, branch))
            except Exception as e: log("cleanup failed", e)
    res["evidence"] = ev + ".md"
    return out, res

def pr_body(c, res, tests, src, ci, marker):
    fm = lambda fs: "、".join(f"`{f}`" for f in fs) or "无"
    iss = ", ".join(f"{ISSUE_REPO}#{n}" for n in sorted(c["issues"])) or "无"
    root = re.findall(r"ROOT CAUSE\**[:：]?\**\s*(.+)", res.get("root") or "")
    L = [f"自动修复 QA 系统自身故障（Pi 线 infrafix，第 {res['round']} 轮）。", "",
         "## 故障", "",
         f"- 签名：`{c['sig']}`（{c['cat']} / {c['stage']}）", f"- 7 天内 {c['count']} 次运行出现；首次 {cst(c['first_seen'])}，最近 {cst(c['last_seen'])}（北京）",
         f"- 原文：{(c.get('sample') or '')[:300]}", f"- 证据：{'、'.join(c['evidence'][:4]) or '无'}", f"- 关联 issue：{iss}", "",
         "## 根因与修复", "", f"- 根因：{root[-1].strip()[:300] if root else '见下方模型说明'}", f"- 改动：{fm(src)}", f"- 测试：{fm(tests)}", "",
         "## 验证", "", "```text"]
    if res.get("untestable"): L.append("复现测试：无（环境类，模型判 UNTESTABLE），所以不会自动合并")
    else: L.append(f"复现测试 {' '.join(runnable(tests))}：修复前失败（rc={res.get('rc_repro')}）→ 修复后通过；撤掉修复再失败，恢复后通过")
    L += [f"本地 CI 同步骤（{' / '.join(s['step'] for s in ci['steps'] if not s['step'].startswith('node --check '))} + node --check）：全部通过，"
          f"npm test {ci['pass']} 过 / {ci['fail']} 失败，{ci['seconds']}s" + (f"；{'；'.join(ci['notes'])}" if ci["notes"] else ""), "```", "",
          "## 合并", "", f"- auto_merge：{'是' if res['auto_merge'] else '否'}（{'；'.join(res['auto_merge_why'])}）",
          "- 自动合并条件：本 PR 头提交的 GitHub Actions 全部 success、auto_merge=是、conf/gates.json infra_automerge 不为 false；否则私信 Praise 看。",
          "- 合并后由 opsenv 在锁空闲时 ff-only 部署；部署后 `scripts/bootstrap-box.sh --check` 与下一次运行核查，不健康会私信建议 `git revert`。", "",
          marker, f"<!-- pi-line infrafix {c['sig']} -->"]
    return "\n".join(L)

def evidence_md(c, res, out, wd):
    md = [f"# infrafix {c['sig']}（{c['cat']} / {c['stage']}，第 {res.get('round', 1)} 轮）{' dry-run' if res['branch'] is None else ''}",
          f"- 结果：**{out['status']}**{('（' + str(out.get('kind')) + '）') if out.get('kind') else ''}{('：' + out['error']) if out.get('error') else ''}",
          f"- base {str(res.get('base') or '?')[:9]}，worktree {wd}{'，分支 ' + res['branch'] if res.get('branch') else ''}",
          f"- 故障：{(c.get('sample') or '')[:200]}；7 天 {c['count']} 次；证据 {'、'.join(c['evidence'][:3])}",
          f"- auto_merge：{res.get('auto_merge')}（{'；'.join(res.get('auto_merge_why') or [])}）", "",
          "| 步骤 | 结果 |", "|---|---|"] + [f"| {k} | {'✅' if ok else '❌'} |" for k, ok, _ in res["steps"]]
    for k, ok, o in res["steps"]: md += ["", f"### {k}", "```", (o or "").strip()[-6000:], "```"]
    if res.get("norepro"): md += ["", "## NOREPRO（不开 PR；note 待人工确认）", res["norepro"][0]]
    for m in res["model"]: md += ["", f"## 模型输出：{m['step']}（task {m['task']}）", "", m["text"]]
    return "\n".join(md) + "\n"

def write_norepro_note(c, res, why, ev):
    name = f"infra-{c['sig8']}-norepro.md"
    text = (res.get("norepro") or ("", ""))[1] or (res["model"][0]["text"] if res.get("model") else "")
    md = [f"# QA 系统故障 {c['sig8']} 不可复现 / 不是 panoptes 缺陷（待人工确认）", "",
          f"> 待人工确认：Pi 线 infrafix 自动判定。认可就保留本文件（这个签名不再自动修）；不认可就删掉本文件，再跑 `infrafix.py --sig {c['sig8']}`。", "",
          f"- 签名：{c['sig']}（{c['cat']} / {c['stage']}），7 天 {c['count']} 次", f"- 原文：{(c.get('sample') or '')[:300]}",
          f"- 证据：{'、'.join(c['evidence'][:4])}", f"- 判定：{why}", f"- 时间：{now_cst():%Y-%m-%d %H:%M} 北京；base {str(res.get('base') or '?')[:9]}",
          f"- 证据全文：{ev}", "", "## 模型完整输出", "", text, ""]
    p = os.path.join(NOTES, name); write_atomic(p, "\n".join(md)); return p

def dm_action(kind, c, detail, key=None):
    what = f"测试系统自己的一个故障（「{(c.get('sample') or c.get('title') or '')[:60]}」，编号 {c['sig8']}）"
    if kind == "norepro":
        it = PN.item("action", "测试系统的一个故障，自修复现不了", f"「测试系统自修」想修{what}，但复现不了，判断可能不是测试系统的毛病。判断依据写在机器上的一份说明里。",
                     "你不确认，这个故障会一直挂着。",
                     todo=[PN.ask_session(f"把 infrafix 对故障 {c['sig8']} 的不可复现说明（{detail[:120]}）讲给我听：为什么判断不是测试系统的毛病"),
                           f"听完回它一句：「同意，保留说明不再修」或「不同意，删掉说明后跑 infrafix.py --sig {c['sig8']} 重修」"])
    else:
        it = PN.item("action", "测试系统的一个故障，自修修不动了", f"「测试系统自修」修{what}时停下来了，原因：{detail[:300]}。",
                     "这个故障不会再自动修。", todo=PN.ask_session(f"infrafix 修故障 {c['sig8']} 停下了（{detail[:150]}），看一下怎么处理，处理完跑 infrafix.py --reset {c['sig8']}"))
    return {"key": key or f"infra-{kind}:{c['sig']}", "type": "slack.dm", "to": "Praise", "sig": c["sig"], "text": PN.text(it)[:1500]}

# ================================================================ reporting / runner
def print_list(vs, ctx):
    g = {k: [v for v in vs if v["state"] == k] for k in ("todo", "held", "watch", "skip", "claimed")}
    print(f"# infrafix 候选 {now_cst():%Y-%m-%d %H:%M} 北京（只读，窗口 {WINDOW_D} 天）：{len(vs)} 个签名；可做 {len(g['todo'])}，"
          f"待 Praise 决定 {len(g['held'])}，观察中 {len(g['watch'])}，跳过 {len(g['skip'])}，已认领 {len(g['claimed'])}")
    print(f"- infra_automerge={'允许' if ctx['automerge'] else '关闭'}；hold {len(ctx['holds'])} 条" + ("；" + "；".join(ctx["warns"]) if ctx["warns"] else ""))
    names = (("todo", "可做（--next 按此顺序一次一单）"), ("held", "待 Praise 决定（infra_hold，不自动修）"), ("watch", "观察中（未达成单规则）"),
             ("skip", "跳过"), ("claimed", "已认领"))
    for k, title in names:
        print(f"\n## {title}（{len(g[k])}）")
        for v in g[k]:
            c = v["c"]
            print(f"- {c['sig8']} [{c['cat']}/{c['stage']}] {c['count']} 次（{cst(c['first_seen'])}–{cst(c['last_seen'])}）{(c.get('sample') or '')[:70]}")
            print(f"    来源 {','.join(sorted(c['sources'])) or '-'}；issue {','.join('#' + str(n) for n in sorted(c['issues'])) or '-'}；"
                  f"PR {','.join('#' + str(n) for n in c['prs'][-5:]) or '-'}；文件 {', '.join(c.get('files') or []) or '-'}")
            if c["evidence"]: print(f"    证据 {c['evidence'][0]}" + (f" 等 {len(c['evidence'])} 处" if len(c["evidence"]) > 1 else ""))
            if k != "todo": print(f"    → {v['reason']}")
            elif v["round"] > 1: print(f"    → 第 {v['round']} 轮")

def finish(r, code, A):
    r.update(exit=code, at=now_cst().isoformat(timespec="seconds"), dry_run=bool(getattr(A, "dry_run", False)))
    try: write_atomic(f"{OUT}/last-result{'.dry' if r['dry_run'] else ''}.json", json.dumps(r, ensure_ascii=False, indent=1, default=str))
    except Exception as e: log("last-result write failed", e)
    print("RESULT " + json.dumps(r, ensure_ascii=False, default=str), flush=True)
    return code

def bookkeeping(vs, now, d):
    """dead running -> transient failure; hard stops -> needs_human + DM once; queued entries = todo now."""
    dms = []
    with tickets_locked() as T:
        tks = T["tickets"]
        for k, t in list(tks.items()):
            if t.get("status") == "running" and not pid_alive(t.get("pid")):
                tks[k] = apply_outcome(t, {"status": "failed", "kind": "transient", "error": "上次运行中断（进程已不在）"}, now)[0]
        for w in vs:
            k, t = w["key"], tks.get(w["key"])
            if w["hard_stop"] and not w["held"] and (t or {}).get("status") != "needs_human":
                t = dict(t or new_ticket(w["c"])); t.update(status="needs_human", last_error=w["hard_stop"], updated_at=now.isoformat(timespec="seconds"))
                tks[k] = t; dms.append(dm_action("needs_human", w["c"], w["hard_stop"]))
            elif w["state"] == "todo" and (k not in tks or tks[k].get("status") in DONE_ST + ("recurred",)):
                prev = tks.get(k) or {}
                tks[k] = dict(new_ticket(w["c"]), round=w["round"], history=prev.get("history") or [], previous=prev.get("pr"))
            elif w["state"] != "todo" and (t or {}).get("status") == "queued": tks.pop(k, None)
        tickets = dict(tks)
    if dms: record_actions(JOB, d, dms)
    return tickets

def pick_sig(A, ctx, vs):
    s = A.sig.lower()
    mine = [v for v in vs if v["key"].startswith(s) or v["c"]["sig8"].startswith(s)]
    if not mine: return None, {"status": "error", "sig": A.sig, "error": "找不到这个签名（7 天窗口内；--sig 按前缀匹配 sig 或 sig8）"}
    if len(mine) > 1: return None, {"status": "error", "sig": A.sig, "error": f"前缀匹配到 {len(mine)} 个签名，请写长一点"}
    v = mine[0]
    if v["held"]: return None, {"status": "held", "sig": v["key"], "error": v["reason"]}
    if v["hard_stop"]: return None, {"status": "needs_human", "sig": v["key"], "error": v["hard_stop"]}
    if v["state"] != "todo": log(f"warn: --sig 人工指定，忽略：{v['reason']}")
    return v, None

def run_ticket(A, ctx, vs):
    d = today(); now = now_cst()
    if mode() != "pi" and not A.dry_run: return finish({"status": "off", "reason": f"fleet-mode={mode()}（只在 pi 模式修）"}, 0, A)
    if A.sig:
        v, err = pick_sig(A, ctx, vs)
        if err: return finish(err, 1, A)
    else:
        if not A.dry_run:
            ctx["tickets"] = bookkeeping(vs, now, d); vs = evaluate_all(ctx, now)
        v = next_ticket(vs)
        if not v: return finish({"status": "idle", "pending_after": 0}, 0, A)
    if mem_gb() < MIN_MEM_GB: return finish({"status": "busy", "reason": f"MemAvailable {mem_gb():.1f}G < {MIN_MEM_GB}G", "sig": v["key"]}, BUSY, A)
    run_lk = try_lock(f"{OUT}/infrafix.run.lock")
    if not run_lk: return finish({"status": "busy", "reason": "另一个 infrafix 正在运行", "sig": v["key"]}, BUSY, A)
    tk = {}
    try:
        if not A.sig:   # under the run lock: another run may have finished this one meanwhile
            ctx["tickets"] = load_tickets().get("tickets", {}); vs = evaluate_all(ctx, now); v = next_ticket(vs)
            if not v: return finish({"status": "idle", "pending_after": 0}, 0, A)
        holds, _, _ = load_gates()      # fresh read right before acting (--sig included)
        if hold_of(v["key"], holds): return finish({"status": "held", "sig": v["key"], "error": hold_of(v["key"], holds)}, 1, A)
        c = dict(v["c"], round=v["round"])
        c["files"] = c.get("files") or files_for(c)
        if not A.dry_run:
            with tickets_locked() as T:
                t = dict(T["tickets"].get(v["key"]) or new_ticket(c))
                t.update(status="running", attempts=int(t.get("attempts") or 0) + 1, started_at=now.isoformat(timespec="seconds"),
                         pid=os.getpid(), round=c["round"], updated_at=now.isoformat(timespec="seconds"))
                T["tickets"][v["key"]] = t
        old = signal.signal(signal.SIGTERM, _on_term)
        try: out, res = fix_one(c, A, d)
        finally: signal.signal(signal.SIGTERM, old)
        if not A.dry_run:
            with tickets_locked() as T:
                tk, notify = apply_outcome(T["tickets"].get(v["key"]) or new_ticket(c), out, now_cst()); T["tickets"][v["key"]] = tk
            acts = []
            if notify == "norepro":
                p = write_norepro_note(c, res, out["error"], res["evidence"])
                acts.append(dm_action("norepro", c, f"{p}（{out['error']}；证据 {res['evidence']}）"))
            elif notify == "needs_human":
                acts.append(dm_action("needs_human", c, f"{tk.get('last_error')}；证据 {res['evidence']}", key=f"infra-needs-human:{c['sig']}:{tk.get('attempts')}"))
            if acts: record_actions(JOB, d, acts)
    finally:
        unlock(run_lk)
    left = [w for w in vs if w["state"] == "todo" and w["key"] != v["key"]]
    r = {"sig": v["key"], "sig8": c["sig8"], "round": c["round"], "status": tk.get("status") or out["status"], "outcome": out["status"],
         "kind": out.get("kind"), "error": out.get("error"), "action_key": out.get("action_key"), "auto_merge": res.get("auto_merge"),
         "auto_merge_why": res.get("auto_merge_why"), "attempts": tk.get("attempts"), "next_retry_at": tk.get("next_retry_at"),
         "evidence": res.get("evidence"), "pending_after": len(left)}
    return finish(r, 0 if out["status"] in ("pr_action_written", "norepro", "dry_ok") else 1, A)

# ================================================================ watch (zero model; called by prwatch every minute)
def _ci_state(p):
    """statusCheckRollup -> ('running'|'green'|'red', names)"""
    cs = p.get("statusCheckRollup") or []
    if not cs: return "running", []
    rows = [((c.get("conclusion") or c.get("state") or "").upper(), (c.get("status") or "COMPLETED").upper(), c.get("name") or c.get("context")) for c in cs]
    if any(s != "COMPLETED" or v in ("", "PENDING", "EXPECTED") for v, s, _ in rows): return "running", []
    red = [n for v, _, n in rows if v not in ("SUCCESS", "SKIPPED", "NEUTRAL")]
    return ("red", red) if red else ("green", [])

def live_sigs(ctx, exclude):
    """signatures seen in the window that are not fixed yet (keep a shared issue open while any is left)"""
    out = set()
    for s, c in ctx["cands"].items():
        t = ctx["tickets"].get(s) or {}
        if s != exclude and c["count"] and t.get("status") not in DONE_ST: out.add(s)
    return out

def watch(st, kick=None, dry=False, now=None, ghf=None, ctx_f=None):
    """PR found -> pr_open; CI green -> gh.infra.merge; merged -> deploy check -> issue comment/close -> deployed -> verified.
    st: prwatch's state dict for infra (poll timestamps, DM dedupe). Returns a list of event strings."""
    now = now or now_cst(); ghf = ghf or gh; ev = []; d = today()
    if time.time() - st.get("polled_at", 0) < POLL_S: return ev
    try: T = load_tickets().get("tickets", {})
    except Exception as e: return [f"tickets unreadable: {e}"]
    live = {k: t for k, t in T.items() if t.get("status") in ("pr_action_written", "pr_open", "merge_action_written", "awaiting_praise", "ci_red", "merged", "deployed")}
    if not live: return ev
    st["polled_at"] = time.time()
    acts, updates = [], {}
    prs = None
    def upd(k, **kw): updates.setdefault(k, {}).update(kw)
    for k, t in live.items():
        s = t.get("status")
        try:
            if s == "pr_action_written" or (s in ("pr_open", "merge_action_written", "awaiting_praise", "ci_red") and not t.get("pr")):
                if prs is None: prs = ghf(["pr", "list", "-R", PANOPTES_REPO, "--state", "all", "--limit", "200", "--json", "number,state,body,headRefOid,url"])
                mk_ = f"{MARK} {k}" + (f" r{t.get('round')}" if int(t.get("round") or 1) > 1 else "")
                p = next((p for p in prs if mk_ in (p.get("body") or "") and (int(t.get("round") or 1) > 1 or f"{mk_} r" not in (p.get("body") or ""))), None)
                if p: upd(k, status="pr_open", pr=p["number"], pr_url=p.get("url")); ev.append(f"{k[:13]} PR #{p['number']} found"); t = {**t, "status": "pr_open", "pr": p["number"]}; s = "pr_open"
                else: continue
            if s in ("pr_open", "merge_action_written", "awaiting_praise", "ci_red"):
                p = ghf(["pr", "view", str(t["pr"]), "-R", PANOPTES_REPO, "--json", "state,headRefOid,statusCheckRollup,mergedAt,mergeCommit,url"])
                if p["state"] == "MERGED":
                    upd(k, status="merged", merged_at=p.get("mergedAt"), merge_sha=(p.get("mergeCommit") or {}).get("oid")); ev.append(f"{k[:13]} merged"); continue
                if p["state"] == "CLOSED":
                    upd(k, status="rejected"); ev.append(f"{k[:13]} PR closed unmerged"); continue
                cis, red = _ci_state(p); head = p["headRefOid"]
                if cis == "green" and t.get("merge_head") != head:
                    acts.append({"key": f"inframerge:{k}:{t['pr']}:{head[:12]}", "type": "gh.infra.merge", "repo": PANOPTES_REPO, "sig": k, "pr": t["pr"],
                                 "head": head, "auto_merge": bool(t.get("auto_merge")), "url": p.get("url")})
                    upd(k, status="merge_action_written" if t.get("auto_merge") else "awaiting_praise", merge_head=head)
                    ev.append(f"{k[:13]} CI green -> merge action (auto_merge={bool(t.get('auto_merge'))})")
                elif cis == "red" and t.get("red_head") != head:
                    acts.append({"key": f"infra-cired:{k}:{head[:12]}", "type": "slack.dm", "to": "Praise", "sig": k,
                                 "text": PN.text(PN.item("action", f"测试系统的修复 PR #{t['pr']} 自动测试没过",
                                     f"「测试系统自修」为测试系统自己的一个故障（编号 {k[5:13]}）开的修复 PR，自动测试没过：{', '.join(red[:5])}。",
                                     "这个 PR 不会自动合并，故障还在。", todo=["打开 PR 看哪项没过", PN.ask_session(f"panoptes 修复 PR #{t['pr']}（infrafix {k[5:13]}）CI 红：{', '.join(red[:5])}，修好推上去")],
                                     link=p.get("url")))})
                    upd(k, status="ci_red", red_head=head, last_error=f"CI 红 {red[:3]}"); ev.append(f"{k[:13]} CI red")
            elif s == "merged":
                sha = t.get("merge_sha")
                if not sha: continue
                if run(["git", "-C", LIVE, "merge-base", "--is-ancestor", sha, "HEAD"], t=60)[0] != 0: continue   # opsenv has not deployed yet
                rc, o = run(["bash", f"{LIVE}/scripts/bootstrap-box.sh", "--check"], t=300)
                if rc != 0:
                    acts.append({"key": f"infra-deploy-bad:{k}:{sha[:12]}", "type": "slack.dm", "to": "Praise", "sig": k,
                                 "text": PN.text(PN.item("action", f"测试系统的修复 PR #{t['pr']} 上线后环境检查不通过",
                                     f"这个修复 PR 已经部署上去了，但部署后的环境检查报错：{tail(o, 3)[:200]}。",
                                     "测试系统可能被这次修复弄坏了，后面的测试可能跑不起来。",
                                     todo=["先撤回这次修复：在 GitHub 上对 PR #" + str(t['pr']) + " 点 Revert 并合并", PN.ask_session(f"panoptes PR #{t['pr']}（{sha[:12]}）部署后 bootstrap-box.sh --check 不健康，看要不要 revert，查原因")],
                                     link=f"https://github.com/PraiseZhu/panoptes/pull/{t['pr']}"))})
                    upd(k, status="deploy_unhealthy", last_error=f"部署后 --check rc={rc}"); ev.append(f"{k[:13]} deployed but check red"); continue
                ctx = (ctx_f or (lambda: gather(strict=False, now=now)))()
                pre = sorted({s_ for s_, c in ctx["cands"].items() if c["cat"] == "qa_tool_failures"})
                for n in sorted((ctx["cands"].get(k) or {}).get("issues") or set(t.get("issues") or [])):
                    other = [s_ for s_ in live_sigs(ctx, k) if n in ctx["cands"][s_]["issues"] and ctx["cands"][s_]["threshold"] is not None]
                    close = not other
                    acts.append({"key": f"infraissue:{k}:{n}", "type": "gh.infra.issue", "repo": ISSUE_REPO, "issue": n, "close": close, "sig": k,
                                 "comment": f"QA 系统故障 `{k}` 已由 PraiseZhu/panoptes#{t['pr']} 修复并部署（{sha[:7]}），部署后 bootstrap-box.sh --check 通过。"
                                            + ("关闭本 issue；再出现时 Mivo QA 会自动重开。" if close else f"本 issue 里还有 {len(other)} 个未修的故障签名，先不关。")})
                upd(k, status="deployed", deployed_at=now.isoformat(timespec="seconds"), pre_sigs=pre); ev.append(f"{k[:13]} deployed + check ok")
            elif s == "deployed":
                at = ts(t.get("deployed_at"))
                fs = [f for f in report_files() if os.path.getmtime(f) > at.timestamp()] if at else []
                first = None
                for f in fs:
                    try: dd = _rjson(f)
                    except Exception: continue
                    g = ts(dd.get("generated_at"))
                    if g and g > at and (first is None or g < first[0]): first = (g, f, dd)
                if not first:
                    if at and now - at > dt.timedelta(hours=72): upd(k, status="verified", verified_note="部署后 72 小时没有新运行，按已修结单")
                    continue
                o_ = report_occs(first[2], first[1], at)
                again = [o for o in o_ if o["sig"] == k]
                new = sorted({o["sig"] for o in o_ if o["cat"] == "qa_tool_failures" and o["sig"] not in set(t.get("pre_sigs") or [])})
                if again:
                    nxt = int(t.get('round') or 1) + 1
                    what = f"这个修复上线后的第一次测试里，同一个故障（编号 {k[5:13]}）又出现了，说明修复无效。"
                    link = f"https://github.com/PraiseZhu/panoptes/pull/{t['pr']}"
                    if nxt <= MAX_ROUNDS:
                        acts.append({"key": f"infra-recurred:{k}:{t.get('pr')}", "type": "slack.dm", "to": "Praise", "sig": k,
                                     "text": PN.text(PN.item("auto", f"测试系统的修复 PR #{t['pr']} 没修好", what,
                                         "这个故障还在。", auto=f"「测试系统自修」会再修一轮（这是第 {nxt} 轮，最多 {MAX_ROUNDS} 轮）；{MAX_ROUNDS} 轮都不行会找你拍板",
                                         link=link))})
                    else:
                        # last round recurred: evaluate() turns this into a hard stop, but bookkeeping (which would DM it) only runs
                        # inside an `infrafix --next`, and prwatch launches none while nothing is todo -> escalate here, under the key
                        # bookkeeping's needs_human DM uses, so a later --next does not DM it twice
                        acts.append({"key": f"infra-needs_human:{k}", "type": "slack.dm", "to": "Praise", "sig": k,
                                     "text": PN.text(PN.item("action", f"测试系统的一个故障修了 {MAX_ROUNDS} 轮都没修好", what,
                                         "「测试系统自修」不会再自动修了，这个故障会一直在。",
                                         todo=PN.ask_session(f"infrafix 故障 {k[5:13]} 修了 {MAX_ROUNDS} 轮（最后一轮 panoptes PR #{t['pr']}）部署后仍复发，"
                                                             f"看要不要 revert {str(t.get('merge_sha'))[:12]}、换个修法，处理完跑 infrafix.py --reset {k[5:13]}"),
                                         link=link))})
                    upd(k, status="recurred", recurred_at=iso(first[0])); ev.append(f"{k[:13]} recurred")
                elif new:
                    acts.append({"key": f"infra-newfault:{k}:{t.get('pr')}", "type": "slack.dm", "to": "Praise", "sig": k,
                                 "text": PN.text(PN.item("action", f"测试系统的修复 PR #{t['pr']} 上线后出现了新故障",
                                     f"这个修复上线后的第一次测试里，出现了之前没有的测试系统故障（编号 {', '.join(x[5:13] for x in new[:3])}），很可能是这次修复引入的。",
                                     "测试系统可能被这次修复弄坏了。",
                                     todo=["先撤回这次修复：在 GitHub 上对 PR #" + str(t['pr']) + " 点 Revert 并合并", PN.ask_session(f"panoptes PR #{t['pr']} 部署后出现新故障 {', '.join(x[5:13] for x in new[:3])}，确认是不是它引入的")],
                                     link=f"https://github.com/PraiseZhu/panoptes/pull/{t['pr']}"))})
                    upd(k, status="deploy_unhealthy", last_error=f"部署后新故障 {new[:3]}"); ev.append(f"{k[:13]} new fault after deploy")
                else:
                    upd(k, status="verified", verified_at=iso(first[0]), verified_by=first[1]); ev.append(f"{k[:13]} verified")
        except Exception as e:
            ev.append(f"{k[:13]} watch error: {str(e)[:120]}")
    if dry:
        return ev + [f"DRY action {a['type']} {a['key']}" for a in acts]
    if updates:
        with tickets_locked() as TT:
            for k, kw in updates.items():
                if k in TT["tickets"]:
                    TT["tickets"][k].update(kw, updated_at=now.isoformat(timespec="seconds"))
                    if kw.get("status") in ("merged", "rejected"):   # the local branch has done its job
                        try: drop_worktree(None, f"fix/infra-{k[5:13]}")
                        except Exception: pass
    if acts:
        record_actions(JOB, d, acts)
        for a in acts:
            if kick: kick(JOB, a["key"])
    return ev

def main(argv=None):
    ap = argparse.ArgumentParser(description="infrafix (Pi line): one QA-system fault per run")
    ap.add_argument("--next", action="store_true", help="highest-priority ticket (default)")
    ap.add_argument("--sig"); ap.add_argument("--list", action="store_true"); ap.add_argument("--pending-json", action="store_true")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--base"); ap.add_argument("--reset")
    A = ap.parse_args(argv)
    if A.reset:
        with tickets_locked() as T:
            ks = [k for k in T["tickets"] if k.startswith(A.reset) or k[5:].startswith(A.reset)]
            if len(ks) != 1: print(f"--reset {A.reset}: 匹配到 {len(ks)} 个单子 {ks[:5]}，没改"); return 1
            old = T["tickets"].pop(ks[0])
        print(f"已删除单子 {ks[0]}（原状态 {old.get('status')}）；下次 --next 按规则重新判断"); return 0
    real_out = sys.stdout
    ro = A.list or A.pending_json
    if A.pending_json: sys.stdout = sys.stderr       # helper logs must never corrupt the JSON on stdout
    try:
        try: ctx = gather(strict=not ro)
        except Exception as e:
            if ro: print(f"infrafix: 读输入失败：{e}", file=sys.stderr); return 1
            return finish({"status": "error", "error": f"读输入失败：{str(e)[:300]}"}, 1, A)
        for w in ctx["warns"]: log("warn:", w)
        for c in ctx["cands"].values():      # file hints first: the quiet rule quotes a likely fixing commit
            if c["count"] and (A.list or quiet(c, ctx, ctx["now"]) or c["threshold"]):
                c["files"] = files_for(c)
                if quiet(c, ctx, ctx["now"]): c["fixed_hint"] = fixed_hint(c)
        vs = evaluate_all(ctx)
        for v in vs:
            if v["state"] == "held" and not v["c"].get("files"): v["c"]["files"] = KNOWN_FILES.get(v["key"], [])
        if A.list: print_list(vs, ctx); return 0
        if A.pending_json: print(json.dumps(pending(vs), ensure_ascii=False, indent=1), file=real_out); return 0
        log("mode", mode(), "dry" if A.dry_run else "real", f"candidates {len(vs)} todo {sum(v['state'] == 'todo' for v in vs)}")
        try: return run_ticket(A, ctx, vs)
        except Exception as e: return finish({"status": "error", "error": f"内部错误：{e!r}"[:300]}, 1, A)
    finally:
        sys.stdout = real_out

if __name__ == "__main__": sys.exit(main())
