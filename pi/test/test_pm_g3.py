"""python3 test/test_pm_g3.py — pm.py G3（10-24「日报合并 None 个」）：Grok 的 sent 原文没有就读 Pi 的 out/daily/<日>.md；
日报那次运行 rc≠0 → daily_crash 返回日志路径（G3 记「daily 崩溃」）。只用临时 PI_STATE，不读写真实数据。"""
import sys, os, tempfile, importlib.util, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import isolated, assert_inside  # noqa: E402

def script():   # was module level (ran at pytest collection and leaked env / frozen paths into other tests)
    tmp = tempfile.mkdtemp(); os.environ["PI_STATE"] = tmp
    B = os.environ.get("PI_BIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
    spec = importlib.util.spec_from_file_location("pm", f"{B}/pm.py"); P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)
    assert_inside(tmp, f"{P.BASE}/out")
    P.QA = tmp + "/qa"   # Grok sent 路径指到空目录
    os.makedirs(f"{tmp}/out/daily"); os.makedirs(f"{tmp}/out/logs")
    day = "2099-01-02"
    assert P.daily_text(day)[1] == ""
    open(f"{tmp}/out/daily/{day}.md", "w").write("Mivo 播报 1 月 2 日：合了 3 个 PR\n")
    f, t = P.daily_text(day); assert f.endswith(f"out/daily/{day}.md") and "合了 3 个" in t
    assert P.daily_crash(day) is None
    lg = f"{tmp}/out/logs/daily-2099-01-03.log"
    open(lg, "w").write("=== start daily\nTraceback\n=== end daily rc=1\n"); assert P.daily_crash(day) == lg
    open(lg, "a").write("=== end daily rc=0\n"); assert P.daily_crash(day) is None   # 补跑成功就不算崩溃
    print("ok test_pm_g3")

class Script(unittest.TestCase):
    def test_pm_g3(self):
        with isolated(): script()   # own env, argv, os.kill and fresh pi/bin imports; all restored afterwards

if __name__ == "__main__":
    unittest.main()
