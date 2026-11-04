#!/usr/bin/env python3
"""GitHub App "Praise QA" installation tokens (11-04, pi-work/gh-app-1104/PLAN.md P0). No callers yet; P1/P2 wire it in behind a flag.

The installation is chosen by target repo (INSTALLATIONS). At call time a JWT (RS256, iat-60 s, exp +9 min) is signed with the App key
via `openssl dgst -sha256 -sign` (no Python crypto dependency; the key is only ever opened by openssl), exchanged for an installation
token (POST /app/installations/<id>/access_tokens), and cached per installation in CACHE_DIR/inst-<id>.json (dir 0700, file 0600,
tmp+rename under flock) until MARGIN_S before it expires. A token is never logged: everything this module prints or raises is passed
through mask(). 11-04 task B: installation 164262283 covers every PraiseZhu/* repo (pr-qa-agent-deploy included, PM); other repos
without an installation raise NoInstallation: their callers stay on the PAT.

usage: ghtoken.py check [REPO]   -> one line: installation, expiry (never the token)
       ghtoken.py token [REPO]   -> the token on stdout, for GH_TOKEN=$(...) in scripts; nothing else is printed
Env (tests / a moved key): PRAISE_QA_APP_ID, PRAISE_QA_KEY, PRAISE_QA_CACHE, PRAISE_QA_API."""
import base64, calendar, fcntl, json, os, re, subprocess, sys, tempfile, time, urllib.error, urllib.request

APP_ID_DEFAULT = "5055491"
INSTALLATIONS = {"PraiseZhu/panoptes": 164262283, "xindong/mivo-canvas-plugin": 178486896}
OWNER_INSTALLATIONS = {"PraiseZhu": 164262283, "xindong": 178486896}   # every repo of the owner (11-04 PM: xindong/* -> 178486896)
DEFAULT_REPO = "PraiseZhu/panoptes"
MARGIN_S = 600          # re-mint when less than 10 min is left (tokens live 1 h)
TOKEN_RE = re.compile(r"\b(gh[pousr]|github_pat)_[A-Za-z0-9_]+")


class NoInstallation(KeyError): pass
class MintError(RuntimeError): pass


# 11-04 #217 follow-up: the shared redaction (outbound clip_err / _redact use it). A bare 40-hex is only masked after a key= / key:
# label: unlabelled 40-hex strings are commit shas in normal log lines and stay readable.
_MASKS = [
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/@\s]+@"), r"\1***@"),                           # scheme://user:tok@ / tok@, any case
    (re.compile(r"(?i)\b(authorization\s*:\s*(?:basic|bearer|token)\s+)\S+"), r"\1***"),         # Authorization headers
    (re.compile(r"(?i)\b(x-access-token\s*[:=]\s*)[^\s@]+"), r"\1***"),                         # x-access-token:<tok> outside a URL
    # labelled 40-hex: any word label ending in token/key/secret/password/pat (GITHUB_TOKEN, secret_key, apikey), bare or as a quoted
    # JSON key ("token": "<hex>"). Unlabelled shas and non-secret labels ("sha":, head=, commit) stay readable.
    (re.compile(r"(?i)(?<![A-Za-z0-9])([A-Za-z0-9_-]*?(?:token|key|secret|password|passwd|pat)[\"']?\s*[=:]\s*[\"']?)[0-9a-f]{40}\b"), r"\1***"),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{20,}"), r"\1***"),                             # bare Bearer <tok>, no header name
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "sk-***"),                                             # sk- API keys
]
def mask(s):
    s = TOKEN_RE.sub(lambda m: m.group(1) + "_***", str(s))
    for rx, rep in _MASKS: s = rx.sub(rep, s)
    return s
def _app_id(): return os.environ.get("PRAISE_QA_APP_ID") or APP_ID_DEFAULT
def _key(): return os.environ.get("PRAISE_QA_KEY") or "/home/box/.secrets/praise-qa/app.pem"
def _cache_dir(): return os.environ.get("PRAISE_QA_CACHE") or os.path.expanduser("~/.cache/praise-qa")
def _api(): return (os.environ.get("PRAISE_QA_API") or "https://api.github.com").rstrip("/")


def installation(repo):
    try: return INSTALLATIONS[repo] if repo in INSTALLATIONS else OWNER_INSTALLATIONS[str(repo).split("/")[0]] if str(repo).count("/") == 1 else INSTALLATIONS[repo]
    except KeyError: raise NoInstallation(f"Praise QA is not installed for {repo!r}: keep this caller on the PAT") from None


def _b64(b): return base64.urlsafe_b64encode(b).rstrip(b"=")


def jwt(now=None):
    """RS256 App JWT. The key file is read by openssl only."""
    now = int(now or time.time())
    head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps({"iat": now - 60, "exp": now + 540, "iss": _app_id()}, separators=(",", ":")).encode())
    msg = head + b"." + body
    r = subprocess.run(["openssl", "dgst", "-sha256", "-sign", _key()], input=msg, capture_output=True, timeout=30)
    if r.returncode: raise MintError(f"openssl could not sign the App JWT (rc={r.returncode}): {mask(r.stderr.decode(errors='replace'))[:200]}")
    return (msg + b"." + _b64(r.stdout)).decode()


def _parse_exp(s):
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) if s else 0


def mint(inst, now=None):
    """-> {"token", "expires_at" (epoch), "installation"} straight from GitHub"""
    req = urllib.request.Request(f"{_api()}/app/installations/{inst}/access_tokens", data=b"{}", method="POST",
                                 headers={"Authorization": f"Bearer {jwt(now)}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "panoptes-ghtoken"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r: d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise MintError(f"installation {inst}: HTTP {e.code} {mask(e.read().decode(errors='replace'))[:200]}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise MintError(f"installation {inst}: {mask(e)[:200]}") from None
    if not d.get("token"): raise MintError(f"installation {inst}: no token in the answer")
    return {"token": d["token"], "expires_at": _parse_exp(d.get("expires_at")), "installation": inst}


def _cache_file(inst): return os.path.join(_cache_dir(), f"inst-{inst}.json")


def _read_cache(inst):
    try:
        with open(_cache_file(inst)) as f: d = json.load(f)
        return d if isinstance(d, dict) and d.get("token") and isinstance(d.get("expires_at"), (int, float)) else None
    except (OSError, ValueError): return None


def _write_cache(inst, d):
    os.makedirs(_cache_dir(), mode=0o700, exist_ok=True); os.chmod(_cache_dir(), 0o700)
    fd, tmp = tempfile.mkstemp(dir=_cache_dir(), prefix=".inst-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f: json.dump(d, f)
        os.replace(tmp, _cache_file(inst))
    except BaseException:
        try: os.remove(tmp)
        except OSError: pass
        raise


def token_info(repo=DEFAULT_REPO, now=None):
    """cached {"token", "expires_at", "installation", "minted": bool}; re-mints when < MARGIN_S is left"""
    inst = installation(repo)
    now = now or time.time()
    os.makedirs(_cache_dir(), mode=0o700, exist_ok=True)
    with open(os.path.join(_cache_dir(), f"inst-{inst}.lock"), "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        c = _read_cache(inst)
        if c and c["expires_at"] - now > MARGIN_S: return {**c, "minted": False}
        d = mint(inst, now); _write_cache(inst, d)
        print(f"praise-qa token minted for installation {inst}, exp {time.strftime('%H:%M', time.localtime(d['expires_at']))}", file=sys.stderr)
        return {**d, "minted": True}


def token(repo=DEFAULT_REPO, now=None): return token_info(repo, now)["token"]


def invalidate(repo=DEFAULT_REPO):
    """drop the cached token (caller got a 401): the next token() mints a new one"""
    try: os.remove(_cache_file(installation(repo)))
    except FileNotFoundError: pass


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    cmd, repo = (a + [None, None])[:2]
    repo = repo or DEFAULT_REPO
    try:
        if cmd == "check":
            d = token_info(repo)
            print(f"ok: {repo} installation {d['installation']}, token expires {time.strftime('%Y-%m-%d %H:%M', time.localtime(d['expires_at']))}"
                  f"{' (newly minted)' if d['minted'] else ' (cached)'}")
            return 0
        if cmd == "token":
            sys.stdout.write(token(repo)); return 0
    except (NoInstallation, MintError) as e:
        print(f"ghtoken: {mask(e)}", file=sys.stderr); return 2
    print("usage: ghtoken.py check|token [OWNER/REPO]", file=sys.stderr); return 64


if __name__ == "__main__": sys.exit(main())
