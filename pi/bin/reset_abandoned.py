#!/usr/bin/env python3
"""Auditable reset of outbound gave_up entries (first used for the 11-02 GitHub email-privacy push declines).

Only the keys named with --keys are touched, and only while their status is still "gave_up": the entry is removed from
out/outbound/ledger.json so the next outbound run treats it as new. A named key that is missing or in any other status
is left alone and reported under "skipped". Every removal is logged with the old entry.
Idempotent: a second run finds nothing to reset and leaves the file byte-for-byte unchanged.
Usage: python3 pi/bin/reset_abandoned.py --keys KEY[,KEY...] [--dry-run] [--ledger PATH]
"""
import argparse, fcntl, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import BASE, write_atomic, log

LEDGER = f"{BASE}/out/outbound/ledger.json"   # = outbound.LEDGER (not imported: outbound pulls in merger's live policy file)

def reset(path, keys, dry_run=False):
    try: led = json.load(open(path))
    except (OSError, ValueError): led = None
    if led is None: raise SystemExit(f"FATAL {path} missing or unreadable")
    hit = [k for k in dict.fromkeys(keys) if (led.get(k) or {}).get("status") == "gave_up"]
    for k in hit:
        log(f"reset_abandoned{' (dry-run)' if dry_run else ''}: remove {k} = {json.dumps(led[k], ensure_ascii=False)[:300]}")
    if not hit: log("reset_abandoned: nothing to reset (already done)"); return []
    if not dry_run:
        for k in hit: del led[k]
        write_atomic(path, json.dumps(led, ensure_ascii=False, indent=1))
    return hit


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--keys", required=True, help="comma-separated ledger keys; only gave_up ones are reset")
    o = ap.parse_args()
    keys = [k.strip() for k in o.keys.split(",") if k.strip()]
    if not keys: ap.error("--keys is empty")
    lk = open(f'{os.environ.get("PI_LOCK_DIR") or "/tmp"}/pi-fallback-outbound.exec.lock', "w")   # same lock as outbound.main
    for _ in range(60):
        try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB); break
        except BlockingIOError: time.sleep(5)
    else: log("outbound holds the exec lock for 5 min -> retry later"); return 1
    hit = reset(o.ledger, keys, o.dry_run)
    print(json.dumps({"reset": hit, "skipped": [k for k in dict.fromkeys(keys) if k not in hit], "dry_run": o.dry_run}))
    return 0


if __name__ == "__main__": sys.exit(main())
