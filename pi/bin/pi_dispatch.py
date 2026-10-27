#!/usr/bin/env python3
"""pi-dispatch -- any bot on the box launches N Pi agent tasks in parallel, admitted one by one by memory (10-27, fleet-wide).

Each task is its own detached process (survives the caller), sandboxed and model-routed like every Pi line call (lib.pi:
bwrap, env allowlist, pi-auto tier) and loads pstack (bin/pi-noskills.sh). Before EACH launch it waits until
prwatch.headroom_mb() -- MemAvailable minus what running fixes, merge tests and other dispatched tasks may still grow to --
is >= ADMIT_FREE_MB + the task's peak; prwatch counts dispatched tasks too, so neither side over-admits. Never a fixed cap.

Task kinds (positional SPECs or JSON):
  qa:N[:fp]   a Praise PR ticket -> `run.sh prfix --qa N [--fp fp]` (prfix owns its worktree, ticket lock and outbound)
  revise:N    `run.sh prfix --revise N`;  next: `run.sh prfix --next`
  free-form   --prompt TEXT (--cwd DIR | --repo GITDIR [--ref REF]) or --tasks FILE.jsonl / --task JSON with keys
              prompt, cwd | repo (+ ref, default origin/main|HEAD), name, tier (quick|work|heavy|deep, default work),
              write (default true with repo, else false), web (false), timeout (s, 1800).
              repo -> a fresh worktree PI_WORK/wt/dispatch/<caller>-<id> on new branch dispatch/<caller>-<id> (marked
              .pi-owned.json, so janitor reaps it after the task ends; commits stay on the branch in that repo).
Results: PI_STATE/out/dispatch/<caller>/<id>.json (status, text, cwd, branch); logs out/logs/pi-dispatch-<caller>-<day>.log.
  pi-dispatch --caller NAME [--max-wait S] [--dry-run] [SPEC ...] [--prompt ... | --task JSON | --tasks FILE]
  pi-dispatch --status [--caller NAME]"""
import argparse, fcntl, json, os, re, subprocess, sys, time, uuid
from lib import BASE, PI_CODE, now_cst
import lib, prwatch, workroot

OUT = lambda caller: f"{BASE}/out/dispatch/{caller}"
LOCK = "/tmp/pi-dispatch.lock"   # admit + launch one at a time across callers: the next admit sees the last launch
POLL_S = 20
TICKET = re.compile(r"^(?:qa:(\d+)(?::([0-9a-f]+))?|revise:(\d+)|next)$")

def logf(caller):
    os.makedirs(f"{BASE}/out/logs", exist_ok=True)
    return f"{BASE}/out/logs/pi-dispatch-{caller}-{now_cst():%Y-%m-%d}.log"

def log(caller, msg):
    with open(logf(caller), "a") as f: f.write(f"{now_cst():%H:%M:%S} [{caller}] {msg}\n")
    print(f"[{caller}] {msg}", flush=True)

def parse_spec(s):
    """'qa:12:abcd' / 'revise:7' / 'next' -> task dict; anything else -> ValueError"""
    m = TICKET.match(s.strip())
    if not m: raise ValueError(f"bad spec {s!r} (qa:N[:fp] | revise:N | next; free-form tasks use --prompt/--task)")
    if m.group(1): return {"kind": "prfix", "args": ["--qa", m.group(1)] + (["--fp", m.group(2)] if m.group(2) else []), "name": f"qa{m.group(1)}"}
    if m.group(3): return {"kind": "prfix", "args": ["--revise", m.group(3)], "name": f"rev{m.group(3)}"}
    return {"kind": "prfix", "args": ["--next"], "name": "next"}

def norm_task(d):
    """validate a free-form task dict"""
    if not isinstance(d, dict) or not str(d.get("prompt") or "").strip(): raise ValueError("task needs a prompt")
    if bool(d.get("cwd")) == bool(d.get("repo")): raise ValueError("task needs exactly one of cwd / repo")
    for k in ("cwd", "repo"):
        if d.get(k) and not os.path.isdir(d[k]): raise ValueError(f"{k} {d[k]} is not a directory")
    if d.get("tier", "work") not in ("quick", "work", "heavy", "deep", "review"): raise ValueError(f"bad tier {d.get('tier')}")
    name = re.sub(r"[^A-Za-z0-9_.-]", "-", str(d.get("name") or "task"))[:40]
    return {"kind": "pi", "name": name, **{k: d[k] for k in ("prompt", "cwd", "repo", "ref", "tier", "write", "web", "timeout") if k in d}}

def peak_mb(task, st):
    return prwatch.learned(st, "fix", prwatch.RUN_PEAK_MB) if task["kind"] == "prfix" else prwatch.TASK_PEAK_MB

def room_and_need(task):
    st = prwatch.jload(prwatch.STATE, {})          # learned peaks, read only
    return prwatch.headroom_mb(prwatch.proc_table(), st=st), prwatch.ADMIT_FREE_MB + peak_mb(task, st)

def wait_admit(caller, task, max_wait, sleep=time.sleep, clock=time.time):
    """block until memory admits task (True) or max_wait seconds pass (False)"""
    t0, said = clock(), False
    while True:
        room, need = room_and_need(task)
        if room >= need: return True
        if clock() - t0 >= max_wait: return False
        if not said: log(caller, f"{task['name']}: waiting for memory (headroom {room} MB < {need} MB)"); said = True
        sleep(POLL_S)

def argv_for(task, caller, tid):
    if task["kind"] == "prfix": return [f"{PI_CODE}/bin/run.sh", "prfix", *task["args"]]
    return [sys.executable, os.path.abspath(__file__), "--run-one", json.dumps({**task, "caller": caller, "id": tid})]

def launch(caller, task, dry=False):
    tid = f"{now_cst():%m%d-%H%M%S}-{task['name']}-{uuid.uuid4().hex[:4]}"
    argv = argv_for(task, caller, tid)
    if dry: log(caller, f"DRY would launch {tid}: {argv[:4]}"); return tid
    os.makedirs(f"{BASE}/out/logs/pi-dispatch", exist_ok=True)
    with open(f"{BASE}/out/logs/pi-dispatch/{caller}-{tid}.log", "a") as lf:
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT, start_new_session=True,
                             cwd=f"{PI_CODE}/bin", env=dict(os.environ, PI_DISPATCH_CALLER=caller, PRFIX_DISPATCHED="1"))
    log(caller, f"launched {tid} pid={p.pid} ({task['kind']})"); return tid

def dispatch(caller, tasks, max_wait=7200, dry=False):
    """-> [(task name, id or None when memory never admitted it)]"""
    out = []
    for task in tasks:
        with open(LOCK, "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            ok = dry or wait_admit(caller, task, max_wait)
            tid = launch(caller, task, dry) if ok else None
            if not ok: log(caller, f"{task['name']}: NOT launched, memory did not admit it within {max_wait}s")
            if tid and not dry: time.sleep(1)   # let the child exec so the next headroom sees it
        out.append((task["name"], tid))
    return out

def run_one(spec):
    """the detached task process: worktree (repo tasks) -> lib.pi (sandbox + pstack) -> result json"""
    t = json.loads(spec); caller, tid = t["caller"], t["id"]
    os.makedirs(OUT(caller), exist_ok=True); res = f"{OUT(caller)}/{tid}.json"
    rec = {"id": tid, "caller": caller, "name": t["name"], "status": "running", "pid": os.getpid(), "started_at": now_cst().isoformat(timespec="seconds")}
    w = lambda: json.dump(rec, open(res, "w"), ensure_ascii=False, indent=1)
    try:
        cwd = t.get("cwd")
        if t.get("repo"):
            branch = f"dispatch/{caller}-{tid}"; cwd = workroot.path("wt", "dispatch", f"{caller}-{tid}")
            ref = t.get("ref") or ("origin/main" if subprocess.run(["git", "-C", t["repo"], "rev-parse", "-q", "--verify", "origin/main"], capture_output=True).returncode == 0 else "HEAD")
            os.makedirs(os.path.dirname(cwd), exist_ok=True)
            r = subprocess.run(["git", "-C", t["repo"], "worktree", "add", "-q", "-b", branch, cwd, ref], capture_output=True, text=True, timeout=600)
            if r.returncode: raise RuntimeError(f"worktree add: {r.stderr.strip()[-300:]}")
            workroot.mark(cwd, "dispatch", repo=t["repo"], branch=branch, ticket=tid, caller=caller)
            rec |= {"branch": branch, "repo": t["repo"]}
        rec["cwd"] = cwd; w()
        text, task = lib.pi(t.get("tier", "work"), cwd, t["prompt"], f"dispatch-{caller}-{tid}", timeout=int(t.get("timeout", 1800)),
                            write=bool(t.get("write", bool(t.get("repo")))), web=bool(t.get("web")), skills=False)
        rec |= {"status": "done", "text": text, "task": task}
    except Exception as e:
        rec |= {"status": "failed", "error": str(e)[-1000:]}
    rec["ended_at"] = now_cst().isoformat(timespec="seconds"); w()
    log(caller, f"{tid} {rec['status']} -> {res}"); return 0 if rec["status"] == "done" else 1

def status(caller=None):
    root = f"{BASE}/out/dispatch"
    for c in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if caller and c != caller: continue
        for f in sorted(os.listdir(f"{root}/{c}"))[-20:]:
            try: r = json.load(open(f"{root}/{c}/{f}"))
            except (OSError, ValueError): continue
            print(f"{c}\t{r.get('id')}\t{r.get('status')}\t{r.get('branch') or r.get('cwd') or ''}")

def main(argv=None):
    ap = argparse.ArgumentParser(prog="pi-dispatch", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("specs", nargs="*"); ap.add_argument("--caller"); ap.add_argument("--run-one")
    ap.add_argument("--prompt"); ap.add_argument("--cwd"); ap.add_argument("--repo"); ap.add_argument("--ref")
    ap.add_argument("--tier", default="work"); ap.add_argument("--name")
    ap.add_argument("--task", action="append", default=[]); ap.add_argument("--tasks")
    ap.add_argument("--max-wait", type=int, default=7200); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--status", action="store_true")
    a = ap.parse_args(argv)
    if a.run_one: return run_one(a.run_one)
    if a.status: status(a.caller); return 0
    if not a.caller or not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", a.caller): ap.error("--caller NAME required (letters, digits, _ . -)")
    try:
        tasks = [parse_spec(s) for s in a.specs] + [norm_task(json.loads(j)) for j in a.task]
        if a.tasks: tasks += [norm_task(json.loads(l)) for l in open(a.tasks) if l.strip()]
        if a.prompt: tasks.append(norm_task({k: v for k, v in {"prompt": a.prompt, "cwd": a.cwd, "repo": a.repo, "ref": a.ref, "tier": a.tier, "name": a.name}.items() if v}))
    except (ValueError, OSError) as e: ap.error(str(e))
    if not tasks: ap.error("no tasks")
    res = dispatch(a.caller, tasks, a.max_wait, a.dry_run)
    for n, tid in res: print(f"{n}\t{tid or 'NOT-ADMITTED'}")
    return 0 if all(t for _, t in res) else 3

if __name__ == "__main__": sys.exit(main())
