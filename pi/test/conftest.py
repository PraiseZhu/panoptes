"""pytest only (python3 test/x.py never loads this). Makes a combined `pytest pi/test` behave like running each file alone.

1. Before collection: every production path that comes from env points into one temp dir (PI_STATE, PI_WORK(_ROOT), HOME,
   boxhealth's /tmp + log dirs, memguard, the outage witness, the rollback mark, the /tmp job locks). Several pi/bin modules build paths from env
   at import (lib.BASE, pmnudge.PROGRESS, ...): a test that imports them without its own env used to get the real
   /workspace/mivo-qa/pi and /workspace/pi-work, and later tests wrote there.
2. Per test module: its pi/bin modules are imported fresh (under its own env); what it does to os.environ, os.kill/killpg,
   sys.argv and sys.modules while being imported is recorded, rolled back before the next module is collected, re-applied
   while its own tests run, and rolled back again afterwards. Test modules can no longer see each other's state."""
import os, sys, tempfile
import pytest

_T = tempfile.mkdtemp(prefix="pi-pytest-")
SANDBOX_ENV = {
    "PI_STATE": f"{_T}/state", "PI_WORK": f"{_T}/work", "PI_WORK_ROOT": f"{_T}/work", "HOME": f"{_T}/home",
    "BOXHEALTH_TMP_ROOT": f"{_T}/tmp", "BOXHEALTH_LOG_DIRS": f"{_T}/logs", "MEMGUARD_DIR": f"{_T}/memguard",
    "PI_OUTAGE_WITNESS": f"{_T}/work/locks/outage-seq-witness", "PI_ROLLBACK_MARK": f"{_T}/state/ROLLBACK-SUSPECTED",
    "npm_config_cache": f"{_T}/home/.npm", "PI_LOCK_DIR": f"{_T}/tmp",   # mergewatch / outbound job flocks (else the real /tmp ones)
}
for _d in ("state", "work", "home", "tmp", "logs", "memguard"): os.makedirs(f"{_T}/{_d}", exist_ok=True)
os.environ.update(SANDBOX_ENV)
for _k in [k for k in os.environ if k.startswith("GIT_")]: del os.environ[_k]   # e.g. GIT_DIR from a pre-push hook

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
_BASELINE = dict(os.environ)
_REAL = (os.kill, os.killpg, list(sys.argv))
_PER_MODULE = {}   # module path -> (env after its import, its pi/bin modules, os.kill, os.killpg)

def _bin_modules():
    """pi/bin modules, plus test modules imported as helpers (test_prfix_parallel imports test_prfix_unit: that copy must use
    the same prfix as the importer, not one collected under another module's env)"""
    out = {}
    for n, m in list(sys.modules.items()):
        f = os.path.abspath(getattr(m, "__file__", None) or "/")
        if os.path.dirname(f) == BIN or (os.path.dirname(f) == HERE and os.path.basename(f) != "conftest.py"): out[n] = m
    return out

def _set_env(env):
    cur = os.environ.get("PYTEST_CURRENT_TEST")      # pytest's own bookkeeping: it deletes this after each phase
    os.environ.clear(); os.environ.update({k: v for k, v in env.items() if k != "PYTEST_CURRENT_TEST"})
    if cur is not None: os.environ["PYTEST_CURRENT_TEST"] = cur

def _reset():
    _set_env(_BASELINE)
    os.kill, os.killpg = _REAL[0], _REAL[1]; sys.argv[:] = _REAL[2]
    for n in _bin_modules(): del sys.modules[n]

@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector):
    if not isinstance(collector, pytest.Module): return (yield)
    _reset()                                   # fresh pi/bin imports for this module, baseline env
    try: return (yield)
    finally:
        _PER_MODULE[str(collector.path)] = (dict(os.environ), _bin_modules(), os.kill, os.killpg)
        _reset()

@pytest.fixture(autouse=True, scope="module")
def _module_isolation(request):
    env, mods, kill, killpg = _PER_MODULE.get(str(request.path), (_BASELINE, {}, _REAL[0], _REAL[1]))
    _reset(); _set_env(env); sys.modules.update(mods); os.kill, os.killpg = kill, killpg
    yield
    _reset()
