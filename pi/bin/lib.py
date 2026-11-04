import json, os, subprocess, sys, glob, datetime as dt, re, shutil, tempfile, time
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

# 11-03 (prwatch:error:drafts 10:44): one GitHub 5xx / network blip failed a read and the cause was lost -- the full command came
# first and prwatch cuts errors at 200 chars, so only 'Post "https://api.github.com/g' was left. Now the real stderr comes first
# (command after it, shortened), and a READ that failed with a 5xx / timeout / dropped connection is retried once. Writes never retry.
GH_TRANSIENT_RE = re.compile(r"HTTP 5\d\d|\b50[0234]\b|timed? ?out|deadline exceeded|\bEOF\b|connection (?:reset|refused)|"
                             r"TLS handshake|i/o timeout|stream error|server error", re.I)
GH_READS = {("pr", "list"), ("pr", "view"), ("pr", "checks"), ("pr", "diff"), ("issue", "list"), ("issue", "view"),
            ("repo", "view"), ("run", "list"), ("run", "view"), ("label", "list")}
GH_WRITE_FLAGS = {"-X", "--method", "-f", "-F", "--field", "--raw-field", "--input"}
def gh_is_read(args):
    if tuple(args[:2]) in GH_READS: return True
    return bool(args) and args[0] == "api" and not any(a.split("=", 1)[0] in GH_WRITE_FLAGS for a in args[1:])
# 11-04 (pi-work/ratelimit-1104/FINDINGS.md): PraiseZhu's GraphQL quota ran out and every job kept sending doomed gh calls each
# minute (a rate-limit error matched nothing above: no backoff). Now a primary or secondary rate limit records its reset time in
# GH_RL_FILE; until then gh() raises GhRateLimited WITHOUT calling GitHub, jobs skip their GitHub steps (one log line, never an
# error/streak) and watchdog reports one gh:ratelimit item once skipping has lasted > GH_RL_ALERT_H. The next success removes the file.
from time import time as _epoch   # not lib.time.time: tests stub lib.time with a sleep-only object
GH_RL_RE = re.compile(r"rate limit|HTTP 429|status(?: code)?:? 429|\b429 Too Many Requests|abuse detection|retry-after", re.I)
GH_RL_FILE = f"{PI_STATE}/gh-ratelimit.json"
GH_RL_ALERT_H = 2
GH_RL_MAX_S = 2 * 3600   # a recorded reset is never further away than this (a bogus Retry-After / reset must not wedge every job)
class GhRateLimited(RuntimeError): pass
def gh_rl_state(f=None):
    """the state dict; a missing file is {}, a corrupt one (not a dict / non-numeric until or since) is ignored with one log line"""
    GH_RL_FILE_ = f or GH_RL_FILE
    try: d = json.load(open(GH_RL_FILE_))
    except FileNotFoundError: return {}
    except (OSError, ValueError) as e: log(f"{GH_RL_FILE_} unreadable ({e.__class__.__name__}), ignored"); return {}
    num = lambda v: v is None or (isinstance(v, (int, float)) and not isinstance(v, bool))
    if not isinstance(d, dict) or not num(d.get("until")) or not num(d.get("since")): log(f"{GH_RL_FILE_} malformed, ignored"); return {}
    return d
def gh_rl_until(now=None, f=None):
    """epoch the GitHub calls may resume, or 0 when not rate-limited. Writes cap until at now+GH_RL_MAX_S, so a stored until further
    out is bogus: ignored (one log line), never clamped on read -- a read clamp would roll forward to now+2h forever"""
    now = now or _epoch(); u = gh_rl_state(f).get("until") or 0
    if u > now + GH_RL_MAX_S + 60: log(f"{f or GH_RL_FILE} until is > {GH_RL_MAX_S}s away, ignored"); return 0
    return u if u > now else 0
def _gh_rl_reset(err):
    """epoch from Retry-After / X-RateLimit-Reset in err, else from `gh api rate_limit` (free, not counted), else a short default"""
    now = _epoch()
    m = re.search(r"retry-after:?\s*(\d+)", err, re.I)
    if m: return now + int(m.group(1))
    m = re.search(r"x-ratelimit-reset:?\s*(\d{9,})", err, re.I)
    if m: return float(m.group(1))
    if re.search(r"secondary|abuse", err, re.I): return now + 60
    try:
        r = subprocess.run(["gh", "api", "rate_limit"], capture_output=True, text=True, timeout=30)
        res = json.loads(r.stdout).get("resources") or {}
        ts = [v.get("reset") or 0 for v in res.values() if isinstance(v, dict) and v.get("remaining") == 0]
        if ts and max(ts) > now: return float(max(ts))
    except Exception: pass
    return now + 900
def gh_rl_record(err, f=None):
    """err is a GitHub rate limit -> record the reset in GH_RL_FILE (keeping 'since' of an ongoing streak) and return True"""
    if not GH_RL_RE.search(err or ""): return False
    old = gh_rl_state(f); until = min(_gh_rl_reset(err), _epoch() + GH_RL_MAX_S)
    write_atomic(f or GH_RL_FILE, json.dumps({"since": old.get("since") or _epoch(), "until": until, "error": err[:200]}))
    return True
def gh_rl_clear(f=None):
    try: os.remove(f or GH_RL_FILE)
    except FileNotFoundError: pass

# 11-04 (pi-work/gh-app-1104 P1): GitHub App "Praise QA" for PraiseZhu/panoptes READS, behind the flag file $PI_STATE/gh-app-enabled
# (read at call time). Flag absent, a repo without an App route, an identity-dependent call (@me, /user) or a token that cannot be
# minted -> the default gh login (PAT), exactly as before. The App has its own quota, so its rate-limit marker is a separate file.
GH_APP_REPOS = {"PraiseZhu/panoptes"}      # + every PraiseZhu/* repo (task B: installation 164262283 covers the owner), gh_app_own_repo()
def gh_app_own_repo(repo): return bool(repo) and (repo in GH_APP_REPOS or (repo.count("/") == 1 and repo.split("/")[0] == "PraiseZhu"))
def gh_app_flag(): return f"{PI_STATE}/gh-app-enabled"
def gh_app_rl_file(): return f"{PI_STATE}/gh-ratelimit-praise-qa.json"
def gh_target_repo(args):
    """OWNER/REPO a gh call targets: -R/--repo X, `repo view|clone X`, or `api [/]repos/OWNER/REPO/...`; None if unknown"""
    a = list(args)
    for i, x in enumerate(a):
        if x in ("-R", "--repo") and i + 1 < len(a): return a[i + 1]
        if x.startswith("--repo="): return x.split("=", 1)[1]
    if a[:1] == ["repo"] and len(a) > 2 and a[1] in ("view", "clone") and "/" in a[2]: return a[2]
    if a[:1] == ["api"]:
        for x in a[1:]:
            m = re.match(r"/?repos/([^/\s]+/[^/?\s]+)", x)
            if m: return m.group(1)
    return None
# P3 (11-04, PM 17:00): product-repo READS too, while $PI_STATE/gh-app-product-enabled also exists. Product WRITES stay on the PAT:
# outbound/prfix dedupe their fix PRs with author:@me and gate pushes on author == PraiseZhu, so an App-authored product PR would be
# invisible to them (duplicate PRs). Those gates move first, then writes.
GH_APP_PRODUCT_REPOS = {"xindong/mivo-canvas-plugin"}
def gh_app_product_flag(): return f"{PI_STATE}/gh-app-product-enabled"
def gh_app_wanted(args):
    """True when this call should use the App token (see above and task B below)"""
    repo = gh_target_repo(args)
    if not os.path.exists(gh_app_flag()): return False
    if any("@me" in x or re.match(r"/?user(?:$|/)", x) for x in args[1:]): return False   # identity-dependent (also inside --search): PAT
    if repo in GH_APP_PRODUCT_REPOS:
        if gh_is_read(args): return os.path.exists(gh_app_product_flag()) or gh_component_on()
        return gh_app_product_writes_on()
    if not gh_app_own_repo(repo): return False
    return gh_is_read(args) or gh_app_writes_ok(args) or gh_component_on()
# 11-04 task B: every remaining caller behind its own flag (each also needs gh-app-enabled); rm the flag -> that caller is back on the PAT.
#   gh-app-product-writes-enabled  outbound/prfix product writes: gh pr create/comment/ready, issue writes, fix-branch clone + git push
#   gh-app-merger-enabled / gh-app-mergewatch-enabled / gh-app-nightly-enabled / gh-app-ledgers-enabled  that job's gh calls (the job
#   names itself with gh_component(); node/shell jobs get GH_TOKEN from `ghtoken.py env-for <component> <repo>`)
GH_APP_COMPONENTS = ("merger", "mergewatch", "nightly", "ledgers")
_COMPONENT = [os.environ.get("PI_GH_COMPONENT") or None]
def gh_component(name):
    assert name in GH_APP_COMPONENTS, name
    _COMPONENT[0] = name
def gh_component_flag(name): return f"{PI_STATE}/gh-app-{name}-enabled"
def gh_component_on(name=None):
    c = name or _COMPONENT[0]
    return bool(c) and os.path.exists(gh_app_flag()) and os.path.exists(gh_component_flag(c))
def gh_component_node_env(name, repo=REPO):
    """node/shell jobs (ledgers -> build-ledger.mjs / track-followups.mjs, nightly -> argus): {PRAISE_QA_GH_TOKEN, PRAISE_QA_GH_REPOS}
    while that job's flag is on and the token mints, else {} (= the PAT). Those scripts use the token ONLY for calls whose target repo
    is PRAISE_QA_GH_REPOS; every other repo (handoff repos, pr-qa-agent-deploy) keeps the inherited login."""
    if not gh_component_on(name): return {}
    try:
        import ghtoken
        return {"PRAISE_QA_GH_TOKEN": ghtoken.token(repo), "PRAISE_QA_GH_REPOS": repo}
    except Exception as e:
        try: import ghtoken as _g; msg = _g.mask(e)
        except Exception: msg = e.__class__.__name__
        log(f"{name}: gh app token unavailable ({str(msg)[:160]}); node tools stay on the default gh login"); return {}
def gh_app_product_writes_flag(): return f"{PI_STATE}/gh-app-product-writes-enabled"
def gh_app_product_writes_on(): return os.path.exists(gh_app_flag()) and os.path.exists(gh_app_product_writes_flag())
# Red lines (Praise, unchanged): only Praise merges product PRs, and no bot edits a product PR body. No code path does either, on ANY
# identity: gh() refuses these before a token is chosen.
class RedLine(RuntimeError): pass
def gh_red_line(args):
    """-> reason if args would merge a product PR or edit a product PR body/title, else None"""
    if gh_target_repo(args) not in GH_APP_PRODUCT_REPOS: return None
    a = list(args)
    if a[:2] == ["pr", "merge"]: return "merging a product PR (only Praise merges)"
    if a[:2] == ["pr", "edit"] and any(x.split("=", 1)[0] in ("--body", "-b", "--body-file", "-F", "--title", "-t") for x in a[2:]):
        return "editing a product PR body"
    if a[:1] == ["api"]:
        path = next((x for x in a[1:] if re.match(r"/?repos/", x)), "")
        meth = next((a[i + 1].upper() for i, x in enumerate(a) if x in ("-X", "--method") and i + 1 < len(a)), "")
        if re.search(r"/pulls/\d+/merge$", path) and meth in ("PUT", ""): return "merging a product PR (only Praise merges)"
        if re.search(r"/pulls/\d+$", path) and meth == "PATCH": return "editing a product PR body"
    return None
# Explicit identities (task B 4: no @me / `api user` anywhere). Our own PRs are authored by PraiseZhu (PAT, older) or by the App.
PAT_LOGIN = "PraiseZhu"
SELF_LOGINS = frozenset({PAT_LOGIN, "praise-qa[bot]", "app/praise-qa", "praise-qa"})   # REST login / gh --json author.login / slug
SELF_AUTHORS = (PAT_LOGIN, "app/praise-qa")                                           # gh --author / search author: values
def is_self(login):
    if isinstance(login, dict): login = login.get("login")
    return (login or "") in SELF_LOGINS
def self_pr_list(repo, state, fields, search=None, limit=100, extra=(), run=None):
    """our own PRs = union over SELF_AUTHORS (deduped by number): a dedupe never misses an old PraiseZhu PR nor a new App PR"""
    seen = {}
    for au in SELF_AUTHORS:
        q = [f"{search} author:{au}"] if search else []
        args = ["pr", "list", "-R", repo, "--state", state, "--limit", str(limit), "--json", fields] + list(extra)
        args += (["--search", q[0]] if q else ["--author", au])
        for p in (run or gh)(args) or []: seen.setdefault(p.get("number"), p)   # run: the caller's gh (tests stub module.gh)
    return sorted(seen.values(), key=lambda p: -(p.get("number") or 0))
# P2 (11-04): panoptes WRITES (PR create / merge / comment / branch delete, git push of infra branches) use the App too while BOTH
# $PI_STATE/gh-app-enabled and $PI_STATE/gh-app-writes-enabled exist. rm gh-app-writes-enabled -> writes back on the PAT (reads stay).
APP_LOGIN = "praise-qa[bot]"; APP_AUTHOR = "app/praise-qa"   # how the App shows up as a PR author (gh --author app/<slug>)
def gh_app_writes_flag(): return f"{PI_STATE}/gh-app-writes-enabled"
def gh_app_writes_ok(args): return os.path.exists(gh_app_writes_flag())
def gh_app_writes_on(): return os.path.exists(gh_app_flag()) and os.path.exists(gh_app_writes_flag())
def git_push_env(repo):
    """-> (git -c args, env) for a git push to repo: the App token through gh's credential helper while the write flags are on,
    else ([], None) = the box's default credentials (PAT) exactly as before"""
    ident, env = gh_env(["repo", "clone", repo])   # "repo clone" is not a read: needs the (product-)writes flag
    if ident != "app": return [], None
    return ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"], env
def gh_env(args):
    """-> (identity "app"|"pat", env for subprocess or None = inherit). Minting failures fall back to the PAT with one masked line."""
    if not gh_app_wanted(args): return "pat", None
    try:
        import ghtoken
        tok = ghtoken.token(gh_target_repo(args))
    except Exception as e:
        try: import ghtoken as _g; msg = _g.mask(e)
        except Exception: msg = e.__class__.__name__
        log(f"gh app token unavailable ({str(msg)[:160]}); using the default gh login for this call"); return "pat", None
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN")}
    env["GH_TOKEN"] = tok
    return "app", env
def gh_skip(job):
    """True (and one log line) when GitHub is rate-limited: the caller skips its GitHub steps this run"""
    u = gh_rl_until()
    if u: log(f"{job}: GitHub rate-limited until {dt.datetime.fromtimestamp(u, CST).strftime('%H:%M:%S')} CST, skipping GitHub steps")
    return bool(u)
def gh(args, js=True, retries=1):
    cmd = f"gh {' '.join(args[:2])}"
    why = gh_red_line(args)
    if why: raise RedLine(f"{cmd}: refused, red line: {why}")
    ident, env = gh_env(args)
    rlf = gh_app_rl_file() if ident == "app" else None   # None = GH_RL_FILE (the PAT's)
    u = gh_rl_until(f=rlf)
    if u: raise GhRateLimited(f"{cmd}: GitHub rate-limited until {dt.datetime.fromtimestamp(u, CST).strftime('%H:%M:%S')} CST (not sent)")
    for left in range(retries if gh_is_read(args) else 0, -1, -1):
        try: r = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=120, env=env)
        except subprocess.TimeoutExpired:
            if left: print(f"{cmd}: timeout after 120s, retrying once", file=sys.stderr, flush=True); continue
            raise RuntimeError(f"{cmd}: timeout after 120s [{' '.join(args)[:120]}]")
        if r.returncode == 0:
            if os.path.exists(rlf or GH_RL_FILE): gh_rl_clear(rlf)
            return json.loads(r.stdout) if js else r.stdout
        err = (r.stderr or "").strip()
        if ident == "app" and re.search(r"HTTP 401|Bad credentials", err):
            try: import ghtoken; ghtoken.invalidate(gh_target_repo(args))
            except Exception: pass
        if gh_rl_record(err, rlf): raise GhRateLimited(f"{cmd}: {err[:300]} [{' '.join(args)[:120]}]")
        if left and GH_TRANSIENT_RE.search(err): print(f"{cmd}: {err[:200]} -- retrying once", file=sys.stderr, flush=True); time.sleep(2); continue
        raise RuntimeError(f"{cmd}: {err[:300]} [{' '.join(args)[:120]}]")

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
    # anything else -- empty, garbage, or a naive ISO time without offset (ambiguous zone) -- falls back to mtime + CLAIM_TTL_H
    # (fail closed: a naive claim that "expired" an hour ago may still block until mtime+4h). Write an offset or unix seconds.
    return dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc) + dt.timedelta(hours=CLAIM_TTL_H)
def branch_claim(n, now=None):
    """reason string while takeover/branch-claims/<n> holds a valid claim, else None."""
    e = claim_expiry(f"{TAKEOVER}/branch-claims/{int(n)}")
    now = now or dt.datetime.now(dt.timezone.utc)
    return f"takeover/branch-claims/{int(n)} 有效到 {e.isoformat(timespec='seconds')}：别的 worker 认领了这个 PR，不碰" if e and e > now else None
# 11-03 B rollback: touch PI_STATE/pmnudge-b-off (re-read every run) -> pmnudge exempts the bot's own fix PRs again and skips the
# dirty / blocked / main-CI / kiro rules; prwatch + prfix --revise treat a red / readied / re-run head that turns DIRTY as handled.
B_OFF = f"{BASE}/pmnudge-b-off"
def b_on(): return not os.path.exists(B_OFF)
# Ownership: a commit on the PR branch in the last OWNER_WINDOW_H hours WITHOUT a Pi trailer (Pi-Open: first commit of a Pi fix PR,
# Pi-Revise: a --revise fix; Fingerprint alone does not count, a human may keep it) = someone else is working on it.
OWNER_WINDOW_H = 6
# Only the final paragraph (git trailer block) counts, and only key-shaped values Pi's own builders write
# (prfix fix_one: fixpr:<qa>:<fp>; prfix/outbound revise: revise:<pr>:<head12>[:merge]); a body line "Pi-Revise: x" is foreign.
PI_MARK_RE = re.compile(r"^(Pi-Revise: revise:\d+:[0-9a-f]{12}(:merge)?|Pi-Open: fixpr:\d+:\S+)$", re.M)
def has_pi_trailer(msg):
    paras = [p for p in re.split(r"\n\s*\n", (msg or "").strip()) if p.strip()]
    return len(paras) > 1 and bool(PI_MARK_RE.search(paras[-1]))
def foreign_recent_commits(commits, now=None, hours=OWNER_WINDOW_H):
    """commits: GitHub pulls/N/commits items -> short shas of recent commits without a PI_MARK_RE trailer."""
    now = now or dt.datetime.now(dt.timezone.utc); out = []
    for c in commits or []:
        cm = c.get("commit") or {}
        when = (cm.get("committer") or {}).get("date") or (cm.get("author") or {}).get("date")
        try: t = dt.datetime.fromisoformat(str(when).replace("Z", "+00:00"))
        except ValueError: t = now   # unreadable date: count it as recent (fail closed)
        if t.tzinfo is None: t = now   # naive date (no zone): count it as recent (fail closed)
        if now - t <= dt.timedelta(hours=hours) and not has_pi_trailer(cm.get("message")): out.append((c.get("sha") or "?")[:9])
    return out
def ownership_block(n, commits, now=None):
    """reason not to push onto PR n (claim, or recent foreign commits), else None."""
    why = branch_claim(n, now)
    if why: return why
    f = foreign_recent_commits(commits, now)
    return f"PR 分支最近 {OWNER_WINDOW_H} 小时有不带 Pi-Open / Pi-Revise 标记的提交 {f[:5]}：别人在改，不推送" if f else None
def pr_commits_args(n): return ["api", "--paginate", "--slurp", f"repos/{REPO}/pulls/{int(n)}/commits?per_page=100"]
def flatten_pages(x):
    """`gh api --paginate --slurp` -> [[page1...], [page2...]]: one flat list (an already flat list passes through)."""
    return [i for p in x for i in p] if isinstance(x, list) and x and all(isinstance(p, list) for p in x) else (x or [])
# Gate files: a planned change touching these is a hard stop; --allow-sensitive does NOT override it.
PROTECTED_RE = re.compile(r"(^|/)__tests__/(.*/)?[^/]*(Guard|Policy)[^/]*$|^\.githooks/|^scripts/verify", re.I)
def protected_files(files): return sorted(f for f in files if PROTECTED_RE.search(f.replace("\\", "/")))
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
