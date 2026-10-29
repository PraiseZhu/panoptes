#!/usr/bin/env python3
"""给 Praise 的私信骨架（Praise 2026-10-24：私信看不懂）—— praise_notice 本身，以及看门狗 / 环境自检 / 外发转发用它写出来的样子。
Local only. Run: cd bin && python3 ../test/test_praise_notice.py"""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import os, sys, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import praise_notice as PN
BOX_MERGER = os.path.exists("/workspace/mivo-merger/app/scripts/loops/automerge/policy.json")   # box-only data (CI has none)
BOX_PANOPTES = os.path.isdir("/workspace/panoptes/.git")

PREFIXES = tuple(PN.KINDS.values())
INTERNAL = ("rc=", "stale:", "infra:", "sig", "ledger.json", "SHADOW", "NOT SENT", "<!--")


def check_skeleton(tc, msg):
    """每件事：前缀｜标题 / 发生了什么 / 你要做（或 系统在做 + 你要做：不用动手）"""
    blocks = [b for b in msg.split("\n\n") if b.startswith(PREFIXES)]
    tc.assertTrue(blocks, msg)
    for b in blocks:
        lines = b.split("\n")
        tc.assertIn("｜", lines[0]); tc.assertTrue(lines[1].startswith("发生了什么："), b)
        tc.assertTrue(any(l.startswith("你要做") for l in lines), b)
        if b.startswith(PN.KINDS["action"]):
            todo = "\n".join(l for l in lines if l.startswith("你要做") or l[:2] in ("1.", "2.", "3."))
            tc.assertNotIn("不用", todo.split("\n")[0][:8], b)
        # 标题和「发生了什么」不写内部代号（代号只出现在「你要做」里给会话的那句话里）
        for bad in INTERNAL:
            tc.assertNotIn(bad, lines[0] + lines[1], b)


class Skeleton(unittest.TestCase):
    def test_one_item(self):
        m = PN.text(PN.item("action", "「消息外发」停了", "它 95 分钟没跑。", "通知都发不出去。", todo=PN.ask_session("查 outbound")))
        self.assertEqual(m.split("\n")[0], "🔴 要你处理｜「消息外发」停了")
        self.assertIn("把这句话发给它：「查 outbound」", m)
        check_skeleton(self, m)

    def test_several_items_sorted_and_counted(self):
        m = PN.text(PN.item("fyi", "进展", "做完了。"), PN.item("action", "坏了", "坏了。", todo="修"), PN.item("auto", "在修", "在修。", auto="重试"),
                    still_open=["老问题"])
        self.assertTrue(m.startswith("3 件事，其中 1 件要你处理："), m)
        order = [l.split("｜")[0] for l in m.split("\n") if l.startswith(PREFIXES)]
        self.assertEqual(order, [PN.KINDS["action"], PN.KINDS["auto"], PN.KINDS["fyi"]])
        self.assertTrue(m.endswith("之前说过、还没解决的：老问题"))
        self.assertIn("系统在做：重试\n你要做：不用动手。", m)
        check_skeleton(self, m)

    def test_guards(self):
        with self.assertRaises(ValueError): PN.item("action", "t", "w")      # 要他处理就必须写清做什么
        with self.assertRaises(ValueError): PN.item("auto", "t", "w")        # 系统处理就必须写清系统在做什么
        with self.assertRaises(ValueError): PN.item("bogus", "t", "w")
        self.assertEqual(PN.text(), "")


class Producers(unittest.TestCase):
    def test_watchdog_job_items(self):
        sys.argv = ["watchdog.py", "--dry-run"]
        import watchdog as W
        crit = W.job_item("outbound", "它本该每 40 分钟内成功跑一次，现在已经 95 分钟没有成功跑过", "已 95 分钟没有成功运行")
        self.assertEqual(crit["kind"], "action"); self.assertIn("「消息外发」", crit["title"])
        self.assertIn("所有通知和自动操作都卡住", crit["impact"])
        low = W.job_item("compare", "今天 12:50 那一轮根本没启动", "12:50 这一轮没启动")
        self.assertEqual(low["kind"], "fyi"); self.assertIn("不急", low["title"])
        check_skeleton(self, PN.text(crit, low))

    @unittest.skipUnless(BOX_PANOPTES, "needs the box's /workspace/panoptes checkout")
    def test_opsenv_items(self):
        import opsenv as O
        self.assertIsNone(O.env_item("panoptes HEAD 1234abcd != origin/main 5678ef01"), "有新代码待部署不单独打扰")
        for x in ("missing: chromium", "gh auth not logged in", "panoptes has 3 modified tracked files", "secrets.env missing or not 600",
                  "panoptes diverged from origin/main", "panoptes fetch failed", "something odd"):
            it = O.env_item(x); self.assertIsNotNone(it, x); check_skeleton(self, PN.text(it))
        self.assertEqual(O.env_item("panoptes fetch failed")["kind"], "auto")

    @unittest.skipUnless(BOX_MERGER, "needs the box's mivo-merger policy")
    def test_outbound_relay_and_notices(self):
        import outbound as OB
        # 生产里 job = out/ 下的目录名：pmnudge 写在 out/pm/，key pm-chief:<日>，收件人 "Jasper (Chief of Staff)"
        t = OB.relay_item({"key": "pm-chief:2026-10-23", "type": "SendToAgent", "to": "Jasper (Chief of Staff)", "text": "今天 4 轮，共催 1 次"}, "pm")
        self.assertTrue(t.startswith(PN.KINDS["fyi"] + "｜白天催办汇总"), t)
        self.assertIn("原来是发给 Jasper（Chief of Staff）的", t)
        self.assertNotIn("原发 Grok", t)
        ready = PN.text(PN.item("action", "x", "y", todo="z"))
        self.assertEqual(OB.relay_item({"type": "grokbot.chat-to-Praise", "to": "Praise", "text": ready}, "followup"), ready, "已经按骨架写好的原样转")
        self.assertEqual(OB.what_of("finding:923:abc"), "「你的 PR 测出新问题」的通知")
        self.assertEqual(OB.what_of("weird:1", "gh.pr.merge"), "自动操作")
        self.assertTrue(OB.relay_item({"key": "pm-cos:2026-10-23", "type": "SendToAgent", "to": "Jasper (Chief of Staff)", "text": "x"}, "pm").startswith(PN.KINDS["fyi"] + "｜测试项目经理的核对汇总"))

    def test_opsenv_not_on_main_is_reported(self):
        import opsenv as O
        it = O.env_item("panoptes HEAD 1234abcd != origin/feat/x 5678ef01")
        self.assertEqual(it["kind"], "action"); self.assertIn("feat/x 分支", it["what"])
        self.assertEqual(O.env_item("panoptes HEAD 1234abcd != origin/HEAD 5678ef01")["kind"], "action")

    def test_boxhealth_honours_dry_run_flag(self):
        import boxhealth as B   # may be cached from an earlier test module: DRY is read when used, not at import
        with mock.patch.object(sys, "argv", ["boxhealth.py", "--dry-run"]): self.assertTrue(B.DRY)
        with mock.patch.object(sys, "argv", ["boxhealth.py"]), mock.patch.dict(os.environ, PI_BOXHEALTH_DRYRUN="1"): self.assertTrue(B.DRY)

    def test_watchdog_falls_back_when_praise_notice_is_broken(self):
        import importlib, builtins
        real = builtins.__import__
        def broken(name, *a, **k):
            if name == "praise_notice": raise SyntaxError("broken praise_notice")
            return real(name, *a, **k)
        sys.argv = ["watchdog.py", "--dry-run"]; sys.modules.pop("watchdog", None)
        builtins.__import__ = broken
        try: import watchdog as W2
        finally: builtins.__import__ = real
        try:
            msg = W2.PN.text(W2.job_item("outbound", "停了", "停了"))
            self.assertIn("praise_notice 加载失败", msg); self.assertIn("outbound", msg)
        finally: sys.modules.pop("watchdog", None)


if __name__ == "__main__":
    unittest.main(verbosity=0)
