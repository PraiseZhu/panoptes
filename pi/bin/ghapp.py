#!/usr/bin/env python3
"""ghapp — praiseautomation GitHub App 的只读安装令牌（mergewatch 用；不存盘、不打印）。

需要：私钥 ~/.config/gh-app/praiseautomation.pem（已有），以及 App ID 和安装 ID：
  环境变量 PI_GH_APP_ID / PI_GH_APP_INSTALLATION_ID，或 ~/.config/gh-app/praiseautomation.json {"app_id": .., "installation_id": ..}
任一缺失 → token() 返回 (None, 原因)，调用方退回原来的匿名 / gh 凭证路径。
只读保护：安装令牌返回的 permissions 里只要有一项是 write/admin，就拒用（返回 None）。"""
import base64, json, os, subprocess, time, urllib.request

DIR = os.path.expanduser(os.environ.get("PI_GH_APP_DIR") or "~/.config/gh-app")
KEY = os.path.join(DIR, "praiseautomation.pem")
CONF = os.path.join(DIR, "praiseautomation.json")
API = "https://api.github.com"


def ids():
    c = {}
    try: c = json.load(open(CONF))
    except (OSError, ValueError): pass
    return os.environ.get("PI_GH_APP_ID") or c.get("app_id"), os.environ.get("PI_GH_APP_INSTALLATION_ID") or c.get("installation_id")


def b64(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def jwt(app_id, key=KEY, now=None):
    now = int(now or time.time())
    head = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()); body = b64(json.dumps({"iat": now - 60, "exp": now + 540, "iss": str(app_id)}).encode())
    sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", key], input=f"{head}.{body}".encode(), capture_output=True, timeout=10)
    if sig.returncode != 0: raise RuntimeError("openssl sign failed")
    return f"{head}.{body}.{b64(sig.stdout)}"


def read_only(perms):
    return all(v == "read" for v in (perms or {}).values())


def token(post=None):
    """返回 (token|None, 原因)。原因只写缺什么 / 为什么拒用，不含任何密钥内容。"""
    app_id, inst = ids()
    missing = [n for n, v in (("私钥", os.path.exists(KEY)), ("App ID", app_id), ("安装 ID", inst)) if not v]
    if missing: return None, "缺 " + "、".join(missing)
    try:
        if post is None:
            def post(url, hdr):
                req = urllib.request.Request(url, method="POST", headers=hdr)
                with urllib.request.urlopen(req, timeout=15) as r: return json.load(r)
        d = post(f"{API}/app/installations/{inst}/access_tokens", {"Authorization": f"Bearer {jwt(app_id)}", "Accept": "application/vnd.github+json"})
    except Exception as e:
        return None, f"换安装令牌失败（{type(e).__name__}）"
    if not read_only(d.get("permissions")):
        return None, "App 有写权限，按规定拒用：" + ",".join(f"{k}={v}" for k, v in sorted((d.get("permissions") or {}).items()) if v != "read")
    return d.get("token"), "ok"


def git_env(tok):
    """给 git 子进程的环境：用 GIT_CONFIG_* 传 Authorization 头（不进命令行参数，ps 看不到）。"""
    basic = base64.b64encode(f"x-access-token:{tok}".encode()).decode()
    return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader", "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}"}


if __name__ == "__main__":
    t, why = token(); print("praiseautomation:", "ok (read-only)" if t else why)
