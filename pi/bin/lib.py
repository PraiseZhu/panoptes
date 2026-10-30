import json, os, subprocess, sys, glob, datetime as dt, re, shutil, tempfile
REPO = "xindong/mivo-canvas-plugin"
# Where things live (10-24: the Pi line moved into PraiseZhu/panoptes pi/; three kinds of place, see pi/README.md):
#   PI_CODE   this pi/ directory (code, conf, cron, tests) -- deployed with the /workspace/panoptes checkout
#   PI_STATE  data to keep: out/, takeover/, outbound-enabled, outbound-disabled-types, ops-repair-enabled (backed up hourly)
#   PI_WORK   throw-away work: clones/, wt/, cache/, tmp/, .trash/, locks/ (not backed up, cleaned in seconds)
PI_CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PI_STATE = os.environ.get("PI_STATE") or "/workspace/mivo-qa/pi"
PI_WORK = os.environ.get("PI_WORK") or "/workspace/pi-work"
BASE = PI_STATE   # historic name: every f"{BASE}/out/...", takeover/, outbound-* path is state; code paths use PI_CODE
QA = "/workspace/mivo-qa"
RPT = f"{QA}/reports/merged"
CST = dt.timezone(dt.timedelta(hours=8))

def log(*a): print(dt.datetime.now(CST).strftime("%H:%M:%S"), *a, flush=True)

def data_day(argv):
    if len(argv) > 1: return argv[1]
    return (dt.datetime.now(CST).date() - dt.timedelta(days=1)).isoformat()

def utc_window(day):
    d = dt.date.fromisoformat(day)
    a = dt.datetime(d.year, d.month, d.day, tzinfo=CST).astimezone(dt.timezone.utc)
    b = a + dt.timedelta(days=1)
    f = lambda x: x.strftime("%Y-%m-%dT%H:%M:%SZ")
    return f(a), f(b)

def gh(args, js=True):
    r = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=120)
    if r.returncode != 0: raise RuntimeError(f"gh {' '.join(args)}: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout) if js else r.stdout

def parse_ts(s):
    if not s: return None
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))

def cst(s):
    t = parse_ts(s) if isinstance(s, str) else s
    return t.astimezone(CST).strftime("%m-%d %H:%M") if t else "?"

def merged_prs(day):
    a, b = utc_window(day)
    prs = gh(["pr", "list", "-R", REPO, "--state", "merged", "--limit", "300",
              "--search", f"merged:{a}..{b}",
              "--json", "number,title,author,mergedAt,mergedBy,headRefOid,body"])
    # gh search is minute-granular; re-filter exactly
    prs = [p for p in prs if a <= p["mergedAt"] < b]
    return sorted(prs, key=lambda p: p["number"], reverse=True)

def load_report(n):
    f = f"{RPT}/PR-{n}.json"
    if not os.path.exists(f): return None, None
    try: d = json.load(open(f))
    except Exception as e: return f, {"_error": str(e)}
    pr = next((p for p in d.get("prs", []) if p.get("number") == n), None)
    return f, {"doc": d, "pr": pr}

def first_report_time(n, cur):
    """Earliest generation time across current + history versions (for G1)."""
    ts = []
    for f in [f"{RPT}/PR-{n}.json"] + glob.glob(f"{RPT}/history/PR-{n}.v*.json"):
        try:
            d = json.load(open(f)); t = d.get("generated_at") or d.get("run", {}).get("finished_at")
            if t: ts.append((parse_ts(t), f))
        except Exception: pass
    return min(ts) if ts else (None, None)

def write_atomic(path, text):
    import tempfile
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix="." + os.path.basename(path) + ".")   # unique: concurrent writers never share a tmp
    with os.fdopen(fd, "w") as f: f.write(text)
    os.chmod(tmp, 0o644); os.replace(tmp, path)
    log("wrote", path)

# ---------------- Pi line common ----------------
MODE_FILE = "/workspace/fleet-mode"
OUTBOUND_FLAG = f"{BASE}/outbound-enabled"

def mode():
    """fleet mode; PI_FLEET_MODE env overrides the file (for simulation/tests only, never writes the file)."""
    ov = os.environ.get("PI_FLEET_MODE", "").strip().lower()
    if ov: return ov if ov in ("grok", "pi") else "grok"
    try: m = open(MODE_FILE).read().strip().lower()
    except Exception: m = "grok"
    return m if m in ("grok", "pi") else "grok"

def now_cst(): return dt.datetime.now(CST)
def today(): return now_cst().date().isoformat()

# Praise 10-27: ops/watchdog/relay DMs to him are noise. While PRAISE_DM_MUTE exists, no Slack DM goes to Praise unless its
# dedupe key starts with a prefix listed in that file (one per line, '#' comments; e.g. "brief:" keeps jasper's 09:00 brief).
# Alerts still land in files (watchdog log / out/outbound/muted-*.md) for bots. Delete the file to restore the old behaviour.
PRAISE_DM_MUTE = f"{BASE}/praise-dm-mute"
def praise_dm_blocked(key):
    try: allow = [l.strip() for l in open(PRAISE_DM_MUTE) if l.strip() and not l.lstrip().startswith("#")]
    except FileNotFoundError: return False
    return not str(key).startswith(tuple(allow))
def record_muted(job, key, text):
    """Keep a muted Praise DM in a file so bots can still act on it."""
    d = f"{BASE}/out/{job}"; os.makedirs(d, exist_ok=True)
    with open(f"{d}/muted-{now_cst().strftime('%Y-%m-%d')}.md", "a") as f: f.write(f"## {now_cst().isoformat(timespec='seconds')} {key}\n{text}\n\n")


def record_actions(job, day, actions):
    """Write the outbound actions this job WOULD take. Each action needs a stable 'key' (idempotency).
    Outbound is executed only if mode()=='pi' AND OUTBOUND_FLAG exists; executors are deliberately
    not wired yet (pending Praise approval), so even then nothing is sent."""
    import fcntl
    path = f"{BASE}/out/{job}/{day}.actions.json"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lk = open(path + ".lock", "w"); fcntl.flock(lk, fcntl.LOCK_EX)   # read-modify-write under a lock: two jobs share some files
    prev = json.load(open(path)) if os.path.exists(path) else {"actions": []}
    seen = {a["key"]: a for a in prev.get("actions", [])}
    for a in actions:
        a.setdefault("status", "would-do"); a.setdefault("at", now_cst().isoformat(timespec="seconds"))   # outbound TTLs use "at"
        if a["key"] in seen and seen[a["key"]].get("status") == "done": continue  # never redo a done action
        seen[a["key"]] = {**seen.get(a["key"], {}), **a}
    gate = "pi+flag" if (mode() == "pi" and os.path.exists(OUTBOUND_FLAG)) else "off"
    doc = {"job": job, "day": day, "mode": mode(), "outbound_gate": gate,
           "note": "Executed only by bin/outbound.py when fleet-mode=pi AND outbound-enabled exists; done keys never redone.",
           "updated_at": now_cst().isoformat(timespec="seconds"), "actions": list(seen.values())}
    if gate != "off": log("outbound gate open -> bin/outbound.py (cron */10) will execute pending actions")
    write_atomic(path, json.dumps(doc, ensure_ascii=False, indent=1)); lk.close()

# What a model may see of our environment (10-24): only these names. The 05:19 manual `prfix --qa 1188` (started from a Claude
# session) ran `env | grep -i "pi_|repro|qa"`; __CURSOR_SANDBOX_ENV_RESTORE (value = "builtin export DEEPSEEK_API_KEY=...;
# MIVO_SLACK_BOT_TOKEN=...; ...") matched "pi_" in "API_KEY", and 4 real tokens went to the model and into its session log.
# An allowlist, because secrets also hide in the VALUES of harmless-looking names. DEEPSEEK_API_KEY stays: pi itself needs it
# (models.json apiKey "$DEEPSEEK_API_KEY") and it only reaches the provider it belongs to.
MODEL_ENV_KEEP = re.compile(r"^(PATH|HOME|USER|LOGNAME|SHELL|LANG|LANGUAGE|LC_[A-Z]+|TZ|TERM|TMPDIR|XDG_[A-Z_]+|DISPLAY|NODE_OPTIONS"
                            r"|SAND_EGRESS_[A-Z_]+|SAND_HTTP_PROXY_NAME|PI_[A-Z0-9_]+|PRFIX_[A-Z0-9_]+|INFRA_[A-Z0-9_]+|DEEPSEEK_API_KEY)$")
def model_env(extra=None):
    """The environment for a model and everything a model-driven job starts (tests, hooks, the model's tools): allowlisted names
    only, plus DEEPSEEK_API_KEY (from the secret file when the caller has none)."""
    env = {k: v for k, v in os.environ.items() if MODEL_ENV_KEEP.match(k)}
    if not env.get("DEEPSEEK_API_KEY"):
        key = secret_env_dict().get("DEEPSEEK_API_KEY")
        if key: env["DEEPSEEK_API_KEY"] = key
    env.update(extra or {})
    return env

# The model also runs a bash tool as this user, so it could READ the box's secrets (Praise 10-24: put it in an isolated environment).
# bwrap around pi: own PID namespace + /proc (no other process's environ), the places that hold a real secret (10-24 scan of
# ~, /workspace, /etc, /opt for the real values) hidden behind an empty tmpfs / /dev/null. Network, the worktrees, ~/.pi, the
# node toolchain, ~/.npm and /tmp stay as they are. PI_NO_SANDBOX=1 turns it off (debugging only).
SANDBOX_HIDE_DIRS = ("~/.config/gh", "~/.config/mivo-qa", "~/.config/cindy-launch", "~/.config/CindyGlobal", "~/.pi-fallback-secret",
                     "~/.claude/projects", "~/.claude/shell-snapshots", "~/.cursor", "~/.codex", "~/sand-data", "~/.local/share/keyrings",
                     "~/.ssh", "~/.pi-fallback.git", "/workspace/mivo-merger/secrets", "/workspace/migrate/vault")
SANDBOX_HIDE_FILES = ("~/.claude/.credentials.json", "~/.git-credentials", "~/.netrc")
# 10-24 Q4 (plan 2026-10-24): the machine is READ-ONLY for the model; on top of the secret hiding above, only these are writable:
#   always       what the agent itself writes: ~/.pi (sessions, usage log), PI_STATE/out/tasks (task results), ~/.npm (npx cache)
#   write=True   the call's cwd and, when cwd is a git worktree, its repo's git dir (index, objects, refs: git add / stash / commit)
#   rw=(...)     extra dirs a job names (e.g. an output dir)
#   /tmp         a private /tmp for a call in a bot worktree (PI_WORK/tmp/sbx-*, gone after the call); other calls keep /tmp
# A person's checkouts, the deployed code (/workspace/panoptes), other tickets' worktrees and everything else cannot be changed.
SANDBOX_RW_ALWAYS = ("~/.pi", f"{BASE}/out/tasks", "~/.npm")
def _git_dir_of(d):
    try:
        r = subprocess.run(["git", "-C", d, "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None
    except Exception: return None
def sandbox_prefix(cwd=None, write=False, rw=(), tmp=None):
    """bwrap argv for one model call (see SANDBOX_RW_ALWAYS above); [] when PI_NO_SANDBOX=1 or bwrap is missing"""
    if os.environ.get("PI_NO_SANDBOX") == "1" or not os.path.exists("/usr/bin/bwrap"): return []
    a = ["/usr/bin/bwrap", "--ro-bind", "/", "/", "--dev-bind", "/dev", "/dev", "--unshare-pid", "--proc", "/proc", "--die-with-parent"]
    if tmp: a += ["--bind", tmp, "/tmp"]                  # first: a writable dir under the real /tmp is bound again on top of it
    w = [os.path.expanduser(x) for x in SANDBOX_RW_ALWAYS] + ([] if tmp else ["/tmp"])
    if write and cwd:
        w.append(os.path.abspath(cwd))
        g = _git_dir_of(cwd)
        if g: w.append(g)
    w += [os.path.abspath(os.path.expanduser(x)) for x in rw]
    for d in dict.fromkeys(w):
        if os.path.isdir(d): a += ["--bind", d, d]
    for d in map(os.path.expanduser, SANDBOX_HIDE_DIRS):     # after the binds: a writable parent never shows a hidden dir again
        if os.path.isdir(d): a += ["--tmpfs", d]
    for f in map(os.path.expanduser, SANDBOX_HIDE_FILES):
        if os.path.isfile(f): a += ["--ro-bind", "/dev/null", f]
    return a + ["--"]

def pi(tier, cwd, prompt, session, timeout=600, write=False, web=False, skills=True, nice=False, rw=()):
    """Run Pi via pi-auto (tier picks model/thinking). Returns (text, task_id).
    skills=False: pi without skill discovery (bin/pi-noskills.sh; 10-24: ~/.pi/agent/settings.json loads ~/.claude/skills, 36 KB of
    Claude Code skills in every coding step's system prompt). nice=True: the agent and the tests it runs at nice 10 (merge tests first)."""
    env = model_env({"PI_TASK_DIR": f"{BASE}/out/tasks", "PI_NOTIFY_OFF": "1", "PI_TASK_OWNER": "pi-line-cron"})   # never os.environ as is
    if not skills: env["PI_BIN"] = f"{PI_CODE}/bin/pi-noskills.sh"
    try: os.makedirs(f"{BASE}/out/tasks", exist_ok=True)      # the sandbox binds it writable only when it exists
    except OSError: pass
    tmp = None
    if os.path.realpath(cwd).startswith(os.path.realpath(PI_WORK) + os.sep):   # a bot worktree: its own /tmp for this call
        os.makedirs(f"{PI_WORK}/tmp", exist_ok=True)
        tmp = tempfile.mkdtemp(prefix="sbx-" + re.sub(r"[^A-Za-z0-9_.-]", "-", session)[:60] + "-", dir=f"{PI_WORK}/tmp")
    cmd = sandbox_prefix(cwd, write, rw, tmp) + (["nice", "-n", "10"] if nice else []) + [os.path.expanduser("~/tools/pi-call/pi-auto"), tier, cwd, prompt,
           "--session", session, "--owner", "pi-line-cron", "--timeout", str(timeout)]
    if not write: cmd.append("--no-write")
    if not web: cmd.append("--no-web")
    try: r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout + 120)
    finally:
        if tmp: shutil.rmtree(tmp, ignore_errors=True)
    m = re.search(r"^RESULT (.+)$", r.stdout, re.M); t = re.search(r"^TASK (\S+)", r.stdout, re.M)
    if r.returncode != 0 or not m: raise RuntimeError(f"pi {tier} rc={r.returncode} {r.stdout[-300:]} {r.stderr[-300:]}")
    return open(m.group(1).strip()).read(), (t.group(1) if t else "?")

# 修 PR bot harness version (prfix + prwatch): a ticket / revise that stopped for a human under an older version is tried once more with
# the new one. Bump it whenever a change could unblock what used to stop (10-24).
PRBOT_HARNESS = "2026-10-24e"

def panoptes_busy():
    return subprocess.run(["pgrep", "-f", "run-nightly.sh|argus/.*run"], capture_output=True).returncode == 0

TAKEOVER = os.environ.get("PI_TAKEOVER_DIR") or f"{BASE}/takeover"  # env override for tests only
def takeover(name): return os.path.exists(f"{TAKEOVER}/{name}")

# --- PR ownership guards (10-30: prfix pushed 51802504 onto #1444 while another worker owned it) ---------------------------
# Branch claim: takeover/branch-claims/<PR number>. A human or bot worker writes it and renews it; while it is valid prfix
# (--revise and outbound gh.pr.push-fix) leaves that PR alone, so it is also a per-PR off switch. Format: first line = expiry,
# ISO 8601 with offset ("2026-10-30T18:00:00+08:00") or Unix seconds; anything after the first line is a free-form note
# (who / why). Empty or unparsable first line: expiry = file mtime + CLAIM_TTL_H hours (so `touch` claims and renews).
CLAIM_TTL_H = 4
def claim_expiry(path):
    """expiry (aware datetime) of the claim file at path, or None when there is no file."""
    try:
        st = os.stat(path)
        with open(path, errors="replace") as f: first = (f.readline() or "").strip()
    except FileNotFoundError: return None
    try:
        if re.fullmatch(r"\d{9,11}", first): return dt.datetime.fromtimestamp(int(first), dt.timezone.utc)
        e = dt.datetime.fromisoformat(first.replace("Z", "+00:00"))
        if e.tzinfo: return e
    except ValueError: pass
    return dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc) + dt.timedelta(hours=CLAIM_TTL_H)
def branch_claim(n, now=None):
    """reason string while takeover/branch-claims/<n> holds a valid claim, else None."""
    e = claim_expiry(f"{TAKEOVER}/branch-claims/{int(n)}")
    now = now or dt.datetime.now(dt.timezone.utc)
    return f"takeover/branch-claims/{int(n)} 有效到 {e.isoformat(timespec='seconds')}：别的 worker 认领了这个 PR，不碰" if e and e > now else None
# Ownership: a commit on the PR branch in the last OWNER_WINDOW_H hours WITHOUT a Pi trailer (Pi-Open: first commit of a Pi fix PR,
# Pi-Revise: a --revise fix; Fingerprint alone does not count, a human may keep it) = someone else is working on it.
OWNER_WINDOW_H = 6
PI_MARK_RE = re.compile(r"^(Pi-Open|Pi-Revise): \S", re.M)
def foreign_recent_commits(commits, now=None, hours=OWNER_WINDOW_H):
    """commits: GitHub pulls/N/commits items -> short shas of recent commits without a PI_MARK_RE trailer."""
    now = now or dt.datetime.now(dt.timezone.utc); out = []
    for c in commits or []:
        cm = c.get("commit") or {}
        when = (cm.get("committer") or {}).get("date") or (cm.get("author") or {}).get("date")
        try: t = dt.datetime.fromisoformat(str(when).replace("Z", "+00:00"))
        except ValueError: t = now   # unreadable date: count it as recent (fail closed)
        if now - t <= dt.timedelta(hours=hours) and not PI_MARK_RE.search(cm.get("message") or ""): out.append((c.get("sha") or "?")[:9])
    return out
def ownership_block(n, commits, now=None):
    """reason not to push onto PR n (claim, or recent foreign commits), else None."""
    why = branch_claim(n, now)
    if why: return why
    f = foreign_recent_commits(commits, now)
    return f"PR 分支最近 {OWNER_WINDOW_H} 小时有不带 Pi-Open / Pi-Revise 标记的提交 {f[:5]}：别人在改，不推送" if f else None
def pr_commits_args(n): return ["api", "--paginate", f"repos/{REPO}/pulls/{int(n)}/commits?per_page=100"]
# Gate files: a planned change touching these is a hard stop; --allow-sensitive does NOT override it.
PROTECTED_RE = re.compile(r"(^|/)__tests__/(.*/)?[^/]*(Guard|Policy)[^/]*$|^\.githooks/|^scripts/verify")
def protected_files(files): return sorted(f for f in files if PROTECTED_RE.search(f))
def patch_files(path):
    with open(path, errors="replace") as f: return sorted({m.group(1) for m in re.finditer(r"^diff --git a/(\S+) b/", f.read(), re.M)})
SECRET_FILES = ("~/.pi-fallback-secret/env", "~/.config/mivo-qa/secrets.env")   # both outside every git repo / backup, mode 600
def secret_env_dict():
    """KEY=VALUE pairs of SECRET_FILES (an `export ` prefix and quotes allowed; the earlier file wins); never logs values.
    10-24: one reader for everyone -- watchdog's own copy read only the first file, so its bypass sender had no Slack token, and
    the `export ` prefix of the first file used to end up in the key name."""
    env = {}
    for f in SECRET_FILES:
        try: lines = open(os.path.expanduser(f)).read().splitlines()
        except OSError: continue
        for line in lines:
            line = line.strip()
            if line.startswith("export "): line = line[7:].lstrip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env
def secret_env():
    """Merge SECRET_FILES into os.environ without overriding; never logs values."""
    for k, v in secret_env_dict().items(): os.environ.setdefault(k, v)
