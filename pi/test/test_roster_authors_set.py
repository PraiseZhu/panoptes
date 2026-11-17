"""python3 test/test_roster_authors_set.py — roster.py 对 mivo_authors 做 set() 会 TypeError（dict 不可哈希）。

比较键改为稳定可哈希的 github_login。当前真实 schema（refresh-roster 产出、live / 11-01 / 11-15 映射）是
带 github_login 的 dict 列表；outbound.authors 额外兼容「login -> record」的 dict。本测试只用临时文件和 mock，
不读 live author-map、不调 refresh-roster、不读密钥、不写生产状态、不触网。
"""
import datetime as dt, importlib.util, json, os, sys, tempfile, unittest.mock as mock

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
sys.path.insert(0, BIN)

CST = dt.timezone(dt.timedelta(hours=8))

def load_roster():
    sys.modules.pop("roster", None)
    sys.modules.pop("lib", None)
    spec = importlib.util.spec_from_file_location("roster", f"{BIN}/roster.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def ids(mod, value):
    """Use the fix helper when present; otherwise the pre-fix line-13 expression (set of the field itself)."""
    if hasattr(mod, "mivo_author_ids"):
        return mod.mivo_author_ids(value)
    return set(value)

def rec(*logins):
    return [{"github_login": login} for login in logins]

# --- 1) current schema: list of dicts with github_login ---
mod = load_roster()
list_old = rec("alice", "bob")
list_new = rec("alice", "bob", "carol")

got = ids(mod, list_new)  # old code: TypeError here
assert got == {"alice", "bob", "carol"}, got
assert ids(mod, list_old) == {"alice", "bob"}

# existing logins are not reported as new; a truly new login is
added, removed = ids(mod, list_new) - ids(mod, list_old), ids(mod, list_old) - ids(mod, list_new)
assert added == {"carol"} and removed == set(), (added, removed)

# removals
list_newer = rec("alice", "dave")
added, removed = ids(mod, list_newer) - ids(mod, list_old), ids(mod, list_old) - ids(mod, list_newer)
assert added == {"dave"} and removed == {"bob"}, (added, removed)

# skip records that have no usable github_login (do not crash; do not invent ids)
assert ids(mod, [{"github_login": "x"}, {}, {"github_login": ""}, {"github_login": None}, "not-a-dict"]) == {"x"}

# empty historical default {}
assert ids(mod, {}) == set()
assert ids(mod, None) == set()


# --- 2) dict-of-records schema still used by outbound.authors() ---
dict_old = {"alice": {"github_login": "alice"}, "bob": {"github_login": "bob"}}
dict_new = {"alice": {"github_login": "alice"}, "bob": {"github_login": "bob"}, "carol": {"github_login": "carol"}}
assert ids(mod, dict_new) == {"alice", "bob", "carol"}
added, removed = ids(mod, dict_new) - ids(mod, dict_old), ids(mod, dict_old) - ids(mod, dict_new)
assert added == {"carol"} and removed == set(), (added, removed)

# mixing current list (live/old map) with a dict-shaped refresh should still compare by login
added, removed = ids(mod, dict_new) - ids(mod, list_old), ids(mod, list_old) - ids(mod, dict_new)
assert added == {"carol"} and removed == set(), (added, removed)


# --- 3) main(): mock every external side effect; write only under a temp tree ---
LIVE_MAP = "/workspace/mivo-qa/author-map.json"
live_mtime = os.path.getmtime(LIVE_MAP) if os.path.exists(LIVE_MAP) else None
live_size = os.path.getsize(LIVE_MAP) if os.path.exists(LIVE_MAP) else None

with tempfile.TemporaryDirectory(prefix="roster-test-") as tmp:
    state, qa = f"{tmp}/state", f"{tmp}/qa"
    os.makedirs(f"{state}/out/roster")
    os.makedirs(qa)
    old_path = f"{qa}/author-map.json"
    json.dump({"mivo_authors": rec("alice", "bob")}, open(old_path, "w"))
    new_payload = {"mivo_authors": rec("alice", "bob", "carol")}
    calls = {"secret": 0, "refresh": 0}

    def fake_secret():
        calls["secret"] += 1

    def fake_run(cmd, **kwargs):
        calls["refresh"] += 1
        assert cmd[:2] == ["python3", "/workspace/panoptes/scripts/refresh-roster.py"]
        assert "--out" in cmd
        outp = cmd[cmd.index("--out") + 1]
        assert outp.startswith(state + os.sep), outp
        json.dump(new_payload, open(outp, "w"))
        return type("R", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()

    with mock.patch.object(mod, "takeover", return_value=True), \
         mock.patch.object(mod, "mode", return_value="pi"), \
         mock.patch.object(mod, "now_cst", return_value=dt.datetime(2026, 11, 15, 8, 23, tzinfo=CST)), \
         mock.patch.object(mod, "today", return_value="2026-11-15"), \
         mock.patch.object(mod, "secret_env", fake_secret), \
         mock.patch.object(mod, "subprocess") as sp:
        sp.run.side_effect = fake_run
        mod.BASE = state
        mod.QA = qa
        rc = mod.main()
    assert rc == 0, rc
    assert calls == {"secret": 1, "refresh": 1}, calls
    md = open(f"{state}/out/roster/2026-11-15.md").read()
    expected_out = f"{state}/out/roster/author-map-2026-11-15.json"
    assert md == (
        f"# roster 2026-11-15\n"
        f"- 新增: ['carol']\n"
        f"- 移除: []\n"
        f"- 采纳需人工把 {expected_out} 复制到 {qa}/author-map.json\n"
    ), md
    # did not copy the new map over the (temp) live file
    assert json.load(open(old_path)) == {"mivo_authors": rec("alice", "bob")}

# skip paths keep rc=0 and must not call secret_env / refresh
with tempfile.TemporaryDirectory(prefix="roster-skip-") as tmp:
    state = f"{tmp}/state"
    os.makedirs(state)
    with mock.patch.object(mod, "takeover", return_value=False), \
         mock.patch.object(mod, "secret_env") as sec, \
         mock.patch.object(mod, "subprocess") as sp:
        mod.BASE = state
        assert mod.main() == 0
        sec.assert_not_called()
        sp.run.assert_not_called()

# production live map must be untouched
if live_mtime is not None:
    assert os.path.getmtime(LIVE_MAP) == live_mtime and os.path.getsize(LIVE_MAP) == live_size, "live author-map.json was modified"

print("ok test_roster_authors_set")
