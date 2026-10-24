#!/usr/bin/env python3
"""Pi line work root (10-24, plan /workspace/plans/2026-10-24-pi-line-merge-cleanup.md §4). Everything a bot job builds and throws
away lives under PI_WORK (/workspace/pi-work; PI_WORK_ROOT overrides it, tests only), never in a person's checkout:
  clones/<repo>/     the bot's own clones (fetch only: pushes go through outbound's own temp clones); main checkout left empty
  wt/<kind>/<name>/  worktrees, each with .pi-owned.json -- the only thing janitor trusts: a directory without it is never deleted
  cache/nm/<hash>/   node_modules by package-lock hash (opsfix links it into its worktrees)
  tmp/               scratch dirs (mkdtemp), outbound's temp clones
  .trash/            reaped worktrees: a rename takes them away at once, rm -rf runs in the background
  locks/             <repo>.repo.lock (clone metadata: fetch, worktree add / prune, branch -D), janitor.lock
Not backed up (grokbot-box sync excludes it). Tickets, evidence and logs stay in PI_STATE/out."""
import contextlib, fcntl, json, os, shutil, subprocess, time
from lib import PI_CODE, PI_WORK, now_cst

ROOT = os.path.abspath(os.environ.get("PI_WORK_ROOT") or PI_WORK)
MARK = ".pi-owned.json"
REPOS = {   # same partial-clone filter as the person's clone (blobs over 1 MB come on demand)
    "mivo": {"url": "https://github.com/xindong/mivo-canvas-plugin.git", "filter": "blob:limit=1048576"},
    "panoptes": {"url": "https://github.com/PraiseZhu/panoptes.git", "filter": None},
}
class Busy(Exception): """a lock is held by someone else: try again next round"""

def path(*p): return os.path.join(ROOT, *p)
def clone_dir(repo): return path("clones", repo)
def wt_root(kind): return path("wt", kind)
def tmp_root(): return path("tmp")
def trash_root(): return path(".trash")
def nm_cache(h): return path("cache", "nm", h)
def lock_file(name): return path("locks", name)
def under(p, root=None):
    """p's real path is inside root (default ROOT) -- symlinks resolved, so a link pointing out never counts as inside."""
    r = os.path.realpath(root or ROOT); return os.path.realpath(p).startswith(r + os.sep)

def _git(args, cwd=None, t=900):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=t, stdin=subprocess.DEVNULL)
    return r.returncode, (r.stdout + r.stderr)

# ---------------------------------------------------------------- locks
@contextlib.contextmanager
def flock(p, timeout=None):
    """exclusive flock on file p; timeout None = wait, else Busy after timeout seconds (0 = one try)."""
    os.makedirs(os.path.dirname(p), exist_ok=True)
    f = open(p, "a"); t0 = time.time()
    while True:
        try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); break
        except BlockingIOError:
            if timeout is not None and time.time() - t0 >= timeout: f.close(); raise Busy(p)
            time.sleep(0.2)
    try: yield f
    finally:
        try: fcntl.flock(f, fcntl.LOCK_UN)
        finally: f.close()
def repo_lock(repo, timeout=None): return flock(lock_file(f"{repo}.repo.lock"), timeout)
def lock_free(p):
    """nobody holds flock on p (a missing file is free; p is never created)."""
    if not p or not os.path.exists(p): return True
    try: f = open(p, "a")
    except OSError: return False
    try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); fcntl.flock(f, fcntl.LOCK_UN); return True
    except BlockingIOError: return False
    finally: f.close()
def proc_start(pid):
    """start time of pid (clock ticks since boot, /proc/<pid>/stat field 22) or None: with the pid it names one process for good."""
    try:
        with open(f"/proc/{int(pid)}/stat") as f: return int(f.read().rsplit(")", 1)[1].split()[19])
    except (OSError, ValueError, IndexError, TypeError): return None
def pid_alive(pid, start=None):
    """pid runs -- and, given its recorded start time, is still that same process (a reused pid is not "alive")."""
    try: pid = int(pid)
    except (TypeError, ValueError): return False
    if pid <= 0: return False
    try: os.kill(pid, 0)
    except ProcessLookupError: return False
    except PermissionError: pass
    return start is None or proc_start(pid) == start

# ---------------------------------------------------------------- identity marker
def enforce():
    """conf/janitor.json "enforce" (missing / unreadable = false). Until janitor enforces, jobs keep what they kept before."""
    try:
        with open(os.environ.get("JANITOR_CONF") or os.path.join(PI_CODE, "conf", "janitor.json")) as f: return json.load(f).get("enforce") is True
    except (OSError, ValueError, AttributeError): return False

def _hide_marker(wd):
    """the marker must never show up in git status (prfix / infrafix commit every new file): info/exclude of the repo wd belongs to"""
    rc, o = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=wd, t=60)
    if rc: return
    ex = os.path.join(o.strip(), "info", "exclude")
    try: cur = open(ex).read() if os.path.exists(ex) else ""
    except OSError: return
    if f"/{MARK}\n" not in cur:
        os.makedirs(os.path.dirname(ex), exist_ok=True)
        with open(ex, "a") as f: f.write(f"\n/{MARK}\n/{MARK}.tmp-*\n")

def mark(wd, kind, **kw):
    """write wd/.pi-owned.json (atomic). kind: prfix / revise / infrafix / opsfix / dry; useful keys: repo, ticket, branch,
    base_sha, lock (the ticket lock file the job holds while it works), pr. pid = this process unless given."""
    m = {"schema": 1, "owner": "pi-line", "kind": kind, "pid": os.getpid(), "created_at": now_cst().isoformat(timespec="seconds"), **kw}
    m["pid_start"] = proc_start(m["pid"])
    _hide_marker(wd)
    tmp = os.path.join(wd, f"{MARK}.tmp-{os.getpid()}")
    with open(tmp, "w") as f: json.dump(m, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(wd, MARK)); return m
def marker(wd):
    """the marker of wd, or None: missing, a symlink, unreadable or not ours (owner != pi-line, schema != 1)."""
    p = os.path.join(wd, MARK)
    try:
        if os.path.islink(p) or not os.path.isfile(p): return None
        with open(p) as f: m = json.load(f)
    except (OSError, ValueError): return None
    return m if isinstance(m, dict) and m.get("owner") == "pi-line" and m.get("schema") == 1 else None
def retake(wd):
    """a job reusing a worktree (opsfix keeps one per sha) makes it its own again: pid = this process."""
    m = marker(wd)
    if m is not None: m["pid"] = os.getpid(); mark(wd, **{k: v for k, v in m.items() if k not in ("schema", "owner", "created_at", "pid_start")})

# ---------------------------------------------------------------- the bot's own clones
def ensure_clone(repo):
    """clones/<repo>, cloned once (no checkout: only worktrees are used); its info/exclude hides the marker from git status."""
    d = clone_dir(repo)
    if os.path.exists(os.path.join(d, ".git")): return d
    with repo_lock(repo, timeout=900):
        if os.path.exists(os.path.join(d, ".git")): return d
        src = REPOS[repo]; tmp = f"{d}.tmp-{os.getpid()}"
        shutil.rmtree(tmp, ignore_errors=True); os.makedirs(os.path.dirname(d), exist_ok=True)
        rc, o = _git(["clone", "-q", "--no-checkout", *([f"--filter={src['filter']}"] if src["filter"] else []), src["url"], tmp], t=1800)
        if rc: shutil.rmtree(tmp, ignore_errors=True); raise RuntimeError(f"clone {repo} rc={rc}: {o.strip()[-300:]}")
        with open(os.path.join(tmp, ".git", "info", "exclude"), "a") as f: f.write(f"\n/{MARK}\n/{MARK}.tmp-*\n")
        os.rename(tmp, d)
    return d

# ---------------------------------------------------------------- scratch + disposal
def mkdtemp(prefix):
    import tempfile
    os.makedirs(tmp_root(), exist_ok=True); return tempfile.mkdtemp(prefix=prefix, dir=tmp_root())
def purge_bg(p):
    """rm -rf p in the background at the lowest priority, detached from this process (it may exit first)."""
    subprocess.Popen(["nice", "-n", "19", "ionice", "-c3", "rm", "-rf", "--one-file-system", "--", p], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
def trash(wd, bg=True):
    """take wd away at once: rename into .trash/ (same disk), then delete it in the background. A symlink is unlinked, never
    followed; a directory outside ROOT (tests, a legacy root) is deleted in place, as before. -> where it went, or None."""
    if not os.path.lexists(wd): return None
    if os.path.islink(wd): os.unlink(wd); return None
    wd = os.path.abspath(wd)
    if not under(wd): shutil.rmtree(wd, ignore_errors=True); return None
    os.makedirs(trash_root(), exist_ok=True)
    dst = os.path.join(trash_root(), f"{os.path.basename(wd)}-{int(time.time() * 1000)}")
    try: os.rename(wd, dst)
    except OSError: shutil.rmtree(wd, ignore_errors=True); return None   # cannot rename: delete in place
    if bg: purge_bg(dst)
    return dst
