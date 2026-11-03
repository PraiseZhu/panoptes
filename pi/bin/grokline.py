#!/usr/bin/env python3
"""Grok line (Praise PR bot) overflow: claim rules + usage gate (fleet-standards/Praise-PR.md "Grok 线用量标准", concurrency plan §3).
The Grok side runs elsewhere (CheckSubscriptionUsage, clone, npm, vitest all on its cloud); this file is what the box keeps: who owns
which ticket in tickets.json, the usage record, and the gate. Nothing heavy runs here.
Switch: PI_STATE/takeover/prfix-grok-overflow (present = on, re-read on every call). rm it -> prwatch's next sweep puts every
  Grok-claimed ticket without a PR back in the Pi queue (owner cleared), --claim refuses, Pi stops yielding killed tickets.
Usage gate (pure: gate()): weekly % from CheckSubscriptionUsage, written here by the bot (--claim/--record/--finish --pct).
  baseline = first record of the Beijing day (lowered if a later record is smaller, e.g. the weekly reset).
  rise >= 10 -> stop: every Grok ticket goes back to Pi now; rise >= 8 or week >= 70 -> hold: no new ticket, running ones finish.
When (may_open()): switch on, gate open, and either both Pi slots busy with Pi-line todo still queued, or a ticket Pi handed
  over (emergency-killed > 3 times today, prfix sets grok_wanted).
Claim: --claim --pct P takes the first todo in Pi's own priority order under tickets.json's flock: owner=grok, pid=null,
  host=cloud, status=running, usage_start. Pi never takes owner=grok. --progress KEY keeps it; 90 min without -> back to Pi.
  --finish KEY --pct P --pr N (draft fix PR opened: status pr_action_written, owner cleared, finished_by=grok, usage_end);
  --release KEY --pct P (gave up: back to the Pi queue).
Records: out/praise-pr/grok-usage.json (per day: baseline, samples, opened, done, trips); one line per day in
  out/praise-pr/grok-daily.log from 23:59 Beijing (prwatch sweep): baseline, close, rise, opened, done, trips.
  A trip (stop or week >= 70) sets pm_report_due for the day: the bot reports it to the test PM the same day (--check shows it).
Usage: grokline.py gate --dry-run (read-only) | pr-meta --key QA:FP [--title T] (read-only) | --check | --record --pct P | --claim --pct P | --progress KEY | --finish KEY --pct P --pr N | --release KEY --pct P
Output: one JSON line on stdout."""
import argparse, contextlib, fcntl, sys
from lib import *
import prfix

HOLD_RISE, STOP_RISE, WEEK_CAP = 8.0, 10.0, 70.0
STALE_MIN = 90
PI_SLOTS = 2                     # = prwatch.MAX_FIXES
GROK_AUTHORS = ("PraiseZhu", "kirozeng")   # 11-03: Grok claims only tickets whose source PR is by one of these


def on(): return prfix.grok_on()
def usage_path(): return f"{prfix.OUT}/grok-usage.json"
def daily_path(): return f"{prfix.OUT}/grok-daily.log"


# ---------------------------------------------------------------- pure rules
def gate(baseline, cur):
    """-> (open | hold | stop, reason). No record today -> hold (fail closed)."""
    if baseline is None or cur is None: return "hold", "今天还没有用量记录"
    rise = round(cur - baseline, 2)
    if rise >= STOP_RISE: return "stop", f"今天已涨 {rise} 点 >= {STOP_RISE:g}：停掉所有 Grok 线单，退回 Pi 线"
    if rise >= HOLD_RISE: return "hold", f"今天已涨 {rise} 点 >= {HOLD_RISE:g}：不开新单，只做完在跑的"
    if cur >= WEEK_CAP: return "hold", f"每周用量 {cur}% >= {WEEK_CAP:g}%：本周不开新单"
    return "open", f"今天涨 {rise} 点，每周 {cur}%"


def may_open(switch, g, pi_running, pi_todo, wanted):
    """-> (bool, reason). g = gate()[0]; pi_running = live Pi-line fixes; pi_todo = todo tickets nobody runs; wanted = tickets Pi
    handed over today."""
    if not switch: return False, "开关关着（takeover/prfix-grok-overflow 不在）"
    if g != "open": return False, f"用量闸门 {g}"
    if wanted: return True, f"{wanted} 张单 Pi 今天被杀超过 {prfix.KILLS_TO_GROK} 次，交给 Grok"
    if pi_todo and pi_running >= PI_SLOTS: return True, f"Pi 线 {pi_running} 个位置都满，还有 {pi_todo} 张待修"
    return False, ("没有积压" if not pi_todo else f"Pi 线只占了 {pi_running}/{PI_SLOTS} 个位置")


def stale(t, now):
    """a Grok claim with no progress for STALE_MIN minutes"""
    last = prfix.ts(t.get("progress_at") or t.get("started_at"))
    return not last or now - last > dt.timedelta(minutes=STALE_MIN)


def release(t, why, now):
    """Grok gives a ticket back: Pi may take it at once (status queued, owner cleared)."""
    t = dict(t); stamp = now.isoformat(timespec="seconds")
    for k in ("owner", "host", "progress_at"): t.pop(k, None)
    t.update(status="queued", pid=None, updated_at=stamp, last_error=f"Grok 线退回：{why}")
    t["history"] = (list(t.get("history") or []) + [{"at": stamp, "status": "grok_released", "error": why[:200]}])[-20:]
    return t


# ---------------------------------------------------------------- usage record (own flock)
@contextlib.contextmanager
def usage_locked():
    os.makedirs(prfix.OUT, exist_ok=True)
    lk = open(usage_path() + ".lock", "w"); fcntl.flock(lk, fcntl.LOCK_EX)
    try:
        doc = json.load(open(usage_path())) if os.path.exists(usage_path()) else {}
        doc.setdefault("days", {})
        yield doc
        write_atomic(usage_path(), json.dumps(doc, ensure_ascii=False, indent=1))
    finally:
        fcntl.flock(lk, fcntl.LOCK_UN); lk.close()


def record(doc, pct, now, opened=0, done=0):
    """one usage sample into doc (in place) -> today's entry"""
    d = doc["days"].setdefault(now.date().isoformat(), {"baseline": None, "samples": [], "opened": 0, "done": 0, "trips": []})
    if pct is not None:
        pct = float(pct)
        d["baseline"] = pct if d["baseline"] is None else min(d["baseline"], pct)
        d["samples"] = (d["samples"] + [[now.isoformat(timespec="seconds"), pct]])[-200:]
        g, why = gate(d["baseline"], pct)
        trip = "stop" if g == "stop" else ("week70" if pct >= WEEK_CAP else None)
        if trip and trip not in d["trips"]: d["trips"].append(trip); d["pm_report_due"] = True
    d["opened"] += opened; d["done"] += done
    return d


def day_gate(d):
    cur = d["samples"][-1][1] if d and d.get("samples") else None
    return gate((d or {}).get("baseline"), cur)


def summary_line(day, d):
    close = d["samples"][-1][1] if d.get("samples") else None
    rise = round(close - d["baseline"], 2) if close is not None and d.get("baseline") is not None else None
    return (f"{day} baseline={d.get('baseline')} close={close} rise={rise} opened={d.get('opened', 0)} done={d.get('done', 0)} "
            f"trips={','.join(d.get('trips') or []) or '-'}")


# ---------------------------------------------------------------- tickets
def pi_running(tks):
    return sum(1 for t in tks.values() if t.get("status") == "running" and t.get("owner") != "grok" and prfix.pid_alive(t.get("pid")))


def queue(now):
    """Pi's view (same evaluate + priority): -> (todo keys, handed-over keys)"""
    vs = [v for v in prfix.evaluate_all(prfix.gather(), now) if qualifies(v)]   # backlog cleared -> both empty -> no claim
    todo = [v["key"] for v in vs if v["state"] == "todo"]
    wanted = [v["key"] for v in vs if v["state"] == "skip" and v["skips"] == [prfix.GROK_HANDOVER]]
    return todo, wanted


def qualifies(v):
    """a ticket Grok may take: its source PR author is in GROK_AUTHORS"""
    return ((v.get("c") or {}).get("author") or "") in GROK_AUTHORS


def pr_meta(key, title=None):
    """Pi-line conventions for a Grok fix PR (same builders as outbound/prfix), so prwatch.check_drafts, prfix --revise and the
    6h ownership check (lib.PI_MARK_RE) treat it as the Pi line's own. key = '<qa>:<fp>' (tickets.json key)."""
    import outbound
    n, fp = key.split(":", 1); n = int(n); fk = f"fixpr:{n}:{fp}"
    trailer = outbound.pi_open_trailer(fk)
    return {"branch": "qa-fix/" + fk.replace(":", "-")[:60], "pr_body_block": f"QA: #{n}\nFingerprint: {fp}\n\n<!-- pi-line {fk} -->",
            "commit_trailer": trailer, "commit_message": f"{title or f'fix: QA #{n}'}\n\n{trailer}"}


def check(now=None):
    now = now or now_cst()
    doc = json.load(open(usage_path())) if os.path.exists(usage_path()) else {"days": {}}
    d = doc["days"].get(now.date().isoformat()) or {}
    g, gwhy = day_gate(d)
    tks = prfix.load_tickets().get("tickets", {})
    todo, wanted = queue(now)
    ok, why = may_open(on(), g, pi_running(tks), len(todo), len(wanted))
    return {"switch": on(), "gate": g, "gate_reason": gwhy, "may_open": ok, "reason": why, "pi_running": pi_running(tks),
            "pi_todo": len(todo), "handed_over": wanted, "grok": sorted(k for k, t in tks.items() if t.get("owner") == "grok"),
            "today": d, "pm_report_due": bool(d.get("pm_report_due"))}


def claim(pct, now=None):
    """-> result dict; the ticket is claimed only when may_open (usage checked with THIS pct first)."""
    now = now or now_cst()
    with usage_locked() as U: d = dict(record(U, pct, now))
    g, gwhy = day_gate(d)
    todo, wanted = queue(now)
    ok, why = may_open(on(), g, pi_running(prfix.load_tickets().get("tickets", {})), len(todo), len(wanted))
    if not ok: return {"status": "no", "reason": why, "gate": g, "gate_reason": gwhy}
    with prfix.tickets_locked() as T:   # re-read under the flock: Pi may have taken a ticket since queue()
        tks = T["tickets"]; key = None
        for k in wanted + todo:
            cur = tks.get(k) or {}
            if cur.get("owner") == "grok" or (cur.get("status") == "running" and prfix.pid_alive(cur.get("pid"))): continue
            key = k; break
        if not key: return {"status": "no", "reason": "没有可认领的待修单（都被 Pi 或 Grok 占着）", "gate": g}
        n, fp = key.split(":", 1)
        t = dict(tks.get(key) or {"qa": int(n), "fp": fp, "history": []})
        stamp = now.isoformat(timespec="seconds")
        t.update(status="running", owner="grok", pid=None, host="cloud", started_at=stamp, progress_at=stamp, updated_at=stamp,
                 usage_start=float(pct), attempts=int(t.get("attempts") or 0) + 1)
        tks[key] = t
    with usage_locked() as U: record(U, None, now, opened=1)
    return {"status": "claimed", "ticket": key, "qa": t["qa"], "fp": t["fp"], "reason": why, "gate": g}


def _own(key, fn, now):
    with prfix.tickets_locked() as T:
        t = T["tickets"].get(key)
        if not t or t.get("owner") != "grok": return {"status": "no", "reason": f"{key} 不是 Grok 线认领的单"}
        T["tickets"][key] = fn(dict(t), now.isoformat(timespec="seconds"))
        return {"status": "ok", "ticket": key, "now": T["tickets"][key].get("status")}


def progress(key, now=None):
    """keep a claim alive; with the switch off or the usage gate at stop the ticket goes back to Pi right here (status stop:
    the Grok side must stop working on it), not a minute later in prwatch's sweep."""
    now = now or now_cst()
    try:
        doc = jload_usage()
        g = day_gate(doc["days"].get(now.date().isoformat()))[0]
    except Exception: g = "stop"   # unreadable usage file: fail closed
    if not on() or g == "stop":
        why = "开关关了" if not on() else "用量闸门 stop（今天涨 >= 10 点）"
        r = _own(key, lambda t, s: release(t, why, now), now)
        return dict(r, status="stop", reason=why) if r["status"] == "ok" else r
    return _own(key, lambda t, s: dict(t, progress_at=s, updated_at=s), now)


def jload_usage():
    if not os.path.exists(usage_path()): return {"days": {}}
    with open(usage_path()) as f: return json.load(f)


def gate_dry_run(now=None):
    """read-only: the gate decision and its inputs (no usage record, no tickets write). Any bad input -> hold, never a crash."""
    now = now or now_cst(); out = {"status": "dry-run", "now": now.isoformat(timespec="seconds")}
    try:
        doc = jload_usage()
        d = doc["days"].get(now.date().isoformat()) or {}
        cur = d["samples"][-1][1] if d.get("samples") else None
        g, gwhy = day_gate(d)
        out.update(switch=on(), baseline=d.get("baseline"), current=cur, gate=g, gate_reason=gwhy)
    except Exception as e:
        out.update(switch=None, gate="hold", gate_reason=f"用量记录读不了，按 hold：{str(e)[:120]}"); g = "hold"
    try:
        tks = prfix.load_tickets().get("tickets", {}); todo, wanted = queue(now)
        ok, why = may_open(bool(out["switch"]), g, pi_running(tks), len(todo), len(wanted))
        out.update(pi_running=pi_running(tks), qualifying_todo=todo, handed_over=wanted, authors=list(GROK_AUTHORS),
                   grok=sorted(k for k, t in tks.items() if t.get("owner") == "grok"), may_open=ok, reason=why,
                   would_claim=(wanted + todo)[0] if ok and (wanted + todo) else None)
    except Exception as e:
        out.update(gate="hold", may_open=False, would_claim=None, reason=f"工单队列读不了，按 hold：{str(e)[:120]}")
    if out.get("gate") not in ("open", "hold", "stop"): out["gate"] = "hold"
    return out


def finish(key, pct, pr, now=None):
    now = now or now_cst()
    def fn(t, s):
        for k in ("owner", "host", "progress_at"): t.pop(k, None)
        t.update(status="pr_action_written", action_key=None, pr=int(pr), finished_by="grok", usage_end=float(pct), pid=None,
                 updated_at=s, last_error=None, next_retry_at=None)
        t["history"] = (list(t.get("history") or []) + [{"at": s, "status": "grok_pr", "error": f"#{pr}"}])[-20:]
        return t
    r = _own(key, fn, now)
    if r["status"] == "ok":
        with usage_locked() as U: record(U, pct, now, done=1)
    return r


def give_back(key, pct, why, now=None):
    now = now or now_cst()
    r = _own(key, lambda t, s: dict(release(t, why, now), usage_end=float(pct) if pct is not None else None), now)
    if pct is not None and r["status"] == "ok":
        with usage_locked() as U: record(U, pct, now)
    return r


def sweep(st=None, now=None):
    """prwatch, every minute: switch off / gate stop / 90 min without progress -> back to Pi; the 23:59 day line."""
    now = now or now_cst(); day = now.date().isoformat()
    doc = json.load(open(usage_path())) if os.path.exists(usage_path()) else {"days": {}}
    g, _ = day_gate(doc["days"].get(day))
    def why(t):
        return ("开关关了" if not on() else "用量闸门 stop（今天涨 >= 10 点）" if g == "stop"
                else f"{STALE_MIN} 分钟没有进展" if stale(t, now) else None)
    back = []
    if any(t.get("owner") == "grok" and why(t) for t in prfix.load_tickets().get("tickets", {}).values()):   # lock + write only then
        with prfix.tickets_locked() as T:
            for k, t in list(T["tickets"].items()):
                w = t.get("owner") == "grok" and why(t)
                if w: T["tickets"][k] = release(t, w, now); back.append((k, w))
    due = [x for x, d in doc["days"].items() if not d.get("summarized") and (x < day or (x == day and now.strftime("%H:%M") >= "23:59"))]
    if due:
        with usage_locked() as U:
            for x in sorted(due):
                d = U["days"][x]
                if d.get("summarized"): continue
                os.makedirs(prfix.OUT, exist_ok=True)
                with open(daily_path(), "a") as f: f.write(summary_line(x, d) + "\n")
                d["summarized"] = True
    for k, why in back: log(f"grok line: {k} back to the Pi queue ({why})")
    return back


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", choices=["gate", "pr-meta"]); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--key"); ap.add_argument("--title")
    ap.add_argument("--check", action="store_true"); ap.add_argument("--record", action="store_true")
    ap.add_argument("--claim", action="store_true"); ap.add_argument("--progress"); ap.add_argument("--finish"); ap.add_argument("--release")
    ap.add_argument("--pct", type=float); ap.add_argument("--pr", type=int); ap.add_argument("--why", default="Grok 放弃")
    A = ap.parse_args(argv)
    real = sys.stdout; sys.stdout = sys.stderr       # helper logs never corrupt the JSON line
    try:
        if A.cmd == "gate":
            r = gate_dry_run() if A.dry_run else {"status": "error", "error": "gate 只有 --dry-run（只读）"}
        elif A.cmd == "pr-meta":
            r = dict(pr_meta(A.key, A.title), status="ok") if A.key else {"status": "error", "error": "pr-meta 要 --key QA:FP"}
        elif (A.record or A.claim or A.finish) and A.pct is None: r = {"status": "error", "error": "--pct 必填（CheckSubscriptionUsage 每周用量 %）"}
        elif A.record:
            with usage_locked() as U: d = record(U, A.pct, now_cst())
            r = {"status": "ok", "gate": day_gate(d), "today": d}
        elif A.claim: r = claim(A.pct)
        elif A.progress: r = progress(A.progress)
        elif A.finish: r = finish(A.finish, A.pct, A.pr) if A.pr else {"status": "error", "error": "--finish 要 --pr"}
        elif A.release: r = give_back(A.release, A.pct, A.why)
        else: r = check()
    finally:
        sys.stdout = real
    print(json.dumps(r, ensure_ascii=False, default=str))
    return 0 if r.get("status") not in ("error",) else 1


if __name__ == "__main__": sys.exit(main())
