#!/usr/bin/env python3
"""panoptes deploy status, visible without logging in to the box (11-16, PraiseZhu/panoptes#303).

After a successful deploy (opsenv.deploy(), used by opsdeploy every 10 min and opsenv hourly) the deployed commit of
PraiseZhu/panoptes gets one commit status: context CONTEXT, state success, description "部署于 <host> <YYYY-MM-DD HH:MM> CST"
(host name and Beijing time only: no path, no token, no personal data). It is written with the Praise QA App installation
token for PraiseZhu/panoptes (ghtoken, the same credential route as the rest of the Pi line), never with the PAT.
No credential or a failed request never fails the deploy: the deploy line is followed by "- 部署状态未发布：<reason code>" in
out/ops/env-<day>.md (codes in REASONS; never the token or the response body).

Read it from anywhere (any gh login that can read the repo):
  gh api repos/PraiseZhu/panoptes/commits/<sha>/statuses --jq '.[] | select(.context == "panoptes/deployed")'
  python3 pi/bin/deploy_status.py show [--ref main] [--limit 30]   -> the newest commit of <ref> that carries the status
"""
import argparse, json, os, re, socket, subprocess, sys, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import lib  # noqa: E402

REPO = "PraiseZhu/panoptes"
CONTEXT = "panoptes/deployed"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REASONS = ("bad_sha", "no_installation", "no_credential", "no_statuses_permission", "http_<code>", "network_error", "error_<exception class>")


def host_name(raw=None):
    """the box's host name, reduced to [A-Za-z0-9._-] (a status description is public to every repo reader)"""
    h = re.sub(r"[^A-Za-z0-9._-]", "-", raw if raw is not None else socket.gethostname()).strip("-")[:63]
    return h or "unknown-host"


def describe(host=None, now=None):
    t = (now or lib.now_cst()).astimezone(lib.CST)
    return f"部署于 {host_name(host)} {t.strftime('%Y-%m-%d %H:%M')} CST"


def _token_info():
    import ghtoken
    return ghtoken.token_info(REPO)


def _post(url, token, payload, timeout=30):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "panoptes-deploy-status",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return r.status


def _api():
    import ghtoken
    return ghtoken._api()


def publish(sha, host=None, now=None, token_info=None, post=None):
    """-> (True, None) once the status is written, else (False, reason code). Never raises."""
    if not isinstance(sha, str) or not SHA_RE.match(sha): return False, "bad_sha"
    try:
        import ghtoken
        try: info = (token_info or _token_info)()
        except ghtoken.NoInstallation: return False, "no_installation"
        except Exception: return False, "no_credential"   # no App key, mint refused, network during the mint: the deploy still stands
        perms = info.get("permissions") or {}
        if perms and perms.get("statuses") != "write": return False, "no_statuses_permission"
        payload = {"state": "success", "context": CONTEXT, "description": describe(host, now)}
        try: code = (post or _post)(f"{_api()}/repos/{REPO}/statuses/{sha}", info["token"], payload)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                try: ghtoken.invalidate(REPO)   # a stale cached token: the next deploy mints a new one
                except Exception: pass
            return False, f"http_{e.code}"
        except (urllib.error.URLError, OSError): return False, "network_error"
        if code != 201: return False, f"http_{code}"
        return True, None
    except Exception as e:
        return False, f"error_{e.__class__.__name__}"


def after_deploy(sha, L, **kw):
    """called by opsenv.deploy() right after the fast-forward: one line appended to L (L ends up in out/ops/env-<day>.md)"""
    ok, why = publish(sha, **kw)
    L.append(f"- 部署状态已发布：{CONTEXT} @ {sha[:7]}" if ok else f"- 部署状态未发布：{why}")
    return ok, why


# ---------- read ----------
def _gh_json(args, run=subprocess.run):
    r = run(["gh", "api"] + args, capture_output=True, text=True, timeout=60)
    if r.returncode: raise RuntimeError(f"gh api {args[0]}: {lib._TOKEN_RE.sub('***', (r.stderr or '').strip())[:200]}")
    return json.loads(r.stdout or "null")


def status_of(sha, run=subprocess.run):
    """the newest CONTEXT status of one commit (GitHub lists statuses newest first), or None"""
    return next((s for s in _gh_json([f"repos/{REPO}/commits/{sha}/statuses?per_page=100"], run) or [] if s.get("context") == CONTEXT), None)


def current(ref="main", limit=30, run=subprocess.run):
    """-> {"sha", "description", "created_at", "behind"} for the newest of the last `limit` commits of ref with a deploy status
    (behind = commits of ref newer than it), or None when none of them has one"""
    commits = _gh_json([f"repos/{REPO}/commits?sha={ref}&per_page={int(limit)}"], run) or []
    for i, c in enumerate(commits):
        s = status_of(c["sha"], run)
        if s: return {"sha": c["sha"], "description": s.get("description"), "created_at": s.get("created_at"), "behind": i, "ref_head": commits[0]["sha"]}
    return None


def main(argv=None, run=subprocess.run):
    ap = argparse.ArgumentParser(description="panoptes deploy status (commit status panoptes/deployed)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show", help="which commit is deployed"); s.add_argument("--ref", default="main"); s.add_argument("--limit", type=int, default=30)
    a = ap.parse_args(argv)
    cur = current(a.ref, a.limit, run)
    if not cur:
        print(f"最近 {a.limit} 个 {a.ref} 提交都没有 {CONTEXT} 状态：查不到当前部署版本（部署状态可能没发布，看主机 out/ops/env-<日>.md）")
        return 1
    tail = f"{a.ref} 最新就是它" if not cur["behind"] else f"落后 {a.ref} 最新 {cur['ref_head'][:7]} {cur['behind']} 个提交"
    print(f"当前部署：{cur['sha']}（{cur['description']}）；{tail}")
    return 0


if __name__ == "__main__": sys.exit(main())
