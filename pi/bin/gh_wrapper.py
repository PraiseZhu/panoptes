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
  3. flag off -> the inherited login (PAT), exactly as without the wrapper. A mint failure (11-05 R2) -> one masked stderr line,
     the alert $PI_STATE/out/stdcheck/alerts/gh_app_mint.json and exit 5: nothing sent, never the inherited (PraiseZhu) login.
Then exec the real gh ($PRAISE_QA_REAL_GH, else the first `gh` on PATH that is not this wrapper).

Install (电脑运维, at wrapper install time; see also pi/README.md "gh wrapper 安装"):
  - install this file as `gh` ahead of the real gh on PATH (name or symlink target gh-praise-qa / gh_wrapper.py, lib.WRAPPER_NAMES);
  - REQUIRED: `git config --global credential.useHttpPath true`. Without it git sends no `path=` in the credential request,
    credential_repo() returns None and `auth git-credential get` never reaches the App path (it falls back to the box login).
  - git's `store` / `erase` from non-CALLER_ALLOWLIST callers exit 0 silently (no-op); an unknown or missing op exits 4."""
import json, os, re, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import lib  # noqa: E402

PRODUCT = "xindong/mivo-canvas-plugin"
EXIT_RED_LINE = 4
EXIT_MINT_FAILED = 5   # 11-05 R2: App token mint failed, nothing sent (no PAT fallback)


# (name, calling-script path suffix): one entry per caller; suffix match so copies/checkouts match too. Tracked as gh-allowlist:<name>.
CALLER_ALLOWLIST = (
    # 11-04: grokbot-box-60-gh-auth removed (grokbot-box ebcda7536: verify.d/60-gh-auth.sh calls the real gh directly, explicit identity).
    # 11-04: vigil-cindy-watcher/-repair and vigil-mivo-watcher/-handoff removed (vigil#274 merged; vigil runs only on the Mac mini, not via this wrapper).
    ("lifeline-tick", "scripts/loops/lifeline/tick.mjs"),
    ("lifeline-report", "scripts/loops/lifeline/report.mjs"),
    # mivo-merger's app/scripts/ops/... must precede lifeline's scripts/ops/... (a shorter suffix of it): first match wins.
    ("mivo-merger-org-market-open-company-pr", "app/scripts/ops/org-market-open-company-pr.mjs"),
    ("lifeline-org-market-open-company-pr", "scripts/ops/org-market-open-company-pr.mjs"),
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
# 11-05 allowlist audit: why each lifeline / review-pr entry still needs the PAT. App installation perms (gh-app-1104/PLAN.md):
# actions/checks/statuses/metadata read, contents/issues/pull_requests write; nothing at org level. Not expanded here.
# A caller leaves CALLER_ALLOWLIST only when its reason is gone; test_gh_allowlist_audit.py keeps this table and the allowlist in step.
API_USER = "GET /user (`gh api user` viewer login): installation tokens get 403, no App permission grants it"
TEAM_MEMBERS = "GET orgs/{org}/teams/{team}[/members|/repos] (maintainer authority): org permission members:read"
PRODUCT_WRITE = "wrapper default-deny (lib.gh_product_write) refuses this caller's product writes on the App path"
PAT_KEEP_REASONS = {
    "lifeline-tick": ("`pr list --author @me`: @me becomes praise-qa[bot], PraiseZhu-authored lifeline PRs vanish (identity, not a permission)",),
    "lifeline-report": (API_USER, "checkPrOwnership compares PR author to the /user login (PraiseZhu)"),
    "review-pr-target-branch-reminder": (API_USER, PRODUCT_WRITE + ": pr comment"),
    "review-pr-request-changes-reviewed-pr": (API_USER, TEAM_MEMBERS, PRODUCT_WRITE + ": POST pulls/{n}/reviews, pulls/{n}/comments"),
    "review-pr-merge-reviewed-pr": (API_USER, TEAM_MEMBERS, PRODUCT_WRITE + ": pr merge", "merging a PR that changes .github/workflows: workflows:write"),
    "review-pr-approve-reviewed-pr": (API_USER, TEAM_MEMBERS, PRODUCT_WRITE + ": POST pulls/{n}/reviews"),
    "review-pr-dismiss-own-review": (API_USER, PRODUCT_WRITE + ": PUT reviews/{id}/dismissals (own = PraiseZhu's reviews)"),
    "review-pr-pr-write-guard": (API_USER, TEAM_MEMBERS),
    "review-pr-signoff-release": (API_USER, PRODUCT_WRITE + ": pr comment"),
    "review-pr-dismiss-stale-reviews": (API_USER, PRODUCT_WRITE + ": PUT reviews/{id}/dismissals"),
    "review-pr-notify-author-resolve": (API_USER, PRODUCT_WRITE + ": pr comment"),
    "review-pr-signoff-hold": ("writes go through pr-write-guard (" + API_USER + ")", PRODUCT_WRITE + ": pr comment, issue create"),
}
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
# 11-04 acceptance (#222): gh/cobra skips flags (and their values) before the verb, so `gh auth -h github.com login` runs login.
# The verb is the first non-flag word after the noun; flags before it must be known (PRE_VERB_*), else the command is unparseable
# and refused. For auth / config only known reads pass for non-allowlisted callers (config get/list, auth status without -t).
PRE_VERB_VALUE = frozenset({"-h", "--hostname", "--host", "-R", "--repo"}); PRE_VERB_BOOL = frozenset({"--help"})
AUTH_READS = frozenset({"status"}); CONFIG_READS = frozenset({"get", "list", "ls"})
EXTENSION_NOUNS = ("extension", "extensions", "ext"); EXTENSION_READS = frozenset({"list", "ls", "search", "browse"})
GUARDED_NOUNS = frozenset({"auth", "config", "alias", *EXTENSION_NOUNS})
def split_verb(rest):
    """rest = argv after the noun -> (verb, args after the verb), verb "" when there is none; None when a flag before the verb is
    unknown, `--`, or a value flag misses its value (unparseable: callers refuse)."""
    i = 0
    while i < len(rest):
        x = rest[i]
        if not x.startswith("-") or x == "-": return x, rest[i + 1:]
        if x in PRE_VERB_BOOL: i += 1; continue
        if x in PRE_VERB_VALUE:
            if i + 1 >= len(rest): return None
            i += 2; continue
        if "=" in x and x.split("=", 1)[0] in PRE_VERB_VALUE and x.startswith("--"): i += 1; continue
        if not x.startswith("--") and len(x) > 2 and x[:2] in PRE_VERB_VALUE: i += 1; continue   # -hgithub.com
        return None
    return "", []
CRED_HOST_FLAGS = ("-h", "--hostname")
def credential_op(rest):
    """args after `auth git-credential` -> the operation (get/store/erase/...): first non-flag word, "" when none.
    --hostname/-h and its value are skipped (`-h x`, `--hostname x`, `--hostname=x`, `-hx`): the host is never taken for the op."""
    a = list(rest); i = 0
    while i < len(a):
        x = a[i]
        if x in CRED_HOST_FLAGS: i += 2; continue
        if x.startswith("-"): i += 1; continue
        return x
    return ""
CRED_NOOP_OPS = frozenset({"store", "erase"})
def git_credential_noop(argv):
    """True for `gh [flags] auth [flags] git-credential [flags] store|erase`: for a non-CALLER_ALLOWLIST caller this is git's own
    bookkeeping after an auth (push/fetch); the wrapper exits 0 silently, sends nothing, keeps nothing (F2, 11-04)"""
    a = list(argv)
    if a[:1] != ["auth"]: return False
    sv = split_verb(a[1:])
    return bool(sv) and sv[0] == "git-credential" and credential_op(sv[1]) in CRED_NOOP_OPS
def git_credential_get(argv):
    """True for `gh [flags] auth [flags] git-credential [flags] get`"""
    a = list(argv)
    if a[:1] != ["auth"]: return False
    sv = split_verb(a[1:])
    return bool(sv) and sv[0] == "git-credential" and credential_op(sv[1]) == "get"
def credential_repo(text):
    """git credential request -> OWNER/REPO for https://github.com/<path>, else None (needs credential.useHttpPath)"""
    d = dict(l.split("=", 1) for l in (text or "").splitlines() if "=" in l)
    if d.get("protocol") != "https" or d.get("host") != "github.com": return None
    path = (d.get("path") or "").strip("/")
    if path.endswith(".git"): path = path[:-4]
    return "/".join(path.split("/")[:2]) if path.count("/") >= 1 else None
def shows_token(args):
    return any(x in ("-t", "--show-token") or x.startswith("--show-token=") or (x.startswith("-") and not x.startswith("--") and "t" in x[1:]) for x in args)
def cli_refusal(argv, allowlisted_name=None):
    """-> reason when the wrapper refuses this gh command for this caller (ruling E above), else None"""
    a = list(argv); noun = a[0] if a else ""
    if noun.startswith("-") and GUARDED_NOUNS & set(a) and not allowlisted_name:
        return "gh flags before the command name (unparseable; only CALLER_ALLOWLIST callers)"
    if noun not in GUARDED_NOUNS: return None
    sv = split_verb(a[1:])
    if noun == "alias":
        if sv is None: return "gh alias with unknown flags before the verb (unparseable)"
        if sv[0] in ("set", "import"): return f"gh alias {sv[0]} (an alias can expand to any command later)"
        return None
    if noun in EXTENSION_NOUNS:
        if sv is None: return f"gh {noun} with unknown flags before the verb (unparseable; extensions run arbitrary code with the login)"
        if sv[0] not in EXTENSION_READS: return f"gh {noun} {sv[0]} (extensions run arbitrary code with the login)".replace("  ", " ")
        return None
    if allowlisted_name: return None
    if sv is None: return f"gh {noun} with unknown flags before the verb (unparseable; only CALLER_ALLOWLIST callers)"
    verb, rest = sv
    if noun == "config":
        if verb not in CONFIG_READS: return f"gh config {verb or ''} (only config get/list for non-CALLER_ALLOWLIST callers)".replace("  ", " ")
        return None
    if verb == "git-credential":   # 11-04 #222: git's credential.helper (`!gh auth git-credential`) runs as every caller
        if credential_op(rest) in ("get",) or credential_op(rest) in CRED_NOOP_OPS: return None   # store/erase: silent no-op in main
        return f"gh auth git-credential {credential_op(rest) or ''} (unknown or missing credential op; only get, and store/erase as no-ops, for non-CALLER_ALLOWLIST callers)".replace("  ", " ")
    if verb not in AUTH_READS:
        if verb == "token": return "gh auth token (prints the login token; only CALLER_ALLOWLIST callers)"
        return f"gh auth {verb or ''} (changes the box login / git credentials; only auth status for non-CALLER_ALLOWLIST callers)".replace("  ", " ")
    if shows_token(a[1:]): return "gh auth status --show-token (prints the login token; only CALLER_ALLOWLIST callers)"
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
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "caller": name, "cmd": "gh " + " ".join(x for x in argv[:2] if not x.startswith("-")),
                                "argv": [lib._TOKEN_RE.sub("***", str(x)) for x in argv]}) + "\n")   # 11-05: full argv for attribution
    except OSError as e: print(f"gh-wrapper: allowlist log write failed ({e.__class__.__name__})", file=sys.stderr)


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
    try: tok = ghtoken.token(r)
    except Exception as e:   # 11-05 R2 gap 3: an error + alert, never the inherited (PraiseZhu) gh login
        msg = lib._TOKEN_RE.sub("***", str(ghtoken.mask(e)))[:160]
        print(f"gh-wrapper: praise-qa token unavailable ({msg}); refused, not falling back to the inherited gh login", file=sys.stderr)
        lib.gh_app_mint_alert(msg, source="gh_wrapper", argv=argv)
        raise lib.GhAppMintFailed(msg) from None
    lib.gh_app_mint_alert(None)
    return tok


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
    if not name and wrapper_on():   # G5 (11-05): @me -> the bot identity, bare `api user` -> praise-qa[bot] locally (lib.self_identity_*)
        argv = lib.self_identity_args(argv)
        me = lib.self_identity_answer(argv)
        if me is not None: sys.stdout.write(me); sys.stdout.flush(); return 0
        if argv[:1] == ["search"]: repo = lib.search_repo(argv)   # G5: gh search -> the panoptes installation, never the cwd / product guess
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
    if not name and git_credential_noop(argv):
        try: sys.stdin.read() if stdin is None else None   # drain git's request; nothing is stored, nothing is sent
        except Exception: pass
        return 0
    if not name and git_credential_get(argv):
        # same design as git-credential-praise-qa: App token only for the request's repo when it has an installation (never the
        # cwd repo, never the PRODUCT fallback); otherwise the real gh answers with the box login, stdin re-fed untouched
        try: req = sys.stdin.read() if stdin is None else stdin
        except Exception: req = ""
        r = credential_repo(req)
        pw = lib.product_write_identity(["git", "push"], r) if r else None   # 11-05: xindong/* credentials follow the product-writes flag
        try: tok = app_token(r, ["git", "credential"], no_install_is_none=True) if r and wrapper_on() and pw != "pat" else None
        except lib.GhAppMintFailed: return EXIT_MINT_FAILED   # no answer -> git fails; the PAT helper is never asked
        if tok:
            sys.stdout.write(f"protocol=https\nhost=github.com\nusername=x-access-token\npassword={tok}\n"); sys.stdout.flush()
            return 0
        why = ("credential: repo unresolved" if not r else "credential: switch off" if not wrapper_on()
               else "credential: product writes flag off" if pw == "pat" else "credential: no App installation")
        lib.log_pat(why, ["git", "credential", "get", r or "?"], source="gh_wrapper", caller=script_of(chain))   # no token, host/repo only
        refeed_stdin(req)
        if pw == "pat": env = lib.pat_env(env)   # never an inherited installation token for a xindong/* credential while the flag is off
        gh = real_gh(env)
        return execve(gh, [gh] + list(argv), env)
    if body is not None: refeed_stdin(body)
    if name:
        log_allowlisted(name, argv)
        penv = lib.pat_env(env); gh = lib.real_gh(penv)
        return execve(gh, [gh] + list(argv), penv)
    pw = lib.product_write_identity(argv, repo)   # 11-05: the same decision as lib.gh_env for xindong/* writes (G2 rerun included)
    if pw == "pat":
        lib.log_pat("product writes flag off", argv, source="gh_wrapper", caller=script_of(chain))
        penv = lib.pat_env(env); gh = lib.real_gh(penv)
        return execve(gh, [gh] + list(argv), penv)
    if wrapper_on() and not (env.get("GH_TOKEN") or "").startswith("ghs_"):
        try: tok = app_token(repo, argv)
        except lib.GhAppMintFailed: return EXIT_MINT_FAILED
        if tok:
            env["GH_TOKEN"] = tok
            for k in ("GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN"): env.pop(k, None)
    else:   # the inherited login (default hosts.yml = PraiseZhu) unless the caller already carries an installation token
        if not (env.get("GH_TOKEN") or "").startswith("ghs_"): lib.log_pat("switch off" if not wrapper_on() else "default login", argv, source="gh_wrapper", caller=script_of(chain))
    gh = real_gh(env)
    execve(gh, [gh] + list(argv), env)


if __name__ == "__main__": sys.exit(main(sys.argv[1:]) or 0)
