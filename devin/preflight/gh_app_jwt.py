"""gh_app_jwt — PraiseDEVIN GitHub App installation tokens for Devin Preflight scripts (PraiseZhu/panoptes#312).

Pure standard library (no third-party package, no openssl subprocess): it runs on Devin's Minimal VM inside a single-file
bundle (devin/bin/build-preflight.py). Reads the App key from $PRAISEDEVIN_PRIVATE_KEY_B64 (one-line base64 of a PKCS#1
"RSA PRIVATE KEY" or PKCS#8 "PRIVATE KEY" PEM), signs an RS256 JWT (RSASSA-PKCS1-v1_5 + SHA-256) and exchanges it for
the installation token of the repo owner (PraiseZhu -> 183272732, xindong -> 183272688).
$GITHUB_API_URL overrides https://api.github.com (tests). Keys and tokens are never printed or put in error messages.
"""
import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

APP_ID = os.environ.get("PRAISEDEVIN_APP_ID") or "5815034"
INSTALLATIONS = {"praisezhu": 183272732, "xindong": 183272688}
KEY_ENV = "PRAISEDEVIN_PRIVATE_KEY_B64"
DEFAULT_API = "https://api.github.com"
USER_AGENT = "panoptes-devin-preflight"
SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
RSA_ENCRYPTION_OID = bytes.fromhex("2a864886f70d010101")


class AppAuthError(Exception):
    pass


class GitHubError(Exception):
    pass


def _der(buf, pos):
    """-> (tag, value, next_pos) of the DER element at pos"""
    if pos + 2 > len(buf):
        raise AppAuthError("private key: truncated DER")
    tag, length, pos = buf[pos], buf[pos + 1], pos + 2
    if length & 0x80:
        n = length & 0x7F
        if n == 0 or n > 4 or pos + n > len(buf):
            raise AppAuthError("private key: bad DER length")
        length, pos = int.from_bytes(buf[pos:pos + n], "big"), pos + n
    if pos + length > len(buf):
        raise AppAuthError("private key: truncated DER")
    return tag, buf[pos:pos + length], pos + length


def _children(value):
    out, pos = [], 0
    while pos < len(value):
        tag, v, pos = _der(value, pos)
        out.append((tag, v))
    return out


def _sequence(buf):
    tag, value, end = _der(buf, 0)
    if tag != 0x30 or end != len(buf):
        raise AppAuthError("private key: expected one DER SEQUENCE")
    return _children(value)


def _rsa_from_pkcs1(der):
    items = _sequence(der)
    if len(items) < 9 or any(tag != 0x02 for tag, _ in items[:9]):
        raise AppAuthError("private key: not a PKCS#1 RSAPrivateKey")
    version, n, e, d = (int.from_bytes(v, "big") for _, v in items[:4])
    if version != 0:
        raise AppAuthError(f"private key: unsupported RSAPrivateKey version {version}")
    return {"n": n, "e": e, "d": d}


def _rsa_from_pkcs8(der):
    items = _sequence(der)
    if len(items) < 3 or items[0][0] != 0x02 or items[1][0] != 0x30 or items[2][0] != 0x04:
        raise AppAuthError("private key: not a PKCS#8 PrivateKeyInfo")
    alg = _children(items[1][1])
    if not alg or alg[0] != (0x06, RSA_ENCRYPTION_OID):
        raise AppAuthError("private key: PKCS#8 key is not rsaEncryption")
    return _rsa_from_pkcs1(items[2][1])


def load_private_key(pem):
    """PEM text (PKCS#1 or PKCS#8) -> {"n", "e", "d"}"""
    if isinstance(pem, bytes):
        pem = pem.decode("ascii", "replace")
    for label, parse in (("RSA PRIVATE KEY", _rsa_from_pkcs1), ("PRIVATE KEY", _rsa_from_pkcs8)):
        begin, end = f"-----BEGIN {label}-----", f"-----END {label}-----"
        start = pem.find(begin)
        if start < 0:
            continue
        stop = pem.find(end, start)
        if stop < 0:
            raise AppAuthError(f"private key: no {end} line")
        body = "".join(pem[start + len(begin):stop].split())
        try:
            der = base64.b64decode(body, validate=True)
        except ValueError:
            raise AppAuthError("private key: PEM body is not base64") from None
        return parse(der)
    raise AppAuthError("private key: expected a PKCS#1 'RSA PRIVATE KEY' or PKCS#8 'PRIVATE KEY' PEM")


def sign_rs256(key, data):
    """RSASSA-PKCS1-v1_5 signature with SHA-256 (byte-identical to `openssl dgst -sha256 -sign`)"""
    k = (key["n"].bit_length() + 7) // 8
    t = SHA256_DIGEST_INFO + hashlib.sha256(data).digest()
    if k < len(t) + 11:
        raise AppAuthError(f"private key: modulus too short ({k} bytes)")
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return pow(int.from_bytes(em, "big"), key["d"], key["n"]).to_bytes(k, "big")


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_jwt(key, app_id=APP_ID, now=None):
    """App JWT: iat 60 s in the past (clock drift), exp 9 min ahead (GitHub allows at most 10)"""
    now = int(time.time() if now is None else now)
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = b64url(json.dumps({"iat": now - 60, "exp": now + 540, "iss": str(app_id)}, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode("ascii")
    return f"{header}.{payload}.{b64url(sign_rs256(key, signing_input))}"


def key_from_env(env=None):
    raw = (os.environ if env is None else env).get(KEY_ENV, "").strip()
    if not raw:
        raise AppAuthError(f"{KEY_ENV} is not set")
    try:
        pem = base64.b64decode(raw, validate=True)
    except ValueError:
        raise AppAuthError(f"{KEY_ENV} is not one-line base64") from None
    return load_private_key(pem)


def api_base():
    return (os.environ.get("GITHUB_API_URL") or DEFAULT_API).rstrip("/")


def request(method, path, auth, body=None, accept="application/vnd.github+json", timeout=20):
    """One GitHub REST call -> parsed JSON (raw bytes when accept is not JSON). Errors: GitHubError (status, no secrets)."""
    url = path if path.startswith("http") else api_base() + path
    data = None if body is None else json.dumps(body).encode()
    headers = {"Authorization": auth, "Accept": accept, "User-Agent": USER_AGENT, "X-GitHub-Api-Version": "2022-11-28"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    where = f"{method} {urllib.parse.urlsplit(url).path}"
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        try:
            msg = str(json.loads(e.read() or b"{}").get("message", ""))[:160]
        except (ValueError, AttributeError):
            msg = ""
        raise GitHubError(f"{where}: HTTP {e.code}{' ' + msg if msg else ''}") from None
    except (urllib.error.URLError, OSError) as e:
        raise GitHubError(f"{where}: {type(e).__name__}: {getattr(e, 'reason', e)}") from None
    if "json" not in accept:
        return raw
    try:
        return json.loads(raw or b"null")
    except ValueError:
        raise GitHubError(f"{where}: response is not JSON") from None


def installation_id(owner):
    try:
        return INSTALLATIONS[owner.lower()]
    except KeyError:
        raise AppAuthError(f"no PraiseDEVIN installation for owner {owner!r} (known: {sorted(INSTALLATIONS)})") from None


def installation_token(owner, key=None, app_id=APP_ID):
    jwt = make_jwt(key or key_from_env(), app_id)
    doc = request("POST", f"/app/installations/{installation_id(owner)}/access_tokens", f"Bearer {jwt}", body={})
    token = doc.get("token") if isinstance(doc, dict) else None
    if not isinstance(token, str) or not token:
        raise GitHubError(f"installation token for {owner}: response has no token")
    return token


class Client:
    """GET helper with one installation token per repo owner (minted on first use, kept in memory only)."""

    def __init__(self, key=None):
        self._key, self._tokens = key, {}

    def token(self, owner):
        owner = owner.lower()
        if owner not in self._tokens:
            self._key = self._key or key_from_env()
            self._tokens[owner] = installation_token(owner, self._key)
        return self._tokens[owner]

    def get(self, repo, path, accept="application/vnd.github+json"):
        """GET /repos/{repo}{path} with the token of repo's owner"""
        return request("GET", f"/repos/{repo}{path}", f"token {self.token(repo.split('/')[0])}", accept=accept)
