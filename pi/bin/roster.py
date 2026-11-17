#!/usr/bin/env python3
"""Mivo QA 日报 roster refresh (1st/15th 08:23; gated by takeover/roster). Runs panoptes refresh-roster.py
into out/roster/ (never overwrites the live author-map.json); prints a diff summary vs the live map."""
from lib import *
def mivo_author_ids(mivo_authors):
    """Identity set for the roster diff: github_login from the current list-of-dicts schema, or from a dict of records
    (outbound.authors already accepts both). Missing/empty (the historical default {}) yields an empty set."""
    if isinstance(mivo_authors, dict):
        records = mivo_authors.values()
    elif mivo_authors is None:
        records = ()
    else:
        records = mivo_authors
    ids = set()
    for rec in records:
        if isinstance(rec, dict):
            login = rec.get("github_login")
            if isinstance(login, str) and login:
                ids.add(login)
    return ids
def main():
    if not takeover("roster"): log("takeover/roster absent -> skip"); return 0
    if mode() != "pi" and "--force" not in sys.argv: log("fleet-mode != pi -> skip (Grok line owns roster)"); return 0
    if now_cst().day not in (1, 15) and "--force" not in sys.argv: log("not 1st/15th -> skip"); return 0
    secret_env(); d = today(); out = f"{BASE}/out/roster/author-map-{d}.json"; os.makedirs(os.path.dirname(out), exist_ok=True)
    r = subprocess.run(["python3", "/workspace/panoptes/scripts/refresh-roster.py", "--out", out], capture_output=True, text=True, timeout=900)
    log("refresh-roster rc", r.returncode, r.stdout[-300:], r.stderr[-300:])
    if r.returncode == 0 and os.path.exists(out):
        new = mivo_author_ids(json.load(open(out)).get("mivo_authors", {})); old = mivo_author_ids(json.load(open(f"{QA}/author-map.json")).get("mivo_authors", {}))
        write_atomic(f"{BASE}/out/roster/{d}.md", f"# roster {d}\n- 新增: {sorted(new-old)}\n- 移除: {sorted(old-new)}\n- 采纳需人工把 {out} 复制到 {QA}/author-map.json\n")
    return r.returncode
if __name__ == "__main__": sys.exit(main())
