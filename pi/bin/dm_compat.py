#!/usr/bin/env python3
"""Grok-compatible DM records (10-23). Pi (and report/send-dm-recorded.mjs) record one DM per PR as `dm:<day>:<pr>`;
the Grok line recorded `<repo>#<pr>:<fp>` + covered_fingerprints, and the readers still only understand that form:
build-ledger.mjs (台账 rows; it now also reads dm: keys) and panoptes report/track-followups.mjs (follow-up reminders /
thanks / handoff receipts -> followup/state.json -> ledger evidence). After a dm: send, add the Grok-form record for the
same DM: key `<repo>#<pr>:<primary fp>`, covered_fingerprints = the PR's other P0–P2 the DM stands for (notifiable(f, sent_at):
unified rule from 10-24, the rule of the time before), same open_id / sent_at / message_id, alias_of = the dm: key. Never
overwrites a key; skipped when the PR already has a `<repo>#<pr>:*` record.
Lock = report/lib/jsonlog.mjs protocol via jsonrec.update (O_EXCL <file>.lock, stale 60 s, re-read under the lock, tmp + rename).
usage: dm_compat.py backfill [--apply] [--key dm:YYYY-MM-DD:N ...] [--skip-pr N ...]   (default: dry-run, prints only)
       outbound.h_feishu_dm.do() calls record(SENT, key) right after a successful dm: send."""
import sys, os, json, re, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import QA, RPT, REPO
import jsonrec, finding_policy

SENT = f"{QA}/feishu-sent.json"
SEV = {"P0": 0, "P1": 1, "P2": 2}
BAD_STATUS = re.compile(r"flaky|qa-error|false_positive|misreport|pre-existing|unconfirmed")

def notifiable(f, sent_at=None):
    """The problems a DM stands for, by when it was sent (Praise 2026-10-23 23:18). Sent from finding_policy.UNIFIED_SINCE
    (2026-10-24 00:00 Asia/Shanghai): the unified severity for a new notice -- finding_policy.notifiable(new_notice=True),
    exactly what daily.py put in the DM, so never 待核实 items, coverage-only P2, AI-test leads, unverified AI-only P0/P1,
    flaky / unverified / timeout / setup-failed checks, qa-error, own-tests, PLAUSIBLE review findings, 误报. Sent before
    that (or no time): the rule of the time, finding_policy.legacy_notified -- already-sent problems keep the old flow.
    Same split as build-ledger.mjs (dm: keys) and panoptes report/track-followups.mjs (sentUnderUnified)."""
    if not finding_policy.sent_under_unified(sent_at): return finding_policy.legacy_notified(f)
    if BAD_STATUS.search(f"{(f or {}).get('status') or ''} {(f or {}).get('attribution') or ''}"): return False
    return finding_policy.notifiable(f, new_notice=True)

def first_sent_at(rec):
    """Earliest real send of a dm: record (10-22: Grok sent first, Pi re-sent at 11:50 -> the earliest send counts)."""
    if not isinstance(rec, dict): return None
    ts = [t for t in (rec.get("sent_at"), (rec.get("grok_line_send") or {}).get("sent_at")) if t]
    known = [t for t in ts if finding_policy.instant(t)]
    return min(known, key=finding_policy.instant) if known else (ts[0] if ts else None)

def fingerprints(pr_report, sent_at=None):
    """Fingerprints a DM sent at `sent_at` stands for, main problem first (severity, review before coverage, report order)."""
    fs = [(i, f) for i, f in enumerate((pr_report or {}).get("findings") or []) if notifiable(f, sent_at) and f.get("fingerprint")]
    fs.sort(key=lambda t: (SEV[t[1]["severity"]], t[1].get("source") == "coverage-gate", t[0]))
    out = []
    for _, f in fs:
        if f["fingerprint"] not in out: out.append(f["fingerprint"])
    return out

def compat_entry(dm_key, rec, fps):
    """-> (grok_key, record) or None. Pure."""
    m = re.match(r"^dm:\d{4}-\d{2}-\d{2}:(\d+)$", dm_key or "")
    if not m or not fps or not isinstance(rec, dict) or not rec.get("sent_at"): return None
    out = {k: rec.get(k) for k in ("open_id", "sent_at", "message_id") if k in rec}
    g = rec.get("grok_line_send") or {}                 # 10-22: Grok sent first, Pi re-sent at 11:50 -> the earliest send counts
    if g.get("sent_at") and g["sent_at"] < out["sent_at"]: out.update(sent_at=g["sent_at"], message_id=g.get("message_id"))
    if fps[1:]: out["covered_fingerprints"] = fps[1:]
    out.update(alias_of=dm_key, by="pi-line dm-compat")
    return f"{REPO}#{m.group(1)}:{fps[0]}", out

def plan(sent, load_report, keys=None, skip=()):
    """[(dm_key, grok_key, record, why)] — why=None means add; otherwise the reason it is skipped."""
    out = []
    for k, v in sent.items():
        m = re.match(r"^dm:\d{4}-\d{2}-\d{2}:(\d+)$", k)
        if not m or (keys and k not in keys): continue
        pr = int(m.group(1))
        if pr in skip: out.append((k, None, None, "skip-pr")); continue
        if any(x.startswith(f"{REPO}#{pr}:") for x in sent): out.append((k, None, None, "PR already has a Grok-form record")); continue
        ce = compat_entry(k, v, fingerprints(load_report(pr), first_sent_at(v)))
        out.append((k, *(ce or (None, None)), None if ce else "no notifiable P0–P2 in the report (lead / 待确认 only) or no sent_at"))
    return out

def load_report(pr):
    try: return json.load(open(f"{RPT}/PR-{pr}.json"))["prs"][0]
    except Exception: return None

def apply(path, rows):
    """Add the planned records under the lock; re-reads the file and never overwrites. Returns added keys."""
    def fn(s):
        added = []
        for dm_key, gk, rec, why in rows:
            if why or not gk or gk in s: continue
            pr = gk.split("#")[1].split(":")[0]
            if any(x.startswith(f"{REPO}#{pr}:") for x in s): continue
            s[gk] = rec; added.append(gk)
        return added or None
    return jsonrec.update(path, fn) or []

def record(path, dm_key, loader=load_report):
    """Called by outbound right after send-feishu-dm.mjs recorded `dm_key`. Returns added keys ([] when nothing to add)."""
    if not str(dm_key).startswith("dm:"): return []
    s = json.load(open(path))
    return apply(path, plan(s, loader, keys={dm_key}))

def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("backfill"); b.add_argument("--apply", action="store_true"); b.add_argument("--key", nargs="*"); b.add_argument("--skip-pr", type=int, nargs="*", default=[])
    b.add_argument("--sent", default=SENT)
    a = ap.parse_args()
    rows = plan(json.load(open(a.sent)), load_report, set(a.key) if a.key else None, set(a.skip_pr))
    for dm_key, gk, rec, why in rows:
        print(f"{'ADD ' if not why else 'SKIP'} {dm_key} -> {gk or '-'}" + (f"  covered={len(rec.get('covered_fingerprints', []))}" if rec else "") + (f"  ({why})" if why else ""))
    if a.apply: print("added:", apply(a.sent, rows))
    else: print("DRY (add --apply to write)")
if __name__ == "__main__": main()
