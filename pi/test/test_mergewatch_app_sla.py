"""python3 test/test_mergewatch_app_sla.py — 任务 3：praiseautomation 只读 App 接入 mergewatch（缺什么就退回原路径）；
合并 → 开测 p95 ≤ 2 分钟的 SLA 告警。不联网、不碰真私钥（PI_GH_APP_DIR 指到临时目录）。"""
import os, sys, json, tempfile, subprocess, datetime as dt, importlib.util
tmp = tempfile.mkdtemp(); os.environ["PI_GH_APP_DIR"] = tmp; os.environ.pop("PI_GH_APP_ID", None); os.environ.pop("PI_GH_APP_INSTALLATION_ID", None)
os.environ.setdefault("PI_STATE", tempfile.mkdtemp())
B = os.environ.get("PI_BIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
def load(n):
    s = importlib.util.spec_from_file_location(n, f"{B}/{n}.py"); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
G = load("ghapp")
t, why = G.token(); assert t is None and why == "缺 私钥、App ID、安装 ID", why
subprocess.run(["openssl", "genrsa", "-out", f"{tmp}/praiseautomation.pem", "2048"], capture_output=True, check=True)
json.dump({"app_id": 1, "installation_id": 2}, open(f"{tmp}/praiseautomation.json", "w"))
assert G.jwt(1).count(".") == 2
seen = {}
def post_ro(url, hdr, body): seen.update(url=url, auth=hdr["Authorization"][:7], body=body); return {"token": "ghs_test", "permissions": {"contents": "read", "metadata": "read", "pull_requests": "read"}}
assert G.token(post_ro) == ("ghs_test", "ok") and seen["url"].endswith("/app/installations/2/access_tokens") and seen["auth"] == "Bearer "
assert seen["body"] == {"permissions": {"contents": "read", "pull_requests": "read", "metadata": "read"}}   # 只申请这三项 read
t, why = G.token(lambda u, h, b: {"token": "ghs_x", "permissions": {"contents": "write", "metadata": "read"}})
assert t is None and "拒用" in why and "contents=write" in why          # 有写权限就拒用
t, why = G.token(lambda u, h, b: {"token": "ghs_x", "permissions": {"contents": "read", "issues": "read"}})
assert t is None and "issues=read" in why                              # 多出申请范围之外的权限（哪怕只读）也拒用
t, why = G.token(lambda u, h, b: {"token": "ghs_x"}); assert t is None and "缺失" in why
assert G.token(lambda u, h, b: {"token": "ghs_y", "permissions": {"metadata": "read"}}) == ("ghs_y", "ok")   # 比申请的少可以
t, why = G.token(lambda u, h, b: (_ for _ in ()).throw(OSError("net")))
assert t is None and why == "换安装令牌失败（OSError）"
e = G.git_env("ghs_test"); assert e["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader" and "ghs_test" not in e["GIT_CONFIG_VALUE_0"]
assert "app_auth()" in open(f"{B}/mergewatch.py").read()
# SLA
W = load("watchdog"); now = dt.datetime(2026, 10, 26, 12, tzinfo=dt.timezone.utc)
iso = lambda m: (now - dt.timedelta(minutes=m)).isoformat()
fast = [(iso(60 + i), iso(60 + i - 1)) for i in range(10)]
assert W.merge_sla(fast, now) == (10, 1.0)
slow = fast + [(iso(300), iso(300 - 40))] * 2
n, p = W.merge_sla(slow, now); assert n == 12 and p == 40, (n, p)
assert W.merge_sla([(iso(60 * 30), iso(60 * 29))], now) == (0, None)        # 24 小时以前的不算
rd = tempfile.mkdtemp()
for i, (m, s) in enumerate(slow): json.dump({"prs": [{"merged_at": m, "test_started_at": s}]}, open(f"{rd}/PR-{i}.json", "w"))
probs = {}; W.merge_sla_check(now, probs, rd); assert "sla:merge-to-test" in probs
probs = {}; W.merge_sla_check(now, probs, tempfile.mkdtemp()); assert not probs
print("ok test_mergewatch_app_sla")
