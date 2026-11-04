#!/usr/bin/env python3
"""G3 (11-05): merge a panoptes / vigil PR as the Praise QA App, locked to the head the caller reviewed.

usage: panoptes_merge.py <PR> <head_sha> [-R PraiseZhu/panoptes|PraiseZhu/vigil]

  1. repo must be in ALLOWED (default PraiseZhu/panoptes). The product repo (xindong/mivo-canvas-plugin, Praise-only merges) and every
     other repo are refused before any token is minted (exit 3).
  2. an installation token from ghtoken.token(repo) (the existing helper: cache dir 0700 / file 0600). It stays in this process: never
     printed, never put in an env var or argv, never written anywhere else.
  3. PUT /repos/{repo}/pulls/N/merge {"merge_method": "squash", "sha": head_sha}: GitHub refuses (409) when the head moved.
  4. GET /repos/{repo}/pulls/N: prints merged_by; exit 0 only when merged and merged_by == lib.app_login() (praise-qa[bot]), else exit 1.
Every error line is passed through ghtoken.mask()."""
import json, os, re, sys, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import ghtoken  # noqa: E402
import lib  # noqa: E402

ALLOWED = ("PraiseZhu/panoptes", "PraiseZhu/vigil")
DEFAULT_REPO = "PraiseZhu/panoptes"
EXIT_REFUSED, EXIT_USAGE = 3, 64


class ApiError(RuntimeError): pass


def api(method, path, token, body=None, opener=urllib.request.urlopen):
    req = urllib.request.Request(f"{ghtoken._api()}/{path.lstrip('/')}", method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "panoptes-merge",
                                          **({"Content-Type": "application/json"} if body is not None else {})})
    try:
        with opener(req, timeout=60) as r: return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise ApiError(f"{method} {path}: HTTP {e.code} {ghtoken.mask(e.read().decode(errors='replace'))[:300]}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise ApiError(f"{method} {path}: {ghtoken.mask(e)[:300]}") from None


def parse(argv):
    a, repo = list(argv), DEFAULT_REPO
    for flag in ("-R", "--repo"):
        if flag in a:
            i = a.index(flag)
            if i + 1 >= len(a): raise ValueError(f"{flag} needs OWNER/REPO")
            repo = a[i + 1]; del a[i:i + 2]
    if len(a) != 2: raise ValueError("expected <PR> <head_sha>")
    pr, sha = a
    if not re.fullmatch(r"[1-9]\d*", pr): raise ValueError(f"PR must be a number, got {pr!r}")
    if not re.fullmatch(r"[0-9a-f]{40}", sha): raise ValueError("head_sha must be the full 40-hex commit sha")
    return repo, int(pr), sha


def refusal(repo):
    if repo in ALLOWED: return None
    if repo.lower().startswith("xindong/"): return f"{repo} is a product repo: only Praise merges it"
    return f"{repo} is not one of {', '.join(ALLOWED)}"


def main(argv=None, opener=urllib.request.urlopen, token=None):
    try: repo, n, sha = parse(sys.argv[1:] if argv is None else argv)
    except ValueError as e:
        print(f"panoptes_merge: {e}\nusage: panoptes_merge.py <PR> <head_sha> [-R {'|'.join(ALLOWED)}]", file=sys.stderr); return EXIT_USAGE
    why = refusal(repo)
    if why: print(f"panoptes_merge: refused: {why}; nothing was sent", file=sys.stderr); return EXIT_REFUSED
    try:
        tok = (token or ghtoken.token)(repo)
        res = api("PUT", f"repos/{repo}/pulls/{n}/merge", tok, {"merge_method": "squash", "sha": sha}, opener)
        if not res.get("merged"): raise ApiError(f"merge not done: {ghtoken.mask(res.get('message'))[:200]}")
        pr = api("GET", f"repos/{repo}/pulls/{n}", tok, None, opener)
    except (ApiError, ghtoken.NoInstallation, ghtoken.MintError) as e:
        print(f"panoptes_merge: {repo}#{n}: {ghtoken.mask(e)}", file=sys.stderr); return 1
    by = (pr.get("merged_by") or {}).get("login")
    print(f"{repo}#{n} merged={pr.get('merged')} sha={sha} merge_commit={res.get('sha')} merged_by={by}")
    bot = lib.app_login()   # 11-05: derived from lib.APP_SLUG
    if not pr.get("merged") or by != bot:
        print(f"panoptes_merge: {repo}#{n}: merged_by is {by!r}, expected {bot}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__": sys.exit(main())
