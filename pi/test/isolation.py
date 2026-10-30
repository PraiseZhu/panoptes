"""Test-only isolation helpers (not a test module). Production paths are frozen at import in several pi/bin modules
(lib.BASE = $PI_STATE, pmnudge.PROGRESS, ...): a test that sets PI_STATE after another test imported lib would write the
real /workspace/mivo-qa/pi. isolated() gives a block its own os.environ, sys.argv, os.kill/killpg and a fresh import of every
pi/bin module, and puts all of it back afterwards so nothing leaks into the next test."""
import contextlib, os, sys
from unittest import mock
BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")

def bin_modules():
    return [n for n, m in list(sys.modules.items()) if os.path.dirname(os.path.abspath(getattr(m, "__file__", None) or "/")) == BIN]

@contextlib.contextmanager
def isolated(**env):
    with contextlib.ExitStack() as st:
        st.enter_context(mock.patch.dict(os.environ, env))
        st.enter_context(mock.patch.dict(sys.modules))     # restores the module table (bin modules imported here are dropped)
        st.enter_context(mock.patch.object(sys, "argv", list(sys.argv)))
        st.enter_context(mock.patch.object(sys, "path", list(sys.path)))
        st.enter_context(mock.patch.object(os, "kill", os.kill)); st.enter_context(mock.patch.object(os, "killpg", os.killpg))
        for n in bin_modules(): del sys.modules[n]          # fresh import: constants see THIS block's env
        yield

def assert_inside(root, *paths):
    """refuse to go on unless every path is inside root (a temp dir)"""
    r = os.path.realpath(root) + os.sep
    for p in paths:
        if not (os.path.realpath(p) + os.sep).startswith(r): raise AssertionError(f"{p} is not inside {root}: refusing to touch real paths")


def live_state():
    """the box's live state root (PI_TEST_LIVE_STATE: test-only override, so a fake live dir can be used to prove isolation)"""
    return os.environ.get("PI_TEST_LIVE_STATE") or "/workspace/mivo-qa/pi"

def sandbox_state():
    """call BEFORE importing pi/bin modules in a test file that may be run directly (python3 test/x.py): when PI_STATE is unset
    or is the live root, point PI_STATE / PI_WORK / PI_WORK_ROOT at a fresh temp dir, so lib.BASE and every path derived from it
    are scratch. Under pytest (conftest) and selftest (env -i PI_STATE=tmp) PI_STATE is already a temp dir: no-op. Returns BASE."""
    import tempfile
    cur = os.environ.get("PI_STATE")
    if not cur or os.path.realpath(cur) == os.path.realpath(live_state()):
        t = tempfile.mkdtemp(prefix="pi-test-state-")
        os.environ.update(PI_STATE=f"{t}/state", PI_WORK=f"{t}/work", PI_WORK_ROOT=f"{t}/work")
        for d in ("state", "work"): os.makedirs(f"{t}/{d}", exist_ok=True)
    return os.environ["PI_STATE"]
