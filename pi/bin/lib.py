import json, os, subprocess, sys, glob, datetime as dt, re, shutil, tempfile, time
REPO = "xindong/mivo-canvas-plugin"
# Where things live (10-24: the Pi line moved into PraiseZhu/panoptes pi/; three kinds of place, see pi/README.md):
#   PI_CODE   this pi/ directory (code, conf, cron, tests) -- deployed with the /workspace/panoptes checkout
#   PI_STATE  data to keep: out/, takeover/, outbound-enabled, outbound-disabled-types, ops-repair-enabled (backed up hourly)
#   PI_WORK   throw-away work: clones/, wt/, cache/, tmp/, .trash/, locks/ (not backed up, cleaned in seconds)
PI_CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PI_STATE = os.environ.get("PI_STATE") or "/workspace/mivo-qa/pi"
PI_WORK = os.environ.get("PI_WORK") or "/workspace/pi-work"
BASE = PI_STATE   # historic name: every f"{BASE}/out/...", takeover/, outbound-* path is state; code paths use PI_CODE
QA = "/workspace/mivo-qa"
RPT = f"{QA}/reports/merged"
CST = dt.timezone(dt.timedelta(hours=8))

def log(*a): print(dt.datetime.now(CST).strftime("%H:%M:%S"), *a, flush=True)

def data_day(argv):
    if len(argv) > 1: return argv[1]
    return (dt.datetime.now(CST).date() - dt.timedelta(days=1)).isoformat()

def utc_window(day):
    d = dt.date.fromisoformat(day)
    a = dt.datetime(d.year, d.month, d.day, tzinfo=CST).astimezone(dt.timezone.utc)
    b = a + dt.timedelta(days=1)
    f = lambda x: x.strftime("%Y-%m-%dT%H:%M:%SZ")
    return f(a), f(b)

# 11-03 (prwatch:error:drafts 10:44): one GitHub 5xx / network blip failed a read and the cause was lost -- the full command came
# first and prwatch cuts errors at 200 chars, so only 'Post "https://api.github.com/g' was left. Now the real stderr comes first
# (command after it, shortened), and a READ that failed with a 5xx / timeout / dropped connection is retried once. Writes never retry.
GH_TRANSIENT_RE = re.compile(r"HTTP 5\d\d|\b50[0234]\b|timed? ?out|deadline exceeded|\bEOF\b|connection (?:reset|refused)|"
                             r"TLS handshake|i/o timeout|stream error|server error", re.I)
GH_READS = {("pr", "list"), ("pr", "view"), ("pr", "checks"), ("pr", "diff"), ("issue", "list"), ("issue", "view"),
            ("repo", "view"), ("run", "list"), ("run", "view"), ("label", "list")}
GH_WRITE_FLAGS = {"-X", "--method", "-f", "-F", "--field", "--raw-field", "--input"}
def gh_is_read(args):
    if tuple(args[:2]) in GH_READS: return True
    return bool(args) and args[0] == "api" and not any(a.split("=", 1)[0] in GH_WRITE_FLAGS for a in args[1:])
# 11-04 (pi-work/ratelimit-1104/FINDINGS.md): PraiseZhu's GraphQL quota ran out and every job kept sending doomed gh calls each
# minute (a rate-limit error matched nothing above: no backoff). Now a primary or secondary rate limit records its reset time in
# GH_RL_FILE; until then gh() raises GhRateLimited WITHOUT calling GitHub, jobs skip their GitHub steps (one log line, never an
# error/streak) and watchdog reports one gh:ratelimit item once skipping has lasted > GH_RL_ALERT_H. The next success removes the file.
from time import time as _epoch   # not lib.time.time: tests stub lib.time with a sleep-only object
GH_RL_RE = re.compile(r"rate limit|HTTP 429|status(?: code)?:? 429|\b429 Too Many Requests|abuse detection|retry-after", re.I)
GH_RL_FILE = f"{PI_STATE}/gh-ratelimit.json"
GH_RL_ALERT_H = 2
GH_RL_MAX_S = 2 * 3600   # a recorded reset is never further away than this (a bogus Retry-After / reset must not wedge every job)
class GhRateLimited(RuntimeError): pass
def gh_rl_state(f=None):
    """the state dict; a missing file is {}, a corrupt one (not a dict / non-numeric until or since) is ignored with one log line"""
    GH_RL_FILE_ = f or GH_RL_FILE
    try: d = json.load(open(GH_RL_FILE_))
    except FileNotFoundError: return {}
    except (OSError, ValueError) as e: log(f"{GH_RL_FILE_} unreadable ({e.__class__.__name__}), ignored"); return {}
    num = lambda v: v is None or (isinstance(v, (int, float)) and not isinstance(v, bool))
    if not isinstance(d, dict) or not num(d.get("until")) or not num(d.get("since")): log(f"{GH_RL_FILE_} malformed, ignored"); return {}
    return d
def gh_rl_until(now=None, f=None):
    """epoch the GitHub calls may resume, or 0 when not rate-limited. Writes cap until at now+GH_RL_MAX_S, so a stored until further
    out is bogus: ignored (one log line), never clamped on read -- a read clamp would roll forward to now+2h forever"""
    now = now or _epoch(); u = gh_rl_state(f).get("until") or 0
    if u > now + GH_RL_MAX_S + 60: log(f"{f or GH_RL_FILE} until is > {GH_RL_MAX_S}s away, ignored"); return 0
    return u if u > now else 0
def _gh_rl_reset(err, env=None):
    """epoch from Retry-After / X-RateLimit-Reset in err, else from `gh api rate_limit` (free, not counted), else a short default"""
    now = _epoch()
    m = re.search(r"retry-after:?\s*(\d+)", err, re.I)
    if m: return now + int(m.group(1))
    m = re.search(r"x-ratelimit-reset:?\s*(\d{9,})", err, re.I)
    if m: return float(m.group(1))
    if re.search(r"secondary|abuse", err, re.I): return now + 60
    try:
        r = subprocess.run(["gh", "api", "rate_limit"], capture_output=True, text=True, timeout=30, env=env)   # env: the identity that hit it
        res = json.loads(r.stdout).get("resources") or {}
        ts = [v.get("reset") or 0 for v in res.values() if isinstance(v, dict) and v.get("remaining") == 0]
        if ts and max(ts) > now: return float(max(ts))
    except Exception: pass
    return now + 900
def gh_rl_record(err, f=None, env=None):
    """err is a GitHub rate limit -> record the reset in GH_RL_FILE (keeping 'since' of an ongoing streak) and return True"""
    if not GH_RL_RE.search(err or ""): return False
    old = gh_rl_state(f); until = min(_gh_rl_reset(err, env), _epoch() + GH_RL_MAX_S)
    write_atomic(f or GH_RL_FILE, json.dumps({"since": old.get("since") or _epoch(), "until": until, "error": err[:200]}))
    return True
def gh_rl_clear(f=None):
    try: os.remove(f or GH_RL_FILE)
    except FileNotFoundError: pass

# 11-04 (pi-work/gh-app-1104 P1): GitHub App "Praise QA" for PraiseZhu/panoptes READS, behind the flag file $PI_STATE/gh-app-enabled
# (read at call time). Flag absent, a repo without an App route, an identity-dependent call (@me, /user) or a token that cannot be
# minted -> the default gh login (PAT), exactly as before. The App has its own quota, so its rate-limit marker is a separate file.
GH_APP_REPOS = {"PraiseZhu/panoptes"}      # + every PraiseZhu/* repo (task B: installation 164262283 covers the owner), gh_app_own_repo()
def gh_app_own_repo(repo): return bool(repo) and (repo in GH_APP_REPOS or (repo.count("/") == 1 and repo.split("/")[0] == "PraiseZhu"))
def gh_app_flag(): return f"{PI_STATE}/gh-app-enabled"
def gh_app_rl_file(): return f"{PI_STATE}/gh-ratelimit-praise-qa.json"
# #214 r4 blocker A: gh takes the LAST -R/--repo when one is repeated (`gh pr view 1 -R a -R b` -> b) while a guard reading the first
# one would check a different repo. Any second repo selector, in any spelling (-R X, -RX, -R=X, --repo X, --repo=X, mixed), or a
# github.com/O/R/pull/N selector next to an -R naming another repo, is refused by the red line, the product-write guard and the
# update-branch lookup, for every caller (gh_repo_selector_dup). gh_target_repo returns None for such a call (never a guessed repo).
RED_MULTI = "more than one repo selector (-R/--repo repeated, or a URL selector with another -R): gh uses the last, refused"
def gh_repo_selectors(args):
    """every -R/--repo value of a non-`api` gh call, in order (every token is scanned; a selector's value is consumed with it)"""
    a = list(args); out = []; i = 0
    if a[:1] == ["api"]: return out
    while i < len(a):
        x = a[i]; i += 1
        if x in ("-R", "--repo"): out.append(a[i] if i < len(a) else ""); i += 1
        elif x.startswith("--repo="): out.append(x.split("=", 1)[1])
        elif x.startswith("-R") and len(x) > 2 and not x.startswith("--"): out.append(x[2:].lstrip("="))
    return out
def _url_selector(args):
    for x in list(args)[1:]:
        m = re.match(r"^(?:https?://)?(?:www\.)?github\.com/([^/\s]+/[^/\s]+)/(?:pull|issues|actions/runs)/\d+", x, re.I)
        if m: return m.group(1)
    return None
def gh_repo_selector_dup(args):
    """RED_MULTI when the call names its repo more than once (see above), else None"""
    a = list(args)
    if a[:1] == ["api"]: return None   # gh api has no -R: gh_api_parse refuses it as an unknown flag
    sel = gh_repo_selectors(a)
    if len(sel) > 1: return f"{RED_MULTI} ({len(sel)} -R/--repo)"
    u = _url_selector(a)
    if u and sel and sel[0].lower().split("/")[-2:] != u.lower().split("/")[-2:]: return f"{RED_MULTI} (URL selector + -R)"
    return None
def api_placeholder(r):
    """True when a repos/O/R owner or name is a gh placeholder ({owner}/{repo} or :owner/:repo): gh fills it from GH_REPO / the cwd"""
    return "{" in r or any(seg.startswith(":") for seg in r.split("/"))
def gh_target_repo(args):
    """OWNER/REPO a gh call targets: a github.com/O/R/(pull|issues)/N selector, -R/--repo X (also -RX, -R=X, --repo=X), `repo
    view|clone X`, or `api <path>` naming repos/OWNER/REPO (full URLs, case and query strings normalized); None if unknown.
    `repos/{owner}/{repo}` placeholders -> None (gh fills them from GH_REPO / the cwd, which the wrapper resolves)."""
    a = list(args)
    if gh_repo_selector_dup(a): return None   # r4 A: never guess which of two repos gh will use
    if a[:1] != ["api"]:
        u = _url_selector(a)
        if u: return u
    for i, x in enumerate(a):
        if x in ("-R", "--repo") and i + 1 < len(a): return a[i + 1]
        if x.startswith("--repo="): return x.split("=", 1)[1]
        if x.startswith("-R") and len(x) > 2 and not x.startswith("--"): return x[2:].lstrip("=")
    if a[:1] == ["repo"] and len(a) > 2 and a[1] in ("view", "clone") and "/" in a[2]: return a[2]
    if a[:1] == ["api"]:
        try: p = gh_api_parse(a)
        except GhUnparseable: return None
        r = api_path_repo(p["path"])
        if r: return None if api_placeholder(r) else r   # r4 B: :owner/:repo too
        if p["path"].lower() == "graphql":   # 11-04: graphql names its repo in the variables (owner/name, o/n)
            try: _, vs = gql_query_of(a)
            except GhUnparseable: return None
            o = next((vs[k] for k in ("owner", "o", "repoOwner") if vs.get(k)), None)
            n = next((vs[k] for k in ("name", "n", "repo", "repoName") if vs.get(k)), None)
            if o and n and "/" not in o and "/" not in n: return f"{o}/{n}"
    return None
# P3 (11-04, PM 17:00): product-repo READS too, while $PI_STATE/gh-app-product-enabled also exists. Product WRITES stay on the PAT:
# outbound/prfix dedupe their fix PRs with author:@me and gate pushes on author == PraiseZhu, so an App-authored product PR would be
# invisible to them (duplicate PRs). Those gates move first, then writes.
GH_APP_PRODUCT_REPOS = {"xindong/mivo-canvas-plugin"}
def gh_app_product_flag(): return f"{PI_STATE}/gh-app-product-enabled"
def gh_app_wanted(args, repo=None):
    """True when this call should use the App token (see above and task B below). repo: explicit target for calls that do not name
    one (`api rate_limit` checks the quota of the identity that will serve the repo)"""
    repo = repo or gh_target_repo(args)
    if not os.path.exists(gh_app_flag()): return False
    # G5 (11-05): `@me` is rewritten (self_identity_args) and bare `api user` answered locally (self_identity_answer) before this is
    # asked; whatever identity-dependent call is still left here (/user/<sub>, an @me a caller passed to gh_env directly) stays on the PAT
    if any("@me" in x or re.match(r"/?user(?:$|/)", x) for x in args[1:]): return False
    if args[:1] == ["search"]: return True   # G5: gh search (no repo of its own) -> the App, panoptes installation (search_repo)
    if repo in GH_APP_PRODUCT_REPOS:
        if gh_is_read(args): return os.path.exists(gh_app_product_flag()) or gh_component_on()
        return gh_app_product_writes_on()
    if not gh_app_own_repo(repo): return False
    return gh_is_read(args) or gh_app_writes_ok(args) or gh_component_on()
# 11-04 task B: every remaining caller behind its own flag (each also needs gh-app-enabled); rm the flag -> that caller is back on the PAT.
#   gh-app-product-writes-enabled  outbound/prfix product writes: gh pr create/comment/ready, issue writes, fix-branch clone + git push
#   gh-app-merger-enabled / gh-app-mergewatch-enabled / gh-app-nightly-enabled / gh-app-ledgers-enabled  that job's gh calls (the job
#   names itself with gh_component(); node/shell jobs get GH_TOKEN from `ghtoken.py env-for <component> <repo>`)
GH_APP_COMPONENTS = ("merger", "mergewatch", "nightly", "ledgers")
_COMPONENT = [os.environ.get("PI_GH_COMPONENT") or None]
def gh_component(name):
    assert name in GH_APP_COMPONENTS, name
    _COMPONENT[0] = name
def gh_component_flag(name): return f"{PI_STATE}/gh-app-{name}-enabled"
def gh_component_on(name=None):
    c = name or _COMPONENT[0]
    return bool(c) and os.path.exists(gh_app_flag()) and os.path.exists(gh_component_flag(c))
def gh_component_node_env(name, repo=REPO):
    """node/shell jobs (ledgers -> build-ledger.mjs / track-followups.mjs, nightly -> argus): {PRAISE_QA_GH_TOKEN, PRAISE_QA_GH_REPOS}
    while that job's flag is on and the token mints, else {} (= the PAT). Those scripts use the token ONLY for calls whose target repo
    is PRAISE_QA_GH_REPOS; every other repo (handoff repos, pr-qa-agent-deploy) keeps the inherited login."""
    if not gh_component_on(name): return {}
    try:
        import ghtoken
        # + PRAISE_QA_GH_OWN_TOKEN for every PraiseZhu/* repo (installation 164262283: handoff / deploy issue repos)
        return {"PRAISE_QA_GH_TOKEN": ghtoken.token(repo), "PRAISE_QA_GH_REPOS": repo, "PRAISE_QA_GH_OWN_TOKEN": ghtoken.token("PraiseZhu/panoptes")}
    except Exception as e:
        try: import ghtoken as _g; msg = _g.mask(e)
        except Exception: msg = e.__class__.__name__
        log(f"{name}: gh app token unavailable ({str(msg)[:160]}); node tools stay on the default gh login"); return {}
def gh_app_product_writes_flag(): return f"{PI_STATE}/gh-app-product-writes-enabled"
def gh_app_product_writes_on(): return os.path.exists(gh_app_flag()) and os.path.exists(gh_app_product_writes_flag())
# Red lines (Praise, unchanged): only Praise merges product PRs, and no bot edits a product PR body. No code path does either, on ANY
# identity: gh() refuses these before a token is chosen.
class RedLine(RuntimeError): pass
RED_MERGE = "merging a product PR (only Praise merges)"; RED_EDIT = "editing a product PR body/title"
RED_BRANCH = "moving a product branch (update-branch / merges / git refs: only Praise)"; RED_PARSE = "gh request that cannot be parsed or classified (fail closed)"
_ID_LIT = re.compile(r"\b\w*Ids?\s*:\s*(\[[^\]]*\]|\"[^\"]*\"|\$\w+)")
_ID_KV = re.compile(r"[\"']?\w*Ids?[\"']?\s*[:=]\s*(\[[^\]]*\]|[\"'][^\"']*[\"'])")
def gql_node_ids(doc, vs):
    """node ids a mutation document carries: `xxxId: "..."` literals, `xxxId: $v` variables, and xxxId keys inside JSON/dict
    variables (`input: $input`). -> list, or None when an id-ish argument cannot be read (fail closed)"""
    ids, bad = [], False
    def take(v):
        got = re.findall(r"[\"']([^\"']+)[\"']", v) if v[:1] in "[\"'" else [v]
        ids.extend(x for x in got if x)
    for v in _ID_LIT.findall(doc):
        if v.startswith("$"):
            if v[1:] in vs and vs[v[1:]]: take(str(vs[v[1:]])) if str(vs[v[1:]])[:1] in "[\"'" else ids.append(str(vs[v[1:]]))
            else: bad = True
        else: take(v)
    for k, v in vs.items():
        if re.search(r"Ids?$", k) and v and not str(v)[:1] in "[{": ids.append(str(v))
        for m in _ID_KV.findall(str(v)): take(m)
    for m in re.findall(r"\binput\s*:\s*\$(\w+)", doc):
        if m not in vs: bad = True
    return None if bad else list(dict.fromkeys(ids))
GQL_NODE_Q = ("query{nodes(ids:%s){__typename ... on Repository{nameWithOwner} ... on PullRequest{repository{nameWithOwner}}"
              " ... on Issue{repository{nameWithOwner}} ... on Ref{repository{nameWithOwner}} ... on IssueComment{repository{nameWithOwner}}"
              " ... on PullRequestReview{repository{nameWithOwner}} ... on PullRequestReviewComment{repository{nameWithOwner}}}}")
def gql_node_repos(ids):
    """[OWNER/REPO or None per id] by one `nodes(ids:)` query on the real gh (never the wrapper) with the inherited login; any error
    -> all None. Tests patch this."""
    try:
        r = subprocess.run([real_gh(), "api", "graphql", "-f", "query=" + GQL_NODE_Q % json.dumps(list(ids))], capture_output=True, text=True, timeout=30)
        ns = (json.loads(r.stdout or "{}").get("data") or {}).get("nodes") or []
    except Exception: return [None] * len(ids)
    out = [((n or {}).get("nameWithOwner") or ((n or {}).get("repository") or {}).get("nameWithOwner")) for n in ns]
    return (out + [None] * len(ids))[:len(ids)]
def is_product_repo(r): return r in GH_APP_PRODUCT_REPOS or (r or "").lower().startswith("xindong/")
def gql_ids_product(doc, vs):
    """False = every node id resolves to a non-product repo; True = some id is product; None = no id / unresolvable"""
    ids = gql_node_ids(doc, vs)
    if not ids: return None
    repos = gql_node_repos(ids)
    if any(is_product_repo(r) for r in repos): return True
    return None if any(r is None for r in repos) else False
# ---- #214 acceptance round 2 (11-04, PM): ONE parser for `gh api`, and a DEFAULT-DENY product-repo write guard ----------------------
# Every spelling gh accepts is parsed (-XPUT, -X=PUT, --method=put, -fk=v, -Fk=v, --raw-field=k=v, --field k=v, bool clusters such
# as -iXPUT, full https://api.github.com/... URLs, query strings, %-escapes, case). A flag we do not know, a second endpoint, or a
# body we cannot read (stdin without the wrapper's copy, an unreadable @file) raises GhUnparseable, and every guard refuses it.
class GhUnparseable(ValueError): pass
API_VALUE_FLAGS = {"X": "method", "method": "method", "f": "field", "raw-field": "field", "F": "field", "field": "field",
                   "H": "header", "header": "header", "input": "input", "q": "jq", "jq": "jq", "t": "template", "template": "template",
                   "hostname": "hostname", "p": "preview", "preview": "preview", "cache": "cache"}
API_BOOL_FLAGS = {"i", "include", "paginate", "slurp", "silent", "verbose", "h", "help"}
def gh_api_parse(args):
    """`gh api ...` -> {method (upper, None if not given), path (normalized, case kept), fields [(k, v)], input (None | path | "-")}"""
    a = list(args)[1:]; out = {"method": None, "fields": [], "input": None, "endpoint": []}
    def put(name, v):
        if name == "method": out["method"] = v.upper()
        elif name == "field":
            if "=" not in v: raise GhUnparseable(f"field without '=': {v[:40]}")
            out["fields"].append(tuple(v.split("=", 1)))
        elif name == "input": out["input"] = v
    i = 0
    while i < len(a):
        x = a[i]; i += 1
        if x == "--": out["endpoint"] += a[i:]; break
        if x.startswith("--"):
            k, eq, v = x[2:].partition("=")
            if k in API_BOOL_FLAGS: continue
            if k not in API_VALUE_FLAGS: raise GhUnparseable(f"unknown flag --{k}")
            if not eq:
                if i >= len(a): raise GhUnparseable(f"--{k} without a value")
                v = a[i]; i += 1
            put(API_VALUE_FLAGS[k], v); continue
        if x.startswith("-") and len(x) > 1:
            j = 1
            while j < len(x):
                c = x[j]; j += 1
                if c in API_BOOL_FLAGS: continue
                if c not in API_VALUE_FLAGS: raise GhUnparseable(f"unknown flag -{c}")
                v = x[j:]
                if v.startswith("="): v = v[1:]
                if not x[j:]:
                    if i >= len(a): raise GhUnparseable(f"-{c} without a value")
                    v = a[i]; i += 1
                put(API_VALUE_FLAGS[c], v); break
            continue
        out["endpoint"].append(x)
    if len(out["endpoint"]) != 1: raise GhUnparseable(f"{len(out['endpoint'])} endpoints")
    out["path"] = api_path(out.pop("endpoint")[0])
    return out
def api_path(ep):
    """endpoint -> path without scheme/host (any host, GHE /api/v3), leading /, query string, fragment, %-escapes, // and trailing /"""
    import urllib.parse
    p = urllib.parse.unquote(ep.strip())
    p = re.sub(r"^[a-z][a-z0-9+.-]*://[^/]*", "", p, flags=re.I)
    p = re.split(r"[?#]", p, maxsplit=1)[0]
    p = re.sub(r"/+", "/", p).strip("/")
    return re.sub(r"^api/v3/", "", p, flags=re.I)
def api_path_repo(path):
    m = re.match(r"repos/([^/]+/[^/]+)", path, re.I)
    return m.group(1) if m else None
def api_target(p, hint=None):
    """the repo a parsed REST call hits: repos/O/R in its path; repos/{owner}/{repo} or repos/:owner/:repo (#214 r4 B: gh substitutes
    the colon form too, also :branch) -> hint (gh fills it from GH_REPO / cwd; None when unresolvable = fail closed); else None"""
    r = api_path_repo(p["path"])
    return (hint if api_placeholder(r) else r) if r else None
def api_is_write(p): return bool(p["fields"] or p["input"] is not None or (p["method"] or "GET") not in ("GET", "HEAD"))
GQL_MERGE_RE = re.compile(r"\b(mergePullRequest|enablePullRequestAutoMerge|enqueuePullRequest|mergeBranch|updatePullRequestBranch|updateRef|updateRefs)\s*\(")
GQL_EDIT_RE = re.compile(r"\bupdatePullRequest\s*\(")
def gql_query_of(args, read=open, stdin=None):
    """the GraphQL document of `gh api graphql ...`: every query= field (also =@file and =@- from stdin) and --input FILE|- (JSON
    {"query": ...} or raw); comments stripped, whitespace collapsed. -> (query, variables dict). stdin: the text the wrapper read from
    fd 0 (None = not available). Unreadable stdin/file or an unparseable call -> GhUnparseable (callers refuse)."""
    p = gh_api_parse(args); q, vs = [], {}
    def body(src):
        if src == "-":
            if stdin is None: raise GhUnparseable("request body on stdin")
            return stdin
        try: return read(src).read()
        except Exception as e: raise GhUnparseable(f"unreadable {src[:60]}") from e
    for k, v in p["fields"]:
        if v.startswith("@"): v = body(v[1:])
        (q.append(v) if k == "query" else vs.__setitem__(k, v))
    if p["input"] is not None:
        raw = body(p["input"])
        try:
            d = json.loads(raw); q.append(str(d.get("query") or "")); vs.update({k: str(v) for k, v in (d.get("variables") or {}).items()})
        except Exception: q.append(raw)
    doc = re.sub(r"#[^\n]*", " ", "\n".join(q)); doc = re.sub(r"\s+", " ", doc)
    return doc, vs
def gql_mutation_fields(doc):
    """the field names a mutation document calls (`name(`), minus the operation keyword"""
    return {n for n in re.findall(r"\b([A-Za-z_]\w*)\s*\(", doc) if n not in ("mutation", "query", "subscription")}
RP = r"(?:repos/[^/]+/[^/]+|repositories/\d+)"   # a repo-scoped REST prefix (by name or by numeric id)
def gh_red_line(args, repo=None, read=open, stdin=None):
    """-> reason if args would merge a product PR or move a product branch (merge, update-branch, merges, ref PATCH/POST/PUT/DELETE),
    edit a product PR body/title, or cannot be parsed, else None. Every identity, every caller (allowlisted / PAT pass-through too),
    lib.gh and the wrapper. repo: the caller's known target (the wrapper passes GH_REPO / the cwd's origin); a merge-class call whose repo
    cannot be resolved at all is refused too (fail closed)."""
    a = list(args); tgt = repo or gh_target_repo(a)
    if a[:1] == ["api"]:
        try: p = gh_api_parse(a)
        except GhUnparseable as e: return f"{RED_PARSE} ({e})"
        if p["path"].lower() == "graphql":
            try: doc, vs = gql_query_of(a, read, stdin)
            except GhUnparseable as e: return f"{RED_PARSE} (graphql: {e})"
            merge, edit = GQL_MERGE_RE.search(doc), GQL_EDIT_RE.search(doc) and (re.search(r"\b(body|title)\s*:", doc) or any(k in vs for k in ("body", "title")))
            if not (merge or edit): return None
            # a mutation names its PR by node id, so cwd / GH_REPO / owner+name vars say nothing about the repo it hits: it runs only when
            # EVERY node id it carries resolves (a `nodes` query) to a non-product repo; no id / unresolvable / product id -> refused.
            if gql_ids_product(doc, vs) is False: return None
            return (RED_MERGE if merge else RED_EDIT) + " (graphql, node id not resolved to a non-product repo)"
        at = api_target(p, repo); path = p["path"].lower()
        if at is not None and not is_product_repo(at): return None
        if not api_is_write(p): return None
        meth = p["method"] or "POST"; fields = {k for k, _ in p["fields"]}
        if re.match(RP + r"/pulls/\d+/merge$", path): return RED_MERGE
        if re.match(RP + r"/pulls/\d+/update-branch$", path): return RED_BRANCH + " (update-branch)"
        if re.match(RP + r"/merges$", path): return RED_BRANCH + " (merges API)"
        # git refs/objects writes move branches (PATCH refs -F force=true = updateRef). DELETE of a ref is a product WRITE refused by
        # the wrapper (gh_product_write; janitor's product ref deletes stay refused there, PM ruling), not a lib.gh red line.
        if re.match(RP + r"/git/", path) and meth != "DELETE": return RED_BRANCH + " (git refs/objects API)"
        if re.match(RP + r"/pulls/\d+$", path) and meth != "GET": return RED_EDIT
        if re.match(RP + r"/issues/\d+$", path) and meth != "GET" and (p["input"] is not None or {"title", "body"} & fields): return RED_EDIT + " (issues API)"
        return None
    dup = gh_repo_selector_dup(a)
    if dup: return dup
    product = is_product_repo(tgt); unknown = tgt is None
    if not (product or unknown): return None
    if a[:2] == ["pr", "merge"]: return RED_MERGE
    if a[:2] == ["pr", "update-branch"]: return RED_BRANCH + " (gh pr update-branch)"
    if a[:2] in (["pr", "edit"], ["issue", "edit"]) and any(re.match(r"^(--body|--body-file|--title)(=|$)|^-[btF]", x) for x in a[2:]):
        return RED_EDIT
    return None
# Product WRITES beyond the red lines (#214 2c, PM ruling; round 2: DEFAULT DENY). On the product repo -- or when the target is unknown
# (the wrapper would mint the product installation for it) -- a call passes only if it matches PRODUCT_ALLOWED below. Everything else,
# including any gh subcommand or REST/graphql spelling nobody listed, is refused. No enumeration of bad spellings to keep up to date.
RED_WRITE = "writing to the product repo (not on the product-write allowlist)"
PRODUCT_READ_CMDS = {"pr": {"view", "list", "status", "diff", "checks", "checkout"}, "issue": {"view", "list", "status"},
                     "run": {"view", "list", "watch", "download"}, "workflow": {"view", "list"}, "release": {"view", "list", "download"},
                     "repo": {"view", "clone"}, "label": {"list"}, "cache": {"list"}, "search": None, "browse": None, "status": None}
NON_REPO_CMDS = {"auth", "config", "version", "--version", "help", "--help", "completion", "alias", "extension", "extensions", "ext", "gist", "org"}   # alias/extension writes, auth token, auth login/setup-git/switch/refresh, config set: gh_wrapper.cli_refusal
PRODUCT_WRITE_EXCUSABLE = frozenset({("issue", "create"), ("issue", "comment"), ("issue", "close"), ("pr", "comment")})   # never pr close/review
REVIEW_PR_CMDS = frozenset({("issue", "comment"), ("pr", "comment"), ("pr", "review")})   # B5 ruling: review-pr may approve
REVIEW_PR_REST = re.compile(r"repos/[^/]+/[^/]+/(issues/\d+/comments|pulls/\d+/comments(/\d+/replies)?|pulls/\d+/reviews"
                            r"(/\d+(/events|/dismissals)?)?)$")
REVIEW_PR_REST_METHODS = ("POST", "PUT")
ACTIONS_RERUN = re.compile(r"repos/[^/]+/[^/]+/actions/(runs/\d+/(rerun|rerun-failed-jobs)|jobs/\d+/rerun)$")
REVIEW_PR_GQL = frozenset({"addComment", "addPullRequestReview", "submitPullRequestReview", "addPullRequestReviewComment",
                           "addPullRequestReviewThread"})
def gh_product_write(args, repo=None, caller=None, read=open, excused=frozenset(), stdin=None):
    """-> reason unless args are on the product-write allowlist, else None. Applies when the target is the product repo or unknown
    (graphql mutations: unless every node id resolves to a non-product repo). Allowed: reads (gh read subcommands, REST without method
    /fields/body, graphql without a mutation); gh pr create --draft; gh pr ready (--undo too); gh run rerun and REST POST actions/runs/N/rerun(-failed-jobs), actions/jobs/N/rerun;
    excused: the caller x `gh <noun> <verb>` exceptions (gh_wrapper.PRODUCT_WRITE_EXCEPTIONS, within PRODUCT_WRITE_EXCUSABLE);
    caller review-pr-*: comments and reviews (approve included), gh / REST / graphql."""
    a = list(args); review_pr = (caller or "").startswith("review-pr-")
    if a[:1] == ["api"]:
        try: p = gh_api_parse(a)
        except GhUnparseable as e: return f"{RED_PARSE} ({e})"
        path = p["path"].lower()
        if path == "graphql":
            try: doc, vs = gql_query_of(a, read, stdin)
            except GhUnparseable as e: return f"{RED_PARSE} (graphql: {e})"
            if not re.search(r"\bmutation\b", doc): return None
            if review_pr and gql_mutation_fields(doc) and gql_mutation_fields(doc) <= REVIEW_PR_GQL: return None
            return None if gql_ids_product(doc, vs) is False else RED_WRITE + " (graphql mutation; node ids not all resolved to a non-product repo)"
        at = api_target(p, repo)
        if at is not None and not is_product_repo(at): return None
        if not api_is_write(p): return None
        if at is None and not re.match(r"repos/(\{|:)", path): return RED_WRITE + " (REST write whose target repo is unknown)"
        # #214 r4 ruling C: of the Actions API only the three rerun endpoints (POST); dispatches, secrets, variables, permissions,
        # cancel, ... are refused. GETs under /actions/ passed above (not a write).
        if at is not None and ACTIONS_RERUN.match(path) and (p["method"] or "POST") == "POST": return None
        if review_pr and (p["method"] or "POST") in REVIEW_PR_REST_METHODS and REVIEW_PR_REST.match(path): return None
        return RED_WRITE + f" ({p['method'] or 'POST'} {path})"
    noun = a[0] if a else ""
    dup = gh_repo_selector_dup(a)
    if dup: return dup
    if noun in NON_REPO_CMDS: return None
    tgt = repo or gh_target_repo(a)
    if tgt is not None and not is_product_repo(tgt): return None
    verb = a[1] if len(a) > 1 else ""
    if noun in PRODUCT_READ_CMDS and (PRODUCT_READ_CMDS[noun] is None or verb in PRODUCT_READ_CMDS[noun]): return None
    if (noun, verb) == ("pr", "create") and any(x in ("--draft", "-d") or x == "--draft=true" for x in a[2:]): return None
    if (noun, verb) in (("pr", "ready"), ("run", "rerun")): return None   # r4 ruling D: `pr ready --undo` included
    if (noun, verb) in excused and (noun, verb) in PRODUCT_WRITE_EXCUSABLE: return None
    if review_pr and (noun, verb) in REVIEW_PR_CMDS: return None
    return RED_WRITE + f" (gh {noun} {verb})".rstrip()
# #214 r3 (PM ruling 1): the ONE update-branch exception. Caller pi/bin/outbound.py (exact realpath suffix, as PRODUCT_WRITE_EXCEPTIONS:
# the wrapper's script_of(caller_chain()), lib.gh's own process script), shape `gh pr update-branch N` (no --rebase) or REST
# PUT repos/<product>/pulls/N/update-branch (only expected_head_sha), and a live read of the PR through the real gh: headRefName
# qa-fix/*, head repo == the product repo (no fork), baseRefName main. Anything else stays the red line (fail closed).
UB_CALLER = "pi/bin/outbound.py"; UB_HEAD_PREFIX = "qa-fix/"; UB_BASE = "main"
def update_branch_pr(args, repo=None):
    """-> (pr, None) when args are an exception-shaped update-branch of the product repo, (None, why) when they are some other
    update-branch of it, (None, None) when they are no product update-branch at all"""
    a = list(args)
    if a[:1] == ["api"]:
        try: p = gh_api_parse(a)
        except GhUnparseable: return None, None
        m = re.match(r"repos/([^/]+/[^/]+)/pulls/(\d+)/update-branch$", p["path"], re.I)
        if not m: return None, None
        at = api_target(p, repo)
        if at is not None and not is_product_repo(at): return None, None
        if at != PRODUCT_REPO_UB: return None, f"update-branch target {at or 'unknown'} is not {PRODUCT_REPO_UB}"
        if (p["method"] or "POST") != "PUT": return None, "update-branch not via PUT"
        if p["input"] is not None or any(k != "expected_head_sha" for k, _ in p["fields"]): return None, "update-branch with a body/field other than expected_head_sha (rebase/update_method refused)"
        return int(m.group(2)), None
    if a[:2] != ["pr", "update-branch"]: return None, None
    dup = gh_repo_selector_dup(a)   # r4 A: the lookup would check one repo and gh would update another
    if dup: return None, dup
    tgt = repo or gh_target_repo(a)
    if tgt is not None and not is_product_repo(tgt): return None, None
    if tgt != PRODUCT_REPO_UB: return None, f"update-branch target {tgt or 'unknown'} is not {PRODUCT_REPO_UB}"
    rest, n, i = a[2:], None, 0
    while i < len(rest):
        x = rest[i]; i += 1
        if x in ("-R", "--repo"): i += 1; continue
        if x.startswith("--repo=") or (x.startswith("-R") and len(x) > 2): continue
        if x.startswith("-"): return None, f"gh pr update-branch {x.split('=')[0]} refused (only the plain merge form)"
        if n is not None or not x.isdigit(): return None, "gh pr update-branch needs exactly one PR number"
        n = int(x)
    if n is None: return None, "gh pr update-branch without a PR number"
    return n, None
PRODUCT_REPO_UB = REPO
def is_ub_caller(script):
    s = (script or "").replace("\\", "/")
    return s == UB_CALLER or s.endswith("/" + UB_CALLER)
def ub_lookup(n, env=None, run=subprocess.run):
    """the PR's head/base through the REAL gh (never the wrapper; a read): -> dict, raises on any failure"""
    r = run([real_gh(env), "pr", "view", str(int(n)), "-R", PRODUCT_REPO_UB, "--json", "headRefName,baseRefName,headRepository,headRepositoryOwner,isCrossRepository"],
            capture_output=True, text=True, timeout=60, env=env)
    if r.returncode != 0: raise RuntimeError(f"pr view #{n} failed")
    return json.loads(r.stdout)
def update_branch_exception(args, repo=None, script=None, lookup=None):
    """-> "allow" (skip the red line and the product-write guard), a refusal reason, or None (not a product update-branch)"""
    n, why = update_branch_pr(args, repo)
    if n is None and why is None: return None
    if not is_ub_caller(script): return f"{RED_BRANCH} (update-branch: caller {script or 'unknown'} is not {UB_CALLER})"
    if why: return f"{RED_BRANCH} ({why})"
    try: p = (lookup or ub_lookup)(n)
    except Exception as e: return f"{RED_BRANCH} (update-branch #{n}: PR lookup failed: {e.__class__.__name__})"
    p = p or {}
    head = p.get("headRefName") or ""; base = p.get("baseRefName") or ""
    hrepo = f"{(p.get('headRepositoryOwner') or {}).get('login') or ''}/{(p.get('headRepository') or {}).get('name') or ''}"
    if not head.startswith(UB_HEAD_PREFIX): return f"{RED_BRANCH} (update-branch #{n}: head {head or '?'} is not {UB_HEAD_PREFIX}*)"
    if p.get("isCrossRepository") is not False or hrepo.lower() != PRODUCT_REPO_UB.lower(): return f"{RED_BRANCH} (update-branch #{n}: head repo {hrepo} is not {PRODUCT_REPO_UB})"
    if base != UB_BASE: return f"{RED_BRANCH} (update-branch #{n}: base {base or '?'} is not {UB_BASE})"
    return "allow"
def self_script():
    """lib.gh's caller script: this process's argv[0], realpath'd (outbound.py when run as `python3 .../outbound.py`)"""
    return os.path.realpath(sys.argv[0]) if sys.argv and sys.argv[0] else None
def gh_reads_stdin(args):
    """True when a `gh api` call takes a body from stdin (--input -, a field =@-): the wrapper reads it once, checks it and re-feeds it"""
    if args[:1] != ["api"]: return False
    try: p = gh_api_parse(args)
    except GhUnparseable: return False
    return p["input"] == "-" or any(v == "@-" for _, v in p["fields"])
# Explicit identities (task B 4: no @me / `api user` anywhere). Our own PRs are authored by PraiseZhu (PAT, older) or by the App.
PAT_LOGIN = "PraiseZhu"
# 11-05 (PM): the App slug lives in ONE constant; every bot form is derived from it at call time (patch APP_SLUG alone -> all follow)
APP_SLUG = "praise-qa"   # also hard-coded in JS: argus/src/github.mjs and pi/ledger/build-ledger.mjs (change all three together)
def app_login(): return f"{APP_SLUG}[bot]"        # REST login (merged_by, comment user)
def app_author(): return f"app/{APP_SLUG}"        # gh --author / search author: / gh --json author.login
def self_logins(): return frozenset({PAT_LOGIN, app_login(), app_author(), APP_SLUG})   # REST login / gh --json author.login / slug
def self_authors(): return (PAT_LOGIN, app_author())                                    # gh --author / search author: values
def self_bot(): return {"login": app_login(), "type": "Bot", "html_url": f"https://github.com/apps/{APP_SLUG}"}
def is_self(login):
    if isinstance(login, dict): login = login.get("login")
    return (login or "") in self_logins()
def self_pr_list(repo, state, fields, search=None, limit=100, extra=(), run=None):
    """our own PRs = union over self_authors() (deduped by number): a dedupe never misses an old PraiseZhu PR nor a new App PR"""
    seen = {}
    for au in self_authors():
        q = [f"{search} author:{au}"] if search else []
        args = ["pr", "list", "-R", repo, "--state", state, "--limit", str(limit), "--json", fields] + list(extra)
        args += (["--search", q[0]] if q else ["--author", au])
        for p in (run or gh)(args) or []: seen.setdefault(p.get("number"), p)   # run: the caller's gh (tests stub module.gh)
    return sorted(seen.values(), key=lambda p: -(p.get("number") or 0))
# P2 (11-04): panoptes WRITES (PR create / merge / comment / branch delete, git push of infra branches) use the App too while BOTH
# $PI_STATE/gh-app-enabled and $PI_STATE/gh-app-writes-enabled exist. rm gh-app-writes-enabled -> writes back on the PAT (reads stay).
def gh_app_writes_flag(): return f"{PI_STATE}/gh-app-writes-enabled"
def gh_app_writes_ok(args): return os.path.exists(gh_app_writes_flag())
def gh_app_writes_on(): return os.path.exists(gh_app_flag()) and os.path.exists(gh_app_writes_flag())
# 11-04 (#214): PAT health checks bypass the box gh wrapper (pi/bin/gh-praise-qa on PATH as `gh`): the real binary by absolute path,
# and the PAT explicitly (secrets GITHUB_TOKEN when it is a PAT, else the real gh's own hosts.yml login), never an installation token.
WRAPPER_NAMES = ("gh-praise-qa", "gh_wrapper.py")
def real_gh(env=None):
    env = os.environ if env is None else env
    if env.get("PRAISE_QA_REAL_GH"): return env["PRAISE_QA_REAL_GH"]
    for d in (env.get("PATH") or "").split(os.pathsep):
        c = os.path.join(d, "gh")
        if os.path.isfile(c) and os.access(c, os.X_OK) and os.path.basename(os.path.realpath(c)) not in WRAPPER_NAMES: return os.path.realpath(c)
    return "/usr/bin/gh"
def pat_env(env=None):
    env = dict(os.environ if env is None else env)
    for k in ("GH_TOKEN", "GH_ENTERPRISE_TOKEN"): env.pop(k, None)
    t = env.pop("GITHUB_TOKEN", None)
    if t and not t.startswith("ghs_"): env["GH_TOKEN"] = t   # the PAT from secrets.env; an installation token is dropped
    return env
GIT_NET_READS = ("fetch", "ls-remote", "clone")
def git_read_env(repo):
    """task B: env for a git network READ (fetch / ls-remote / clone) of repo: the App token through gh's credential helper (GIT_CONFIG_*
    env, so no .git/config is edited) while that repo's App read route is on (lib.gh_app_wanted of a read), else None = default creds"""
    ident, env = gh_env(["api", f"repos/{repo}"])
    if ident != "app": return None
    env.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_COUNT="2", GIT_CONFIG_KEY_0="credential.helper", GIT_CONFIG_VALUE_0="",
               GIT_CONFIG_KEY_1="credential.helper", GIT_CONFIG_VALUE_1="!gh auth git-credential")
    return env
def git_push_env(repo):
    """-> (git -c args, env) for a git push to repo: the App token through gh's credential helper while the write flags are on,
    else ([], None) = the box's default credentials (PAT) exactly as before"""
    ident, env = gh_env(["repo", "clone", repo])   # "repo clone" is not a read: needs the (product-)writes flag
    if ident != "app": return [], None
    return ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"], env
# G5 (11-05, acceptance 1105 §1: "@me / api user / gh search still PAT"): while gh-app-enabled exists these go through the App with the
# bot identity hard-coded, never resolved by GitHub: `@me` -> app/praise-qa in author positions (--author, author:) and praise-qa[bot]
# elsewhere (assignee, reviewer, mentions, involves...); bare `gh api user` is answered locally with self_bot() (an installation token
# cannot call /user, and the PAT would answer PraiseZhu); `gh search` uses the panoptes installation (or the --repo it filters on).
_AUTHOR_ME = re.compile(r"(?i)(\bauthor:)@me\b"); _ANY_ME = re.compile(r"(?<![\w@])@me\b")
def self_identity_args(args):
    """args with every @me replaced by the hard-coded bot identity (flag off -> unchanged, the PAT answers as before)"""
    a = list(args)
    if not os.path.exists(gh_app_flag()) or not any("@me" in x for x in a): return a
    out = []
    for i, x in enumerate(a):
        prev = a[i - 1] if i else ""
        if x == "@me" and prev in ("--author", "-A"): out.append(app_author()); continue
        if x.startswith("--author=@me"): out.append("--author=" + app_author()); continue
        x = _AUTHOR_ME.sub(lambda m: m.group(1) + app_author(), x)
        out.append(_ANY_ME.sub(app_login(), x))
    return out
def self_identity_answer(args):
    """bare `gh api user` (GET, optional --jq/-q/-i) while gh-app-enabled exists -> the stdout gh would print for self_bot(), else None"""
    if not os.path.exists(gh_app_flag()) or args[:1] != ["api"]: return None
    try: p = gh_api_parse(args)
    except GhUnparseable: return None
    if api_path(p["path"]).strip("/").lower() != "user" or api_is_write(p): return None
    jq = next((args[i + 1] for i, x in enumerate(args[:-1]) if x in ("--jq", "-q")), None) or next((x.split("=", 1)[1] for x in args if x.startswith("--jq=")), None)
    if jq in (".login", ".login // empty"): return self_bot()["login"] + "\n"
    if jq in (".type",): return self_bot()["type"] + "\n"
    return json.dumps(self_bot()) + "\n"
def search_repo(args):
    """installation for `gh search`: the repo it filters on (--repo X, when the App has one), else panoptes"""
    a = list(args)
    for i, x in enumerate(a):
        r = a[i + 1] if x in ("-R", "--repo") and i + 1 < len(a) else x.split("=", 1)[1] if x.startswith("--repo=") else None
        if r and (gh_app_own_repo(r) or r in GH_APP_PRODUCT_REPOS): return r
    return REPO_APP_DEFAULT
REPO_APP_DEFAULT = "PraiseZhu/panoptes"
# G2 (11-05, Jasper ruling): product-repo CI reruns are the ONE PAT exception (the App gets no Actions write). Exactly two shapes, target
# repo resolved to the product repo (-R / --repo / URL, or repos/<product>/ in the REST path; unknown target -> no exception):
#   gh run rerun [<id>] [--failed] [--job <id>] ...                         (noun "run", verb "rerun")
#   gh api POST repos/<product>/actions/runs/<N>/rerun | .../runs/<N>/rerun-failed-jobs | .../jobs/<N>/rerun   (ACTIONS_RERUN, method POST)
# run cancel / run delete / actions/runs/N/cancel, a GET on a rerun path, any other repo -> not this exception (App or PAT as before).
def pat_rerun(args, repo=None):
    a = list(args)
    if gh_repo_selector_dup(a): return False
    if a[:1] == ["api"]:
        try: p = gh_api_parse(a)
        except GhUnparseable: return False
        path = api_path(p["path"]).lower()
        if not api_is_write(p) or (p["method"] or "POST").upper() != "POST" or not ACTIONS_RERUN.fullmatch(path): return False
        return _rerun_repo(api_path_repo(path))
    if a[:2] != ["run", "rerun"]: return False
    return _rerun_repo(repo or gh_target_repo(a))
def _rerun_repo(r): return bool(r) and r.lower() in {x.lower() for x in GH_APP_PRODUCT_REPOS}
def gh_env(args, repo=None):
    """-> (identity "app"|"pat", env for subprocess or None = inherit). Minting failures fall back to the PAT with one masked line."""
    if pat_rerun(args, repo): return "pat", pat_env()   # G2: product CI rerun -> the PAT explicitly (an inherited ghs_ token is dropped)
    if not gh_app_wanted(args, repo): return "pat", None
    try:
        import ghtoken
        tok = ghtoken.token(search_repo(args) if args[:1] == ["search"] and not repo else repo or gh_target_repo(args))
    except Exception as e:
        try: import ghtoken as _g; msg = _g.mask(e)
        except Exception: msg = e.__class__.__name__
        log(f"gh app token unavailable ({str(msg)[:160]}); using the default gh login for this call"); return "pat", None
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN")}
    env["GH_TOKEN"] = tok
    return "app", env
def gh_skip(job):
    """True (and one log line) when GitHub is rate-limited: the caller skips its GitHub steps this run"""
    u = gh_rl_until()
    if u: log(f"{job}: GitHub rate-limited until {dt.datetime.fromtimestamp(u, CST).strftime('%H:%M:%S')} CST, skipping GitHub steps")
    return bool(u)
def gh(args, js=True, retries=1, repo=None):
    """repo: target for calls that name none (api rate_limit / graphql); picks the identity (and its rate-limit file) of that repo"""
    args = self_identity_args(args)   # G5: @me -> the bot, before any guard or identity choice
    cmd = f"gh {' '.join(args[:2])}"
    me = self_identity_answer(args)
    if me is not None: return json.loads(me) if js else me   # G5: bare `api user` -> praise-qa[bot], nothing sent
    ub = update_branch_exception(args, repo, self_script())
    why = None if ub == "allow" else (ub or gh_red_line(args, repo))
    if why: raise RedLine(f"{cmd}: refused, red line: {why}")
    ident, env = gh_env(args, repo)
    rlf = gh_app_rl_file() if ident == "app" else None   # None = GH_RL_FILE (the PAT's)
    u = gh_rl_until(f=rlf)
    if u: raise GhRateLimited(f"{cmd}: GitHub rate-limited until {dt.datetime.fromtimestamp(u, CST).strftime('%H:%M:%S')} CST (not sent)")
    for left in range(retries if gh_is_read(args) else 0, -1, -1):
        try: r = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=120, env=env)
        except subprocess.TimeoutExpired:
            if left: print(f"{cmd}: timeout after 120s, retrying once", file=sys.stderr, flush=True); continue
            raise RuntimeError(f"{cmd}: timeout after 120s [{' '.join(args)[:120]}]")
        if r.returncode == 0:
            if os.path.exists(rlf or GH_RL_FILE): gh_rl_clear(rlf)
            return json.loads(r.stdout) if js else r.stdout
        err = (r.stderr or "").strip()
        if ident == "app" and re.search(r"HTTP 401|Bad credentials", err):
            try: import ghtoken; ghtoken.invalidate(repo or gh_target_repo(args))
            except Exception: pass
        if gh_rl_record(err, rlf, env): raise GhRateLimited(f"{cmd}: {err[:300]} [{' '.join(args)[:120]}]")
        if left and GH_TRANSIENT_RE.search(err): print(f"{cmd}: {err[:200]} -- retrying once", file=sys.stderr, flush=True); time.sleep(2); continue
        raise RuntimeError(f"{cmd}: {err[:300]} [{' '.join(args)[:120]}]")

def parse_ts(s):
    if not s: return None
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))

def cst(s):
    t = parse_ts(s) if isinstance(s, str) else s
    return t.astimezone(CST).strftime("%m-%d %H:%M") if t else "?"

def merged_prs(day):
    a, b = utc_window(day)
    prs = gh(["pr", "list", "-R", REPO, "--state", "merged", "--limit", "300",
              "--search", f"merged:{a}..{b}",
              "--json", "number,title,author,mergedAt,mergedBy,headRefOid,body"])
    # gh search is minute-granular; re-filter exactly
    prs = [p for p in prs if a <= p["mergedAt"] < b]
    return sorted(prs, key=lambda p: p["number"], reverse=True)

def load_report(n):
    f = f"{RPT}/PR-{n}.json"
    if not os.path.exists(f): return None, None
    try: d = json.load(open(f))
    except Exception as e: return f, {"_error": str(e)}
    pr = next((p for p in d.get("prs", []) if p.get("number") == n), None)
    return f, {"doc": d, "pr": pr}

def first_report_time(n, cur):
    """Earliest generation time across current + history versions (for G1)."""
    ts = []
    for f in [f"{RPT}/PR-{n}.json"] + glob.glob(f"{RPT}/history/PR-{n}.v*.json"):
        try:
            d = json.load(open(f)); t = d.get("generated_at") or d.get("run", {}).get("finished_at")
            if t: ts.append((parse_ts(t), f))
        except Exception: pass
    return min(ts) if ts else (None, None)

def write_atomic(path, text):
    import tempfile
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix="." + os.path.basename(path) + ".")   # unique: concurrent writers never share a tmp
    with os.fdopen(fd, "w") as f: f.write(text)
    os.chmod(tmp, 0o644); os.replace(tmp, path)
    log("wrote", path)

# ---------------- Pi line common ----------------
MODE_FILE = "/workspace/fleet-mode"
OUTBOUND_FLAG = f"{BASE}/outbound-enabled"

def mode():
    """fleet mode; PI_FLEET_MODE env overrides the file (for simulation/tests only, never writes the file)."""
    ov = os.environ.get("PI_FLEET_MODE", "").strip().lower()
    if ov: return ov if ov in ("grok", "pi") else "grok"
    try: m = open(MODE_FILE).read().strip().lower()
    except Exception: m = "grok"
    return m if m in ("grok", "pi") else "grok"

def now_cst(): return dt.datetime.now(CST)
def today(): return now_cst().date().isoformat()

# Praise 10-27: ops/watchdog/relay DMs to him are noise. While PRAISE_DM_MUTE exists, no Slack DM goes to Praise unless its
# dedupe key starts with a prefix listed in that file (one per line, '#' comments; e.g. "brief:" keeps jasper's 09:00 brief).
# Alerts still land in files (watchdog log / out/outbound/muted-*.md) for bots. Delete the file to restore the old behaviour.
PRAISE_DM_MUTE = f"{BASE}/praise-dm-mute"
def praise_dm_blocked(key):
    try: allow = [l.strip() for l in open(PRAISE_DM_MUTE) if l.strip() and not l.lstrip().startswith("#")]
    except FileNotFoundError: return False
    return not str(key).startswith(tuple(allow))
def record_muted(job, key, text):
    """Keep a muted Praise DM in a file so bots can still act on it."""
    d = f"{BASE}/out/{job}"; os.makedirs(d, exist_ok=True)
    with open(f"{d}/muted-{now_cst().strftime('%Y-%m-%d')}.md", "a") as f: f.write(f"## {now_cst().isoformat(timespec='seconds')} {key}\n{text}\n\n")


def record_actions(job, day, actions):
    """Write the outbound actions this job WOULD take. Each action needs a stable 'key' (idempotency).
    Outbound is executed only if mode()=='pi' AND OUTBOUND_FLAG exists; executors are deliberately
    not wired yet (pending Praise approval), so even then nothing is sent."""
    import fcntl
    path = f"{BASE}/out/{job}/{day}.actions.json"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lk = open(path + ".lock", "w"); fcntl.flock(lk, fcntl.LOCK_EX)   # read-modify-write under a lock: two jobs share some files
    prev = json.load(open(path)) if os.path.exists(path) else {"actions": []}
    seen = {a["key"]: a for a in prev.get("actions", [])}
    for a in actions:
        a.setdefault("status", "would-do"); a.setdefault("at", now_cst().isoformat(timespec="seconds"))   # outbound TTLs use "at"
        if a["key"] in seen and seen[a["key"]].get("status") == "done": continue  # never redo a done action
        seen[a["key"]] = {**seen.get(a["key"], {}), **a}
    gate = "pi+flag" if (mode() == "pi" and os.path.exists(OUTBOUND_FLAG)) else "off"
    doc = {"job": job, "day": day, "mode": mode(), "outbound_gate": gate,
           "note": "Executed only by bin/outbound.py when fleet-mode=pi AND outbound-enabled exists; done keys never redone.",
           "updated_at": now_cst().isoformat(timespec="seconds"), "actions": list(seen.values())}
    if gate != "off": log("outbound gate open -> bin/outbound.py (cron */10) will execute pending actions")
    write_atomic(path, json.dumps(doc, ensure_ascii=False, indent=1)); lk.close()

# What a model may see of our environment (10-24): only these names. The 05:19 manual `prfix --qa 1188` (started from a Claude
# session) ran `env | grep -i "pi_|repro|qa"`; __CURSOR_SANDBOX_ENV_RESTORE (value = "builtin export DEEPSEEK_API_KEY=...;
# MIVO_SLACK_BOT_TOKEN=...; ...") matched "pi_" in "API_KEY", and 4 real tokens went to the model and into its session log.
# An allowlist, because secrets also hide in the VALUES of harmless-looking names. DEEPSEEK_API_KEY stays: pi itself needs it
# (models.json apiKey "$DEEPSEEK_API_KEY") and it only reaches the provider it belongs to.
MODEL_ENV_KEEP = re.compile(r"^(PATH|HOME|USER|LOGNAME|SHELL|LANG|LANGUAGE|LC_[A-Z]+|TZ|TERM|TMPDIR|XDG_[A-Z_]+|DISPLAY|NODE_OPTIONS"
                            r"|SAND_EGRESS_[A-Z_]+|SAND_HTTP_PROXY_NAME|PI_[A-Z0-9_]+|PRFIX_[A-Z0-9_]+|INFRA_[A-Z0-9_]+|DEEPSEEK_API_KEY)$")
def model_env(extra=None):
    """The environment for a model and everything a model-driven job starts (tests, hooks, the model's tools): allowlisted names
    only, plus DEEPSEEK_API_KEY (from the secret file when the caller has none)."""
    env = {k: v for k, v in os.environ.items() if MODEL_ENV_KEEP.match(k)}
    if not env.get("DEEPSEEK_API_KEY"):
        key = secret_env_dict().get("DEEPSEEK_API_KEY")
        if key: env["DEEPSEEK_API_KEY"] = key
    env.update(extra or {})
    return env

# The model also runs a bash tool as this user, so it could READ the box's secrets (Praise 10-24: put it in an isolated environment).
# bwrap around pi: own PID namespace + /proc (no other process's environ), the places that hold a real secret (10-24 scan of
# ~, /workspace, /etc, /opt for the real values) hidden behind an empty tmpfs / /dev/null. Network, the worktrees, ~/.pi, the
# node toolchain, ~/.npm and /tmp stay as they are. PI_NO_SANDBOX=1 turns it off (debugging only).
SANDBOX_HIDE_DIRS = ("~/.config/gh", "~/.config/mivo-qa", "~/.config/cindy-launch", "~/.config/CindyGlobal", "~/.pi-fallback-secret",
                     "~/.claude/projects", "~/.claude/shell-snapshots", "~/.cursor", "~/.codex", "~/sand-data", "~/.local/share/keyrings",
                     "~/.ssh", "~/.pi-fallback.git", "/workspace/mivo-merger/secrets", "/workspace/migrate/vault")
SANDBOX_HIDE_FILES = ("~/.claude/.credentials.json", "~/.git-credentials", "~/.netrc")
# 10-24 Q4 (plan 2026-10-24): the machine is READ-ONLY for the model; on top of the secret hiding above, only these are writable:
#   always       what the agent itself writes: ~/.pi (sessions, usage log), PI_STATE/out/tasks (task results), ~/.npm (npx cache)
#   write=True   the call's cwd and, when cwd is a git worktree, its repo's git dir (index, objects, refs: git add / stash / commit)
#   rw=(...)     extra dirs a job names (e.g. an output dir)
#   /tmp         a private /tmp for a call in a bot worktree (PI_WORK/tmp/sbx-*, gone after the call); other calls keep /tmp
# A person's checkouts, the deployed code (/workspace/panoptes), other tickets' worktrees and everything else cannot be changed.
SANDBOX_RW_ALWAYS = ("~/.pi", f"{BASE}/out/tasks", "~/.npm")
def _git_dir_of(d):
    try:
        r = subprocess.run(["git", "-C", d, "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None
    except Exception: return None
def sandbox_prefix(cwd=None, write=False, rw=(), tmp=None):
    """bwrap argv for one model call (see SANDBOX_RW_ALWAYS above); [] when PI_NO_SANDBOX=1 or bwrap is missing"""
    if os.environ.get("PI_NO_SANDBOX") == "1" or not os.path.exists("/usr/bin/bwrap"): return []
    a = ["/usr/bin/bwrap", "--ro-bind", "/", "/", "--dev-bind", "/dev", "/dev", "--unshare-pid", "--proc", "/proc", "--die-with-parent"]
    if tmp: a += ["--bind", tmp, "/tmp"]                  # first: a writable dir under the real /tmp is bound again on top of it
    w = [os.path.expanduser(x) for x in SANDBOX_RW_ALWAYS] + ([] if tmp else ["/tmp"])
    if write and cwd:
        w.append(os.path.abspath(cwd))
        g = _git_dir_of(cwd)
        if g: w.append(g)
    w += [os.path.abspath(os.path.expanduser(x)) for x in rw]
    for d in dict.fromkeys(w):
        if os.path.isdir(d): a += ["--bind", d, d]
    for d in map(os.path.expanduser, SANDBOX_HIDE_DIRS):     # after the binds: a writable parent never shows a hidden dir again
        if os.path.isdir(d): a += ["--tmpfs", d]
    for f in map(os.path.expanduser, SANDBOX_HIDE_FILES):
        if os.path.isfile(f): a += ["--ro-bind", "/dev/null", f]
    return a + ["--"]

def pi(tier, cwd, prompt, session, timeout=600, write=False, web=False, skills=True, nice=False, rw=()):
    """Run Pi via pi-auto (tier picks model/thinking). Returns (text, task_id).
    skills=False: pi without skill discovery (bin/pi-noskills.sh; 10-24: ~/.pi/agent/settings.json loads ~/.claude/skills, 36 KB of
    Claude Code skills in every coding step's system prompt). nice=True: the agent and the tests it runs at nice 10 (merge tests first)."""
    env = model_env({"PI_TASK_DIR": f"{BASE}/out/tasks", "PI_NOTIFY_OFF": "1", "PI_TASK_OWNER": "pi-line-cron"})   # never os.environ as is
    if not skills: env["PI_BIN"] = f"{PI_CODE}/bin/pi-noskills.sh"
    try: os.makedirs(f"{BASE}/out/tasks", exist_ok=True)      # the sandbox binds it writable only when it exists
    except OSError: pass
    tmp = None
    if os.path.realpath(cwd).startswith(os.path.realpath(PI_WORK) + os.sep):   # a bot worktree: its own /tmp for this call
        os.makedirs(f"{PI_WORK}/tmp", exist_ok=True)
        tmp = tempfile.mkdtemp(prefix="sbx-" + re.sub(r"[^A-Za-z0-9_.-]", "-", session)[:60] + "-", dir=f"{PI_WORK}/tmp")
    cmd = sandbox_prefix(cwd, write, rw, tmp) + (["nice", "-n", "10"] if nice else []) + [os.path.expanduser("~/tools/pi-call/pi-auto"), tier, cwd, prompt,
           "--session", session, "--owner", "pi-line-cron", "--timeout", str(timeout)]
    if not write: cmd.append("--no-write")
    if not web: cmd.append("--no-web")
    try: r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout + 120)
    finally:
        if tmp: shutil.rmtree(tmp, ignore_errors=True)
    m = re.search(r"^RESULT (.+)$", r.stdout, re.M); t = re.search(r"^TASK (\S+)", r.stdout, re.M)
    if r.returncode != 0 or not m: raise RuntimeError(f"pi {tier} rc={r.returncode} {r.stdout[-300:]} {r.stderr[-300:]}")
    return open(m.group(1).strip()).read(), (t.group(1) if t else "?")

# 修 PR bot harness version (prfix + prwatch): a ticket / revise that stopped for a human under an older version is tried once more with
# the new one. Bump it whenever a change could unblock what used to stop (10-24).
PRBOT_HARNESS = "2026-10-24e"

def panoptes_busy():
    return subprocess.run(["pgrep", "-f", "run-nightly.sh|argus/.*run"], capture_output=True).returncode == 0

TAKEOVER = os.environ.get("PI_TAKEOVER_DIR") or f"{BASE}/takeover"  # env override for tests only
def takeover(name): return os.path.exists(f"{TAKEOVER}/{name}")

# --- PR ownership guards (10-30: prfix pushed 51802504 onto #1444 while another worker owned it) ---------------------------
# Branch claim: takeover/branch-claims/<PR number>. A human or bot worker writes it and renews it; while it is valid prfix
# (--revise and outbound gh.pr.push-fix) leaves that PR alone, so it is also a per-PR off switch. Format: first line = expiry,
# ISO 8601 with offset ("2026-10-30T18:00:00+08:00") or Unix seconds; anything after the first line is a free-form note
# (who / why). Empty or unparsable first line: expiry = file mtime + CLAIM_TTL_H hours (so `touch` claims and renews).
CLAIM_TTL_H = 4
def claim_expiry(path):
    """expiry (aware datetime) of the claim file at path, or None when there is no file."""
    try:
        st = os.stat(path)
        with open(path, errors="replace") as f: first = (f.readline() or "").strip()
    except FileNotFoundError: return None
    try:
        if re.fullmatch(r"\d{9,11}", first): return dt.datetime.fromtimestamp(int(first), dt.timezone.utc)
        e = dt.datetime.fromisoformat(first.replace("Z", "+00:00"))
        if e.tzinfo: return e
    except ValueError: pass
    # anything else -- empty, garbage, or a naive ISO time without offset (ambiguous zone) -- falls back to mtime + CLAIM_TTL_H
    # (fail closed: a naive claim that "expired" an hour ago may still block until mtime+4h). Write an offset or unix seconds.
    return dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc) + dt.timedelta(hours=CLAIM_TTL_H)
def branch_claim(n, now=None):
    """reason string while takeover/branch-claims/<n> holds a valid claim, else None."""
    e = claim_expiry(f"{TAKEOVER}/branch-claims/{int(n)}")
    now = now or dt.datetime.now(dt.timezone.utc)
    return f"takeover/branch-claims/{int(n)} 有效到 {e.isoformat(timespec='seconds')}：别的 worker 认领了这个 PR，不碰" if e and e > now else None
# 11-03 B rollback: touch PI_STATE/pmnudge-b-off (re-read every run) -> pmnudge exempts the bot's own fix PRs again and skips the
# dirty / blocked / main-CI / kiro rules; prwatch + prfix --revise treat a red / readied / re-run head that turns DIRTY as handled.
B_OFF = f"{BASE}/pmnudge-b-off"
def b_on(): return not os.path.exists(B_OFF)
# Ownership: a commit on the PR branch in the last OWNER_WINDOW_H hours WITHOUT a Pi trailer (Pi-Open: first commit of a Pi fix PR,
# Pi-Revise: a --revise fix; Fingerprint alone does not count, a human may keep it) = someone else is working on it.
OWNER_WINDOW_H = 6
# Only the final paragraph (git trailer block) counts, and only key-shaped values Pi's own builders write
# (prfix fix_one: fixpr:<qa>:<fp>; prfix/outbound revise: revise:<pr>:<head12>[:merge]); a body line "Pi-Revise: x" is foreign.
PI_MARK_RE = re.compile(r"^(Pi-Revise: revise:\d+:[0-9a-f]{12}(:merge)?|Pi-Open: fixpr:\d+:\S+)$", re.M)
def has_pi_trailer(msg):
    paras = [p for p in re.split(r"\n\s*\n", (msg or "").strip()) if p.strip()]
    return len(paras) > 1 and bool(PI_MARK_RE.search(paras[-1]))
def foreign_recent_commits(commits, now=None, hours=OWNER_WINDOW_H):
    """commits: GitHub pulls/N/commits items -> short shas of recent commits without a PI_MARK_RE trailer."""
    now = now or dt.datetime.now(dt.timezone.utc); out = []
    for c in commits or []:
        cm = c.get("commit") or {}
        when = (cm.get("committer") or {}).get("date") or (cm.get("author") or {}).get("date")
        try: t = dt.datetime.fromisoformat(str(when).replace("Z", "+00:00"))
        except ValueError: t = now   # unreadable date: count it as recent (fail closed)
        if t.tzinfo is None: t = now   # naive date (no zone): count it as recent (fail closed)
        if now - t <= dt.timedelta(hours=hours) and not has_pi_trailer(cm.get("message")): out.append((c.get("sha") or "?")[:9])
    return out
def ownership_block(n, commits, now=None):
    """reason not to push onto PR n (claim, or recent foreign commits), else None."""
    why = branch_claim(n, now)
    if why: return why
    f = foreign_recent_commits(commits, now)
    return f"PR 分支最近 {OWNER_WINDOW_H} 小时有不带 Pi-Open / Pi-Revise 标记的提交 {f[:5]}：别人在改，不推送" if f else None
def pr_commits_args(n): return ["api", "--paginate", "--slurp", f"repos/{REPO}/pulls/{int(n)}/commits?per_page=100"]
def flatten_pages(x):
    """`gh api --paginate --slurp` -> [[page1...], [page2...]]: one flat list (an already flat list passes through)."""
    return [i for p in x for i in p] if isinstance(x, list) and x and all(isinstance(p, list) for p in x) else (x or [])
# Gate files: a planned change touching these is a hard stop; --allow-sensitive does NOT override it.
PROTECTED_RE = re.compile(r"(^|/)__tests__/(.*/)?[^/]*(Guard|Policy)[^/]*$|^\.githooks/|^scripts/verify", re.I)
def protected_files(files): return sorted(f for f in files if PROTECTED_RE.search(f.replace("\\", "/")))
def patch_files(path):
    with open(path, errors="replace") as f: return sorted({m.group(1) for m in re.finditer(r"^diff --git a/(\S+) b/", f.read(), re.M)})
SECRET_FILES = ("~/.pi-fallback-secret/env", "~/.config/mivo-qa/secrets.env")   # both outside every git repo / backup, mode 600
def secret_env_dict():
    """KEY=VALUE pairs of SECRET_FILES (an `export ` prefix and quotes allowed; the earlier file wins); never logs values.
    10-24: one reader for everyone -- watchdog's own copy read only the first file, so its bypass sender had no Slack token, and
    the `export ` prefix of the first file used to end up in the key name."""
    env = {}
    for f in SECRET_FILES:
        try: lines = open(os.path.expanduser(f)).read().splitlines()
        except OSError: continue
        for line in lines:
            line = line.strip()
            if line.startswith("export "): line = line[7:].lstrip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env
def secret_env():
    """Merge SECRET_FILES into os.environ without overriding; never logs values."""
    for k, v in secret_env_dict().items(): os.environ.setdefault(k, v)
