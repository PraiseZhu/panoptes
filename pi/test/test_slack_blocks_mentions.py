"""report/slack/build-slack-blocks.mjs: an @ it cannot resolve (e.g. @scope/pkg, an unknown name) is sent as plain
text with a warning on stderr -- the daily report is never refused (exit 0, JSON on stdout)."""
import json, os, shutil, subprocess, tempfile, unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
SCRIPT = os.path.join(ROOT, "report", "slack", "build-slack-blocks.mjs")
IDS = {"_meta": {}, "alice": {"name": "张三", "slack_id": "U0ALICE1"}}
REPORT = "\n".join([
    "Mivo 播报 10 月 30 日：今日 1 个问题",
    "P1 严重问题",
    "#101 升级 @types/node 和 @babel/core — @张三",
    "· 问题：依赖 @scope/pkg 不兼容，@未知人 跟进",
    "系统状态",
    "· 正常 @someone",
])

@unittest.skipUnless(shutil.which("node"), "node not installed")
class SlackBlocksMentions(unittest.TestCase):
    def run_build(self):
        d = tempfile.mkdtemp()
        try:
            src, ids = os.path.join(d, "group-content.txt"), os.path.join(d, "ids.json")
            open(src, "w", encoding="utf-8").write(REPORT)
            json.dump(IDS, open(ids, "w", encoding="utf-8"), ensure_ascii=False)
            return subprocess.run(["node", SCRIPT, src, ids], capture_output=True, text=True, timeout=60)
        finally:
            shutil.rmtree(d)

    def test_unknown_mentions_never_refuse_report(self):
        p = self.run_build()
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        blob = json.dumps(out, ensure_ascii=False)
        self.assertIn("<@U0ALICE1>", blob)                    # known name resolved
        for raw in ("@types/node", "@babel/core", "@scope/pkg", "@未知人", "@someone"):
            self.assertIn(raw, blob)                          # unknown @ kept as plain text
        self.assertIn("warning", p.stderr.lower())
        self.assertIn("@未知人", p.stderr)
        self.assertNotIn("@scope/pkg", p.stderr)              # package names are not people: no warning noise

if __name__ == "__main__":
    unittest.main()
