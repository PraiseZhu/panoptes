#!/usr/bin/env python3
"""Box-global gh wrapper (11-04, PM/Jasper: the whole test line stops using Praise's account, bot sessions included).

Installed by 电脑运维 as `gh` ahead of the real gh on PATH (see the PR / docs). Every call:
  1. red lines first, on every identity and caller (allowlisted PAT pass-through too) and with the flag off: product PR merges,
     branch moves (pr update-branch, REST update-branch / merges / git refs), PR title/body edits, and any `gh api` call that cannot
     be parsed -> exit 4, nothing sent. One exception (#214 r3, PM): pi/bin/outbound.py's plain update-branch of a qa-fix/* PR
     (head repo = product, base main; checked by a real-gh read first), lib.update_branch_exception. Same function as lib.gh (lib.gh_red_line). Then lib.gh_product_write (wrapper only), DEFAULT
     DENY on the product repo (or an unknown target): only reads, pr create --draft, pr ready, run rerun, REST .../actions/...,
     PRODUCT_WRITE_EXCEPTIONS and review-pr comments/reviews pass. A body on stdin is read, checked, and re-fed to the real gh.
  2. flag on ($PI_STATE/gh-app-enabled AND $PI_STATE/gh-app-wrapper-enabled): GH_TOKEN = a Praise QA installation token for the
     target repo (-R/--repo, GH_REPO, `api repos/O/R`, `repo clone O/R`, else the cwd's origin): xindong/* -> 178486896,
     PraiseZhu/* -> 164262283. Unresolvable -> 178486896, and one line {at, cmd, repo} in $PI_STATE/out/gh-wrapper/unresolved.jsonl
     (never a token, never the args). A caller that already holds an installation token (ghs_*, e.g. lib.gh) keeps it.
  2a. caller allowlist (11-04, PM/Jasper): when the calling SCRIPT (script_of the parent's cmdline, realpath; a `sh -c` parent -> up to 3 ancestors) ends with a CALLER_ALLOWLIST path,
     the call keeps the PAT: the real gh with lib.pat_env(), one line {at, caller, cmd} in $PI_STATE/out/gh-wrapper/allowlist.jsonl.
     Red lines (1) still apply. Each entry is tracked as gh-allowlist:<name> in fleet-standards/gaps-open.json, to be removed.
  3. flag off, or a mint failure (one masked stderr line) -> the inherited login (PAT), exactly as without the wrapper.
Then exec the real gh ($PRAISE_QA_REAL_GH, else the first `gh` on PATH that is not this wrapper)."""
import json, os, re, subprocess, sys, time
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
    # mivo-merger's app/scripts/ops/... must precede lifeline's scripts/ops/... (a shorter suffix of it): first match wins.
    ("mivo-merger-org-market-open-company-pr", "app/scripts/ops/org-market-open-company-pr.mjs"),
    ("lifeline-org-market-open-company-pr", "scripts/ops/org-market-open-company-pr.mjs"),
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
# 11-04 (#214, PM ruling): product-write exceptions per caller x operation, never a whole script. The caller is the calling SCRIPT
# (script_of(caller_chain()), realpath, path suffix as for CALLER_ALLOWLIST). These callers are NOT PAT pass-through (not allowlisted):
# only the listed `gh <noun> <verb>` skips lib.gh_product_write; pr close / review / merge / ref delete / REST / graphql stay refused.
PRODUCT_WRITE_EXCEPTIONS = (
    ("panoptes-outbound", "pi/bin/outbound.py", frozenset({("issue", "create"), ("pr", "comment"), ("issue", "close")})),
    # infra_outbound's issue comment: matches only when infra_outbound.py is the process script (normally it is imported by
    # outbound.py, whose ops above do not include issue comment); its target is pr-qa-agent-deploy, not a product repo, anyway.
    ("panoptes-infra-outbound", "pi/bin/infra_outbound.py", frozenset({("issue", "comment")})),
)


# #214 r4 ruling E: gh commands that change what a later `gh <x>` runs, or print the login token, refused in the wrapper (exit 4):
#   alias set/import (an alias can expand to anything later), every extension subcommand except list/search/browse (install /
#   upgrade / exec / create / remove: an extension runs arbitrary code with the login), and `auth token` / `auth status
#   --show-token|-t` unless the caller is on CALLER_ALLOWLIST (box grep 11-04: no allowlisted caller uses it; see the PR body).
# 11-04: `config set` and the login-changing auth verbs are CALLER_ALLOWLIST-only too; reads (config get/list, auth status) stay open.
AUTH_WRITES = frozenset({"login", "setup-git", "switch", "refresh"})
EXTENSION_NOUNS = ("extension", "extensions", "ext"); EXTENSION_READS = frozenset({"list", "ls", "search", "browse"})
def cli_refusal(argv, allowlisted_name=None):
    """-> reason when the wrapper refuses this gh command for this caller (ruling E above), else None"""
    a = list(argv); noun = a[0] if a else ""; verb = a[1] if len(a) > 1 else ""
    if noun == "alias" and verb in ("set", "import"): return f"gh alias {verb} (an alias can expand to any command later)"
    if noun in EXTENSION_NOUNS and verb not in EXTENSION_READS: return f"gh {noun} {verb or ''} (extensions run arbitrary code with the login)".replace("  ", " ")
    if noun == "config" and verb == "set" and not allowlisted_name: return "gh config set (changes how every later gh call runs; only CALLER_ALLOWLIST callers)"
    if noun == "auth" and not allowlisted_name:
        if verb in AUTH_WRITES: return f"gh auth {verb} (changes the box login / git credentials; only CALLER_ALLOWLIST callers)"
        if verb == "token": return "gh auth token (prints the login token; only CALLER_ALLOWLIST callers)"
        if verb == "status" and any(x in ("-t", "--show-token") or x.startswith("--show-token=") or (x.startswith("-") and not x.startswith("--") and "t" in x[1:]) for x in a[2:]):
            return "gh auth status --show-token (prints the login token; only CALLER_ALLOWLIST callers)"
    return None


def write_exceptions(cmdline):
    """frozenset of (noun, verb) product writes excused for the caller's SCRIPT (exact path-suffix match), else empty"""
    s = script_of(cmdline)
    if not s: return frozenset()
    a = s.replace("\\", "/")
    for _, suf, ops in sorted(PRODUCT_WRITE_EXCEPTIONS, key=lambda e: -len(e[1])):
        if a == suf or a.endswith("/" + suf): return ops
    return frozenset()


SCRIPT_EXTS = ("", ".mjs", ".js", ".sh", ".py", ".ts")


INTERPRETERS = re.compile(r"^(node|nodejs|deno|bun|tsx|ts-node|python[0-9.]*|bash|sh|dash|zsh|ksh|perl|ruby)$")
SHELLS = ("bash", "sh", "dash", "zsh", "ksh")
MAX_SHELL_HOPS = 3


def parent_cmdline(pid=None):
    try: return [x for x in open(f"/proc/{pid or os.getppid()}/cmdline", "rb").read().decode(errors="replace").split("\0") if x]
    except OSError: return []


def parent_pid(pid):
    try: return int(open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError): return 0


def is_shell_c(cmdline):
    return bool(cmdline) and os.path.basename(cmdline[0]) in SHELLS and any(x == "-c" or (x.startswith("-") and not x.startswith("--") and "c" in x) for x in cmdline[1:2] + cmdline[1:3])


def caller_chain(pid=None):
    """the caller's cmdline: the parent, or, when the parent is `sh -c`/`bash -c ...`, the first non-`-c` ancestor at most
    MAX_SHELL_HOPS levels up (a script that runs `sh -c "gh ..."`)"""
    p = pid or os.getppid()
    for _ in range(MAX_SHELL_HOPS + 1):
        c = parent_cmdline(p)
        if not is_shell_c(c): return c
        p = parent_pid(p)
        if p <= 1: return []
    return []


# #214 r3 (PM ruling 4): options that take a VALUE (skipped with it: a preload is never the script) and options that mean "no script"
NODE_VALUE_OPTS = frozenset({"-r", "--require", "--import", "--loader", "--experimental-loader", "--env-file", "--input-type", "-C",
                             "--conditions", "--title", "--inspect-port", "--max-old-space-size", "--stack-size", "--watch-path",
                             "--experimental-config-file", "--localstorage-file", "--disable-warning"})
NO_SCRIPT_OPTS = frozenset({"-e", "--eval", "-p", "--print", "-c", "-m", "-i", "--interactive", "-"})
PY_VALUE_OPTS = frozenset({"-W", "-X", "-Q"})
SHELL_VALUE_OPTS = frozenset({"-o", "-O", "+o", "+O", "--rcfile", "--init-file"})


def script_of(cmdline):
    """the script the interpreter actually EXECUTES, realpath'd: argv[0] when it is no interpreter, else the first operand after the
    interpreter's options, skipping preload options AND their values (node --require/-r/--import/--loader/..., python -W/-X, sh -o).
    -e/-p/--eval/--print/-c (inline code), python -m (a module), -i, stdin ("-") -> None. Unknown --opt=value is one token."""
    if not cmdline: return None
    if not INTERPRETERS.match(os.path.basename(cmdline[0])): return os.path.realpath(cmdline[0]) if "/" in cmdline[0] else cmdline[0]
    base = os.path.basename(cmdline[0])
    py = base.startswith("python"); shell = base in SHELLS
    value_opts = PY_VALUE_OPTS if py else SHELL_VALUE_OPTS if shell else NODE_VALUE_OPTS
    a = cmdline[1:]; i = 0
    while i < len(a):
        x = a[i]; i += 1
        if x == "--":
            return os.path.realpath(a[i]) if i < len(a) else None
        if x in NO_SCRIPT_OPTS: return None
        if x.startswith("--"):
            if x.split("=", 1)[0] in NO_SCRIPT_OPTS: return None   # --eval=code / --print=code
            if "=" in x: continue
            if x in value_opts: i += 1
            continue
        if x.startswith("-") or (shell and x.startswith("+")):
            if py and len(x) > 2 and x[1] in "WXQ": continue                 # -Werror, -Xdev: value attached
            if py and any(c in x[1:] for c in "cm"): return None           # -c / -m (also bundled: -Ic, -um)
            if not py and not shell and len(x) > 2 and x[:2] in ("-e", "-p"): return None   # -e'code' / -pcode
            if not py and not shell and len(x) > 2 and x[:2] in ("-r", "-C"): continue       # -r./preload.js: value attached
            if shell and "c" in x[1:]: return None
            if x in value_opts: i += 1
            continue
        if base in ("deno", "bun") and x in ("run", "x") and i < len(a): continue   # `deno run [opts] script`
        return os.path.realpath(x)
    return None


def allowlisted(cmdline):
    """name of the CALLER_ALLOWLIST entry whose path suffix ends the caller's SCRIPT (script_of: never other argv), longest suffix
    first; extension optional for review-pr. None otherwise"""
    s = script_of(cmdline)
    if not s: return None
    a = s.replace("\\", "/")
    for name, suf in sorted(CALLER_ALLOWLIST, key=lambda e: -len(e[1])):
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


def app_token(repo, argv, no_install_is_none=False):
    """installation token for repo (unresolvable/uninstalled -> the product installation, logged), or None (masked line) on failure"""
    import ghtoken
    r = repo
    try: ghtoken.installation(r) if r else None
    except ghtoken.NoInstallation:
        if no_install_is_none: return None   # credential helper: a repo with no installation -> silent, git asks the PAT helper
        r = None
    if not r: log_unresolved(argv); r = PRODUCT
    try: return ghtoken.token(r)
    except Exception as e:
        print(f"gh-wrapper: praise-qa token unavailable ({str(ghtoken.mask(e))[:160]}); using the inherited gh login", file=sys.stderr)
        return None


def refeed_stdin(data):
    """put the request body the wrapper read from fd 0 back on fd 0 for the real gh (an in-memory file; nothing on disk)"""
    fd = os.memfd_create("gh-wrapper-stdin")
    os.write(fd, data.encode() if isinstance(data, str) else data); os.lseek(fd, 0, os.SEEK_SET)
    os.dup2(fd, 0); os.close(fd)


def main(argv, env=None, execve=os.execve, run=subprocess.run, caller=None, stdin=None):
    env = dict(os.environ if env is None else env)
    repo = target(argv, env, run)
    chain = caller_chain() if caller is None else caller
    name = allowlisted(chain)
    # #214 r2 B1: a body on stdin (--input -, field=@-) is read here, checked like any other body, then re-fed to the real gh
    body = None
    if lib.gh_reads_stdin(argv):
        try: body = sys.stdin.read() if stdin is None else stdin
        except Exception: body = None   # unreadable -> the guards see no body -> refused (fail closed)
    # every caller, allowlisted (PAT pass-through) and excused ones included, goes through both guards first
    ub = lib.update_branch_exception(argv, repo, script_of(chain))   # #214 r3: outbound's qa-fix/* update-branch only
    why = cli_refusal(argv, name) or (None if ub == "allow" else ub or lib.gh_red_line(argv, repo, stdin=body) or lib.gh_product_write(argv, repo, name, excused=write_exceptions(chain), stdin=body))
    if why:
        print(f"gh-wrapper: refused, red line: {why} (repo {repo or 'unknown'}); nothing was sent", file=sys.stderr)
        return EXIT_RED_LINE
    if body is not None: refeed_stdin(body)
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
