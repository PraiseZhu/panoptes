"""python3 test/test_merger_identity.py — 10-28：合并官用 config tokenPath 的专用 token 读 GitHub（policy 要 scopes 恰好 ['repo']），
不再拿 box 默认 gh 登录去比（每轮 STOP identity 的根因）。读不到 token 时不改 env（照旧 STOP，不放宽）。"""
import os, sys, json, tempfile, importlib.util
B = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
if not os.path.exists("/workspace/mivo-merger/config.json"): print("skip test_merger_identity (no merger config)"); sys.exit(0)
s = importlib.util.spec_from_file_location("merger", f"{B}/merger.py"); M = importlib.util.module_from_spec(s); s.loader.exec_module(M)
d = tempfile.mkdtemp(); tp = f"{d}/token"; open(tp, "w").write("tok-123\n")
env = {"GITHUB_TOKEN": "ambient"}
assert M.use_merger_token({"tokenPath": tp}, env) and env == {"GH_TOKEN": "tok-123"}, env
env = {"GITHUB_TOKEN": "ambient"}
assert not M.use_merger_token({"tokenPath": f"{d}/missing"}, env) and env == {"GITHUB_TOKEN": "ambient"}
assert not M.use_merger_token({}, env)
G = M.G; P = M.P
ok = {"tokenLogin": P["tokenLogin"], "tokenScopes": list(P["tokenScopes"]), "incomplete": []}
wide = {**ok, "tokenScopes": sorted(P["tokenScopes"] + ["admin:org", "workflow"])}
assert not G.same_set(wide["tokenScopes"], P["tokenScopes"]) and G.same_set(ok["tokenScopes"], P["tokenScopes"])   # 规则不变：宽 scope 不过
src = open(f"{B}/merger.py").read(); assert "def main():\n    use_merger_token()" in src
print("ok test_merger_identity")
