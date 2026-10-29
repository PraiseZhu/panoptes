#!/usr/bin/env python3
"""Praise PR fix duty (Pi line, file-only: never pushes; gh is read-only). ONE ticket per process (2026-10-23 rework).
Model (Praise 10-24): the Pi agent on deepseek-v4.1-flash with thinking max = pi-auto tier `deep` (PRFIX_TIER overrides).
Parallel (Praise 10-24, "并发数量看内存 不设上限"): prwatch starts as many prfix processes as memory allows. There is no global
  run lock: each process claims its ticket atomically under tickets.json's flock (re-read + re-evaluate + mark running), holds a
  per-ticket lock while it works, and serialises only the shared clone's metadata ops (fetch, worktree add/remove, branch -D)
  through out/praise-pr/git.lock. Log lines carry the ticket tag, since parallel runs share out/logs/prfix-<day>.log.
A ticket = one QA finding (report PR N, fingerprint FP).
  Candidates: current reports/merged/PR-*.json ∪ latest ledger rows with status=open; P0, P1, or P2 whose PR is ruled 修 in
    p2-rulings.md. P0/P1 that exist only in reports/merged/history are listed for a human, never auto-picked.
  Claimed (fingerprint level, any one is enough): (a) a PR body (any author) carries the full fingerprint; (b) the ledger row has a
    fix PR not in fix_candidates_rejected / a concluded_reason / a done status; (c) a notes/ file name carries N and (fp8 or the
    level, e.g. p1); (d) a handoff-repo receipt 误报/不修/已修 (handoff.py RECEIPT); (e) the original author (or his handoff-route
    owner) opened a PR after the report that references #N and does not point at a different finding.
  Rounds = PRs whose body has both the fingerprint and "QA: #N" (floor: ledger fix_round). Round 2 only when the ledger row is
    retest_reopened; a third round -> needs_human. Gates (conf/gates.json "no_fix_pr") apply in every mode, --qa included.
Per ticket (memory is the only gate: MemAvailable >= 3G here, prwatch's headroom check before launching; the panoptes
  nightly.lock is NOT taken unless PRFIX_SHARE_WITH_PANOPTES=0, then non-blocking, busy -> exit 75):
  Praise 10-24 ("让 deepseek 能充分利用模型能力自主把修复 PR 推进到正式"): ONE pi session per run (repro -> fix -> pre-push -> docs
  continue the same conversation; the context also carries the diff of the PR QA tested), and every check the harness runs
  answers the model with its (colour-free) output instead of ending the run:
  1 Pi writes ONLY a failing regression test -> vitest must FAIL (no test / test passes on base -> told once; NOREPRO / still
    passing -> norepro note + Slack DM)
  2 Pi writes the minimal fix, up to FIX_ROUNDS: the new regression test is locked (restored before every check); existing tests
    may be updated when they assert the old buggy behaviour (listed under TEST CHANGES, shown in the PR body), never weakened
    (skip/only/todo, fewer cases or assertions -> reverted + told); regression PASS; source reverted -> FAIL; restored -> PASS;
    `vitest related` + same-directory tests PASS (files that fail on base too are noted, not blamed)
  3 the PR number is predicted now; Pi writes docs/pr/<pred>/review.md + qa.md; pr-handoff check --type fix (DOCS_ROUNDS)
  4 real run: branch fix/qa-<N>-<fp8> in worktree /workspace/praise-pr/pi-wt-<N>-<fp8>, local commit as Praise, then the
    repo's own .githooks/pre-push (tsc, lint, verify:*, guards, test:unit); red -> the failed step's output to Pi, step-2 checks,
    amend, pre-push again (PREPUSH_FIX_ROUNDS); a formatter rewrite is amended in. The number is predicted again and the docs dir
    renamed if it moved. Only then <day>-<tag>.pr.patch + .pr.md (PR body per .github/pull_request_template.md) and a
    gh.pr.create(draft) action with docs_dir_predicted (outbound renames docs/pr/<pred> to the real number after creating the PR).
  5 --revise N (prwatch, takeover/prfix-revise): GitHub CI red on our own draft fix PR -> failed-step logs to Pi, step-2 checks,
    pre-push, gh.pr.push-fix action (outbound: plain push onto the PR branch if its head is unchanged). MAX_REVISIONS per PR.
  Anything but success removes the worktree + branch in `finally` (evidence .md/.patch stay in out/praise-pr/).
Tickets: out/praise-pr/tickets.json (flock + atomic write). Transient failures (npm, network, model rc/timeout) back off
  15m / 1h / 4h, the 4th transient failure -> needs_human + Slack DM (waiting for memory backs off 15m without counting);
  deterministic failures (fix still red after the rounds,
  pre-push red, ...) -> needs_human + Slack DM.
Exit: 0 handled (pr_action_written / norepro / dry-run ok) or nothing to do; 75 busy (MemAvailable < 3G, panoptes lock if taken,
  the --qa ticket already running elsewhere); 1 failed / needs_human / gated / error. Last stdout line: RESULT {json}; also
  out/praise-pr/last-result.json (last finisher only; prwatch kicks outbound from tickets.json, which has every run's action).
Usage: prfix.py [--next] | --qa N [--fp FP] | --revise PR | --list | --pending-json | --cleanup-stale [--yes]   [--dry-run] [--base SHA]
  --list / --pending-json are read-only (gh reads only, zero model; the one write is the shared PR-list cache out/praise-pr/cache/).
  --cleanup-stale lists prfix leftovers
  (pi-wt-* worktrees on unpushed fix/qa-<N>-<fp8> branches, /tmp/pi-prfix/*); --yes deletes them.
Env (tests / read-only rehearsal only): PRFIX_OUT PRFIX_NOTES PRFIX_WT_ROOT PRFIX_TMP PRFIX_GIT PRFIX_GATES PRFIX_QA_LOCK."""
import sys, shutil, argparse, fcntl, time, contextlib, tempfile, hashlib
from lib import *
import lib
import praise_notice as PN
import workroot as W
from finding_policy import notifiable, own_pr_ticket, no_fix_rulings
os.environ["PATH"] = "/usr/local/bin:" + os.environ.get("PATH", "")

_E = os.environ.get
# 10-24 (plan §4.1): the bot's own clone and worktrees under PI_WORK -- a person's /workspace/mivo-canvas-plugin and
# /workspace/praise-pr are no longer touched; every worktree carries .pi-owned.json (janitor reaps only marked ones)
def _GIT(): return globals()["GIT"] if "GIT" in globals() else (_E("PRFIX_GIT") or W.clone_dir("mivo"))   # read at call time; a test may set GIT
def _WT_ROOT(): return globals()["WT_ROOT"] if "WT_ROOT" in globals() else (_E("PRFIX_WT_ROOT") or W.wt_root("mivo"))   # read at call time; a test may set WT_ROOT
NOTES = _E("PRFIX_NOTES") or "/workspace/praise-pr/notes"
def _TMPROOT(): return globals()["TMPROOT"] if "TMPROOT" in globals() else (_E("PRFIX_TMP") or W.wt_root("mivo-dry"))   # read at call time; a test may set TMPROOT
OUT = _E("PRFIX_OUT") or f"{BASE}/out/praise-pr"
GATES = _E("PRFIX_GATES") or f"{PI_CODE}/conf/gates.json"
CI_RULES = _E("PRFIX_CI_RULES") or f"{PI_CODE}/conf/ci-rules"   # <repo name>.md: the target repo's CI rules brief (10-25)
def _QA_LOCK(): return globals()["QA_LOCK"] if "QA_LOCK" in globals() else (_E("PRFIX_QA_LOCK") or f"{QA}/argus-root/nightly.lock")   # read at call time; a test may set QA_LOCK   # the flock panoptes run-nightly.sh takes (exec 9>)
RULINGS = "/workspace/fleet-standards/p2-rulings.md"
LEDGER_DIR = f"{QA}/ledger/daily"
GIT_ID = ["-c", "user.name=Praise", "-c", "user.email=zhuzan@xd.com"]   # = repo-local config of /workspace/mivo-canvas-plugin
DEFAULT_GATES = {923: "Praise 闸：#923 修复 PR 不得创建"}               # used only while conf/gates.json does not exist
MAX_ROUNDS = 2
# Praise 10-24 ("让 deepseek 能充分利用模型能力自主把修复 PR 推进到正式"): every check the harness runs feeds its failure back to the
# model in the SAME session (pi resumes a session id), instead of giving up on the first red result.
FIX_ROUNDS = 4          # fix attempts (regression test, related tests, test-edit guard, revert check) before needs_human
REPRO_ROUNDS = 2        # repro attempts (no test / test already passes on base -> told why, once)
DOCS_ROUNDS = 3         # handoff docs attempts (pr-handoff check output fed back)
PREPUSH_FIX_ROUNDS = 2  # extra rounds after a red .githooks/pre-push (failed step output fed back), then needs_human
MAX_REVISIONS = 2       # --revise rounds per bot draft fix PR (red GitHub CI fed back), then needs_human
AUTHOR_SELF = "PraiseZhu"       # Praise 10-23: prfix fixes only our own PRs (others: daily bot -> their handoff repos)
BACKOFF_MIN = (15, 60, 240)        # after the 1st/2nd/3rd transient failure; the 4th -> needs_human
MIN_MEM_GB = 3
BUSY = 75
TIER = _E("PRFIX_TIER") or "deep"   # pi-auto: deep = tap/deepseek/deepseek-v4.1-flash, thinking max (Praise 10-24; was heavy = high)
PR_CACHE_TTL = 60                    # s: parallel runs + prwatch share one PR list (10-24: a full 3000-PR list per run ate GraphQL quota)
PR_CACHE_FULL = 3600                 # s: full re-list at least hourly; in between only PRs updated since the last fetch are re-read
SEV_RANK = {"P0": 0, "P1": 1, "P2": 2}
DONE_ROW = ("fixed", "fix_merged_pending_retest", "fix_pr_open", "concluded")
FP_RE = re.compile(r"mq1-[0-9a-f]{20}")
BRANCH_RE = re.compile(r"^fix/qa-(\d+)-([0-9a-f]{8})$")
RUNNABLE_TEST_RE = re.compile(r"\.(?:test|spec)\.[cm]?[jt]sx?$")
TEST_SUPPORT_RE = re.compile(r"(?:^|/)(?:tests?|__tests__|__fixtures__|fixtures?|__snapshots__)/|\.snap$")
NOREPRO_RE = re.compile(r"(?m)^[\s#>*_`-]*NOREPRO\b")
TRUSTED_PROOF_ERR = "trusted current-head Windows unit and browser smoke success required"
CONTRIB = "贡献流程见 [CONTRIBUTING.md](https://github.com/xindong/mivo-canvas-plugin/blob/main/CONTRIBUTING.md)。"

class Transient(Exception): """npm / network / model rc / timeout: back off and retry"""
class Waiting(Transient): """the box had no memory for us: retry later without spending one of the transient retries"""
class Deterministic(Exception): """the fix itself is not good enough (yet): parked, retried later by itself"""
class PraiseNeeded(Exception): """category 5 (the only things the bot asks Praise about): the fix touches CI config / repo rules / secrets"""
class GateFileError(Exception): pass
class Interrupted(Transient): """SIGTERM while a ticket runs"""
def _on_term(signum, frame): raise Interrupted(f"收到信号 {signum}，本单中断（已清理）")
class NoRepro(Exception):
    def __init__(self, why, text="", confirmed=False, judge=""): super().__init__(why); self.text = text; self.confirmed = confirmed; self.judge = judge
JUDGE_TIER = _E("PRFIX_JUDGE_TIER") or "judge"   # pi-auto judge = claude-opus-5-5 medium (Praise 10-22: opus for rulings)
# 裁决 (Praise 10-24: "pr bot 遇到难点可以调用 opus5.5 来做裁决，但仅限裁决；定义清楚什么问题才需要裁决").
# A ruling is a yes/no judgement the bot's own checks cannot make. The judge reads the worktree (no edits), answers with one verdict
# line plus evidence, never writes code; every question is ruled at most once per run, and the verdict is binding for the bot:
#   R1 不可复现  the fixing model says NOREPRO / cannot write a failing test.  Q: is the finding a real bug on the current code?
#                AGREE (not a bug / already fixed) -> closed by the note, no DM.  DISAGREE -> one more repro round with its reasoning.
#   R2 无关失败  a red pre-push step (already re-run once) or a red draft CI check the fixing model looked at and says is not caused by
#                this change (it changes nothing).  Q: is the failure caused by this PR's change?
#                UNRELATED -> pre-push: the draft PR is opened anyway, GitHub CI decides; CI: its failed jobs re-run once.
#                RELATED -> one more fix round with its reasoning.
#   R3 改老测试  the fix changed the expectation of an EXISTING test case (the mechanical weakening guard already passed).
#                Q: did the old expectation encode exactly the buggy behaviour this fix removes?
#                LEGIT -> kept, ruling quoted in the PR body.  REJECT -> those test edits reverted, one more fix round with its reasoning.
# Everything a check can decide (tests, revert check, pre-push, docs check, conflicts) is never sent to the judge. After the one extra
# round a ruling buys, a still-stuck ticket goes to Praise -- with the ruling attached.
RULING_VERDICTS = {"R1": ("AGREE", "DISAGREE"), "R2": ("UNRELATED", "RELATED"), "R3": ("LEGIT", "REJECT")}
def ruling(kind, c, wd, question, tag):
    """-> (verdict | None, text). verdict is RULING_VERDICTS[kind][0] / [1], None when the judge gave no usable answer."""
    yes, no = RULING_VERDICTS[kind]; x = c.get("finding") or {}
    prompt = (f"You are the judge for one question about a QA fix (ruling {kind}). You only rule: read the code in this worktree, do not edit "
              f"anything, do not write the fix.\nQA finding on PR #{c['qa']} ({c.get('sev')}, {c.get('fp')}): {c.get('title')}\n"
              f"file: {x.get('file')}:{x.get('line')} symbol {x.get('symbol')}\nevidence: {json.dumps(x.get('evidence'), ensure_ascii=False)[:2000]}\n\n"
              f"{question}\n\nStart your reply with exactly one line: `{yes}` or `{no}`. Then the evidence (file:line), short.")
    try: text, _ = pi(JUDGE_TIER, wd, prompt, f"pi-line-prfix-ruling-{kind}-{tag}-{now_cst():%m%d%H%M%S}", timeout=1200, write=False, skills=False)
    except Exception as e: return None, f"裁决模型调用失败：{str(e)[:200]}"
    m = re.search(rf"(?m)^[\s#>*_`-]*({yes}|{no})\b", text or "")
    return (m.group(1) if m else None), text
# When the judge may be asked at all (Praise 10-24 evening: "不应该按次数来限制调用裁决，而是问题分类规则做细；目标是 pr bot 自主推进别来问我").
# Every stop is one of five kinds (Praise-PR.md「修 PR bot 的问题分类」); only kind 2 goes to the judge, and only when:
#   G1 the bot's own means are used up first -- R1 only after the fixing model said NOREPRO (or failed to reproduce) TWICE; R2 only
#      after the failing step was re-run as is and the model looked and changed nothing (call sites); R3 only after the mechanical
#      weakening guard passed.
#   G2 nothing a script can establish decides it (mechanical_ruling): R1 the finding's file no longer exists on base -> AGREE; R2 the
#      failure output names a file this change touched -> RELATED, the same checks are red on main's head -> UNRELATED; R3 an edited
#      old test file has nothing to do with the changed source (other directory, does not name it) -> REJECT.
#   G3 it is a NEW question: the same kind for the same finding on the same code (base / head commit, failing checks) is answered
#      from out/praise-pr/rulings.json, never asked twice. New code (main moved, a new head) = a new question.
# The verdict is final; nothing a ruling decided goes to Praise. A judge that gives no answer is a transient failure (retried).
def _ruling_book(fn):
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/rulings.json.lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try: b = json.loads(_read(f"{OUT}/rulings.json")) if os.path.exists(f"{OUT}/rulings.json") else {}
        except ValueError: b = {}
        r = fn(b); write_atomic(f"{OUT}/rulings.json", json.dumps(b, ensure_ascii=False, indent=1)); return r
def ruling_key(kind, c, base, extra=""):
    return f"{kind}:{c['qa']}:{c['fp']}:{(base or '')[:12]}:{hashlib.sha1(str(extra).encode()).hexdigest()[:8]}"
def main_red_checks():
    """names of check runs that failed on main's head (G2 for R2 on CI); empty when unknown"""
    try:
        cr = gh(["api", f"repos/{REPO}/commits/main/check-runs?per_page=100"]).get("check_runs") or []
        return {x.get("name") for x in cr if (x.get("conclusion") or "").upper() in ("FAILURE", "TIMED_OUT", "CANCELLED")}
    except Exception: return set()
def mechanical_ruling(kind, c, wd, base, failure_text="", changed_files=(), failing_names=(), old_tests=()):
    """G2 -> (verdict, why) when a script can decide, else (None, None)."""
    x = c.get("finding") or {}
    if kind == "R1":
        f = (x.get("file") or "").split(":")[0]
        if f and git("cat-file", "-e", f"{base}:{f}", cwd=wd)[0] != 0: return "AGREE", f"报告指向的文件 {f} 在当前 main 上已经不存在"
    if kind == "R2":
        hit = [f for f in changed_files if f and not f.startswith("docs/") and (f in failure_text or os.path.basename(f) in failure_text)]
        if hit: return "RELATED", f"失败输出里出现了本次改过的文件：{', '.join(hit[:3])}"
        red = main_red_checks() & set(failing_names) if failing_names else set()
        if red and set(failing_names) <= red: return "UNRELATED", f"main 最新提交上同样的检查也是红的：{', '.join(sorted(red)[:3])}"
    if kind == "R3":
        src = [f for f in changed_files if not is_test(f) and not f.startswith("docs/")]
        for t in old_tests:
            body = _read(f"{wd}/{t}") if os.path.exists(f"{wd}/{t}") else ""
            near = any(os.path.dirname(t) == os.path.dirname(f) or os.path.splitext(os.path.basename(f))[0] in body for f in src)
            if not near: return "REJECT", f"改了跟这次修复无关的旧测试 {t}（不在被改源码的目录里，也没引用它）"
    return None, None
SENSITIVE_RE = re.compile(r"^\.github/|(^|/)CODEOWNERS$|(^|/)\.env(\.|$)|secret|credential|\.pem$|\.key$|(^|/)rulesets?/", re.I)
A_ALLOW = {"sensitive": False}   # --allow-sensitive: kept for old commands / DMs; since 10-25 sensitive files no longer stop the bot
# 10-25 (Praise: 「全放行，交给审查机把关」「保证 pr bot 能顺利推进所有 pr」): a fix that touches CI config / repo rules / secrets
# files is no longer a kind-5 stop. The files are listed at the top of the PR's risk section so the reviewer (审查机) and the merge
# gate (humanOnlyPaths) see them; nothing here waits for Praise any more.
def sensitive_files(files): return sorted(f for f in files if SENSITIVE_RE.search(f))
def gated_ruling(kind, c, wd, base, question, tag, step, **facts):
    """G2 + G3 around ruling(): -> (verdict | None, text, judged) ; judged False = decided without asking the judge now."""
    v, why = mechanical_ruling(kind, c, wd, base, **facts)
    if v: step(f"裁决 {kind} 不用问裁决模型，按事实判定：{v}", True, why); return v, why, False
    key = ruling_key(kind, c, base, sorted(facts.get("failing_names") or []))
    hit = _ruling_book(lambda b: (b.get("answers") or {}).get(key))
    if hit: step(f"裁决 {kind}：同一份代码上已经裁过，沿用上次结论 {hit['verdict']}（{hit['at']}）", True, hit["text"][-1500:]); return hit["verdict"], hit["text"], False
    v, t = ruling(kind, c, wd, question, tag)
    if v:
        def keep(b): b.setdefault("answers", {})[key] = {"verdict": v, "text": (t or "")[-4000:], "at": now_cst().isoformat(timespec="seconds")}
        _ruling_book(keep)
    return v, t, True
def judge_norepro(c, wd, claim, tag, base=None, step=None):
    """R1 -> (agree True / False / None, text, asked the judge?)"""
    v, t, asked = gated_ruling("R1", c, wd, base,  "The fixing model could not reproduce it and says it is not a bug on the current code (or already fixed):\n"
                  f"{(claim or '')[-6000:]}\n\nIs the finding NOT a real bug on the current code? AGREE = not a bug / already fixed; DISAGREE = it "
                  "is real (then say how a test can make it fail).", tag, step or (lambda *a: None))
    return (None if v is None else v == "AGREE"), t, asked

# ---------------------------------------------------------------- process helpers (argv only, real exit codes)
def run(cmd, cwd=None, t=1800, env=None, inp=None):
    """Never a shell (dash has no PIPESTATUS). Returns (rc, stdout+stderr); timeout -> 124, missing binary -> 127."""
    kw = {"input": inp} if inp is not None else {"stdin": subprocess.DEVNULL}
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=t, env=env, **kw)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired as e:
        o = e.stdout or ""
        if isinstance(o, bytes): o = o.decode("utf-8", "replace")
        return 124, f"{o}\n[prfix] timeout after {t}s"
    except FileNotFoundError as e:
        return 127, f"[prfix] {e}"
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]")
def clean(s):
    """Tool output without terminal colour codes (10-24: the fix2 prompt for QA1198 quoted vitest as `31m1 failed?[39m`)."""
    return ANSI_RE.sub("", s or "")
NOCOLOR = {"NO_COLOR": "1", "FORCE_COLOR": "0", "CI": "1"}
def _read(p, mode="r"):
    with open(p, mode) as f: return f.read()
def _write(p, data, mode="w"):
    with open(p, mode) as f: f.write(data)
def _rjson(p): return json.loads(_read(p))
def tail(s, n=60): return "\n".join((s or "").rstrip().splitlines()[-n:])
def git(*a, cwd=None, t=600):
    if cwd is None and not os.path.exists(_GIT()) and os.path.abspath(_GIT()) == W.clone_dir("mivo"):
        W.ensure_clone("mivo")      # 10-24: the bot clone, made on first use (a test pointing GIT elsewhere never gets here)
    return run(["git", *a], cwd=cwd or _GIT(), t=t)
def fp8(fp): return (fp or "")[4:12]
def iso(t): return t.astimezone(CST).isoformat(timespec="seconds") if t else None
def ts(s):
    try: return parse_ts(s) if isinstance(s, str) else s
    except Exception: return None
def mem_gb():
    ov = _E("PRFIX_TEST_MEM_GB")   # tests only
    if ov: return float(ov)
    for l in _read("/proc/meminfo").splitlines():
        if l.startswith("MemAvailable"): return int(l.split()[1]) / 1048576
    return 0.0
def pid_alive(pid):
    try: return bool(pid) and "prfix" in _read(f"/proc/{int(pid)}/cmdline", "rb").decode("utf-8", "replace")
    except Exception: return False
def try_lock(path):
    """Non-blocking flock; returns the open file (keep it) or None when someone else holds it."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    f = open(path, "a")
    try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); return f
    except BlockingIOError: f.close(); return None
def unlock(f):
    if f:
        try: fcntl.flock(f, fcntl.LOCK_UN)
        finally: f.close()
@contextlib.contextmanager
def repo_lock():
    """10-24 parallel runs: serialise what changes the shared clone's metadata (fetch, worktree add/remove/prune, branch -D).
    Edits, tests, commit and pre-push inside a ticket's own worktree run in parallel."""
    if os.path.abspath(_GIT()) == W.clone_dir("mivo"):           # the bot clone: the same lock janitor / opsfix take
        W.ensure_clone("mivo")                                 # made once, before the lock (cloning takes that lock itself)
        with W.repo_lock("mivo"): yield
        return
    os.makedirs(OUT, exist_ok=True)
    f = open(f"{OUT}/git.lock", "a"); fcntl.flock(f, fcntl.LOCK_EX)
    try: yield
    finally: unlock(f)
_TAG = {"t": ""}                    # this process's ticket tag: parallel runs share out/logs/prfix-<day>.log
_lib_log = log
def log(*a): _lib_log(*((f"[{_TAG['t']}]",) if _TAG["t"] else ()), *a)
_QA_HOLD = {}   # the nightly.lock file while a ticket runs (only when SHARE_WITH_PANOPTES is False)
# Praise 2026-10-23 23:xx: "机器内存够同时跑" -> prfix no longer takes panoptes' nightly.lock; merge tests never wait for a fix.
# The guard is memory instead: MemAvailable >= 3G before every heavy step (wait up to 30 min, then transient back-off).
SHARE_WITH_PANOPTES = os.environ.get("PRFIX_SHARE_WITH_PANOPTES", "1") != "0"
def yield_to_panoptes():
    """Called before every model step and heavy local step (10-23): merge tests go first. If a panoptes run is queued on
    nightly.lock, hand the lock over, wait until panoptes is done, then take it back and continue the ticket. Praise's rule
    (no prfix tests while panoptes runs) still holds: nothing of ours runs while we do not hold the lock."""
    f = _QA_HOLD.get("f")
    if SHARE_WITH_PANOPTES:
        t0 = time.time()
        while mem_gb() < MIN_MEM_GB:
            if time.time() - t0 > 1800: raise Waiting(f"等内存超过 30 分钟（MemAvailable {mem_gb():.1f}G < {MIN_MEM_GB}G）")
            time.sleep(30)
        return
    if not f or subprocess.run(["pgrep", "-f", "panoptes/run-nightly.sh"], capture_output=True).returncode != 0: return
    log("panoptes run queued -> yield nightly.lock until it is done (merge tests first)")
    fcntl.flock(f, fcntl.LOCK_UN); time.sleep(5); t0 = time.time()
    while True:
        try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); break
        except BlockingIOError:
            if time.time() - t0 > 4 * 3600: raise Transient("让出测试锁后等 panoptes 超过 4 小时")
            time.sleep(15)
    log(f"nightly.lock back after {int(time.time() - t0)}s")

# heavy local steps run at nice 10 (10-24): prfix shares the CPU with merge tests (memory is the only gate), and panoptes'
# 5 s unit-test timeouts tripped under the load (PR-1256 needs_review, #1227 flaky). Merge tests go first, also on the CPU.
NICE = ["nice", "-n", "10"]
def vitest_full(wd, args, t=900):
    """npx vitest <args> without colour; full (cleaned) output. rc 124 = timeout."""
    yield_to_panoptes()
    rc, out = run([*NICE, "timeout", "-k", "30", str(t), "npx", "vitest", *args], cwd=wd, t=t + 120, env=dict(os.environ, **NOCOLOR))
    return rc, clean(out)
def vitest(wd, files, t=900):
    """Targeted vitest; the output tail is cut here in Python. rc 124 = timeout."""
    rc, out = vitest_full(wd, ["run", *files], t)
    return rc, tail(out, 60)
FAILED_FILE_RE = re.compile(r"(?m)^\s*(?:FAIL|❯|×)\s+(\S+?\.(?:test|spec)\.[cm]?[jt]sx?)(?=[\s:>]|$)")
def failed_files(out):
    """test files vitest reported as failing (FAIL lines and the ❯ file summary lines), in order, no duplicates."""
    return list(dict.fromkeys(FAILED_FILE_RE.findall(clean(out))))
FAIL_MARK_RE = re.compile(r"(?m)^.*(?:⎯+ Failed Tests|^\s*FAIL\s|^\s*❯ \S+\.(?:test|spec)\.\S+ \(\d+ tests?.*failed|error TS\d+|^\[preflight\] ❌|✖ \d+ problem).*$")
NOISE_RE = re.compile(r"(?m)^(?:DOMException \[AbortError\].*|\s+at .*)\n?")
def failing_blocks(out, n=4000):
    """the part of a vitest / pre-push log a model needs: from the first real failure marker (Failed Tests / FAIL / failed file /
    tsc error / the pre-push ❌ line) on, else the first Error line, else the tail. Stack lines and happy-dom AbortError teardown
    noise are dropped first (10-24 QA1198: 6 KB of AbortError stacks came before the one failing test)."""
    o = NOISE_RE.sub("", clean(out)).rstrip()
    m = FAIL_MARK_RE.search(o) or re.search(r"(?m)^.*(?:\bFAIL\b|❯|×|Error|✖|❌).*$", o)
    seg = o[m.start():] if m else o
    return seg if len(seg) <= n else seg[:n // 2] + "\n…\n" + seg[-n // 2:]
def same_dir_tests(wd, files):
    """existing test files next to the changed source files (same directory and its __tests__/)."""
    out = []
    for f in files:
        d = os.path.dirname(f)
        for sub in (d, os.path.join(d, "__tests__")):
            p = os.path.join(wd, sub)
            if os.path.isdir(p):
                out += [os.path.join(sub, x) for x in sorted(os.listdir(p)) if RUNNABLE_TEST_RE.search(x)]
    return list(dict.fromkeys(out))
def related_run(wd, src, extra=()):
    """The module's other tests (10-24 #1188: an existing tick.test.mjs broke and only pre-push saw it, 15 min later):
    `vitest related --run <changed src>` + the tests next to the changed files + `extra`. -> (rc, full output, files run or None
    when vitest picked them itself). Timeout of the related run -> only the same-directory ones."""
    near = list(dict.fromkeys([*same_dir_tests(wd, src), *extra]))
    if src:
        rc, out = vitest_full(wd, ["related", "--run", *src, *near], t=1200)
        if rc != 124 and "No test files found" not in out: return rc, out, None
    if not near: return 0, "（没有相关的已有测试）", []
    rc, out = vitest_full(wd, ["run", *near], t=900)
    return rc, out, near
def vitest_verdict(rc, out):
    """'pass' / 'fail' (tests ran); infrastructure problems raise Transient instead of passing for a red test."""
    if rc == 124: raise Transient(f"vitest 超时：{tail(out, 3)}")
    if rc == 127: raise Transient(f"vitest 起不来：{tail(out, 3)}")
    if "No test files found" in (out or ""): raise Transient("vitest 没找到测试文件（测试路径不在 vitest include 里？）")
    return "pass" if rc == 0 else "fail"
def npm_ci(wd):
    yield_to_panoptes()
    rc, out = run([*NICE, "npm", "ci", "--ignore-scripts", "--prefer-offline", "--no-audit", "--no-fund"], cwd=wd, t=1500)
    return rc, tail(out, 30)

# ---------------------------------------------------------------- inputs
def load_gates(path=None):
    """{pr: reason}. Missing file -> built-in default (+ warning); unreadable file -> GateFileError (fail closed)."""
    path = path or GATES
    if not os.path.exists(path): return dict(DEFAULT_GATES), f"{path} 不存在，暂用内置闸 {sorted(DEFAULT_GATES)}"
    try:
        g = _rjson(path).get("no_fix_pr", {})
        if not isinstance(g, dict): raise ValueError("no_fix_pr 不是对象")
        return {int(k): str(v) for k, v in g.items()}, None
    except Exception as e:
        raise GateFileError(f"闸名单 {path} 读不出（{e}），拒绝运行")

def fix_rulings(text):
    """PRs whose P2 ruling is exactly 修 (已修 = fixed elsewhere; 不修 / 产品代码不修 = no PR)."""
    s = set()
    for line in (text or "").splitlines():
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(c) >= 4 and re.fullmatch(r"#?\d+", c[1] or "") and c[3] == "修": s.add(int(c[1].lstrip("#")))
    return s

def latest_ledger():
    fs = sorted(f for f in glob.glob(f"{LEDGER_DIR}/*.json") if re.search(r"/\d{4}-\d{2}-\d{2}\.json$", f))
    if not fs: return None, {}
    return fs[-1], {(r["pr"], r.get("fingerprint")): r for r in _rjson(fs[-1]).get("rows", [])}

def load_reports():
    """{N: {"cur": (t, pr, file) | None, "hist": [(t, pr, file)] newest first}}"""
    reps = {}
    def rd(f):
        try: d = _rjson(f)
        except Exception: return None, []
        return ts(d.get("generated_at") or (d.get("run") or {}).get("finished_at")), d.get("prs", []) or []
    for f in glob.glob(f"{RPT}/PR-*.json"):
        if not re.search(r"/PR-\d+\.json$", f): continue
        t, prs = rd(f)
        for p in prs: reps.setdefault(p["number"], {"cur": None, "hist": []})["cur"] = (t, p, f)
    for f in glob.glob(f"{RPT}/history/PR-*.v*.json"):
        t, prs = rd(f)
        for p in prs: reps.setdefault(p["number"], {"cur": None, "hist": []})["hist"].append((t, p, f))
    for f in glob.glob(f"{QA}/reports/acceptance/review-PR-*.json"):   # acceptance re-reviews (context for ledger-only rows)
        _, prs = rd(f)
        for p in prs: reps.setdefault(p["number"], {"cur": None, "hist": []}).setdefault("acc", []).extend(p.get("findings") or [])
    for r in reps.values():
        r["hist"].sort(key=lambda v: (v[0] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), v[2]), reverse=True)
    return reps

def author_of(p):
    a = (p or {}).get("author")
    return (a.get("login") if isinstance(a, dict) else a) or None

def parse_receipts(gh_issues, receipt_re, mark_re):
    """handoff repos -> receipts {(N, fp): (结论, url)} and issue links {url | 'owner/repo#k': (N, fp)}."""
    receipts, links = {}, {}
    for repo, issues in (gh_issues or {}).items():
        for i in issues or []:
            body = i.get("body") or ""; m = mark_re.search(body)
            if m:
                n = int(m.group(2)); key = m.group(1)
                fps = {key[:-len(f"-pr{n}")]} if key.endswith(f"-pr{n}") else set(FP_RE.findall(key))
            else:
                mt = re.search(r"#(\d+)", i.get("title") or ""); n = int(mt.group(1)) if mt else None
                fps = set(FP_RE.findall(body))
            if not n or not fps: continue
            recs = [x.group(1) for x in (receipt_re.match(c.get("body") or "") for c in i.get("comments") or []) if x]
            for fp in fps:
                for ln in (i.get("url"), f"{repo}#{i.get('number')}"):
                    if ln: links[ln] = (n, fp)
                if recs: receipts[(n, fp)] = (re.sub(r"\s+", " ", recs[-1]), i.get("url"))
    return receipts, links

def load_tickets():
    p = f"{OUT}/tickets.json"
    if not os.path.exists(p): return {"tickets": {}}
    return _rjson(p)               # unreadable -> exception -> run fails closed (backoff state must not be lost)

@contextlib.contextmanager
def tickets_locked():
    os.makedirs(OUT, exist_ok=True)
    lk = open(f"{OUT}/tickets.json.lock", "w"); fcntl.flock(lk, fcntl.LOCK_EX)
    try:
        doc = load_tickets(); doc.setdefault("tickets", {})
        yield doc
        doc["updated_at"] = now_cst().isoformat(timespec="seconds")
        write_atomic(f"{OUT}/tickets.json", json.dumps(doc, ensure_ascii=False, indent=1))
    finally:
        fcntl.flock(lk, fcntl.LOCK_UN); lk.close()

PR_FIELDS = "number,author,title,body,createdAt,state,mergedAt"
# A note that only PARKED a confirmed P2 until Praise settled the P2 rule (10-23 morning: "P2 暂停，不开 PR" / "等 Praise 确认规则")
# is no conclusion. The rule is settled (p2-rulings header, 10-23: confirmed P2 are fixed), so such notes no longer claim the
# finding (10-24 audit: #1148 / #1177 / #1198, all "真问题", were stuck behind them for a day).
PARKED_NOTE = re.compile(r"P2\s*(?:新\s*PR\s*)?暂停|等\s*Praise\s*确认规则")
def note_claims(path):
    try: return not PARKED_NOTE.search(open(path, errors="replace").read(4000))
    except OSError: return True
def _gh_json(args, t):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=t, stdin=subprocess.DEVNULL)
    if r.returncode: raise RuntimeError(f"gh {' '.join(args[:3])} …: {(r.stderr or '').strip()[:300]}")
    return json.loads(r.stdout)
def pr_list():
    """Every PR of REPO (gh pr list --json PR_FIELDS), shared by parallel prfix runs and prwatch through out/praise-pr/cache/prs.json
    (10-24). Fresher than PR_CACHE_TTL: as is. Older: one search page of the PRs updated since the last fetch (5 min overlap for the
    search index) merged in. No cache / full list older than PR_CACHE_FULL / a full search page: the whole list again (300 s
    timeout; 10-23 22:00 the old 120 s one timed out). A failed refresh raises, so gather() still fails closed."""
    d = f"{OUT}/cache"; os.makedirs(d, exist_ok=True); p = f"{d}/prs.json"
    lk = open(f"{d}/prs.lock", "a"); fcntl.flock(lk, fcntl.LOCK_EX)          # one refresher; the others wait and reuse its result
    def save(c):
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".prs.json.")
        with os.fdopen(fd, "w") as f: f.write(json.dumps(c, ensure_ascii=False))
        os.replace(tmp, p); return c["prs"]
    try:
        now = time.time()
        try: c = _rjson(p)
        except Exception: c = None
        if c and now - c.get("fetched_at", 0) < PR_CACHE_TTL: return c["prs"]
        if c and now - c.get("full_at", 0) < PR_CACHE_FULL:
            since = dt.datetime.fromtimestamp(c["fetched_at"] - 300, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            upd = _gh_json(["pr", "list", "-R", REPO, "--state", "all", "--limit", "200", "--search", f"updated:>={since}", "--json", PR_FIELDS], 120)
            if len(upd) < 200:
                by = {x["number"]: x for x in c["prs"]}; by.update({x["number"]: x for x in upd})
                return save({"fetched_at": now, "full_at": c["full_at"], "prs": sorted(by.values(), key=lambda x: -x["number"])})
        return save({"fetched_at": now, "full_at": now,
                     "prs": _gh_json(["pr", "list", "-R", REPO, "--state", "all", "--limit", "3000", "--json", PR_FIELDS], 300)})
    finally: unlock(lk)

def gather():
    """Everything the decision needs, read-only (local files + gh reads; the only write is the shared PR-list cache). Raises on
    anything it cannot verify."""
    import handoff
    gates, gate_warn = load_gates()
    lf, ledger = latest_ledger()
    routes = handoff.routes()
    owner = {a: rt["owner"] for rt in routes for a in rt.get("authors", [])}
    repo_of = {a: rt["repo"] for rt in routes for a in rt.get("authors", [])}
    prs = pr_list()
    _, _, gi = handoff.known_keys(gh_ok=True)
    receipts, links = parse_receipts(gi, handoff.RECEIPT, handoff.MARK)
    for (n, fp), r in ledger.items():
        u = (r.get("handoff_issue") or {}).get("url")
        if u: links.setdefault(u, (n, fp))
    try: g = _rjson(GATES) if os.path.exists(GATES) else {}
    except Exception: g = {}
    sent = _rjson(f"{QA}/feishu-sent.json") if os.path.exists(f"{QA}/feishu-sent.json") else {}
    return {"gates": gates, "gate_warn": gate_warn, "ledger_file": lf, "ledger": ledger, "reports": load_reports(),
            "dms": dm_assignments(sent),
            "fix2": fix_rulings(_read(RULINGS)) if os.path.exists(RULINGS) else set(),
            "nofix": no_fix_rulings(_read(RULINGS)) if os.path.exists(RULINGS) else set(),
            "notes": sorted(f for f in os.listdir(NOTES) if os.path.isfile(os.path.join(NOTES, f)) and note_claims(os.path.join(NOTES, f)))
                     if os.path.isdir(NOTES) else [],
            "prs": prs, "receipts": receipts, "links": links, "owner": owner, "repo_of": repo_of,
            "unread_repos": sorted({rt["repo"] for rt in routes} - set(gi)), "tickets": load_tickets().get("tickets", {}),
            "outbound": outbound_ledger()}
OUTBOUND_LEDGER = _E("PRFIX_OUTBOUND_LEDGER") or f"{BASE}/out/outbound/ledger.json"
def outbound_ledger():
    """{action key: outbound status} (read-only; 10-24: an action outbound gave up on must not hold its ticket forever)."""
    try: return {k: (v or {}).get("status") for k, v in _rjson(OUTBOUND_LEDGER).items()}
    except Exception: return {}

# ---------------------------------------------------------------- candidates and claims (pure)
# 统一分级（Praise 2026-10-23）：候选只取作者口径里成立的问题（finding_policy.notifiable，new_notice=True：不取待核实、只差覆盖率、
# AI 补测线索、flaky / unverified、PLAUSIBLE 或没复核证实的 AI 单独 P1）。P2：新口径报告（带 pending_verification）里的 P2 已按
# severity.md 确认成立 → 要修；旧报告的 P2 仍只取 p2-rulings.md 判「修」的
def want(n, sev, fix2, unified=False): return sev in ("P0", "P1") or (sev == "P2" and (unified or n in fix2))

def dm_assignments(sent):
    """feishu-sent.json -> {"fps": {(N, fp)}, "prs": {N}}: problems already DM'd to their author (Grok form <repo>#N:fp +
    covered_fingerprints; Pi form dm:<day>:N = the whole PR)."""
    fps, prs = set(), set()
    for k, v in (sent or {}).items():
        m = re.match(rf"^{re.escape(REPO)}#(\d+):(mq1-[0-9a-f]+)$", k)
        if m:
            fps.add((int(m.group(1)), m.group(2)))
            for f in (v or {}).get("covered_fingerprints") or []: fps.add((int(m.group(1)), f))
            continue
        m = re.match(r"^dm:\d{4}-\d{2}-\d{2}:(\d+)$", k)
        if m and (v or {}).get("sent_at"): prs.add(int(m.group(1)))
    return {"fps": fps, "prs": prs}

# own_sev = finding_policy.own_pr_ticket (moved there 2026-10-23 so build-ledger.mjs keeps the same owner rows; body unchanged)
own_sev = own_pr_ticket
def owners():
    """PR authors whose findings prfix fixes (Praise 10-23: only our own PRs; others go to their handoff repos). '*' = all (tests)."""
    o = os.environ.get("PRFIX_OWNERS", AUTHOR_SELF)
    return None if o.strip() == "*" else {x.strip() for x in o.split(",") if x.strip()}

def collect(reps, ledger, fix2, nofix=frozenset()):
    """current report ∪ ledger open rows (fp absent from the current report) ∪ history-only (listed, never auto-picked).
    Only PRs authored by owners() (default PraiseZhu)."""
    out, cur_fps = {}, {}; own = owners()
    mine = lambda a: own is None or (a or "") in own
    def add(n, x, source, author, row=None):
        fp = x.get("fingerprint")
        out[(n, fp)] = {"qa": n, "fp": fp, "sev": x.get("severity") or (row or {}).get("level"), "source": source, "author": author,
                        "title": x.get("title") or x.get("check_name") or x.get("check_id") or (row or {}).get("summary") or "",
                        "finding": x, "row": row}
    for n, r in reps.items():
        if not r["cur"]: continue
        _, p, _ = r["cur"]; fs = (p.get("findings") or []) + (p.get("pending_verification") or [])
        cur_fps[n] = {x.get("fingerprint") for x in fs}
        if not mine(author_of(p)): continue
        pv = {id(x) for x in p.get("pending_verification") or []}
        for x in fs:
            pend = id(x) in pv or not notifiable(x, new_notice=True)
            sev = x.get("fingerprint") and own_sev(x, n, fix2, "pending_verification" in p, nofix=nofix, pending=pend)
            if sev: add(n, dict(x, severity=sev, pending=pend), "report", author_of(p))
    for (n, fp), row in ledger.items():
        if not fp or row.get("status") != "open" or (n, fp) in out or fp in cur_fps.get(n, set()): continue
        if not mine(row.get("author")) or not want(n, row.get("level"), fix2, True): continue
        if row.get("level") == "P2" and (n in nofix or row.get("pending_verification")): continue
        h = next((x for _, p, _ in (reps.get(n) or {}).get("hist", []) for x in p.get("findings") or [] if x.get("fingerprint") == fp), None) \
            or next((x for x in (reps.get(n) or {}).get("acc", []) if x.get("fingerprint") == fp), None)
        x = dict(h or {}, fingerprint=fp, severity=row.get("level"))
        x.setdefault("title", row.get("summary")); x.setdefault("source", row.get("source"))
        if h and not own_sev(x, n, fix2, False, nofix=nofix, pending=bool(row.get("pending_verification")) or not notifiable(x, new_notice=True)): continue   # dismissed / coverage-only / qa-error / unconfirmed-P2 rows are not tickets
        add(n, x, "ledger", row.get("author"), row)
    for n, r in reps.items():
        for _, p, _ in r["hist"]:
            if not mine(author_of(p)): continue
            for x in p.get("findings") or []:
                fp = x.get("fingerprint")
                if fp and (n, fp) not in out and fp not in cur_fps.get(n, set()) and want(n, x.get("severity"), fix2) \
                        and (x.get("severity") in ("P0", "P1")):
                    add(n, x, "history", author_of(p))
    for c in out.values():
        if c["row"] is None: c["row"] = ledger.get((c["qa"], c["fp"]))
    return out

def times(c, reps):
    """first_seen = earliest report version carrying the fingerprint (else ledger notice, else the PR's first report)."""
    n, fp = c["qa"], c["fp"]; r = reps.get(n) or {"cur": None, "hist": []}
    vers = ([r["cur"]] if r["cur"] else []) + r["hist"]
    hit = [t for t, p, _ in vers if t and any(x.get("fingerprint") == fp for x in p.get("findings") or [])]
    first_report = min((t for t, _, _ in vers if t), default=None)
    fs = min(hit) if hit else ts(((c.get("row") or {}).get("notice") or {}).get("dm_sent_at")) or first_report
    return fs, (fs + dt.timedelta(hours=24) if fs else None), first_report

def qa_ref_re(n):
    """QA: #N / QA #N / QA#N / qa：#N, also right after Chinese text ("修复QA：#N"; \b would not match there), and since 10-24 one fix
    PR for several reports: up to 8 non-# chars after QA, then a list of PR numbers split by / , ， 、 & 和
    ("QA 回访 #904/#910/#898/#884", #918). Same rule as handoff.qa_ref and panoptes report/handoff-receipts.mjs qaRefRe."""
    one = r"(?:[\w.-]+/[\w.-]+)?#"
    return re.compile(rf"(?<![A-Za-z])QA[^#\n]{{0,8}}?(?:{one}[0-9]+(?![0-9])\s*[/,，、&和]\s*)*{one}{int(n)}(?![0-9])", re.I)
def fmt_prs(ps): return " ".join(f"#{p['number']}({(p.get('state') or '?').lower()})" for p in ps)

def ticket_view(t, now):
    """A ticket as --next would see it after normalising a dead 'running' entry into a transient failure."""
    if not t: return None
    t = dict(t)
    if t.get("status") == "running" and not pid_alive(t.get("pid")):
        t = apply_outcome(t, {"status": "failed", "kind": "transient", "error": "上次运行中断（进程已不在）"}, now)[0]
    return t

def evaluate(c, ctx, now=None):
    """-> verdict {state: todo|skip|claimed, reason, claims[], skips[], rounds, round, needs_human, first_seen, deadline}."""
    now = now or now_cst()
    n, fp, sev = c["qa"], c["fp"], c["sev"]; row = c.get("row") or {}
    body = lambda p: p.get("body") or ""
    claims, skips, nh, limit = [], [], None, None
    gate = ctx["gates"].get(n)
    if gate: skips.append(gate)
    reopened = bool(row.get("retest_reopened") and row.get("status") == "open")
    # (a) any author's PR body carries the full fingerprint; rounds
    fp_prs = [p for p in ctx["prs"] if fp in body(p)]
    rq = qa_ref_re(n); round_prs = [p for p in fp_prs if rq.search(body(p))]
    try: lrounds = int(row.get("fix_round") or 0)
    except (TypeError, ValueError): lrounds = 0
    rounds = max(len(round_prs), lrounds)
    open_ = [p for p in fp_prs if p.get("state") == "OPEN"]; merged = [p for p in fp_prs if p.get("state") == "MERGED"]
    if open_: claims.append(f"修复 PR 进行中（正文含指纹）：{fmt_prs(open_)}")
    elif merged and not reopened: claims.append(f"修复 PR 已合并（正文含指纹）：{fmt_prs(merged)}")
    # (b) ledger
    if row:
        rej = set(row.get("fix_candidates_rejected") or [])
        fixes = [x for x in ([row.get("fix_pr")] + [f.get("pr") for f in row.get("fix_prs") or []]) if x and x not in rej]
        fixes = list(dict.fromkeys(fixes))
        if row.get("concluded_reason"): claims.append(f"台账结论：{row['concluded_reason'][:80]}")
        elif not reopened and (fixes or row.get("status") in DONE_ROW):
            claims.append(f"台账：{row.get('status_zh') or row.get('status')}" + (f" 修复 PR {' '.join('#' + str(x) for x in fixes)}" if fixes else ""))
        if row.get("needs_human"): nh = nh or "台账标了 needs_human"
    # (c) notes/: file name carries N and (fp8 or this level)
    for name in ctx["notes"]:
        low = name.lower()
        if not re.search(rf"(?<!\d){n}(?!\d)", low): continue
        lv = set(re.findall(r"(?<![a-z0-9])(p[0-3])(?=[a-z]?(?:[^a-z0-9]|$))", low))
        if fp8(fp) in low or (sev or "").lower() in lv: claims.append(f"已有 note：{name}")
    # (d) handoff receipt
    rc = ctx["receipts"].get((n, fp))
    if rc:
        extra = ""
        m = re.search(r"已修\s*#?(\d+)", rc[0])
        if m and int(m.group(1)) in set(row.get("fix_candidates_rejected") or []): extra = f"（注意：台账已拒认 #{m.group(1)} 为这条的修复 PR，需人工核对）"
        claims.append(f"交接回执「{rc[0]}」{rc[1] or ''}{extra}")
    # (e) the original author (or his handoff-route owner) opened a PR after the report that references #N
    au = c.get("author") or row.get("author")
    sides = {au, ctx["owner"].get(au)} - {None}
    _, _, first_report = times(c, ctx["reports"])
    t0 = first_report or c.get("first_seen")
    others = {f for (m_, f) in ctx["links"].values() if m_ == n} | {k[1] for k in ctx["ledger"] if k[0] == n} \
        | {x.get("fingerprint") for v in ([(ctx["reports"].get(n) or {}).get("cur")] + (ctx["reports"].get(n) or {}).get("hist", [])) if v
           for x in v[1].get("findings") or []}
    attributed = {}   # PR -> fingerprints the ledger already attributes it to (fix_prs) or rejects it for
    for (m_, f), r in ctx["ledger"].items():
        if m_ != n: continue
        for x in [r.get("fix_pr")] + [y.get("pr") for y in r.get("fix_prs") or []]:
            if x: attributed.setdefault(x, set()).add(f)
    rej_here = set(row.get("fix_candidates_rejected") or [])
    for p in ctx["prs"]:
        if author_of(p) not in sides or p.get("state") not in ("OPEN", "MERGED") or fp in body(p): continue
        pt = ts(p.get("createdAt"))
        if t0 and pt and pt <= t0: continue
        text = (p.get("title") or "") + "\n" + body(p)
        if not re.search(rf"#{n}(?!\d)|/pull/{n}(?!\d)", text): continue
        if p["number"] in rej_here: continue
        if p["number"] in attributed and fp not in attributed[p["number"]]: continue
        named = ({f for f in FP_RE.findall(body(p)) if f in others}
                 | {f for ln, (m_, f) in ctx["links"].items() if m_ == n and re.search(re.escape(ln) + r"(?!\d)", text)})
        if named and fp not in named: continue
        refs = set(re.findall(r"#(\d{3,5})(?!\d)", text))
        if len(refs) > 6 and not re.search(rf"#{n}(?!\d)", p.get("title") or ""): continue   # changelog / listing PRs
        claims.append(f"作者 {author_of(p)} 在修：#{p['number']}（{p['state'].lower()}，{cst(p.get('createdAt'))} 北京）")
    # Pi's own ticket history
    tk = ticket_view(ctx["tickets"].get(f"{n}:{fp}"), now) or {}
    st = tk.get("status")
    if st == "pr_action_written":   # waits for outbound until a PR body carries THIS action's marker (outbound appends it):
        m = re.search(r":r(\d+)(?::a\d+)?$", tk.get("action_key") or "")   # round 1 "fp", round N "fp rN" -- 10-24: for round 2 the merged
        mark = fp + (f" r{m.group(1)}" if m else "")              # round-1 PR already has fp, so "no fp PR" re-opened the ticket
        if (ctx.get("outbound") or {}).get(tk.get("action_key")) == "gave_up":
            # outbound gave up creating the PR (patch no longer applies on main, push refused, ...): fix it again on the current main
            # under a new key -- once; a second give-up waits for a human (no endless fix / give-up loop)
            if int(tk.get("gave_up_count") or 0) >= 1:
                limit = f"修好的修复 PR 外发两次都没开出来（最后一次 {tk.get('action_key')}），停下等人看外发日志"; nh = nh or limit
        elif not any(mark in body(p) for p in ctx["prs"]): claims.append(f"Pi 已写修复 PR 动作 {tk.get('action_key')}（{tk.get('updated_at')}），等外发")
    elif st == "norepro" and tk.get("confirmed"): claims.append(f"两个模型都判不可复现，已结案（{tk.get('updated_at')}）")
    # policy stops (only matter when nobody holds it)
    floc = (c.get("finding") or {}).get("file") or ""
    manual = None   # 10-27 (owner): 修不修只看统一分级 argus/config/severity.md；P0-P2 必修，Pi 自己修不了的转 Praise PR bot 人工工单，不自判「不修/转人工」
    if not claims and re.search(r"\.(?:md|mdx|txt)$", floc):
        manual = f"文档类问题（{floc}）没法用失败的回归测试证明，Pi 不能自动修；按统一分级 {c['sev']} 必修，转 Praise PR bot 人工工单（待派单）"
    if not claims and not limit and not manual:
        if rounds >= MAX_ROUNDS: limit = f"已用 {rounds} 轮修复 PR（{fmt_prs(round_prs) or '台账 fix_round'}），第三轮转人工"; nh = nh or limit
        elif round_prs and not reopened and all(p.get("state") == "CLOSED" for p in round_prs):
            # 10-24: a fix PR somebody closed without merging used to freeze the finding silently (a round, never retried, no DM)
            limit = f"修复 PR {fmt_prs(round_prs)} 被关掉了、没有合并；bot 不会自己重开，要你决定重修还是放弃"; nh = nh or limit
        elif rounds >= 1 and not reopened: skips.append(f"已有 {rounds} 轮修复 PR（{fmt_prs(round_prs) or '台账 fix_round'}），台账未标复测重开，不开第二轮")
    if c["source"] == "history":
        skips.append("仅见于历史报告版本（当前报告已无此条，台账也没有），待人工确认"
                     + ("" if (limit or manual) else f"；要自动修就跑 prfix.py --qa {n} --fp {fp}"))
    au_repo = ctx["repo_of"].get(au)
    if au_repo and au_repo in ctx.get("unread_repos", []): skips.append(f"交接仓 {au_repo} 回执没读到（gh 读取失败），先不做")
    # a ticket that stopped under an older harness is tried again once with the current one (10-24: #1188 / #1198 each needed a manual
    # --qa rerun after the fix that would have unblocked them went live)
    if st == "needs_praise": nh = nh or f"等 Praise 定：{tk.get('last_error')}"
    if nh: skips.append(nh)
    if st in ("parked", "norepro") and tk.get("next_retry_at") and (ts(tk["next_retry_at"]) or now) > now:
        skips.append(f"暂停中（{tk.get('park_reason') or st}），{cst(tk['next_retry_at'])} 北京后自动再试：{(tk.get('last_error') or '')[:80]}")
    if st == "failed" and tk.get("next_retry_at") and (ts(tk["next_retry_at"]) or now) > now:
        skips.append(f"退避中，{cst(tk['next_retry_at'])} 北京后重试（已失败 {tk.get('transient_failures', 0)} 次：{(tk.get('last_error') or '')[:80]}）")
    if st == "running" and pid_alive(tk.get("pid")): skips.append(f"运行中（pid {tk.get('pid')}，{tk.get('started_at')} 开始）")
    fs, dl, _ = times(c, ctx["reports"])
    if manual and c["source"] != "history" and not gate: skips.insert(0, manual)
    else: manual = None
    state = "claimed" if claims else ("manual" if manual else ("skip" if skips else "todo"))
    return {"key": f"{n}:{fp}", "c": c, "state": state, "reason": (claims or skips or [None])[0], "manual": manual if not claims else None, "claims": claims, "skips": skips,
            "rounds": rounds, "round": rounds + 1, "needs_human": nh if not claims else None, "hard_stop": limit if not claims else None,
            "first_seen": fs, "deadline": dl,
            "gated": bool(gate)}

def prio(v):
    far = dt.datetime.max.replace(tzinfo=dt.timezone.utc)
    return (SEV_RANK.get(v["c"]["sev"], 9), v["deadline"] or far, v["c"]["qa"], v["c"]["fp"])

def evaluate_all(ctx, now=None):
    cands = collect(ctx["reports"], ctx["ledger"], ctx["fix2"], ctx.get("nofix", frozenset()))
    for c in cands.values(): c["first_seen"] = times(c, ctx["reports"])[0]
    return sorted((evaluate(c, ctx, now) for c in cands.values()), key=prio)

def next_ticket(vs): return next((v for v in vs if v["state"] == "todo"), None)

def pending(vs):
    """--pending-json rows: every candidate nobody has claimed; reason_if_skipped null = there is work for --next."""
    return [{"qa": v["c"]["qa"], "fp": v["c"]["fp"], "sev": v["c"]["sev"], "first_seen": iso(v["first_seen"]), "deadline": iso(v["deadline"]),
             "reason_if_skipped": None if v["state"] == "todo" else v["reason"], "title": v["c"]["title"][:80], "source": v["c"]["source"],
             **({"manual_order": "待派单"} if v["state"] == "manual" else {})}
            for v in vs if v["state"] != "claimed"]

def manual_orders_path(): return f"{OUT}/manual-orders.json"
def manual_order_rows(vs, ctx):
    rows = []
    for v in vs:
        if v["state"] != "manual": continue
        c = v["c"]; r = ctx["reports"].get(c["qa"]) or {}
        ver = r.get("cur") or (r.get("hist") or [None])[0]
        f = c.get("finding") or {}
        rows.append({"qa": c["qa"], "fp": c["fp"], "sev": c["sev"], "title": (c.get("title") or "")[:120],
                     "evidence": ver[2] if ver else None, "file": f.get("file"), "line": f.get("line"),
                     "why_not_auto": v["manual"], "assignee": "praise-pr-bot", "state": "待派单"})
    return rows

def write_manual_orders(vs, ctx, now=None):
    """Queue for the Praise PR bot (no message sent): out/praise-pr/manual-orders.json, keyed qa:fp, flock + atomic write.
    Existing entries keep their state / created_at (the PR bot moves them on); new ones start 「待派单」."""
    now = (now or now_cst()).isoformat(timespec="seconds"); rows = manual_order_rows(vs, ctx); MANUAL_ORDERS = manual_orders_path()
    os.makedirs(OUT, exist_ok=True)
    with open(f"{MANUAL_ORDERS}.lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try: doc = json.loads(_read(MANUAL_ORDERS)) if os.path.exists(MANUAL_ORDERS) else {}
        except Exception: doc = {}
        orders = doc.get("orders") or {}; changed = False
        for x in rows:
            k = f"{x['qa']}:{x['fp']}"; old = orders.get(k)
            if old:
                upd = {**old, **{a: b for a, b in x.items() if a != "state"}}
                if upd != old: orders[k] = {**upd, "updated_at": now}; changed = True
            else: orders[k] = {**x, "created_at": now, "updated_at": now}; changed = True
        if changed or not os.path.exists(MANUAL_ORDERS):
            write_atomic(MANUAL_ORDERS, json.dumps({"orders": orders, "updated_at": now}, ensure_ascii=False, indent=1))
    return orders

# ---------------------------------------------------------------- tickets (pure state machine)
def new_ticket(c):
    return {"qa": c["qa"], "fp": c["fp"], "sev": c["sev"], "title": (c.get("title") or "")[:80], "status": "queued", "attempts": 0,
            "transient_failures": 0, "next_retry_at": None, "last_error": None, "action_key": None, "history": []}

PARK_WAIT_H = (6, 24, 72)   # a parked ticket is tried again by itself after this long (1st / 2nd / 3rd+ park): new main, new session
def park(tk, reason, err, now):
    """kind 4 (the bot cannot do it yet): no DM -- it waits and tries again by itself; the daily digest lists it."""
    pc = int(tk.get("park_count") or 0) + 1
    tk.update(status="parked", park_reason=reason, last_error=err, park_count=pc, parked_at=now.isoformat(timespec="seconds"),
              next_retry_at=(now + dt.timedelta(hours=PARK_WAIT_H[min(pc, len(PARK_WAIT_H)) - 1])).isoformat(timespec="seconds"),
              fresh_retries=0, transient_failures=0)
def apply_outcome(tk, out, now):
    """-> (ticket, notify) ; notify = 'needs_praise' | None (the DM to write). Since 10-24 evening only kind 5 asks Praise."""
    tk = dict(tk); st, kind, err = out.get("status"), out.get("kind"), out.get("error")
    notify = None; stamp = now.isoformat(timespec="seconds")
    if st == "pr_action_written":
        tk.update(status=st, action_key=out.get("action_key"), last_error=None, next_retry_at=None, transient_failures=0, fresh_retries=0)
    elif st == "norepro":
        if out.get("confirmed"): tk.update(status="norepro", last_error=err, next_retry_at=None, confirmed=True)   # two models agree: closed
        else: park(tk, "复现存疑", err, now)   # the judge thinks it is real, the fixer still cannot show it: try again later
    elif kind == "deterministic" and int(tk.get("fresh_retries") or 0) < 1:
        # 10-24 (Praise: 能自己解决就别私信): before a human, the whole ticket once more from scratch -- new worktree, new session
        tk.update(status="failed", last_error=err, fresh_retries=int(tk.get("fresh_retries") or 0) + 1,
                  next_retry_at=(now + dt.timedelta(minutes=BACKOFF_MIN[0])).isoformat(timespec="seconds"))
    elif kind == "deterministic": park(tk, "修不动", err, now)
    elif kind == "praise":
        tk.update(status="needs_praise", last_error=err, next_retry_at=None); notify = "needs_praise"
    elif kind == "waiting":   # no memory for us: not the ticket's fault, so it does not use up a transient retry
        tk.update(status="failed", last_error=err, next_retry_at=(now + dt.timedelta(minutes=BACKOFF_MIN[0])).isoformat(timespec="seconds"))
    else:
        tf = int(tk.get("transient_failures") or 0) + 1; tk["transient_failures"] = tf
        if tf > len(BACKOFF_MIN):
            park(tk, "一直出故障", f"瞬时失败 {tf} 次仍未成功，最后一次：{err}", now)
        else:
            tk.update(status="failed", last_error=err, next_retry_at=(now + dt.timedelta(minutes=BACKOFF_MIN[tf - 1])).isoformat(timespec="seconds"))
    tk["pid"] = None; tk["updated_at"] = stamp
    tk["history"] = (list(tk.get("history") or []) + [{"at": stamp, "status": tk["status"], "error": (err or "")[:200]}])[-20:]
    return tk, notify

def dm_action(kind, c, text_detail):
    """kind 5 only (10-24 evening): the fix touches CI config / repo rules / secrets -- Praise decides."""
    n, fp, sev = c["qa"], c["fp"], c["sev"]; title = (c.get("title") or "")[:60]
    it = PN.item("action", f"你的 PR #{n} 上的一个修复要动敏感文件，等你点头",
                 f"#{n} 测出的 {sev} 问题「{title}」，修 PR bot 已经修好，但修复要改 CI 配置、仓库规则或密钥相关的文件（{text_detail[:200]}），按规则这类改动要你同意。",
                 "你不点头，这个修复就不开 PR。",
                 todo=[f"同意：跟会话说「跑 bin/run.sh prfix --qa {n} --fp {fp[:12]} --allow-sensitive」", "不同意：不用管，它不会再自动修这一条"],
                 link=f"https://github.com/{REPO}/pull/{n}")
    return {"key": f"needs-praise:{n}:{fp}", "type": "slack.dm", "to": "Praise", "pr": n, "fingerprint": fp, "text": PN.text(it)[:1500]}

# ---------------------------------------------------------------- worktree hygiene
def worktrees():
    _, o = git("worktree", "list", "--porcelain"); out = []
    for blk in o.strip().split("\n\n"):
        w = {}
        for l in blk.splitlines():
            k, _, v = l.partition(" "); w[k] = v
        if "worktree" in w:
            out.append({"path": os.path.abspath(w["worktree"]), "branch": w.get("branch", "").removeprefix("refs/heads/") or None,
                        "detached": "detached" in w})
    return out
def has_upstream(b): return git("config", "--get", f"branch.{b}.remote")[0] == 0
def branch_exists(b): return git("rev-parse", "--verify", "-q", f"refs/heads/{b}")[0] == 0
def remote_has(b):
    rc, o = git("ls-remote", "--heads", "origin", f"refs/heads/{b}", t=120)
    return None if rc else bool(o.strip())
def ours_dir(wd):
    wd = os.path.abspath(wd); par = os.path.dirname(wd)
    return (par == os.path.abspath(_WT_ROOT()) and os.path.basename(wd).startswith("pi-wt-")) or par == os.path.abspath(_TMPROOT())

def drop_worktree(wd, branch=None):
    """Remove a worktree and its local branch that this script created. Refuses directories outside WT_ROOT/pi-wt-* and
    TMPROOT/*, branches not shaped fix/qa-<N>-<fp8>, and branches with an upstream (= pushed). Returns notes."""
    wd = os.path.abspath(wd); msg = []
    if not ours_dir(wd): raise ValueError(f"refuse to remove {wd}: not a prfix worktree directory")
    reg = {w["path"] for w in worktrees()}
    if wd in reg:
        if os.path.isdir(wd):   # 10-24: rename into the trash (instant), rm -rf in the background, then drop the registration
            W.trash(wd); rc, o = git("worktree", "prune"); msg.append(f"worktree -> trash, prune rc={rc}{'' if rc == 0 else ' ' + tail(o, 2)}")
        else:
            git("worktree", "prune"); msg.append("worktree prune (目录已不在)")
    mine = os.path.dirname(wd) == os.path.abspath(_TMPROOT()) or re.fullmatch(r"pi-wt-\d+-[0-9a-f]{8}", os.path.basename(wd))
    if os.path.isdir(wd) and mine and wd not in {w["path"] for w in worktrees()}:
        shutil.rmtree(wd, ignore_errors=True); msg.append("rm -rf (未登记的残留目录)")
    if branch:
        if not BRANCH_RE.match(branch): raise ValueError(f"refuse to delete branch {branch}")
        if branch_exists(branch):
            if has_upstream(branch): msg.append(f"保留分支 {branch}：有 upstream（推送过）")
            else:
                rc, o = git("branch", "-D", branch); msg.append(f"branch -D {branch} rc={rc}{'' if rc == 0 else ' ' + tail(o, 2)}")
    return msg

def stale_scan(tickets=None):
    """prfix leftovers: pi-wt-* worktrees on unpushed fix/qa-<N>-<fp8> branches, /tmp/pi-prfix/*, orphan fix/qa-<N>-<fp8>."""
    tickets = tickets if tickets is not None else load_tickets().get("tickets", {})
    rows = []; wts = worktrees(); reg = {w["path"]: w for w in wts}
    running = {f"{t.get('qa')}-{fp8(t.get('fp'))}" for t in tickets.values() if t.get("status") == "running" and pid_alive(t.get("pid"))}
    wroot, troot = os.path.abspath(_WT_ROOT()), os.path.abspath(_TMPROOT())
    for w in wts:
        p, b = w["path"], w["branch"]
        if os.path.dirname(p) == wroot and re.fullmatch(r"pi-wt-rev-\d+", os.path.basename(p)) and w["detached"]:
            t_ = tickets.get(f"rev:{os.path.basename(p).rsplit('-', 1)[1]}") or {}
            busy = t_.get("status") == "running" and pid_alive(t_.get("pid"))
            rows.append({"path": p, "branch": None, "act": "keep" if busy else "delete", "why": "--revise 正在用" if busy else "--revise 残留 worktree"})
        elif os.path.dirname(p) == wroot and os.path.basename(p).startswith("pi-wt-"):
            m = BRANCH_RE.match(b or "")
            if not m: rows.append({"path": p, "branch": b, "act": "keep", "why": "分支不是 fix/qa-<N>-<fp8>"}); continue
            if f"{m.group(1)}-{m.group(2)}" in running: rows.append({"path": p, "branch": b, "act": "keep", "why": "这一单正在运行"}); continue
            if has_upstream(b): rows.append({"path": p, "branch": b, "act": "keep", "why": "分支有 upstream（推送过）"}); continue
            r = remote_has(b)
            if r is None: rows.append({"path": p, "branch": b, "act": "keep", "why": "远端没核实（ls-remote 失败）"}); continue
            if r: rows.append({"path": p, "branch": b, "act": "keep", "why": "远端有同名分支"}); continue
            tk = next((t for t in tickets.values() if t.get("qa") == int(m.group(1)) and fp8(t.get("fp")) == m.group(2)), {})
            rows.append({"path": p, "branch": b, "act": "delete", "why": f"prfix 残留（单子状态 {tk.get('status', '无记录')}）"})
        elif os.path.dirname(p) == troot:
            rows.append({"path": p, "branch": b, "act": "delete", "why": "dry-run 残留 worktree"})

    if os.path.isdir(troot):
        for name in sorted(os.listdir(troot)):
            p = os.path.join(troot, name)
            if p not in reg: rows.append({"path": p, "branch": None, "act": "delete", "why": "/tmp/pi-prfix 残留文件/目录"})
    if os.path.isdir(wroot):
        for name in sorted(os.listdir(wroot)):
            p = os.path.join(wroot, name)
            if name.startswith("pi-wt-") and os.path.isdir(p) and p not in reg:
                rows.append({"path": p, "branch": None, "act": "keep", "why": "未登记为 worktree，人工核查"})
    in_wt = {w["branch"] for w in wts if w["branch"]}
    _, o = git("for-each-ref", "--format=%(refname:short)", "refs/heads/fix/")
    for b in o.split():
        if BRANCH_RE.match(b) and b not in in_wt:
            if has_upstream(b): rows.append({"path": None, "branch": b, "act": "keep", "why": "孤立分支但有 upstream"}); continue
            r = remote_has(b)
            rows.append({"path": None, "branch": b, "act": "delete" if r is False else "keep",
                         "why": "孤立的 prfix 分支（无 worktree、未推送）" if r is False else "孤立分支，远端有同名或没核实"})
    return rows

def cleanup_stale(yes):
    rows = stale_scan()
    print(f"# prfix 残留 {now_cst():%Y-%m-%d %H:%M} 北京（{'删除' if yes else '只列出，加 --yes 才删'}）")
    for r in rows: print(f"- {'删' if r['act'] == 'delete' else '留'} {r['path'] or '-'} [{r['branch'] or '-'}]：{r['why']}")
    if not rows: print("- 无")
    if not yes:
        print("RESULT " + json.dumps({"status": "listed", "delete": sum(r["act"] == "delete" for r in rows), "keep": sum(r["act"] == "keep" for r in rows)}, ensure_ascii=False))
        return 0
    done = []
    with repo_lock():   # 10-24: no global run lock any more. Holding the clone lock, nobody adds/removes a worktree meanwhile;
        rows = stale_scan()   # re-scan under it: worktrees of tickets that are running now (live pid) are never listed
        for r in rows:
            if r["act"] != "delete": continue
            # a ticket that is being worked on right now (a dry-run never marks itself running) holds its per-ticket lock
            m = (re.search(r"pi-wt-(\d+)-([0-9a-f]{8})$|QA(\d+)-mq1-([0-9a-f]{8})", os.path.basename(r["path"] or ""))
                 or BRANCH_RE.match(r["branch"] or ""))
            g = [x for x in (m.groups() if m else ()) if x]
            rv = re.fullmatch(r"pi-wt-rev-(\d+)", os.path.basename(r["path"] or ""))
            tl = try_lock(f"{OUT}/locks/rev-{rv.group(1)}.lock") if rv else try_lock(f"{OUT}/locks/{g[0]}-{g[1]}.lock") if len(g) == 2 else True
            if not tl: done.append((r, ["跳过：这一单正在运行（锁被占）"])); continue
            try:
                if r["path"] and os.path.dirname(r["path"]) == os.path.abspath(_TMPROOT()) and r["path"] not in {w["path"] for w in worktrees()}:
                    (shutil.rmtree if os.path.isdir(r["path"]) else os.remove)(r["path"]); done.append((r, ["rm"]))
                elif r["path"]: done.append((r, drop_worktree(r["path"], r["branch"] if BRANCH_RE.match(r["branch"] or "") else None)))
                elif r["branch"]:
                    rc, o = git("branch", "-D", r["branch"]); done.append((r, [f"branch -D rc={rc}"]))
            except Exception as e: done.append((r, [f"失败：{e}"]))
            finally:
                if tl is not True: unlock(tl)
    for r, m in done: print(f"  {r['path'] or r['branch']}: {'；'.join(m)}")
    print("RESULT " + json.dumps({"status": "cleaned", "deleted": sum(not m[0].startswith("跳过") for _, m in done), "skipped_running": sum(m[0].startswith("跳过") for _, m in done)}, ensure_ascii=False))
    return 0

# ---------------------------------------------------------------- working-tree helpers
def untracked(wd):
    _, o = run(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=wd)
    return [f for f in o.split("\0") if f]
SCRATCH_RE = re.compile(r"(?:^|/)(?:tmp|temp|scratch|debug|out|output)(?:[-_.]?\d+)?\.(?:[cm]?[jt]s|txt|log|json|sh|out)$|\.(?:log|orig|rej|bak)$", re.I)
def scratch(f):
    """a new untracked file that is not part of a fix: scratch / debug / log names, or a new non-test file at the repo root."""
    return bool(SCRATCH_RE.search(f)) or ("/" not in f and not RUNNABLE_TEST_RE.search(f))
def left_out(wd, skip=()):
    """new untracked files changed() leaves out as scratch (shown to the model, so a real file with such a name gets renamed)."""
    return [f for f in untracked(wd) if f not in skip and scratch(f)]
def changed(wd, base=None, skip=()):
    """Files that differ (working tree vs `base`, default the index), new files included (intent-to-add) except scratch files,
    package-lock.json and `skip` (pre-push build output) excluded. With base, a local commit does not hide the fix from later checks."""
    new = [f for f in untracked(wd) if f not in skip and not scratch(f)]
    if new: run(["git", "add", "-N", "--", *new], cwd=wd)
    # 10-24 review: no rename detection -- a move (the model may `git mv`) lists both paths, so the sensitive-file check and the
    # stray-file check see where a file came from (a .github/ file moved away showed only its new name)
    _, o = run(["git", "diff", "--no-renames", "--name-only", "-z", *([base] if base else [])], cwd=wd)
    return [f for f in o.split("\0") if f and f != "package-lock.json" and f not in skip]
def is_test(f): return bool(RUNNABLE_TEST_RE.search(f) or TEST_SUPPORT_RE.search(f))
def runnable(fs): return [f for f in fs if RUNNABLE_TEST_RE.search(f)]
def snapshot(wd, files):
    return {f: (_read(os.path.join(wd, f), "rb") if os.path.isfile(os.path.join(wd, f)) else None) for f in files}
def blob(wd, rev, f):
    """bytes of f at rev, or None when f does not exist there (binary-safe, no output cleaning)."""
    r = subprocess.run(["git", "show", f"{rev}:{f}"], cwd=wd, capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
    return r.stdout if r.returncode == 0 else None
def to_base(wd, base, files):
    """Put the WORKING-TREE copy of files back to `base` (absent there -> removed). The index is not touched (10-24 review: `git
    checkout <base> -- f` also staged the base copy, so a later commit of only the model's files shipped a revert of the fix)."""
    for f in files:
        p = os.path.join(wd, f); b = blob(wd, base, f)
        if b is None:
            if os.path.isfile(p) or os.path.islink(p): os.remove(p)
        else:
            os.makedirs(os.path.dirname(p) or wd, exist_ok=True); _write(p, b, "wb")
def put_back(wd, snap):
    for f, b in snap.items():
        p = os.path.join(wd, f)
        if b is None:
            if os.path.isfile(p): os.remove(p)
        else:
            os.makedirs(os.path.dirname(p), exist_ok=True); _write(p, b, "wb")

def is_norepro(text): return bool(NOREPRO_RE.search(text or ""))
def root_cause(text):
    last = None
    for m in re.finditer(r"ROOT CAUSE\**[:：]?\**\s*(.+)", text or ""): last = m
    return last.group(1).strip().strip("*`").strip()[:300] if last else None
def explanation(text):
    t = text or ""; i = t.rfind("ROOT CAUSE")
    seg = t[i:].split("\n", 1)[1] if i >= 0 and "\n" in t[i:] else ""
    seg = re.sub(r"(?m)^#+\s*", "", seg).strip().replace("<!--", "&lt;!--")
    return seg[:900] + ("…" if len(seg) > 900 else "")

# ---------------------------------------------------------------- the model's working rules and the harness's checks (10-24)
RULES = ("Rules: no git commit/push/stash/reset/checkout, no network, edit only inside cwd, never kill processes, delete scratch / debug "
         "files you create (new files must be part of the fix). You may run targeted "
         "checks: `npx vitest run <files>`, `npx vitest related --run <changed files>`, `npx tsc --noEmit -p <tsconfig>`, `npx eslint <files>`; "
         "no full test suite, no watch mode. Code may have moved since the report: locate by symbol. "
         "Your cwd is the bot's own throw-away worktree under /workspace/pi-work (not a person's checkout); the rest of the machine is read-only, /tmp is private to this call, and the bot removes the worktree itself when the ticket ends -- no cleanup needed. Never touch other worktrees or the bot's own code (pi/ in panoptes).")
# 10-25 (Praise): fixes were sent back again and again for simple CI rules of the target repo (Windows-only reds, lint,
# verify:i18n, the PR format gate). Every model step now opens with that repo's short CI rules brief, so the model
# knows the rules before it starts instead of learning them from a red check. A missing brief is logged, not fatal.
_BRIEFS = {}
def ci_brief(repo=None):
    """conf/ci-rules/<repo name>.md for REPO (or the given owner/name), "" when there is none (logged once)."""
    name = (repo or REPO).rsplit("/", 1)[-1]
    if name not in _BRIEFS:
        p = os.path.join(CI_RULES, f"{name}.md")
        try: _BRIEFS[name] = re.sub(r"(?s)<!--.*?-->\n?", "", _read(p)).strip()
        except OSError as e: _BRIEFS[name] = ""; log(f"ci rules brief missing for {name}: {p} ({e.__class__.__name__}); prompts go without it")
    return _BRIEFS[name]
def step_prompt(k, prompt, repo=None):
    """What one model step is sent: the step tag, the repo's CI rules brief, then the step's own prompt."""
    b = ci_brief(repo)
    return f"[step {k}]\n" + (f"{b}\n\n---\n\n" if b else "") + prompt
FIX_FOLLOW = ("The regression test cases you wrote in TASK 1 are locked (restored before every check), so do not edit them; other cases in the "
              "same file count as existing tests. Existing tests: if one asserts the OLD "
              "buggy behaviour that this fix intentionally changes, update that expectation -- never skip/only/todo, never delete cases or "
              "assertions -- and list it under a line `TEST CHANGES:` with one reason per file. Otherwise fix the source, minimal diff.")
WEAKEN_RE = re.compile(r"\b(?:it|test|describe|suite|bench)\s*\.\s*(?:skip|only|todo|skipIf|runIf|fails)\b|\b(?:xit|xtest|xdescribe|fit|fdescribe)\s*\(")
CASE_RE = re.compile(r"(?<![\w.$])(?:it|test)(?:\s*\.\s*(?:each|for|concurrent|sequential))?\s*[(`]")
EXPECT_RE = re.compile(r"(?<![\w$])(?:expect|assert)\s*[.(]")
WEAK_MATCHER_RE = re.compile(r"\.\s*(?:toBeDefined|toBeTruthy|toBeFalsy)\s*\(|expect\s*\.\s*any(?:thing)?\s*\(")
# a platform check that switches a case off (`if (process.platform === 'win32') return` / wraps an it()); one that builds the expected
# value (path separators) is fine -- Windows-only CI reds are the main thing --revise fixes
PLATFORM_SKIP_RE = re.compile(r"if\s*\([^)]*(?:process\s*\.\s*platform|os\s*\.\s*platform\s*\(\)|\bisWindows\b)[^)]*\)\s*(?:\{\s*)?(?:return\b|(?:it|test|describe)\s*[.(])")
TEST_CONFIG_RE = re.compile(r"(?:^|/)(?:vitest|jest)\.(?:config|workspace|setup)[^/]*$|(?:^|/)(?:test-setup|setup-?tests?|vitest\.setup)\.[cm]?[jt]s$")
def strip_comments(src):
    """JS/TS source without // and /* */ comments (string contents may lose a '//' -- only used for counting)."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)(^|[^:\\'\"`])//.*$", r"\1", src)
def config_problems(wd, base, files):
    """edits that switch tests off from outside the test files: vitest / vite config, test setup, package.json scripts."""
    probs = []
    for f in files:
        if TEST_CONFIG_RE.search(f): probs.append(f"{f}：改了测试配置 / setup")
        elif os.path.basename(f) == "package.json":
            try: old = json.loads(blob(wd, base, f) or b"{}").get("scripts"); new = json.loads(_read(os.path.join(wd, f))).get("scripts")
            except Exception: continue
            if old != new: probs.append(f"{f}：改了 scripts")
    return probs
def weakened(f, old, new):
    """ways `new` weakens test file f compared with `old` (both texts; comments stripped before counting)."""
    old, new = strip_comments(old), strip_comments(new); probs = []
    if len(WEAKEN_RE.findall(new)) > len(WEAKEN_RE.findall(old)): probs.append(f"{f}：加了 skip / only / todo 之类的开关")
    if len(WEAK_MATCHER_RE.findall(new)) > len(WEAK_MATCHER_RE.findall(old)): probs.append(f"{f}：把断言换成了更宽松的写法（toBeDefined / toBeTruthy / expect.anything …）")
    if len(PLATFORM_SKIP_RE.findall(new)) > len(PLATFORM_SKIP_RE.findall(old)): probs.append(f"{f}：加了按平台跳过用例的判断")
    a, b = len(CASE_RE.findall(old)), len(CASE_RE.findall(new))
    if b < a: probs.append(f"{f}：测试用例从 {a} 个变成 {b} 个")
    a, b = len(EXPECT_RE.findall(old)), len(EXPECT_RE.findall(new))
    if b < a: probs.append(f"{f}：断言从 {a} 处变成 {b} 处")
    return probs
def added_lines(wd, base, f):
    """non-blank lines the working copy of f adds over base (what the repro step wrote into an existing test file)."""
    _, d = run(["git", "diff", "--no-color", "-U0", base, "--", f], cwd=wd)
    return [l[1:].strip() for l in d.splitlines() if l.startswith("+") and not l.startswith("+++") and l[1:].strip()]
def regression_guard(wd, snap, added):
    """The repro step's test lives in a file that existed before (10-24 QA1188 rerun: it added its case to tick.test.mjs, and
    locking the WHOLE file also reverted the old case's expectation the fix has to change). Only the lines the repro step added are
    locked: they must all still be there, and the file may not get weaker than the repro version. -> problems (file reverted)."""
    probs = []
    for f, lines in added.items():
        p = os.path.join(wd, f); cur = _read(p, "rb").decode("utf-8", "replace") if os.path.isfile(p) else ""
        have = {}
        for l in (x.strip() for x in cur.splitlines()): have[l] = have.get(l, 0) + 1
        need = {}
        for l in lines: need[l] = need.get(l, 0) + 1
        gone = [l for l, k in need.items() if have.get(l, 0) < k]
        pr = ([f"{f}：改动了复现阶段写的回归用例（{gone[0][:60]} …）"] if gone else []) + weakened(f, snap[f].decode("utf-8", "replace"), cur)
        if pr: put_back(wd, {f: snap[f]}); probs += pr
    return probs
def test_edit_problems(wd, base, f):
    """Ways an edit to an EXISTING test file weakens it (Praise 10-24: the model may update an old test's expectation when the fix
    intentionally changes that behaviour, but never switch it off). [] for a new file or a fair edit."""
    if git("cat-file", "-e", f"{base}:{f}", cwd=wd)[0] != 0: return []
    p = os.path.join(wd, f)
    if not os.path.isfile(p): return [f"{f}：删掉了已有测试文件"]
    rc, old = git("show", f"{base}:{f}", cwd=wd)
    if rc or not RUNNABLE_TEST_RE.search(f): return []          # fixtures / snapshots: the revert check covers them
    return weakened(f, old, _read(p, "rb").decode("utf-8", "replace"))

def check_fix(wd, base, runs, new_tests, step, chg, guard_base=None, skip=(), repro_snap=None, test_fix_ok=False, revert_check=True):
    """Everything the harness checks after a fix round. -> (ok, feedback for the model, info). Raises Deterministic only for results
    a further round cannot change (an unstable reproduction). guard_base: what test / config edits are compared with (--revise:
    the PR head, so the PR's own changes are not taken for the model's)."""
    gb = guard_base or base
    files = chg()
    src = [f for f in files if not is_test(f)]
    old_tests = [f for f in files if is_test(f) and f not in new_tests]
    probs = [p for f in old_tests for p in test_edit_problems(wd, gb, f)] + config_problems(wd, gb, src)
    if probs:
        bad = sorted({p.split("：")[0] for p in probs}); to_base(wd, gb, bad)
        step("改已有测试：被判为削弱，已撤回", False, "\n".join(probs))
        return False, ("Your edits to existing tests were reverted because they weaken them:\n- " + "\n- ".join(probs)
                       + "\nOnly update expectations that encode the old buggy behaviour."), {"why": "改已有测试时削弱了测试：" + "；".join(probs)[:150]}
    lo = left_out(wd, skip)
    note = (f"\n(Not part of the commit -- they look like scratch files: {lo}. Delete them, or rename them if they belong to the fix.)" if lo else "")
    rc, o = vitest(wd, runs); v = vitest_verdict(rc, o)
    step("fix：回归测试通过", v == "pass", f"rc={rc}\n{o}")
    if v != "pass": return False, f"The regression test(s) {runs} still fail:\n{failing_blocks(o)}{note}", {"why": "回归测试仍不过"}
    # test_fix_ok (the finding itself is in test code, 10-24 QA1198: a test helper left spies behind): the fix lives in test files --
    # the repro files changed after TASK 1, and existing test files. "Reverting the fix" then means: those back to the repro version.
    rs = repro_snap or {}
    tfix = ([f for f in rs if snapshot(wd, [f])[f] != rs[f]] + old_tests) if test_fix_ok else []
    if not revert_check: rc2 = None
    elif not src and not tfix: raise Deterministic("修复没改任何源码文件，回归测试却通过了（复现不稳定？）")
    if revert_check:
        snap = snapshot(wd, src + old_tests + [f for f in tfix if f in rs])
        to_base(wd, base, src + [f for f in tfix if f not in rs]); put_back(wd, {f: rs[f] for f in tfix if f in rs})
        rc2, o2 = vitest(wd, runs); v2 = vitest_verdict(rc2, o2); step("撤掉修复：测试失败", v2 == "fail", f"rc={rc2}\n{o2}")
        put_back(wd, snap); rc3, o3 = vitest(wd, runs); v3 = vitest_verdict(rc3, o3); step("恢复修复：测试通过", v3 == "pass", f"rc={rc3}\n{o3}")
    else: v2, v3 = "fail", "pass"   # --revise of a PR whose fix is test code: the first run proved it; the regression run above still must pass
    if v2 != "fail":
        sup = [f for f in old_tests if not RUNNABLE_TEST_RE.search(f)]
        return False, (f"With your source change reverted, the regression test(s) {runs} still PASS, so the green result does not come from a "
                       f"source fix{' (you changed test support files: ' + ', '.join(sup) + ')' if sup else ''}. Fix the bug in the source."), \
            {"why": "撤掉源码修复后回归测试仍通过（测试没测到这次修复）"}
    if v3 != "pass": raise Deterministic("恢复修复后测试不过（结果不稳定）")
    rc4, o4, ran = related_run(wd, src, [f for f in old_tests if RUNNABLE_TEST_RE.search(f)])
    info = {"src": src, "old_tests": old_tests, "rc_revert": rc2, "preexisting": [], "test_fix": tfix}
    if lo: step("这些新文件看着像临时文件，不进提交", True, "\n".join(lo))
    if rc4 == 0:
        info["related"] = "通过" + (f"（{len(ran)} 个同目录测试文件）" if ran else "（vitest related）")
        step("相关的已有测试通过", True, tail(o4, 30)); return True, None, info
    if rc4 == 124: raise Transient("相关的已有测试超时")
    fails = [f for f in failed_files(o4) if f not in runs]
    pre = set()
    if fails:   # failing on base too (main already red there) is not this fix's business
        snap = snapshot(wd, src + old_tests); to_base(wd, base, src + old_tests)
        try: rcb, ob = vitest_full(wd, ["run", *fails])
        finally: put_back(wd, snap)
        if rcb == 124: raise Transient("相关的已有测试在 base 上复核超时")
        pre = set(failed_files(ob)) if rcb else set()
    new = [f for f in fails if f not in pre] if fails else ["（没认出失败文件）"]
    step("相关的已有测试", not new, f"失败 {fails or '?'}；base 上本来就失败 {sorted(pre)}\n{tail(o4, 60)}")
    if not new:
        info.update(related=f"通过（{len(pre)} 个文件在 main 上本来就失败：{'、'.join(sorted(pre))}）", preexisting=sorted(pre)); return True, None, info
    return False, (f"These existing tests passed before your change and fail now: {new}\n{failing_blocks(o4)}\n"
                   "Either the fix breaks them (fix the source), or they assert the old buggy behaviour that this fix intentionally changes "
                   f"(update the expectation and list it under `TEST CHANGES:`).{note}"), {"why": f"改完后已有测试 {'、'.join(new[:3])} 挂了"}

def test_changes_note(texts):
    """the model's last `TEST CHANGES:` section (why it touched existing tests)."""
    for t in reversed(texts or []):
        m = re.search(r"TEST CHANGES\**:?\**\s*(.+?)(?=\n\s*\n|\n\s*(?:#+\s*)?ROOT CAUSE|\Z)", t or "", re.S)
        if m and m.group(1).strip().lower() not in ("none", "无", "-", "n/a"): return m.group(1).strip()[:600]
    return None

def orig_pr_diff(n, file=None, cap=12000):
    """diff of the merged PR QA tested (the model only got the report's evidence before 10-24); the finding's file first."""
    rc, o = run(["gh", "pr", "diff", str(n), "-R", REPO], t=120)
    if rc or not o.strip(): return ""
    blocks = [b for b in re.split(r"(?m)^(?=diff --git )", o) if b.strip()]
    if file: blocks.sort(key=lambda b: 0 if f" b/{file}" in b.split("\n", 1)[0] else 1)
    out, left = "", len(blocks)
    for b in blocks:
        if out and len(out) + len(b) > cap: break
        out += b[:cap]; left -= 1
    return out + (f"\n…（还有 {left} 个文件的 diff 没贴，需要的话自己看代码）" if left else "")

def rename_docs(wd, old, new):
    """docs/pr/<old> -> docs/pr/<new> in the worktree (front matter pr: and the '# PR #' heading), like outbound does after the PR exists."""
    src, dst = f"docs/pr/{old}", f"docs/pr/{new}"
    if not os.path.isdir(os.path.join(wd, src)): return
    if os.path.exists(os.path.join(wd, dst)): raise Deterministic(f"{dst} 已存在，不能把交接目录改名过去")
    rc, o = git("mv", src, dst, cwd=wd)
    if rc: raise Deterministic(f"交接目录改名失败：{tail(o, 2)}")
    for kind in ("review.md", "qa.md"):
        p = os.path.join(wd, dst, kind)
        if not os.path.isfile(p): continue
        t = _read(p)
        t2 = re.sub(rf"(?m)^(pr:\s*[\"']?){old}([\"']?\s*)$", rf"\g<1>{new}\g<2>", t, count=1)
        t2 = re.sub(rf"(?m)^(#\s*PR\s*#){old}(?!\d)", rf"\g<1>{new}", t2)
        if t2 != t: _write(p, t2)

def predict_pr(key=None, dry=False):
    """latest issue/PR number + 1, past the numbers other live prfix runs reserved and have not used yet (10-24: #1257 / #1258 were
    predicted as 1256 / 1257 while the previous ticket's PR was still on its way -> an extra docs/pr rename push each, which
    re-triggers the whole-PR gates). A reservation is a ticket's pred_pr (written here, under the tickets lock) while it is running
    or its PR action waits for outbound; once that PR exists the number is <= latest and stops counting."""
    try: latest = int(gh(["api", f"repos/{REPO}/issues?state=all&per_page=1&sort=created&direction=desc", "-q", ".[0].number"], js=False).strip())
    except Exception as e: raise Transient(f"预测 PR 号失败：{str(e)[:200]}")
    if dry or not key: return latest + 1
    with tickets_locked() as T:
        tks = T["tickets"]
        gone = {k for k, v in outbound_ledger().items() if v == "gave_up"}
        held = [int(t.get("pred_pr") or 0) for k, t in tks.items() if k != key and ((t.get("status") == "pr_action_written"
                and t.get("action_key") not in gone) or (t.get("status") == "running" and pid_alive(t.get("pid"))))]
        pred = max([latest] + held) + 1
        if key in tks: tks[key]["pred_pr"] = pred
    return pred

def handoff_check(wd, pred, base):
    if not os.path.exists(f"{wd}/scripts/pr-handoff/check.mjs"): return True, "（仓库没有 scripts/pr-handoff/check.mjs，跳过）"
    rc, out = run(["node", "scripts/pr-handoff/check.mjs", str(pred), "--type", "fix", "--base", base], cwd=wd, t=300)
    return rc == 0, tail(out, 40)

@contextlib.contextmanager
def prepush_slot():
    """One .githooks/pre-push at a time (10-24 QA1198: two parallel full test:unit runs pushed a 5 s vitest timeout over the edge
    in the other ticket's run). Waiting costs no memory; the model steps of other tickets still run in parallel."""
    os.makedirs(OUT, exist_ok=True)
    f = open(f"{OUT}/prepush.lock", "a"); t0 = time.time()
    while True:
        try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); break
        except BlockingIOError:
            if time.time() - t0 > 2 * 3600: f.close(); raise Waiting("等另一单的推送前检查超过 2 小时")
            time.sleep(20)
    if time.time() - t0 > 30: log(f"pre-push slot after {int(time.time() - t0)}s")
    try: yield
    finally: unlock(f)
def preflight(wd):
    """The repo's own .githooks/pre-push, run as is in the worktree (same 7 steps a real push runs); one at a time."""
    with prepush_slot(): return _preflight(wd)
def _preflight(wd):
    yield_to_panoptes()
    hook = f"{wd}/.githooks/pre-push"
    if not os.path.exists(hook): raise Deterministic("仓库没有 .githooks/pre-push，无法做推送前检查")
    src = _read(hook)
    names = [m.group(3).rstrip(".").strip() for m in re.finditer(r'echo "\[preflight\] (\d+)/(\d+) ([^"]+)"', src)]
    env = {k: v for k, v in os.environ.items() if k != "PREFLIGHT_SKIP" and not k.startswith("GIT_")}
    env.update(NOCOLOR)
    t0 = time.time(); rc, out = run([*NICE, "bash", hook], cwd=wd, t=3600, env=env); secs = round(time.time() - t0); out = clean(out)
    seen = re.findall(r"^\[preflight\] (\d+)/(\d+) ", out, re.M)
    failed = re.search(r"^\[preflight\] ❌ (.+?) 失败", out, re.M)
    ok = rc == 0 and "本地验证通过" in out
    total = len(names) or (int(seen[-1][1]) if seen else 0)
    return {"ok": ok, "rc": rc, "seconds": secs, "timeout": rc == 124, "steps": names, "total": total,
            "passed": total if ok else max(0, len(seen) - 1), "failed": failed.group(1) if failed else None}, tail(out, 80)

def scope_of(files):
    gen = {"src", "cindyplugin", "scripts", "loops", "server", "shared", "lib", "app", "packages", "docs"}
    for f in files:
        if f.startswith(".github/"): return "ci"
        for part in f.split("/")[:-1]:
            if part not in gen and re.fullmatch(r"[A-Za-z][\w-]{1,24}", part): return part
    return None

def platform_eval(wd, base, head, body, committed):
    """Ask the repo's own evaluator (scripts/ci/platform-risk.mjs, pure, no network) which platform risks the diff has."""
    script = f"{wd}/scripts/ci/platform-risk.mjs"
    if not os.path.exists(script): return None
    try:
        pr = _rjson(f"{wd}/agent-use/docs/pr-rules.json")
        rules = {"uiPaths": pr.get("uiPaths") or ["src/"], "uiExcludePaths": pr.get("uiExcludePaths") or []}
    except Exception: return None
    rng = [base, head] if committed else [base]
    _, ns = run(["git", "diff", "--name-status", "-z", "-M", *rng], cwd=wd)
    tok = [x for x in ns.split("\0") if x]; files = []; i = 0
    stmap = {"A": "added", "M": "modified", "D": "removed", "R": "renamed", "C": "copied", "T": "changed"}
    while i < len(tok):
        s = tok[i]; i += 1
        if s[:1] in ("R", "C"): old, new = tok[i], tok[i + 1]; i += 2
        else: old, new = None, tok[i]; i += 1
        _, patch = run(["git", "diff", *rng, "--", *( [old, new] if old else [new])], cwd=wd)
        e = {"filename": new, "status": stmap.get(s[:1], "modified"), "patch": patch[:60000]}
        if old: e["previous_filename"] = old
        files.append(e)
    inp = {"baseSha": base, "headSha": head if committed else base, "body": body, "files": files, "rules": rules}
    rc, o = run(["node", "scripts/ci/platform-risk.mjs"], cwd=wd, inp=json.dumps(inp), t=60)
    try: return json.loads(o.strip().splitlines()[-1])
    except Exception: return None

def platform_block(risks, tests):
    ev = {"schemaVersion": 1, "risks": risks, "windowsStatus": "tested",
          "environment": "GitHub Actions windows-latest unit shards + plugin smoke on current head",
          "evidence": runnable(tests) or ["docs/pr"], "fallback": "revert the PR",
          "reason": "minimal QA fix covered by the regression test above; Windows result comes from the trusted CI run on this head"}
    return ["### Windows 平台证据", "", "```mivo-platform-evidence", json.dumps(ev, ensure_ascii=False, indent=2), "```", ""]

def pr_body(c, res, tests, src, pf, docs_ok, issue_url, plat):
    n, sev, fp, x = c["qa"], c["sev"], c["fp"], c["finding"]
    src = src or res.get("test_fix_files") or []      # the finding was in test code: the fix is there
    loc = f"{x.get('file')}:{x.get('line')}" if x.get("file") else "（报告未给文件）"
    root = root_cause(res.get("root")) or root_cause(res.get("repro_text")) or f"{loc}（QA 报告定位）"
    expl = explanation(res.get("root"))
    fm = lambda fs: "、".join(f"`{f}`" for f in fs) or "无"
    cmd = "npx vitest run " + " ".join(runnable(tests))
    steps = " → ".join(pf.get("steps") or []) or ".githooks/pre-push"
    L = [CONTRIB, "", f"QA: #{n}", "", "## 这次改了什么", "", "### 摘要", "",
         f"QA 报告 PR-{n} {sev}（{fp}，`{loc}`{(' ' + x['symbol']) if x.get('symbol') else ''}）：{c.get('title')}", "",
         f"根因：{root}", ""] + ([expl, ""] if expl else []) + [
         f"修复：最小改动 {fm(src)}；回归测试 {fm(tests)}（修复前失败、修复后通过）。本 PR 由 Pi 线 prfix 自动生成：先开 draft；CI 红了 bot 会把失败日志交给模型再修（最多 {MAX_REVISIONS} 轮）；除 review-pr 外的 CI 全绿后由 bot 自动转 ready，之后由 Mac mini 盯梢跟进 review-pr 和审查意见。", "",
         "### 变更类型", "", "- [ ] 新模型或新能力", "- [ ] 功能改进", "- [x] 缺陷修复", "- [ ] 重构或性能优化", "- [ ] 文档、测试或工程维护", "- [ ] 其他：", "",
         "### 范围", "", f"- 关联 Issue / 需求：QA 报告 PR-{n} {sev}，指纹 {fp}" + (f"；交接 issue {issue_url}" if issue_url else ""),
         f"- 本 PR 包含：{fm(src)} 的最小修复；{fm(tests)} 回归测试；交接文件（docs/pr 下本 PR 号目录）",
         "- 明确不包含：重构与无关改动", "- 用户可见变化：QA 报告描述的错误行为不再出现（见摘要）",
         "- 是否影响已有调用：无（只修正报告指出的错误行为）", "", "### 效果展示", "", "不涉及", "",
         "## 怎么验证的", "", "### 自动验证", "", "```text",
         f"命令：{cmd}",
         f"结果：修复前失败（rc={res.get('rc_repro')}）；修复后通过（rc=0）；撤掉源码修复后失败（rc={res.get('rc_revert')}），恢复后通过",
         f"命令：npx vitest related --run <改动的源码>（及同目录已有测试）",
         f"结果：{res.get('related') or '未跑'}",
         f"命令：node scripts/pr-handoff/check.mjs {res.get('pred_pr')} --type fix（预测 PR 号；实际号不同时外发会改目录名）",
         f"结果：{'通过' if docs_ok else '未过'}",
         f"命令：.githooks/pre-push（{steps}）",
         f"结果：{pf.get('passed')}/{pf.get('total')} 通过，用时 {max(1, round((pf.get('seconds') or 0) / 60))} 分钟", "```", "",
         "### 实际任务验证", "", "不涉及（缺陷由回归单测复现并验证）", "",
         "### 未执行的验证", "", "- Cindy / Windows 实机手测（Windows 以本 PR 当前 HEAD 的 CI Windows unit 分片为准）", "",
         "## 风险", ""] + ([f"> ⚠️ 本 PR 改了 CI 配置 / 仓库规则 / 密钥相关的文件：{fm(res['sensitive'])}。修 PR bot 按规则直接放行，请审查重点看这几处。", ""]
                         if res.get("sensitive") else []) + (plat or []) + [
         "### 需要特别留意", "", "- [ ] 无已知风险", "- [ ] 生成结果或模型行为发生变化", "- [ ] 可能产生额外任务或费用",
         "- [ ] 影响已有参数、默认值或调用方", "- [ ] 影响任务提交、轮询或失败重试", "- [ ] 影响素材、产物、下载或数据留存",
         "- [ ] 涉及用户数据、凭证或第三方服务", "- [x] 其他：自动生成的最小修复，没有实机验证；请核对根因与改动范围"]
    if res.get("test_fix_files"):
        L += [f"- [x] 问题本身出在测试代码里，修复改的就是测试代码：{fm(res['test_fix_files'])}（回归用例在修复前失败、修复后通过）"]
    if res.get("test_changes"):
        L += [f"- [x] 改了已有测试的期望（这些用例原来断言的正是这次修掉的错误行为）：{fm(res['test_changes'])}"
              + (f"。模型给的理由：{res['test_changes_why']}" if res.get("test_changes_why") else "")]
    if res.get("preexisting"):
        L += [f"- [x] 这些已有测试在 main 上本来就失败，与本 PR 无关：{fm(res['preexisting'])}"]
    if res.get("ruled_R3"):
        L += [f"- [x] 改已有测试的期望经裁决（{JUDGE_TIER}）认定正当：{re.sub(r'\s+', ' ', res['ruled_R3'])[:400]}"]
    if res.get("prepush_unrelated"):
        st_, jt_ = res["prepush_unrelated"]
        L += [f"- [x] 本地推送前检查 `{st_}` 没过（原样重跑也没过），裁决（{JUDGE_TIER}）认定与本改动无关，以 GitHub CI 为准：{re.sub(r'\s+', ' ', jt_)[:400]}"]
    L += ["",
         "### 影响与回滚", "", f"- 可能影响：{fm(src)}", "- 回滚 / 降级方式：回退本 PR", "",
         "### 提交前检查", "", "- [x] 已完整 review 本次改动", "- [x] 已说明用户能感知到的变化", "- [x] 已记录实际执行的验证",
         "- [x] 已在 `docs/pr/<PR号>/` 附 review.md 与 qa.md，并与当前代码一致", "- [x] 未提交密钥、凭证、用户素材或敏感日志",
         "- [x] 涉及模型或调用协议时，已确认已有调用是否兼容", "- [x] 涉及付费任务时，已检查重复提交和重试行为", ""]
    return "\n".join(L)

# ---------------------------------------------------------------- one ticket
def fix_one(c, A, d):
    """-> (outcome, res). outcome: status pr_action_written | dry_ok | norepro | failed (kind transient|deterministic)."""
    n, sev, fp = c["qa"], c["sev"], c["fp"]
    tag = f"QA{n}-{fp[:12]}"; c["tag"] = tag; _TAG["t"] = tag
    branch = f"fix/qa-{n}-{fp8(fp)}"
    wd = f"{_TMPROOT()}/{tag}" if A.dry_run else f"{_WT_ROOT()}/pi-wt-{n}-{fp8(fp)}"
    res = {"qa": n, "sev": sev, "fp": fp, "steps": [], "model": [], "base": None, "pred_pr": None, "wd": wd,
           "branch": None if A.dry_run else branch, "round": c.get("round", 1)}
    out = {"status": "failed", "kind": "transient", "error": None, "action_key": None}
    created = False; ev = f"{OUT}/{d}-{tag}{'-dry' if A.dry_run else ''}"
    def step(k, ok, text=""): res["steps"].append((k, ok, text)); log(k, "OK" if ok else "FAIL")
    log("pick", tag, sev, c.get("title"), f"round {res['round']}", "dry-run" if A.dry_run else "real")
    try:
        os.makedirs(_TMPROOT(), exist_ok=True)
        with repo_lock():   # 10-24: parallel prfix processes share one clone; its refs / worktree list change one at a time
            if not A.dry_run:   # a legacy prfix worktree (pi-wt-<N>) may still hold this branch
                for w in worktrees():
                    if w["branch"] == branch and w["path"] != os.path.abspath(wd) and ours_dir(w["path"]):
                        log("legacy worktree of this ticket ->", drop_worktree(w["path"]))
            if os.path.exists(wd) or (not A.dry_run and branch_exists(branch)):
                log("leftover of this ticket ->", drop_worktree(wd, None if A.dry_run else branch))
            rc, o = git("fetch", "-q", "origin", "main")
            if rc: raise Transient(f"git fetch 失败 rc={rc}：{tail(o, 2)}")
            base = A.base or git("rev-parse", "origin/main")[1].strip()
            if not re.fullmatch(r"[0-9a-f]{40}", base):
                rc, o = git("rev-parse", base); base = o.strip()
                if rc or not re.fullmatch(r"[0-9a-f]{40}", base): raise Transient(f"base 解析失败：{tail(o, 2)}")
            res["base"] = base
            rc, o = git("worktree", "add", "-q", *(["--detach", wd, base] if A.dry_run else ["-b", branch, wd, base]))
            if rc: raise Transient(f"worktree add 失败：{tail(o, 3)}")
            created = True
            W.mark(wd, "dry" if A.dry_run else "prfix", repo="mivo", clone=os.path.abspath(_GIT()), ticket=tag, branch=res["branch"], base_sha=base,
                   lock=f"{OUT}/locks/{n}-{fp8(fp)}.lock", tickets=f"{OUT}/tickets.json", ticket_key=f"{n}:{fp}")
        junk = set()   # untracked files the pre-push run left (build output): never part of the fix
        chg = lambda: changed(wd, base, junk)
        rc, o = npm_ci(wd); step("npm ci", rc == 0, f"rc={rc}\n{o}")
        if rc: raise Transient(f"npm ci rc={rc}")
        x = c["finding"]
        odiff = orig_pr_diff(n, x.get("file"))
        ctx = (f"QA report PR-{n}.json, {sev}, fingerprint {fp}, title: {c.get('title')}\nfile: {x.get('file')}:{x.get('line')} symbol {x.get('symbol')}\n"
               f"evidence: {json.dumps(x.get('evidence'), ensure_ascii=False)[:2500]}"
               + (f"\n\nDiff of the merged PR #{n} that QA tested (the bug is in it or exposed by it):\n{odiff}" if odiff else ""))
        rules = RULES
        # one pi session per run (10-24): repro -> fix rounds -> pre-push rounds -> docs continue the same conversation, so what the
        # model found in one step is there in the next; the stamp keeps a retry from resuming an older run's conversation
        session = f"pi-line-prfix-{tag}-{now_cst():%m%d%H%M%S}"
        texts = []
        def ask(k, prompt):
            yield_to_panoptes()
            try: text, task = pi(TIER, wd, step_prompt(k, prompt), session, timeout=2400, write=True, skills=False, nice=True)
            except Exception as e: raise Transient(f"模型 {k} 调用失败：{str(e)[:300]}")
            res["model"].append({"step": k, "task": task, "text": text}); texts.append(text); return text
        # 1 reproduce (the model is told once when it wrote no test / its test passes on base)
        t1 = ask("repro", f"{ctx}\n\nTASK 1 (reproduce only): add a regression test next to the existing tests of that module that FAILS on current code "
                 f"because of this bug. Do NOT change non-test files. If the bug cannot be reproduced, change nothing and start your reply with a line "
                 f"`NOREPRO`, followed by the evidence (file:line). {rules}\nEnd with: ROOT CAUSE file:line, TEST FILE path, the test names.")
        judged = {}; nr = {"n": 0}
        def norepro(why, text):
            """a NOREPRO goes to a second model first: it agrees -> closed with a note, no DM; it disagrees -> one more repro round
            with its reasoning; contested again (or no answer) -> Praise."""
            if judged: raise NoRepro(why, text, judge=judged.get("text", ""))
            nr["n"] += 1
            if nr["n"] == 1:   # G1: one more try by the fixing model itself before anybody is asked to rule
                step("复现不了：先让修复模型自己再试一次（还不问裁决）", False, why)
                return ("Before 'cannot reproduce' is accepted, try once more from a different angle: read the reported file:line on the "
                        "current code, follow the exact inputs / ordering / timing the report describes, and write a regression test for that "
                        "path (still no non-test changes). Only if you can show with file:line that the code path is gone or guarded, start "
                        "your reply with `NOREPRO` again, with that evidence.")
            agree, jt, asked = judge_norepro(c, wd, text, tag, base, step); judged["text"] = jt
            if asked: step(f"不可复现交给第二个模型复核（{JUDGE_TIER}）：{'同意不是问题' if agree else '认为问题成立' if agree is False else '没给出结论'}", agree is not None, jt)
            if agree: raise NoRepro(why, text, confirmed=True, judge=jt)
            if agree is None: raise Transient(f"裁决模型没给出结论（{(jt or '')[:120]}）")
            return (f"A second reviewer DISAGREES -- it thinks the finding is real:\n{jt[-3000:]}\n\nWrite the failing regression test now (still no "
                    "non-test changes). Only if you can show it wrong with file:line evidence, start your reply with `NOREPRO` again.")
        rr = 0
        while True:
            rr += 1; last_round = rr >= REPRO_ROUNDS + (1 if judged else 0)
            ch = chg(); tests = [f for f in ch if is_test(f)]; src = [f for f in ch if not is_test(f)]
            if src: to_base(wd, base, src)
            runs = runnable(tests); again = None
            if is_norepro(t1):
                if tests: to_base(wd, base, tests)
                step("repro", False, "模型回 NOREPRO"); again = norepro("模型判不可复现（NOREPRO）", t1); runs = ["-"]
            elif not runs:
                step("repro", False, "模型没写测试，也没回 NOREPRO")
                if last_round: raise Transient("模型没写回归测试，也没回 NOREPRO")
                again = ("You added no runnable test file (*.test.* / *.spec.*) and did not reply NOREPRO. Add the failing regression test now, "
                         "or start your reply with a line `NOREPRO` and the evidence (file:line).")
            if again is None and runs:
                rc, o = vitest(wd, runs)
                try: v = vitest_verdict(rc, o)
                except Transient as e:
                    if "没找到测试文件" not in str(e) or last_round: raise
                    step("repro：vitest 找不到测试文件", False, f"rc={rc}\n{o}")
                    again = (f"`npx vitest run {' '.join(runs)}` found no test files: that path is outside the vitest include patterns. "
                             "Put the test next to the module's existing tests (same naming) so vitest picks it up.")
                else:
                    step("repro：测试在 base 上失败", v == "fail", f"rc={rc}\n{o}"); res["rc_repro"] = rc
                    if v == "fail": break
                    if last_round: again = norepro("模型写的回归测试在当前代码上就通过（没复现）", t1 + f"\n\n--- vitest rc={rc} ---\n{o}")
                    else: again = (f"Your test(s) {runs} PASS on the current code, so they do not reproduce the bug:\n{failing_blocks(o, 2000)}\n"
                             "Make the test exercise the reported behaviour so it fails because of the bug (still no non-test changes). If you are now "
                             "sure the code is correct, delete your test and start your reply with `NOREPRO` plus the evidence.")
            t1 = ask(f"repro{rr + 1}", f"{again}\n{rules}\nEnd with: ROOT CAUSE file:line, TEST FILE path, the test names.")
        res["repro_text"] = t1
        test_snap = snapshot(wd, tests); new_tests = set(tests)
        in_tests = is_test(x.get("file") or "")   # the finding itself is in test code: the fix edits test files
        lock = {f: b for f, b in test_snap.items() if blob(wd, base, f) is None and not (in_tests and f == x.get("file"))}   # repro files: locked whole
        added = {f: added_lines(wd, base, f) for f in tests if f not in lock}             # cases it added to an existing file
        # 2 fix rounds: every check the harness runs (regression test, test-edit guard, revert check, the module's other tests)
        # answers with its output in the same conversation; the new regression test is restored before every check
        def fix_loop(keyp, first, rounds):
            fb = None; info = {}
            for i in range(1, rounds + 1):
                text = ask(f"{keyp}{i}", first if i == 1 else f"{fb}\n\n{FIX_FOLLOW}\n{rules}\nEnd with ROOT CAUSE file:line and a one-paragraph explanation.")
                put_back(wd, lock)
                gp = regression_guard(wd, test_snap, added)
                if gp:
                    fb = ("Your edits to the regression test lines were reverted:\n- " + "\n- ".join(gp) + "\nThe cases you wrote in TASK 1 are locked; "
                          "in the same file you may only update OLD cases that assert the old buggy behaviour."); info = {"why": "改动了复现用例"}
                    step(f"{keyp} 第 {i} 轮没过：改动了复现用例", False, "\n".join(gp)); continue
                ok, fb, info = check_fix(wd, base, runs, new_tests, step, chg, skip=junk, repro_snap=test_snap, test_fix_ok=in_tests)
                if ok: return text, info
                step(f"{keyp} 第 {i} 轮没过：{info.get('why')}", False, fb)
            raise Deterministic(f"修了 {rounds} 轮仍没过：{info.get('why')}")
        where = (f"The bug is in test code ({x.get('file')}): fix that code -- outside the cases you wrote in TASK 1 -- with the smallest diff. " if in_tests else "")
        t2, info = fix_loop("fix", f"TASK 2: the failing test(s) {runs} reproduce the bug. {where}Make the MINIMAL source fix (smallest diff, follow existing style, "
                            f"no refactor) so they pass without breaking the module's other tests. {FIX_FOLLOW}\nBefore you finish, run the regression "
                            f"test and `npx vitest related --run <the files you changed>`. {rules}\nEnd with ROOT CAUSE file:line and a one-paragraph explanation.",
                            FIX_ROUNDS)
        res["root"] = t2; res.update(rc_revert=info.get("rc_revert"), related=info.get("related"), preexisting=info.get("preexisting") or [])
        # R3: the fix changed expectations of existing test cases -> the judge rules whether the old expectation was the bug
        def old_case_edits():
            return [f for f in chg() if is_test(f) and f not in new_tests and RUNNABLE_TEST_RE.search(f)] + \
                   [f for f in added if snapshot(wd, [f])[f] != test_snap[f]]
        oce = [] if in_tests else old_case_edits()
        if oce:
            _, tdiff = git("diff", base, "--", *oce, cwd=wd)
            _, sdiff = git("diff", base, "--", *[f for f in chg() if not is_test(f) and not f.startswith("docs/")], cwd=wd)
            v, jt, _ = gated_ruling("R3", c, wd, base, f"The fix changes these existing test cases:\n{tdiff[:8000]}\n\nThe source fix:\n{sdiff[:6000]}\n\nThe fixing model's "
                           f"reason:\n{(test_changes_note(texts) or '-')[:1500]}\n\nDid each changed OLD expectation encode exactly the buggy behaviour "
                           "this fix removes? LEGIT = yes for all; REJECT = at least one correct test was bent to fit the fix (say which).", tag, step,
                                   changed_files=chg(), old_tests=oce)
            step(f"裁决 R3（改已有测试的期望是否正当）：{v or '没给出结论'}", v is not None, jt)
            if v == "LEGIT": res["ruled_R3"] = jt
            else:
                for f in oce:
                    if f in added: put_back(wd, {f: test_snap[f]})
                    else: to_base(wd, base, [f])
                t2, info = fix_loop("fix-ruled-", (f"A judge ruled that your edits to existing tests are NOT legitimate:\n{jt[-3000:]}\n\n" if v else
                                   "Your edits to existing tests could not be confirmed and were reverted.\n\n")
                                   + f"They have been reverted. Fix the source so the existing tests pass unchanged. {FIX_FOLLOW}\n{rules}\n"
                                   "End with ROOT CAUSE file:line and a one-paragraph explanation.", 2)
                if old_case_edits(): raise Deterministic("裁决不认可改已有测试，模型给不出不改老测试的修复")
                res["root"] = t2; res.update(rc_revert=info.get("rc_revert"), related=info.get("related"), preexisting=info.get("preexisting") or [])
        # 3 handoff docs (same commit), checked with the repo's own pr-handoff check. The PR number is predicted only now (10-24:
        # predicted at the start, 3 of 6 were wrong by the time the PR existed -> rename push -> whole-PR gates again)
        pred = predict_pr(f"{n}:{fp}", A.dry_run); res["pred_pr"] = pred
        ddir = f"docs/pr/{pred}/"; pre = snapshot(wd, chg())
        def keep_docs_only():
            for f in chg():
                if f.startswith(ddir): continue
                if f in pre: put_back(wd, {f: pre[f]})
                else: to_base(wd, base, [f])
        docs = [f"{ddir}review.md", f"{ddir}qa.md"]
        def docs_check():
            have = all(os.path.exists(f"{wd}/{f}") and re.search(rf"(?m)^pr:\s*{pred}\s*$", _read(f"{wd}/{f}")) for f in docs)
            return handoff_check(wd, pred, base) if have else (False, "review.md / qa.md 缺失或 pr: 不对")
        for dr in range(1, DOCS_ROUNDS + 1):
            ask("docs" if dr == 1 else f"docs{dr}",
                (f"Write docs/pr/{pred}/review.md and docs/pr/{pred}/qa.md by filling docs/pr/_template/review.md and qa.md: keep the front matter "
                 f"(schema/kind unchanged, pr: {pred}); replace EVERY <…> placeholder; keep every heading and every row of the security table (answer 是/否); "
                 f"Chinese, concise; at least '### R1' with all five fields and '### Q1' with 层次/期望/失败判据/反证/自动化 filled; every R has a Q; "
                 f"Q1 反证 = '撤掉改动后用例失败；作者已实测：是'. Context: QA #{n} {sev} {c.get('title')}. Root cause: {root_cause(t2) or '-'}. "
                 f"Changed files: {sorted(pre)}. Test command: npx vitest run {' '.join(runs)} (修复前失败、修复后通过). "
                 f"Self-check with `node scripts/pr-handoff/check.mjs {pred} --type fix --base {base}` and fix every error it reports. "
                 f"Do not touch any other file. {rules} For this step you may also run `node scripts/pr-handoff/check.mjs`.") if dr == 1 else
                (f"`node scripts/pr-handoff/check.mjs {pred} --type fix --base {base}` still reports:\n{dout[-2500:]}\n"
                 f"Fix docs/pr/{pred}/review.md and docs/pr/{pred}/qa.md only (front matter pr: {pred}). {rules} "
                 "For this step you may also run `node scripts/pr-handoff/check.mjs`."))
            keep_docs_only(); dok, dout = docs_check()
            if dok: break
        step(f"docs/pr/{pred} 交接文件自查（pr-handoff check --type fix）", dok, dout)
        if not dok: raise Deterministic(f"交接文件自查不过（pr-handoff check，{DOCS_ROUNDS} 轮）")
        # 4 commit (real) + the repo's pre-push checks; a red step goes back to the model (PREPUSH_FIX_ROUNDS), a formatter's rewrite is taken
        if git("diff", "--quiet", "--", "package-lock.json", cwd=wd)[0]: git("checkout", "--", "package-lock.json", cwd=wd)
        def split_files():
            files = chg()
            return files, [f for f in files if is_test(f)], [f for f in files if not is_test(f) and not f.startswith(ddir)]
        files, tests, src = split_files()
        res["sensitive"] = sensitive_files(files)
        if res["sensitive"]: step(f"修复改了 CI 配置 / 仓库规则 / 密钥相关的文件（放行，PR 里标出交审查机把关）：{', '.join(res['sensitive'][:4])}", True)
        ptype = "docs" if src and all(f.endswith(".md") or f.startswith("docs/") for f in src) else "fix"
        sc = scope_of(src or tests); title = f"{ptype}{'(' + sc + ')' if sc else ''}: {(c.get('title') or '')[:60]} (QA #{n})"
        res["title"] = title
        def commit(amend=False):
            if git("diff", "--quiet", "--", "package-lock.json", cwd=wd)[0]: git("checkout", "--", "package-lock.json", cwd=wd)
            fs = chg()
            if fs: git("add", "-A", "--", *fs, cwd=wd)
            rc, o = git(*GIT_ID, "commit", "-q", *(["--amend", "--no-edit"] if amend else ["-m", title, "-m", f"QA: #{n}\nFingerprint: {fp}"]), cwd=wd)
            step("本地提交（作者 Praise）" + ("，并入修改" if amend else ""), rc == 0, o)
            if rc: raise Deterministic(f"本地提交失败：{tail(o, 3)}")
            return git("rev-parse", "HEAD", cwd=wd)[1].strip()
        head = base
        if not A.dry_run: head = commit()
        steps_txt = "?"
        for pr_round in range(PREPUSH_FIX_ROUNDS + 1):
            pf, pfo = preflight(wd); res["preflight"] = pf; junk.update(untracked(wd)); steps_txt = " → ".join(pf.get("steps") or []) or ".githooks/pre-push"
            step(f"推送前检查 .githooks/pre-push（{pf['passed']}/{pf['total']}，{pf['seconds']}s）", pf["ok"], pfo)
            if pf["timeout"]: raise Transient("推送前检查超时（1 小时）")
            dirty = "" if A.dry_run else git("status", "--porcelain", "--untracked-files=no", cwd=wd)[1].strip()
            if pf["ok"] and not dirty: break
            if pr_round == PREPUSH_FIX_ROUNDS:
                if not pf["ok"]: raise Deterministic(f"推送前检查没过（又修了 {PREPUSH_FIX_ROUNDS} 轮）：{pf['failed'] or 'rc=' + str(pf['rc'])}")
                raise Deterministic(f"推送前检查一直在改已跟踪文件，提交与检查不一致：{tail(dirty, 5)}")
            if pf["ok"]:   # a formatter / generator step rewrote tracked files: take its result into the commit and check again
                head = commit(amend=True); continue
            before = snapshot(wd, chg()); before_head = head
            reply, info2 = fix_loop(f"prepush{pr_round + 1}-",
                f"The repo's pre-push checks (.githooks/pre-push: {steps_txt}) FAILED at step `{pf['failed'] or '?'}` with your change. Output:\n"
                f"{failing_blocks(pfo, 6000)}\n\nFind the cause and fix it in this worktree (you may re-run that step's own command). {FIX_FOLLOW} "
                f"Do not touch docs/pr/. If this failure has nothing to do with the change (it fails the same way on main), change nothing and say so.\n"
                f"{rules}\nEnd with ROOT CAUSE file:line and a one-paragraph explanation.", 2)
            if (A.dry_run and snapshot(wd, chg()) == before) or (not A.dry_run and not git("status", "--porcelain", "--untracked-files=no", cwd=wd)[1].strip()):
                # the model looked and changed nothing (10-24 QA1198: a 5 s timeout under load, "flaky, unrelated"): run pre-push
                # again as is, once; red again with nothing to change -> a human
                if res.get("prepush_rerun"):
                    failed_txt = pf['failed'] or 'rc=' + str(pf['rc'])
                    if res.get("ruled_R2"):
                        raise Deterministic(f"推送前检查没过（{failed_txt}），裁决后又修了一轮还是没过：{reply.strip()[:200]}")
                    _, cdiff = git("diff", base, "--", ".", ":(exclude)package-lock.json", ":(exclude)docs/pr", cwd=wd)
                    v, jt, _ = gated_ruling("R2", c, wd, base, f"The repo's pre-push step `{failed_txt}` fails with this change, and failed again when re-run as is:\n"
                                   f"{failing_blocks(pfo, 5000)}\n\nThe fixing model looked at it, changed nothing, and says it is not caused by the change:\n"
                                   f"{reply.strip()[-2500:]}\n\nThe change (diff vs main):\n{cdiff[:8000]}\n\nIs the failure caused by this PR's change? "
                                   "UNRELATED = no (flaky, already broken on main, environment); RELATED = yes.", tag, step,
                                   failure_text=pfo, changed_files=chg())
                    res["ruled_R2"] = (v, jt); step(f"裁决 R2（推送前检查失败与本次改动有没有关系）：{v or '没给出结论'}", v is not None, jt)
                    if v == "UNRELATED":   # binding: open the draft anyway, GitHub CI decides (its red goes to --revise)
                        res["prepush_unrelated"] = (failed_txt, jt); break
                    if v is None: raise Transient(f"推送前检查没过（{failed_txt}），裁决模型没给出结论，稍后重试")
                    fix_loop(f"prepush-ruled-", f"A judge ruled that this pre-push failure IS caused by your change:\n{jt[-3000:]}\n\nFix it. {FIX_FOLLOW} "
                             f"Do not touch docs/pr/.\n{rules}\nEnd with ROOT CAUSE file:line and a one-paragraph explanation.", 2)
                    if not A.dry_run: head = commit(amend=True)
                    continue
                res["prepush_rerun"] = True; step("模型判断与本次改动无关、没改代码：原样重跑推送前检查", True, reply.strip()[:800]); continue
            res.update(related=info2.get("related") or res.get("related"), preexisting=sorted(set(res.get("preexisting") or []) | set(info2.get("preexisting") or [])))
            dok, dout = docs_check()
            if not dok:
                ask(f"docs-after-prepush{pr_round + 1}", f"After that change `node scripts/pr-handoff/check.mjs {pred} --type fix --base {base}` reports:\n"
                    f"{dout[-2500:]}\nFix docs/pr/{pred}/review.md and qa.md only. {rules}")
                dok, dout = docs_check()
                step(f"改完后交接文件自查", dok, dout)
                if not dok: raise Deterministic("推送前检查修复后，交接文件自查不过")
            if not A.dry_run: head = commit(amend=True)
            log(f"pre-push round {pr_round + 1}: model changed the fix ({before_head[:9]} -> {head[:9]}), checking again")
        files, tests, src = split_files()
        res["test_changes"] = [f for f in tests if f not in new_tests] + [f for f in added if snapshot(wd, [f])[f] != test_snap[f]]
        if in_tests: res["test_fix_files"], res["test_changes"] = res["test_changes"] or [x.get("file")], []
        res["test_changes_why"] = test_changes_note(texts)
        # the PR number may have moved while pre-push ran (~15 min): rename the handoff dir now, not after the PR exists
        if not A.dry_run:
            p2 = predict_pr(f"{n}:{fp}")
            if p2 != pred:
                rename_docs(wd, pred, p2); old = pred; pred = p2; ddir = f"docs/pr/{pred}/"; docs = [f"{ddir}review.md", f"{ddir}qa.md"]
                head = commit(amend=True); res["pred_pr"] = pred
                dok, dout = docs_check(); step(f"PR 号预测从 #{old} 变成 #{pred}，交接目录已改名，重新自查", dok, dout)
                if not dok: raise Deterministic("交接目录改名后自查不过")
                files, tests, src = split_files()
        issue = next((u for u, k in sorted(get_links().items()) if k == (n, fp) and u.startswith("https://")), None)
        body0 = pr_body(c, res, tests, src, pf, dok, issue, [])
        ev_ = platform_eval(wd, base, head, body0, not A.dry_run)
        warn = []
        if ev_ is None:
            need = any(re.match(r"^(?:src|shared|cindyplugin|server)/|^scripts/(?!ci/|ops/)", f) for f in src)
            plat = platform_block([], tests) if need else []; warn.append("platform-risk 评估器不可用，Windows 平台证据按保守规则生成")
        else:
            plat = platform_block(ev_.get("risks") or [], tests) if ev_.get("requiresWindowsEvidence") else []
        body = pr_body(c, res, tests, src, pf, dok, issue, plat)
        if ev_ is not None:
            chk = platform_eval(wd, base, head, body, not A.dry_run) or {}
            bad = [e for e in chk.get("errors") or [] if e != TRUSTED_PROOF_ERR]
            if bad: warn.append("平台证据自查：" + "；".join(bad))
        res["warnings"] = warn
        if A.dry_run:
            write_atomic(ev + ".pr.md", body)
            out = {"status": "dry_ok", "kind": None, "error": None, "action_key": None}
        else:
            _, pdiff = git("diff", "--binary", base, "HEAD", "--", ".", ":(exclude)package-lock.json", cwd=wd)
            pfile = f"{OUT}/{d}-{tag}.pr.patch"
            write_atomic(pfile, pdiff); write_atomic(pfile[:-6] + ".md", body)
            rnd = int(res.get("round") or 1)   # round 2+ needs its own key + dedupe string, or outbound sees round 1 as "done"
            att = int(c.get("attempt") or 1)   # 10-24: a later attempt after outbound gave up on this ticket's action needs a new key
            key = f"fixpr:{n}:{fp}" + (f":r{rnd}" if rnd > 1 else "") + (f":a{att}" if att > 1 else "")
            record_actions("praise-pr", d, [{"key": key, "type": "gh.pr.create(draft)", "patch": pfile, "title": title,
                "body_must_contain": fp + (f" r{rnd}" if rnd > 1 else ""), "qa": n, "fingerprint": fp, "sev": sev, "ticket": f"{n}:{fp}", "round": res["round"],
                "local_branch": branch, "base": base, "head": head, "pred_pr": pred, "docs_dir_predicted": f"docs/pr/{pred}",
                "preflight": f"{pf['passed']}/{pf['total']} ok in {pf['seconds']}s",
                "note": "outbound: after gh pr create, if the real number != pred_pr, git mv docs/pr/<pred> docs/pr/<real> (+ front matter pr:) "
                        "on the same branch, commit, plain push (no force)"}])
            out = {"status": "pr_action_written", "kind": None, "error": None, "action_key": key}
    except NoRepro as e:
        out = {"status": "norepro", "kind": "norepro", "error": str(e), "action_key": None, "confirmed": e.confirmed}
        res["norepro"] = (str(e), e.text); res["judge"] = e.judge; res["norepro_confirmed"] = e.confirmed
    except Transient as e:
        out = {"status": "failed", "kind": "waiting" if isinstance(e, Waiting) else "transient", "error": str(e), "action_key": None}
    except Deterministic as e:
        out = {"status": "failed", "kind": "deterministic", "error": str(e), "action_key": None}
    except PraiseNeeded as e:
        out = {"status": "failed", "kind": "praise", "error": str(e), "action_key": None}
    except Exception as e:
        out = {"status": "failed", "kind": "transient", "error": f"内部错误：{e!r}"[:300], "action_key": None}
    finally:
        try:
            if created and os.path.isdir(wd):
                rc, diff = run(["git", "add", "-N", "--", "."], cwd=wd)
                _, diff = git("diff", "--binary", res["base"] or "HEAD", "--", ".", ":(exclude)package-lock.json", cwd=wd)
                write_atomic(ev + ".patch", diff)
            write_atomic(ev + ".md", evidence_md(c, res, out, wd))
        except Exception as e: log("evidence write failed", e)
        # success too once janitor enforces (10-24 plan S1): outbound works from its own clone + the saved patch, not from here
        if created and (A.dry_run or out["status"] != "pr_action_written" or W.enforce()):
            try:
                with repo_lock(): log("cleanup", drop_worktree(wd, None if A.dry_run else branch))
            except Exception as e: log("cleanup failed", e)
    res["evidence"] = ev + ".md"
    return out, res

_LINKS = {}
def get_links(): return _LINKS

def evidence_md(c, res, out, wd):
    n, sev, fp = c["qa"], c["sev"], c["fp"]
    md = [f"# prfix {c.get('tag')}（{sev}，QA #{n}，第 {res.get('round', 1)} 轮）{' dry-run' if res['branch'] is None else ''}",
          f"- 结果：**{out['status']}**{('（' + str(out.get('kind')) + '）') if out.get('kind') else ''}{('：' + out['error']) if out.get('error') else ''}",
          f"- base {str(res.get('base') or '?')[:9]}，预测 PR #{res.get('pred_pr')}，worktree {wd}{'，分支 ' + res['branch'] if res.get('branch') else ''}",
          f"- 问题：{c.get('title')}（{(c.get('finding') or {}).get('file')}:{(c.get('finding') or {}).get('line')}，来源 {c.get('source')}）"]
    if res.get("warnings"): md.append("- 提醒：" + "；".join(res["warnings"]))
    md += ["", "| 步骤 | 结果 |", "|---|---|"] + [f"| {k} | {'✅' if ok else '❌'} |" for k, ok, _ in res["steps"]]
    for k, ok, o in res["steps"]: md += ["", f"### {k}", "```", (o or "").strip()[-6000:], "```"]
    if res.get("norepro"): md += ["", "## NOREPRO（不开 PR；note 待人工确认）", res["norepro"][0]]
    for m in res["model"]: md += ["", f"## 模型输出：{m['step']}（task {m['task']}）", "", m["text"]]
    return "\n".join(md) + "\n"

def write_norepro_note(c, res, why, ev):
    n, sev, fp = c["qa"], c["sev"], c["fp"]
    name = f"{n}-{(sev or 'p').lower()}-{fp8(fp)}-norepro.md"; x = c.get("finding") or {}
    text = (res.get("norepro") or ("", ""))[1] or (res["model"][0]["text"] if res.get("model") else "")
    ok2 = res.get("norepro_confirmed")
    md = [f"# #{n} {sev} 不可复现（{'两个模型复核一致：不是问题' if ok2 else '待人工确认'}）", "",
          ("> 修复模型（deepseek）复现不了，第二个模型（" + JUDGE_TIER + "）独立看代码后也认定不是问题，按结论收起，不再修。不认可就删掉本文件，"
           if ok2 else "> 待人工确认：Pi 线 prfix 自动判定，没有人工核实。认可就保留本文件当结论；不认可就删掉本文件，")
          + f"再用 `prfix.py --qa {n} --fp {fp}` 重跑。", "",
          f"- QA 报告：reports/merged/PR-{n}.json，{sev}，指纹 {fp}", f"- 问题：{c.get('title')}",
          f"- 位置：{x.get('file')}:{x.get('line')} {x.get('symbol') or ''}", f"- 判定：{why}",
          f"- 时间：{now_cst():%Y-%m-%d %H:%M} 北京；base {str(res.get('base') or '?')[:9]}", f"- 证据全文：{ev}", "",
          "## 模型完整输出", "", text, ""] + (["## 第二个模型的复核", "", res["judge"], ""] if res.get("judge") else [])
    p = os.path.join(NOTES, name); write_atomic(p, "\n".join(md)); return p

# ---------------------------------------------------------------- reporting
def print_list(vs, ctx):
    todo = [v for v in vs if v["state"] == "todo"]; skip = [v for v in vs if v["state"] in ("skip", "manual")]; cl = [v for v in vs if v["state"] == "claimed"]
    print(f"# prfix 候选 {now_cst():%Y-%m-%d %H:%M} 北京（只读）：{len(vs)} 条；可做 {len(todo)}，跳过 {len(skip)}，已认领 {len(cl)}")
    print(f"- 台账 {os.path.basename(ctx['ledger_file'] or '无')}；P2 判「修」{sorted(ctx['fix2'])}；闸 {ctx['gates']}{'；' + ctx['gate_warn'] if ctx['gate_warn'] else ''}"
          + (f"；交接仓没读到 {ctx['unread_repos']}" if ctx.get("unread_repos") else ""))
    for title, group in (("可做（--next 按此顺序一次一单）", todo), ("跳过", skip), ("已认领", cl)):
        print(f"\n## {title}（{len(group)}）")
        for v in group:
            c = v["c"]
            tail_ = f"截止 {cst(v['deadline'])}" + (f"，第 {v['round']} 轮" if v["round"] > 1 else "") if v["state"] == "todo" else v["reason"]
            more = f"（另 {len(v['claims']) + len(v['skips']) - 1} 条理由）" if len(v["claims"]) + len(v["skips"]) > 1 else ""
            print(f"#{c['qa']} {c['sev']} {c['fp']} [{c['source']}] {c['title'][:40]} -> {tail_}{more}")

def finish(r, code, A):
    r.update(exit=code, at=now_cst().isoformat(timespec="seconds"), dry_run=bool(getattr(A, "dry_run", False)))
    try: write_atomic(f"{OUT}/last-result{'.dry' if r['dry_run'] else ''}.json", json.dumps(r, ensure_ascii=False, indent=1))
    except Exception as e: log("last-result write failed", e)
    print("RESULT " + json.dumps(r, ensure_ascii=False), flush=True)
    return code

def bookkeeping(ctx, vs, now, d):
    """File-only upkeep before picking: dead 'running' -> transient failure; exhausted rounds -> needs_human + DM (once);
    the queue view (queued entries = todo now). Returns ctx with fresh tickets."""
    dms = []
    with tickets_locked() as T:
        tks = T["tickets"]
        for k, t in list(tks.items()):
            if t.get("status") == "running" and not pid_alive(t.get("pid")):
                tks[k] = apply_outcome(t, {"status": "failed", "kind": "transient", "error": "上次运行中断（进程已不在）"}, now)[0]
        for w in vs:
            k = w["key"]; t = tks.get(k)
            if w["hard_stop"] and w["c"]["source"] != "history" and not w["gated"] and (t or {}).get("status") != "parked":
                t = dict(t or new_ticket(w["c"])); park(t, "规则上不自动修", w["hard_stop"], now); t["updated_at"] = now.isoformat(timespec="seconds")
                tks[k] = t
            elif w["state"] == "todo" and k not in tks: tks[k] = new_ticket(w["c"])
            elif w["state"] != "todo" and (t or {}).get("status") == "queued": tks.pop(k, None)
        ctx["tickets"] = dict(tks)
    if dms: record_actions("praise-pr", d, dms)
    return ctx

def pick_qa(A, ctx, vs, now):
    """--qa N [--fp FP]: (verdict, error-result). Gates and the round limit hold; claims / ticket states are overridden."""
    if A.qa in ctx["gates"]: return None, {"status": "gated", "qa": A.qa, "fp": A.fp, "error": ctx["gates"][A.qa]}
    mine = [v for v in vs if v["c"]["qa"] == A.qa and (not A.fp or v["c"]["fp"].startswith(A.fp))]
    if not mine:   # the operator may also force a P2 that is not ruled 修: look it up in the current report
        rep = (ctx["reports"].get(A.qa) or {}).get("cur")
        x = next((x for x in (rep[1].get("findings") or [] if rep else []) if x.get("fingerprint") and x.get("severity") in ("P0", "P1", "P2")
                  and (not A.fp or x["fingerprint"].startswith(A.fp))), None)
        if x:
            c = {"qa": A.qa, "fp": x["fingerprint"], "sev": x["severity"], "source": "report", "author": author_of(rep[1]), "finding": x,
                 "title": x.get("title") or "", "row": ctx["ledger"].get((A.qa, x["fingerprint"]))}
            c["first_seen"] = times(c, ctx["reports"])[0]; mine = [evaluate(c, ctx, now)]
            log(f"warn: --qa {A.qa} {x['severity']} 不在自动范围（P2 未判「修」），按人工指定处理")
    if not mine: return None, {"status": "error", "qa": A.qa, "fp": A.fp, "error": "找不到这条 finding（P0–P2；--fp 按前缀匹配）"}
    if len(mine) > 1:
        todo = [v for v in mine if v["state"] == "todo"]
        if not todo: return None, {"status": "error", "qa": A.qa, "error": f"#{A.qa} 有 {len(mine)} 条 finding 且都已认领/跳过，请用 --fp 指定"}
        mine = todo
    v = mine[0]
    if v["hard_stop"]: return None, {"status": "stopped", "qa": A.qa, "fp": v["c"]["fp"], "error": v["hard_stop"]}
    if v["state"] != "todo": log(f"warn: --qa 人工指定，忽略：{v['reason']}")
    return v, None

PRWATCH_STATE = _E("PRFIX_PRWATCH_STATE") or f"{BASE}/out/prwatch/state.json"
def prwatch_dispatching():
    """True when this run did not come from prwatch (no PRFIX_DISPATCHED) while prwatch is alive (heartbeat < 10 min) and dispatching
    (takeover/prfix-event)."""
    if _E("PRFIX_DISPATCHED") == "1" or not takeover("prfix-event"): return False
    try: last = dt.datetime.strptime(_rjson(PRWATCH_STATE)["last_check"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST)
    except Exception: return False
    return now_cst() - last < dt.timedelta(minutes=10)

def run_ticket(A, ctx, vs):
    d = today(); now = now_cst()
    if A.qa:
        v, err = pick_qa(A, ctx, vs, now)
        if err: return finish(err, 1, A)
    else:
        if not A.dry_run and prwatch_dispatching():
            # 10-24: the hourly cron fallback (run.sh prfix @14) only checked MemAvailable >= 3G and bypassed prwatch's memory
            # admission; while prwatch is alive and dispatching, it decides when a fix starts
            return finish({"status": "idle", "reason": "prwatch 在派单（兜底 cron 不另起修复）", "pending_after": None}, 0, A)
        if not A.dry_run: ctx = bookkeeping(ctx, vs, now, d); vs = sorted((evaluate(w["c"], ctx, now) for w in vs), key=prio)
        v = next_ticket(vs)
        if not v: return finish({"status": "idle", "pending_after": 0}, 0, A)
    if mem_gb() < MIN_MEM_GB:
        return finish({"status": "busy", "reason": f"MemAvailable {mem_gb():.1f}G < {MIN_MEM_GB}G", "ticket": v["key"]}, BUSY, A)
    qa_lk = None; tk_lk = None; tk = {}
    try:
        gates, _ = load_gates()   # fresh read right before acting (defence in depth; --qa included)
        qa_lk = None if SHARE_WITH_PANOPTES else try_lock(_QA_LOCK())
        if not qa_lk and not SHARE_WITH_PANOPTES: return finish({"status": "busy", "reason": f"panoptes 正在跑（{_QA_LOCK()} 被占）", "ticket": v["key"]}, BUSY, A)
        _QA_HOLD["f"] = qa_lk
        # 10-24 parallel runs, no global run lock. The claim is atomic under tickets.json's flock: re-read every ticket (another
        # prfix may have taken or finished one since gather()), re-evaluate, take the first todo whose per-ticket lock is free
        # (--qa / --dry-run: the chosen ticket), check the fresh gates, mark it running, and only then release the tickets lock.
        stop = None
        with (contextlib.nullcontext({"tickets": dict(ctx["tickets"])}) if A.dry_run else tickets_locked()) as T:
            ctx["tickets"] = dict(T["tickets"])
            fixed_pick = bool(A.qa or A.dry_run)
            if not fixed_pick: vs = sorted((evaluate(w["c"], ctx, now) for w in vs), key=prio)
            cands = [v] if fixed_pick else [w for w in vs if w["state"] == "todo"]
            v = gated = None
            for w in cands:
                if w["c"]["qa"] in gates:   # gated since gather(): never fixed, and it does not hold up the rest of the queue
                    gated = gated or ({"status": "gated", "qa": w["c"]["qa"], "fp": w["c"]["fp"], "error": gates[w["c"]["qa"]]}, 1); continue
                cur = T["tickets"].get(w["key"]) or {}
                if cur.get("status") == "running" and pid_alive(cur.get("pid")): continue      # another process is on it
                lk = try_lock(f"{OUT}/locks/{w['c']['qa']}-{fp8(w['c']['fp'])}.lock")
                if lk: v, tk_lk = w, lk; break
            if not v:
                stop = gated or (({"status": "busy", "reason": "这一单正由另一个 prfix 在修", "ticket": cands[0]["key"]}, BUSY) if fixed_pick and cands
                                 else ({"status": "idle", "pending_after": 0}, 0))
            if v and not A.dry_run:
                t = dict(T["tickets"].get(v["key"]) or new_ticket(dict(v["c"], round=v["round"])))
                if t.get("status") == "pr_action_written" and (ctx.get("outbound") or {}).get(t.get("action_key")) == "gave_up":
                    t["gave_up_count"] = int(t.get("gave_up_count") or 0) + 1   # the next give-up stops for a human (evaluate)
                t.update(status="running", attempts=int(t.get("attempts") or 0) + 1, started_at=now.isoformat(timespec="seconds"),
                         pid=os.getpid(), updated_at=now.isoformat(timespec="seconds"), harness=PRBOT_HARNESS)
                T["tickets"][v["key"]] = t
        if stop: return finish(*stop, A)
        c = dict(v["c"], round=v["round"], attempt=int((T["tickets"].get(v["key"]) or {}).get("attempts") or 1) if not A.dry_run else 1)
        import signal
        old_term = signal.signal(signal.SIGTERM, _on_term)
        try: out, res = fix_one(c, A, d)
        finally: signal.signal(signal.SIGTERM, old_term)
        if not A.dry_run:
            with tickets_locked() as T:
                tk, notify = apply_outcome(T["tickets"].get(v["key"]) or new_ticket(c), out, now_cst()); T["tickets"][v["key"]] = tk
            acts = []
            if out["status"] == "norepro" and out.get("confirmed"): write_norepro_note(c, res, out["error"], res["evidence"])
            if notify == "needs_praise": acts.append(dm_action("needs_praise", c, f"{tk.get('last_error')}；证据 {res['evidence']}"))
            if acts: record_actions("praise-pr", d, acts)
    finally:
        _QA_HOLD.pop("f", None); unlock(qa_lk); unlock(tk_lk)
    left = [w for w in vs if w["state"] == "todo" and w["key"] != v["key"]]
    r = {"ticket": v["key"], "qa": c["qa"], "fp": c["fp"], "sev": c["sev"], "round": c["round"],
         "status": tk.get("status") or out["status"], "outcome": out["status"], "kind": out.get("kind"), "error": out.get("error"),
         "action_key": out.get("action_key"), "attempts": tk.get("attempts"), "next_retry_at": tk.get("next_retry_at"),
         "evidence": res.get("evidence"), "warnings": res.get("warnings") or [], "pending_after": len(left)}
    return finish(r, 0 if out["status"] in ("pr_action_written", "norepro", "dry_ok") else 1, A)

# ---------------------------------------------------------------- --revise: GitHub CI went red on our own draft fix PR (10-24)
# Before, prwatch only DM'd Praise "have a session fix it"; the model that wrote the fix never saw the failure. Now prwatch hands
# the red head to `prfix.py --revise N`: the failed-step logs go to the same model, the harness re-checks (regression test, revert
# check, related tests, pre-push), and the new commit goes out as a gh.pr.push-fix action (outbound: plain push onto the PR's
# qa-fix/* branch, only when the head is still the one that was fixed). MAX_REVISIONS per PR, then needs_human + DM.
# Switch: takeover/prfix-revise (delete it to go back to DM-only).
PILINE_MARK = re.compile(r"<!-- pi-line fixpr:(\d+):(mq1-[0-9a-f]{20})")
REVISE_FLAG = "prfix-revise"
BAD_CONCL = ("FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR")
PR_VIEW = "number,state,isDraft,author,headRefName,headRefOid,body,title,statusCheckRollup,mergeStateStatus"
MAX_MERGES = 3          # --revise conflict rounds per PR (main moves; every round is a fresh merge of the current main)

def red_checks(p):
    return [c for c in p.get("statusCheckRollup") or [] if "review-pr" not in (c.get("name") or c.get("context") or "")
            and (c.get("conclusion") or c.get("state") or "").upper() in BAD_CONCL]

def revise_target(p):
    """-> (qa, fp, why_not). Only our own open DRAFT fix PRs that the Pi line opened (pi-line marker, qa-fix/* branch)."""
    m = PILINE_MARK.search(p.get("body") or "")
    if p.get("state") != "OPEN": return None, None, "PR 不是 open"
    if not p.get("isDraft"): return None, None, "PR 已经转正式（之后由 Mac mini 盯梢跟进）"
    if author_of(p) != AUTHOR_SELF: return None, None, "不是我们的 PR"
    if not (p.get("headRefName") or "").startswith("qa-fix/") or not m: return None, None, "不是 Pi 线开的修复 PR"
    return int(m.group(1)), m.group(2), None

def failed_check_logs(checks, cap=12000):
    """the failed steps' logs of the red checks (gh run view --log-failed), cleaned; checks without an Actions run are only named."""
    runs, other = {}, []
    for c in checks:
        nm = c.get("name") or c.get("context")
        m = re.search(r"/actions/runs/(\d+)", c.get("detailsUrl") or c.get("targetUrl") or "")
        if m: runs.setdefault(m.group(1), []).append(nm)
        else: other.append(nm)
    out = []
    for rid, names in runs.items():
        rc, o = run(["gh", "run", "view", rid, "-R", REPO, "--log-failed"], t=180)
        lines = [re.sub(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s?", "", l.split("\t", 2)[-1]) for l in clean(o).splitlines()]
        out.append(f"--- {', '.join(names)} (run {rid}) ---\n" + (failing_blocks("\n".join(lines[-400:]), 5000) if rc == 0 else f"（日志取不到：{tail(o, 2)}）"))
    if other: out.append(f"--- 没有 Actions 日志的检查：{', '.join(other)} ---")
    return "\n\n".join(out)[:cap]

def finding_of(qa, fp):
    r = load_reports().get(qa) or {}
    for v in ([r["cur"]] if r.get("cur") else []) + r.get("hist", []):
        for x in (v[1].get("findings") or []) + (v[1].get("pending_verification") or []):
            if x.get("fingerprint") == fp: return x
    return {}

def revise_dm(n, qa, fp, head, why):
    """kind 5 only: the CI fix touches CI config / repo rules / secrets."""
    it = PN.item("action", f"修复 PR #{n} 的 CI 修正要动敏感文件，等你点头",
                 f"修 PR bot 为 #{qa} 开的修复 PR #{n}（草稿）CI 没过，bot 已经修好，但修正要改 CI 配置、仓库规则或密钥相关的文件（{why[:200]}）。",
                 f"你不点头，这个修复 PR 就一直红着。",
                 todo=[f"同意：跟会话说「跑 bin/run.sh prfix --revise {n} --allow-sensitive」", "不同意：不用管"],
                 link=f"https://github.com/{REPO}/pull/{n}")
    return {"key": f"revise-needs-praise:{n}:{head[:12]}", "type": "slack.dm", "to": "Praise", "pr": n, "qa": qa, "fingerprint": fp, "text": PN.text(it)[:1500]}

def revise(A):
    n = A.revise; d = today(); key = f"rev:{n}"; _TAG["t"] = f"REV{n}"
    if not A.dry_run and not takeover(REVISE_FLAG):
        return finish({"status": "off", "pr": n, "error": f"takeover/{REVISE_FLAG} 不存在：修草稿 CI 关着"}, 1, A)
    try: p = _gh_json(["pr", "view", str(n), "-R", REPO, "--json", PR_VIEW], 120)
    except Exception as e: return finish({"status": "error", "pr": n, "error": f"读 PR #{n} 失败：{str(e)[:250]}"}, 1, A)
    qa, fp, why = revise_target(p)
    if why: return finish({"status": "skipped", "pr": n, "reason": why}, 0, A)
    head, br, red = p["headRefOid"], p["headRefName"], red_checks(p)
    conflict = (p.get("mergeStateStatus") or "").upper() == "DIRTY"   # 10-24: a draft that conflicts with main gets main merged in
    cnt_key, cap = ("merges", MAX_MERGES) if conflict else ("revisions", MAX_REVISIONS)
    if not red and not conflict: return finish({"status": "skipped", "pr": n, "reason": "当前 head 没有红的检查，也没有冲突"}, 0, A)
    if not A.dry_run and mem_gb() < MIN_MEM_GB:
        return finish({"status": "busy", "pr": n, "reason": f"MemAvailable {mem_gb():.1f}G < {MIN_MEM_GB}G"}, BUSY, A)
    lk = try_lock(f"{OUT}/locks/rev-{n}.lock")
    if not lk: return finish({"status": "busy", "pr": n, "reason": "另一个 prfix 正在修这个 PR"}, BUSY, A)
    stop = None; now = now_cst()
    try:
        with (contextlib.nullcontext({"tickets": {}}) if A.dry_run else tickets_locked()) as T:
            t = dict(T["tickets"].get(key) or {"pr": n, "qa": qa, "fp": fp, "kind": "revise", "status": "queued", "revisions": 0, "attempts": 0,
                                                 "transient_failures": 0, "history": []})
            if t.get("status") == "running" and not pid_alive(t.get("pid")):   # the last run died (OOM / reboot): counts as a transient failure
                t, _ = apply_outcome(t, {"status": "failed", "kind": "transient", "error": "上次运行中断（进程已不在）"}, now); t["head"] = t.get("head") or head
                T["tickets"][key] = t
            paused = t.get("status") in ("parked", "failed") and (ts(t.get("next_retry_at")) or now) > now
            if t.get("status") == "needs_praise" and t.get("head") == head:
                stop = ({"status": "needs_praise", "pr": n, "error": t.get("last_error")}, 1)
            elif paused: stop = ({"status": "skipped", "pr": n, "reason": f"暂停中，{cst(t['next_retry_at'])} 北京后自动再试：{(t.get('last_error') or '')[:100]}"}, 0)
            elif t.get("status") == "pushed" and t.get("last_head") == head: stop = ({"status": "skipped", "pr": n, "reason": "这个 head 已经修过、等推送"}, 0)
            elif int(t.get(cnt_key) or 0) >= cap and t.get("status") != "parked":
                # kind 4, no DM: pause, then try again by itself with a fresh budget (10-24 evening)
                park(t, "修不动", (f"跟 main 的冲突已经合了 {t.get(cnt_key)} 轮，又冲突了" if conflict else
                     f"CI 已经修了 {t.get('revisions')} 轮还是红（{', '.join((c.get('name') or c.get('context') or '?') for c in red[:4])}）"), now)
                t.update(head=head, updated_at=now.isoformat(timespec="seconds")); T["tickets"][key] = t
                stop = ({"status": "parked", "pr": n, "error": t["last_error"]}, 0)
            elif not A.dry_run:
                if t.get("status") == "parked": t[cnt_key] = 0   # the pause is over: a fresh budget of rounds
                t.update(status="running", pid=os.getpid(), attempts=int(t.get("attempts") or 0) + 1, started_at=now.isoformat(timespec="seconds"),
                         updated_at=now.isoformat(timespec="seconds"), head=head, harness=PRBOT_HARNESS); T["tickets"][key] = t
        if stop: return finish(*stop, A)
        import signal
        old_term = signal.signal(signal.SIGTERM, _on_term)
        try: out, res = (revise_merge if conflict else revise_one)(A, d, n, qa, fp, p, red)
        finally: signal.signal(signal.SIGTERM, old_term)
        if not A.dry_run:
            with tickets_locked() as T:
                t = dict(T["tickets"].get(key) or {}); stamp = now_cst().isoformat(timespec="seconds")
                if out["status"] == "pr_action_written":
                    t.update(status="pushed", action_key=out["action_key"], last_head=head, rerun_only=bool(out.get("rerun_only")),
                             **{cnt_key: int(t.get(cnt_key) or 0) + 1},
                             last_error=None, next_retry_at=None, transient_failures=0, pid=None, updated_at=stamp)
                    t["history"] = (list(t.get("history") or []) + [{"at": stamp, "status": "pushed", "error": ""}])[-20:]
                else:
                    t, notify = apply_outcome(t, out, now_cst())
                    if notify == "needs_praise": record_actions("praise-pr", d, [revise_dm(n, qa, fp, head, t.get("last_error") or out.get("error") or "")])
                T["tickets"][key] = t
        return finish({"pr": n, "qa": qa, "fp": fp, "status": out["status"], "kind": out.get("kind"), "error": out.get("error"),
                       "action_key": out.get("action_key"), "evidence": res.get("evidence")}, 0 if out["status"] in ("pr_action_written", "dry_ok") else 1, A)
    finally: unlock(lk)

CONFLICT_MARK_RE = re.compile(r"(?m)^(?:<{7}|>{7})(?: |$)|^={7}$")
def revise_merge(A, d, n, qa, fp, p, red):
    """Our own draft fix PR conflicts with main (DIRTY: GitHub runs no checks, update-branch cannot merge). Merge the current main
    into the PR head here, let the model resolve the conflicts (keep main's changes AND the fix), re-run the PR's regression tests,
    the related tests and pre-push, then hand outbound a bundle with the merge commit (gh.pr.push-fix mode merge: plain push, a
    fast-forward of the PR branch). A clean local merge is committed as is."""
    head, br = p["headRefOid"], p["headRefName"]
    tag = f"REV{n}-{head[:9]}"; wd = f"{_TMPROOT()}/{tag}" if A.dry_run else f"{_WT_ROOT()}/pi-wt-rev-{n}"
    res = {"qa": qa, "sev": "", "fp": fp, "steps": [], "model": [], "base": None, "pred_pr": n, "wd": wd, "branch": None, "round": "merge"}
    out = {"status": "failed", "kind": "transient", "error": None, "action_key": None}
    created = False; ev = f"{OUT}/{d}-{tag}-merge{'-dry' if A.dry_run else ''}"
    c = {"qa": qa, "fp": fp, "sev": "", "tag": tag, "title": f"修复 PR #{n} 跟 main 冲突：合入 main", "finding": {}, "source": "revise"}
    def step(k, ok, text=""): res["steps"].append((k, ok, text)); log(k, "OK" if ok else "FAIL")
    log("merge main into", n, head[:9])
    try:
        os.makedirs(_TMPROOT(), exist_ok=True)
        with repo_lock():
            if os.path.exists(wd): log("leftover ->", drop_worktree(wd))
            rc, o = git("fetch", "-q", "origin", "main", f"+refs/heads/{br}:refs/remotes/origin/{br}")
            if rc: raise Transient(f"git fetch 失败 rc={rc}：{tail(o, 2)}")
            if git("rev-parse", f"refs/remotes/origin/{br}")[1].strip() != head: raise Transient("PR 的 head 在这期间变了")
            main = git("rev-parse", "origin/main")[1].strip(); res["base"] = main
            old_base = git("merge-base", main, head)[1].strip()
            rc, o = git("worktree", "add", "-q", "--detach", wd, head)
            if rc == 0: W.mark(wd, "dry" if A.dry_run else "revise", repo="mivo", clone=os.path.abspath(_GIT()), ticket=tag, base_sha=head,
                               lock=f"{OUT}/locks/rev-{n}.lock", tickets=f"{OUT}/tickets.json", ticket_key=f"rev:{n}")
            if rc: raise Transient(f"worktree add 失败：{tail(o, 3)}")
            created = True
        _, o = git("diff", "--name-only", old_base, head, cwd=wd)
        pr_files = o.split()
        tests = [f for f in pr_files if RUNNABLE_TEST_RE.search(f)]
        _, pdiff = git("diff", old_base, head, "--", ".", ":(exclude)package-lock.json", ":(exclude)docs/pr", cwd=wd)
        rc, o = git(*GIT_ID, "merge", "--no-ff", "--no-commit", main, cwd=wd)
        conflicted = [f for f in git("diff", "--name-only", "--diff-filter=U", cwd=wd)[1].split() if f]
        if "package-lock.json" in conflicted:   # bot fixes never change dependencies: main's lock file
            git("checkout", main, "--", "package-lock.json", cwd=wd); git("add", "package-lock.json", cwd=wd); conflicted.remove("package-lock.json")
        step(f"合入 main {main[:9]}：{'冲突 ' + '、'.join(conflicted) if conflicted else '没有冲突'}", True, tail(o, 20))
        if git("rev-parse", "-q", "--verify", "MERGE_HEAD", cwd=wd)[0]:
            raise Deterministic(f"本地合入 main 没有进行中的合并（GitHub 说有冲突，本地 git merge：{tail(o, 3)}）")
        x = finding_of(qa, fp); c["finding"] = x; c["sev"] = res["sev"] = x.get("severity") or ""
        session = f"pi-line-prfix-{tag}-merge-{now_cst():%m%d%H%M%S}"
        def ask(k, prompt):
            yield_to_panoptes()
            try: text, task = pi(TIER, wd, step_prompt(k, prompt), session, timeout=2400, write=True, skills=False, nice=True)
            except Exception as e: raise Transient(f"模型 {k} 调用失败：{str(e)[:300]}")
            res["model"].append({"step": k, "task": task, "text": text}); return text
        left = conflicted
        for i in range(1, 4):
            if not left: break
            ask(f"merge{i}", (f"This worktree is our own draft fix PR #{n} (branch {br}) for QA report PR-{qa} {c['sev']}: {x.get('title') or p.get('title')}. "
                              f"The current main {main[:9]} is being merged into it and these files have conflicts: {left}. Resolve every conflict: keep "
                              f"main's changes AND the PR's fix (the PR's own diff is below), remove all conflict markers, change nothing else. Do not run "
                              f"git commands.\n\nThe PR's diff:\n{pdiff[:12000]}\n{RULES}") if i == 1 else
                (f"Conflict markers are still in: {left}. Resolve them (keep main's changes and the fix). {RULES}"))
            left = [f for f in conflicted if os.path.isfile(os.path.join(wd, f)) and CONFLICT_MARK_RE.search(_read(os.path.join(wd, f), "rb").decode("utf-8", "replace"))]
        if left: raise Deterministic(f"跟 main 的冲突解了 3 轮还有冲突标记：{left[:4]}")
        if conflicted: git("add", "--", *conflicted, cwd=wd)
        if conflicted: step("冲突已解开", True, "、".join(conflicted))
        rc, o = npm_ci(wd); step("npm ci", rc == 0, f"rc={rc}\n{o}")
        if rc: raise Transient(f"npm ci rc={rc}")
        live_tests = [f for f in tests if os.path.isfile(os.path.join(wd, f))]
        if live_tests:
            rc, o = vitest(wd, live_tests); v = vitest_verdict(rc, o); step("合入 main 后 PR 的回归测试通过", v == "pass", f"rc={rc}\n{o}")
            if v != "pass": raise Deterministic(f"合入 main 后 PR 的回归测试不过：{failing_blocks(o, 300)}")
        src = [f for f in pr_files if not is_test(f) and not f.startswith("docs/") and os.path.isfile(os.path.join(wd, f))]
        rc4, o4, _ = related_run(wd, src, live_tests)
        step("合入 main 后相关的已有测试", rc4 == 0, tail(o4, 40))
        if rc4: raise Deterministic(f"合入 main 后相关的已有测试不过：{failing_blocks(o4, 300)}")
        akey = f"revise:{n}:{head[:12]}:merge"
        if git("diff", "--quiet", "--", "package-lock.json", cwd=wd)[0]: git("checkout", "--", "package-lock.json", cwd=wd)
        rc, o = git(*GIT_ID, "commit", "-q", "--no-verify", "-m", f"Merge main {main[:9]} into {br}", "-m", f"QA: #{qa}\nFingerprint: {fp}\nPi-Revise: {akey}", cwd=wd)
        step("合并提交（作者 Praise，本地）", rc == 0, o)
        if rc: raise Deterministic(f"合并提交失败：{tail(o, 3)}")
        new = git("rev-parse", "HEAD", cwd=wd)[1].strip()
        pf, pfo = preflight(wd); res["preflight"] = pf
        step(f"推送前检查 .githooks/pre-push（{pf['passed']}/{pf['total']}，{pf['seconds']}s）", pf["ok"], pfo)
        if pf["timeout"]: raise Transient("推送前检查超时（1 小时）")
        if not pf["ok"]: raise Deterministic(f"合入 main 后推送前检查没过：{pf['failed'] or 'rc=' + str(pf['rc'])}")
        if A.dry_run: out = {"status": "dry_ok", "kind": None, "error": None, "action_key": None}
        else:
            bundle = f"{OUT}/{d}-{tag}.merge.bundle"
            rc, o = git("bundle", "create", bundle, "HEAD", "--not", head, main, cwd=wd)
            if rc: raise Deterministic(f"打包合并提交失败：{tail(o, 3)}")
            record_actions("praise-pr", d, [{"key": akey, "type": "gh.pr.push-fix", "mode": "merge", "pr": n, "branch": br, "head": head, "main": main,
                "new_head": new, "bundle": bundle, "qa": qa, "fingerprint": fp, "conflicts": conflicted,
                "preflight": f"{pf['passed']}/{pf['total']} ok in {pf['seconds']}s",
                "note": "outbound: clone the PR branch, require HEAD == head, fetch main + the bundle, require new_head's parents == [head, main], plain push"}])
            out = {"status": "pr_action_written", "kind": None, "error": None, "action_key": akey}
    except Transient as e:
        out = {"status": "failed", "kind": "waiting" if isinstance(e, Waiting) else "transient", "error": str(e), "action_key": None}
    except Deterministic as e:
        out = {"status": "failed", "kind": "deterministic", "error": str(e), "action_key": None}
    except PraiseNeeded as e:
        out = {"status": "failed", "kind": "praise", "error": str(e), "action_key": None}
    except Exception as e:
        out = {"status": "failed", "kind": "transient", "error": f"内部错误：{e!r}"[:300], "action_key": None}
    finally:
        try: write_atomic(ev + ".md", evidence_md(c, res, out, wd))
        except Exception as e: log("evidence write failed", e)
        if created:
            try:
                git("merge", "--abort", cwd=wd)
                with repo_lock(): log("cleanup", drop_worktree(wd))
            except Exception as e: log("cleanup failed", e)
    res["evidence"] = ev + ".md"
    return out, res

def revise_one(A, d, n, qa, fp, p, red):
    head, br = p["headRefOid"], p["headRefName"]
    tag = f"REV{n}-{head[:9]}"; wd = f"{_TMPROOT()}/{tag}" if A.dry_run else f"{_WT_ROOT()}/pi-wt-rev-{n}"
    names = [c.get("name") or c.get("context") or "?" for c in red]
    res = {"qa": qa, "sev": "", "fp": fp, "steps": [], "model": [], "base": None, "pred_pr": n, "wd": wd, "branch": None, "round": "revise"}
    out = {"status": "failed", "kind": "transient", "error": None, "action_key": None}
    created = False; ev = f"{OUT}/{d}-{tag}{'-dry' if A.dry_run else ''}"
    c = {"qa": qa, "fp": fp, "sev": "", "tag": tag, "title": f"修复 PR #{n} 的 CI：{', '.join(names[:4])}", "finding": {}, "source": "revise"}
    def step(k, ok, text=""): res["steps"].append((k, ok, text)); log(k, "OK" if ok else "FAIL")
    log("revise", n, head[:9], names)
    try:
        os.makedirs(_TMPROOT(), exist_ok=True)
        with repo_lock():
            if os.path.exists(wd): log("leftover ->", drop_worktree(wd))
            rc, o = git("fetch", "-q", "origin", "main", f"+refs/heads/{br}:refs/remotes/origin/{br}")
            if rc: raise Transient(f"git fetch 失败 rc={rc}：{tail(o, 2)}")
            if git("rev-parse", f"refs/remotes/origin/{br}")[1].strip() != head: raise Transient("PR 的 head 在这期间变了")
            base = git("merge-base", "origin/main", head)[1].strip(); res["base"] = base
            rc, o = git("worktree", "add", "-q", "--detach", wd, head)
            if rc == 0: W.mark(wd, "dry" if A.dry_run else "revise", repo="mivo", clone=os.path.abspath(_GIT()), ticket=tag, base_sha=head,
                               lock=f"{OUT}/locks/rev-{n}.lock", tickets=f"{OUT}/tickets.json", ticket_key=f"rev:{n}")
            if rc: raise Transient(f"worktree add 失败：{tail(o, 3)}")
            created = True
        junk = set(); chg = lambda: changed(wd, base, junk)
        rc, o = npm_ci(wd); step("npm ci", rc == 0, f"rc={rc}\n{o}")
        if rc: raise Transient(f"npm ci rc={rc}")
        _, o = git("diff", "--name-only", base, head, cwd=wd)
        tests = [f for f in o.split() if RUNNABLE_TEST_RE.search(f) and os.path.isfile(os.path.join(wd, f))]
        rvc = any(not is_test(f) and not f.startswith("docs/") for f in o.split())   # a test-code fix PR: no source to revert
        _, pdiff = git("diff", base, head, "--", ".", ":(exclude)package-lock.json", ":(exclude)docs/pr", cwd=wd)
        x = finding_of(qa, fp); c["finding"] = x; c["sev"] = res["sev"] = x.get("severity") or ""
        logs = failed_check_logs(red)
        session = f"pi-line-prfix-{tag}-{now_cst():%m%d%H%M%S}"; texts = []; touched = set()
        def ask(k, prompt):
            yield_to_panoptes()
            before = snapshot(wd, sorted(set(changed(wd, head, junk)) | touched))
            try: text, task = pi(TIER, wd, step_prompt(k, prompt), session, timeout=2400, write=True, skills=False, nice=True)
            except Exception as e: raise Transient(f"模型 {k} 调用失败：{str(e)[:300]}")
            after = snapshot(wd, sorted(set(changed(wd, head, junk)) | set(before)))
            touched.update(f for f in after if f not in before or after.get(f) != before.get(f))   # files the model itself wrote
            # (review 10-24; "not in before": a file it deleted or moved away reads None both times)
            res["model"].append({"step": k, "task": task, "text": text}); texts.append(text); return text
        follow = ("The PR's regression test(s) must keep testing the bug: you may change how they run (Windows paths, timing, platform "
                  "differences) but never weaken them -- no skip/only/todo, no deleted cases or assertions. Do not touch docs/pr/. "
                  "If a check fails for a reason this PR cannot fix (CI infrastructure, the same failure on main), change nothing and say so.")
        first = (f"This worktree is the head of our own DRAFT fix PR #{n} (branch {br}) for QA report PR-{qa} {c['sev']} ({fp}): "
                 f"{x.get('title') or p.get('title')}\nfile: {x.get('file')}:{x.get('line')}\n\nGitHub CI failed on this head: {', '.join(names)}.\n"
                 f"Failed-step logs:\n{logs}\n\nThe PR's diff against main:\n{pdiff[:12000]}\n\nFind why these checks fail and fix it in this worktree. "
                 f"{follow} {RULES}\nEnd with CAUSE: one paragraph, and the files you changed.")
        fb = None
        for i in range(1, FIX_ROUNDS):
            text = ask(f"revise{i}", first if i == 1 else f"{fb}\n\n{follow} {RULES}\nEnd with CAUSE: one paragraph, and the files you changed.")
            if not changed(wd, head, junk):
                if res.get("ruled_R2"): raise Deterministic(f"CI 没过（{', '.join(names[:4])}），裁决后又看了一轮仍认为不是这个 PR 能修的：{text.strip()[:200]}")
                v, jt, _ = gated_ruling("R2", c, wd, base, f"GitHub CI failed on our draft fix PR #{n}: {', '.join(names)}.\nFailed-step logs:\n{logs[:6000]}\n\nThe fixing model "
                               f"looked at it, changed nothing, and says it is not caused by the PR:\n{text.strip()[-2500:]}\n\nThe PR's diff:\n{pdiff[:8000]}\n\n"
                               "Is the failure caused by this PR's change? UNRELATED = no (flaky, broken on main, CI infrastructure); RELATED = yes.", tag, step,
                               failure_text=logs, changed_files=re.findall(r"(?m)^diff --git a/(\S+)", pdiff), failing_names=names)
                res["ruled_R2"] = (v, jt); step(f"裁决 R2（CI 失败与本 PR 有没有关系）：{v or '没给出结论'}", v is not None, jt)
                if v == "UNRELATED":   # binding: re-run the failed jobs once (prwatch: still red after that -> another round / a human)
                    akey = f"rerun-ruled:{n}:{head[:12]}"
                    if not A.dry_run: record_actions("praise-pr", d, [{"key": akey, "type": "gh.run.rerun-failed", "pr": n, "head": head, "names": names,
                                                                      "ruled": True, "ruling": jt[:600]}])
                    out = {"status": "dry_ok" if A.dry_run else "pr_action_written", "kind": None, "error": None, "action_key": None if A.dry_run else akey,
                           "rerun_only": True}
                    res["evidence"] = ev + ".md"; return out, res
                if v is None: raise Transient(f"CI 没过（{', '.join(names[:4])}），裁决模型没给出结论，稍后重试")
                fb = f"A judge ruled that this CI failure IS caused by the PR:\n{jt[-3000:]}\n\nFix it."; ok = False; info = {"why": "裁决认为跟本 PR 有关"}
                step(f"revise 第 {i} 轮：交回裁决理由再修", False, fb); continue
            probs = [q for f in tests for q in test_edit_problems(wd, head, f)]
            if probs:
                to_base(wd, head, sorted({q.split("：")[0] for q in probs}))
                fb = "Your edits to the regression test(s) were reverted because they weaken them:\n- " + "\n- ".join(probs); ok = False; info = {"why": "削弱了回归测试"}
            else:
                ok, fb, info = check_fix(wd, base, tests, set(tests), step, chg, guard_base=head, skip=junk, revert_check=rvc) if tests else (True, None, {})
            if ok: break
            step(f"revise 第 {i} 轮没过：{info.get('why')}", False, fb)
        else: raise Deterministic(f"CI 修了 {FIX_ROUNDS - 1} 轮本地检查仍没过：{info.get('why')}")
        for r in range(2):
            pf, pfo = preflight(wd); res["preflight"] = pf; junk.update(untracked(wd))
            step(f"推送前检查 .githooks/pre-push（{pf['passed']}/{pf['total']}，{pf['seconds']}s）", pf["ok"], pfo)
            if pf["timeout"]: raise Transient("推送前检查超时（1 小时）")
            if pf["ok"]: break
            if r == 1: raise Deterministic(f"改完后推送前检查没过：{pf['failed'] or 'rc=' + str(pf['rc'])}")
            ask("revise-prepush", f"The repo's pre-push checks FAILED at `{pf['failed'] or '?'}` after your change:\n{failing_blocks(pfo, 6000)}\n"
                f"Fix it. {follow} {RULES}")
            ok, fb, info = check_fix(wd, base, tests, set(tests), step, chg, guard_base=head, skip=junk, revert_check=rvc) if tests else (True, None, {})
            if not ok: raise Deterministic(f"推送前检查修复后本地检查不过：{info.get('why')}")
        if git("diff", "--quiet", "--", "package-lock.json", cwd=wd)[0]: git("checkout", "--", "package-lock.json", cwd=wd)
        files = changed(wd, head, junk)          # what this revision changes on top of the PR head
        if not files: raise Deterministic("改完后相对 PR 当前 head 没有任何改动")
        stray = sorted(set(files) - touched)
        if stray: raise Deterministic(f"相对 PR head 改动了模型没碰过的文件，拒绝推送：{stray[:5]}")
        sens = sensitive_files(files)   # 10-25: no longer a stop (see A_ALLOW); the commit message names them for the reviewer
        if sens: step(f"CI 修正改了 CI 配置 / 仓库规则 / 密钥相关的文件（放行，交审查机把关）：{', '.join(sens[:4])}", True)
        title = f"fix: CI 没过的修正（{', '.join(names[:3])}）"[:120]
        if A.dry_run:
            out = {"status": "dry_ok", "kind": None, "error": None, "action_key": None}
        else:
            git("add", "-A", "--", *files, cwd=wd)
            akey = f"revise:{n}:{head[:12]}"
            _, staged = git("diff", "--cached", "--no-renames", "--name-only", "-z", head, cwd=wd)
            extra = sorted(set(f for f in staged.split("\0") if f) - set(files) - {"package-lock.json"})
            if extra: raise Deterministic(f"提交里混进了模型没改的文件，拒绝推送：{extra[:5]}")
            msg = f"QA: #{qa}\nFingerprint: {fp}\nPi-Revise: {akey}" + (f"\nSensitive-Files: {', '.join(sens[:8])}" if sens else "")
            rc, o = git(*GIT_ID, "commit", "-q", "-m", title, "-m", msg, cwd=wd)
            step("本地提交（作者 Praise）", rc == 0, o)
            if rc: raise Deterministic(f"本地提交失败：{tail(o, 3)}")
            _, pdiff2 = git("diff", "--binary", head, "HEAD", "--", ".", ":(exclude)package-lock.json", cwd=wd)
            pfile = f"{OUT}/{d}-{tag}.push.patch"; write_atomic(pfile, pdiff2)
            write_atomic(pfile[:-6] + ".md", f"{title}\n\n" + (texts[-1] if texts else ""))
            record_actions("praise-pr", d, [{"key": akey, "type": "gh.pr.push-fix", "pr": n, "branch": br, "head": head, "patch": pfile,
                "title": title, "message": msg, "qa": qa, "fingerprint": fp, "checks": names,
                "preflight": f"{pf['passed']}/{pf['total']} ok in {pf['seconds']}s",
                "note": "outbound: clone the PR branch, require HEAD == head, git apply, commit, plain push (no force); draft only"}])
            out = {"status": "pr_action_written", "kind": None, "error": None, "action_key": akey}
    except Transient as e:
        out = {"status": "failed", "kind": "waiting" if isinstance(e, Waiting) else "transient", "error": str(e), "action_key": None}
    except Deterministic as e:
        out = {"status": "failed", "kind": "deterministic", "error": str(e), "action_key": None}
    except PraiseNeeded as e:
        out = {"status": "failed", "kind": "praise", "error": str(e), "action_key": None}
    except Exception as e:
        out = {"status": "failed", "kind": "transient", "error": f"内部错误：{e!r}"[:300], "action_key": None}
    finally:
        try:
            if created and os.path.isdir(wd):
                _, diff = git("diff", "--binary", head, "--", ".", ":(exclude)package-lock.json", cwd=wd)
                write_atomic(ev + ".patch", diff)
            write_atomic(ev + ".md", evidence_md(c, res, out, wd))
        except Exception as e: log("evidence write failed", e)
        if created:
            try:
                with repo_lock(): log("cleanup", drop_worktree(wd))
            except Exception as e: log("cleanup failed", e)
    res["evidence"] = ev + ".md"
    return out, res

def scrub_env():
    """10-24 security: everything prfix starts (the model, its bash tool, npm / vitest / the pre-push hook, whose output goes back to
    the model) sees only lib.model_env()'s allowlist -- not the secrets of whoever launched it (a Claude session, prwatch). gh and git
    authenticate from ~/.config/gh, not from the environment."""
    keep = model_env(); os.environ.clear(); os.environ.update(keep)

def main(argv=None):
    scrub_env()
    ap = argparse.ArgumentParser(description="Praise PR fix duty (Pi line): one QA finding per run")
    ap.add_argument("--next", action="store_true", help="highest-priority unclaimed ticket (default)")
    ap.add_argument("--qa", type=int); ap.add_argument("--fp")
    ap.add_argument("--list", action="store_true"); ap.add_argument("--pending-json", action="store_true")
    ap.add_argument("--cleanup-stale", action="store_true"); ap.add_argument("--yes", action="store_true")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--base")
    ap.add_argument("--revise", type=int, help="our own draft fix PR N went red on GitHub CI: fix it (prwatch launches this)")
    ap.add_argument("--allow-sensitive", action="store_true", help="no-op since 10-25 (sensitive files no longer stop the bot); kept so old commands still run")
    A = ap.parse_args(argv); A_ALLOW["sensitive"] = bool(A.allow_sensitive) and bool(A.qa or A.revise)
    if A.fp and not A.qa: ap.error("--fp needs --qa")
    if A.cleanup_stale: return cleanup_stale(A.yes)
    if A.revise:
        try: return revise(A)
        except Exception as e: return finish({"status": "error", "pr": A.revise, "error": f"内部错误：{e!r}"[:300]}, 1, A)
    real_out = sys.stdout
    if A.pending_json: sys.stdout = sys.stderr      # logs from helpers must never corrupt the JSON on stdout
    try:
        try:
            ctx = gather()
        except Exception as e:
            if A.list or A.pending_json: print(f"prfix: 读输入失败：{e}", file=sys.stderr); return 1
            return finish({"status": "error", "error": f"读输入失败：{str(e)[:300]}"}, 1, A)
        _LINKS.clear(); _LINKS.update(ctx["links"])
        if ctx["gate_warn"]: log("warn:", ctx["gate_warn"])
        vs = evaluate_all(ctx)
        if A.list: print_list(vs, ctx); return 0
        try: write_manual_orders(vs, ctx)
        except Exception as e: log("manual-orders write failed", e)
        if A.pending_json: print(json.dumps(pending(vs), ensure_ascii=False, indent=1), file=real_out); return 0
        log("mode", mode(), "dry" if A.dry_run else "real", f"candidates {len(vs)} todo {sum(v['state'] == 'todo' for v in vs)}")
        try: return run_ticket(A, ctx, vs)
        except Exception as e: return finish({"status": "error", "error": f"内部错误：{e!r}"[:300]}, 1, A)
    finally:
        sys.stdout = real_out

if __name__ == "__main__": sys.exit(main())


def __getattr__(name):   # GIT WT_ROOT TMPROOT QA_LOCK: resolved lazily (env read at use, not at import), old attribute reads keep working
    if name in ('GIT', 'WT_ROOT', 'TMPROOT', 'QA_LOCK'): return globals()["_" + name]()
    raise AttributeError(name)
