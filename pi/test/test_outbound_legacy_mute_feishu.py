#!/usr/bin/env python3
"""11-03: (a) a pre-#153 ledger record "skipped praise-dm-mute" for a brief-routed key must go to the brief queue once, not
short-circuit as skipped(ledger); a key present in two days' actions files is listed once per run. (b) feishu.group post: the
sender pretty-prints JSON over several lines; a real success / dedupe must not be recorded as failed.
Run: python3 test/test_outbound_legacy_mute_feishu.py"""
import json, os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_outbound_mute_brief as B  # noqa: E402  (same sandbox + fixtures)
O = B.O
KEY = "overdue:zhongxingtian-ai/handover-mivo#8"
LEGACY = {"status": "skipped", "reason": "praise-dm-mute", "at": "2026-11-02T15:30:01+08:00"}


class LegacyMute(unittest.TestCase):   # B.T's sandbox, not its tests
    tearDown, main, ledger, queue, report = B.T.tearDown, B.T.main, B.T.ledger, B.T.queue, B.T.report
    def setUp(self):
        B.T.setUp(self)
        os.environ["PI_LOCK_DIR"] = self.tmp   # own exec lock per test: a run that crashes holding it can't make later runs wait 5 min
        a = dict(B.ROUTED[-1][1])   # the same overdue key was emitted on 11-02 too
        json.dump({"actions": [{**a, "at": "2026-11-02T15:30:00+08:00"}]}, open(f"{self.tmp}/out/handoff/2026-11-02.actions.json", "w"))
        os.makedirs(os.path.dirname(O.LEDGER), exist_ok=True); json.dump({KEY: LEGACY}, open(O.LEDGER, "w"))
    def rows(self): return [l for l in self.report().splitlines() if KEY in l]
    def test_legacy_mute_skip_is_briefed_once_and_idempotent(self):
        self.assertEqual(self.main("--days", "2"), 0)
        self.assertEqual(self.ledger()[KEY]["status"], "briefed")
        self.assertEqual([q["key"] for q in self.queue()].count(KEY), 1)
        self.assertEqual(self.main("--days", "2"), 0)   # later run: no re-enqueue
        self.assertEqual([q["key"] for q in self.queue()].count(KEY), 1)
    def test_bad_queue_line_does_not_crash_and_legacy_is_queued_once(self):
        open(O.BRIEF_QUEUE(), "w").write('{bad\n[1]\n{"key": "other"}\n')   # torn line from another writer, non-dict, unrelated
        self.assertEqual(self.main("--days", "2"), 0); self.assertEqual(self.main("--days", "2"), 0)
        self.assertEqual(self.ledger()[KEY]["status"], "briefed")
        keys = [json.loads(l).get("key") for l in open(O.BRIEF_QUEUE()).read().splitlines()[3:]]
        self.assertEqual(keys.count(KEY), 1, keys)
    def test_invalid_utf8_queue_does_not_crash_and_legacy_is_queued_once(self):
        open(O.BRIEF_QUEUE(), "wb").write(b'\xff\xfe{bad\n{"key": "x", "text": "\xe5\xa4'   # junk + torn multi-byte char
                                          b'"}\n')
        self.assertEqual(self.main("--days", "2"), 0); self.assertEqual(self.main("--days", "2"), 0)
        led = self.ledger(); self.assertEqual(led[KEY]["status"], "briefed")
        for _, a in B.ROUTED[:-1]: self.assertEqual(led[a["key"]]["status"], "briefed", a["key"])   # other actions still processed
        keys = [json.loads(l).get("key") for l in open(O.BRIEF_QUEUE(), "rb").read().splitlines()[2:]]
        self.assertEqual(keys.count(KEY), 1, keys)
    def test_key_in_two_days_listed_once_per_run(self):
        self.assertEqual(self.main("--days", "2"), 0)
        self.assertEqual(len(self.rows()), 1, self.rows())
    def test_mute_off_legacy_record_stays_skipped(self):
        os.remove(B.L.PRAISE_DM_MUTE)   # mute lifted: the old record keeps its pre-fix meaning (no DM, no queue)
        self.assertEqual(self.main("--days", "2", "--key", KEY), 0)
        self.assertEqual(self.ledger()[KEY], LEGACY); self.assertEqual(self.queue(), [])


# real sender output (send-feishu-report.mjs out(): JSON.stringify(obj, null, 2)), ledger.json 11-03 15:40 / feishu-sent.json
SENT = json.dumps({"ok": True, "message_id": "om_x100b61acb3a854a8cd0e819591c5d12", "receive_id_type": "chat_id",
                   "receive_id": "oc_ecd450e0bed37d49bc033f3cd07cd9af", "msg_type": "post", "dedupe_key": "mivo-overdue-notice:2026-11-03"}, indent=2) + "\n"
DEDUPE = json.dumps({"ok": True, "skipped": True, "reason": "dedupe_key_exists", "dedupe_key": "mivo-overdue-notice:2026-11-03",
                     "message_id": "om_x100b61acb3a854a8cd0e819591c5d12"}, indent=2) + "\n"


class FeishuGroupPost(unittest.TestCase):
    def do(self, stdout):
        import tempfile; d = tempfile.mkdtemp(); f = f"{d}/c.json"; json.dump({"zh_cn": {"content": []}}, open(f, "w"))
        saved = O.run, O.guard_text; self.addCleanup(lambda: (setattr(O, "run", saved[0]), setattr(O, "guard_text", saved[1])))
        O.run = lambda *a, **k: stdout; O.guard_text = lambda p: p
        a = {"key": "mivo-overdue-notice:2026-11-03", "type": "feishu.group", "msg_type": "post", "chat_id": "oc_ecd450e0bed37d49bc033f3cd07cd9af", "content_file": f}
        return O.h_feishu_group(a)[3]()
    def test_multiline_success_is_sent(self): self.assertEqual(self.do(SENT), "sent")
    def test_multiline_dedupe_is_skipped_not_failed(self): self.assertEqual(self.do(DEDUPE), "skipped (already sent)")
    def test_log_then_multiline_json_parses(self): self.assertEqual(self.do("node: warning x\n" + SENT), "sent")
    def test_one_line_after_log_still_parses(self): self.assertEqual(self.do("log line\n" + json.dumps({"ok": True}) + "\n"), "sent")


if __name__ == "__main__": unittest.main()
