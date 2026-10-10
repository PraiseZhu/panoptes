#!/usr/bin/env python3
"""Refresh /workspace/mivo-qa/author-map.json end-to-end (stdlib only, read-only on GitHub & Feishu).

1. Fetch roster READMEs (xindong/org, taptap/org) via GitHub REST  -> needs GITHUB_TOKEN with read access
   to both org repos (SAML-SSO-authorized). Offline alternative: --readme owner/repo=/path/README.md
2. Parse every '| [@login](..) | Name | Email | Role |' row with its section headings as teams; dedupe by
   login (prefer rows with name/email filled).
3. Optionally list PR authors of the target repo (needs GITHUB_TOKEN); otherwise reuse the previous file.
4. Resolve Feishu open_ids via contact/v3/users/batch_get_id (50 emails/call) with the Mivo bot app.
   App id from env MIVO_FEISHU_APP_ID, secret from env MIVO_FEISHU_APP_SECRET (never printed). Also records contact/v3/scopes visibility.
Never sends any message, never writes to GitHub.

Usage: python3 refresh-roster.py [--readme xindong/org=path ...] [--no-feishu] [--out PATH]
"""
import argparse, base64, json, os, re, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

ROSTER_REPOS = ["xindong/org", "taptap/org"]
TARGET_REPO = "xindong/mivo-canvas-plugin"
OWNER_LOGIN = "PraiseZhu"
APP_ID = os.environ.get("MIVO_FEISHU_APP_ID")  # Mivo bot app id; set in the secrets env file
FEISHU = "https://open.feishu.cn/open-apis"
SH = timezone(timedelta(hours=8))
ROW = re.compile(r"^\|\s*\[@([^\]]+)\]\([^)]*\)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|")
HEAD = re.compile(r"^(#{2,4})\s+(.+?)\s*$")


def http(method, url, body=None, headers=None, timeout=30):
    h = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h.setdefault("content-type", "application/json; charset=utf-8")
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}"), dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read() or b"{}"
        try:
            return e.code, json.loads(raw), dict(e.headers)
        except ValueError:
            return e.code, {"raw": raw[:300].decode("utf8", "replace")}, dict(e.headers)


def gh(path):
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not tok:
        raise SystemExit("GITHUB_TOKEN (or GH_TOKEN) is required to fetch from GitHub; or pass --readme files")
    st, d, hd = http("GET", f"https://api.github.com/{path}", headers={
        "authorization": f"Bearer {tok}", "accept": "application/vnd.github+json", "x-github-api-version": "2022-11-28"})
    if st != 200:
        raise SystemExit(f"GitHub GET {path} -> {st} {str(d)[:200]}")
    return d, hd


def fetch_readme(repo):
    d, _ = gh(f"repos/{repo}/contents/README.md")
    return base64.b64decode(d["content"]).decode("utf8")


def parse_roster(text, source):
    out, stack = {}, {}
    for line in text.splitlines():
        m = HEAD.match(line)
        if m:
            lvl = len(m.group(1)); stack = {k: v for k, v in stack.items() if k < lvl}; stack[lvl] = m.group(2)
            continue
        m = ROW.match(line)
        if not m:
            continue
        login, name, email, role = (x.strip() for x in m.groups())
        team = stack.get(4) or stack.get(3) or stack.get(2) or ""
        e = out.setdefault(login.lower(), {"github_login": login, "name": "", "email": "", "role": "", "teams": [], "sources": [source]})
        if name and not e["name"]: e["name"] = name
        if email and not e["email"]: e["email"] = email
        if role and (not e["role"] or role == "owner"): e["role"] = role
        if team and team != "Organization" and team not in e["teams"]: e["teams"].append(team)
    return out


def merge(rosters):
    allp = {}
    for r in rosters:
        for k, e in r.items():
            if k not in allp:
                allp[k] = json.loads(json.dumps(e)); continue
            a = allp[k]
            for f in ("name", "email", "role"):
                if e[f] and not a[f]: a[f] = e[f]
            a["teams"] += [t for t in e["teams"] if t not in a["teams"]]
            a["sources"] += [s for s in e["sources"] if s not in a["sources"]]
    return allp


def pr_authors(repo):
    agg, page = {}, 1
    while True:
        d, _ = gh(f"repos/{repo}/pulls?state=all&per_page=100&page={page}")
        if not d: break
        for p in d:
            a = agg.setdefault(p["user"]["login"], {"account_type": p["user"]["type"], "pr_total": 0, "pr_open": 0, "pr_merged": 0, "last_pr_created": ""})
            a["pr_total"] += 1; a["pr_open"] += p["state"] == "open"; a["pr_merged"] += bool(p.get("merged_at"))
            a["last_pr_created"] = max(a["last_pr_created"], p["created_at"])
        page += 1
    return agg


def feishu_resolve(emails):
    secret = os.environ.get("MIVO_FEISHU_APP_SECRET")
    if not APP_ID or not secret:
        return {"status": "skipped: MIVO_FEISHU_APP_ID / MIVO_FEISHU_APP_SECRET not set"}, {}
    st, d, _ = http("POST", f"{FEISHU}/auth/v3/tenant_access_token/internal", {"app_id": APP_ID, "app_secret": secret})
    info = {"token": {"http": st, "code": d.get("code"), "msg": d.get("msg")}}
    tok = d.get("tenant_access_token")
    if not tok:
        return info, {}
    auth = {"authorization": f"Bearer {tok}"}
    # visibility range (paginated)
    users, depts, pt = [], [], ""
    while True:
        st, d, _ = http("GET", f"{FEISHU}/contact/v3/scopes?page_size=100&user_id_type=open_id" + (f"&page_token={pt}" if pt else ""), headers=auth)
        info["scopes"] = {"http": st, "code": d.get("code"), "msg": d.get("msg")}
        dd = d.get("data") or {}
        users += dd.get("user_ids", []); depts += dd.get("department_ids", [])
        pt = dd.get("page_token") or ""
        if not dd.get("has_more"): break
    info["scopes"].update(visible_users=len(users), visible_departments=len(depts), department_ids=depts)
    res, calls = {}, []
    for i in range(0, len(emails), 50):
        chunk = emails[i:i + 50]
        st, d, _ = http("POST", f"{FEISHU}/contact/v3/users/batch_get_id?user_id_type=open_id", {"emails": chunk, "include_resigned": False}, headers=auth)
        calls.append({"http": st, "code": d.get("code"), "msg": (d.get("msg") or "")[:300], "n": len(chunk)})
        for u in (d.get("data") or {}).get("user_list", []):
            if u.get("email"): res[u["email"].lower()] = u.get("user_id")
        time.sleep(0.3)
    info["batch_get_id_calls"] = calls
    info["visible_open_ids"] = set(users)
    return info, res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--readme", action="append", default=[], help="owner/repo=/path/README.md (offline input)")
    ap.add_argument("--out", default=os.path.join(os.environ.get("QA_HOME", "/workspace/mivo-qa"), "author-map.json"))
    ap.add_argument("--no-feishu", action="store_true")
    a = ap.parse_args()
    local = dict(x.split("=", 1) for x in a.readme)
    rosters, src_meta = [], {}
    for repo in ROSTER_REPOS:
        if repo in local:
            text = open(local[repo], encoding="utf8").read(); how = f"local file {local[repo]}"
        else:
            text = fetch_readme(repo); how = "GitHub REST"
        m = re.search(r"Last updated:\s*([^\n]+)", text)
        src_meta[repo] = {"file": "README.md", "via": how, "roster_last_updated": m.group(1).strip() if m else None}
        rosters.append(parse_roster(text, repo))
    people = merge(rosters)

    prev = {}
    if os.path.exists(a.out):
        try: prev = json.load(open(a.out, encoding="utf8"))
        except ValueError: prev = {}
    if os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"):
        authors = pr_authors(TARGET_REPO); authors_src = "GitHub REST (live)"
    else:
        authors = {x["github_login"]: {k: x.get(k) for k in ("account_type", "pr_total", "pr_open", "pr_merged", "last_pr_created")}
                   for x in prev.get("mivo_authors", prev.get("authors", []))}
        authors_src = "reused from previous author-map.json (no GITHUB_TOKEN)"

    emails = sorted({p["email"].lower() for p in people.values() if p["email"]})
    finfo, res = ({"status": "skipped (--no-feishu)"}, {}) if a.no_feishu else feishu_resolve(emails)
    visible = finfo.pop("visible_open_ids", set()) if isinstance(finfo, dict) else set()
    lookup_ok = any(c.get("code") == 0 for c in finfo.get("batch_get_id_calls", []))

    for p in people.values():
        e = p["email"].lower()
        oid = res.get(e) if e else None
        p["feishu_open_id"] = oid or None
        if not e: p["feishu_status"] = "no_email"
        elif not lookup_ok: p["feishu_status"] = "lookup_failed"
        elif oid: p["feishu_status"] = "resolved"
        else: p["feishu_status"] = "not_found_or_outside_visibility"
        if oid and visible: p["in_visible_user_list"] = oid in visible

    mivo = []
    for login, s in sorted(authors.items(), key=lambda x: -(x[1].get("pr_total") or 0)):
        p = people.get(login.lower(), {})
        is_bot = (s.get("account_type") == "Bot") or login.endswith("[bot]")
        mivo.append({"github_login": login, **s, "name": p.get("name") or None, "email": p.get("email") or None,
                     "teams": p.get("teams", []), "roster_sources": p.get("sources", []), "in_roster": bool(p),
                     "feishu_open_id": p.get("feishu_open_id"), "feishu_status": p.get("feishu_status", "not_in_roster"),
                     "notify_via_feishu": (not is_bot) and login != OWNER_LOGIN and bool(p.get("feishu_open_id")),
                     **({"note": "owner; never notified via Feishu"} if login == OWNER_LOGIN else {}),
                     **({"note": "bot; not notifiable"} if is_bot else {})})

    from collections import Counter
    counts = dict(Counter(p["feishu_status"] for p in people.values())); counts["total_people"] = len(people)
    out = {"resolved_at": datetime.now(SH).isoformat(timespec="seconds"), "target_repo": TARGET_REPO,
           "owner_login": OWNER_LOGIN, "roster_sources": src_meta, "mivo_authors_source": authors_src,
           "feishu": {"app": "Mivo", "app_id": APP_ID, **finfo}, "counts": counts,
           "mivo_authors": mivo, "roster": sorted(people.values(), key=lambda p: p["github_login"].lower())}
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf8") as f: json.dump(out, f, ensure_ascii=False, indent=2)
    os.replace(tmp, a.out)
    print(json.dumps({"counts": counts, "feishu": out["feishu"], "mivo": [(m["github_login"], m["feishu_status"], m["notify_via_feishu"]) for m in mivo]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
