"""opsdeploy 只在 deploy 无错且 HEAD == origin/main 时才同步 pi-call / cron；picall_sync 复制保留可执行位、不带 mtime。"""
import os, sys, time, types, tempfile, importlib
B = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)


class _MP:
    def __init__(self): self.undo = []
    def setattr(self, o, n, v): self.undo.append((setattr, o, n, getattr(o, n))); setattr(o, n, v)
    def setitem(self, d, k, v): self.undo.append(("item", d, k, d.get(k, _MP))); d[k] = v
    def restore(self):
        for f, o, n, v in reversed(self.undo):
            if f == "item":
                if v is _MP: o.pop(n, None)
                else: o[n] = v
            else: setattr(o, n, v)


def _od():
    monkeypatch = _MP()
    import opsdeploy, opsenv
    calls = []
    monkeypatch.setattr(opsdeploy, "mode", lambda: "pi")
    monkeypatch.setattr(opsdeploy, "log", lambda *a: None)
    monkeypatch.setitem(sys.modules, "picall_sync", types.SimpleNamespace(sync=lambda: calls.append("picall") or []))
    monkeypatch.setitem(sys.modules, "cron_sync", types.SimpleNamespace(sync=lambda: calls.append("cron") or None))
    state = {"err": None, "head": "a" * 40, "tgt": "a" * 40}
    monkeypatch.setattr(opsenv, "deploy", lambda L: state["err"])
    def sh(c, t=60):
        if "rev-parse HEAD" in c: return 0, state["head"]
        if "rev-parse origin/main" in c: return 0, state["tgt"]
        return 0, ""
    monkeypatch.setattr(opsenv, "sh", sh)
    return opsdeploy, state, calls, monkeypatch


def test_deploy_error_no_sync():
    m, st, calls, mp = _od(); st["err"] = "panoptes fetch failed"
    try: m.main(); assert calls == []
    finally: mp.restore()


def test_head_behind_origin_no_sync():
    m, st, calls, mp = _od(); st["tgt"] = "b" * 40
    try: m.main(); assert calls == []
    finally: mp.restore()


def test_success_syncs():
    m, st, calls, mp = _od()
    try: m.main(); assert calls == ["picall", "cron"]
    finally: mp.restore()


def test_copy_keeps_exec_bit_not_mtime():
    import picall_sync as M
    d = tempfile.mkdtemp(); repo, live = f"{d}/repo", f"{d}/live"; os.makedirs(repo); os.makedirs(live)
    p = f"{repo}/pi-auto"; open(p, "w").write("x\n"); os.chmod(p, 0o644); os.utime(p, (1000000, 1000000))
    assert M.sync(repo, live) == ["pi-auto"]
    st = os.stat(f"{live}/pi-auto")
    assert st.st_mode & 0o777 == 0o755
    assert abs(st.st_mtime - time.time()) < 60


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"): f()
    print("ok test_opsdeploy_sync_guard")
