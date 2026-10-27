"""python3 test/test_mergewatch_resume.py — 10-27：VM 每次恢复（outage pause）mergewatch 都对同一个 main sha 重起 catch-up
（03:46、07:42、07:56、08:23、08:36、09:18 共 6 次，都无事可做）。停机恢复不再置 dirty，只有 main sha 变了才起。不联网、不起真进程。"""
import os, sys, json, tempfile, types, importlib.util
tmp = tempfile.mkdtemp(); os.environ.update(PI_STATE=tmp, PI_FLEET_MODE="pi"); os.environ.pop("PI_BASE", None)
B = os.environ.get("PI_BIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
s = importlib.util.spec_from_file_location("mergewatch", f"{B}/mergewatch.py"); M = importlib.util.module_from_spec(s); s.loader.exec_module(M)
M.DRY = False; launches = []; head = ["23247021aaaa"]
M.heartbeat = lambda mode: {"kind": "pause", "start": "2026-10-27T08:37:01+08:00", "end": "2026-10-27T09:18:35+08:00", "gap_min": 41.6}
M.subprocess = types.SimpleNamespace(run=lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=f"{head[0]}\trefs/heads/main\n"), TimeoutExpired=TimeoutError)
M.gh_token = lambda: None; M.catchup_running = lambda: False
M.launch = lambda cmd, sha: launches.append((cmd, sha)) or types.SimpleNamespace(pid=1)
os.makedirs(M.STATE_DIR); json.dump({"main_sha": head[0], "dirty": False}, open(M.STATE, "w"))
for _ in range(3): M.main()
assert launches == [], launches                       # 同一个 sha，停机恢复不重起 catch-up
head[0] = "96edc713bbbb"; M.main()                    # 停机期间有合并：sha 变了 → 起一次
assert len(launches) == 1 and "--catch-up" in launches[0][0] and launches[0][1] == head[0], launches
M.main(); assert len(launches) == 1
print("ok test_mergewatch_resume")
