#!/usr/bin/env python3
"""Ticket B (10-30): daily handoff issue carries the full body (severity, file:line, tested merge sha, fingerprint) + the
mivo-qa-handoff marker; outbound gh.issue.create adds the `handoff` label (handoff.py only tracks marker + label issues);
new outbound gh.handoff.files writes daily/<YYYY>/<MM>/<day>/{feedback,receipts}.md in the route's handoff repo and
pushes them, fail-closed (handoff repos only, fixed paths, never overwrites a file it did not write). FAKE gh/git only."""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import os, sys, json, types, base64, tempfile, unittest, importlib.util
B = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
sys.modules.setdefault("merger", types.SimpleNamespace(FIELDS="number,title,headRefOid,state", blockers=lambda p: []))
spec = importlib.util.spec_from_file_location("ob_b", f"{B}/outbound.py"); O = importlib.util.module_from_spec(spec); spec.loader.exec_module(O)
import daily as D

F = {"severity": "P2", "file": "cindyplugin/src/a.test.ts", "line": 323, "fingerprint": "mq1-290ea2aeb2f2124988e8",
     "pr_fingerprint": "mq1-290ea2aeb2f2124988e8-pr1454", "title": "用例没有断言守卫确实拒绝", "source": "code-review"}
IT = {"n": 1454, "login": "aj0928", "who": "阿杰", "tested_sha": "abc123def4567890", "merge_sha": "abc123def4567890"}
REPO = "aj0928/aj0928-handoff"

class DailyIssueBody(unittest.TestCase):
    def test_body_has_severity_file_line_sha_fingerprint_and_marker(self):
        a = D.handoff_issue_action("2026-10-29", IT, F)
        self.assertEqual(a["type"], "gh.issue.create"); self.assertEqual(a["key"], "handoff:1454:mq1-290ea2aeb2f2124988e8-pr1454")
        t = a["text"]
        for want in ("P2", "cindyplugin/src/a.test.ts:323", "abc123def4567890", "mq1-290ea2aeb2f2124988e8", "#1454"):
            self.assertIn(want, t)
        self.assertEqual(a["handoff_key"], "mq1-290ea2aeb2f2124988e8-pr1454")
        self.assertEqual(a["labels"], ["handoff"])
    def test_missing_sha_or_line_is_said_not_invented(self):
        t = D.handoff_issue_action("2026-10-29", {**IT, "tested_sha": None, "merge_sha": None}, {**F, "line": None})["text"]
        self.assertIn("cindyplugin/src/a.test.ts", t); self.assertIn("未知", t)

class Gh:
    def __init__(self): self.calls = []; self.remote = {}; self.labels = {"handoff"}
    def __call__(self, args, js=True):
        self.calls.append(args)
        if args[:2] == ["issue", "list"]: return []
        if args[:2] == ["issue", "create"]: return "https://github.com/x/y/issues/9\n"
        if args[:2] == ["repo", "view"]: return {"nameWithOwner": REPO, "hasIssuesEnabled": True, "viewerPermission": "WRITE"}
        if args[:2] == ["label", "list"]: return [{"name": n} for n in self.labels]
        if args[0] == "api" and "/contents/" in args[-1]:
            p = args[-1].split("/contents/")[1].split("?")[0]
            if p not in self.remote: raise RuntimeError("gh api: HTTP 404 Not Found")
            return {"content": base64.b64encode(self.remote[p].encode()).decode(), "sha": "s"}
        raise AssertionError(args)

class IssueLabel(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); O.QA = self.d; self.g = O.gh = Gh()
    def test_create_adds_handoff_label_and_keeps_body(self):
        a = {**D.handoff_issue_action("2026-10-29", IT, F), "route_author": "aj0928"}
        who, state, validate, do = O.handler(a)
        self.assertEqual(state(), "pending"); validate(); do()
        c = next(c for c in self.g.calls if c[:2] == ["issue", "create"])
        self.assertEqual(c[c.index("--label") + 1], "handoff")
        body = c[c.index("--body") + 1]
        self.assertIn("cindyplugin/src/a.test.ts:323", body); self.assertIn("<!-- mivo-qa-handoff: key=mq1-290ea2aeb2f2124988e8-pr1454 pr=1454 -->", body)
        import handoff; m = handoff.MARK.search(body); self.assertEqual((m.group(1), m.group(2)), ("mq1-290ea2aeb2f2124988e8-pr1454", "1454"))
    def test_missing_label_fails_closed(self):
        self.g.labels = set(); a = {**D.handoff_issue_action("2026-10-29", IT, F), "route_author": "aj0928"}
        with self.assertRaises(RuntimeError): O.handler(a)[2]()

class HandoffFiles(unittest.TestCase):
    DAY = "2026-10-30"; BASEP = "daily/2026/10/2026-10-30"
    def setUp(self):
        self.d = tempfile.mkdtemp(); O.QA = self.d; self.g = O.gh = Gh(); self.runs = []
        for n in ("feedback.md", "receipts.md"): open(f"{self.d}/{n}", "w").write(f"# {n}\n内容\n")
        def fake_run(cmd, **kw):
            self.runs.append(cmd)
            if cmd[:3] == ["gh", "repo", "clone"]: os.makedirs(cmd[4], exist_ok=True)
            return ""
        O.run = fake_run
    def act(self, **kw):
        return {"key": f"handoff-files:{REPO}:{self.DAY}", "type": "gh.handoff.files", "repo": REPO, "day": self.DAY,
                "files": {"feedback.md": f"{self.d}/feedback.md", "receipts.md": f"{self.d}/receipts.md"}, **kw}
    def test_writes_both_files_and_pushes_plain(self):
        who, state, validate, do = O.handler(self.act())
        self.assertEqual(state(), "pending"); validate(); do()
        push = [c for c in self.runs if "push" in c]
        self.assertEqual(len(push), 1); self.assertFalse(any(x in push[0] for x in ("-f", "--force", "--no-verify")))
        self.assertTrue(any(c[-1].startswith("HEAD:refs/heads/") for c in push))
    def test_done_when_remote_equals_local(self):
        for n in ("feedback.md", "receipts.md"):
            self.g.remote[f"{self.BASEP}/{n}"] = O.handoff_file_text(open(f"{self.d}/{n}").read(), self.act()["key"])
        self.assertEqual(O.handler(self.act())[1](), "done")
    def test_never_overwrites_a_file_it_did_not_write(self):
        self.g.remote[f"{self.BASEP}/receipts.md"] = "Grok 写的回执\n"
        self.assertTrue(O.handler(self.act())[1]().startswith("stale:"))
        self.assertTrue(O.state_error_skips("gh.handoff.files"))
    def test_refuses_non_handoff_repo_bad_name_or_empty_file(self):
        for bad in (self.act(repo="xindong/mivo-canvas-plugin"), self.act(files={"../x.md": f"{self.d}/feedback.md"}),
                    self.act(day="2026/10/30"), self.act(files={"feedback.md": f"{self.d}/nope.md"})):
            with self.assertRaises(RuntimeError): O.handler(bad)[2]()
        open(f"{self.d}/feedback.md", "w").write("")
        with self.assertRaises(RuntimeError): O.handler(self.act())[2]()

if __name__ == "__main__":
    unittest.main()
