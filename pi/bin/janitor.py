#!/usr/bin/env python3
"""janitor -- the Pi line's one reconciler for PI_WORK (10-24, plan /workspace/plans/2026-10-24-pi-line-merge-cleanup.md §4.4).
  janitor.py [run] [--dry-run] [--aggressive] [--event ticket-done|pr-closed] [--ticket ID] [--pr REPO#N]
  janitor.py reap PATH [--reason TEXT]
  janitor.py status [--json]
Cron */10 through run.sh (a light job: runs under memory pressure too); prwatch kicks `run --event pr-closed` when a bot PR closes.
It deletes only what it can prove is the bot's and finished (see workroot for the layout):
  wt/<kind>/<name> with a valid .pi-owned.json whose process is gone (pid + start time), whose ticket lock is free and whose
    ticket is not running -> salvage a dirty diff (<= 2 MB) into out/janitor/salvage/, rename into .trash/, git worktree prune and
    branch -D (bot branch names only) in the bot clone -- all under that clone's repo lock, decided again once the lock is held
  a directory under wt/ WITHOUT the marker (or a symlink): never deleted, listed in unmarked[] (watchdog reminds once)
  bot branches in the bot clones (fix/qa-<N>-<fp8>, fix/infra-<sig8>) no worktree uses; registrations whose directory is gone (prune)
  remote branches of CLOSED bot PRs (body carries the bot's marker): panoptes fix/infra-* (merged or not; GitHub deletes merged ones
    itself since Q2) and mivo qa-fix/* (closed unmerged), only when the remote tip == the PR head or is already in main; gh failing
    or a pushed-on branch -> kept, listed in remote_kept[] with the reason
  .trash/* (rm -rf, lowest priority), tmp/* and the legacy /tmp dirs (older than tmp_hours, nothing has them open), cache/nm/*
    (not the newest nm_keep, not linked from a worktree), evidence/nightly days (older than keep_days or over cap_gb, oldest first,
    never the newest keep_newest days) and step-results (stored more than keep_days ago) -- the last two hourly, or when aggressive
Hard limits: every path is resolved (symlinks never followed) and must be under PI_WORK or one of the configured data dirs.
Never a person's checkout, ~/.claude, ~/.config. Free space under free_gb_low -> aggressive (trash at once, nm_keep 1).
conf/janitor.json "enforce": false (or --dry-run) = every decision written, nothing changed -- except .trash/, whose entries a job
already threw away (purging them is what `git worktree remove` did before). Exit: 0 ok, 75 another run holds janitor.lock, 1 some
object failed (the rest still handled), 2 config / environment error. Output: out/janitor/last.json, decisions on stdout (run.sh
appends them to out/logs/janitor-<day>.log)."""
import argparse, json, os, re, shutil, subprocess, sys, time
from lib import *
import workroot as W
import rollback_hold

CONF = os.environ.get("JANITOR_CONF") or f"{PI_CODE}/conf/janitor.json"
OUT = os.environ.get("JANITOR_OUT") or f"{BASE}/out/janitor"
BOT_BRANCH = {"mivo": re.compile(r"^fix/qa-\d+-[0-9a-f]{8}$"), "panoptes": re.compile(r"^fix/infra-[0-9a-f]{8}$")}
REMOTE = {   # repo -> remote branches of bot PRs: name prefix, the bot's mark in the PR body, merged ones too?
    "mivo": {"gh": "xindong/mivo-canvas-plugin", "prefix": "qa-fix/", "mark": "<!-- pi-line ", "merged": False},
    "panoptes": {"gh": "PraiseZhu/panoptes", "prefix": "fix/infra-", "mark": "Infra-Sig: inf1-", "merged": True},
}
INFRA_TICKETS = os.environ.get("JANITOR_INFRA_TICKETS") or f"{BASE}/out/infrafix/tickets.json"
# infrafix keeps fix/infra-<sig8> (worktree gone) while its PR is in flight -- the same set infrafix itself checks; it drops the
# branch on merged / rejected. Janitor deletes such a branch only once the ticket has left this set (or no ticket names it).
INFRA_IN_FLIGHT = {"running", "pr_action_written", "pr_open", "merge_action_written", "awaiting_praise", "ci_red"}
DEFAULTS = {"enforce": False, "tmp_hours": 6, "nm_keep": 2, "salvage_max_bytes": 2 << 20, "free_gb_low": 15, "trash_grace_min": 10,
            "remote_days": 30, "legacy_tmp": [],  # old /tmp homes of opsfix/prfix, empty since 10-25; dropped after the 7-day window
            "evidence": {"dir": "/workspace/mivo-qa/argus-root/app/evidence/nightly", "keep_days": 7, "cap_gb": 3, "keep_newest": 2},
            "step_results": {"dir": "/workspace/mivo-qa/argus-root/cache/step-results", "keep_days": 14}}

class ConfError(Exception): pass
def load_conf():
    c = json.loads(json.dumps(DEFAULTS))
    if os.path.exists(CONF):
        try: user = json.load(open(CONF))
        except (OSError, ValueError) as e: raise ConfError(f"{CONF}: {e}")
        if not isinstance(user, dict) or not isinstance(user.get("enforce", False), bool): raise ConfError(f"{CONF}: enforce must be true/false")
        for k, v in user.items():
            if k.startswith("_"): continue
            c[k] = {**c[k], **v} if isinstance(c.get(k), dict) and isinstance(v, dict) else v
    return c

def sh(cmd, cwd=None, t=600):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=t, stdin=subprocess.DEVNULL)
    return r.returncode, (r.stdout + r.stderr).strip()
def git(*a, cwd=None, t=600): return sh(["git", *a], cwd=cwd, t=t)
def du(p):
    rc, o = sh(["du", "-sb", "--one-file-system", p], t=300)
    try: return int(o.split()[0]) if rc == 0 else 0
    except (ValueError, IndexError): return 0
def age_h(p):
    try: return (time.time() - os.lstat(p).st_mtime) / 3600
    except OSError: return 0
def open_paths():
    """every path a process has as cwd or open (from /proc): a directory something uses is never deleted."""
    s = set()
    for p in os.listdir("/proc"):
        if not p.isdigit(): continue
        for x in [f"/proc/{p}/cwd", *(f"/proc/{p}/fd/{fd}" for fd in _ls(f"/proc/{p}/fd"))]:
            try: s.add(os.readlink(x))
            except OSError: pass
    return s
def _ls(d):
    try: return os.listdir(d)
    except OSError: return []
def in_use(p, opened):
    p = os.path.realpath(p); return any(x == p or x.startswith(p + os.sep) for x in opened)

class Run:
    def __init__(self, conf, enforce, aggressive, event=None):
        self.c, self.enforce, self.aggr, self.event = conf, enforce, aggressive, event
        self.r = {"ran_at": now_cst().isoformat(timespec="seconds"), "mode": "enforce" if enforce else "dry-run", "event": event,
                  "aggressive": aggressive, "reaped": [], "skipped": [], "unmarked": [], "pruned": [], "branches_deleted": [],
                  "remote_deleted": [], "remote_kept": [], "deleted": [], "trash_purged": [], "errors": [], "long_running": []}
        self.opened = open_paths()
    def say(self, verb, what, why=""):
        print(f"{now_cst():%H:%M:%S} {'' if self.enforce else '[dry] '}{verb} {what}{('  -- ' + why) if why else ''}", flush=True)
    def err(self, what, e):
        self.r["errors"].append({"what": what, "error": str(e)[:300]}); self.say("ERROR", what, str(e)[:200])

    # ------------------------------------------------------------ worktrees
    def busy(self, wd, m):
        """-> why this marked worktree must stay, or None when it may go"""
        if W.pid_alive(m.get("pid"), m.get("pid_start")): return f"进程 {m.get('pid')} 还在跑"
        if m.get("lock") and not W.lock_free(m["lock"]): return f"单子锁被占（{os.path.basename(m['lock'])}）"
        tf, key = m.get("tickets"), m.get("ticket_key")
        if tf and key:
            try: t = (json.load(open(tf)).get("tickets") or {}).get(key) or {}
            except (OSError, ValueError): return "单子状态读不出来"
            if t.get("status") == "running" and W.pid_alive(t.get("pid")): return f"单子 {key} 正在运行（pid {t.get('pid')}）"
        if in_use(wd, self.opened): return "有进程正在用这个目录"
        return None
    def worktrees(self):
        for kind in sorted(_ls(W.path("wt"))):
            kd = W.path("wt", kind)
            if os.path.islink(kd) or not os.path.isdir(kd):
                self.r["unmarked"].append({"path": kd, "why": "wt/ 下不是普通目录"}); continue
            for name in sorted(_ls(kd)):
                wd = os.path.join(kd, name)
                if name.startswith(".pi-trash"): continue
                if os.path.islink(wd) or not os.path.isdir(wd):
                    self.r["unmarked"].append({"path": wd, "why": "符号链接或文件（不跟随、不删）"}); continue
                m = W.marker(wd)
                if m is None:
                    self.r["unmarked"].append({"path": wd, "why": "没有 .pi-owned.json"}); self.say("keep", wd, "没有标记，不删"); continue
                why = self.busy(wd, m)
                if why:
                    self.r["skipped"].append({"path": wd, "why": why, "age_h": round(age_h(os.path.join(wd, W.MARK)), 1)})
                    if age_h(os.path.join(wd, W.MARK)) > 4: self.r["long_running"].append({"path": wd, "hours": round(age_h(os.path.join(wd, W.MARK)), 1), "why": why})
                    continue
                self.reap(wd, m, "单子已结束（进程不在、锁空闲）")
    def reap(self, wd, m, reason):
        """§4.2: repo lock -> decide again -> salvage -> rename into .trash -> prune + branch -D. -> True when it went"""
        repo = m.get("repo"); clone = m.get("clone") or (W.clone_dir(repo) if repo in W.REPOS else None)
        if clone and not W.under(clone): clone = None          # a clone we do not own: never touch its metadata
        if not self.enforce:
            self.r["reaped"].append({"path": wd, "kind": m.get("kind"), "ticket": m.get("ticket"), "why": reason}); self.say("reap", wd, reason); return True
        try:
            with (W.repo_lock(repo, timeout=30) if repo in W.REPOS and clone else _null()):
                m2 = W.marker(wd)
                if m2 is None: return False                     # gone meanwhile (or no longer ours)
                why = self.busy(wd, m2)
                if why: self.r["skipped"].append({"path": wd, "why": why}); return False
                salv = self.salvage(wd, m2)
                dst = W.trash(wd)
                if clone and os.path.isdir(clone):
                    git("worktree", "prune", cwd=clone)
                    br = m2.get("branch")
                    if br and BOT_BRANCH.get(repo, re.compile("$^")).match(br) and git("rev-parse", "--verify", "-q", f"refs/heads/{br}", cwd=clone)[0] == 0:
                        rc, o = git("branch", "-D", br, cwd=clone)
                        if rc == 0: self.r["branches_deleted"].append({"repo": repo, "branch": br})
            self.r["reaped"].append({"path": wd, "kind": m.get("kind"), "ticket": m.get("ticket"), "why": reason, "salvage": salv, "trash": dst})
            self.say("reap", wd, reason)
            return True
        except W.Busy: self.r["skipped"].append({"path": wd, "why": "repo 锁 30 秒内拿不到，下一轮再清"}); return False
        except Exception as e: self.err(f"reap {wd}", e); return False
    def salvage(self, wd, m):
        """uncommitted work in a worktree that is going away: one patch (tracked changes + new files' names), <= salvage_max_bytes"""
        rc, st = git("status", "--porcelain", cwd=wd, t=120)
        if rc: return "git status 失败，没留底"
        lines = [l for l in st.splitlines() if not l[3:].startswith(W.MARK)]
        if not lines: return None
        _, d = git("diff", "HEAD", "--binary", cwd=wd, t=300)
        body = f"# {wd} ({m.get('kind')} {m.get('ticket')}) status:\n" + "\n".join(f"# {l}" for l in lines) + "\n" + (d or "")
        if len(body.encode()) > self.c["salvage_max_bytes"]: return "太大，没留底"
        os.makedirs(f"{OUT}/salvage", exist_ok=True)
        f = f"{OUT}/salvage/{m.get('kind')}-{os.path.basename(wd)}-{int(time.time())}.patch"
        write_atomic(f, body); return f
    def clones(self):
        """per bot clone (under its repo lock): prune registrations whose directory is gone; delete bot branches no worktree uses"""
        for repo in sorted(W.REPOS):
            d = W.clone_dir(repo)
            if not os.path.exists(os.path.join(d, ".git")): continue
            try:
                with W.repo_lock(repo, timeout=30):
                    rc, o = git("worktree", "list", "--porcelain", cwd=d)
                    if rc: raise RuntimeError(o[-200:])
                    used, gone = set(), []
                    for blk in o.split("\n\n"):
                        kv = dict(l.split(" ", 1) if " " in l else (l, "") for l in blk.splitlines())
                        if "worktree" not in kv or os.path.abspath(kv["worktree"]) == os.path.abspath(d): continue
                        if "branch" in kv: used.add(kv["branch"].removeprefix("refs/heads/"))
                        if not os.path.exists(kv["worktree"]) and "locked" not in kv: gone.append(kv["worktree"])
                    if gone:
                        self.r["pruned"] += [{"repo": repo, "path": g} for g in gone]; self.say("prune", f"{repo}: {len(gone)}", ", ".join(gone)[:200])
                        if self.enforce: git("worktree", "prune", cwd=d)
                    keep = self.infra_in_flight() if repo == "panoptes" else set()
                    _, o = git("for-each-ref", "--format=%(refname:short)", "refs/heads/", cwd=d)
                    for b in o.split():
                        if not BOT_BRANCH[repo].match(b) or b in used: continue
                        if keep is None or b.removeprefix("fix/infra-") in keep:
                            self.r["skipped"].append({"path": f"{repo}:{b}", "why": "infrafix 单子的 PR 还在路上（或单子读不出）"}); continue
                        self.say("branch -D", f"{repo} {b}", "bot 分支，没有工作树在用")
                        if self.enforce:
                            rc, e = git("branch", "-D", b, cwd=d)
                            if rc: self.err(f"branch -D {repo} {b}", e); continue
                        self.r["branches_deleted"].append({"repo": repo, "branch": b})
            except W.Busy: self.r["skipped"].append({"path": d, "why": "repo 锁 30 秒内拿不到"})
            except Exception as e: self.err(f"clone {repo}", e)

    def infra_in_flight(self):
        """sig8 of infrafix tickets whose PR is in flight; None = tickets unreadable (then no fix/infra-* branch is deleted)"""
        if not os.path.exists(INFRA_TICKETS): return set()
        try:
            with open(INFRA_TICKETS) as f: ts = json.load(f).get("tickets") or {}
        except (OSError, ValueError, AttributeError): return None
        return {k[5:13] for k, t in ts.items() if isinstance(t, dict) and t.get("status") in INFRA_IN_FLIGHT}

    # ------------------------------------------------------------ remote branches of closed bot PRs
    def remote(self):
        since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400 * self.c["remote_days"]))
        for repo, R in sorted(REMOTE.items()):
            try:
                prs = gh(["pr", "list", "-R", R["gh"], "--state", "closed", "--author", "@me", "--limit", "100", "--search", f"closed:>={since}",
                          "--json", "number,headRefName,headRefOid,mergedAt,body"])
            except Exception as e: self.r["remote_kept"].append({"repo": repo, "why": f"gh 查不到 PR 列表，远端一律不删：{str(e)[:120]}"}); continue
            for p in prs or []:
                br, head = p.get("headRefName") or "", p.get("headRefOid") or ""
                if not br.startswith(R["prefix"]) or R["mark"] not in (p.get("body") or ""): continue
                if p.get("mergedAt") and not R["merged"]: continue
                k = {"repo": repo, "branch": br, "pr": p.get("number")}
                try:
                    try: tip = gh(["api", f"repos/{R['gh']}/git/ref/heads/{br}", "--jq", ".object.sha"], js=False).strip()
                    except Exception as e:
                        if "404" in str(e) or "Not Found" in str(e): continue          # already gone
                        raise
                    if tip != head:
                        st = gh(["api", f"repos/{R['gh']}/compare/main...{tip}", "--jq", ".status"], js=False).strip()
                        if st not in ("identical", "behind"):
                            self.r["remote_kept"].append({**k, "why": f"远端分支最新提交 {tip[:9]} 不是 PR 的 head，也不在 main 里（被推进过？）"}); continue
                    if rollback_hold.active():   # 10-28：疑似回滚期间不对 GitHub 做直接写，标记删掉后下一轮再删
                        self.r["remote_kept"].append({**k, "why": "ROLLBACK-SUSPECTED 存在，暂不删远端分支"}); continue
                    self.say("delete remote", f"{R['gh']} {br}", f"PR #{p.get('number')} 已{'合并' if p.get('mergedAt') else '关闭'}，远端 = PR head 或已在 main")
                    if self.enforce: gh(["api", "-X", "DELETE", f"repos/{R['gh']}/git/refs/heads/{br}"], js=False)
                    self.r["remote_deleted"].append({**k, "tip": tip})
                except Exception as e: self.r["remote_kept"].append({**k, "why": f"gh 出错，保留：{str(e)[:120]}"})

    # ------------------------------------------------------------ trash, scratch, caches, data
    def remove(self, p, why, root):
        """delete one entry after the hard-limit check (real path under root, not a symlink target)"""
        if not W.under(p, root): self.err(f"refuse {p}", f"不在 {root} 下"); return
        self.r["deleted"].append({"path": p, "why": why}); self.say("rm", p, why)
        if self.enforce:
            if os.path.islink(p) or not os.path.isdir(p): os.unlink(p)
            else: sh(["nice", "-n", "19", "ionice", "-c3", "rm", "-rf", "--one-file-system", "--", p], t=3600)
    def trash(self):
        t = W.trash_root(); grace = 0 if self.aggr else self.c["trash_grace_min"] * 60
        for name in sorted(_ls(t)):
            p = os.path.join(t, name)
            if time.time() - os.lstat(p).st_ctime < grace: continue    # a job's own background rm is probably still on it
            self.r["trash_purged"].append(p); self.say("purge", p)
            sh(["nice", "-n", "19", "ionice", "-c3", "rm", "-rf", "--one-file-system", "--", p], t=3600)   # always: already thrown away
    def tmp(self):
        for root in [W.tmp_root(), *self.c["legacy_tmp"]]:
            for name in sorted(_ls(root)):
                p = os.path.join(root, name)
                if age_h(p) < self.c["tmp_hours"]: continue
                if in_use(p, self.opened): self.r["skipped"].append({"path": p, "why": "有进程正在用"}); continue
                if os.path.exists(os.path.join(p, ".git")): self.r["skipped"].append({"path": p, "why": "是 git 工作树（旧位置，不在这里删）"}); continue
                self.remove(p, f"临时目录，超过 {self.c['tmp_hours']} 小时没动", root)
    def nm(self):
        root = W.path("cache", "nm"); keep = 1 if self.aggr else self.c["nm_keep"]
        linked = set()
        for kind in _ls(W.path("wt")):
            for name in _ls(W.path("wt", kind)):
                l = W.path("wt", kind, name, "node_modules")
                if os.path.islink(l): linked.add(os.path.realpath(l))
        hs = sorted(_ls(root), key=lambda h: os.lstat(os.path.join(root, h)).st_mtime, reverse=True)
        for i, h in enumerate(hs):
            p = os.path.join(root, h)
            if i < keep or os.path.realpath(os.path.join(p, "node_modules")) in linked or in_use(p, self.opened): continue
            self.remove(p, "依赖缓存：不是最新的几份，也没有工作树在用", root)
    def data(self):
        e = self.c["evidence"]; d = e["dir"]
        days = sorted(x for x in _ls(d) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", x))
        protect = set(days[-e["keep_newest"]:]) | {now_cst().strftime("%Y-%m-%d")}
        cutoff = (now_cst() - dt.timedelta(days=e["keep_days"])).strftime("%Y-%m-%d")
        sizes = {x: du(os.path.join(d, x)) for x in days}; total = sum(sizes.values())
        cap = (e["cap_gb"] / 2 if self.aggr else e["cap_gb"]) * (1 << 30)
        for x in days:
            if x in protect: continue
            if x < cutoff: why = f"超过 {e['keep_days']} 天"
            elif total > cap: why = f"总量 {total / (1 << 30):.1f}G 超过上限 {cap / (1 << 30):.1f}G，先删最老的"
            else: continue
            self.remove(os.path.join(d, x), f"evidence {why}", d); total -= sizes[x]
        s = self.c["step_results"]; d = s["dir"]; n = 0
        for name in _ls(d):
            p = os.path.join(d, name)
            if name.endswith(".json") and age_h(p) > (s["keep_days"] + 1) * 24:
                if self.enforce: os.unlink(p)
                n += 1
        if n: self.r["deleted"].append({"path": d, "why": f"step-results 存了超过 {s['keep_days']} 天：{n} 个"}); self.say("rm", f"{d}/*.json x{n}", "超过保留天数")

    # ------------------------------------------------------------ one run
    def go(self, data):
        for f in (self.worktrees, self.clones, self.remote, self.trash, self.tmp, self.nm, *([self.data] if data else [])):
            try: f()
            except Exception as e: self.err(f.__name__, e)
        self.r["wt_count"] = sum(len(_ls(W.path("wt", k))) for k in _ls(W.path("wt")))
        self.r["trash_bytes"] = du(W.trash_root()) if os.path.isdir(W.trash_root()) else 0
        self.r["trash_entries"] = len(_ls(W.trash_root()))
        ages = [time.time() - os.lstat(os.path.join(W.trash_root(), x)).st_ctime for x in _ls(W.trash_root())]
        self.r["trash_oldest_min"] = int(max(ages) / 60) if ages else 0
        self.r["root_bytes"] = du(W.root()) if os.path.isdir(W.root()) else 0
        self.r["free_gb"] = round(shutil.disk_usage(W.root() if os.path.isdir(W.root()) else "/").free / (1 << 30), 1)
        return self.r

class _null:
    def __enter__(self): return self
    def __exit__(self, *a): return False

def state():
    try:
        with open(f"{OUT}/state.json") as f: return json.load(f)
    except (OSError, ValueError): return {}

def cmd_run(A, conf):
    enforce = conf["enforce"] and not A.dry_run
    free = shutil.disk_usage(W.root() if os.path.isdir(W.root()) else "/").free / (1 << 30)
    aggr = A.aggressive or free < conf["free_gb_low"]
    st = state(); data = aggr or time.time() - (st.get("data_at") or 0) > 3600
    try:
        with W.flock(W.lock_file("janitor.lock"), timeout=0):
            r = Run(conf, enforce, aggr, A.event and {"event": A.event, "ticket": A.ticket, "pr": A.pr}).go(data)
    except W.Busy: print("skip: another janitor run holds janitor.lock"); return 75
    if data: st["data_at"] = time.time()
    st["errors_in_a_row"] = (st.get("errors_in_a_row", 0) + 1) if r["errors"] else 0; r["errors_in_a_row"] = st["errors_in_a_row"]
    os.makedirs(OUT, exist_ok=True); write_atomic(f"{OUT}/last.json", json.dumps(r, ensure_ascii=False, indent=1)); write_atomic(f"{OUT}/state.json", json.dumps(st))
    print(f"{now_cst():%H:%M:%S} {r['mode']}: reaped {len(r['reaped'])}, skipped {len(r['skipped'])}, unmarked {len(r['unmarked'])}, "
          f"branches {len(r['branches_deleted'])}, remote {len(r['remote_deleted'])}/{len(r['remote_kept'])} kept, deleted {len(r['deleted'])}, "
          f"trash {r['trash_entries']} ({r['trash_bytes'] >> 20}M), work root {r['root_bytes'] >> 20}M, free {r['free_gb']}G, errors {len(r['errors'])}")
    return 1 if r["errors"] else 0

def cmd_reap(A, conf):
    wd = os.path.abspath(A.path)
    if not os.path.lexists(wd): return 0                                    # already gone: idempotent
    if os.path.islink(wd) or not W.under(wd, W.path("wt")) or os.path.dirname(os.path.dirname(wd)) != W.path("wt"):
        print(f"refuse: {wd} is not wt/<kind>/<name> under {W.root()}"); return 1
    m = W.marker(wd)
    if m is None: print(f"refuse: {wd} has no {W.MARK}"); return 1
    r = Run(conf, conf["enforce"], False)
    ok = r.reap(wd, m, A.reason or "reap")
    print(json.dumps({"reaped": ok, "mode": r.r["mode"], "skipped": r.r["skipped"], "errors": r.r["errors"]}, ensure_ascii=False))
    return 0 if ok or r.r["skipped"] else 1

def cmd_status(A):
    try: r = json.load(open(f"{OUT}/last.json"))
    except (OSError, ValueError): print("janitor has not run yet"); return 0
    if A.json: print(json.dumps(r, ensure_ascii=False, indent=1)); return 0
    print(f"{r['ran_at']} {r['mode']}: reaped {len(r['reaped'])}, skipped {len(r['skipped'])}, unmarked {len(r['unmarked'])}, "
          f"remote deleted {len(r['remote_deleted'])} kept {len(r['remote_kept'])}, trash {r.get('trash_entries')}, free {r['free_gb']}G, errors {len(r['errors'])}")
    for k in ("unmarked", "long_running", "remote_kept", "errors"):
        for x in r.get(k) or []: print(f"  {k}: {json.dumps(x, ensure_ascii=False)}")
    return 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="run", choices=["run", "reap", "status"]); ap.add_argument("path", nargs="?")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--aggressive", action="store_true")
    ap.add_argument("--event", choices=["ticket-done", "pr-closed"]); ap.add_argument("--ticket"); ap.add_argument("--pr")
    ap.add_argument("--reason"); ap.add_argument("--json", action="store_true")
    A = ap.parse_args()
    if A.cmd == "status": return cmd_status(A)
    try: conf = load_conf()
    except ConfError as e: print(f"config error: {e}"); return 2
    if A.cmd == "reap":
        if not A.path: print("reap needs a path"); return 2
        return cmd_reap(A, conf)
    return cmd_run(A, conf)

if __name__ == "__main__":
    sys.exit(main())
