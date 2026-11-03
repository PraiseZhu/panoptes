#!/usr/bin/env python3
"""lib.gh (11-03 prwatch:error:drafts): the real gh stderr leads the error (prwatch keeps only 200 chars), and a READ that failed
with a GitHub 5xx / network blip is retried once; writes and real errors (404, auth) are never retried.
A fake `gh` on PATH, no network. Run: cd bin && python3 ../test/test_lib_gh_retry.py"""
import json, os, stat, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import lib  # noqa: E402

FAKE = """#!/bin/sh
n=$(cat "$GHF_DIR/n" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "$GHF_DIR/n"
if [ "$n" -le "$GHF_FAILS" ]; then printf '%s\\n' "$GHF_ERR" >&2; exit 1; fi
echo '[{"number": 1}]'
"""
STDERR_502 = 'Post "https://api.github.com/graphql": HTTP 502: Bad Gateway (https://api.github.com/graphql)'
LONG = ["pr", "list", "-R", "xindong/mivo-canvas-plugin", "--author", "PraiseZhu", "--draft", "--state", "open", "--limit", "50",
        "--json", "number,headRefOid,headRefName,body,statusCheckRollup,mergeStateStatus"]   # the 10:44 drafts call

class GhRetry(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); f = f"{self.d}/gh"
        with open(f, "w") as fh: fh.write(FAKE)
        os.chmod(f, os.stat(f).st_mode | stat.S_IEXEC)
        self.env = {k: os.environ.get(k) for k in ("PATH", "GHF_DIR", "GHF_FAILS", "GHF_ERR")}
        os.environ.update(PATH=f"{self.d}:{os.environ['PATH']}", GHF_DIR=self.d)
        self.sleep = getattr(lib, "time", None)
        if self.sleep is not None: lib.time = type("T", (), {"sleep": staticmethod(lambda s: None)})
    def tearDown(self):
        for k, v in self.env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        if self.sleep is not None: lib.time = self.sleep
    def fake(self, fails, err): os.environ.update(GHF_FAILS=str(fails), GHF_ERR=err)
    def calls(self):
        with open(f"{self.d}/n") as f: return int(f.read())

    def test_read_5xx_retried_once(self):
        self.fake(1, STDERR_502)
        self.assertEqual(lib.gh(LONG), [{"number": 1}]); self.assertEqual(self.calls(), 2)
    def test_real_stderr_survives_prwatch_cut(self):
        self.fake(9, STDERR_502)
        with self.assertRaises(RuntimeError) as cm: lib.gh(LONG)
        self.assertIn("HTTP 502: Bad Gateway", str(cm.exception)[:200])   # prwatch.main keeps str(e)[:200]
        self.assertEqual(self.calls(), 2)                                   # one retry only
    def test_write_never_retried(self):
        self.fake(9, STDERR_502)
        with self.assertRaises(RuntimeError): lib.gh(["pr", "create", "-R", "o/r", "--title", "t"], js=False)
        with self.assertRaises(RuntimeError): lib.gh(["api", "-X", "POST", "repos/o/r/issues"])
        self.assertEqual(self.calls(), 2)   # one call each
    def test_real_error_not_retried(self):
        self.fake(1, "GraphQL: Could not resolve to a Repository with the name 'o/r'. (repository)")
        with self.assertRaises(RuntimeError) as cm: lib.gh(LONG)
        self.assertIn("Could not resolve", str(cm.exception)[:200]); self.assertEqual(self.calls(), 1)

if __name__ == "__main__": unittest.main()
