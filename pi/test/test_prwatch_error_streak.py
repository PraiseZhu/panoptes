#!/usr/bin/env python3
"""④ 11-04（prwatch-drafts-1103/FINDINGS.md「单次抖动不该告警」「成功后清掉 errors[name]」）：
11-03 10:44:12 drafts 一次 GraphQL 失败（之后每 110 秒都成功），watchdog 10:50 就报了 prwatch:error:drafts；errors.drafts 一直挂在 state.json。
现在：prwatch.run_step 记连续失败次数 error_counts[name]，真跑成功一次清掉 errors/error_counts（节流跳过不算）；watchdog 连续 3 次才报。
touch $PI_STATE/prwatch-error-alert-once 回到旧行为。只用临时目录，不联网、不调 gh。"""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import json, os, shutil, sys, tempfile, time, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import prwatch, watchdog  # noqa: E402

def boom(st): raise RuntimeError('gh pr list: Post "https://api.github.com/graphql": 502 Bad Gateway')
def ok(st): pass
def drafts_real(fail):
    def fn(st):
        st["drafts_at"] = time.time() + len(st.get("_n", "")); st["_n"] = st.get("_n", "") + "x"   # 真跑：节流时间戳变了
        if fail: boom(st)
    return fn

class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prwatch-streak-")
        self.saved = (getattr(prwatch, "ERR_ALERT_ONCE", None), prwatch.qlog, watchdog.PRWATCH_STATE, getattr(watchdog, "PRWATCH_ERR_ALERT_ONCE", None))
        prwatch.ERR_ALERT_ONCE = watchdog.PRWATCH_ERR_ALERT_ONCE = self.flag = f"{self.tmp}/prwatch-error-alert-once"
        watchdog.PRWATCH_STATE = f"{self.tmp}/state.json"
        self.logs = []; prwatch.qlog = self.logs.append
        self.now = watchdog.now_cst()
    def tearDown(self):
        p, prwatch.qlog, watchdog.PRWATCH_STATE, w = self.saved
        for m, a, v in ((prwatch, "ERR_ALERT_ONCE", p), (watchdog, "PRWATCH_ERR_ALERT_ONCE", w)):
            if v is None: delattr(m, a)
            else: setattr(m, a, v)
        shutil.rmtree(self.tmp, ignore_errors=True)
    def alerts(self, st):
        json.dump(st, open(watchdog.PRWATCH_STATE, "w")); p = {}
        watchdog.prfix_check(self.now, p)
        return sorted(k for k in p if k.startswith("prwatch:error:"))
    def fresh(self): return (self.now - watchdog.dt.timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")

    def test_watchdog_single_error_quiet_three_in_a_row_alerts(self):
        e = f"{self.fresh()} gh pr list: 502"
        self.assertEqual(self.alerts({"errors": {"drafts": e}}), [])                                   # 旧 state（没有计数）也不报
        self.assertEqual(self.alerts({"errors": {"drafts": e}, "error_counts": {"drafts": 2}}), [])
        self.assertEqual(self.alerts({"errors": {"drafts": e}, "error_counts": {"drafts": 3}}), ["prwatch:error:drafts"])
        open(self.flag, "w").close()                                                                   # 旧行为：一次就报
        self.assertEqual(self.alerts({"errors": {"drafts": e}}), ["prwatch:error:drafts"])

    def test_run_step_counts_consecutive_and_clears_on_success(self):
        st = {}
        for i in (1, 2, 3):
            prwatch.run_step(st, "fix", boom); self.assertEqual(st["error_counts"]["fix"], i)
        self.assertIn("502 Bad Gateway", st["errors"]["fix"])
        self.assertEqual(self.alerts(st), ["prwatch:error:fix"])
        prwatch.run_step(st, "fix", ok)
        self.assertNotIn("fix", st.get("errors") or {}); self.assertNotIn("fix", st.get("error_counts") or {})
        self.assertEqual(self.alerts(st), [])

    def test_throttled_skip_neither_clears_nor_breaks_the_streak(self):
        st = {}
        for _ in range(3):
            prwatch.run_step(st, "drafts", drafts_real(True))
            prwatch.run_step(st, "drafts", ok)                     # 110 秒节流内：drafts_at 没变，不算成功
        self.assertEqual(st["error_counts"]["drafts"], 3); self.assertIn("drafts", st["errors"])
        self.assertEqual(self.alerts(st), ["prwatch:error:drafts"])
        prwatch.run_step(st, "drafts", drafts_real(False))         # 真跑成功
        self.assertEqual((st["errors"], st["error_counts"]), ({}, {}))

    def test_flag_restores_old_behaviour(self):
        open(self.flag, "w").close(); st = {}
        prwatch.run_step(st, "fix", boom); prwatch.run_step(st, "fix", ok)
        self.assertIn("fix", st["errors"]); self.assertNotIn("error_counts", st)                  # 不计数、成功不清
        self.assertEqual(self.alerts(st), ["prwatch:error:fix"])

if __name__ == "__main__":
    unittest.main()
