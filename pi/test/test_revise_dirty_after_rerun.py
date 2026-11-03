#!/usr/bin/env python3
"""11-03 B: prfix --revise recorded a re-run at head H as "pushed, last_head=H"; when the draft then conflicted with main at the
same head H it stopped with "这个 head 已经修过、等推送" and never merged main in. Now that stop only holds for the same kind of round
(CI fix vs main merge). touch PI_STATE/pmnudge-b-off -> old stop. Local only. Run: python3 test/test_revise_dirty_after_rerun.py"""
import os, sys, types, contextlib, unittest, importlib
os.environ.setdefault("PRFIX_OWNERS", "*"); os.environ["PRFIX_DISPATCHED"] = "1"
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin")); sys.path.insert(0, HERE)
import isolation  # noqa: E402
isolation.sandbox_state()
import prfix  # noqa: E402
H = "a" * 40

class T(unittest.TestCase):
    def run_revise(self, ticket):
        T_ = {"tickets": {"rev:1301": dict(ticket)}}; calls = []
        p = {"number": 1301, "headRefOid": H, "headRefName": "qa-fix/fixpr-1179-x", "mergeStateStatus": "DIRTY", "statusCheckRollup": []}
        def merge(*a): calls.append("merge"); return {"status": "pr_action_written", "action_key": "revise:1301:" + H[:12] + ":merge"}, {}
        patches = dict(takeover=lambda n: True, _gh_json=lambda a, t=0: p if "view" in a else [], revise_target=lambda p, c: (1179, "mq1-x", None),
                       mem_gb=lambda: 99.0, try_lock=lambda path: object(), unlock=lambda f: None, revise_merge=merge,
                       tickets_locked=lambda: contextlib.nullcontext(T_), finish=lambda r, code, A: r, log=lambda *a: None)
        saved = {k: getattr(prfix, k) for k in patches}
        try:
            for k, v in patches.items(): setattr(prfix, k, v)
            r = prfix.revise(types.SimpleNamespace(revise=1301, dry_run=False))
        finally:
            for k, v in saved.items(): setattr(prfix, k, v)
        return r, calls, T_["tickets"]["rev:1301"]

    def test_rerun_at_this_head_then_dirty_merges_main(self):
        r, calls, t = self.run_revise({"status": "pushed", "last_head": H, "rerun_only": True, "revisions": 1, "last_cnt": "revisions"})
        self.assertEqual(calls, ["merge"], r)
        self.assertEqual((t["status"], t["last_cnt"], t["merges"]), ("pushed", "merges", 1))

    def test_merge_already_pushed_at_this_head_is_not_redone(self):
        r, calls, _ = self.run_revise({"status": "pushed", "last_head": H, "merges": 1, "last_cnt": "merges"})
        self.assertEqual((calls, r["status"]), ([], "skipped"))

    def test_off_flag_keeps_old_stop(self):
        os.makedirs(os.path.dirname(prfix.B_OFF), exist_ok=True); open(prfix.B_OFF, "w").close()
        try:
            r, calls, _ = self.run_revise({"status": "pushed", "last_head": H, "rerun_only": True, "revisions": 1})
            self.assertEqual((calls, r["status"]), ([], "skipped"))
        finally: os.remove(prfix.B_OFF)

if __name__ == "__main__": unittest.main()
