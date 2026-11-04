#!/usr/bin/env python3
"""gaps-open.json has one lock (11-05, #215 acceptance): pmnudge.record_gap / close_gaps and stdcheck both go through jsonrec.update.
Before, record_gap flock()ed gaps-open.json.lock and left the file, which jsonrec's O_EXCL protocol read as held: the writers did not
exclude each other (lost updates) and stdcheck waited up to 30 s. Two writer processes interleave here (no entry lost, no hang); a
leftover empty .lock from a flock writer does not block; a flock writer still holding it is waited for; gh recovery closes pmnudge's
gap. Temp files only, no gh, no network."""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import fcntl, json, multiprocessing as mp, os, sys, tempfile, threading, time, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
T = tempfile.mkdtemp(prefix="gaps-lock-")
os.environ.update(PI_STATE=f"{T}/state", PI_FLEET_MODE="grok", PI_GAPS_FILE=f"{T}/gaps-open.json")
import jsonrec, pmnudge  # noqa: E402
import stdcheck  # noqa: E402
N = 25


def w_pmnudge(path, k):
    for i in range(N): pmnudge.record_gap(f"2026-12-{k}{i:02d}", f"{i:02d}:05", ["boom"], path=path)

def w_stdcheck(path, k):
    stdcheck.GAPS = path; now = stdcheck.now_cst()
    for i in range(N):
        stdcheck.sync_gaps({"qa_start": {f"PR-{k}{i:02d}": "late"}}, now)


class T_(unittest.TestCase):
    def setUp(self):
        self.p = tempfile.mktemp(dir=T, suffix=".json")

    def test_two_writers_interleave_no_loss_no_hang(self):
        """pmnudge.record_gap in one process, jsonrec.update appends in another, both at once"""
        def raw(path):
            for i in range(N): jsonrec.update(path, lambda d, i=i: d.append({"id": f"raw-{i}", "status": "open"}) or True, default=[])
        ctx = mp.get_context("fork"); ps = [ctx.Process(target=w_pmnudge, args=(self.p, 1)), ctx.Process(target=raw, args=(self.p,))]
        t0 = time.time()
        for p in ps: p.start()
        for p in ps: p.join(60)
        self.assertTrue(all(p.exitcode == 0 for p in ps), [p.exitcode for p in ps]); self.assertLess(time.time() - t0, 30)
        ids = {g["id"] for g in json.load(open(self.p))}
        self.assertEqual(len(ids), 2 * N); self.assertFalse(os.path.exists(self.p + ".lock"))

    def test_pmnudge_and_stdcheck_interleave(self):
        """the two real gaps-open.json writers at once: every pmnudge-gh-fail-* and stdcheck:* entry survives"""
        ctx = mp.get_context("fork"); ps = [ctx.Process(target=w_pmnudge, args=(self.p, 2)), ctx.Process(target=w_stdcheck, args=(self.p, 3))]
        for p in ps: p.start()
        for p in ps: p.join(60)
        self.assertEqual([p.exitcode for p in ps], [0, 0])
        gs = json.load(open(self.p))
        self.assertEqual(len([g for g in gs if g["id"].startswith("pmnudge-gh-fail-")]), N)
        self.assertEqual(len([g for g in gs if g["id"].startswith("stdcheck:")]), N)   # later runs close earlier ones, none lost

    def test_stale_flock_leftover_does_not_block(self):
        json.dump([], open(self.p, "w")); open(self.p + ".lock", "w").close()   # what the old record_gap left behind
        t0 = time.time(); pmnudge.record_gap("2026-12-31", "10:05", ["x"], path=self.p)
        self.assertLess(time.time() - t0, jsonrec.FLOCK_STALE_S + 3)
        self.assertEqual([g["id"] for g in json.load(open(self.p))], ["pmnudge-gh-fail-2026-12-31"])

    def test_live_flock_holder_is_waited_for(self):
        json.dump([], open(self.p, "w")); lk = open(self.p + ".lock", "a"); fcntl.flock(lk, fcntl.LOCK_EX)   # an old-style writer mid-update
        done = []
        th = threading.Thread(target=lambda: done.append(jsonrec.update(self.p, lambda d: d.append({"id": "a"}) or True))); th.start()
        time.sleep(jsonrec.FLOCK_STALE_S + 1); self.assertEqual(done, [])           # still waiting: not taken over
        lk.close(); th.join(20); self.assertEqual(done, [True])

    def test_gh_recovery_closes_pmnudge_gap(self):
        json.dump([{"id": "other", "status": "open"}], open(self.p, "w"))
        pmnudge.record_gap("2026-12-30", "11:05", ["rate limit"], path=self.p)
        self.assertEqual(pmnudge.close_gaps("2026-12-30", "12:05", path=self.p), ["pmnudge-gh-fail-2026-12-30"])
        gs = {g["id"]: g for g in json.load(open(self.p))}
        g = gs["pmnudge-gh-fail-2026-12-30"]; self.assertEqual(g["status"], "closed"); self.assertIn("12:05", g["closed_evidence"])
        self.assertEqual(gs["other"], {"id": "other", "status": "open"})
        self.assertEqual(pmnudge.close_gaps("2026-12-30", "13:05", path=self.p), [])   # idempotent
        self.assertEqual(pmnudge.close_gaps("2026-12-30", "13:05", path=self.p + ".none"), [])
        self.assertFalse(os.path.exists(self.p + ".none"))                          # never creates the file

    def test_main_closes_gap_when_gh_recovers(self):
        os.environ["PI_GAPS_FILE"] = self.p
        pmnudge.gh = lambda a, *x, **k: (_ for _ in ()).throw(RuntimeError("HTTP 502")); pmnudge.SLEEP = lambda s: None
        sys.argv = ["pmnudge.py"]; pmnudge.main()
        self.assertEqual([g["status"] for g in json.load(open(self.p))], ["open"])
        pmnudge.gh = lambda a, *x, **k: []; pmnudge.main()
        self.assertEqual([g["status"] for g in json.load(open(self.p))], ["closed"])


if __name__ == "__main__":
    try: unittest.main(verbosity=2)
    finally: __import__("shutil").rmtree(T, ignore_errors=True)
