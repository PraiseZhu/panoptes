"""并发写同一 JSON：outage.jw / mergewatch.save 多进程同时写不崩、结果是合法 JSON（不起任何真实作业）"""
import json, multiprocessing as mp, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import outage

def _w(p, i, n, q):
    try:
        for k in range(n): outage.jw(p, {"w": i, "k": k, "pad": "x" * 2000})
        q.put(None)
    except BaseException as e: q.put(repr(e))

def _mw(d, i, n, q):
    try:
        os.environ["PI_BASE"] = d
        import mergewatch as M
        M.DRY = False; M.STATE_DIR = d; M.STATE = os.path.join(d, "state.json")
        for k in range(n): M.save({"w": i, "k": k})
        q.put(None)
    except BaseException as e: q.put(repr(e))

class T(unittest.TestCase):
    def run_many(self, tgt, arg):
        q = mp.Queue(); ps = [mp.Process(target=tgt, args=(arg, i, 150, q)) for i in range(6)]
        [p.start() for p in ps]; [p.join(60) for p in ps]
        errs = [q.get(timeout=5) for _ in ps]
        self.assertEqual([e for e in errs if e], [])

    def test_jw_concurrent(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "heartbeat.json"); self.run_many(_w, p)
            self.assertIn("w", json.load(open(p)))
            self.assertEqual([f for f in os.listdir(d) if f.endswith(".tmp")], [])

    def test_mergewatch_save_concurrent(self):
        with tempfile.TemporaryDirectory() as d:
            self.run_many(_mw, d)
            self.assertIn("k", json.load(open(os.path.join(d, "state.json"))))
            self.assertEqual([f for f in os.listdir(d) if f.endswith(".tmp")], [])

if __name__ == "__main__": unittest.main()
