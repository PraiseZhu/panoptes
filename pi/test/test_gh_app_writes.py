#!/usr/bin/env python3
"""11-04 gh-app-1104 P2: PraiseZhu/panoptes WRITES (gh pr create/merge/comment, branch delete, infra_outbound clone + git push) use the
Praise QA App while $PI_STATE/gh-app-enabled AND gh-app-writes-enabled exist. Removing gh-app-writes-enabled puts writes back on the PAT
(reads stay on the App); removing gh-app-enabled puts everything back on the PAT. janitor also lists closed infra PRs authored by the
App. ghtoken, gh and git are mocked: no network, no real key or token."""
import io, os, sys, types, unittest
from contextlib import redirect_stdout
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin")); sys.path.insert(0, HERE)
import isolation  # noqa: E402
isolation.sandbox_state()
import lib  # noqa: E402

FAKE = "ghs_" + "W" * 36
PAN = "PraiseZhu/panoptes"


class Base(unittest.TestCase):
    def setUp(self):
        self.flags = (lib.gh_app_flag(), lib.gh_app_writes_flag())
        for f in self.flags:
            if os.path.exists(f): os.remove(f)
        self.tok = mock.patch.dict(sys.modules, {"ghtoken": types.SimpleNamespace(token=lambda repo: FAKE, invalidate=lambda repo: None, mask=str)}); self.tok.start()
    def tearDown(self):
        self.tok.stop()
        for f in self.flags:
            if os.path.exists(f): os.remove(f)
    def on(self, reads=True, writes=True):
        if reads: open(self.flags[0], "w").close()
        if writes: open(self.flags[1], "w").close()
    def env_of(self, args):
        seen = []
        with mock.patch.object(lib.subprocess, "run", lambda cmd, **kw: seen.append(kw.get("env")) or types.SimpleNamespace(returncode=0, stdout="{}", stderr="")), \
             redirect_stdout(io.StringIO()):
            lib.gh(args)
        return seen[-1]


WRITES = (["pr", "create", "-R", PAN, "--base", "main", "--head", "fix/infra-1", "--title", "t", "--body", "b"],
          ["pr", "merge", "1", "-R", PAN, "--squash"], ["api", "-X", "DELETE", f"repos/{PAN}/git/refs/heads/fix/infra-1"],
          ["api", "-X", "POST", f"repos/{PAN}/issues/1/comments", "-f", "body=x"])


class Writes(Base):
    def test_both_flags_panoptes_writes_use_app(self):
        self.on()
        for a in WRITES: self.assertEqual((self.env_of(a) or {}).get("GH_TOKEN"), FAKE, a)

    def test_removing_writes_flag_puts_writes_back_on_pat_reads_stay_app(self):
        self.on(); os.remove(self.flags[1])
        for a in WRITES: self.assertIsNone(self.env_of(a), a)
        self.assertEqual(self.env_of(["pr", "list", "-R", PAN])["GH_TOKEN"], FAKE)

    def test_removing_app_flag_puts_everything_back_on_pat(self):
        self.on(); os.remove(self.flags[0])
        for a in WRITES + (["pr", "list", "-R", PAN],): self.assertIsNone(self.env_of(a), a)

    def test_product_writes_stay_on_pat_deploy_repo_moves(self):
        self.on()   # product writes need gh-app-product-writes-enabled (task B); pr-qa-agent-deploy is in installation 164262283 now
        self.assertIsNone(self.env_of(["pr", "create", "-R", "xindong/mivo-canvas-plugin", "--title", "t"]))
        self.assertIsNotNone(self.env_of(["issue", "create", "-R", "PraiseZhu/pr-qa-agent-deploy", "--title", "t"]))

    def test_git_push_env(self):
        self.assertEqual(lib.git_push_env(PAN), ([], None))
        self.on()
        c, env = lib.git_push_env(PAN)
        self.assertEqual(env["GH_TOKEN"], FAKE); self.assertIn("credential.helper=!gh auth git-credential", c)
        self.assertEqual(lib.git_push_env("xindong/mivo-canvas-plugin"), ([], None))


class InfraOutbound(Base):
    def do_pr(self):
        import infra_outbound as IO
        patch = os.path.join(lib.PI_STATE, "p.patch"); open(patch, "w").write("x"); body = os.path.join(lib.PI_STATE, "b.md"); open(body, "w").write("b")
        a = {"key": "k", "repo": PAN, "marker": "Infra-Sig: inf1-abcdef", "patch": patch, "branch": "fix/infra-abcdef12", "body_file": body, "title": "fix: t"}
        runs, gits = [], []
        def fake_run(cmd, **kw): runs.append((cmd, kw.get("env"))); return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        def fake_git(d, *args, env=None): gits.append((args, env)); return ""
        gh = lambda args, js=True: {"viewerPermission": "WRITE", "defaultBranchRef": {"name": "main"}} if args[:2] == ["repo", "view"] else ([] if js else "https://github.com/x/y/pull/9\n")
        with mock.patch.object(IO.subprocess, "run", fake_run), mock.patch.object(IO, "_git", fake_git):
            IO.h_pr(a, gh)[3]()
        clone = next(e for c, e in runs if c[:3] == ["gh", "repo", "clone"])
        push = next((args, e) for args, e in gits if "push" in args)
        return clone, push

    def test_clone_and_push_use_app_when_write_flags_on(self):
        self.on()
        clone, (args, env) = self.do_pr()
        self.assertEqual(clone["GH_TOKEN"], FAKE); self.assertEqual(env["GH_TOKEN"], FAKE)
        self.assertIn("credential.helper=!gh auth git-credential", args); self.assertNotIn("--force", args)

    def test_clone_and_push_on_pat_without_flag(self):
        clone, (args, env) = self.do_pr()
        self.assertIsNone(clone); self.assertIsNone(env); self.assertNotIn("credential.helper=", args)


class Janitor(Base):
    def test_lists_closed_infra_prs_authored_by_the_app(self):
        import janitor as J
        self.on(); calls = []
        def gh(args, js=True): calls.append(args); return []
        self_ = types.SimpleNamespace(c={"remote_days": 3}, r={"remote_kept": [], "remote_deleted": []})
        with mock.patch.object(J, "gh", gh), mock.patch.object(J, "REMOTE", {"panoptes": J.REMOTE["panoptes"]}):
            try: J.Run.remote(self_)
            except Exception: pass
        searches = [a[a.index("--search") + 1] for a in calls if "--search" in a]   # task B: explicit identities, no @me
        self.assertEqual([q.split("author:")[1] for q in searches], ["PraiseZhu", lib.app_author()])


if __name__ == "__main__": unittest.main()
