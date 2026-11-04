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
  2a. caller allowlist (11-04, PM/Jasper): when the calling script (the parent process's cmdline) ends with a CALLER_ALLOWLIST path,
     the call keeps the PAT: the real gh with lib.pat_env(), one line {at, caller, cmd} in $PI_STATE/out/gh-wrapper/allowlist.jsonl.
     Red lines (1) still apply. Each entry is tracked as gh-allowlist:<name> in fleet-standards/gaps-open.json, to be removed.
  3. flag off, or a mint failure (one masked stderr line) -> the inherited login (PAT), exactly as without the wrapper.
Then exec the real gh ($PRAISE_QA_REAL_GH, else the first `gh` on PATH that is not this wrapper)."""
import json, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import lib  # noqa: E402

PRODUCT = "xindong/mivo-canvas-plugin"
EXIT_RED_LINE = 4


# (name, calling-script path suffix): one entry per caller; suffix match so copies/checkouts match too. Tracked as gh-allowlist:<name>.
CALLER_ALLOWLIST = (
    ("vigil-cindy-watcher", "cindy/bin/cindy-watcher.mjs"),
    ("vigil-cindy-repair", "cindy/bin/cindy-repair.mjs"),
    ("vigil-mivo-watcher", "mivo/bin/mivo-watcher.mjs"),
    ("vigil-mivo-handoff", "mivo/bin/mivo-handoff.mjs"),
    ("lifeline-tick", "scripts/loops/lifeline/tick.mjs"),
    ("lifeline-report", "scripts/loops/lifeline/report.mjs"),
    ("lifeline-org-market-open-company-pr", "scripts/ops/org-market-open-company-pr.mjs"),
    ("mivo-merger-org-market-open-company-pr", "app/scripts/ops/org-market-open-company-pr.mjs"),
    ("grokbot-box-60-gh-auth", "verify.d/60-gh-auth.sh"),
    ("review-pr-target-branch-reminder", "review-pr/scripts/target-branch-reminder"),
    ("review-pr-request-changes-reviewed-pr", "review-pr/scripts/request-changes-reviewed-pr"),
    ("review-pr-merge-reviewed-pr", "review-pr/scripts/merge-reviewed-pr"),
    ("review-pr-approve-reviewed-pr", "review-pr/scripts/approve-reviewed-pr"),
    ("review-pr-dismiss-own-review", "review-pr/scripts/dismiss-own-review"),
    ("review-pr-pr-write-guard", "review-pr/scripts/pr-write-guard"),
    ("review-pr-signoff-release", "review-pr/scripts/signoff-release"),
    ("review-pr-dismiss-stale-reviews", "review-pr/scripts/dismiss-stale-reviews"),
    ("review-pr-notify-author-resolve", "review-pr/scripts/notify-author-resolve"),
    ("review-pr-signoff-hold", "review-pr/scripts/signoff-hold"),
)
SCRIPT_EXTS = ("", ".mjs", ".js", ".sh", ".py", ".ts")


def parent_cmdline(pid=None):
    try: return [x for x in open(f"/proc/{pid or os.getppid()}/cmdline", "rb").read().decode(errors="replace").split("\0") if x]
    except OSError: return []


def allowlisted(cmdline):
    """name of the CALLER_ALLOWLIST entry whose path suffix ends one of the caller's argv paths (extension optional for review-pr), else None"""
    for arg in cmdline:
        a = arg.replace("\\", "/")
        for name, suf in CALLER_ALLOWLIST:
            exts = SCRIPT_EXTS if "." not in os.path.basename(suf) else ("",)
            if any(a == suf + e or a.endswith("/" + suf + e) for e in exts): return name
    return None


def allowlist_log(): return f"{lib.PI_STATE}/out/gh-wrapper/allowlist.jsonl"


def log_allowlisted(name, argv):
    print(f"gh-wrapper: allowlisted caller {name}: PAT pass-through", file=sys.stderr)
    try:
        os.makedirs(os.path.dirname(allowlist_log()), exist_ok=True)
        with open(allowlist_log(), "a") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "caller": name, "cmd": "gh " + " ".join(x for x in argv[:2] if not x.startswith("-"))}) + "\n")
    except OSError: pass


def wrapper_flag(): return f"{lib.PI_STATE}/gh-app-wrapper-enabled"
def wrapper_on(): return os.path.exists(lib.gh_app_flag()) and os.path.exists(wrapper_flag())
def unresolved_log(): return f"{lib.PI_STATE}/out/gh-wrapper/unresolved.jsonl"


def real_gh(env=os.environ): return lib.real_gh(env)   # first `gh` on PATH that is not this wrapper (or $PRAISE_QA_REAL_GH)


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


def main(argv, env=None, execve=os.execve, run=subprocess.run, caller=None):
    env = dict(os.environ if env is None else env)
    repo = target(argv, env, run)
    why = lib.gh_red_line(argv, repo)
    if why:
        print(f"gh-wrapper: refused, red line: {why} (repo {repo or 'unknown'}); nothing was sent", file=sys.stderr)
        return EXIT_RED_LINE
    name = allowlisted(parent_cmdline() if caller is None else caller)
    if name:
        log_allowlisted(name, argv)
        penv = lib.pat_env(env); gh = lib.real_gh(penv)
        return execve(gh, [gh] + list(argv), penv)
    if wrapper_on() and not (env.get("GH_TOKEN") or "").startswith("ghs_"):
        tok = app_token(repo, argv)
        if tok:
            env["GH_TOKEN"] = tok
            for k in ("GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN"): env.pop(k, None)
    gh = real_gh(env)
    execve(gh, [gh] + list(argv), env)


if __name__ == "__main__": sys.exit(main(sys.argv[1:]) or 0)
