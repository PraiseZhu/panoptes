#!/usr/bin/env python3
"""python3 test/test_janitor.py -- janitor.py + workroot.py (plan 2026-10-24 §10): everything in a temp dir -- a bare "GitHub"
remote, the bot clone, worktrees, a person's repo next to it, a fake gh on PATH. Never touches a real repo or GitHub."""
import os, sys, json, time, shutil, subprocess, tempfile, unittest, io, contextlib, fcntl
from unittest import mock
_ENV_BEFORE = dict(os.environ)   # restored right after the imports below: this module's env must not leak into other test modules
for _k in [k for k in os.environ if k.startswith("GIT_")]: del os.environ[_k]
T = tempfile.mkdtemp(prefix="janitor-test-")
ENV = dict(PI_WORK_ROOT=f"{T}/work", PI_STATE=f"{T}/state", JANITOR_OUT=f"{T}/state/out/janitor", JANITOR_CONF=f"{T}/janitor.json",
                  FAKE_GH_LOG=f"{T}/gh.log", FAKE_GH_FIXTURE=f"{T}/gh.json", PYTHONDONTWRITEBYTECODE="1",
                  GIT_CONFIG_COUNT="3", GIT_CONFIG_KEY_0="user.name", GIT_CONFIG_VALUE_0="t", GIT_CONFIG_KEY_1="user.email",
                  GIT_CONFIG_VALUE_1="t@t", GIT_CONFIG_KEY_2="init.defaultBranch", GIT_CONFIG_VALUE_2="main")
GIT_ENV = {k: v for k, v in ENV.items() if k.startswith("GIT_")}
os.environ.update(ENV)   # for import time only (restored below); every test re-applies ENV in setUp
os.makedirs(f"{T}/bin"); open(f"{T}/bin/gh", "w").write('''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]; open(os.environ["FAKE_GH_LOG"], "a").write(json.dumps(a) + "\\n")
fx = json.load(open(os.environ["FAKE_GH_FIXTURE"]))
if fx.get("fail"): print("gh: boom", file=sys.stderr); sys.exit(1)
if a[:2] == ["pr", "list"]: print(json.dumps(fx.get("prs", {}).get(a[a.index("-R") + 1], []))); sys.exit(0)
if a[0] == "api":
    p = next(x for x in a[1:] if x.startswith("repos/"))
    if "-X" in a: sys.exit(0)
    if "/git/ref/heads/" in p:
        v = fx.get("refs", {}).get(p.split("/git/ref/heads/", 1)[1])
        if v is None: print("gh: Not Found (HTTP 404)", file=sys.stderr); sys.exit(1)
        print(v); sys.exit(0)
    if "/compare/" in p: print(fx.get("compare", {}).get(p.split("...", 1)[1], "diverged")); sys.exit(0)
sys.exit(3)
'''); os.chmod(f"{T}/bin/gh", 0o755); ENV["PATH"] = f"{T}/bin:" + os.environ["PATH"]; os.environ["PATH"] = ENV["PATH"]
B = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
import workroot as W   # noqa: E402
import janitor as J    # noqa: E402
os.environ.clear(); os.environ.update(_ENV_BEFORE)
# 10-24: every built-in default path points into T as well -- a test conf that leaves one out must never fall back to the box's
# real dirs (one did: test_10 with enforce=true removed the real /tmp/opsfix-retest/nm-* cache before this line existed)
J.DEFAULTS.update(legacy_tmp=[f"{T}/data/legacy"], evidence={**J.DEFAULTS["evidence"], "dir": f"{T}/data/evidence"},
                  step_results={**J.DEFAULTS["step_results"], "dir": f"{T}/data/steps"})
# 10-30 (#142 follow-up): the "nothing outside the sandbox was deleted" guard watches decoys in T, never the live
# /workspace/pi-work, /tmp/opsfix-retest, /tmp/pi-prfix or argus-root dirs (other sessions change those mid-run: false failures).
# Every fallback janitor/workroot could reach without a test conf points at a decoy with a sentinel entry that must survive.
DECOY = {n: f"{T}/decoy/{n}" for n in ("opsfix-retest", "pi-prfix", "evidence", "step-results", "pi-work")}
for _d in DECOY.values(): os.makedirs(f"{_d}/keep-me", exist_ok=True)
W.PI_WORK = DECOY["pi-work"]   # workroot.root() fallback when PI_WORK_ROOT is unset
REAL = list(DECOY.values())
def real_state(): return {p: (sorted(os.listdir(p)) if os.path.isdir(p) else None) for p in REAL}
REAL_BEFORE = real_state()

def sh(*a, cwd=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}; env.update(GIT_ENV)   # identity explicit, not inherited
    r = subprocess.run(a, cwd=cwd, capture_output=True, text=True, env=env)
    if r.returncode: raise AssertionError(f"{a}: {r.stdout}{r.stderr}")
    return r.stdout.strip()
def wf(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "w").write(s)
def dead_pid():
    p = subprocess.Popen(["true"]); p.wait(); return p.pid      # exited: pid_alive(pid, its old start) is False even if reused
def snapshot(root):
    """every path under root (link flag, size) -- locks/ aside: taking a lock creates its file, that is not a decision"""
    out = []
    for d, ds, fs in os.walk(root):
        if os.path.relpath(d, root) == ".": ds[:] = [x for x in ds if x != "locks"]
        for x in ds + fs:
            p = os.path.join(d, x)
            out.append((os.path.relpath(p, root), os.path.islink(p), os.path.getsize(p) if os.path.isfile(p) and not os.path.islink(p) else 0))
    return sorted(out)

def isolate(tc):
    """re-apply this module's env for one test and refuse to run unless the work root is inside T"""
    p = mock.patch.dict(os.environ, ENV); p.start(); tc.addCleanup(p.stop)
    for k in [k for k in os.environ if k.startswith("GIT_") and k not in GIT_ENV]: del os.environ[k]
    if not os.path.realpath(W.root()).startswith(os.path.realpath(T) + os.sep):
        raise AssertionError(f"work root {W.root()} is not inside {T}: refusing to touch real dirs")

class Base(unittest.TestCase):
    def setUp(self):
        isolate(self)
        for d in ("work", "state", "human", "origin.git", "seed", "outside", "data"): shutil.rmtree(f"{T}/{d}", ignore_errors=True)
        for f in ("gh.log", "janitor.json"):
            if os.path.exists(f"{T}/{f}"): os.remove(f"{T}/{f}")
        self.conf({"enforce": True})
        self.gh({})
        sh("git", "init", "-q", "--bare", f"{T}/origin.git"); sh("git", "init", "-q", f"{T}/seed")
        wf(f"{T}/seed/a.txt", "a\n"); sh("git", "add", "-A", cwd=f"{T}/seed"); sh("git", "commit", "-qm", "a", cwd=f"{T}/seed")
        sh("git", "push", "-q", f"{T}/origin.git", "main", cwd=f"{T}/seed")
        self.saved = json.loads(json.dumps(W.REPOS)); W.REPOS["mivo"]["url"] = f"{T}/origin.git"; W.REPOS["mivo"]["filter"] = None
        self.clone = W.ensure_clone("mivo")
    def tearDown(self):
        W.REPOS.clear(); W.REPOS.update(self.saved)
    def conf(self, c, data_dirs=True):
        if data_dirs: c = {"evidence": {"dir": f"{T}/data/evidence"}, "step_results": {"dir": f"{T}/data/steps"}, "legacy_tmp": [f"{T}/data/legacy"], **c}
        c = {"free_gb_low": 0, **c}       # a CI runner has < 15 G free: never aggressive unless the test says so
        json.dump(c, open(f"{T}/janitor.json", "w"))
    def gh(self, fx): json.dump(fx, open(f"{T}/gh.json", "w"))
    def gh_calls(self):
        if not os.path.exists(f"{T}/gh.log"): return []
        with open(f"{T}/gh.log") as f: return [json.loads(l) for l in f]
    def wt(self, name="pi-wt-1-abcdef12", branch="fix/qa-1-abcdef12", pid=None, **kw):
        wd = W.path("wt", "mivo", name); os.makedirs(os.path.dirname(wd), exist_ok=True)
        sh("git", "worktree", "add", "-q", *(["-b", branch, wd, "main"] if branch else ["--detach", wd, "main"]), cwd=self.clone)
        m = W.mark(wd, "prfix", repo="mivo", ticket=name, branch=branch, **kw)
        if pid is not None:
            m["pid"] = pid; m["pid_start"] = W.proc_start(pid) if pid == os.getpid() else 1
            json.dump(m, open(f"{wd}/{W.MARK}", "w"))
        return wd
    def run_j(self, *argv):
        buf = io.StringIO(); sys.argv = ["janitor.py", *argv]
        with contextlib.redirect_stdout(buf): rc = J.main()
        self.out = buf.getvalue(); return rc
    def last(self): return json.load(open(f"{T}/state/out/janitor/last.json"))
    def branches(self, repo=None): return sh("git", "for-each-ref", "--format=%(refname:short)", "refs/heads/", cwd=repo or self.clone).split()

class TestReap(Base):
    def test_01_reap_is_quick_and_takes_worktree_and_branch(self):
        wd = self.wt(pid=dead_pid())
        self.assertNotIn(W.MARK, sh("git", "status", "--porcelain", cwd=wd))          # the marker never shows up in git status
        t0 = time.time(); rc = self.run_j("reap", wd)
        self.assertEqual(rc, 0, self.out); self.assertLess(time.time() - t0, 5)
        self.assertFalse(os.path.exists(wd)); self.assertNotIn("fix/qa-1-abcdef12", self.branches())
        self.assertNotIn(wd, sh("git", "worktree", "list", cwd=self.clone))
        self.assertEqual(self.run_j("reap", wd), 0)                                     # again: already gone, still 0

    def test_02_dirty_worktree_is_salvaged_first(self):
        wd = self.wt(pid=dead_pid()); wf(f"{wd}/a.txt", "changed\n"); wf(f"{wd}/new.txt", "n\n")
        self.assertEqual(self.run_j("run"), 0, self.out)
        r = self.last()["reaped"][0]; self.assertTrue(r["salvage"] and os.path.exists(r["salvage"]), r)
        body = open(r["salvage"]).read(); self.assertIn("+changed", body); self.assertIn("new.txt", body)
        self.assertFalse(os.path.exists(wd))

    def test_03_live_pid_or_held_lock_is_skipped(self):
        wd = self.wt(pid=os.getpid())
        self.run_j("run"); self.assertTrue(os.path.isdir(wd)); self.assertIn("还在跑", self.last()["skipped"][0]["why"])
        lk = f"{T}/state/t.lock"; os.makedirs(os.path.dirname(lk), exist_ok=True)
        wd2 = self.wt("pi-wt-2-abcdef12", "fix/qa-2-abcdef12", pid=dead_pid(), lock=lk)
        f = open(lk, "a"); fcntl.flock(f, fcntl.LOCK_EX)
        try:
            self.run_j("run"); self.assertTrue(os.path.isdir(wd2))
            self.assertTrue(any("锁被占" in s["why"] for s in self.last()["skipped"]))
        finally: f.close()
        self.run_j("run"); self.assertFalse(os.path.exists(wd2))                        # lock released: reaped

    def test_04_killed_job_is_reaped_by_the_next_run(self):
        p = subprocess.Popen(["sleep", "60"]); wd = self.wt()
        m = json.load(open(f"{wd}/{W.MARK}")); m["pid"], m["pid_start"] = p.pid, W.proc_start(p.pid); json.dump(m, open(f"{wd}/{W.MARK}", "w"))
        self.run_j("run"); self.assertTrue(os.path.isdir(wd))
        p.kill(); p.wait()                                                               # SIGKILL: no finally ran
        self.run_j("run"); self.assertFalse(os.path.exists(wd)); self.assertNotIn("fix/qa-1-abcdef12", self.branches())

    def test_05_unmarked_outside_and_symlinks_are_never_deleted(self):
        plain = W.path("wt", "mivo", "pi-wt-9-abcdef12"); wf(f"{plain}/x", "person's\n")
        outside = f"{T}/outside/pi-wt-8-abcdef12"; wf(f"{outside}/{W.MARK}", json.dumps({"schema": 1, "owner": "pi-line", "kind": "prfix", "pid": dead_pid()}))
        os.symlink(outside, W.path("wt", "mivo", "pi-wt-8-abcdef12"))
        self.assertEqual(self.run_j("run"), 0, self.out)
        self.assertTrue(os.path.exists(f"{plain}/x") and os.path.exists(f"{outside}/{W.MARK}"))
        self.assertEqual(sorted(os.path.basename(u["path"]) for u in self.last()["unmarked"]), ["pi-wt-8-abcdef12", "pi-wt-9-abcdef12"])
        self.assertEqual(self.run_j("reap", plain), 1); self.assertEqual(self.run_j("reap", outside), 1)
        self.assertEqual(self.run_j("reap", W.path("wt", "mivo", "pi-wt-8-abcdef12")), 1)
        self.assertTrue(os.path.exists(f"{plain}/x") and os.path.exists(f"{outside}/{W.MARK}"))

    def test_06_a_persons_repo_with_the_same_names_is_left_alone(self):
        h = f"{T}/human"; sh("git", "clone", "-q", f"{T}/origin.git", h); sh("git", "branch", "fix/qa-1-abcdef12", cwd=h)
        sh("git", "worktree", "add", "-q", f"{T}/human-wt/pi-wt-1-abcdef12", "fix/qa-1-abcdef12", cwd=h)
        wd = self.wt(pid=dead_pid()); sh("git", "branch", "fix/qa-3-abcdef12", cwd=self.clone)   # an orphan bot branch in OUR clone
        self.run_j("run")
        self.assertFalse(os.path.exists(wd)); self.assertNotIn("fix/qa-3-abcdef12", self.branches())
        self.assertIn("fix/qa-1-abcdef12", self.branches(h)); self.assertTrue(os.path.isdir(f"{T}/human-wt/pi-wt-1-abcdef12"))
        shutil.rmtree(f"{T}/human-wt", ignore_errors=True)

class TestInfraBranches(Base):
    def test_17_top_level_worktree_is_one_entry_and_manual_hold_is_held(self):   # 10-30 ticket C
        top = W.path("wt", "base-run"); sh("git", "worktree", "add", "-q", "--detach", top, "main", cwd=self.clone)
        wf(f"{top}/docs/x.md", "person's\n")
        held_top = W.path("wt", "verify-6x"); sh("git", "worktree", "add", "-q", "--detach", held_top, "main", cwd=self.clone)
        hold = {"schema": 1, "owner": "manual-hold", "kind": "manual", "created_at": "2026-10-30T12:00:00+08:00", "pid": dead_pid()}
        wf(f"{held_top}/{W.MARK}", json.dumps(hold))
        held_in = W.path("wt", "mivo", "daily-null-cg"); wf(f"{held_in}/{W.MARK}", json.dumps(hold)); wf(f"{held_in}/x", "y\n")
        other = W.path("wt", "mivo", "pi-wt-7-abcdef12"); wf(f"{other}/{W.MARK}", json.dumps({**hold, "owner": "someone"}))
        self.assertEqual(self.run_j("run"), 0, self.out)
        r = self.last()
        self.assertEqual(sorted(u["path"] for u in r["unmarked"]), sorted([other, top]))                  # the worktree once, not .git/docs/a.txt
        self.assertEqual(sorted(h["path"] for h in r["held"]), sorted([held_in, held_top]))
        self.assertTrue(all(h["owner"] == "manual-hold" and h["created_at"] for h in r["held"]))
        for u in r["unmarked"]: self.assertIn(f"unmarked {u['path']}", self.out)   # 10-30: the log names each unmarked dir, not just the count
        for d in (top, held_top, held_in, other): self.assertTrue(os.path.isdir(d), d)        # never reaped
        self.assertEqual(self.run_j("run"), 0, self.out); self.assertTrue(os.path.isdir(held_top) and os.path.isdir(held_in))

    def test_14_infra_branch_kept_while_its_pr_is_in_flight(self):
        os.environ["JANITOR_INFRA_TICKETS"] = f"{T}/infra-tickets.json"; J.INFRA_TICKETS = os.environ["JANITOR_INFRA_TICKETS"]
        W.REPOS["panoptes"]["url"] = f"{T}/origin.git"; W.REPOS["panoptes"]["filter"] = None; pc = W.ensure_clone("panoptes")
        for b in ("fix/infra-aaaaaaaa", "fix/infra-bbbbbbbb", "feature/person"): sh("git", "branch", b, cwd=pc)
        json.dump({"tickets": {"inf1-aaaaaaaa0000": {"status": "pr_open"}, "inf1-bbbbbbbb0000": {"status": "merged"}}}, open(J.INFRA_TICKETS, "w"))
        self.run_j("run"); left = self.branches(pc)
        self.assertIn("fix/infra-aaaaaaaa", left); self.assertNotIn("fix/infra-bbbbbbbb", left); self.assertIn("feature/person", left)
        open(J.INFRA_TICKETS, "w").write("{broken"); sh("git", "branch", "fix/infra-cccccccc", cwd=pc)
        self.run_j("run"); self.assertIn("fix/infra-cccccccc", self.branches(pc))      # tickets unreadable: keep every one

class TestRemote(Base):
    PR = {"number": 5, "headRefName": "qa-fix/fixpr-1-x", "headRefOid": "a" * 40, "mergedAt": None, "body": "x\n<!-- pi-line fixpr:1 -->"}
    def deletes(self): return [c for c in self.gh_calls() if "-X" in c]
    def test_07_remote_branch_sha_check(self):
        self.gh({"prs": {"xindong/mivo-canvas-plugin": [self.PR]}, "refs": {"qa-fix/fixpr-1-x": "a" * 40}})
        self.run_j("run"); self.assertEqual(len(self.deletes()), 1); self.assertEqual(self.last()["remote_deleted"][0]["branch"], "qa-fix/fixpr-1-x")
        os.remove(f"{T}/gh.log")
        self.gh({"prs": {"xindong/mivo-canvas-plugin": [self.PR]}, "refs": {"qa-fix/fixpr-1-x": "b" * 40}})   # pushed on since
        self.run_j("run"); self.assertEqual(self.deletes(), []); self.assertIn("被推进过", self.last()["remote_kept"][0]["why"])
        self.gh({"prs": {"xindong/mivo-canvas-plugin": [self.PR]}, "refs": {"qa-fix/fixpr-1-x": "b" * 40}, "compare": {"b" * 40: "behind"}})
        self.run_j("run"); self.assertEqual(len(self.deletes()), 1)                    # ... but already in main: goes
        os.remove(f"{T}/gh.log"); self.gh({"fail": True})
        self.run_j("run"); self.assertEqual(self.deletes(), []); self.assertTrue(all("gh" in k["why"] for k in self.last()["remote_kept"]))
        os.remove(f"{T}/gh.log")
        nomark = dict(self.PR, body="a person's PR"); merged = dict(self.PR, mergedAt="2026-10-24T00:00:00Z")
        self.gh({"prs": {"xindong/mivo-canvas-plugin": [nomark, merged]}, "refs": {"qa-fix/fixpr-1-x": "a" * 40}})
        self.run_j("run"); self.assertEqual(self.deletes(), [])                         # not ours / merged mivo (GitHub deletes those)

    def test_16_rollback_hold_keeps_remote_branch(self):   # 10-28 #96: no direct GitHub delete while ROLLBACK-SUSPECTED exists
        mark = f"{T}/ROLLBACK-SUSPECTED"; saved = os.environ.get("PI_ROLLBACK_MARK"); os.environ["PI_ROLLBACK_MARK"] = mark
        try:
            self.gh({"prs": {"xindong/mivo-canvas-plugin": [self.PR]}, "refs": {"qa-fix/fixpr-1-x": "a" * 40}})
            open(mark, "w").close()
            for _ in range(2):   # marker appears, then persists
                self.run_j("run"); self.assertEqual(self.deletes(), []); self.assertNotIn("delete remote", self.out)
                self.assertIn("ROLLBACK-SUSPECTED", self.last()["remote_kept"][0]["why"]); self.assertEqual(self.last()["remote_deleted"], [])
            os.remove(mark)      # marker removed -> deleted on the next run
            self.run_j("run"); self.assertEqual(len(self.deletes()), 1); self.assertEqual(self.last()["remote_deleted"][0]["branch"], "qa-fix/fixpr-1-x")
        finally:
            if os.path.exists(mark): os.remove(mark)
            if saved is None: os.environ.pop("PI_ROLLBACK_MARK", None)
            else: os.environ["PI_ROLLBACK_MARK"] = saved

class TestIdempotentAndData(Base):
    def test_08_runs_are_idempotent(self):
        wd = self.wt(pid=dead_pid()); self.wt("pi-wt-5-abcdef12", "fix/qa-5-abcdef12", pid=os.getpid())
        self.run_j("run"); a = snapshot(W.ROOT)
        self.run_j("run"); self.assertEqual(snapshot(W.ROOT), a); self.assertFalse(os.path.exists(wd))
        shutil.rmtree(W.path("wt", "mivo", "pi-wt-5-abcdef12"))                         # directory gone, registration left
        self.run_j("run"); self.assertEqual(self.last()["pruned"][0]["repo"], "mivo")
        self.assertNotIn("pi-wt-5", sh("git", "worktree", "list", cwd=self.clone))

    def test_09_nm_cache_keeps_newest_two_and_linked(self):
        for i, h in enumerate(["old1", "old2", "linked", "new1", "new2"]):
            wf(W.nm_cache(h) + "/node_modules/x", h); t = time.time() - 86400 * (10 - i); os.utime(W.nm_cache(h), (t, t))
        wd = self.wt(pid=os.getpid()); os.symlink(W.nm_cache("linked") + "/node_modules", f"{wd}/node_modules")
        self.run_j("run")
        self.assertEqual(sorted(os.listdir(W.path("cache", "nm"))), ["linked", "new1", "new2"])

    def test_10_evidence_and_step_results(self):
        import datetime as dt
        today = J.now_cst().date()
        days = [(today - dt.timedelta(days=k)).isoformat() for k in range(12, -1, -1)]
        for d in days: wf(f"{T}/data/evidence/{d}/big", "x" * 1000)
        self.conf({"enforce": True, "evidence": {"dir": f"{T}/data/evidence", "keep_days": 7, "cap_gb": 5000 / (1 << 30), "keep_newest": 2},
                   "step_results": {"dir": f"{T}/data/steps", "keep_days": 14}}, data_dirs=False)
        wf(f"{T}/data/steps/old.json", "{}"); wf(f"{T}/data/steps/new.json", "{}"); t = time.time() - 16 * 86400; os.utime(f"{T}/data/steps/old.json", (t, t))
        self.run_j("run", "--aggressive")
        left = sorted(os.listdir(f"{T}/data/evidence"))
        self.assertEqual(left, days[-2:])                                               # cap 5 KB, aggressive halves it: only the newest 2 stay
        self.assertEqual(os.listdir(f"{T}/data/steps"), ["new.json"])
        for d in days: wf(f"{T}/data/evidence/{d}/big", "x")
        self.conf({"enforce": True, "evidence": {"dir": f"{T}/data/evidence", "keep_days": 7, "cap_gb": 3, "keep_newest": 2}}, data_dirs=False)
        self.run_j("run", "--aggressive"); self.assertEqual(sorted(os.listdir(f"{T}/data/evidence")), days[-8:])   # age: 7 days + today

    def test_11_dry_run_changes_nothing(self):
        self.wt(pid=dead_pid()); wf(W.nm_cache("a") + "/node_modules/x", "1"); wf(W.path("tmp", "old", "f"), "1")
        t = time.time() - 86400; os.utime(W.path("tmp", "old"), (t, t)); sh("git", "branch", "fix/qa-7-abcdef12", cwd=self.clone)
        wf(f"{T}/data/evidence/2000-01-01/x", "1")
        self.gh({"prs": {"xindong/mivo-canvas-plugin": [TestRemote.PR]}, "refs": {"qa-fix/fixpr-1-x": "a" * 40}})
        before = (snapshot(W.ROOT), snapshot(f"{T}/data"), self.branches())
        self.conf({"enforce": False}); self.run_j("run")
        self.assertEqual((snapshot(W.ROOT), snapshot(f"{T}/data"), self.branches()), before)
        r = self.last(); self.assertEqual(r["mode"], "dry-run")
        self.assertTrue(r["reaped"] and r["branches_deleted"] and r["remote_deleted"] and r["deleted"], r)
        self.assertFalse([c for c in self.gh_calls() if "-X" in c])
        self.conf({"enforce": True}); self.run_j("run", "--dry-run")                     # --dry-run wins over enforce
        self.assertEqual((snapshot(W.ROOT), snapshot(f"{T}/data"), self.branches()), before)

    def test_12_second_run_while_one_holds_the_lock(self):
        with W.flock(W.lock_file("janitor.lock"), timeout=0):
            self.assertEqual(self.run_j("run"), 75)
        self.assertEqual(self.run_j("run"), 0, self.out)

    def test_13_broken_config_is_exit_2_and_trash_is_purged(self):
        open(f"{T}/janitor.json", "w").write("{broken"); self.assertEqual(self.run_j("run"), 2)
        self.conf({"enforce": False}); wf(W.path(".trash", "x-1", "f"), "1"); t = time.time() - 3600
        os.utime(W.path(".trash", "x-1"), (t, t))
        self.run_j("run", "--aggressive"); self.assertEqual(os.listdir(W.trash_root()), [])   # thrown away already: purged in dry-run too

class TestWatchdog(Base):
    def test_15_watchdog_reads_the_report(self):
        import watchdog as WD
        p = mock.patch.object(WD, "BASE", f"{T}/state"); p.start(); self.addCleanup(p.stop)   # lib.BASE is fixed at first import (maybe another module's PI_STATE)
        now = J.now_cst(); f = f"{T}/state/out/janitor/last.json"; os.makedirs(os.path.dirname(f), exist_ok=True)
        r = {"ran_at": now.isoformat(), "mode": "enforce", "free_gb": 3, "unmarked": [{"path": "/x/wt/mivo/a"}], "trash_oldest_min": 90,
             "skipped": [{"path": "/x/wt/mivo/b", "why": "单子锁被占", "age_h": 5}, {"path": "/x/wt/mivo/d", "why": "进程 9 还在跑", "age_h": 5}],
             "long_running": [{"path": "/x/wt/mivo/c", "hours": 5, "why": "进程 1 还在跑"}]}
        json.dump(r, open(f, "w")); probs = {}; WD.janitor_check(now, probs)
        self.assertEqual(sorted(k.split(":")[1] for k in probs), ["disk", "long", "stuck", "trash", "unmarked"])
        self.assertIn("/x/wt/mivo/b", json.dumps(probs["janitor:stuck"], ensure_ascii=False))
        r.update(mode="dry-run", free_gb=50, unmarked=[], trash_oldest_min=0, long_running=[]); json.dump(r, open(f, "w"))
        probs = {}; WD.janitor_check(now, probs); self.assertEqual(probs, {})      # rehearsal: leftovers are expected, nothing to say
        r["ran_at"] = (now - __import__("datetime").timedelta(hours=3)).isoformat(); r["free_gb"] = 1; json.dump(r, open(f, "w"))
        # 10-30 ticket C: only unmarked counts (held is info) and the alert names the parent dir, not .git / .github
        r.update(mode="enforce", ran_at=now.isoformat(), free_gb=50, skipped=[], unmarked=[{"path": f"{WD.PI_WORK}/wt/base-run"}],
                 held=[{"path": f"{WD.PI_WORK}/wt/verify-{i}", "owner": "manual-hold"} for i in range(5)]); json.dump(r, open(f, "w"))
        probs = {}; WD.janitor_check(now, probs); (k,) = [k for k in probs if k.startswith("janitor:unmarked")]
        self.assertIn("有 1 个目录", probs[k]["what"]); self.assertIn("wt/base-run", probs[k]["what"])
        r.update(unmarked=[]); json.dump(r, open(f, "w")); probs = {}; WD.janitor_check(now, probs); self.assertEqual(probs, {})
        r.update(mode="dry-run", free_gb=1, held=[])
        probs = {}; WD.janitor_check(now, probs); self.assertEqual(probs, {})      # stale report: "janitor stopped" is job_checks' to say

    def test_16_unmarked_key_per_dir_survives_set_changes(self):   # 10-30: a 4th dir coming and going must not recover/re-raise a/b/c
        import watchdog as WD
        p = mock.patch.object(WD, "BASE", f"{T}/state"); p.start(); self.addCleanup(p.stop)
        now = J.now_cst(); f = f"{T}/state/out/janitor/last.json"; os.makedirs(os.path.dirname(f), exist_ok=True)
        def keys(names):
            json.dump({"ran_at": now.isoformat(), "mode": "enforce", "free_gb": 50, "skipped": [],
                       "unmarked": [{"path": f"{T}/wt/{n}"} for n in names]}, open(f, "w"))
            probs = {}; WD.janitor_check(now, probs); return {k for k in probs if k.startswith("janitor:unmarked:")}
        k3 = keys("abc"); k4 = keys("abcd"); k3b = keys("abc")
        self.assertEqual(len(k3), 3); self.assertEqual(len(k4), 4)
        self.assertTrue(k3 <= k4, "a/b/c keys must stay open when d appears (no false recovered)")
        self.assertEqual(k3, k3b, "same dirs -> same keys (no re-raise)")
        self.assertEqual(keys("bc"), k3 - keys("a"), "removing a recovers only a's key")

class TestZZIsolation(unittest.TestCase):
    def test_00_guard_reads_no_live_path(self):
        for p in REAL: self.assertTrue(os.path.realpath(p).startswith(os.path.realpath(T) + os.sep), p)
        self.assertTrue(os.path.realpath(W.PI_WORK).startswith(os.path.realpath(T) + os.sep), W.PI_WORK)
    def test_99_nothing_outside_the_sandbox_was_deleted(self):   # production jobs may add entries meanwhile; none may go
        now = real_state()
        for p, before in REAL_BEFORE.items():
            self.assertEqual(sorted(set(before or []) - set(now[p] or [])), [], p)

if __name__ == "__main__":
    try: unittest.main(verbosity=1)
    finally: shutil.rmtree(T, ignore_errors=True)
