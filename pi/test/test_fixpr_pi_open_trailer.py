#!/usr/bin/env python3
"""11-03 B: outbound opens a Pi fix PR with a commit that had only the title, no Pi-Open trailer. For OWNER_WINDOW_H (6 h) prfix
--revise then saw the PR's own first commit as someone else's and skipped it (prfix-2026-11-02.log:29,39: #1563 d39839610,
#1559 cb9fac0a7). Now the opening commit and the docs/pr rename commit carry the trailer prfix fix_one writes.
Scratch state only, no network. Run: python3 test/test_fixpr_pi_open_trailer.py"""
import datetime as dt, os, subprocess, sys, tempfile, types, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); BIN = os.path.join(os.path.dirname(HERE), "bin"); sys.path.insert(0, BIN)
sys.path.insert(0, HERE); import isolation  # noqa: E402
isolation.sandbox_state()
sys.modules.setdefault("merger", types.SimpleNamespace(FIELDS="number", blockers=lambda p: []))
import outbound as O  # noqa: E402
import lib as L  # noqa: E402
KEY = "fixpr:1518:mq1-a4b3e0d3594c99940e19"

def commit_msgs(cmds):
    """the -m values of every `git commit` the handler ran, joined the way git stores them"""
    out = []
    for c in cmds:
        if "commit" in c:
            out.append("\n\n".join(c[i + 1] for i, x in enumerate(c) if x == "-m"))
    return out

def recent(msg):   # a GitHub pulls/N/commits item from 1 minute ago
    when = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)).isoformat()
    return {"sha": "d39839610abc", "commit": {"message": msg, "committer": {"date": when}}}

class T(unittest.TestCase):
    def test_opening_commit_is_pi_owned(self):
        patch = os.path.join(tempfile.mkdtemp(), "x.patch"); 
        with open(patch, "w") as f: f.write("diff --git a/a b/a\n")
        a = {"key": KEY, "patch": patch, "title": "fix(ci): 连续失败判定只查询最近 100 次运行 (QA #1518)", "body_must_contain": "QA: #1518"}
        cmds = []
        with mock.patch.object(O, "run", side_effect=lambda c, *k, **kw: cmds.append(list(c)) or ""), \
             mock.patch.object(O, "apply_fix_patch", return_value="main"), \
             mock.patch.object(O, "fixpr_gate_reason", return_value=None), \
             mock.patch.object(O, "gh", return_value="https://github.com/xindong/mivo-canvas-plugin/pull/1563\n"), \
             mock.patch.object(O, "fixpr_docs_rename", return_value="ok"):
            O.h_fixpr(a)[3]()
        msgs = commit_msgs(cmds)
        self.assertEqual(len(msgs), 1, cmds)
        self.assertTrue(L.has_pi_trailer(msgs[0]), msgs[0])
        self.assertEqual(L.foreign_recent_commits([recent(msgs[0])]), [])
        self.assertEqual(L.foreign_recent_commits([recent(a["title"])]), ["d39839610"])   # the old title-only commit was foreign

    def test_docs_rename_commit_is_pi_owned(self):
        d = tempfile.mkdtemp(); g = ["git", "-C", d]
        subprocess.run(g + ["init", "-q", "-b", "br"], check=True)
        for k in ("user.name", "user.email"): subprocess.run(g + ["config", k, "t@t"], check=True)
        os.makedirs(f"{d}/docs/pr/1560"); open(f"{d}/docs/pr/1560/qa.md", "w").write("---\npr: 1560\n---\n# PR #1560\n")
        subprocess.run(g + ["add", "-A"], check=True); subprocess.run(g + ["commit", "-q", "-m", "seed"], check=True)
        O.fixpr_docs_rename(d, "br", {"key": KEY, "docs_dir_predicted": "docs/pr/1560"}, 1563, push=False)
        msg = subprocess.run(g + ["log", "-1", "--format=%B"], capture_output=True, text=True, check=True).stdout
        self.assertTrue(L.has_pi_trailer(msg), msg)
        O.fixpr_docs_rename(d, "br", {"key": "fixpr-docs:1563", "from": KEY, "docs_dir_predicted": "docs/pr/1563"}, 1564, push=False)
        msg = subprocess.run(g + ["log", "-1", "--format=%B"], capture_output=True, text=True, check=True).stdout
        self.assertTrue(L.has_pi_trailer(msg) and f"Pi-Open: {KEY}" in msg, msg)   # gh.pr.docs-fix retry: trailer of the PR it fixes

class Superseded(unittest.TestCase):
    """11-03 B: round 2 of a finding (body_must_contain "<fp> r2") while the round-1 fix PR with <fp> is still open -> outbound
    opens no second PR and the ledger gets a real "superseded" status (TERMINAL), not a stale row re-read every run."""
    A = {"key": "fixpr:1519:mq1-0fea:r2", "body_must_contain": "mq1-0fea r2", "fingerprint": "mq1-0fea", "patch": "/nope"}
    def gh(self, open_prs):
        return lambda args, js=True: open_prs if "open" in args else []
    def test_open_earlier_fix_pr_supersedes(self):
        with mock.patch.object(O, "gh", self.gh([{"number": 1621, "body": "QA: #1519\nmq1-0fea\n"}])):
            self.assertEqual(O.h_fixpr(self.A)[1](), "superseded:open fix PR #1621 already carries mq1-0fea")
        with mock.patch.object(O, "gh", self.gh([])):
            self.assertEqual(O.h_fixpr(self.A)[1](), "pending")
        self.assertIn("superseded", O.TERMINAL)
    def test_supersedes_only_named_pr(self):   # 11-04 「取代 #N」: the closed fix PR N already carrying the marker is not "done"
        a = dict(self.A, supersedes=1678)
        old = [{"number": 1678, "body": "mq1-0fea r2"}]
        with mock.patch.object(O, "gh", lambda args, js=True: old):
            self.assertEqual(O.h_fixpr(self.A)[1](), "done")                      # no supersedes: still deduped
            self.assertEqual(O.h_fixpr(a)[1](), "pending")
        with mock.patch.object(O, "gh", lambda args, js=True: old + [{"number": 1700, "body": "mq1-0fea r2"}]):
            self.assertEqual(O.h_fixpr(a)[1](), "done")                           # another PR with the marker still dedupes
        with mock.patch.object(O, "gh", self.gh([{"number": 1621, "body": "mq1-0fea"}])):
            self.assertEqual(O.h_fixpr(dict(self.A, supersedes=1678))[1](), "superseded:open fix PR #1621 already carries mq1-0fea")
    def test_off_flag(self):
        os.makedirs(os.path.dirname(L.B_OFF), exist_ok=True); open(L.B_OFF, "w").close()
        try:
            with mock.patch.object(O, "gh", self.gh([{"number": 1621, "body": "mq1-0fea"}])):
                self.assertEqual(O.h_fixpr(self.A)[1](), "pending")
        finally: os.remove(L.B_OFF)

if __name__ == "__main__": unittest.main()
