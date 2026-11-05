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
# node-id resolution (acceptance 2b) never touches the network in tests: ids PAN_* -> own repo, PROD_* -> product, else unresolvable
NODE_CALLS = []
def fake_node_repos(ids):
    NODE_CALLS.append(list(ids))
    return [PAN if i.startswith("PAN_") else PROD if i.startswith("PROD_") else None for i in ids]
lib.gql_node_repos = fake_node_repos
# the update-branch exception's PR lookup (#214 r3) never touches the network in tests: unmocked -> a lookup failure (refused)
def _no_lookup(n, *a, **k): raise RuntimeError("no network in tests")
lib.ub_lookup = _no_lookup

PROD, PAN = "xindong/mivo-canvas-plugin", "PraiseZhu/panoptes"
AJ, ZX = "aj0928/aj0928-handoff", "zhongxingtian-ai/handover-mivo"
FAKE = {PROD: "ghs_" + "P" * 36, PAN: "ghs_" + "O" * 36, AJ: "ghs_" + "A" * 36, ZX: "ghs_" + "Z" * 36}
def F(n): return f"{lib.PI_STATE}/{n}"
FLAGS = ("gh-app-enabled", "gh-app-merger-enabled", "gh-app-wrapper-enabled", "gh-app-product-writes-enabled", "gh-ratelimit.json", "gh-ratelimit-praise-qa.json")


class Base(unittest.TestCase):
    def setUp(self):
        self.clear(); self.minted = []
        def token(repo):
            self.minted.append(repo)
            if getattr(self, "mint_fail", False): raise RuntimeError("HTTP 401 Bad credentials " + FAKE[PAN])
            return FAKE.get(repo) or (FAKE[PAN] if str(repo).startswith("PraiseZhu/") else FAKE[PROD])
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
        self.assertIsNone(self.refused(["api", "graphql", "-f", f"query={self.M}", "-F", "id=PAN_1"]))   # node id resolves to an own repo

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
        _, c, _ = self.wrap(["issue", "list", "secret-arg"]); self.assertEqual(c[2]["GH_TOKEN"], FAKE[PROD]); self.assertEqual(self.minted[-1], PROD)
        line = json.loads(open(W.unresolved_log()).read().splitlines()[-1])
        self.assertEqual((line["cmd"], line["repo"]), ("gh issue list", None))
        # G5 (11-05): gh search names no repo but is no longer a guess: the panoptes installation (lib.search_repo)
        _, c, _ = self.wrap(["search", "prs", "secret-arg"]); self.assertEqual(c[2]["GH_TOKEN"], FAKE[PAN]); self.assertEqual(self.minted[-1], PAN); self.assertNotIn("secret-arg", json.dumps(line)); self.assertNotIn("ghs_", json.dumps(line))

    def test_allowlisted_caller_gets_the_pat_and_the_real_gh(self):
        import gh_wrapper as W
        self.on("gh-app-wrapper-enabled")
        for caller in (["node", "/home/x/lifeline/scripts/loops/lifeline/tick.mjs"],
                       ["/usr/bin/python3", "/Users/j/review-pr/scripts/merge-reviewed-pr.py"], ["node", "/srv/app/scripts/ops/org-market-open-company-pr.mjs"]):
            _, c, err = self.wrap(["pr", "list", "-R", PROD], env={"GITHUB_TOKEN": "ghp_pat", "GH_TOKEN": "ghs_old"}, caller=caller)
            self.assertEqual(c[0], "/real/gh", caller); self.assertEqual(c[2].get("GH_TOKEN"), "ghp_pat", caller); self.assertIn("allowlisted caller", err)
        self.assertEqual(json.loads(open(W.allowlist_log()).read().splitlines()[-1])["caller"], "mivo-merger-org-market-open-company-pr")
        self.assertEqual(len({n for n, _ in W.CALLER_ALLOWLIST}), len(W.CALLER_ALLOWLIST))

    def test_vigil_callers_no_longer_allowlisted(self):
        """11-04: vigil#274 merged; the 4 vigil entries are gone. A vigil watcher path gets None and the App token, not the PAT."""
        import gh_wrapper as W
        names = {n for n, _ in W.CALLER_ALLOWLIST}
        for n in ("vigil-cindy-watcher", "vigil-cindy-repair", "vigil-mivo-watcher", "vigil-mivo-handoff"): self.assertNotIn(n, names)
        self.assertNotIn("grokbot-box-60-gh-auth", names)
        self.on("gh-app-wrapper-enabled")
        for p in ("/home/x/vigil/watchers/cindy/bin/cindy-watcher.mjs", "/x/cindy/bin/cindy-repair.mjs", "/x/mivo/bin/mivo-watcher.mjs", "/x/mivo/bin/mivo-handoff.mjs"):
            self.assertIsNone(W.allowlisted(["node", p]), p)
            _, c, err = self.wrap(["pr", "list", "-R", PROD], env={"GITHUB_TOKEN": "ghp_pat"}, caller=["node", p])
            self.assertEqual(c[2]["GH_TOKEN"], FAKE[PROD], p); self.assertNotIn("allowlisted", err)

    def test_org_market_open_company_pr_is_attributed_to_the_right_repo(self):
        import gh_wrapper as W
        for path, name in (("/srv/mivo-merger/app/scripts/ops/org-market-open-company-pr.mjs", "mivo-merger-org-market-open-company-pr"),
                           ("app/scripts/ops/org-market-open-company-pr.mjs", "mivo-merger-org-market-open-company-pr"),
                           ("/home/x/mivo-canvas-plugin/scripts/ops/org-market-open-company-pr.mjs", "lifeline-org-market-open-company-pr"),
                           ("scripts/ops/org-market-open-company-pr.mjs", "lifeline-org-market-open-company-pr"),
                           ("/x/myapp/scripts/ops/org-market-open-company-pr.mjs", "lifeline-org-market-open-company-pr")):
            self.assertEqual(W.allowlisted(["node", path]), name, path)

    def test_non_listed_caller_gets_the_app(self):
        self.on("gh-app-wrapper-enabled")
        for caller in (["node", "/home/x/other/bin/cindy-watcher-old.mjs"], ["node", "/x/notcindy/bin/cindy-watcher.mjs.bak"], []):
            _, c, err = self.wrap(["pr", "list", "-R", PROD], env={"GITHUB_TOKEN": "ghp_pat"}, caller=caller)
            self.assertEqual(c[2]["GH_TOKEN"], FAKE[PROD], caller); self.assertNotIn("allowlisted", err)

    def test_red_lines_still_block_allowlisted_callers(self):
        for a in (["pr", "merge", "1", "-R", PROD], ["pr", "edit", "1", "--body", "x", "-R", PROD]):
            rc, c, err = self.wrap(a, caller=["node", "/x/scripts/loops/lifeline/report.mjs"]); self.assertEqual((rc, c), (4, None), a); self.assertIn("red line", err)

    def test_mint_failure_refuses_and_alerts_no_pat(self):
        """11-05 R2 gap 3: mint failure -> exit 5, nothing exec'd (no inherited PraiseZhu login), alert written; recovery clears it"""
        self.on("gh-app-wrapper-enabled"); self.mint_fail = True
        al = lib.gh_app_mint_alert_file()
        if os.path.exists(al): os.remove(al)
        rc, c, err = self.wrap(["pr", "list", "-R", PROD], env={"GITHUB_TOKEN": "ghp_pat"})
        self.assertEqual((rc, c), (5, None)); self.assertIn("not falling back", err)
        doc = json.load(open(al)); self.assertEqual((doc["check"], doc["consecutive_failures"]), ("gh_app_mint", 1))
        self.assertNotIn(FAKE[PROD], json.dumps(doc)); self.assertNotIn("ghp_pat", json.dumps(doc)); self.assertIn("gh_wrapper", doc["breaches"])
        self.mint_fail = False
        rc, c, _ = self.wrap(["pr", "list", "-R", PROD], env={"GITHUB_TOKEN": "ghp_pat"})
        self.assertEqual(c[2]["GH_TOKEN"], FAKE[PROD]); self.assertFalse(os.path.exists(al))

    def test_mint_failure_git_credential_gives_no_answer(self):
        self.on("gh-app-wrapper-enabled", "gh-app-product-writes-enabled"); self.mint_fail = True
        import gh_wrapper as W
        out = io.StringIO()
        with redirect_stderr(io.StringIO()), mock.patch("sys.stdout", out):
            rc = W.main(["auth", "git-credential", "get"], env={"PATH": "", "PRAISE_QA_REAL_GH": "/real/gh", "GITHUB_TOKEN": "ghp_pat"},
                        execve=lambda *a: self.fail("real gh (PAT) must not answer"), run=lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="", stderr=""),
                        caller=[], stdin="protocol=https\nhost=github.com\npath=xindong/mivo-canvas-plugin.git\n\n")
        self.assertEqual(rc, 5); self.assertNotIn("password", out.getvalue())

    def test_allowlisted_caller_still_on_the_pat_when_mint_would_fail(self):
        """#234 CALLER_ALLOWLIST unchanged: never mints, still the PAT pass-through"""
        self.on("gh-app-wrapper-enabled"); self.mint_fail = True
        rc, c, _ = self.wrap(["pr", "list", "-R", PROD], env={"GITHUB_TOKEN": "ghp_pat"}, caller=["node", "/x/scripts/loops/lifeline/report.mjs"])
        self.assertIsNotNone(c); self.assertEqual(c[2].get("GH_TOKEN"), "ghp_pat")


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
        self.assertEqual(self.get(q.format("xindong/mivo-canvas-plugin.git")), "", "11-05: product-writes off -> PAT (next helper)")
        self.on("gh-app-product-writes-enabled")
        self.assertEqual(self.get(q.format("xindong/mivo-canvas-plugin.git")), f"username=x-access-token\npassword={FAKE[PROD]}\n")
        self.assertIn(FAKE[PAN], self.get(q.format("PraiseZhu/grokbot-box.git")))
        self.assertEqual(self.get("protocol=https\nhost=github.com\n"), "")   # no path -> nothing (next helper)
        self.assertEqual(self.get("protocol=https\nhost=gitlab.com\npath=a/b\n"), "")

    def test_xindong_owner_maps_to_product_installation(self):
        import ghtoken
        self.assertEqual(ghtoken.installation("xindong/anything"), 178486896); self.assertEqual(ghtoken.installation("PraiseZhu/x"), 164262283)


class AcceptanceRound1(Base):
    """#214 acceptance round 1 (11-04): 2b graphql merge by node id, 5 credential helper on foreign repos, 4 allowlist script-only
    matching + sh -c ancestors, 2c product writes"""
    PANCWD = "https://github.com/PraiseZhu/panoptes.git"
    def gql(self, q, *kv): return ["api", "graphql", "-f", f"query={q}"] + [y for x in kv for y in ("-F", x)]

    def test_graphql_merge_by_product_node_id_refused_from_own_repo_context(self):
        for m in ("mergePullRequest", "enablePullRequestAutoMerge", "enqueuePullRequest", "updatePullRequestBranch"):
            q = "mutation($id:ID!){ %s(input:{pullRequestId:$id}){ clientMutationId } }" % m
            for a, env, url in ((self.gql(q, "id=PROD_1"), {}, self.PANCWD), (self.gql(q, "id=PROD_1"), {"GH_REPO": PAN}, ""),
                                (self.gql(q, "id=PROD_1", "owner=PraiseZhu", "name=panoptes"), {}, ""),
                                (self.gql(q, "id=PROD_1"), {}, self.PANCWD)):
                for caller in ([], ["node", "/x/review-pr/scripts/merge-reviewed-pr.mjs"]):
                    rc, c, err = self.wrap(a, env=env, cwd_url=url, caller=caller)
                    self.assertEqual((rc, c), (4, None), (m, env, caller)); self.assertIn("merging", err)
        q = 'mutation { mergeBranch(input:{repositoryId:"PROD_R", base:"main", head:"x"}){ clientMutationId } }'
        self.assertEqual(self.wrap(self.gql(q), cwd_url=self.PANCWD)[0], 4)

    def test_graphql_merge_unresolvable_or_idless_refused_own_resolved_allowed(self):
        q = "mutation($id:ID!){ mergePullRequest(input:{pullRequestId:$id}){ clientMutationId } }"
        self.assertEqual(self.wrap(self.gql(q, "id=UNKNOWN"), cwd_url=self.PANCWD)[0], 4)
        self.assertEqual(self.wrap(self.gql(q), cwd_url=self.PANCWD)[0], 4)                       # $id missing -> fail closed
        self.assertEqual(self.wrap(self.gql("mutation($i:MergePullRequestInput!){ mergePullRequest(input:$i){ clientMutationId } }"), cwd_url=self.PANCWD)[0], 4)
        rc, c, _ = self.wrap(self.gql(q, "id=PAN_1"), cwd_url=self.PANCWD); self.assertIsNotNone(c)
        self.assertEqual(self.wrap(self.gql('mutation { mergePullRequest(input:{pullRequestId:"PAN_1"}) { a: clientMutationId } b: mergePullRequest(input:{pullRequestId:"PROD_2"}) { clientMutationId } }'))[0], 4)
        NODE_CALLS.clear(); self.assertIsNone(lib.gh_red_line(self.gql("query{ viewer { login } }"))); self.assertEqual(NODE_CALLS, [])

    def test_credential_helper_silent_for_repos_without_installation(self):
        self.on("gh-app-wrapper-enabled"); q = "protocol=https\nhost=github.com\npath={}\n\n"
        # 11-05: the two handoff repos now have Praise QA installed; other uninstalled remotes stay silent
        self.assertIn(FAKE[AJ], self.get(q.format("aj0928/aj0928-handoff.git")))
        self.assertIn(FAKE[ZX], self.get(q.format("zhongxingtian-ai/handover-mivo.git")))
        self.minted.clear(); self.assertEqual(self.get(q.format("someone/else")), ""); self.assertEqual(self.minted, [])
        self.assertIn(FAKE[PAN], self.get(q.format("PraiseZhu/panoptes.git")))
        self.on("gh-app-product-writes-enabled")
        self.assertIn(FAKE[PROD], self.get(q.format("xindong/mivo-canvas-plugin.git")))
    get = CredentialHelper.get
    wrap = Wrapper.wrap

    def test_allowlist_matches_only_the_script_longest_suffix_first(self):
        import gh_wrapper as W
        self.assertEqual(W.allowlisted(["node", "/workspace/mivo-merger/app/scripts/ops/org-market-open-company-pr.mjs"]), "mivo-merger-org-market-open-company-pr")
        for c in (["vim", "/tmp/evil/scripts/loops/lifeline/tick.mjs"], ["node", "x.mjs", "--note", "review-pr/scripts/signoff-hold"],
                  ["node", "/tmp/a.mjs", "/x/scripts/loops/lifeline/tick.mjs"], ["sh", "-c", "gh pr list"], ["node", "-e", "x", "/x/scripts/loops/lifeline/tick.mjs"], []):
            self.assertIsNone(W.allowlisted(c), c)
        self.assertEqual(W.allowlisted(["node", "--no-warnings", "/x/scripts/loops/lifeline/tick.mjs", "--once"]), "lifeline-tick")
        self.assertEqual(W.allowlisted(["/x/review-pr/scripts/signoff-hold", "1"]), "review-pr-signoff-hold")
        d = tempfile.mkdtemp(); os.makedirs(f"{d}/real/scripts/loops/lifeline"); open(f"{d}/real/scripts/loops/lifeline/tick.mjs", "w").close()
        os.symlink(f"{d}/real/scripts/loops/lifeline/tick.mjs", f"{d}/innocent.mjs")
        self.assertEqual(W.allowlisted(["node", f"{d}/innocent.mjs"]), "lifeline-tick")   # realpath of the script

    def test_sh_c_parent_walks_up_at_most_three_levels(self):
        import gh_wrapper as W
        procs = {10: (["sh", "-c", "gh pr list"], 11), 11: (["bash", "-c", "sh -c x"], 12), 12: (["node", "/x/scripts/loops/lifeline/tick.mjs"], 1)}
        with mock.patch.object(W, "parent_cmdline", lambda p=None: procs.get(p, ([], 0))[0]), mock.patch.object(W, "parent_pid", lambda p: procs.get(p, ([], 0))[1]):
            self.assertEqual(W.allowlisted(W.caller_chain(10)), "lifeline-tick")
            procs.update({12: (["sh", "-c", "a"], 13), 13: (["sh", "-c", "b"], 14), 14: (["node", "/x/scripts/loops/lifeline/tick.mjs"], 1)})
            self.assertIsNone(W.allowlisted(W.caller_chain(10)))   # 4 shells deep: not walked
            self.assertEqual(W.caller_chain(14), ["node", "/x/scripts/loops/lifeline/tick.mjs"])

    def test_product_writes_refused_except_review_pr_comments_and_ci_rerun(self):
        R = PROD; rp = ["node", "/x/review-pr/scripts/signoff-hold.mjs"]
        always = (["issue", "create", "-R", R, "-t", "x"], ["pr", "close", "1", "-R", R], ["api", "-X", "POST", f"repos/{R}/issues", "-f", "title=x"],
                  ["api", f"repos/{R}/issues", "-f", "title=x"], ["api", "-X", "DELETE", f"repos/{R}/git/refs/heads/x"],
                  ["api", "--method", "DELETE", f"/repos/{R}/git/refs/heads/x"], ["api", "-X", "PATCH", f"repos/{R}/pulls/1", "-f", "state=closed"],
                  self.gql('mutation { closePullRequest(input:{pullRequestId:"PROD_1"}){ clientMutationId } }'),
                  self.gql('mutation { deleteRef(input:{refId:"PROD_ref"}){ clientMutationId } }'),
                  self.gql('mutation { createIssue(input:{repositoryId:"PROD_R", title:"x"}){ clientMutationId } }'))
        comments = (["issue", "comment", "1", "-R", R, "-b", "x"], ["pr", "comment", "1", "-R", R, "-b", "x"], ["pr", "review", "1", "-R", R, "--approve"],
                    ["api", "-X", "POST", f"repos/{R}/issues/1/comments", "-f", "body=x"], ["api", "-X", "POST", f"repos/{R}/pulls/1/reviews", "-f", "event=APPROVE"],
                    self.gql('mutation { addComment(input:{subjectId:"PROD_1", body:"x"}){ clientMutationId } }'),
                    self.gql('mutation { addPullRequestReview(input:{pullRequestId:"PROD_1", event:APPROVE}){ clientMutationId } }'))
        for a in always:
            for caller in ([], rp): self.assertEqual(self.wrap(a, caller=caller)[0], 4, (a, caller))
        for a in comments:
            self.assertEqual(self.wrap(a)[0], 4, a); self.assertEqual(self.wrap(a, caller=["node", "/x/scripts/loops/lifeline/report.mjs"])[0], 4, a)
            self.assertIsNotNone(self.wrap(a, caller=rp)[1], a)
        for a in (["run", "rerun", "123", "-R", R], ["run", "rerun", "123", "--failed", "-R", R], ["api", "-X", "POST", f"repos/{R}/actions/runs/1/rerun"],
                  ["api", "-X", "POST", f"repos/{R}/actions/runs/1/rerun-failed-jobs"], ["api", "-X", "POST", f"repos/{R}/actions/jobs/1/rerun"],
                  ["pr", "comment", "1", "-R", PAN, "-b", "x"], ["issue", "create", "-R", PAN, "-t", "x"], ["api", f"repos/{R}/issues/1/comments"],
                  self.gql('mutation { addComment(input:{subjectId:"PAN_1", body:"x"}){ clientMutationId } }')):
            self.assertIsNotNone(self.wrap(a)[1], a)


class ProductWriteExceptions(Base):
    """PM ruling 11-04: product-write exceptions per caller x operation (gh_wrapper.PRODUCT_WRITE_EXCEPTIONS), never a whole script"""
    wrap = Wrapper.wrap
    gql = AcceptanceRound1.gql
    OUT = ["python3", "/x/panoptes/pi/bin/outbound.py", "--job", "daily"]
    INFRA = ["python3", "/x/panoptes/pi/bin/infra_outbound.py"]

    def test_listed_caller_x_op_allowed(self):
        R = PROD
        for caller, a in ((self.OUT, ["issue", "create", "-R", R, "--title", "x", "--body", "y"]),
                          (self.OUT, ["pr", "comment", "1", "-R", R, "--body", "x"]),
                          (self.OUT, ["issue", "close", "1", "-R", R, "--reason", "completed", "--comment", "x"]),
                          (self.INFRA, ["issue", "comment", "1", "-R", R, "--body", "x"])):
            rc, c, err = self.wrap(a, caller=caller); self.assertIsNotNone(c, (caller, a)); self.assertNotIn("refused", err)
            self.assertNotIn("allowlisted", err)   # an exception is not PAT pass-through

    def test_listed_callers_other_ops_refused(self):
        R = PROD
        others = (["pr", "close", "1", "-R", R], ["api", "-X", "DELETE", f"repos/{R}/git/refs/heads/x"], ["pr", "review", "1", "-R", R, "--approve"],
                  ["pr", "merge", "1", "-R", R, "--squash"], ["api", "-X", "POST", f"repos/{R}/issues", "-f", "title=x"],
                  ["api", "-X", "POST", f"repos/{R}/issues/1/comments", "-f", "body=x"], ["api", "-X", "PATCH", f"repos/{R}/issues/1", "-f", "state=closed"],
                  ["pr", "edit", "1", "-R", R, "--body", "x"], ["issue", "delete", "1", "-R", R],
                  self.gql('mutation { addComment(input:{subjectId:"PROD_1", body:"x"}){ clientMutationId } }'),
                  self.gql('mutation { createIssue(input:{repositoryId:"PROD_R", title:"x"}){ clientMutationId } }'))
        for caller in (self.OUT, self.INFRA):
            for a in others: self.assertEqual(self.wrap(a, caller=caller)[:2], (4, None), (caller, a))
        self.assertEqual(self.wrap(["issue", "comment", "1", "-R", R, "-b", "x"], caller=self.OUT)[:2], (4, None))   # not listed for outbound
        for a in (["issue", "create", "-R", R, "-t", "x"], ["pr", "comment", "1", "-R", R, "-b", "x"], ["issue", "close", "1", "-R", R]):
            self.assertEqual(self.wrap(a, caller=self.INFRA)[:2], (4, None), a)   # infra_outbound: issue comment only

    def test_non_panoptes_callers_still_refused(self):
        R = PROD
        ops = (["issue", "create", "-R", R, "-t", "x"], ["pr", "comment", "1", "-R", R, "-b", "x"], ["issue", "close", "1", "-R", R],
               ["issue", "comment", "1", "-R", R, "-b", "x"])
        for caller in ([], ["python3", "/x/other/outbound.py"], ["python3", "/x/pi/bin/my_outbound.py"], ["python3", "/x/pi/bin/outbound.py.bak"],
                       ["python3", "-c", "import outbound"], ["sh", "-c", "python3 /x/pi/bin/outbound.py"], ["node", "/x/scripts/loops/lifeline/report.mjs"],
                       ["python3", "/tmp/evil.py", "/x/panoptes/pi/bin/outbound.py"]):
            for a in ops: self.assertEqual(self.wrap(a, caller=caller)[:2], (4, None), (caller, a))
        self.assertEqual(self.wrap(["issue", "comment", "1", "-R", R, "-b", "x"], caller=["python3", "/x/panoptes/pi/bin/infra_outbound.py.old"])[:2], (4, None))


class AcceptanceRound2(Base):
    """#214 acceptance round 2 (11-04): default-deny product-write guard; B1 stdin/@file bodies, B2 full URLs, B3 branch moves,
    B4 every flag spelling, B5 review-pr approve allowed"""
    wrap = Wrapper.wrap
    gql = AcceptanceRound1.gql
    M = 'mutation{mergePullRequest(input:{pullRequestId:"PROD_1"}){clientMutationId}}'
    ALLOW = ["node", "/x/review-pr/scripts/merge-reviewed-pr.mjs"]; OUT = ProductWriteExceptions.OUT; RP = ["node", "/x/review-pr/scripts/signoff-hold.mjs"]
    CALLERS = ([], ["node", "/x/scripts/loops/lifeline/report.mjs"], ALLOW, OUT, RP)
    def blocked_for_all(self, a, stdin=None, **kw):
        import gh_wrapper as W
        for caller in self.CALLERS:
            for env, url in (({}, ""), ({}, AcceptanceRound1.PANCWD), ({"GH_REPO": PAN}, "")):
                with mock.patch.object(W, "refeed_stdin", lambda d: self.fail("re-fed a refused call")), mock.patch.object(sys, "stdin", io.StringIO(stdin or "")):
                    rc, c, err = self.wrap(a, env=env, cwd_url=url, caller=caller)
                self.assertEqual((rc, c), (4, None), (a, caller, env, url)); self.assertIn("refused", err)

    def test_b1_graphql_body_from_stdin_or_file_is_read_and_checked(self):
        body = json.dumps({"query": self.M}); d = tempfile.mkdtemp(); jf = f"{d}/b.json"; open(jf, "w").write(body); qf = f"{d}/q.graphql"; open(qf, "w").write(self.M)
        for a, sin in ((["api", "graphql", "--input", "-"], body), (["api", "graphql", "--input=-"], body), (["api", "graphql", "-F", "query=@-"], self.M),
                       (["api", "graphql", "-Fquery=@-"], self.M), (["api", "graphql", "--field=query=@-"], self.M),
                       (["api", "graphql", "--input", jf], None), (["api", "graphql", "-F", f"query=@{qf}"], None)):
            self.blocked_for_all(a, sin)
        for a in (["api", "graphql", "--input", "-"], ["api", "graphql", "-F", "query=@-"], ["api", "graphql", "--input", f"{d}/missing.json"]):
            self.assertIn("cannot be parsed", lib.gh_red_line(a, repo=PAN) or "", a)   # lib.gh has no stdin copy: unparseable -> refused

    def test_b1_stdin_read_query_passes_and_is_refed_to_the_real_gh(self):
        import gh_wrapper as W
        fed = []
        q = json.dumps({"query": "query{ viewer { login } }"})
        with mock.patch.object(W, "refeed_stdin", fed.append), mock.patch.object(sys, "stdin", io.StringIO(q)):
            rc, c, _ = self.wrap(["api", "graphql", "--input", "-"], cwd_url=AcceptanceRound1.PANCWD)
        self.assertIsNotNone(c); self.assertEqual(fed, [q])
        with mock.patch.object(sys, "stdin", io.StringIO(q)):
            self.assertIsNone(lib.gh_product_write(["api", "graphql", "--input", "-"], PROD, stdin=q))

    def test_b2_full_urls_query_strings_and_case_are_normalized(self):
        R = PROD
        for a in (["api", "-X", "PUT", f"https://api.github.com/repos/{R}/pulls/1/merge"], ["api", "-X", "PUT", f"HTTPS://API.GITHUB.COM/REPOS/XINDONG/MIVO-CANVAS-PLUGIN/PULLS/1/MERGE"],
                  ["api", "-X", "PUT", f"/repos/{R}/pulls/1/merge?x=1"], ["api", "-X", "PUT", f"repos/{R}//pulls/1/merge/"], ["api", "-X", "PUT", f"repos/xindong%2Fmivo-canvas-plugin/pulls/1/merge"],
                  ["api", "-X", "PUT", f"https://github.example.com/api/v3/repos/{R}/pulls/1/merge"], ["api", "-X", "POST", f"https://api.github.com/repos/{R}/issues", "-f", "title=x"],
                  ["api", "-X", "PUT", "repos/{owner}/{repo}/pulls/1/merge"], ["api", "-X", "PUT", "repositories/123/pulls/1/merge"],
                  ["api", "https://api.github.com/graphql", "-f", f"query={self.M}"], ["pr", "merge", f"https://github.com/{R}/pull/1"]):
            for caller in self.CALLERS: self.assertEqual(self.wrap(a, caller=caller)[:2], (4, None), (a, caller))
        self.assertEqual(lib.gh_target_repo(["api", f"https://api.github.com/repos/{R}/issues?state=all"]), R)
        self.assertIsNotNone(self.wrap(["api", f"https://api.github.com/repos/{R}/pulls?state=open"])[1])   # a read
        self.assertIsNotNone(self.wrap(["api", "-X", "PUT", "repos/{owner}/{repo}/pulls/1/merge"], cwd_url=AcceptanceRound1.PANCWD)[1])   # placeholder -> cwd = own repo

    def test_b3_branch_moves_refused_for_every_caller_and_lib_gh(self):
        R = PROD
        for a in (["pr", "update-branch", "1", "-R", R], ["pr", "update-branch", "1", "--rebase", "-R", R], ["api", "-X", "PUT", f"repos/{R}/pulls/1/update-branch"],
                  ["api", "-X", "PUT", f"repos/{R}/pulls/1/update-branch", "-f", "expected_head_sha=abc"], ["api", "-X", "POST", f"repos/{R}/merges", "-f", "base=main", "-f", "head=x"],
                  ["api", "-X", "PATCH", f"repos/{R}/git/refs/heads/main", "-f", "sha=x", "-F", "force=true"], ["api", "-X", "POST", f"repos/{R}/git/refs", "-f", "ref=refs/heads/x"],
                  self.gql('mutation{updateRef(input:{refId:"PROD_r", oid:"x", force:true}){clientMutationId}}')):
            self.blocked_for_all(a)
            self.assertIsNotNone(lib.gh_red_line(a), a)   # lib.gh (merger, outbound) too, not only the wrapper
        self.blocked_for_all(["api", "-X", "DELETE", f"repos/{R}/git/refs/heads/x"])   # ref delete: wrapper product-write (janitor stays refused)

    def test_b4_every_flag_spelling_is_parsed(self):
        R = PROD
        for a in (["api", "-XDELETE", f"repos/{R}/git/refs/heads/x"], ["api", "-X=DELETE", f"repos/{R}/git/refs/heads/x"], ["api", "--method", "delete", f"repos/{R}/git/refs/heads/x"],
                  ["api", "--method=PATCH", f"repos/{R}/issues/1", "-f", "state=closed"], ["api", f"repos/{R}/issues", "-ftitle=x"], ["api", f"repos/{R}/issues", "--raw-field=title=x"],
                  ["api", f"repos/{R}/issues", "--raw-field", "title=x"], ["api", f"repos/{R}/issues", "-F", "title=x"], ["api", f"repos/{R}/issues", "--field", "title=x"],
                  ["api", f"repos/{R}/issues", "-Ftitle=x"], ["api", f"repos/{R}/issues/1", "-X", "PATCH", "-fstate=closed"], ["api", "-iXPUT", f"repos/{R}/pulls/1/merge"],
                  ["api", f"repos/{R}/issues", "--input", "/dev/null"], ["api", "-X", "PUT", "--", f"repos/{R}/pulls/1/merge"],
                  ["api", f"repos/{R}/issues", "--unknown-flag", "x"], ["api", f"repos/{R}/issues", f"repos/{R}/pulls"], ["api", f"repos/{R}/issues", "-f", "noequals"]):
            for caller in self.CALLERS: self.assertEqual(self.wrap(a, caller=caller)[:2], (4, None), (a, caller))
        p = lib.gh_api_parse(["api", "-iXput", "--raw-field=a=1", "-Fb=2", "--field", "c=3", "-H", "Accept: x", "https://api.github.com/repos/O/R/x?y=1"])
        self.assertEqual((p["method"], p["path"], p["fields"]), ("PUT", "repos/O/R/x", [("a", "1"), ("b", "2"), ("c", "3")]))
        self.assertIn("editing", lib.gh_red_line(["pr", "edit", "1", "-R", R, "-bx"]) or ""); self.assertIn("editing", lib.gh_red_line(["pr", "edit", "1", "-R", R, "--title=x"]) or "")

    def test_default_deny_unlisted_product_ops_and_unknown_targets(self):
        R = PROD
        for a in (["issue", "edit", "1", "-R", R, "--add-label", "x"], ["issue", "reopen", "1", "-R", R], ["pr", "reopen", "1", "-R", R], ["pr", "create", "-R", R, "--title", "x"],
                  ["label", "create", "x", "-R", R], ["release", "create", "v1", "-R", R], ["repo", "edit", R], ["repo", "delete", R], ["workflow", "run", "x", "-R", R],
                  ["run", "cancel", "1", "-R", R], ["secret", "set", "X", "-R", R], ["something-new", "verb", "-R", R], ["pr", "lock", "1", "-R", R],
                  ["api", "-X", "PUT", f"repos/{R}/topics", "-f", "names[]=x"], ["api", "-X", "POST", f"repos/{R}/labels", "-f", "name=x"],
                  ["api", "-X", "PUT", f"repos/{R}/contents/a.txt", "-f", "message=x"], ["api", "-X", "POST", "markdown", "-f", "text=x"],
                  self.gql('mutation{createLabel(input:{repositoryId:"PROD_R", name:"x"}){clientMutationId}}'), self.gql("mutation{ createGist { id } }")):
            for caller in self.CALLERS: self.assertEqual(self.wrap(a, caller=caller)[:2], (4, None), (a, caller))
        for caller in ([], self.OUT, ["node", "/x/scripts/loops/lifeline/report.mjs"]):   # review-pr callers may comment (B5)
            self.assertEqual(self.wrap(self.gql('mutation{addComment(input:{subjectId:"UNKNOWN", body:"x"}){clientMutationId}}'), caller=caller)[:2], (4, None))
        self.assertEqual(self.wrap(["issue", "create", "-t", "x"])[:2], (4, None))   # no -R, no GH_REPO, no cwd remote: unknown -> refused

    def test_allowed_set_still_passes(self):
        R = PROD
        for a in (["pr", "view", "1", "-R", R], ["pr", "list", "-R", R], ["pr", "checks", "1", "-R", R], ["issue", "list", "-R", R], ["repo", "view", R], ["label", "list", "-R", R],
                  ["pr", "create", "-R", R, "--draft", "--base", "main", "--head", "x", "--title", "t", "--body", "b"], ["pr", "ready", "1", "-R", R],
                  ["run", "rerun", "1", "--failed", "-R", R], ["run", "view", "1", "-R", R], ["api", f"repos/{R}/pulls/1"], ["api", f"repos/{R}/commits/abc", "-q", ".sha"],
                  ["api", "-X", "POST", f"repos/{R}/actions/runs/1/rerun-failed-jobs"], ["api", "-X", "POST", f"https://api.github.com/repos/{R}/actions/jobs/1/rerun"],
                  ["api", "rate_limit"], ["api", "graphql", "-f", "query=query{ repository(owner:\"xindong\",name:\"mivo-canvas-plugin\"){ id } }"],
                  ["search", "prs", "x"], ["auth", "status"], ["pr", "merge", "1", "-R", PAN], ["api", "-X", "PUT", f"repos/{PAN}/pulls/1/update-branch"]):
            rc, c, err = self.wrap(a); self.assertIsNotNone(c, (a, err))

    def test_b5_review_pr_may_approve_and_pat_passthrough_goes_through_the_guard(self):
        R = PROD
        for a in (["pr", "review", "1", "-R", R, "--approve"], ["api", "-X", "POST", f"repos/{R}/pulls/1/reviews", "-f", "event=APPROVE"],
                  ["api", f"repos/{R}/pulls/1/reviews", "-fevent=APPROVE"], ["api", "-X", "PUT", f"repos/{R}/pulls/1/reviews/9/dismissals", "-f", "message=x"],
                  self.gql('mutation{addPullRequestReview(input:{pullRequestId:"PROD_1", event:APPROVE}){clientMutationId}}')):
            for caller in (self.RP, self.ALLOW):
                _, c, err = self.wrap(a, caller=caller); self.assertIsNotNone(c, (a, caller)); self.assertIn("allowlisted caller", err)
            for caller in ([], self.OUT, ["node", "/x/scripts/loops/lifeline/report.mjs"]): self.assertEqual(self.wrap(a, caller=caller)[:2], (4, None), (a, caller))
        for a in (["pr", "close", "1", "-R", R], ["api", "-X", "PATCH", f"repos/{R}/pulls/1", "-f", "state=closed"], ["issue", "create", "-R", R, "-t", "x"],
                  self.gql('mutation{addPullRequestReview(input:{pullRequestId:"PROD_1"}){clientMutationId} closePullRequest(input:{pullRequestId:"PROD_1"}){clientMutationId}}')):
            rc, c, err = self.wrap(a, caller=self.RP); self.assertEqual((rc, c), (4, None), a); self.assertNotIn("allowlisted caller", err)   # PAT pass-through: guard first


class AcceptanceRound3(Base):
    """#214 round 3 (PM rulings 11-04): (1) the narrow outbound qa-fix/* update-branch exception, wrapper AND lib.gh; (3) review
    dismissals/events only for review-pr callers; (4) script_of = the script the interpreter executes (preloads skipped)"""
    wrap = Wrapper.wrap
    OUT = ["python3", "/x/panoptes/pi/bin/outbound.py", "--once"]
    GOOD = {"headRefName": "qa-fix/fixpr-1-a", "baseRefName": "main", "headRepository": {"name": "mivo-canvas-plugin"},
            "headRepositoryOwner": {"login": "xindong"}, "isCrossRepository": False}
    OK_CALLS = (["pr", "update-branch", "7", "-R", PROD], ["api", "-X", "PUT", f"repos/{PROD}/pulls/7/update-branch", "-f", "expected_head_sha=abc"],
                ["api", "-X", "PUT", f"repos/{PROD}/pulls/7/update-branch"])
    def look(self, pr=None, fail=False):
        seen = []
        def f(n):
            seen.append(n)
            if fail: raise RuntimeError("HTTP 502")
            return dict(self.GOOD, **(pr or {}))
        self.seen = seen; return mock.patch.object(lib, "ub_lookup", f)

    def test_r3_outbound_qa_fix_update_branch_allowed_wrapper(self):
        for a in self.OK_CALLS:
            with self.look():
                rc, c, err = self.wrap(a, caller=self.OUT)
            self.assertIsNotNone(c, (a, err)); self.assertEqual(c[1][1:], a); self.assertEqual(self.seen, [7])
        with self.look():   # repos/{owner}/{repo} filled from the cwd's origin = the product repo
            self.assertIsNotNone(self.wrap(["api", "-X", "PUT", "repos/{owner}/{repo}/pulls/7/update-branch"], cwd_url="https://github.com/xindong/mivo-canvas-plugin.git", caller=self.OUT)[1])

    def test_r3_outbound_qa_fix_update_branch_allowed_lib_gh(self):
        # outbound.py:723-724 h_update_branch -> lib.gh(["api", "-X", "PUT", .../update-branch, ("-f", "expected_head_sha=..")]): caller = this process
        for a in self.OK_CALLS[1:]:
            with self.look(), mock.patch.object(lib, "self_script", lambda: "/x/panoptes/pi/bin/outbound.py"), \
                 mock.patch.object(lib.subprocess, "run", lambda cmd, **k: types.SimpleNamespace(returncode=0, stdout="{}", stderr="")) as _:
                self.assertEqual(lib.gh(a, js=False), "{}")
            with self.look(), mock.patch.object(lib, "self_script", lambda: "/x/panoptes/pi/bin/prwatch.py"):
                with self.assertRaises(lib.RedLine): lib.gh(a, js=False)
            with self.look(fail=True), mock.patch.object(lib, "self_script", lambda: "/x/panoptes/pi/bin/outbound.py"):
                with self.assertRaises(lib.RedLine): lib.gh(a, js=False)

    def test_r3_update_branch_refusals(self):
        bad_pr = ({"headRefName": "fix/qa-1-x"}, {"headRefName": "main"}, {"baseRefName": "release"}, {"isCrossRepository": True},
                  {"headRepositoryOwner": {"login": "someone"}}, {"headRepository": {"name": "fork"}}, {"headRefName": None})
        for pr in bad_pr:
            for a in self.OK_CALLS:
                with self.look(pr): self.assertEqual(self.wrap(a, caller=self.OUT)[:2], (4, None), (a, pr))
        for a in self.OK_CALLS:
            with self.look(fail=True): self.assertEqual(self.wrap(a, caller=self.OUT)[:2], (4, None), a)   # lookup failure
            for caller in ([], ["python3", "/x/panoptes/pi/bin/prwatch.py"], ["python3", "/x/pi/bin/outbound.py.bak"], ["python3", "/x/notpi/bin/outbound.py2"],
                           ["node", "/x/review-pr/scripts/merge-reviewed-pr.mjs"], ["node", "/x/review-pr/scripts/signoff-hold.mjs"],
                           ["python3", "-c", "x", "/x/panoptes/pi/bin/outbound.py"], ["node", "--require", "/x/pi/bin/outbound.py", "/y/z.mjs"]):
                with self.look(): self.assertEqual(self.wrap(a, caller=caller)[:2], (4, None), (a, caller))
                self.assertEqual(self.seen, [], (a, caller))   # never looked up for another caller
        for a in (["pr", "update-branch", "7", "--rebase", "-R", PROD], ["pr", "update-branch", "--rebase=true", "7", "-R", PROD], ["pr", "update-branch", "-R", PROD],
                  ["pr", "update-branch", "qa-fix/x", "-R", PROD], ["pr", "update-branch", "7", "8", "-R", PROD],
                  ["api", "-X", "PUT", f"repos/{PROD}/pulls/7/update-branch", "-f", "update_method=rebase"],
                  ["api", "-X", "PUT", f"repos/{PROD}/pulls/7/update-branch", "-f", "expected_head_sha=a", "-f", "update_method=merge"],
                  ["api", "-X", "PUT", f"repos/{PROD}/pulls/7/update-branch", "--input", "-"], ["api", "-X", "POST", f"repos/{PROD}/pulls/7/update-branch"],
                  ["api", f"repos/{PROD}/pulls/7/update-branch", "-f", "expected_head_sha=a"], ["api", "-X", "PUT", f"repos/xindong/other/pulls/7/update-branch"],
                  ["api", "-X", "PUT", f"repos/{PROD}/merges"], ["api", "-X", "PUT", f"repos/{PROD}/pulls/7/merge"]):
            with self.look(), mock.patch.object(sys, "stdin", io.StringIO("{}")): self.assertEqual(self.wrap(a, caller=self.OUT)[:2], (4, None), a)
            with self.look(), mock.patch.object(lib, "self_script", lambda: "/x/panoptes/pi/bin/outbound.py"):
                with self.assertRaises(lib.RedLine): lib.gh(a, js=False)

    def test_r3_review_dismissals_and_events_only_for_review_pr(self):
        R = PROD; RP = ["node", "/x/review-pr/scripts/dismiss-own-review.mjs"]
        for a in (["api", "-X", "PUT", f"repos/{R}/pulls/1/reviews/9/dismissals", "-f", "message=x"], ["api", "-X", "POST", f"repos/{R}/pulls/1/reviews/9/events", "-f", "event=APPROVE"]):
            self.assertIsNotNone(self.wrap(a, caller=RP)[1], a)
            self.assertIsNone(lib.gh_product_write(a, R, "review-pr-dismiss-own-review"))
            for caller in ([], self.OUT, ["node", "/x/scripts/loops/lifeline/report.mjs"], ["node", "/x/scripts/loops/lifeline/tick.mjs"],
                           ["node", "--require", "/x/review-pr/scripts/dismiss-own-review.mjs", "/y/z.mjs"]):
                self.assertEqual(self.wrap(a, caller=caller)[:2], (4, None), (a, caller))
            for name in (None, "lifeline-report", "lifeline-tick", "panoptes-outbound"): self.assertIsNotNone(lib.gh_product_write(a, R, name), (a, name))

    def test_r3_script_of_skips_preloads(self):
        import gh_wrapper as W
        self.assertIsNone(W.allowlisted(["node", "--require", "/a/review-pr/scripts/signoff-hold.mjs", "x.mjs"]))
        self.assertTrue(W.script_of(["node", "--require", "/a/review-pr/scripts/signoff-hold.mjs", "/b/x.mjs"]).endswith("/b/x.mjs"))
        for c in (["node", "-r", "/a/review-pr/scripts/signoff-hold.mjs", "/b/x.mjs"], ["node", "--import", "/a/review-pr/scripts/signoff-hold.mjs", "/b/x.mjs"],
                  ["node", "--loader", "/a/review-pr/scripts/signoff-hold.mjs", "/b/x.mjs"], ["node", "--experimental-loader", "/a/review-pr/scripts/signoff-hold.mjs", "/b/x.mjs"],
                  ["node", "--import=/a/review-pr/scripts/signoff-hold.mjs", "/b/x.mjs"], ["node", "-r/a/review-pr/scripts/signoff-hold.mjs", "/b/x.mjs"]):
            self.assertEqual(W.script_of(c), "/b/x.mjs", c); self.assertIsNone(W.allowlisted(c), c)
        for c in (["node", "-e", "1", "/a/review-pr/scripts/signoff-hold.mjs"], ["node", "-p", "1", "/a/review-pr/scripts/signoff-hold.mjs"],
                  ["node", "--eval=1", "/a/review-pr/scripts/signoff-hold.mjs"], ["python3", "-m", "x", "/a/pi/bin/outbound.py"], ["python3", "-c", "1", "/a/pi/bin/outbound.py"],
                  ["python3", "-Im", "x", "/a/pi/bin/outbound.py"], ["bash", "-c", "x", "/a/verify.d/60-gh-auth.sh"], ["node", "-", "/a/review-pr/scripts/signoff-hold.mjs"]):
            self.assertIsNone(W.script_of(c), c); self.assertIsNone(W.allowlisted(c), c)
        self.assertEqual(W.allowlisted(["node", "--require", "/b/x.mjs", "/a/review-pr/scripts/signoff-hold.mjs"]), "review-pr-signoff-hold")
        self.assertEqual(W.script_of(["python3", "-u", "-W", "ignore", "/a/pi/bin/outbound.py"]), "/a/pi/bin/outbound.py")
        self.assertEqual(W.write_exceptions(["python3", "-X", "dev", "/a/pi/bin/outbound.py"]), W.PRODUCT_WRITE_EXCEPTIONS[0][2])


class AcceptanceRound4(Base):
    """#214 round 4: (A) a repeated repo selector, any spelling, refused (gh uses the last); (B) :owner/:repo/:branch placeholders =
    {owner}/{repo}/{branch}; (C) Actions writes only the three reruns; (D) pr ready --undo; (E) wrapper refuses alias set/import,
    extension writes/exec and auth token (except CALLER_ALLOWLIST callers)"""
    wrap = Wrapper.wrap
    OUT = AcceptanceRound3.OUT; look = AcceptanceRound3.look; GOOD = AcceptanceRound3.GOOD
    CALLERS = ([], ["node", "/x/foo.mjs"], OUT, ["node", "/x/review-pr/scripts/signoff-hold.mjs"])
    def dups(self, verb, n="1"):
        O = "xindong/other"
        return ([*verb, n, "-R", PAN, "-R", PROD], [*verb, n, "--repo", PAN, "--repo", PROD], [*verb, n, f"-R{PAN}", "-R", PROD],
                [*verb, n, f"-R={PAN}", f"--repo={PROD}"], [*verb, n, "--repo", PAN, f"-R{PROD}"], [*verb, n, "-R", PROD, "-R", O],
                [*verb, n, "-R", O, "-R", PROD], [*verb, n, "-R", PROD, "-R", PROD], [*verb, f"https://github.com/{PROD}/pull/{n}", "-R", PAN],
                [*verb, f"https://github.com/{PAN}/pull/{n}", "-R", PROD])

    def test_r4_a_repeated_repo_selector_refused_red_line_and_product_write(self):
        for a in self.dups(["pr", "merge"]) + self.dups(["pr", "close"]) + self.dups(["pr", "view"]) + self.dups(["issue", "comment"]):
            self.assertIsNotNone(lib.gh_repo_selector_dup(a), a); self.assertIsNone(lib.gh_target_repo(a), a)
            self.assertIn("more than one repo selector", lib.gh_red_line(a) or "", a)
            self.assertIn("more than one repo selector", lib.gh_product_write(a, None, "review-pr-signoff-hold", excused=frozenset({("issue", "comment")})) or "", a)
            for c in self.CALLERS:
                rc, call, err = self.wrap(a, caller=c); self.assertEqual((rc, call), (4, None), (a, c))
            with self.assertRaises(lib.RedLine): lib.gh(a)
        # a single selector, and URL + the same repo, are unchanged
        for a in (["pr", "view", "1", "-R", PROD], ["pr", "view", "1", f"--repo={PAN}"], ["pr", "view", f"https://github.com/{PROD}/pull/1", "-R", PROD]):
            self.assertIsNone(lib.gh_repo_selector_dup(a), a); self.assertIsNone(lib.gh_red_line(a), a)

    def test_r4_a_update_branch_lookup_cannot_be_spoofed(self):
        for a in self.dups(["pr", "update-branch"], "5"):
            with self.look():
                n, why = lib.update_branch_pr(a); self.assertIsNone(n, a); self.assertIn("more than one repo selector", why or "", a)
                rc, call, err = self.wrap(a, caller=self.OUT); self.assertEqual((rc, call), (4, None), a)
                self.assertEqual(self.seen, [], "no lookup is made for an ambiguous target")
        with self.look():
            self.assertIsNotNone(self.wrap(["pr", "update-branch", "5", "-R", PROD], caller=self.OUT)[1])

    def test_r4_b_colon_placeholders_resolve_to_the_hint_repo(self):
        url = "https://github.com/xindong/mivo-canvas-plugin.git"
        writes = (["api", "-XPUT", "repos/:owner/:repo/pulls/1/merge"], ["api", "-XPUT", "repos/:owner/:repo/pulls/1/update-branch"],
                  ["api", "-XPATCH", "repos/:owner/:repo/git/refs/heads/:branch", "-fsha=x", "-Fforce=true"],
                  ["api", "-XPATCH", "repos/:owner/:repo/git/refs/heads/main", "-fsha=x", "-Fforce=true"],
                  ["api", "-XPOST", "repos/:owner/:repo/issues", "-ftitle=x"], ["api", "-XPOST", "repos/xindong/:repo/issues", "-ftitle=x"],
                  ["api", "-XPATCH", "repos/:owner/:repo/pulls/1", "-fbody=x"])
        for a in writes:
            self.assertIsNone(lib.gh_target_repo(a), a)
            self.assertEqual(lib.api_target(lib.gh_api_parse(a), PROD), PROD); self.assertIsNone(lib.api_target(lib.gh_api_parse(a)), a)
            self.assertTrue(lib.gh_red_line(a, PROD) or lib.gh_product_write(a, PROD), a)
            self.assertTrue(lib.gh_red_line(a) or lib.gh_product_write(a), ("unresolvable", a))
            for c in self.CALLERS[:2] + self.CALLERS[3:]:
                for env, cw in (({"GH_REPO": PROD}, ""), ({}, url), ({}, "")):
                    rc, call, _ = self.wrap(a, env=env, cwd_url=cw, caller=c); self.assertEqual((rc, call), (4, None), (a, c, env, cw))
        # own repo through the placeholder: still allowed; reads allowed
        self.assertIsNotNone(self.wrap(["api", "-XPOST", "repos/:owner/:repo/issues", "-ftitle=x"], env={"GH_REPO": PAN})[1])
        self.assertIsNotNone(self.wrap(["api", "repos/:owner/:repo/pulls"], env={"GH_REPO": PROD})[1])

    def test_r4_c_actions_writes_only_reruns(self):
        R = f"repos/{PROD}/actions"
        for ep in ("runs/12/rerun", "runs/12/rerun-failed-jobs", "jobs/34/rerun"):
            self.assertIsNone(lib.gh_product_write(["api", "-XPOST", f"{R}/{ep}"], PROD), ep)
            self.assertIsNotNone(self.wrap(["api", "-XPOST", f"{R}/{ep}"], caller=["node", "/x/foo.mjs"])[1], ep)
            self.assertIsNotNone(lib.gh_product_write(["api", "-XDELETE", f"{R}/{ep}"], PROD), ep)
        for a in (["api", "-XPOST", f"{R}/workflows/x.yml/dispatches", "-fref=main"], ["api", "-XPUT", f"{R}/secrets/X", "-fencrypted_value=x"],
                  ["api", "-XPOST", f"{R}/variables", "-fname=X", "-fvalue=y"], ["api", "-XPATCH", f"{R}/variables/X", "-fvalue=y"],
                  ["api", "-XPUT", f"{R}/permissions", "-fenabled=false"], ["api", "-XPOST", f"{R}/runs/12/cancel"], ["api", "-XPOST", f"{R}/runs/12/force-cancel"],
                  ["api", "-XDELETE", f"{R}/runs/12"], ["api", "-XDELETE", f"{R}/caches?key=x"], ["api", "-XPUT", f"{R}/workflows/1/disable"],
                  ["api", "-XPOST", f"{R}/runs/12/approve"], ["api", "-XPOST", f"{R}/runs/12/rerun/x"], ["api", "-XPOST", "repos/:owner/:repo/actions/workflows/x/dispatches"]):
            self.assertIsNotNone(lib.gh_product_write(a, PROD), a)
            for c in self.CALLERS: self.assertEqual(self.wrap(a, env={"GH_REPO": PROD}, caller=c)[:2], (4, None), (a, c))
        for a in (["api", f"{R}/runs"], ["api", f"{R}/secrets"], ["api", "-XGET", f"{R}/runs/12/jobs"]):
            self.assertIsNone(lib.gh_product_write(a, PROD), a)

    def test_r4_d_pr_ready_undo_allowed(self):
        for a in (["pr", "ready", "1", "--undo", "-R", PROD], ["pr", "ready", "--undo", "1", "-R", PROD], ["pr", "ready", "1", "-R", PROD]):
            self.assertIsNone(lib.gh_red_line(a), a); self.assertIsNone(lib.gh_product_write(a), a)
            self.assertIsNotNone(self.wrap(a, caller=["node", "/x/foo.mjs"])[1], a)

    def test_r4_e_wrapper_refuses_alias_extension_and_auth_token(self):
        import gh_wrapper as W
        AL = ["node", "/x/review-pr/scripts/signoff-hold.mjs"]
        for a in (["alias", "set", "m", "pr merge"], ["alias", "import", "f.yml"], ["alias", "import", "-"], ["extension", "install", "evil/gh-x"],
                  ["extension", "upgrade", "--all"], ["extension", "exec", "x", "merge"], ["ext", "install", "evil/gh-x"], ["extensions", "upgrade", "x"],
                  ["extension", "create", "x"], ["extension", "remove", "x"], ["extension"]):
            for c in self.CALLERS:
                rc, call, err = self.wrap(a, caller=c); self.assertEqual((rc, call), (4, None), (a, c)); self.assertIn("refused", err)
        for a in (["auth", "token"], ["auth", "token", "--hostname", "github.com"], ["auth", "status", "--show-token"], ["auth", "status", "-t"]):
            for c in self.CALLERS[:3]: self.assertEqual(self.wrap(a, caller=c)[:2], (4, None), (a, c))
            self.assertIsNotNone(W.cli_refusal(a, None), a); self.assertIsNone(W.cli_refusal(a, "review-pr-signoff-hold"), a)
            rc, call, _ = self.wrap(a, caller=AL); self.assertIsNotNone(call, a)   # allowlisted: the real gh, PAT env
        for a in (["alias", "list"], ["alias", "delete", "m"], ["extension", "list"], ["ext", "search", "x"], ["auth", "status"]):
            self.assertIsNone(W.cli_refusal(a, None), a); self.assertIsNotNone(self.wrap(a, caller=["node", "/x/foo.mjs"])[1], a)


    def test_config_set_and_auth_writes_only_for_allowlisted_callers(self):
        import gh_wrapper as W
        AL = ["node", "/x/review-pr/scripts/signoff-hold.mjs"]; OTHER = ["node", "/x/foo.mjs"]
        writes = (["config", "set", "git_protocol", "https"], ["config", "set", "-h", "github.com", "editor", "vim"], ["auth", "login"],
                  ["auth", "login", "--with-token"], ["auth", "setup-git"], ["auth", "switch", "-u", "x"], ["auth", "refresh", "-s", "repo"])
        for a in writes:
            self.assertIsNotNone(W.cli_refusal(a, None), a); self.assertIsNone(W.cli_refusal(a, "review-pr-signoff-hold"), a)
            for c in ([], OTHER, ["python3", "/x/pi/bin/outbound.py"]):
                rc, call, err = self.wrap(a, caller=c); self.assertEqual((rc, call), (4, None), (a, c)); self.assertIn("refused", err)
            self.assertIsNotNone(self.wrap(a, caller=AL)[1], a)   # allowlisted: the real gh
        for a in (["config", "get", "git_protocol"], ["config", "list"], ["config", "list", "-h", "github.com"], ["auth", "status"],
                  ["auth", "status", "--hostname", "github.com"]):
            self.assertIsNone(W.cli_refusal(a, None), a); self.assertIsNotNone(self.wrap(a, caller=OTHER)[1], a)

    def test_flags_before_verb_cannot_bypass_auth_config_alias_extension(self):
        # #222 acceptance: gh skips flags (and values) before the verb; the verb is not simply argv[1]
        import gh_wrapper as W
        AL = ["node", "/x/review-pr/scripts/signoff-hold.mjs"]; OTHER = ["node", "/x/foo.mjs"]
        refused = (["auth", "-h", "github.com", "login", "--with-token"], ["auth", "--hostname", "github.com", "login", "--with-token"],
                   ["auth", "-h", "github.com", "setup-git"], ["auth", "--hostname=github.com", "setup-git"], ["auth", "-hgithub.com", "switch"],
                   ["config", "--host", "github.com", "set", "editor", "x"], ["config", "-h", "github.com", "set", "editor", "x"],
                   ["auth", "-h", "github.com", "status", "--show-token"], ["auth", "-h", "github.com", "status", "-at"], ["auth", "-h", "github.com", "token"],
                   ["auth", "--bogus", "status"], ["auth", "-x", "github.com", "status"], ["auth", "-h"], ["auth", "--", "status"], ["config", "--weird", "get", "x"],
                   ["auth", "logout"], ["auth", "git-credential", "approve"], ["auth", "-h", "github.com", "git-credential"], ["auth"], ["config", "clear-cache"], ["config"],
                   ["alias", "-h", "x", "set", "m", "pr merge"], ["alias", "--bogus", "import", "f.yml"],
                   ["extension", "-R", "o/r", "install", "evil/gh-x"], ["ext", "--help", "install", "evil/gh-x"], ["extension", "--bogus", "list"],
                   ["--help", "auth", "login"], ["-R", "o/r", "config", "set", "x", "y"])
        for a in refused:
            self.assertIsNotNone(W.cli_refusal(a, None), a)
            for c in ([], OTHER, ["python3", "/x/pi/bin/outbound.py"]):
                rc, call, err = self.wrap(a, caller=c); self.assertEqual((rc, call), (4, None), (a, c)); self.assertIn("refused", err)
        for a in refused[:10]: self.assertIsNone(W.cli_refusal(a, "review-pr-signoff-hold"), a); self.assertIsNotNone(self.wrap(a, caller=AL)[1], a)
        for a in (["auth", "git-credential", "store"], ["auth", "-h", "github.com", "git-credential", "erase"]):   # F2: silent no-op, not sent
            self.assertIsNone(W.cli_refusal(a, None), a)
            for c in ([], OTHER): self.assertEqual(self.wrap(a, caller=c), (0, None, ""), (a, c))
        for a in (["alias", "-h", "x", "set", "m", "x"], ["ext", "--help", "install", "x"]): self.assertIsNotNone(W.cli_refusal(a, "review-pr-signoff-hold"), a)
        for a in (["auth", "-h", "github.com", "status"], ["auth", "--hostname", "github.com", "status", "-a"], ["config", "--host", "github.com", "get", "editor"],
                  ["config", "-h", "github.com", "list"], ["alias", "--help", "list"], ["extension", "-R", "o/r", "list"], ["auth", "--help", "status"]):
            self.assertIsNone(W.cli_refusal(a, None), a); self.assertIsNotNone(self.wrap(a, caller=OTHER)[1], a)


class WrapperGitCredential(Base):
    """#222: `credential.helper=!gh auth git-credential` runs through the wrapper for every caller: get answers, store/erase silent no-op (F2), other ops refused"""
    Q = "protocol=https\nhost=github.com\npath={}\n"
    OTHER = ["git", "credential", "fill"]

    def cred(self, argv, req, caller=None):
        import gh_wrapper as W
        calls, fed, out, err = [], [], io.StringIO(), io.StringIO()
        with mock.patch.object(W, "refeed_stdin", fed.append), redirect_stdout(out), redirect_stderr(err):
            rc = W.main(argv, env={"PATH": "", "PRAISE_QA_REAL_GH": "/real/gh"}, execve=lambda p, a, e: calls.append((p, a, e)),
                        run=lambda cmd, **k: types.SimpleNamespace(returncode=0, stdout=f"https://github.com/{PROD}.git", stderr=""),
                        caller=list(caller or self.OTHER), stdin=req)
        return rc, (calls[-1] if calls else None), fed, out.getvalue(), err.getvalue()

    def test_get_allowed_with_flags_before_verb_store_erase_refused(self):
        import gh_wrapper as W
        for a in (["auth", "git-credential", "get"], ["auth", "-h", "github.com", "git-credential", "get"], ["auth", "--hostname=github.com", "git-credential", "get"]):
            self.assertIsNone(W.cli_refusal(a, None), a)
        # F2: store/erase from a non-allowlisted caller: exit 0, no real gh call, no stdout/stderr, nothing re-fed, no mint
        self.on("gh-app-wrapper-enabled")
        for a in (["auth", "git-credential", "store"], ["auth", "git-credential", "erase"], ["auth", "-h", "github.com", "git-credential", "erase"],
                  ["auth", "git-credential", "--hostname", "github.com", "store"], ["auth", "git-credential", "-h", "erase", "store"]):
            self.assertIsNone(W.cli_refusal(a, None), a); self.assertTrue(W.git_credential_noop(a), a)
            rc, call, fed, out, err = self.cred(a, self.Q.format(PAN + ".git") + "username=u\npassword=p\n")
            self.assertEqual((rc, call, fed, out, err), (0, None, [], "", ""), a); self.assertEqual(self.minted, [], a)
        # missing / unknown op: still exit 4
        for a in (["auth", "git-credential"], ["auth", "git-credential", "approve"], ["auth", "git-credential", "-h", "store"], ["auth", "git-credential", "--hostname=store"]):
            self.assertIsNotNone(W.cli_refusal(a, None), a); self.assertFalse(W.git_credential_noop(a), a)
            rc, call, fed, out, err = self.cred(a, self.Q.format(PAN + ".git")); self.assertEqual((rc, call), (4, None), a); self.assertIn("refused", err)
        # allowlisted callers: unchanged, store/erase go to the real gh with the PAT (not the no-op)
        for a in (["auth", "git-credential", "store"], ["auth", "git-credential"]):
            self.assertIsNone(W.cli_refusal(a, "review-pr-signoff-hold"), a)
        rc, call, *_ = self.cred(["auth", "git-credential", "store"], "x", caller=["node", "/a/review-pr/scripts/signoff-hold.mjs"])
        self.assertEqual(call[1][1:], ["auth", "git-credential", "store"])

    def test_f3_credential_op_skips_hostname_and_its_value(self):
        import gh_wrapper as W
        for rest, op in ((["get"], "get"), (["-h", "github.com", "get"], "get"), (["--hostname", "github.com", "store"], "store"),
                         (["--hostname=github.com", "erase"], "erase"), (["-hgithub.com", "get"], "get"), (["-h", "get", "store"], "store"),
                         (["--hostname", "erase"], ""), (["-h"], ""), (["--hostname=get"], ""), (["-hget"], ""), ([], ""), (["--x", "get"], "get")):
            self.assertEqual(W.credential_op(rest), op, rest)
        self.assertFalse(W.git_credential_get(["auth", "git-credential", "-h", "get"]))   # `get` here is the host, not the op
        self.assertTrue(W.git_credential_get(["auth", "git-credential", "-h", "github.com", "get"]))

    def test_f1_app_path_needs_use_http_path(self):
        """credential.useHttpPath: git sends path= only with it; without a path the request never reaches the App token"""
        import gh_wrapper as W
        self.assertEqual(W.credential_repo(self.Q.format(PAN + ".git")), PAN)
        self.assertIsNone(W.credential_repo("protocol=https\nhost=github.com\n"))
        self.on("gh-app-wrapper-enabled")
        rc, call, fed, out, _ = self.cred(["auth", "git-credential", "get"], self.Q.format(PAN + ".git"))
        self.assertIn("password=" + FAKE[PAN], out); self.assertIsNone(call)
        self.minted.clear(); rc, call, fed, out, _ = self.cred(["auth", "git-credential", "get"], "protocol=https\nhost=github.com\n")
        self.assertEqual(out, ""); self.assertEqual(self.minted, []); self.assertEqual(call[1][1:], ["auth", "git-credential", "get"])

    def test_get_flag_off_passes_stdin_to_real_gh_without_app_token(self):
        req = self.Q.format(PAN + ".git")
        rc, call, fed, out, _ = self.cred(["auth", "git-credential", "get"], req)
        self.assertEqual(call[1], ["/real/gh", "auth", "git-credential", "get"]); self.assertEqual(fed, [req]); self.assertEqual(out, "")
        self.assertNotIn("GH_TOKEN", call[2]); self.assertEqual(self.minted, [])

    def test_get_flag_on_app_token_for_request_repo_not_cwd(self):
        self.on("gh-app-wrapper-enabled")
        rc, call, fed, out, _ = self.cred(["auth", "git-credential", "get"], self.Q.format(PAN + ".git"))   # cwd is PROD
        self.assertEqual((rc, call), (0, None)); self.assertIn(f"password={FAKE[PAN]}\n", out); self.assertEqual(self.minted, [PAN])
        # repo with no installation / non-github host / no path: never the PRODUCT fallback, the real gh (box login) answers
        for req in (self.Q.format("aj0928/x.git"), "protocol=https\nhost=github.com\n", "protocol=https\nhost=example.com\npath=a/b\n"):
            self.minted.clear(); rc, call, fed, out, _ = self.cred(["auth", "git-credential", "get"], req)
            self.assertEqual(call[1][1:], ["auth", "git-credential", "get"], req); self.assertEqual(fed, [req]); self.assertEqual(out, "")
            self.assertNotIn("GH_TOKEN", call[2]); self.assertNotIn(PROD, self.minted)


class WrapperGitCredentialE2E(unittest.TestCase):
    """real git -> credential.helper=!<wrapper> auth git-credential -> mock real gh, against a local https smart-http remote"""
    def setUp(self):
        import shutil, ssl, threading, http.server, base64
        if not shutil.which("openssl") or not os.path.exists(os.path.join(subprocess.check_output(["git", "--exec-path"], text=True).strip(), "git-http-backend")):
            self.skipTest("needs openssl + git-http-backend")
        self.d = d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d, True)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=127.0.0.1", "-days", "1",
                        "-keyout", f"{d}/k.pem", "-out", f"{d}/c.pem"], check=True, capture_output=True)
        subprocess.run(["git", "init", "-q", "--bare", f"{d}/srv/r.git"], check=True)
        subprocess.run(["git", "-C", f"{d}/srv/r.git", "config", "http.receivepack", "true"], check=True)
        backend = os.path.join(subprocess.check_output(["git", "--exec-path"], text=True).strip(), "git-http-backend")
        want = "Basic " + base64.b64encode(b"mockuser:MOCKPW").decode()
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def go(self):
                if self.headers.get("Authorization") != want:
                    self.send_response(401); self.send_header("WWW-Authenticate", 'Basic realm="t"'); self.send_header("Content-Length", "0"); self.end_headers(); return
                path, _, qs = self.path.partition("?")
                n = int(self.headers.get("Content-Length") or 0); body = self.rfile.read(n) if n else b""
                env = {"PATH": os.environ.get("PATH", ""), "GIT_PROJECT_ROOT": f"{d}/srv", "GIT_HTTP_EXPORT_ALL": "1", "PATH_INFO": path,
                       "QUERY_STRING": qs, "REQUEST_METHOD": self.command, "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                       "CONTENT_LENGTH": str(n), "REMOTE_USER": "mockuser", "REMOTE_ADDR": "127.0.0.1", "GIT_PROTOCOL": self.headers.get("Git-Protocol", "")}
                r = subprocess.run([backend], input=body, env=env, capture_output=True)
                head, _, out = r.stdout.partition(b"\r\n\r\n")
                if not out and b"\n\n" in r.stdout: head, _, out = r.stdout.partition(b"\n\n")
                status = 200; hdrs = []
                for line in head.decode().splitlines():
                    k, _, v = line.partition(":")
                    if k.lower() == "status": status = int(v.split()[0])
                    elif k: hdrs.append((k, v.strip()))
                self.send_response(status)
                for k, v in hdrs: self.send_header(k, v)
                self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
            do_GET = do_POST = go
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(f"{d}/c.pem", f"{d}/k.pem")
        self.srv.socket = ctx.wrap_socket(self.srv.socket, server_side=True)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start(); self.addCleanup(self.srv.shutdown)
        self.url = f"https://127.0.0.1:{self.srv.server_address[1]}/r.git"
        self.log = f"{d}/gh.log"
        with open(f"{d}/realgh", "w") as f:   # mock real gh: answers get with a fixed credential, logs argv + stdin
            f.write(f"#!/bin/sh\nin=$(cat)\nprintf '%s|%s\\n' \"$*\" \"$(echo \"$in\" | tr '\\n' ' ')\" >> {self.log}\n"
                    "[ \"$3\" = get ] && printf 'username=mockuser\\npassword=MOCKPW\\n'\nexit 0\n")
        with open(f"{d}/gh", "w") as f:   # what /usr/local/bin/gh would be once installed: the wrapper
            f.write(f"#!/bin/sh\nexec python3 {os.path.join(BIN, 'gh_wrapper.py')} \"$@\"\n")
        os.chmod(f"{d}/realgh", 0o755); os.chmod(f"{d}/gh", 0o755)
        self.env = {"PATH": os.environ.get("PATH", ""), "HOME": d, "PRAISE_QA_REAL_GH": f"{d}/realgh", "PI_STATE": f"{d}/state",
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0", "PYTHONDONTWRITEBYTECODE": "1"}
        self.cfg = ["-c", f"credential.helper=!{d}/gh auth git-credential", "-c", "http.sslVerify=false", "-c", "credential.useHttpPath=true"]

    def git(self, *a, cwd=None, stdin=None):
        return subprocess.run(["git", *self.cfg, *a], cwd=cwd or self.d, env=self.env, input=stdin, capture_output=True, text=True, timeout=60)

    def test_fill_and_push_succeed_store_erase_silent_noop(self):
        r = self.git("credential", "fill", stdin=f"url={self.url}\n\n")
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("password=MOCKPW", r.stdout)
        w = f"{self.d}/w"; subprocess.run(["git", "init", "-q", w], check=True, env=self.env)
        for c in (["commit", "-q", "--allow-empty", "-m", "x"],):
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *c], cwd=w, check=True, env=self.env)
        r = self.git("push", "--dry-run", self.url, "HEAD:refs/heads/main", cwd=w)
        self.assertEqual(r.returncode, 0, r.stderr); self.assertNotIn("refused", r.stderr); self.assertNotIn("git-credential store", r.stderr)
        r = self.git("push", self.url, "HEAD:refs/heads/main", cwd=w)   # a real push: git then calls `store`: a silent no-op (F2)
        self.assertEqual(r.returncode, 0, r.stderr); self.assertNotIn("refused", r.stderr); self.assertNotIn("gh-wrapper", r.stderr)
        log = open(self.log).read()
        self.assertIn("auth git-credential get|", log); self.assertIn("host=127.0.0.1", log)   # stdin reached the real gh
        self.assertNotIn("store", log); self.assertNotIn("erase", log)
        for op in ("store", "erase"):
            r = subprocess.run([f"{self.d}/gh", "auth", "git-credential", op], input="protocol=https\nhost=127.0.0.1\nusername=u\npassword=p\n\n",
                               env=self.env, capture_output=True, text=True, timeout=60)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""), op)
        r = subprocess.run([f"{self.d}/gh", "auth", "git-credential", "approve"], input="", env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 4); self.assertIn("refused", r.stderr)
        self.assertNotIn("store", open(self.log).read()); self.assertNotIn("approve", open(self.log).read())

    def test_f1_without_use_http_path_no_path_reaches_the_helper(self):
        """real git: with credential.useHttpPath=true the helper sees path=r.git; without it, no path (-> never the App path)"""
        r = self.git("credential", "fill", stdin=f"url={self.url}\n\n")
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("path=r.git", open(self.log).read().splitlines()[-1])
        self.cfg = [c for c in self.cfg if c != "credential.useHttpPath=true"]; self.cfg = [x for i, x in enumerate(self.cfg) if not (x == "-c" and i == len(self.cfg) - 1)]
        r = self.git("credential", "fill", stdin=f"url={self.url}\n\n")
        self.assertEqual(r.returncode, 0, r.stderr); self.assertNotIn("path=", open(self.log).read().splitlines()[-1])


if __name__ == "__main__": unittest.main()
