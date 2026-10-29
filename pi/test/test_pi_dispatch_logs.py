"""python3 test/test_pi_dispatch_logs.py — 10-28：pi-dispatch 的 smoke / test 调用方日志进 out/test-logs/，不进生产 out/logs/。"""
import os, sys, tempfile, importlib.util, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import isolated, assert_inside  # noqa: E402

def script():   # was module level (ran at pytest collection and leaked env / frozen paths into other tests)
    os.environ["PI_STATE"] = tempfile.mkdtemp()
    B = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
    s = importlib.util.spec_from_file_location("pi_dispatch", f"{B}/pi_dispatch.py"); M = importlib.util.module_from_spec(s); s.loader.exec_module(M)
    assert_inside(os.environ["PI_STATE"], M.logf("smoke"), M.logf("prfix"))
    for c in ("smoke", "test", "test-x", "smoke_2"): assert "/out/test-logs/" in M.logf(c), (c, M.logf(c))
    for c in ("praise-pr", "prfix", "testament"): assert "/out/logs/" in M.logf(c), (c, M.logf(c))
    M.log("smoke", "hi"); assert not os.listdir(f"{os.environ['PI_STATE']}/out/logs")
    print("ok test_pi_dispatch_logs")

class Script(unittest.TestCase):
    def test_pi_dispatch_logs(self):
        with isolated(): script()   # own env, argv, os.kill and fresh pi/bin imports; all restored afterwards

if __name__ == "__main__":
    unittest.main()
