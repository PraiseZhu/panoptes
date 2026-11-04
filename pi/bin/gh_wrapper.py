#!/usr/bin/env python3
"""Box-global gh wrapper (11-04, PM/Jasper: the whole test line stops using Praise's account, bot sessions included).

Installed by 电脑运维 as `gh` ahead of the real gh on PATH (see the PR / docs). Every call:
  1. red lines first, on every identity and with the flag off too: product-repo PR merges and PR title/body edits are refused (gh pr
     merge / pr edit --title|--body, REST PUT .../merge / PATCH pulls/N, graphql mergePullRequest / enablePullRequestAutoMerge /
     updatePullRequest) -> exit 4, nothing sent. Same function as lib.gh (lib.gh_red_line).
  2. flag on ($PI_STATE/gh-app-enabled AND $PI_STATE/gh-app-wrapper-enabled): GH_TOKEN = a Praise QA installation token for the
     target repo (-R/--repo, GH_REPO, `api repos/O/R`, `repo clone O/R`, else the cwd's origin): xindong/* -> 178486896,
     PraiseZhu/* -> 164262283. Unresolvable -> 178486896, and one line {at, cmd, repo} in $PI_STATE/out/gh-wrapper/unresolved.jsonl
     (never a token, never the args). A caller that already holds an installation token (ghs_*, e.g. lib.gh) keeps it.
  3. flag off, or a mint failure (one masked stderr line) -> the inherited login (PAT), exactly as without the wrapper.
Then exec the real gh ($PRAISE_QA_REAL_GH, else the first `gh` on PATH that is not this wrapper)."""
import json, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import lib  # noqa: E402

PRODUCT = "xindong/mivo-canvas-plugin"
EXIT_RED_LINE = 4


def wrapper_flag(): return f"{lib.PI_STATE}/gh-app-wrapper-enabled"
def wrapper_on(): return os.path.exists(lib.gh_app_flag()) and os.path.exists(wrapper_flag())
def unresolved_log(): return f"{lib.PI_STATE}/out/gh-wrapper/unresolved.jsonl"


def real_gh(env=os.environ):
    if env.get("PRAISE_QA_REAL_GH"): return env["PRAISE_QA_REAL_GH"]
    me = os.path.realpath(sys.argv[0] if sys.argv and sys.argv[0] else __file__)
    mine = {me, os.path.realpath(__file__), os.path.realpath(os.path.join(os.path.dirname(__file__), "gh-praise-qa"))}
    for d in (env.get("PATH") or "").split(os.pathsep):
        c = os.path.join(d, "gh")
        if os.path.isfile(c) and os.access(c, os.X_OK) and os.path.realpath(c) not in mine: return c
    return "/usr/bin/gh"


def repo_from_url(url):
    import re
    m = re.search(r"github\.com[:/]+([^/\s]+/[^/\s]+?)(?:\.git)?/?$", url or "")
    return m.group(1) if m else None


def cwd_repo(run=subprocess.run):
    try: r = run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, timeout=10)
    except Exception: return None
    return repo_from_url(r.stdout.strip()) if r.returncode == 0 else None


def target(argv, env=os.environ, run=subprocess.run):
    a = list(argv)
    t = lib.gh_target_repo(a) or (env.get("GH_REPO") or None)
    if t and t.count("/") >= 2: t = "/".join(t.split("/")[-2:])   # HOST/OWNER/REPO
    return t or cwd_repo(run)


def log_unresolved(argv):
    try:
        os.makedirs(os.path.dirname(unresolved_log()), exist_ok=True)
        with open(unresolved_log(), "a") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "cmd": ("" if argv[:1] == ["git"] else "gh ") + " ".join(x for x in argv[:2] if not x.startswith("-")), "repo": None}) + "\n")
    except OSError: pass


def app_token(repo, argv):
    """installation token for repo (unresolvable/uninstalled -> the product installation, logged), or None (masked line) on failure"""
    import ghtoken
    r = repo
    try: ghtoken.installation(r) if r else None
    except ghtoken.NoInstallation: r = None
    if not r: log_unresolved(argv); r = PRODUCT
    try: return ghtoken.token(r)
    except Exception as e:
        print(f"gh-wrapper: praise-qa token unavailable ({str(ghtoken.mask(e))[:160]}); using the inherited gh login", file=sys.stderr)
        return None


def main(argv, env=None, execve=os.execve, run=subprocess.run):
    env = dict(os.environ if env is None else env)
    repo = target(argv, env, run)
    why = lib.gh_red_line(argv, repo)
    if why:
        print(f"gh-wrapper: refused, red line: {why} (repo {repo or 'unknown'}); nothing was sent", file=sys.stderr)
        return EXIT_RED_LINE
    if wrapper_on() and not (env.get("GH_TOKEN") or "").startswith("ghs_"):
        tok = app_token(repo, argv)
        if tok:
            env["GH_TOKEN"] = tok
            for k in ("GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN"): env.pop(k, None)
    gh = real_gh(env)
    execve(gh, [gh] + list(argv), env)


if __name__ == "__main__": sys.exit(main(sys.argv[1:]) or 0)
