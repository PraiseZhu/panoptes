import json, os, subprocess, sys, glob, datetime as dt, re
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
def sandbox_prefix():
    if os.environ.get("PI_NO_SANDBOX") == "1" or not os.path.exists("/usr/bin/bwrap"): return []
    a = ["/usr/bin/bwrap", "--dev-bind", "/", "/", "--unshare-pid", "--proc", "/proc", "--die-with-parent"]
    for d in map(os.path.expanduser, SANDBOX_HIDE_DIRS):
        if os.path.isdir(d): a += ["--tmpfs", d]
    for f in map(os.path.expanduser, SANDBOX_HIDE_FILES):
        if os.path.isfile(f): a += ["--ro-bind", "/dev/null", f]
    return a + ["--"]

def pi(tier, cwd, prompt, session, timeout=600, write=False, web=False, skills=True, nice=False):
    """Run Pi via pi-auto (tier picks model/thinking). Returns (text, task_id).
    skills=False: pi without skill discovery (bin/pi-noskills.sh; 10-24: ~/.pi/agent/settings.json loads ~/.claude/skills, 36 KB of
    Claude Code skills in every coding step's system prompt). nice=True: the agent and the tests it runs at nice 10 (merge tests first)."""
    env = model_env({"PI_TASK_DIR": f"{BASE}/out/tasks", "PI_NOTIFY_OFF": "1", "PI_TASK_OWNER": "pi-line-cron"})   # never os.environ as is
    if not skills: env["PI_BIN"] = f"{PI_CODE}/bin/pi-noskills.sh"
    cmd = sandbox_prefix() + (["nice", "-n", "10"] if nice else []) + [os.path.expanduser("~/tools/pi-call/pi-auto"), tier, cwd, prompt, "--session", session,
           "--owner", "pi-line-cron", "--timeout", str(timeout)]
    if not write: cmd.append("--no-write")
    if not web: cmd.append("--no-web")
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout + 120)
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
