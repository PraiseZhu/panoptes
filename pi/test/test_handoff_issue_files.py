#!/usr/bin/env python3
"""Ticket B (10-30): daily handoff issue carries the full body (severity, file:line, tested merge sha, fingerprint) + the
mivo-qa-handoff marker; outbound gh.issue.create adds the `handoff` label (handoff.py only tracks marker + label issues);
new outbound gh.handoff.files writes daily/<YYYY>/<MM>/<day>/{feedback,receipts}.md in the route's handoff repo and
pushes them, fail-closed (handoff repos only, fixed paths, never overwrites a file it did not write). FAKE gh/git only."""
for _k in [k for k in __import__("os").environ if k.startswith("GIT_")]: del __import__("os").environ[_k]
import os, sys, json, types, base64, shutil, tempfile, unittest, importlib.util
B = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
sys.modules.setdefault("merger", types.SimpleNamespace(FIELDS="number,title,headRefOid,state", blockers=lambda p: []))
spec = importlib.util.spec_from_file_location("ob_b", f"{B}/outbound.py"); O = importlib.util.module_from_spec(spec); spec.loader.exec_module(O)
import daily as D
O.BASE = tempfile.mkdtemp(prefix="pi-test-handoff-")   # the HMAC key file lives here, never in the real PI_STATE

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
    """PM 10-30: 日报 writes the files to $QA/handoff/staging/<repo name>/daily/<YYYY>/<MM>/<day>/{feedback,receipts}.md;
    gh.handoff.files only reads them and pushes, byte for byte; identical remote = no-op, never an empty commit."""
    DAY = "2026-10-30"; BASEP = "daily/2026/10/2026-10-30"
    def setUp(self):
        self.d = tempfile.mkdtemp(); O.QA = self.d; self.g = O.gh = Gh(); self.runs = []; self.cached_diff = 1
        self.st = f"{self.d}/handoff/staging/aj0928-handoff/{self.BASEP}"; os.makedirs(self.st)
        for n in ("feedback.md", "receipts.md"): open(f"{self.st}/{n}", "w").write(f"# {n}\n内容\n")
        def fake_run(cmd, **kw):
            self.runs.append(cmd)
            if cmd[:3] == ["gh", "repo", "clone"]: os.makedirs(cmd[4], exist_ok=True)
            if "diff" in cmd and "--quiet" in cmd and self.cached_diff == 0: return ""
            if "diff" in cmd and "--quiet" in cmd: raise RuntimeError("git rc=1: ")
            return ""
        O.run = fake_run
    def act(self, **kw):
        return {"key": f"handoff-files:{REPO}:{self.DAY}", "type": "gh.handoff.files", "repo": REPO, "day": self.DAY, **kw}
    def test_staging_path_contract(self):
        self.assertEqual(O.handoff_staging_dir(REPO, self.DAY), self.st)
    def test_pushes_staged_files_verbatim_plain_push(self):
        who, state, validate, do = O.handler(self.act())
        self.assertEqual(state(), "pending"); validate(); do()
        push = [c for c in self.runs if "push" in c]
        self.assertEqual(len(push), 1); self.assertFalse(any(x in push[0] for x in ("-f", "--force", "--no-verify")))
        self.assertEqual(push[0][-1], "HEAD:refs/heads/main")
        self.assertTrue(any("commit" in c for c in self.runs))
    def test_identical_remote_is_noop_no_commit(self):
        for n in ("feedback.md", "receipts.md"): self.g.remote[f"{self.BASEP}/{n}"] = open(f"{self.st}/{n}").read()
        self.assertEqual(O.handler(self.act())[1](), "done")
    def test_only_changed_file_is_written(self):
        self.g.remote[f"{self.BASEP}/feedback.md"] = open(f"{self.st}/feedback.md").read()
        self.g.remote[f"{self.BASEP}/receipts.md"] = O.handoff_owned_text("旧回执\n", REPO, f"{self.BASEP}/receipts.md", self.act()["key"])
        O.handler(self.act())[3]()
        add = next(c for c in self.runs if "add" in c)
        self.assertEqual(add[-1:], [f"{self.BASEP}/receipts.md"])
    def test_clone_already_identical_makes_no_commit(self):
        self.cached_diff = 0   # e.g. someone pushed the same bytes between state() and the clone
        out = O.handler(self.act())[3]()
        self.assertFalse(any("commit" in c or "push" in c for c in self.runs)); self.assertIn("no change", out)
    def test_missing_staging_waits(self):
        os.remove(f"{self.st}/receipts.md")
        self.assertTrue(O.handler(self.act())[1]().startswith("stale:"))
        self.assertTrue(O.state_error_skips("gh.handoff.files"))
    def test_refuses_non_handoff_repo_bad_day_or_empty_file(self):
        for bad in (self.act(repo="xindong/mivo-canvas-plugin"), self.act(day="2026/10/30"), self.act(day="../x")):
            with self.assertRaises(RuntimeError): O.handler(bad)[2]()
        open(f"{self.st}/feedback.md", "w").write("")
        with self.assertRaises(RuntimeError): O.handler(self.act())[2]()

class DailyEnqueues(unittest.TestCase):
    def test_one_action_per_route_for_report_day(self):
        acts = D.handoff_files_actions("2026-10-30", {"routes": [{"repo": REPO}, {"repo": "zhongxingtian-ai/handover-mivo"}]})
        self.assertEqual([a["key"] for a in acts], [f"handoff-files:{REPO}:2026-10-30", "handoff-files:zhongxingtian-ai/handover-mivo:2026-10-30"])
        self.assertTrue(all(a["type"] == "gh.handoff.files" and a["day"] == "2026-10-30" and "files" not in a for a in acts))

class Acceptance138(unittest.TestCase):
    """ops acceptance 10-30 (#138 FAIL items) + PM allowlists: one test per fix."""
    DAY = "2026-10-30"; BASEP = "daily/2026/10/2026-10-30"
    def setUp(self):
        self.d = tempfile.mkdtemp(); O.QA = self.d; self.g = O.gh = Gh(); self.runs = []; self.clone = {}
        self.st = f"{self.d}/handoff/staging/aj0928-handoff/{self.BASEP}"; os.makedirs(self.st)
        for n in ("feedback.md", "receipts.md"): open(f"{self.st}/{n}", "w").write(f"# {n}\n内容\n")
        def fake_run(cmd, **kw):
            self.runs.append(cmd)
            if cmd[:3] == ["gh", "repo", "clone"]:
                for rel, t in {**self.g.remote, **self.clone}.items():
                    os.makedirs(os.path.dirname(os.path.join(cmd[4], rel)), exist_ok=True); open(os.path.join(cmd[4], rel), "w").write(t)
                os.makedirs(cmd[4], exist_ok=True)
            if "diff" in cmd and "--quiet" in cmd: raise RuntimeError("git rc=1: ")
            return ""
        O.run = fake_run
    def act(self, **kw): return {"key": f"handoff-files:{REPO}:{self.DAY}", "type": "gh.handoff.files", "repo": REPO, "day": self.DAY, **kw}
    def owned(self, text, key=None): return O.handoff_owned_text(text, REPO, f"{self.BASEP}/x", key or self.act()["key"])
    # 1. staging read guard: ../ and symlinks resolve outside staging -> refused
    def test_staging_guard_rejects_symlink_out(self):
        secret = f"{self.d}/outside.txt"; open(secret, "w").write("not staged\n")
        os.remove(f"{self.st}/receipts.md"); os.symlink(secret, f"{self.st}/receipts.md")
        with self.assertRaises(RuntimeError) as e: O.handler(self.act())[2]()
        self.assertIn("outside", str(e.exception))
    def test_staging_guard_rejects_symlinked_dir_and_dotdot(self):
        shutil.rmtree(f"{self.d}/handoff/staging/aj0928-handoff"); os.makedirs(f"{self.d}/elsewhere/{self.BASEP}")
        for n in ("feedback.md", "receipts.md"): open(f"{self.d}/elsewhere/{self.BASEP}/{n}", "w").write("x\n")
        os.symlink(f"{self.d}/elsewhere", f"{self.d}/handoff/staging/aj0928-handoff")
        with self.assertRaises(RuntimeError): O.handler(self.act())[2]()
        with self.assertRaises(RuntimeError): O.staged_path(f"{self.d}/handoff/staging/../elsewhere/{self.BASEP}/feedback.md")
    # 2. ownership marker: last line, matching key, unforgeable (HMAC with a local key), rechecked in the clone before the write
    def test_marker_must_be_last_line_with_key_and_valid_mac(self):
        body = "旧回执\n"; good = self.owned(body)
        self.assertTrue(O.handoff_owned(good, REPO, f"{self.BASEP}/x", self.act()["key"]))
        self.assertFalse(O.handoff_owned(good + "追加一行\n", REPO, f"{self.BASEP}/x", self.act()["key"]))      # not last line
        self.assertFalse(O.handoff_owned(good, REPO, f"{self.BASEP}/x", "handoff-files:other:2026-10-30"))     # other key
        forged = body + "\n" + good.strip().splitlines()[-1].rsplit("mac=", 1)[0] + "mac=" + "0" * 64 + " -->\n"
        self.assertFalse(O.handoff_owned(forged, REPO, f"{self.BASEP}/x", self.act()["key"]))                  # forged mac
        quoted = "引用：" + good.strip().splitlines()[-1] + "\n别的内容\n"
        self.assertFalse(O.handoff_owned(quoted, REPO, f"{self.BASEP}/x", self.act()["key"]))                  # marker quoted mid-file
    def test_foreign_remote_file_is_never_overwritten(self):
        self.g.remote[f"{self.BASEP}/receipts.md"] = "别人写的回执\n"
        self.assertTrue(O.handler(self.act())[1]().startswith("stale:"))
    def test_ownership_rechecked_in_clone_before_write(self):
        # API view said ours/absent, but the clone (main moved) has a foreign file: refuse, no commit, no push
        self.clone[f"{self.BASEP}/receipts.md"] = "clone 里别人刚写的\n"
        with self.assertRaises(RuntimeError): O.handler(self.act())[3]()
        self.assertFalse(any("commit" in c or "push" in c for c in self.runs))
    # 3. dedupe by content, not key
    def test_same_content_other_key_is_noop(self):
        for n in ("feedback.md", "receipts.md"):
            self.g.remote[f"{self.BASEP}/{n}"] = O.handoff_owned_text(open(f"{self.st}/{n}").read(), REPO, f"{self.BASEP}/{n}", "handoff-files:old-key")
        self.assertEqual(O.handler(self.act(key="handoff-files:new-key"))[1](), "done")
        self.assertEqual(O.handler(self.act(key="handoff-files:new-key"))[3](), "no change (remote identical)")
        self.assertFalse(any("push" in c for c in self.runs))
    # 4a. handoff allowlist (issues + files): only the two handoff repos; the main repo always refused
    def test_handoff_allowlist(self):
        for bad in ("xindong/mivo-canvas-plugin", "PraiseZhu/pr-qa-agent-deploy", "PraiseZhu/panoptes"):
            with self.assertRaises(RuntimeError): O.check_repo("handoff", bad)
            with self.assertRaises(RuntimeError): O.handler({**D.handoff_issue_action("2026-10-29", IT, F), "repo": bad})[2]()
            with self.assertRaises(RuntimeError): O.handler({**D.handoff_issue_action("2026-10-29", IT, F), "repo": bad})[3]()
        for ok in ("aj0928/aj0928-handoff", "zhongxingtian-ai/handover-mivo"): O.check_repo("handoff", ok)
    # 4b. dispatch allowlist: infra issues only PraiseZhu/pr-qa-agent-deploy; main repo / handoff repos refused at outbound too
    def test_dispatch_allowlist(self):
        O.check_repo("dispatch", "PraiseZhu/pr-qa-agent-deploy")
        for bad in ("xindong/mivo-canvas-plugin", REPO, "zhongxingtian-ai/handover-mivo"):
            with self.assertRaises(RuntimeError): O.check_repo("dispatch", bad)
            h = O.handler({"key": "infra-issue:x", "type": "gh.infra.issue", "repo": bad, "issue": 1, "comment": "c"})
            with self.assertRaises(RuntimeError): h[1]()
        with self.assertRaises(RuntimeError): O.check_repo("handoff", O.REPO)

class FinalRules138(Acceptance138):
    """PM final rules 10-30: marker is an invisible HTML comment with no secret / local path; every skip of a non-owned
    remote file writes one log line with repo and path."""
    def test_marker_is_html_comment_without_paths_or_key_material(self):
        text = self.owned("回执\n")
        last = text.rstrip("\n").splitlines()[-1]
        self.assertRegex(last, r"^<!--[^>]*-->$")                                   # HTML comment: not rendered
        self.assertTrue(last.startswith("<!-- ") and last.endswith(" -->") and "--" not in last[4:-3])
        key_material = open(f"{O.BASE}/outbound/handoff-files.key").read().strip()
        for bad in ("/workspace", O.BASE, os.environ.get("PI_STATE") or "/workspace/mivo-qa/pi", self.d, key_material, "handoff-files.key"):
            self.assertNotIn(bad, text, bad)
        self.assertEqual(text[:-len(last) - 1].rstrip("\n") + "\n", "回执\n")    # body untouched above the marker
    def test_every_non_owned_skip_logs_repo_and_path(self):
        logs = []; orig = O.log; O.log = lambda *a: logs.append(" ".join(map(str, a)))
        try:
            self.g.remote[f"{self.BASEP}/receipts.md"] = "别人写的回执\n"
            self.assertTrue(O.handler(self.act())[1]().startswith("stale:"))
            O.handler(self.act())[1]()
            self.clone[f"{self.BASEP}/feedback.md"] = "clone 里别人写的\n"; self.g.remote.clear()
            with self.assertRaises(RuntimeError): O.handler(self.act())[3]()
        finally: O.log = orig
        hits = [l for l in logs if "not ours" in l]
        self.assertEqual(len(hits), 3, logs)                                        # one line per skip, every time
        self.assertIn(f"repo={REPO} path={self.BASEP}/receipts.md", hits[0]); self.assertIn(f"path={self.BASEP}/feedback.md", hits[2])

if __name__ == "__main__":
    unittest.main()
