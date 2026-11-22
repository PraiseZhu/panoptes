#!/usr/bin/env python3
"""watchdog.py — Devin automation preflight script for the Pi line on Devin (PraiseZhu/panoptes#313).

Self-contained: one file, Python standard library only (uploaded as is to the Devin preflight VM; imports nothing from the
repo, has its own small GitHub App JWT + REST client). Meant to run every 30 minutes.

Input (paths in env, Devin automation contract): EVENT_FILE (read), STATE_FILE (read/write JSON, <= 64 KiB), LAST_RUN_FILE
(read: recently emitted items with their `key`). Output: OUTPUT_FILE gets {"items": [...]} (one item per anomaly) or
{"items": []} when all is well.

Checks (Beijing time, item id = <check>:<date>:<object>, the same id is emitted at most once):
  merged-report  a PR of WATCHDOG_PR_REPO merged more than 90 min ago (last 24 h) has no reports/merged/PR-N.json
                 (nor reports/catch-up/PR-N.json) on the data branch           -> merged-report:<today>:PR-<n>
  daily-sent     after 07:50 the outbound ledger has no done mivo-daily-report:<data day> -> daily-sent:<today>:<data day>
  data-stale     the data branch head commit is older than 7 h                 -> data-stale:<today>:<branch>
  slot-missed    a slot passed 45 min ago and the lock branch has no runs/<day>/<HHMM>[.shadow].json
                                                                                -> slot-missed:<slot day>:<HHMM>
  A check that cannot read its data emits watchdog-error:<today>:<check> (also once a day) instead of failing silently.

Env (all optional): WATCHDOG_PR_REPO (xindong/mivo-canvas-plugin), WATCHDOG_DATA_REPO (PraiseZhu/grokbot-box),
WATCHDOG_DATA_BRANCH (devin), WATCHDOG_LOCK_BRANCH (devin-lock), WATCHDOG_DATA_PATH (box/workspace/mivo-qa),
WATCHDOG_SLOTS (comma list of HH:MM, default = the 14 slots of devin/conf/slots.json), WATCHDOG_NOW (ISO time, tests),
GitHub auth: WATCHDOG_GH_TOKEN or GITHUB_TOKEN, else the PraiseDEVIN App (PRAISEDEVIN_PRIVATE_KEY_B64 = base64 PEM,
PRAISEDEVIN_APP_ID default 5815034, PRAISEDEVIN_INSTALLATION_ID optional), WATCHDOG_API (https://api.github.com).
Tests: import the file and call run(fetcher, now, state, last_run) with a fake fetcher.
"""
import base64
import datetime as dt
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CST = dt.timezone(dt.timedelta(hours=8))
SLOTS = ("00:05", "06:20", "07:00", "07:35", "08:20", "08:35", "09:30", "09:50", "12:30", "15:20", "16:30", "18:35",
         "20:30", "23:40")
MERGE_REPORT_MIN = 90
MERGE_LOOKBACK_H = 24
DAILY_DEADLINE = dt.time(7, 50)
DATA_STALE_H = 7
SLOT_GRACE_MIN = 45
KEEP_DAYS = 3


def env(name, default):
    return os.environ.get(name) or default


# ---------------- minimal RS256 JWT (PKCS#1 / PKCS#8 RSA PEM, standard library only) ----------------
def _der(buf, i):
    tag, n = buf[i], buf[i + 1]
    i += 2
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(buf[i:i + k], "big")
        i += k
    return tag, buf[i:i + n], i + n


def _seq(body):
    out, i = [], 0
    while i < len(body):
        tag, val, i = _der(body, i)
        out.append((tag, val))
    return out


def rsa_key(pem):
    """(n, d) of an RSA private key in PEM (BEGIN RSA PRIVATE KEY or BEGIN PRIVATE KEY)."""
    lines = [ln.strip() for ln in pem.strip().splitlines()]
    der = base64.b64decode("".join(ln for ln in lines if ln and not ln.startswith("-----")))
    _, body, _ = _der(der, 0)
    fields = _seq(body)
    if len(fields) >= 3 and fields[1][0] == 0x30 and fields[2][0] == 0x04:  # PKCS#8: version, algorithm, OCTET STRING
        _, body, _ = _der(fields[2][1], 0)
        fields = _seq(body)
    ints = [int.from_bytes(v, "big") for t, v in fields if t == 0x02]
    if len(ints) < 4:
        raise ValueError("not an RSA private key")
    return ints[1], ints[3]


SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def rs256(msg, n, d):
    k = (n.bit_length() + 7) // 8
    t = SHA256_PREFIX + hashlib.sha256(msg).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big")


def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def app_jwt(app_id, pem, now=None):
    now = int(now if now is not None else time.time())
    head = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = b64url(json.dumps({"iat": now - 60, "exp": now + 540, "iss": str(app_id)}, separators=(",", ":")).encode())
    n, d = rsa_key(pem)
    return f"{head}.{body}." + b64url(rs256(f"{head}.{body}".encode(), n, d))


# ---------------- GitHub REST ----------------
class GitHub:
    def __init__(self):
        self.api = env("WATCHDOG_API", "https://api.github.com").rstrip("/")
        self.static = env("WATCHDOG_GH_TOKEN", os.environ.get("GITHUB_TOKEN", ""))
        self.tokens = {}

    def _req(self, path, token, method="GET", accept="application/vnd.github+json"):
        req = urllib.request.Request(self.api + path, method=method, headers={
            "Accept": accept, "Authorization": f"Bearer {token}", "User-Agent": "panoptes-devin-watchdog",
            "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 404, b""
            raise RuntimeError(f"GitHub {method} {path.split('?')[0]} -> HTTP {e.code}") from None

    def token(self, owner):
        if self.static:
            return self.static
        if owner not in self.tokens:
            pem = base64.b64decode(os.environ.get("PRAISEDEVIN_PRIVATE_KEY_B64", "")).decode()
            if not pem:
                raise RuntimeError("no GitHub credentials (WATCHDOG_GH_TOKEN / GITHUB_TOKEN / PRAISEDEVIN_PRIVATE_KEY_B64)")
            jwt = app_jwt(env("PRAISEDEVIN_APP_ID", "5815034"), pem)
            inst = os.environ.get("PRAISEDEVIN_INSTALLATION_ID")
            if not inst:
                _, raw = self._req("/app/installations?per_page=100", jwt)
                inst = next((i["id"] for i in json.loads(raw) if i.get("account", {}).get("login") == owner), None)
                if not inst:
                    raise RuntimeError(f"PraiseDEVIN is not installed on {owner}")
            _, raw = self._req(f"/app/installations/{inst}/access_tokens", jwt, "POST")
            self.tokens[owner] = json.loads(raw)["token"]
        return self.tokens[owner]

    def get(self, repo, path, raw=False):
        """JSON (or bytes with raw=True) of GET /repos/<repo><path>; None on 404."""
        status, body = self._req(f"/repos/{repo}{path}", self.token(repo.split("/")[0]),
                                 accept="application/vnd.github.raw+json" if raw else "application/vnd.github+json")
        if status == 404:
            return None
        return body if raw else json.loads(body)


def iso(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


class GitHubFetcher:
    """The data the checks read. A fake with the same five methods replaces it in tests."""

    def __init__(self, gh=None):
        self.gh = gh or GitHub()
        self.pr_repo = env("WATCHDOG_PR_REPO", "xindong/mivo-canvas-plugin")
        self.data_repo = env("WATCHDOG_DATA_REPO", "PraiseZhu/grokbot-box")
        self.branch = env("WATCHDOG_DATA_BRANCH", "devin")
        self.lock_branch = env("WATCHDOG_LOCK_BRANCH", "devin-lock")
        self.data_path = env("WATCHDOG_DATA_PATH", "box/workspace/mivo-qa").strip("/")

    def _contents(self, path, ref, raw=False):
        return self.gh.get(self.data_repo, f"/contents/{urllib.parse.quote(path)}?ref={urllib.parse.quote(ref)}", raw)

    def merged_prs(self, since):
        """[{number, merged_at (aware datetime), title}] of PRs merged at or after `since`."""
        out = []
        for page in range(1, 6):
            batch = self.gh.get(self.pr_repo, f"/pulls?state=closed&sort=updated&direction=desc&per_page=100&page={page}") or []
            for p in batch:
                if p.get("merged_at") and iso(p["merged_at"]) >= since:
                    out.append({"number": p["number"], "merged_at": iso(p["merged_at"]), "title": p.get("title", "")})
            if len(batch) < 100 or all(iso(p["updated_at"]) < since for p in batch):
                break
        return out

    def data_exists(self, rel):
        return self._contents(f"{self.data_path}/{rel}", self.branch) is not None

    def data_json(self, rel):
        raw = self._contents(f"{self.data_path}/{rel}", self.branch, raw=True)
        return None if raw is None else json.loads(raw)

    def data_head_time(self):
        b = self.gh.get(self.data_repo, f"/branches/{urllib.parse.quote(self.branch)}")
        if b is None:
            raise RuntimeError(f"data branch {self.branch} not found")
        return iso(b["commit"]["commit"]["committer"]["date"])

    def slot_markers(self, day):
        """File names under runs/<day>/ on the lock branch (empty when there are none)."""
        listing = self._contents(f"runs/{day}", self.lock_branch)
        return {x["name"] for x in listing or [] if isinstance(x, dict)}


# ---------------- checks ----------------
def check_merged_report(f, now, today):
    for p in sorted(f.merged_prs(now - dt.timedelta(hours=MERGE_LOOKBACK_H)), key=lambda p: p["number"]):
        age = (now - p["merged_at"]).total_seconds() / 60
        n = p["number"]
        if age < MERGE_REPORT_MIN or f.data_exists(f"reports/merged/PR-{n}.json") or f.data_exists(f"reports/catch-up/PR-{n}.json"):
            continue
        yield f"merged-report:{today}:PR-{n}", f"PR #{n} merged {int(age)} min ago and still has no QA report"


def check_daily_sent(f, now, today):
    if now.time() < DAILY_DEADLINE:
        return
    data_day = (now.date() - dt.timedelta(days=1)).isoformat()
    ledger = f.data_json("pi/out/outbound/ledger.json") or {}
    entry = ledger.get(f"mivo-daily-report:{data_day}") or {}
    if entry.get("status") != "done":
        yield (f"daily-sent:{today}:{data_day}",
               f"daily report for {data_day} not sent by {DAILY_DEADLINE:%H:%M} (ledger status: {entry.get('status') or 'absent'})")


def check_data_stale(f, now, today):
    head = f.data_head_time()
    age_h = (now - head).total_seconds() / 3600
    if age_h > DATA_STALE_H:
        branch = getattr(f, "branch", "devin")
        yield f"data-stale:{today}:{branch}", f"data branch {branch} has no new commit for {age_h:.1f} h (last {head.astimezone(CST):%m-%d %H:%M})"


def check_slot_missed(f, now, today):
    slots = [s.strip() for s in env("WATCHDOG_SLOTS", ",".join(SLOTS)).split(",") if s.strip()]
    markers = {}
    for back in (1, 0):
        day = now.date() - dt.timedelta(days=back)
        for s in slots:
            h, m = map(int, s.split(":"))
            at = dt.datetime.combine(day, dt.time(h, m), CST)
            if not (now - dt.timedelta(hours=24) < at <= now - dt.timedelta(minutes=SLOT_GRACE_MIN)):
                continue
            if day not in markers:
                markers[day] = f.slot_markers(day.isoformat())
            hhmm = s.replace(":", "")
            if f"{hhmm}.json" in markers[day] or f"{hhmm}.shadow.json" in markers[day]:
                continue
            yield f"slot-missed:{day}:{hhmm}", f"slot {s} of {day} passed {SLOT_GRACE_MIN} min ago and has no run record"


CHECKS = (("merged-report", check_merged_report), ("daily-sent", check_daily_sent), ("data-stale", check_data_stale),
          ("slot-missed", check_slot_missed))


def last_run_keys(last_run):
    items = last_run.get("items", []) if isinstance(last_run, dict) else last_run if isinstance(last_run, list) else []
    return {str(i.get("key") or i.get("id")) for i in items if isinstance(i, dict) and (i.get("key") or i.get("id"))}


def run(fetcher, now, state, last_run):
    """(items, new_state). An id already in state["reported"] or in LAST_RUN_FILE is not emitted again."""
    now = now.astimezone(CST)
    today = now.date().isoformat()
    reported = dict((state or {}).get("reported") or {})
    seen = set(reported) | last_run_keys(last_run or {})
    items = []
    for name, check in CHECKS:
        try:
            found = list(check(fetcher, now, today))
        except Exception as e:
            found = [(f"watchdog-error:{today}:{name}", f"watchdog check {name} failed: {type(e).__name__}: {str(e)[:200]}")]
        for iid, title in found:
            if iid in seen:
                continue
            seen.add(iid)
            reported[iid] = today
            items.append({"id": iid, "title": title})
    cutoff = (now.date() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    new_state = dict(state or {})
    new_state["reported"] = {k: v for k, v in reported.items() if v >= cutoff}
    new_state["last_check"] = now.isoformat(timespec="seconds")
    return items, new_state


def load(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError, TypeError):
        return default


def main(fetcher=None):
    out = os.environ.get("OUTPUT_FILE")
    if not out:
        print("watchdog: OUTPUT_FILE is not set", file=sys.stderr)
        return 2
    load(os.environ.get("EVENT_FILE") or "", {})
    state_file = os.environ.get("STATE_FILE")
    state = load(state_file, {}) if state_file else {}
    last_run = load(os.environ.get("LAST_RUN_FILE") or "", {})
    now = dt.datetime.fromisoformat(os.environ["WATCHDOG_NOW"]) if os.environ.get("WATCHDOG_NOW") else dt.datetime.now(CST)
    items, new_state = run(fetcher or GitHubFetcher(), now, state if isinstance(state, dict) else {}, last_run)
    if state_file:
        with open(state_file, "w") as fh:
            json.dump(new_state, fh, separators=(",", ":"))
    with open(out, "w") as fh:
        json.dump({"items": items}, fh, ensure_ascii=False)
    print(f"watchdog: {len(items)} item(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
