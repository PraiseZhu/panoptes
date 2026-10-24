#!/usr/bin/env python3
"""prwatch — the Praise PR bot's dispatcher: picks work up within a minute instead of once a day (zero model, every minute).

1. New findings: a new/changed reports/merged/PR-N.json with P0/P1 -> one Slack DM to Praise (what, where, that the fix bot
   has it queued). Replaces the push half of the old Grok on-merge routine. First run only baselines (no back-fill).
   A report counts as new only when its CONTENT changed (sha1; mtime/size is just the cheap pre-check): 10-24 the restore re-touched
   every report file and 16 old findings came back as "new" DMs. The first run, and a change of this format, only baseline:
   every P0/P1 fingerprint present then is remembered as notified, nothing is sent.
2. Fix dispatch (only when takeover/prfix-event exists). Praise 10-24: "并发数量看内存 不设上限" -- as many prfix processes
   (one ticket each) as memory allows, no fixed cap. Every minute: when the inputs changed (reports, p2 rulings, ledger, notes,
   tickets) or 15 min passed or there was more work than runs, and no launched run is still picking its ticket, and
   headroom = MemAvailable - what the running fixes and a merge test may still grow to (learned peaks; from the 2nd fix on, a
   merge test that has not started yet counts too) >= ADMIT_FREE_MB + a fix's peak: ask `prfix.py --pending-json` and launch one more
   `run.sh prfix --next` detached. prfix claims tickets atomically, so parallel runs never take the same one.
   Every new fix PR action (tickets.json status pr_action_written; any of the parallel runs) is kicked to outbound at once.
3. Draft fix PRs the Pi line opened (pi-line marker, qa-fix/* branch; 10-24 -- not session/human drafts), every 2 min: every check
   except review-pr green -> gh.pr.ready action + immediate outbound kick (outbound re-verifies head and checks). Only whole-PR
   gates red: behind main -> update-branch (<= 6 per PR per day), else one rerun per head. A real red (Praise 10-24, "让 deepseek
   自主把修复 PR 推进到正式"): `run.sh prfix --revise N` -- the failed logs go back to the model, its fix is pushed onto the draft
   (gh.pr.push-fix); MAX_REVISIONS per PR, then prfix pauses it and tries again itself. Switch: takeover/prfix-revise (absent -> one DM per head).
5. Every 6 h: prfix --cleanup-stale --yes (finished tickets' worktrees).
4. Infra fixes (infrafix.py, QA-system faults in PraiseZhu/panoptes): infrafix.watch() follows open infra tickets every minute
   (gh at most every 2 min): PR found -> CI green -> gh.infra.merge action + kick; CI red -> DM; merged -> deploy check ->
   issue comment/close; first run after deploy -> verified / recurred / new fault (DM + suggest git revert). Dispatch (only when
   takeover/infrafix-event exists): inputs changed or 15 min -> `infrafix.py --pending-json`; work, memory >= 3G and no infrafix
   running -> launch `run.sh infrafix --next`; its action key (out/infrafix/last-result.json) is kicked to outbound at once.
Heartbeat: out/prwatch/state.json last_check (watchdog alerts when stale). Quiet log out/logs/prwatch-<day>.log (events only).
--dry-run: print decisions, write/launch/send nothing.
"""
import fcntl, hashlib, sys, time
from lib import *
import praise_notice as PN

DRY = "--dry-run" in sys.argv
STATE = f"{BASE}/out/prwatch/state.json"
LOCK_NIGHTLY = f"{QA}/argus-root/nightly.lock"
FIX_FLAG = f"{BASE}/takeover/prfix-event"
OK_CONCL = ("SUCCESS", "SKIPPED", "NEUTRAL")
BAD_CONCL = ("FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR")


def jload(f, d):
    try: return json.load(open(f))
    except Exception: return d


def qlog(msg):
    line = f"{now_cst().strftime('%F %T')} {msg}"
    if DRY: print(line); return
    os.makedirs(f"{BASE}/out/logs", exist_ok=True)
    with open(f"{BASE}/out/logs/prwatch-{today()}.log", "a") as f: f.write(line + "\n")


def lock_free(path):
    try: fd = open(path, "a")
    except OSError: return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); fcntl.flock(fd, fcntl.LOCK_UN); return True
    except BlockingIOError: return False
    finally: fd.close()


def mem_mb():
    for l in open("/proc/meminfo"):
        if l.startswith("MemAvailable"): return int(l.split()[1]) // 1024
    return 0


def kick_outbound(job, key):
    if DRY: qlog(f"DRY: would kick outbound --job {job} --key {key}"); return
    # outbound needs the Slack / Feishu secrets; only its own env gets them. prwatch's os.environ stays clean, so the prfix runs it
    # launches never carry secrets.env into the model's shell (10-24: secret_env() used to rewrite os.environ here)
    env = {**secret_env_dict(), **os.environ}
    with open(f"{BASE}/out/logs/prwatch-outbound-kicks.log", "a") as lf:
        subprocess.Popen([sys.executable, f"{PI_CODE}/bin/outbound.py", "--job", job, "--key", key], stdin=subprocess.DEVNULL,
                         stdout=lf, stderr=subprocess.STDOUT, start_new_session=True, env=env, cwd=f"{PI_CODE}/bin")
    qlog(f"kicked outbound {job} {key}")


def act(job, action):
    if DRY: qlog(f"DRY: would record {job} {action['type']} {action['key']}: {action.get('text', '')[:160]}"); return
    record_actions(job, today(), [action]); kick_outbound(job, action["key"])


# ---- 1. new findings -> DM ----
SIG_V = 2   # 2 = {m: mtime:size, h: sha1 of the content}; 1 (until 10-24) = "mtime:size" only
def scan_reports(st):
    seen = st.setdefault("reports", {})
    first = not st.get("baselined") or st.get("sig_v") != SIG_V    # first run / format change: baseline only, no DMs
    notified = st.setdefault("notified_fps", [])
    for f in sorted(glob.glob(f"{RPT}/PR-*.json")):
        k = os.path.basename(f)
        try: s = os.stat(f); m = f"{int(s.st_mtime)}:{s.st_size}"
        except OSError: continue
        old = seen.get(k) if isinstance(seen.get(k), dict) else {}
        if old.get("m") == m: continue
        try:
            with open(f, "rb") as fh: raw = fh.read()
        except OSError: continue
        h = hashlib.sha1(raw).hexdigest()
        if old.get("h") == h: seen[k] = {"m": m, "h": h}; continue    # touched (restore / copy), same content: nothing new
        try: doc = json.loads(raw)
        except ValueError: continue          # still being written: read it again next minute
        seen[k] = {"m": m, "h": h}
        for p in doc.get("prs", []):
            if (p.get("author") or "") != "PraiseZhu": continue   # Praise 10-23: others' PRs go to their handoff repos (daily bot), not to us
            xs = [dict(x, severity=x.get("severity") or x.get("severity_if_confirmed")) for x in (p.get("findings") or []) + (p.get("pending_verification") or [])]
            xs = [x for x in xs if x.get("severity") in ("P0", "P1") and x.get("fingerprint") not in notified]
            if not xs: continue
            if first: notified += [x["fingerprint"] for x in xs]; continue     # baseline: remember as notified, send nothing
            n = p.get("number"); who = p.get("author") or "?"
            # 2026-10-24（Praise：私信看不懂）：按 praise_notice 的骨架写；「1 条」这类数字保留（test_prwatch_dispatch）
            src = {"code-review": "AI 看代码发现的", "check": "自动测试跑出来的", "ai-test": "AI 补写的测试跑出来的", "stated-acceptance": "PR 自己写的验收没达到"}
            probs = [f"{i}. {(x.get('title') or x.get('check_id') or '')[:70]}（{x['severity']}，{src.get(x.get('source'), x.get('source') or '?')}）" for i, x in enumerate(xs[:5], 1)]
            lines = [PN.render(PN.item("fyi", f"你的 PR #{n} 合并后测出 {len(xs)} 条严重问题，修 PR bot 已经接手",
                f"你的 PR #{n} 合并后的测试新发现 {len(xs)} 条 {'/'.join(sorted({x['severity'] for x in xs}))} 问题：\n" + "\n".join(probs) + (f"\n…另 {len(xs) - 5} 条" if len(xs) > 5 else ""),
                "这些问题会留在主线上，直到修好。",
                todo="不用做什么。修 PR bot 已经接单，内存够就马上开修：先写一个能复现问题的测试，再开修复 PR；复现不了会另外告诉你",
                link=f"https://github.com/{REPO}/pull/{n}"))]
            # Praise 10-24 ("pr bot 如果有能力解决问题 不需要私信"): new findings on his PRs are the bot's work, not his -- no DM; a
            # DM comes only for kind 5 (sensitive files; 10-24 evening: the bot never stops for him otherwise)
            notified += [x["fingerprint"] for x in xs]
            qlog(f"new findings #{n}: {len(xs)} -> fix bot (no DM): {lines[0][:80]}")
    st["notified_fps"] = notified[-5000:]   # was 500: an evicted fingerprint would come back as "new" when its report is touched
    if first:
        st["baselined"] = now_cst().isoformat(timespec="seconds"); st["sig_v"] = SIG_V
        qlog(f"baselined {len(seen)} reports, {len(notified)} P0/P1 fingerprints remembered (no back-fill, sig v{SIG_V})")


# ---- 2. fix dispatch ----
def inputs_sig():
    parts = []
    for pat in (f"{RPT}/PR-*.json", "/workspace/fleet-standards/p2-rulings.md", f"{QA}/ledger/daily/*.json",
                "/workspace/praise-pr/notes/*", f"{BASE}/out/praise-pr/tickets.json"):
        for f in sorted(glob.glob(pat)):
            try: parts.append(f"{f}:{int(os.path.getmtime(f))}")
            except OSError: pass
    return hashlib.sha1("\n".join(parts).encode()).hexdigest()


ADMIT_FREE_MB = 1536    # free memory left even if every running job, a merge test and the new fix all hit their peaks at once
                        # (the live guard stays: prfix waits for MemAvailable >= 3 GB before every heavy step, Praise 10-23)
RUN_PEAK_MB = int(os.environ.get("PRFIX_RUN_PEAK_MB") or 2560)      # a fix's peak until 3 runs are observed (10-23 pre-push ~1-1.5 GB)
PANOPTES_PEAK_MB = int(os.environ.get("PANOPTES_PEAK_MB") or 6144)  # a panoptes run's peak until 3 runs are observed (10-23: ~6 GB)
RO_FLAGS = (b"--list", b"--pending-json", b"--cleanup-stale", b"--dry-run")   # never claim a ticket: not fix runs
# How many fixes run at once is decided by memory alone (Praise 10-24, no cap). The peaks are learned: every minute prwatch
# records each fix's and each panoptes run's process-tree RSS; once 3 finished runs are known, the estimate is the largest of
# the last 20 x 1.3 (minute samples miss short tsc / vitest spikes). A merge test may start any minute (cron :07, every merge),
# so from the second fix on its peak is reserved even while none runs. On this 16 GB box that usually means one fix beside a
# merge test (10-23 17:08 the box already dipped to 2.1 GB free); more memory -> more fixes.


def proc_table():
    """{pid: (ppid, rss MB, raw cmdline)} of every readable process."""
    page, t = os.sysconf("SC_PAGE_SIZE"), {}
    for d in os.listdir("/proc"):
        if not d.isdigit(): continue
        try:
            with open(f"/proc/{d}/stat") as f: ppid = int(f.read().rsplit(")", 1)[1].split()[1])
            with open(f"/proc/{d}/statm") as f: rss = int(f.read().split()[1]) * page // 1048576
            with open(f"/proc/{d}/cmdline", "rb") as f: cmd = f.read()
        except (OSError, ValueError, IndexError): continue
        t[int(d)] = (ppid, rss, cmd)
    return t


def tree(t, root):
    kids = {}
    for p, (pp, _, _) in t.items(): kids.setdefault(pp, []).append(p)
    out, stack = [], [root]
    while stack:
        p = stack.pop()
        if p in t and p not in out: out.append(p); stack += kids.get(p, [])
    return out


def prfix_runs(t):
    """-> (prfix.py ticket processes started by an interpreter, run.sh prfix wrappers whose prfix.py has not started yet: run.sh
    may wait for memory). Read-only / dry-run invocations and anything else naming the file (an editor, less) do not count."""
    a = lambda p: t[p][2].split(b"\0")
    def is_py(p):
        v = a(p)
        return len(v) > 1 and os.path.basename(v[0]).startswith(b"python") and v[1].endswith(b"bin/prfix.py") and not any(x in RO_FLAGS for x in v)
    def is_wrap(p):
        v = a(p)
        return len(v) > 2 and v[1].endswith(b"bin/run.sh") and v[2] == b"prfix" and not any(x in RO_FLAGS for x in v)
    pys = [p for p in t if is_py(p)]
    return pys, [w for w in t if is_wrap(w) and not set(tree(t, w)) & set(pys)]


def panoptes_procs(t):
    """every process under any run-nightly.sh (nested runs: catch-up -> node -> run-nightly --pr N), each counted once"""
    pan = set()
    for p, (_, _, c) in t.items():
        if b"panoptes/run-nightly.sh" in c: pan.update(tree(t, p))
    return pan


def learned(st, kind, default):
    h = ((st or {}).get("peaks") or {}).get(kind) or []
    return max(default // 4, int(max(h[-20:]) * 1.3)) if len(h) >= 3 else default


def sample_peaks(st, t):
    """Highest process-tree RSS of each running fix (prfix.py pid) and panoptes run (its top pid); when a run ends, its peak
    joins the history learned() uses. Runs that ended without real work (idle / busy exits) are not recorded."""
    pk = st.setdefault("peaks", {}); cur = st.setdefault("peaks_cur", {}); live = {}
    for p in prfix_runs(t)[0]: live[f"fix:{p}"] = sum(t[q][1] for q in tree(t, p))
    pan = panoptes_procs(t)
    if pan: live[f"panoptes:{min(p for p in pan if t[p][0] not in pan)}"] = sum(t[q][1] for q in pan)
    for k, v in live.items(): cur[k] = max(cur.get(k, 0), v)
    for k in [k for k in cur if k not in live]:
        kind, v = k.split(":", 1)[0], cur.pop(k)
        if v >= (700 if kind == "fix" else 1000): pk[kind] = (pk.get(kind, []) + [v])[-20:]


def headroom_mb(t, mem=None, st=None):
    """MemAvailable minus what is still to come: every running fix up to its learned peak (a wrapper not yet started counts in
    full), and a merge test up to its learned peak -- the running one's remaining growth or, once a fix is running, a whole one
    that may start any minute (cron :07, every merge)."""
    run_pk, pan_pk = learned(st, "fix", RUN_PEAK_MB), learned(st, "panoptes", PANOPTES_PEAK_MB)
    pys, waiting = prfix_runs(t)
    grow = sum(max(0, run_pk - sum(t[q][1] for q in tree(t, p))) for p in pys) + run_pk * len(waiting)
    pan = panoptes_procs(t)
    if pan: grow += max(0, pan_pk - sum(t[p][1] for p in pan))
    elif pys or waiting: grow += pan_pk
    return (mem_mb() if mem is None else mem) - grow


def kick_finished(st):
    """outbound at once for every fix PR action a prfix run wrote in the last day (parallel runs: tickets.json, not last-result)."""
    ks = st.setdefault("kicked", []); cut = (now_cst() - dt.timedelta(days=1)).isoformat()
    keys = [jload(f"{BASE}/out/praise-pr/last-result.json", {}).get("action_key")]
    tks = jload(f"{BASE}/out/praise-pr/tickets.json", {}).get("tickets", {})
    keys += [x.get("action_key") for x in tks.values() if x.get("status") in ("pr_action_written", "pushed") and (x.get("updated_at") or "") >= cut]
    # the only DMs left (kind 5, 10-24 evening): kick them like the PR actions
    keys += [f"needs-praise:{x.get('qa')}:{x.get('fp')}" for k, x in tks.items()
             if not k.startswith("rev:") and x.get("status") == "needs_praise" and (x.get("updated_at") or "") >= cut]
    keys += [f"revise-needs-praise:{x.get('pr')}:{(x.get('head') or '')[:12]}" for k, x in tks.items()
             if k.startswith("rev:") and x.get("status") == "needs_praise" and (x.get("updated_at") or "") >= cut]
    for k in keys:
        if k and k not in ks: kick_outbound("praise-pr", k); ks.append(k)
    st["kicked"] = ks[-500:]


DIGEST_HOUR = 9   # Beijing


def digest_parked(st, now=None):
    """Once a day (from 09:00): one fyi DM listing what the bot paused (kind 4) -- information only, nothing to answer; the bot keeps
    trying by itself. Nothing paused -> no message."""
    now = now or now_cst(); day = now.date().isoformat()
    if now.hour < DIGEST_HOUR or st.get("digest_day") == day: return
    tks = jload(f"{BASE}/out/praise-pr/tickets.json", {}).get("tickets", {})
    rows = []
    for k, t in sorted(tks.items()):
        if t.get("status") != "parked": continue
        what = f"修复 PR #{t.get('pr')} 的 CI / 冲突" if k.startswith("rev:") else f"#{t.get('qa')} 的 {t.get('sev') or ''} 问题"
        rows.append(f"- {what}：{t.get('park_reason') or '暂停'}（{(t.get('last_error') or '')[:70]}），{str(t.get('next_retry_at') or '')[5:16].replace('T', ' ')} 再试")
    st["digest_day"] = day
    if not rows: return
    act("prwatch", {"key": f"prbot-digest:{day}", "type": "slack.dm", "to": "Praise", "text": PN.text(PN.item("fyi",
        f"修 PR bot 暂时搁着 {len(rows)} 件事，会自己再试",
        "下面这些它这次没弄成，已经排好时间自己再试（主线有新代码、或换个会话从头修）：\n" + "\n".join(rows[:15]) + (f"\n…另 {len(rows) - 15} 件" if len(rows) > 15 else ""),
        todo="不用做什么。想让哪一件马上重试或者不再修，跟会话说一声就行"))})
    qlog(f"digest: {len(rows)} parked -> DM")


def dispatch_fix(st):
    kick_finished(st)
    t = proc_table(); sample_peaks(st, t)
    if not os.path.exists(FIX_FLAG): return
    pys, waiting = prfix_runs(t)
    claimed = {x.get("pid") for x in jload(f"{BASE}/out/praise-pr/tickets.json", {}).get("tickets", {}).values() if x.get("status") == "running"}
    picking = waiting + [p for p in pys if p not in claimed]
    if picking:   # a launched run is still picking its ticket: count again after it claims
        since = st.setdefault("fix_picking_since", time.time())
        if time.time() - since > 900 and not st.get("fix_picking_logged"):
            qlog(f"fix dispatch held {int((time.time() - since) / 60)} min by prfix process(es) that claimed no ticket: {picking}"); st["fix_picking_logged"] = True
        return
    st.pop("fix_picking_since", None); st.pop("fix_picking_logged", None)
    sig = inputs_sig()
    # a ticket whose back-off ran out since the last check is work now (10-24: #1188 waited up to 15 min more for the recheck)
    last = dt.datetime.fromtimestamp(st.get("fix_checked_at", 0), CST).isoformat(timespec="seconds"); now_s = now_cst().isoformat(timespec="seconds")
    retry_due = any(x.get("status") == "failed" and last < (x.get("next_retry_at") or "") <= now_s
                    for x in jload(f"{BASE}/out/praise-pr/tickets.json", {}).get("tickets", {}).values())
    due = sig != st.get("fix_inputs") or time.time() - st.get("fix_checked_at", 0) > 900 or st.get("fix_more") or retry_due
    if not due: return
    room, need = headroom_mb(t, st=st), ADMIT_FREE_MB + learned(st, "fix", RUN_PEAK_MB)
    if room < need:                                               # stay due; re-check next minute
        if st.get("fix_wait") != len(pys): qlog(f"fix dispatch waits for memory: headroom {room} MB < {need} MB, {len(pys)} fixing")
        st["fix_wait"] = len(pys)
        if st.get("fix_pending"): st.setdefault("fix_wait_since", time.time())   # work known to wait (watchdog: > 3 h -> tell him)
        return
    st.pop("fix_wait", None)
    r = subprocess.run([sys.executable, f"{PI_CODE}/bin/prfix.py", "--pending-json"], capture_output=True, text=True, timeout=600, cwd=f"{PI_CODE}/bin")
    st["fix_checked_at"] = time.time()
    try: pending = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None   # whole stdout is one JSON array
    except ValueError: pending = None
    if pending is None:
        qlog(f"prfix --pending-json failed rc={r.returncode}: {(r.stderr or r.stdout)[-160:]}"); return
    pending = [x for x in pending if not x.get("reason_if_skipped")]   # running tickets and entries for a human carry a reason
    st["fix_inputs"] = sig; st["fix_more"] = len(pending) > 1; st["fix_pending"] = len(pending)
    if not pending: st.pop("fix_wait_since", None); return
    top = pending[0]
    msg = f"(top #{top.get('qa')} {top.get('sev')} {top.get('fp')}, {len(pending)} todo, {len(pys)} already fixing, headroom {room} MB)"
    if DRY: qlog(f"DRY: would launch prfix --next {msg}"); return
    os.makedirs(f"{BASE}/out/logs", exist_ok=True)
    with open(f"{BASE}/out/logs/prwatch-launches.log", "a") as lf:
        subprocess.Popen([f"{PI_CODE}/bin/run.sh", "prfix", "--next"], stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT,
                         start_new_session=True, env=dict(os.environ, PRFIX_DISPATCHED="1"))
    st.pop("fix_wait_since", None); qlog(f"launched prfix --next {msg}")


# ---- 3. draft fix PRs: green -> ready; whole-PR gate races -> sync / rerun; real red -> the model fixes it (prfix --revise) ----
RACE_PRONE = ("pr-format-gate", "conflict sentinel", "pr-ci-freshness")   # whole-PR gates that can judge before the other checks finish
AGGREGATE = ("verify",)   # ci.yml `verify` only says "a needed job failed": red together with that job, it adds nothing
REVISE_FLAG = f"{BASE}/takeover/prfix-revise"
UB_PER_DAY = 6          # update-branch per PR per day (10-24: the old cap of 2 per PR for life ran out on #1257; main keeps moving)
MAX_REVISIONS = 2       # = prfix.MAX_REVISIONS
MAX_MERGES = 3          # = prfix.MAX_MERGES
PILINE = "<!-- pi-line fixpr:"


def pid_alive(pid):
    try: return bool(pid) and b"prfix" in open(f"/proc/{int(pid)}/cmdline", "rb").read()
    except (OSError, ValueError): return False


def revise_ticket(n):
    return jload(f"{BASE}/out/praise-pr/tickets.json", {}).get("tickets", {}).get(f"rev:{n}") or {}


def launch_revise(st, n, head):
    """prfix --revise N, admitted like a fix (learned peaks). -> 'launched' / 'wait' (memory, retry next round) / 'busy'."""
    t = proc_table()
    pys, waiting = prfix_runs(t)   # a run.sh wrapper still waiting for memory counts too (review 10-24: wrappers stacked up)
    if any(b"--revise" in t[q][2] and str(n).encode() in t[q][2].split(b"\0") for q in pys + waiting): return "busy"
    room, need = headroom_mb(t, st=st), ADMIT_FREE_MB + learned(st, "fix", RUN_PEAK_MB)
    if room < need: return "wait"
    if DRY: qlog(f"DRY: would launch prfix --revise {n} (head {head[:9]})"); return "launched"
    os.makedirs(f"{BASE}/out/logs", exist_ok=True)
    with open(f"{BASE}/out/logs/prwatch-launches.log", "a") as lf:
        subprocess.Popen([f"{PI_CODE}/bin/run.sh", "prfix", "--revise", str(n)], stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT,
                         start_new_session=True, env=dict(os.environ, PRFIX_DISPATCHED="1"))
    qlog(f"#{n} CI red -> launched prfix --revise {n} (head {head[:9]}, headroom {room} MB)"); return "launched"


def alert(st, n, why="red"):
    """remember: a DM asked him to act on bot fix PR #n -- why = red (CI) / dirty (conflict) -- (a 🟢 follows once it gets done)."""
    st.setdefault("alerted", {}).setdefault(str(n), why)


def pr_now(n):
    p = gh(["pr", "view", str(n), "-R", REPO, "--json", "state,isDraft,mergeStateStatus"])
    if p.get("state") == "MERGED": return "已合并"
    if p.get("state") == "OPEN" and not p.get("isDraft"): return "已转正式" + ("（现在跟 main 有冲突，归 Mac mini 盯梢跟进）" if (p.get("mergeStateStatus") or "").upper() == "DIRTY" else "")
    return None if p.get("state") == "OPEN" else "closed"


def resolved(st):
    """Praise 10-24 (red DMs from before the bot fixed things looked like it was still stuck): every 10 min, one 🟢 DM for what a
    🔴 asked him to handle and the bot then finished on its own -- a needs_human / norepro ticket that later got its fix PR, or a fix
    PR whose red CI / conflict DM was followed by ready or merged. First run: also the alerts already in draft_seen."""
    if time.time() - st.get("resolved_at", 0) < 600: return
    st["resolved_at"] = time.time()
    al = st.setdefault("alerted", {}); sent = st.setdefault("resolved_sent", [])
    if not st.get("alerted_backfilled"):
        for tag, v in (st.get("draft_seen") or {}).items():
            if v in ("red", "dirty"): al.setdefault(tag.split(":")[0], "backfill")
        st["alerted_backfilled"] = True
    items, done_keys = [], []
    ob = jload(f"{BASE}/out/outbound/ledger.json", {})
    for k, t in jload(f"{BASE}/out/praise-pr/tickets.json", {}).get("tickets", {}).items():
        if k.startswith("rev:") or k in sent or t.get("status") != "pr_action_written": continue
        if not any(h.get("status") in ("needs_human", "norepro", "needs_praise") for h in t.get("history") or []): continue   # was 🔴-DM'd
        r = ob.get(t.get("action_key")) or {}
        m = re.search(r"/pull/(\d+)", r.get("result") or "")
        if r.get("status") != "done" or not m: continue
        pr = int(m.group(1)); now = pr_now(pr)
        items.append(PN.item("resolved", f"#{t.get('qa')} 的问题 bot 后来自己修好了",
                             f"之前私信说修 PR bot 修不动 #{t.get('qa')} 的 {t.get('sev') or ''} 问题「{(t.get('title') or '')[:50]}」；它后来重修成功，修复 PR #{pr} 已开"
                             + (f"，{now}" if now and now != "closed" else "（草稿，自动测试过了就转正式）") + "。",
                             link=f"https://github.com/{REPO}/pull/{pr}"))
        done_keys.append(k)
        al.pop(str(pr), None)
    for n, since in list(al.items()):
        try: now = pr_now(int(n))
        except Exception: continue
        if now is None: continue          # still an open draft: not done yet
        al.pop(n, None)
        if now == "closed" or f"pr:{n}" in sent: continue
        was = "跟 main 冲突" if since == "dirty" else "自动测试没过"
        items.append(PN.item("resolved", f"修复 PR #{n} 后来弄好了", f"之前私信说 bot 开的修复 PR #{n} {was}；后来处理好了，现在{now}。",
                             link=f"https://github.com/{REPO}/pull/{n}"))
        done_keys.append(f"pr:{n}")
    if not items: return
    key = "resolved:" + hashlib.sha1(",".join(sorted(done_keys)).encode()).hexdigest()[:10]
    act("prwatch", {"key": key, "type": "slack.dm", "to": "Praise", "text": PN.text(*items)})
    st["resolved_sent"] = (sent + done_keys)[-500:]; qlog(f"resolved -> DM {key}: {done_keys}")


def revise_or_wait(st, done, n, head, tag, seen, rv, cnt_key, cap, what):
    """hand this head to prfix --revise (CI fix or main merge). -> True when it is in hand (launched / running / waiting), False when
    the switch is off, the rounds are used up or prfix stopped at this head (the caller then DMs)."""
    # 10-24 evening (Praise: 自主推进别来问我): prfix --revise never stops for a human any more -- it pauses (parked) and tries again
    # by itself; only the switch being off, or a kind-5 question (sensitive files, prfix DMs that itself), leaves it to the caller
    if not os.path.exists(REVISE_FLAG): return False
    if rv.get("status") == "needs_praise" and rv.get("head") == head: return True
    if rv.get("status") == "parked" and (rv.get("next_retry_at") or "") > now_cst().isoformat(): return True
    dead = rv.get("status") == "running" and not pid_alive(rv.get("pid"))   # prfix --revise records it as a transient failure when it starts
    if rv.get("status") == "pushed" and rv.get("last_head") == head and rv.get("rerun_only") \
            and (jload(f"{BASE}/out/outbound/ledger.json", {}).get(rv.get("action_key")) or {}).get("status") == "done":
        pass   # the ruled re-run of this head happened and the checks are red again: not in hand any more (another round / a human)
    elif (rv.get("status") == "running" and not dead) or (rv.get("status") == "pushed" and rv.get("last_head") == head): return True   # in hand
    if rv.get("status") == "failed" and rv.get("head") == head and (rv.get("next_retry_at") or "") > now_cst().isoformat(): return True
    lt = st.setdefault("revise_launched", {})
    try: ran_after = rv.get("status") == "failed" and rv.get("head") == head and dt.datetime.fromisoformat(rv.get("updated_at")).timestamp() > lt.get(tag, 0)
    except (TypeError, ValueError): ran_after = False
    if seen == "revise" and time.time() - lt.get(tag, 0) < 1800 and not ran_after:
        return True   # launched for this head and not finished (or died): again only after 30 min, or once it has recorded a failure
    r = launch_revise(st, n, head)
    if r == "launched": done[tag] = "revise"; lt[tag] = time.time(); st["revise_launched"] = dict(list(lt.items())[-100:])
    elif r == "wait" and st.get("revise_wait") != tag: st["revise_wait"] = tag; qlog(f"#{n} {what} -> revise waits for memory")
    return True


def red_dm(st, n, qa, tag, red, how):
    alert(st, n)
    act("prwatch", {"key": f"cired:{tag}", "type": "slack.dm", "to": "Praise",
                    "text": PN.text(PN.item("action", f"修复 PR #{n} 的自动测试没过",
                        f"修 PR bot 为 #{qa} 开的修复 PR #{n}（还是草稿），GitHub 上的自动测试没过：{', '.join(red[:5])}。{how}",
                        "这个修复 PR 合不了，#" + str(qa) + " 的问题还在。",
                        todo=["打开 PR 看哪项没过", "需要改代码的话，" + PN.ask_session(f"修复草稿 PR #{n}（QA: #{qa}）CI 红：{', '.join(red[:5])}，看失败原因，修好推上去")],
                        link=f"https://github.com/{REPO}/pull/{n}"))})


def check_drafts(st):
    if time.time() - st.get("drafts_at", 0) < 110: return
    st["drafts_at"] = time.time()
    prs = gh(["pr", "list", "-R", REPO, "--author", "PraiseZhu", "--draft", "--state", "open", "--limit", "50",
              "--json", "number,headRefOid,headRefName,body,statusCheckRollup,mergeStateStatus"])
    done = st.setdefault("draft_seen", {}); ub = st.setdefault("update_branch_day", {}); day = today()
    for p in prs:
        # 10-24: only the fix PRs the Pi line opened. Session / human drafts with "QA: #N" (#1166) are not ours to ready or fix, and a
        # PR the Mac mini watcher turned back into a draft while it fixes it must not be readied mid-fix.
        if PILINE not in (p.get("body") or "") or not (p.get("headRefName") or "").startswith("qa-fix/"): continue
        n, head = p["number"], p["headRefOid"]
        cs = [c for c in p.get("statusCheckRollup") or [] if "review-pr" not in (c.get("name") or c.get("context") or "")]
        concl = [((c.get("conclusion") or c.get("state") or "").upper(), (c.get("status") or "COMPLETED").upper(), c.get("name") or c.get("context")) for c in cs]
        tag = f"{n}:{head[:12]}"; seen = done.get(tag)
        qa = (re.search(r"QA[:：]\s*#(\d+)", p.get("body") or "") or [None, "?"])[1]
        if (p.get("mergeStateStatus") or "").upper() == "DIRTY":
            # conflicts with main: GitHub runs few or no checks and update-branch cannot merge -> prfix --revise merges main in and the
            # model resolves the conflicts (10-24); a DM only when that is off / used up / stopped at this head
            if seen in ("ready", "red", "dirty"): continue
            if revise_or_wait(st, done, n, head, tag, seen, revise_ticket(n), "merges", MAX_MERGES, "conflicts with main"): continue
            if seen not in ("ready", "red", "dirty"):
                alert(st, n, "dirty")
                act("prwatch", {"key": f"dirty:{tag}", "type": "slack.dm", "to": "Praise", "text": PN.text(PN.item("action", f"修复 PR #{n} 跟 main 冲突了",
                    f"修 PR bot 为 #{qa} 开的修复 PR #{n}（草稿）和最新 main 有冲突，GitHub 不再跑完整测试；bot 自己合 main 解冲突已经停下了（或开关关着）。",
                    f"这个修复 PR 停住了，#{qa} 的问题还在。",
                    todo=PN.ask_session(f"修复草稿 PR #{n}（QA: #{qa}）跟 main 冲突，把 main 合进来解决冲突后推到它的分支上"),
                    link=f"https://github.com/{REPO}/pull/{n}"))})
                done[tag] = "dirty"; qlog(f"#{n} conflicts with main -> DM")
            continue
        if not concl or any(s != "COMPLETED" or v in ("", "PENDING", "EXPECTED") for v, s, _ in concl): continue   # still running
        red = [nm for v, s, nm in concl if v in BAD_CONCL]
        if seen in ("ready", "red", "dirty"): continue
        if not red and all(v in OK_CONCL for v, _, _ in concl):
            act("praise-pr", {"key": f"ready:{n}:{head[:12]}", "type": "gh.pr.ready", "pr": n, "head": head,
                              "cmd": f"gh pr ready {n} --repo {REPO}", "dedupe": "outbound re-checks isDraft, head and checks"})
            done[tag] = "ready"; qlog(f"#{n} all checks but review-pr green -> ready"); continue
        if not red: continue
        if any(not nm.startswith(AGGREGATE) for nm in red): red = [nm for nm in red if not nm.startswith(AGGREGATE)]
        behind = (p.get("mergeStateStatus") or "").upper() == "BEHIND"
        cnt = ub.get(str(n)) if (ub.get(str(n)) or {}).get("day") == day else {"day": day, "n": 0}
        if all(nm.startswith(RACE_PRONE) for nm in red):
            # gates that judge the whole check set (10-23 #1250, #1257): when the PR is behind main they want evidence on the current
            # base/head merge -> update-branch at once (the repo's own pr-gate-rerun already re-ran them; prwatch's reruns fixed 0 of 4);
            # not behind -> one rerun per head, then it counts as a real red
            uat = st.setdefault("ub_at", {})
            if seen == "ub" and behind and time.time() - uat.get(tag, 0) < 1800: continue   # update-branch on its way; new head = new tag
            if behind and cnt["n"] < UB_PER_DAY:   # (again after 30 min when that update-branch never landed)
                act("praise-pr", {"key": f"update-branch:{tag}" + (f":{cnt['n']}" if seen == "ub" else ""), "type": "gh.pr.update-branch", "pr": n, "head": head})
                cnt["n"] += 1; ub[str(n)] = cnt; done[tag] = "ub"; uat[tag] = time.time(); st["ub_at"] = dict(list(uat.items())[-100:])
                qlog(f"#{n} race-prone gates red {red}, behind main -> update-branch ({cnt['n']}/{UB_PER_DAY} today)"); continue
            if not behind and seen is None:
                act("praise-pr", {"key": f"rerun:{tag}", "type": "gh.run.rerun-failed", "pr": n, "head": head, "names": red})
                done[tag] = "rerun"; qlog(f"#{n} only race-prone gates red {red} -> rerun once"); continue
            # still red after the rerun / out of syncs: nothing for a model or for Praise (kind 3: it waits for the CI / main) -- re-run
            # the gates again every 3 h; the daily update-branch budget refills tomorrow (10-24 evening: no DM)
            rw = st.setdefault("race_wait", {})
            if time.time() - rw.setdefault(tag, time.time()) >= 3 * 3600:
                act("praise-pr", {"key": f"rerun:{tag}:{now_cst():%m%d%H}", "type": "gh.run.rerun-failed", "pr": n, "head": head, "names": red})
                rw[tag] = time.time(); qlog(f"#{n} whole-PR gates still red {red} -> rerun again (every 3 h)")
            st["race_wait"] = dict(list(rw.items())[-100:]); continue
        # a real red: the model that wrote the fix gets the failed logs (prfix --revise), up to MAX_REVISIONS rounds per PR
        rv = revise_ticket(n)
        dead = rv.get("status") == "running" and not pid_alive(rv.get("pid"))   # prfix --revise records it as a transient failure when it starts
        real = [nm for nm in red if not nm.startswith(RACE_PRONE)]
        if real and revise_or_wait(st, done, n, head, tag, seen, rv, "revisions", MAX_REVISIONS, f"CI red {red[:4]}"): continue
        if rv.get("status") == "needs_praise" and rv.get("head") == head:
            done[tag] = "red"; alert(st, n); continue    # kind 5: prfix --revise asked him
        how = "自动修 CI 的开关关着（takeover/prfix-revise），所以直接告诉你。"   # the only way here since 10-24 evening
        red_dm(st, n, qa, tag, red, how); done[tag] = "red"; qlog(f"#{n} CI red {red[:5]} -> DM")
    st["draft_seen"] = dict(list(done.items())[-300:])
    st.pop("update_branch", None)     # the old per-PR lifetime counter (2 per PR) is gone


# ---- 4. infra fixes (infrafix.py) ----
INFRA_FLAG = f"{BASE}/takeover/infrafix-event"


def infra_inputs_sig():
    parts = []   # control.log / nightly logs left out on purpose: they change every minute while panoptes runs (15-min recheck covers them)
    for pat in (f"{RPT}/PR-*.json", f"{RPT}/history/PR-*.json", f"{QA}/reports/catch-up/state.json", f"{QA}/reports/catch-up/run-*.json",
                f"{PI_CODE}/conf/gates.json", "/workspace/praise-pr/notes/infra-*", f"{BASE}/out/infrafix/tickets.json"):
        for f in sorted(glob.glob(pat)):
            try: parts.append(f"{f}:{int(os.path.getmtime(f))}")
            except OSError: pass
    return hashlib.sha1("\n".join(parts).encode()).hexdigest()


def dispatch_infra(st):
    s = st.setdefault("infra", {})
    res = jload(f"{BASE}/out/infrafix/last-result.json", {})
    k = res.get("action_key")
    if k and k not in s.setdefault("kicked", []):
        kick_outbound("infrafix", k); s["kicked"] = (s["kicked"] + [k])[-200:]
    import infrafix
    for e in infrafix.watch(s, kick=kick_outbound, dry=DRY): qlog(f"infra: {e}")   # PR / CI / merge / deploy follow-up
    if not os.path.exists(INFRA_FLAG): return
    if not lock_free("/tmp/pi-fallback-infrafix.lock") or not lock_free(f"{BASE}/out/infrafix/infrafix.run.lock"): return
    sig = infra_inputs_sig()
    due = sig != s.get("inputs") or time.time() - s.get("checked_at", 0) > 900 or s.get("more")
    if not due: return
    if mem_mb() < 3072: return      # stay due; re-check next minute (Praise: memory is the only gate, no panoptes lock)
    r = subprocess.run([sys.executable, f"{PI_CODE}/bin/infrafix.py", "--pending-json"], capture_output=True, text=True, timeout=600, cwd=f"{PI_CODE}/bin")
    s["checked_at"] = time.time()
    try: pending = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None
    except ValueError: pending = None
    if pending is None:
        qlog(f"infrafix --pending-json failed rc={r.returncode}: {(r.stderr or r.stdout)[-160:]}"); return
    todo = [x for x in pending if not x.get("reason_if_skipped")]   # held / watch / backoff rows carry a reason
    s["inputs"] = sig; s["more"] = len(todo) > 1
    if not todo: return
    top = todo[0]
    if DRY: qlog(f"DRY: would launch infrafix --next (top {top.get('sig8')} {top.get('cat')}/{top.get('stage')}, {len(todo)} pending)"); return
    with open(f"{BASE}/out/logs/prwatch-launches.log", "a") as lf:
        subprocess.Popen([f"{PI_CODE}/bin/run.sh", "infrafix", "--next"], stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT,
                         start_new_session=True)
    qlog(f"launched infrafix --next (top {top.get('sig8')} {top.get('cat')}/{top.get('stage')}, {len(todo)} pending)")


def cleanup(st):
    """every 6 h: prfix --cleanup-stale --yes (10-24: worktrees of finished tickets stayed, ~440 MB each; running tickets are kept)."""
    if time.time() - st.get("cleanup_at", 0) < 6 * 3600: return
    st["cleanup_at"] = time.time()
    if DRY: qlog("DRY: would run prfix --cleanup-stale --yes"); return
    with open(f"{BASE}/out/logs/prwatch-cleanup.log", "a") as lf:
        subprocess.Popen([sys.executable, f"{PI_CODE}/bin/prfix.py", "--cleanup-stale", "--yes"], stdin=subprocess.DEVNULL, stdout=lf,
                         stderr=subprocess.STDOUT, start_new_session=True, cwd=f"{PI_CODE}/bin")


def main():
    if mode() != "pi": return 0
    lk = open("/tmp/pi-fallback-prwatch.lock", "w")
    try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: return 0
    st = jload(STATE, {})
    for name, fn in (("reports", scan_reports), ("fix", dispatch_fix), ("drafts", check_drafts), ("resolved", resolved), ("infra", dispatch_infra), ("cleanup", cleanup), ("digest", digest_parked)):
        try: fn(st)
        except Exception as e:
            st.setdefault("errors", {})[name] = f"{now_cst().strftime('%F %T')} {str(e)[:200]}"; qlog(f"{name} error: {str(e)[:200]}")
    st["last_check"] = now_cst().strftime("%Y-%m-%d %H:%M:%S CST")
    if not DRY:
        os.makedirs(os.path.dirname(STATE), exist_ok=True); tmp = f"{STATE}.{os.getpid()}"
        open(tmp, "w").write(json.dumps(st, ensure_ascii=False, indent=1)); os.replace(tmp, STATE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
