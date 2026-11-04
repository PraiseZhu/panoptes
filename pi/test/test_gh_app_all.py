#!/usr/bin/env python3
"""11-04 task B: every caller on the Praise QA App behind its own flag (PAT fallback), explicit identities instead of @me / `api user`,
dedupe over PraiseZhu + praise-qa[bot] PRs, the product red lines (no merge, no PR body edit) on every identity, every PraiseZhu/* repo
in installation 164262283. ghtoken, gh and git are mocked; no key, no token."""
import io, os, re, sys, types, unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); BIN = os.path.join(os.path.dirname(HERE), "bin")
sys.path.insert(0, BIN); sys.path.insert(0, HERE)
import isolation  # noqa: E402
isolation.sandbox_state()
import lib  # noqa: E402

FAKE = "ghs_" + "Q" * 36
PROD, PAN, DEPLOY = "xindong/mivo-canvas-plugin", "PraiseZhu/panoptes", "PraiseZhu/pr-qa-agent-deploy"
OLD_PAT_PR = {"number": 10, "body": "QA: #1 fp-aaa\n<!-- pi-line fixpr:1:fp-aaa -->", "author": {"login": "PraiseZhu"}}
NEW_BOT_PR = {"number": 20, "body": "QA: #2 fp-bbb\n<!-- pi-line fixpr:2:fp-bbb -->", "author": {"login": "app/praise-qa", "is_bot": True}}


def flagfile(name): return f"{lib.PI_STATE}/{name}"
ALL_FLAGS = ["gh-app-enabled", "gh-app-product-enabled", "gh-app-writes-enabled", "gh-app-product-writes-enabled",
             "gh-app-merger-enabled", "gh-app-mergewatch-enabled", "gh-app-nightly-enabled", "gh-app-ledgers-enabled"]


class Base(unittest.TestCase):
    def setUp(self):
        self.clear()
        self.tok = mock.patch.dict(sys.modules, {"ghtoken": types.SimpleNamespace(token=lambda repo: FAKE, invalidate=lambda repo: None, mask=str)})
        self.tok.start(); lib._COMPONENT[0] = None
    def tearDown(self): self.tok.stop(); self.clear(); lib._COMPONENT[0] = None
    def clear(self):
        for f in ALL_FLAGS + ["gh-ratelimit.json", "gh-ratelimit-praise-qa.json"]:
            if os.path.exists(flagfile(f)): os.remove(flagfile(f))
    def on(self, *names):
        for n in ("gh-app-enabled",) + names: open(flagfile(n), "w").close()
    def env_of(self, args):
        seen = []
        with mock.patch.object(lib.subprocess, "run", lambda cmd, **kw: seen.append(kw.get("env")) or types.SimpleNamespace(returncode=0, stdout="[]", stderr="")), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            lib.gh(args)
        return seen[-1]
    def app(self, args): return (self.env_of(args) or {}).get("GH_TOKEN") == FAKE


class Dedupe(Base):
    def fake_gh(self, prs):
        calls = []
        def gh(args, js=True):
            calls.append(args); q = args[args.index("--search") + 1] if "--search" in args else ""
            au = q.split("author:")[1] if "author:" in q else args[args.index("--author") + 1]
            assert au != "@me", args
            want = re.findall(r'"([^"]+)" in:body', q)
            return [p for p in prs if lib.is_self(p["author"]) and p["author"]["login"].replace("praise-qa[bot]", "app/praise-qa") == au
                    and all(w in p["body"] for w in want)]
        return gh, calls

    def test_old_pat_pr_and_new_bot_pr_both_dedupe(self):
        gh, calls = self.fake_gh([OLD_PAT_PR, NEW_BOT_PR])
        self.assertEqual([p["number"] for p in lib.self_pr_list(PROD, "all", "number,body", search='"fp-aaa" in:body', run=gh)], [10])
        self.assertEqual([p["number"] for p in lib.self_pr_list(PROD, "all", "number,body", search='"fp-bbb" in:body', run=gh)], [20])
        self.assertEqual(sorted(p["number"] for p in lib.self_pr_list(PROD, "open", "number", run=gh)), [10, 20])
        self.assertTrue(all("@me" not in " ".join(c) for c in calls))

    def test_outbound_fixpr_never_opens_a_duplicate(self):
        import outbound as O
        for pr, must in ((OLD_PAT_PR, "fp-aaa"), (NEW_BOT_PR, "fp-bbb")):
            gh, _ = self.fake_gh([pr])
            with mock.patch.object(O, "gh", gh):
                _, state, _, _ = O.h_fixpr({"key": f"fixpr:x:{must}", "patch": "/nonexistent", "body_must_contain": must})
                self.assertEqual(state(), "done", pr["author"])

    def test_is_self(self):
        for l in ("PraiseZhu", "praise-qa[bot]", "app/praise-qa", {"login": "app/praise-qa"}): self.assertTrue(lib.is_self(l), l)
        for l in ("kirozeng", "someone[bot]", None, {}): self.assertFalse(lib.is_self(l), l)

    def test_push_author_check_accepts_the_bot(self):
        import outbound as O
        p = {"state": "OPEN", "author": {"login": "app/praise-qa"}, "headRefName": "qa-fix/x", "headRefOid": "h" * 40, "isDraft": True,
             "body": "<!-- pi-line fixpr:1:fp -->"}
        with mock.patch.object(O, "gh", lambda a, js=True: p if a[:2] == ["pr", "view"] else []), \
             mock.patch.object(O, "ownership_block", lambda *a: None), mock.patch.object(O, "flatten_pages", lambda x: x):
            _, state, _, _ = O.h_push_fix({"key": "rev:1", "pr": 1, "branch": "qa-fix/x", "head": "h" * 40, "patch": "/p"})
            self.assertEqual(state(), "pending")


class RedLines(Base):
    def test_product_merge_and_body_edit_refused_on_every_identity(self):
        for flags in ((), ("gh-app-product-enabled", "gh-app-product-writes-enabled", "gh-app-merger-enabled")):
            self.clear(); self.on(*flags) if flags else None
            for a in (["pr", "merge", "1", "-R", PROD, "--squash"], ["api", "-X", "PUT", f"repos/{PROD}/pulls/1/merge"],
                      ["pr", "edit", "1", "-R", PROD, "--body", "x"], ["api", "-X", "PATCH", f"repos/{PROD}/pulls/1", "-f", "body=x"]):
                with mock.patch.object(lib.subprocess, "run", lambda *a, **k: self.fail(f"sent {a}")):
                    with self.assertRaises(lib.RedLine, msg=a): lib.gh(a)
        # panoptes merges (infra) are not a product red line
        self.assertIsNone(lib.gh_red_line(["pr", "merge", "1", "-R", PAN, "--squash"]))

    def test_outbound_product_merge_is_stale_never_sent(self):
        import outbound as O
        with mock.patch.object(O, "gh", lambda *a, **k: self.fail("gh called")):
            _, state, _, _ = O.h_merge({"key": "merge:1:" + "a" * 40, "pr": 1})
            self.assertTrue(state().startswith("stale:red line"))


class Flags(Base):
    def test_product_writes_flag(self):
        w = ["pr", "create", "-R", PROD, "--draft", "--title", "t"]
        self.on("gh-app-product-enabled"); self.assertFalse(self.app(w))
        self.on("gh-app-product-writes-enabled"); self.assertTrue(self.app(w))
        os.remove(flagfile("gh-app-product-writes-enabled")); self.assertFalse(self.app(w), "rollback -> PAT")

    def test_components_each_behind_own_flag(self):
        r = ["api", f"repos/{PROD}/pulls/1"]
        for c in lib.GH_APP_COMPONENTS:
            self.clear(); lib._COMPONENT[0] = c; self.on()
            self.assertFalse(self.app(r), c)
            self.on(f"gh-app-{c}-enabled"); self.assertTrue(self.app(r), c)
            os.remove(flagfile("gh-app-enabled")); self.assertFalse(self.app(r), f"{c}: master flag off -> PAT")

    def test_node_env_only_with_flag(self):
        self.assertEqual(lib.gh_component_node_env("ledgers"), {})
        self.on("gh-app-ledgers-enabled")
        self.assertEqual(lib.gh_component_node_env("ledgers"), {"PRAISE_QA_GH_TOKEN": FAKE, "PRAISE_QA_GH_REPOS": PROD})

    def test_every_praisezhu_repo_in_installation(self):
        sys.modules.pop("ghtoken", None)
        import importlib; G = importlib.import_module("ghtoken")
        for r in (PAN, DEPLOY, "PraiseZhu/anything"): self.assertEqual(G.installation(r), 164262283, r)
        self.assertEqual(G.installation(PROD), 178486896)
        with self.assertRaises(G.NoInstallation): G.installation("someone/else")
        self.on("gh-app-writes-enabled"); self.tok.start()
        self.assertTrue(self.app(["issue", "create", "-R", DEPLOY, "--title", "t"]))

    def test_outbound_run_routes_product_push(self):
        import outbound as O
        self.on("gh-app-product-enabled", "gh-app-product-writes-enabled")
        def fake(cmd, **kw):
            return types.SimpleNamespace(returncode=0, stdout="https://github.com/xindong/mivo-canvas-plugin.git\n" if "get-url" in cmd else "", stderr="")
        with mock.patch.object(O.subprocess, "run", fake):
            cmd, env = O._run_route(["git", "-C", "/d", "push", "origin", "HEAD:refs/heads/qa-fix/x"])
        self.assertIn("credential.helper=!gh auth git-credential", cmd); self.assertNotIn("--force", cmd); self.assertEqual(env["GH_TOKEN"], FAKE)
        os.remove(flagfile("gh-app-product-writes-enabled"))
        with mock.patch.object(O.subprocess, "run", fake):
            self.assertEqual(O._run_route(["git", "-C", "/d", "push", "origin", "x"])[1], None)


class NoImplicitIdentity(unittest.TestCase):
    def test_no_at_me_or_api_user_in_code(self):
        root = os.path.dirname(os.path.dirname(BIN)); bad = []
        for d in ("pi/bin", "pi/ledger", "report", "argus/src", "scripts", "run-nightly.sh"):
            p = os.path.join(root, d)
            files = [p] if os.path.isfile(p) else [os.path.join(a, f) for a, _, fs in os.walk(p) for f in fs if f.endswith((".py", ".mjs", ".sh", ".js"))]
            for f in files:
                for i, line in enumerate(open(f, errors="replace"), 1):
                    code = line.split("#")[0] if f.endswith((".py", ".sh")) else line.split("//")[0]
                    if re.search(r"""author:@me|["']--author["'],\s*["']@me["']|["']api["'],\s*(["']-i["'],\s*)?["']user["']""", code): bad.append(f"{f}:{i}")
        self.assertEqual(bad, [])


if __name__ == "__main__": unittest.main()
