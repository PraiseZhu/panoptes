#!/usr/bin/env python3
"""11-04 (deploy-211 evidence): merger's quota check on the identity that serves the repo; graphql / issues-API red lines; graphql
target repo from variables; the box-global gh wrapper and git credential helper (flag gh-app-wrapper-enabled). Mocks only."""
import io, json, os, subprocess, sys, tempfile, types, unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); BIN = os.path.join(os.path.dirname(HERE), "bin"); ROOT = os.path.dirname(os.path.dirname(BIN))
sys.path.insert(0, BIN); sys.path.insert(0, HERE)
import isolation  # noqa: E402
isolation.sandbox_state()
import lib  # noqa: E402

PROD, PAN = "xindong/mivo-canvas-plugin", "PraiseZhu/panoptes"
FAKE = {PROD: "ghs_" + "P" * 36, PAN: "ghs_" + "O" * 36}
def F(n): return f"{lib.PI_STATE}/{n}"
FLAGS = ("gh-app-enabled", "gh-app-merger-enabled", "gh-app-wrapper-enabled", "gh-ratelimit.json", "gh-ratelimit-praise-qa.json")


class Base(unittest.TestCase):
    def setUp(self):
        self.clear(); self.minted = []
        def token(repo):
            self.minted.append(repo)
            if getattr(self, "mint_fail", False): raise RuntimeError("HTTP 401 Bad credentials " + FAKE[PAN])
            return FAKE.get(repo, FAKE[PAN] if repo.startswith("PraiseZhu/") else FAKE[PROD])
        import ghtoken as real
        self.tok = mock.patch.dict(sys.modules, {"ghtoken": types.SimpleNamespace(token=token, invalidate=lambda r: None, mask=real.mask,
                                   installation=real.installation, NoInstallation=real.NoInstallation)}); self.tok.start()
        lib._COMPONENT[0] = None
    def tearDown(self): self.tok.stop(); self.clear(); lib._COMPONENT[0] = None
    def clear(self):
        for n in FLAGS:
            if os.path.exists(F(n)): os.remove(F(n))
    def on(self, *ns):
        for n in ("gh-app-enabled",) + ns: open(F(n), "w").close()


class MergerQuota(Base):
    def run_gh(self, args, **kw):
        seen = []
        with mock.patch.object(lib.subprocess, "run", lambda cmd, **k: seen.append(k.get("env")) or types.SimpleNamespace(returncode=0, stdout='{"resources":{}}', stderr="")):
            lib.gh(args, **kw)
        return (seen[-1] or {}).get("GH_TOKEN")

    def test_rate_limit_checks_the_app_pool_while_merger_flag_on(self):
        lib.gh_component("merger"); self.on("gh-app-merger-enabled")
        self.assertEqual(self.run_gh(["api", "rate_limit"], repo=PROD), FAKE[PROD])
        self.assertIsNone(self.run_gh(["api", "rate_limit"]), "no repo -> the PAT (the old bug)")
        os.remove(F("gh-app-merger-enabled")); self.assertIsNone(self.run_gh(["api", "rate_limit"], repo=PROD), "rollback -> PAT")

    def test_exhausted_pat_does_not_block_the_app(self):
        lib.gh_component("merger"); self.on("gh-app-merger-enabled")
        lib.write_atomic(lib.GH_RL_FILE, json.dumps({"since": lib._epoch(), "until": lib._epoch() + 900}))
        self.assertEqual(self.run_gh(["api", "rate_limit"], repo=PROD), FAKE[PROD])

    def test_merger_source_passes_the_repo(self):
        src = open(os.path.join(BIN, "merger.py")).read()
        self.assertIn('gh(["api", "rate_limit"], repo=REPO)', src); self.assertNotIn('api("rate_limit")', src)

    def test_rate_limit_reset_is_read_with_the_identity_that_hit_it(self):
        seen = []
        with mock.patch.object(lib.subprocess, "run", lambda cmd, **k: seen.append(k.get("env")) or types.SimpleNamespace(returncode=0, stdout="{}", stderr="")):
            lib._gh_rl_reset("API rate limit exceeded", {"GH_TOKEN": "x"})
        self.assertEqual(seen[-1], {"GH_TOKEN": "x"})


class RedLines(unittest.TestCase):
    M = "mutation($id:ID!){ mergePullRequest(input:{pullRequestId:$id}){ clientMutationId } }"
    def refused(self, a, **kw): return lib.gh_red_line(a, **kw)

    def test_graphql_merge_all_spellings(self):
        d = tempfile.mkdtemp(); qf = f"{d}/q.graphql"; jf = f"{d}/b.json"
        open(qf, "w").write("# merge it\nmutation {\n  m1: mergePullRequest (input: {pullRequestId: \"X\"}) { clientMutationId }\n}")
        json.dump({"query": "mutation { enablePullRequestAutoMerge(input:{pullRequestId:\"X\"}){clientMutationId} }"}, open(jf, "w"))
        for a in (["api", "graphql", "-f", f"query={self.M}", "-F", "id=X"], ["api", "graphql", f"--raw-field=query={self.M}"],
                  ["api", "graphql", f"-fquery={self.M}"], ["api", "graphql", "-F", f"query=@{qf}"], ["api", "graphql", "--input", jf],
                  ["api", "/graphql", "-f", f"query={self.M}", "-F", "owner=xindong", "-F", "name=mivo-canvas-plugin"]):
            self.assertIn("merging", self.refused(a) or "", a)

    def test_graphql_edit_title_body(self):
        q = 'mutation { updatePullRequest(input:{pullRequestId:"X", title:"t"}){ clientMutationId } }'
        self.assertIn("editing", self.refused(["api", "graphql", "-f", f"query={q}"]))
        qv = "mutation($b:String!){ updatePullRequest(input:{pullRequestId:\"X\", body:$b}){ clientMutationId } }"
        self.assertIn("editing", self.refused(["api", "graphql", "-f", f"query={qv}", "-f", "b=x"]))

    def test_graphql_reads_and_own_repo_allowed(self):
        self.assertIsNone(self.refused(["api", "graphql", "-f", "query=query{ repository(owner:\"xindong\",name:\"m\"){ id } }"]))
        self.assertIsNone(self.refused(["api", "graphql", "-f", f"query={self.M}", "-F", "owner=PraiseZhu", "-F", "name=panoptes"]))

    def test_issues_api_and_issue_edit(self):
        self.assertIn("editing", self.refused(["api", "-X", "PATCH", f"repos/{PROD}/issues/1700", "-f", "title=x"]))
        self.assertIn("editing", self.refused(["api", "--method", "PATCH", f"repos/{PROD}/issues/1700", "--input", "-"]))
        self.assertIn("editing", self.refused(["issue", "edit", "1700", "-R", PROD, "--body", "x"]))
        self.assertIsNone(self.refused(["api", "-X", "PATCH", f"repos/{PROD}/issues/1700", "-f", "state=open"]))
        self.assertIsNone(self.refused(["issue", "edit", "1", "-R", PROD, "--add-label", "x"]))

    def test_unresolvable_merge_fails_closed(self):
        self.assertIn("merging", self.refused(["pr", "merge", "5"]))
        self.assertIsNone(self.refused(["pr", "merge", "5"], repo=PAN))

    def test_graphql_target_repo_from_variables(self):
        self.assertEqual(lib.gh_target_repo(["api", "graphql", "-f", "query=q", "-F", "o=xindong", "-F", "n=mivo-canvas-plugin"]), PROD)

    def test_argus_api_send_red_line(self):
        js = ("import('%s/argus/src/github.mjs').then(m => console.log(JSON.stringify([m.redLine('PUT','repos/xindong/x/pulls/1/merge'),"
              " m.redLine('PATCH','repos/xindong/x/issues/1',{title:'t'}), m.redLine('PATCH','repos/PraiseZhu/d/issues/1',{state:'open'})])))" % ROOT)
        r = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, cwd=os.path.join(ROOT, "argus"))
        v = json.loads(r.stdout.strip().splitlines()[-1]); self.assertTrue(v[0] and v[1]); self.assertIsNone(v[2])


class Wrapper(Base):
    def wrap(self, argv, env=None, cwd_url="", caller=()):
        import gh_wrapper as W
        calls = []
        fake_run = lambda cmd, **k: types.SimpleNamespace(returncode=0 if cwd_url else 1, stdout=cwd_url, stderr="")
        err = io.StringIO()
        with redirect_stderr(err):
            rc = W.main(argv, env={"PATH": "", "PRAISE_QA_REAL_GH": "/real/gh", **(env or {})}, execve=lambda p, a, e: calls.append((p, a, e)), run=fake_run, caller=list(caller))
        self.assertNotIn(FAKE[PROD], err.getvalue()); self.assertNotIn(FAKE[PAN], err.getvalue())
        return rc, (calls[-1] if calls else None), err.getvalue()

    def test_red_line_in_the_wrapper_even_with_flag_off(self):
        for a, url in ((["pr", "merge", "1", "-R", PROD], ""), (["pr", "merge", "1"], "https://github.com/xindong/mivo-canvas-plugin.git"),
                       (["api", "graphql", "-f", f"query={RedLines.M}"], ""), (["pr", "edit", "1", "--title", "x"], "git@github.com:xindong/mivo-canvas-plugin.git")):
            rc, call, err = self.wrap(a, cwd_url=url); self.assertEqual((rc, call), (4, None), a); self.assertIn("red line", err)
        rc, call, _ = self.wrap(["pr", "merge", "1"], cwd_url="https://github.com/PraiseZhu/panoptes.git"); self.assertIsNotNone(call)

    def test_installation_by_repo_and_rollback(self):
        self.on("gh-app-wrapper-enabled")
        _, c, _ = self.wrap(["pr", "list", "-R", PROD], env={"GITHUB_TOKEN": "pat"}); self.assertEqual(c[2]["GH_TOKEN"], FAKE[PROD]); self.assertNotIn("GITHUB_TOKEN", c[2])
        _, c, _ = self.wrap(["issue", "list"], cwd_url="https://github.com/PraiseZhu/pr-qa-agent-deploy"); self.assertEqual(c[2]["GH_TOKEN"], FAKE[PAN])
        self.assertEqual(c[0], "/real/gh"); self.assertEqual(c[1], ["/real/gh", "issue", "list"])
        _, c, _ = self.wrap(["pr", "list", "-R", PROD], env={"GH_TOKEN": "ghs_caller"}); self.assertEqual(c[2]["GH_TOKEN"], "ghs_caller")
        os.remove(F("gh-app-wrapper-enabled"))
        _, c, _ = self.wrap(["pr", "list", "-R", PROD], env={"GITHUB_TOKEN": "pat"}); self.assertNotIn("GH_TOKEN", c[2]); self.assertEqual(c[2]["GITHUB_TOKEN"], "pat")

    def test_unresolved_defaults_to_product_and_logs_repo_and_command_only(self):
        import gh_wrapper as W
        if os.path.exists(W.unresolved_log()): os.remove(W.unresolved_log())
        self.on("gh-app-wrapper-enabled")
        _, c, _ = self.wrap(["search", "prs", "secret-arg"]); self.assertEqual(c[2]["GH_TOKEN"], FAKE[PROD]); self.assertEqual(self.minted[-1], PROD)
        line = json.loads(open(W.unresolved_log()).read().splitlines()[-1])
        self.assertEqual((line["cmd"], line["repo"]), ("gh search prs", None)); self.assertNotIn("secret-arg", json.dumps(line)); self.assertNotIn("ghs_", json.dumps(line))

    def test_allowlisted_caller_gets_the_pat_and_the_real_gh(self):
        import gh_wrapper as W
        self.on("gh-app-wrapper-enabled")
        for caller in (["node", "/home/x/vigil/cindy/bin/cindy-watcher.mjs"], ["bash", "/opt/box/verify.d/60-gh-auth.sh"],
                       ["/usr/bin/python3", "/Users/j/review-pr/scripts/merge-reviewed-pr.py"], ["node", "/srv/app/scripts/ops/org-market-open-company-pr.mjs"]):
            _, c, err = self.wrap(["pr", "list", "-R", PROD], env={"GITHUB_TOKEN": "ghp_pat", "GH_TOKEN": "ghs_old"}, caller=caller)
            self.assertEqual(c[0], "/real/gh", caller); self.assertEqual(c[2].get("GH_TOKEN"), "ghp_pat", caller); self.assertIn("allowlisted caller", err)
        self.assertEqual(json.loads(open(W.allowlist_log()).read().splitlines()[-1])["caller"], "lifeline-org-market-open-company-pr")
        self.assertEqual(len({n for n, _ in W.CALLER_ALLOWLIST}), len(W.CALLER_ALLOWLIST))

    def test_non_listed_caller_gets_the_app(self):
        self.on("gh-app-wrapper-enabled")
        for caller in (["node", "/home/x/other/bin/cindy-watcher-old.mjs"], ["node", "/x/notcindy/bin/cindy-watcher.mjs.bak"], []):
            _, c, err = self.wrap(["pr", "list", "-R", PROD], env={"GITHUB_TOKEN": "ghp_pat"}, caller=caller)
            self.assertEqual(c[2]["GH_TOKEN"], FAKE[PROD], caller); self.assertNotIn("allowlisted", err)

    def test_red_lines_still_block_allowlisted_callers(self):
        for a in (["pr", "merge", "1", "-R", PROD], ["pr", "edit", "1", "--body", "x", "-R", PROD]):
            rc, c, err = self.wrap(a, caller=["node", "/x/mivo/bin/mivo-handoff.mjs"]); self.assertEqual((rc, c), (4, None), a); self.assertIn("red line", err)

    def test_mint_failure_falls_back(self):
        self.on("gh-app-wrapper-enabled"); self.mint_fail = True
        _, c, err = self.wrap(["pr", "list", "-R", PROD], env={"GITHUB_TOKEN": "pat"})
        self.assertNotIn("GH_TOKEN", c[2]); self.assertIn("inherited gh login", err)


class PatChecksBypassTheWrapper(unittest.TestCase):
    def test_real_gh_skips_the_wrapper_on_path(self):
        d = tempfile.mkdtemp(); os.makedirs(f"{d}/local"); os.makedirs(f"{d}/usr")
        os.symlink(os.path.join(BIN, "gh-praise-qa"), f"{d}/local/gh")             # 电脑运维's install: /usr/local/bin/gh -> wrapper
        open(f"{d}/usr/gh", "w").write("#!/bin/sh\n"); os.chmod(f"{d}/usr/gh", 0o755)
        self.assertEqual(lib.real_gh({"PATH": f"{d}/local:{d}/usr"}), os.path.realpath(f"{d}/usr/gh"))

    def test_pat_env_is_the_pat_never_an_installation_token(self):
        self.assertEqual(lib.pat_env({"GH_TOKEN": "ghs_x", "GITHUB_TOKEN": "ghp_pat", "A": "1"}), {"GH_TOKEN": "ghp_pat", "A": "1"})
        self.assertEqual(lib.pat_env({"GH_TOKEN": "ghs_x", "GITHUB_TOKEN": "ghs_y"}), {}, "no PAT in env -> the real gh's hosts.yml login")

    def test_credcheck_and_opsenv_call_the_real_gh_with_the_pat(self):
        import ast
        for f in ("credcheck.py", "opsenv.py"):
            t = ast.parse(open(os.path.join(BIN, f)).read()); hits = []
            for c in ast.walk(t):
                if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "run" and c.args and isinstance(c.args[0], ast.List) and c.args[0].elts:
                    e0 = c.args[0].elts[0]
                    if isinstance(e0, ast.Constant) and e0.value == "gh": self.fail(f"{f}:{c.lineno} bare gh")
                    if isinstance(e0, ast.Call) and getattr(e0.func, "id", "") == "real_gh":
                        hits.append(any(k.arg == "env" and isinstance(k.value, ast.Call) and getattr(k.value.func, "id", "") == "pat_env" for k in c.keywords))
            self.assertTrue(hits and all(hits), f)
            self.assertNotIn('sh("gh ', open(os.path.join(BIN, f)).read(), f)

    def test_credcheck_runs_real_gh_not_the_wrapper(self):
        sys.modules.setdefault("merger", types.SimpleNamespace(FIELDS="number", blockers=lambda p: []))
        import credcheck as C
        seen = []
        fake = lambda cmd, **k: seen.append((cmd, k.get("env"))) or types.SimpleNamespace(returncode=0, stdout="true", stderr="")
        with mock.patch.object(C, "secret_env", lambda: None), mock.patch.object(C, "NEED", {}), mock.patch.object(C.subprocess, "run", fake), \
             mock.patch.object(C, "real_gh", lambda: "/usr/bin/gh"), mock.patch.dict(os.environ, {"GH_TOKEN": "ghs_wrapper", "GITHUB_TOKEN": "ghp_pat"}), \
             mock.patch.object(C, "write_atomic", lambda *a: None), redirect_stdout(io.StringIO()):
            C.main()
        cmd, env = seen[-1]
        self.assertEqual(cmd[0], "/usr/bin/gh"); self.assertEqual(env["GH_TOKEN"], "ghp_pat"); self.assertNotIn("GITHUB_TOKEN", env)

    def test_argus_doctor_identity_is_explicit(self):
        js = ("import('%s/argus/src/github.mjs').then(async m => console.log(JSON.stringify(await m.currentIdentity('PraiseZhu/pr-qa-agent-deploy', 'PraiseZhu'))))" % ROOT)
        env = {**os.environ, "PRAISE_QA_GH_OWN_TOKEN": "O", "ARGUS_SECRETS_FILE": "/nonexistent"}
        r = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, cwd=os.path.join(ROOT, "argus"), env=env)
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1]), {"login": "praise-qa[bot]", "via": "praise-qa-app"}, r.stderr[-400:])
        src = open(os.path.join(ROOT, "argus/src/github.mjs")).read(); self.assertNotIn("apiGet('user')", src)


class CredentialHelper(Base):
    def get(self, text):
        import importlib.machinery, importlib.util
        spec = importlib.util.spec_from_loader("gcp", importlib.machinery.SourceFileLoader("gcp", os.path.join(BIN, "git-credential-praise-qa")))
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        out = io.StringIO(); m.main(["get"], stdin=io.StringIO(text), stdout=out); return out.getvalue()

    def test_by_remote_and_flag(self):
        q = "protocol=https\nhost=github.com\npath={}\n"
        self.assertEqual(self.get(q.format("xindong/mivo-canvas-plugin.git")), "", "flag off -> next helper (PAT)")
        self.on("gh-app-wrapper-enabled")
        self.assertEqual(self.get(q.format("xindong/mivo-canvas-plugin.git")), f"username=x-access-token\npassword={FAKE[PROD]}\n")
        self.assertIn(FAKE[PAN], self.get(q.format("PraiseZhu/grokbot-box.git")))
        with redirect_stderr(io.StringIO()): self.assertIn(FAKE[PROD], self.get("protocol=https\nhost=github.com\n"))   # no path -> product, logged
        self.assertEqual(self.get("protocol=https\nhost=gitlab.com\npath=a/b\n"), "")

    def test_xindong_owner_maps_to_product_installation(self):
        import ghtoken
        self.assertEqual(ghtoken.installation("xindong/anything"), 178486896); self.assertEqual(ghtoken.installation("PraiseZhu/x"), 164262283)


if __name__ == "__main__": unittest.main()
