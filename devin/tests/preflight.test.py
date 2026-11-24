"""Tests of PraiseZhu/panoptes#312: routing.json + resolve-mode.py, gh_app_jwt, the mergewatch / catchup Preflight scripts and
their self-contained bundles (run against a mock GitHub REST server with temporary EVENT/STATE/LAST_RUN/OUTPUT files)."""
import ast
import base64
import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEVIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFLIGHT = os.path.join(DEVIN, "preflight")
DIST = os.path.join(PREFLIGHT, "dist")
RESOLVE = os.path.join(DEVIN, "bin", "resolve-mode.py")
BUILD = os.path.join(DEVIN, "bin", "build-preflight.py")
ROUTING = os.path.join(DEVIN, "conf", "routing.json")
MIVO = "xindong/mivo-canvas-plugin"
DATA = "PraiseZhu/grokbot-box"
STABLE = ["normal", "fast", "fusion", "lite", "ultra", "fast-ultra"]
NOW = dt.datetime(2026, 11, 23, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, PREFLIGHT)
resolve_mode = load("resolve_mode", RESOLVE)
gh_app_jwt = load("gh_app_jwt", os.path.join(PREFLIGHT, "gh_app_jwt.py"))

KEYS = tempfile.mkdtemp(prefix="preflight-keys-")


def openssl(*args, data=None):
    return subprocess.run(["openssl", *args], input=data, capture_output=True, check=True).stdout


openssl("genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", os.path.join(KEYS, "pkcs8.pem"))
openssl("rsa", "-in", os.path.join(KEYS, "pkcs8.pem"), "-traditional", "-out", os.path.join(KEYS, "pkcs1.pem"))
PEMS = {}
for _kind in ("pkcs1", "pkcs8"):
    with open(os.path.join(KEYS, f"{_kind}.pem"), encoding="ascii") as _f:
        PEMS[_kind] = _f.read()
KEY_B64 = base64.b64encode(PEMS["pkcs8"].encode()).decode()


def b64url_decode(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def iso(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(n):
    return f"a{n:039d}"


def pull(n, merged_ago_s, base="main", merge_sha=None, merged=True):
    t = NOW - merged_ago_s
    return {"number": n, "merged_at": iso(t) if merged else None, "updated_at": iso(t), "base": {"ref": base},
            "merge_commit_sha": merge_sha or sha(n)}


def report(n, tested_sha, verdict="clean", merged_sha=None):
    doc = {"prs": [{"number": n, "verdict": verdict, "tested_sha": None if verdict == "docs-only" else tested_sha}]}
    if merged_sha:
        doc["merged_sha"] = merged_sha
    return doc


class MockGitHub:
    """GitHub REST stand-in: App token exchange, matching-refs, closed pulls, data branch tree and blobs."""

    def __init__(self):
        self.fx = {"heads": {"main": "c" * 40}, "pulls": [], "reports": {}, "runs": {}, "fail": False}
        self.requests = []
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, code, doc):
                body = json.dumps(doc).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                mock.requests.append(("POST", self.path))
                m = re.fullmatch(r"/app/installations/(\d+)/access_tokens", self.path)
                auth = self.headers.get("Authorization", "")
                if mock.fx["fail"] or not m or not auth.startswith("Bearer ") or auth.count(".") != 2:
                    return self.reply(500 if mock.fx["fail"] else 401, {"message": "bad"})
                self.reply(201, {"token": f"ghs_mocktoken_{m.group(1)}"})

            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                path, query = urllib.parse.unquote(url.path), urllib.parse.parse_qs(url.query)
                mock.requests.append(("GET", path))
                if mock.fx["fail"]:
                    return self.reply(502, {"message": "upstream down"})
                owner = path.split("/")[2] if path.startswith("/repos/") else ""
                want = {"xindong": "183272688", "PraiseZhu": "183272732"}.get(owner)
                if self.headers.get("Authorization") != f"token ghs_mocktoken_{want}":
                    return self.reply(401, {"message": "wrong installation token"})
                if path.startswith(f"/repos/{MIVO}/git/matching-refs/heads/"):
                    prefix = path[len(f"/repos/{MIVO}/git/matching-refs/"):]
                    refs = [{"ref": f"refs/heads/{b}", "object": {"sha": s}}
                            for b, s in sorted(mock.fx["heads"].items()) if f"heads/{b}".startswith(prefix)]
                    return self.reply(200, refs)
                if path == f"/repos/{MIVO}/pulls":
                    page = int(query.get("page", ["1"])[0])
                    return self.reply(200, mock.fx["pulls"][(page - 1) * 100:page * 100])
                if path == f"/repos/{DATA}/git/trees/devin:box/workspace/mivo-qa/reports/merged":
                    tree = [{"path": name, "type": "blob", "sha": f"blob-{name}"} for name in sorted(mock.fx["reports"])]
                    return self.reply(200, {"tree": tree, "truncated": False})
                if path.startswith(f"/repos/{DATA}/git/blobs/blob-"):
                    doc = mock.fx["reports"].get(path.split("blob-", 1)[1])
                    raw = doc if isinstance(doc, str) else json.dumps(doc)
                    return self.reply(200, {"content": base64.b64encode(raw.encode()).decode(), "encoding": "base64"})
                prefix = f"/repos/{DATA}/contents/box/workspace/mivo-qa/pi/out/devin/runs/"
                if path.startswith(prefix) and query.get("ref") == ["devin"]:
                    record = mock.fx["runs"].get(path[len(prefix):])
                    if isinstance(record, int):
                        return self.reply(record, {"message": "run record unavailable"})
                    if record is not None:
                        raw = json.dumps(record).encode()
                        return self.reply(200, {"content": base64.b64encode(raw).decode(), "encoding": "base64"})
                self.reply(404, {"message": "Not Found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def count(self, needle):
        return sum(1 for _, p in self.requests if needle in p)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class PreflightCase(unittest.TestCase):
    def setUp(self):
        self.gh = MockGitHub()
        self.tmp = tempfile.mkdtemp(prefix="preflight-run-")
        self.files = {k: os.path.join(self.tmp, f"{k.lower()}.json") for k in ("EVENT_FILE", "STATE_FILE", "LAST_RUN_FILE",
                                                                               "OUTPUT_FILE")}
        with open(self.files["EVENT_FILE"], "w") as f:
            json.dump({"trigger": "schedule"}, f)

    def tearDown(self):
        self.gh.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, script, now=NOW, last_run=None, cwd=None, isolated=False, args=()):
        if os.path.exists(self.files["OUTPUT_FILE"]):
            os.remove(self.files["OUTPUT_FILE"])
        with open(self.files["LAST_RUN_FILE"], "w") as f:
            json.dump(last_run or {"items": [], "truncated": False}, f)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PRAISEDEVIN_PRIVATE_KEY_B64": KEY_B64,
               "GITHUB_API_URL": self.gh.url, "PREFLIGHT_NOW": str(now), **self.files}
        self.gh.requests.clear()
        cmd = [sys.executable, *(["-I"] if isolated else []), script, *args]
        p = subprocess.run(cmd, env=env, cwd=cwd or self.tmp, capture_output=True, text=True, timeout=60)
        out = None
        if os.path.exists(self.files["OUTPUT_FILE"]):
            with open(self.files["OUTPUT_FILE"]) as f:
                out = json.load(f)
        return p, out

    def state(self):
        with open(self.files["STATE_FILE"]) as f:
            return json.load(f)

    def assert_clean_state(self):
        with open(self.files["STATE_FILE"], "rb") as f:
            raw = f.read()
        self.assertLess(len(raw), 64 * 1024)
        for secret in (b"ghs_", KEY_B64[:40].encode(), b"PRIVATE KEY", b"Bearer"):
            self.assertNotIn(secret, raw)


class SC1AppJwt(unittest.TestCase):
    def test_signature_matches_openssl_for_pkcs1_and_pkcs8(self):
        data = b"header.payload-of-a-jwt"
        for kind, pem in PEMS.items():
            with self.subTest(kind=kind):
                key = gh_app_jwt.load_private_key(pem)
                want = openssl("dgst", "-sha256", "-sign", os.path.join(KEYS, f"{kind}.pem"), data=data)
                self.assertEqual(gh_app_jwt.sign_rs256(key, data), want)

    def test_jwt_claims_and_signature_segment(self):
        for kind, pem in PEMS.items():
            with self.subTest(kind=kind):
                token = gh_app_jwt.make_jwt(gh_app_jwt.load_private_key(pem), "5815034", now=1_800_000_000)
                h, p, s = token.split(".")
                self.assertEqual(json.loads(b64url_decode(h)), {"alg": "RS256", "typ": "JWT"})
                self.assertEqual(json.loads(b64url_decode(p)), {"iat": 1_799_999_940, "exp": 1_800_000_540, "iss": "5815034"})
                want = openssl("dgst", "-sha256", "-sign", os.path.join(KEYS, f"{kind}.pem"), data=f"{h}.{p}".encode())
                self.assertEqual(b64url_decode(s), want)

    def test_pkcs1_and_pkcs8_parse_to_same_key(self):
        self.assertEqual(gh_app_jwt.load_private_key(PEMS["pkcs1"]), gh_app_jwt.load_private_key(PEMS["pkcs8"]))

    def test_installation_ids_by_owner(self):
        self.assertEqual(gh_app_jwt.installation_id("PraiseZhu"), 183272732)
        self.assertEqual(gh_app_jwt.installation_id("xindong"), 183272688)
        with self.assertRaises(gh_app_jwt.AppAuthError):
            gh_app_jwt.installation_id("makecindy")

    def test_bad_key_errors_do_not_echo_the_key(self):
        bogus = base64.b64encode(b"-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAKj34GkxFhD9\n-----END RSA PRIVATE KEY-----\n")
        for env in ({}, {"PRAISEDEVIN_PRIVATE_KEY_B64": "not base64!!"}, {"PRAISEDEVIN_PRIVATE_KEY_B64": bogus.decode()}):
            with self.subTest(env=list(env)):
                with self.assertRaises(gh_app_jwt.AppAuthError) as cm:
                    gh_app_jwt.key_from_env(env)
                for value in env.values():
                    self.assertNotIn(value, str(cm.exception))
                self.assertNotIn("MIIBOgIBAAJBAKj34GkxFhD9", str(cm.exception))

    def test_standard_library_only_and_no_subprocess(self):
        stdlib = set(sys.stdlib_module_names)
        for name in ("gh_app_jwt.py", "mergewatch.py", "catchup.py"):
            with self.subTest(file=name):
                with open(os.path.join(PREFLIGHT, name), encoding="utf-8") as f:
                    tree = ast.parse(f.read())
                imported = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported |= {a.name.split(".")[0] for a in node.names}
                    elif isinstance(node, ast.ImportFrom):
                        imported.add((node.module or "").split(".")[0])
                local = {"gh_app_jwt", "mergewatch"}
                self.assertEqual(imported - stdlib - local, set())
                self.assertFalse(imported & {"subprocess", "ctypes", "multiprocessing"}, imported)
        with open(RESOLVE, encoding="utf-8") as f:
            self.assertNotIn("subprocess", f.read())


class SC2ResolveMode(unittest.TestCase):
    routing = resolve_mode.load_routing(ROUTING)

    def resolve(self, kind, available):
        return resolve_mode.resolve(kind, self.routing, available)

    def test_preview_available_sends_preferred_and_fallback(self):
        r = self.resolve("merge_qa", STABLE + ["swe-2-medium"])
        self.assertEqual(r["api_session_settings"], {"devin_mode": "swe-2-medium", "fallback_devin_mode": "lite"})
        self.assertEqual(r["audit"], {"requested_mode": "swe-2-medium", "effective_mode": "swe-2-medium",
                                      "fallback_used": False, "fallback_reason": None})
        self.assertEqual(r["platform"], "linux")

    def test_preview_not_open_sends_fallback_only_without_fallback_key(self):
        r = self.resolve("catchup_qa", STABLE)
        self.assertEqual(r["api_session_settings"], {"devin_mode": "lite"})
        self.assertNotIn("fallback_devin_mode", r["api_session_settings"])
        self.assertTrue(r["audit"]["fallback_used"])
        self.assertEqual(r["audit"]["requested_mode"], "swe-2-medium")
        self.assertEqual(r["audit"]["effective_mode"], "lite")
        self.assertIn("swe-2-medium", r["audit"]["fallback_reason"])

    def test_preview_withdrawn_falls_back_without_fallback_key(self):
        self.assertEqual(self.resolve("bot2_fix", STABLE + ["swe-2-high"])["api_session_settings"],
                         {"devin_mode": "swe-2-high", "fallback_devin_mode": "fusion"})
        withdrawn = self.resolve("bot2_fix", STABLE)
        self.assertEqual(withdrawn["api_session_settings"], {"devin_mode": "fusion"})
        self.assertTrue(withdrawn["audit"]["fallback_used"])

    def test_stable_or_null_mode_with_fallback_is_rejected(self):
        for settings in ({"devin_mode": "lite", "fallback_devin_mode": "lite"},
                         {"devin_mode": None, "fallback_devin_mode": "normal"},
                         {"fallback_devin_mode": "normal"}):
            with self.subTest(settings=settings):
                with self.assertRaises(resolve_mode.RoutingError):
                    resolve_mode.validate_settings(settings, self.routing)
        with self.assertRaises(resolve_mode.RoutingError):
            resolve_mode.validate_settings({"devin_mode": "swe-2-high"}, self.routing)
        resolve_mode.validate_settings({"devin_mode": "normal"}, self.routing)
        resolve_mode.validate_settings({"devin_mode": None}, self.routing)

    def test_task_mapping(self):
        want = {"merge_qa": ("swe-2-medium", "lite"), "catchup_qa": ("swe-2-medium", "lite"),
                "slot_job": ("swe-2-medium", "lite"), "watchdog_triage": ("swe-2-high", "normal"),
                "A4": ("swe-2-high", "normal"), "bot1_chat": ("swe-2-high", "normal"), "bot1": ("swe-2-high", "normal"),
                "bot2_fix": ("swe-2-high", "fusion"), "bot3_eval": ("swe-2-high", "fusion"),
                "bot3": ("swe-2-high", "fusion"), "deep_fix_or_review": ("swe-2-max", "ultra")}
        all_modes = STABLE + ["swe-2-medium", "swe-2-high", "swe-2-max"]
        for kind, (pref, fb) in want.items():
            with self.subTest(kind=kind):
                self.assertEqual(self.resolve(kind, all_modes)["api_session_settings"],
                                 {"devin_mode": pref, "fallback_devin_mode": fb})
                self.assertEqual(self.resolve(kind, STABLE)["api_session_settings"], {"devin_mode": fb})
        with open(ROUTING) as f:
            routing = json.load(f)
        self.assertEqual(set(routing["tasks"]), {"merge_qa", "catchup_qa", "slot_job", "watchdog_triage", "bot1_chat",
                                                 "bot2_fix", "bot3_eval", "deep_fix_or_review"})
        self.assertEqual({t["platform"] for t in routing["tasks"].values()}, {"linux"})
        self.assertEqual((routing["internal_llm"]["provider"], routing["internal_llm"]["model"],
                          routing["internal_llm"]["consumes_devin_acu"]), ("openai", "gpt-6-luna", False))

    def test_unknown_kind_and_missing_fallback_mode_fail(self):
        with self.assertRaises(resolve_mode.RoutingError):
            self.resolve("nope", STABLE)
        with self.assertRaises(resolve_mode.RoutingError):
            self.resolve("deep_fix_or_review", ["normal", "lite"])

    def test_cli(self):
        tmp = tempfile.mkdtemp(prefix="resolve-")
        try:
            schemas = os.path.join(tmp, "schemas.json")
            with open(schemas, "w") as f:
                json.dump({"session_settings": {"available_devin_modes": STABLE}}, f)
            p = subprocess.run([sys.executable, RESOLVE, "bot1", "--schemas", schemas], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            doc = json.loads(p.stdout)
            self.assertEqual(doc["api_session_settings"], {"devin_mode": "normal"})
            self.assertEqual(doc["audit"]["effective_mode"], "normal")
            bad = os.path.join(tmp, "bad.json")
            with open(bad, "w") as f:
                json.dump({"devin_mode": "lite", "fallback_devin_mode": "lite"}, f)
            p = subprocess.run([sys.executable, RESOLVE, "--validate", bad], capture_output=True, text=True)
            self.assertEqual(p.returncode, 1)
            self.assertIn("fallback_devin_mode must be omitted", p.stderr)
            p = subprocess.run([sys.executable, RESOLVE, "deep_fix_or_review", "--available-modes",
                                ",".join(STABLE + ["swe-2-max"])], capture_output=True, text=True)
            self.assertEqual(json.loads(p.stdout)["api_session_settings"],
                             {"devin_mode": "swe-2-max", "fallback_devin_mode": "ultra"})
        finally:
            shutil.rmtree(tmp)


MW = os.path.join(PREFLIGHT, "mergewatch.py")
CU = os.path.join(PREFLIGHT, "catchup.py")


class SC3MergeWatch(PreflightCase):
    def test_new_merge_writes_item_to_output_file(self):
        self.gh.fx["pulls"] = [pull(41, 300), pull(40, 600, merged=False)]
        p, out = self.run_script(MW)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout, "")
        self.assertEqual(out["items"], [{"id": f"PR-41@{sha(41)[:12]}", "version": "try-1", "pr": 41, "sha": sha(41),
                                         "day": "2026-11-23", "try": 1, "base": "main", "mode": "event",
                                         "title": out["items"][0]["title"]}])
        self.assertIn("PR #41", out["items"][0]["title"])
        self.assert_clean_state()
        self.assertEqual(self.gh.count("/access_tokens"), 2)

    def test_quiet_poll_does_not_list_pulls(self):
        self.gh.fx["pulls"] = [pull(41, 300)]
        self.run_script(MW)
        p, out = self.run_script(MW, now=NOW + 60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(out, {"items": []})
        self.assertEqual(self.gh.count("/pulls"), 0)
        self.assertEqual(self.gh.count("/matching-refs/"), 3)
        self.gh.fx["heads"]["release/1.2"] = "d" * 40
        self.run_script(MW, now=NOW + 90)
        self.assertEqual(self.gh.count("/pulls"), 1)
        self.run_script(MW, now=NOW + 90 + 600)
        self.assertEqual(self.gh.count("/pulls"), 1, "10 min since the last scan forces a scan")

    def test_skips_probe_bases_and_matching_reports(self):
        self.gh.fx["pulls"] = [pull(1, 100, base="probe-x"), pull(2, 200), pull(3, 300, base="integration/s1"),
                               pull(4, 400), pull(5, 500)]
        self.gh.fx["reports"] = {"PR-2.json": report(2, sha(2)), "PR-4.json": report(4, "f" * 40),
                                 "PR-5.json": report(5, None, verdict="docs-only", merged_sha=sha(5))}
        p, out = self.run_script(MW)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([i["pr"] for i in out["items"]], [4, 3], "main first; probe-* and matching reports skipped")
        self.assertIn(f"PR-2@{sha(2)[:12]}", self.state()["done"])

    def test_backoff_and_three_tries(self):
        self.gh.fx["pulls"] = [pull(7, 60)]
        self.gh.fx["runs"] = {
            "PR-7/2026-11-23-try1-event.json": {"exit_code": 3, "finished_at": iso(NOW + 5)},
            "PR-7/2026-11-23-try2-event.json": {"exit_code": 3, "finished_at": iso(NOW + 130)},
        }
        timeline = [(0, ["try-1"]), (60, []), (125, ["try-2"]), (125 + 300, []), (125 + 605, ["try-3"]),
                    (125 + 605 + 2000, [])]
        for offset, want in timeline:
            with self.subTest(offset=offset):
                self.gh.fx["heads"]["main"] = f"{offset:040d}"
                p, out = self.run_script(MW, now=NOW + offset)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual([i["version"] for i in out["items"]], want)
                self.assertTrue(all(i["id"] == f"PR-7@{sha(7)[:12]}" for i in out["items"]))
        self.assertEqual(self.state()["tries"][f"PR-7@{sha(7)[:12]}"]["n"], 3)

    def test_running_previous_try_is_not_reissued(self):
        self.gh.fx["pulls"] = [pull(8, 60)]
        self.run_script(MW)
        key = f"PR-8@{sha(8)[:12]}"
        self.gh.fx["heads"]["main"] = "e" * 40
        p, out = self.run_script(MW, now=NOW + 300, last_run={"items": [{"key": key, "status": "running"}]})
        self.assertEqual(out, {"items": []})
        p, out = self.run_script(MW, now=NOW + 301, last_run={"items": [{"key": key, "status": "succeeded"}]})
        self.assertEqual(out, {"items": []})

    def test_sc1_missing_record_does_not_retry_at_two_or_ten_minutes(self):
        self.gh.fx["pulls"] = [pull(41, 60)]
        for elapsed in (120, 600):
            p, out = self.run_script(MW)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual([i["version"] for i in out["items"]], ["try-1"])
            with self.subTest(elapsed=elapsed):
                p, out = self.run_script(MW, now=NOW + elapsed)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(out, {"items": []})
            os.unlink(self.files["STATE_FILE"])

    def test_sc2_failed_record_backs_off_from_finish_and_successful_report_is_done(self):
        self.gh.fx["pulls"] = [pull(43, 60), pull(44, 60), pull(45, 60)]
        p, out = self.run_script(MW)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([i["version"] for i in out["items"]], ["try-1"] * 3)
        for pr, code in [(43, 3), (44, 0), (45, 0)]:
            self.gh.fx["runs"][f"PR-{pr}/2026-11-23-try1-event.json"] = {
                "exit_code": code, "finished_at": iso(NOW + 300),
            }
        self.gh.fx["reports"]["PR-44.json"] = report(44, sha(44))
        p, out = self.run_script(MW, now=NOW + 419)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(out, {"items": []})
        self.assertIn(f"PR-44@{sha(44)[:12]}", self.state()["done"])
        self.assertEqual(self.gh.count("/PR-43/2026-11-23-try1-event.json"), 1)
        p, out = self.run_script(MW, now=NOW + 420)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([(i["pr"], i["version"]) for i in out["items"]], [(43, "try-2")])
        p, out = self.run_script(MW, now=NOW + 5 * 3600)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(out, {"items": []})

    def test_sc3_missing_record_retries_only_after_five_hours(self):
        self.gh.fx["pulls"] = [pull(46, 60)]
        p, out = self.run_script(MW)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([i["version"] for i in out["items"]], ["try-1"])
        results = []
        for elapsed in (5 * 3600 - 1, 5 * 3600):
            p, out = self.run_script(MW, now=NOW + elapsed)
            self.assertEqual(p.returncode, 0, p.stderr)
            results.append([i["version"] for i in out["items"]])
        self.assertEqual(results, [[], ["try-2"]])

    def test_sc4_last_run_terminal_status_retries_only_matching_items_after_backoff(self):
        self.gh.fx["pulls"] = [pull(n, 60) for n in (47, 48, 49)]
        p, out = self.run_script(MW)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([i["version"] for i in out["items"]], ["try-1"] * 3)
        last_run = {"items": [{"key": f"PR-47@{sha(47)[:12]}", "status": "FAILED"},
                              {"key": f"PR-48@{sha(48)[:12]}:try-1", "status": "skipped"},
                              {"key": f"PR-49@{sha(49)[:12]}#try-2", "status": "failed"}]}
        p, out = self.run_script(MW, now=NOW + 119, last_run=last_run)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(out, {"items": []})
        p, out = self.run_script(MW, now=NOW + 120, last_run=last_run)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([(i["pr"], i["version"]) for i in out["items"]], [(47, "try-2"), (48, "try-2")])

    def test_run_record_github_error_exits_without_writing_state_or_output(self):
        self.gh.fx["pulls"] = [pull(50, 60)]
        self.run_script(MW)
        before = self.state()
        self.gh.fx["runs"]["PR-50/2026-11-23-try1-event.json"] = 502
        p, out = self.run_script(MW, now=NOW + 120)
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertIn("HTTP 502", p.stderr)
        self.assertIsNone(out)
        self.assertEqual(self.state(), before)

    def test_at_most_ten_items_per_run_and_small_state(self):
        self.gh.fx["pulls"] = [pull(100 + i, 1000 + i * 60, base="main" if i % 3 else "integration/x") for i in range(15)]
        p, out = self.run_script(MW)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(out["items"]), 10)
        bases = [i["base"] for i in out["items"]]
        self.assertEqual(bases, sorted(bases, key=lambda b: b != "main"))
        self.gh.fx["heads"]["main"] = "f" * 40
        p, out = self.run_script(MW, now=NOW + 30)
        self.assertEqual(len(out["items"]), 5)
        self.assertEqual({i["try"] for i in out["items"]}, {1})
        self.assert_clean_state()

    def test_state_stays_under_64k_with_many_prs(self):
        self.gh.fx["pulls"] = [pull(1000 + i, 600 + i) for i in range(100)]
        self.gh.fx["reports"] = {f"PR-{1000 + i}.json": report(1000 + i, sha(1000 + i)) for i in range(0, 100, 2)}
        for k in range(12):
            self.gh.fx["heads"]["main"] = f"{k:040d}"
            p, _ = self.run_script(MW, now=NOW + k * 700)
            self.assertEqual(p.returncode, 0, p.stderr)
        self.assert_clean_state()

    def test_github_error_exits_nonzero_without_touching_state(self):
        self.gh.fx["pulls"] = [pull(9, 60)]
        self.run_script(MW)
        with open(self.files["STATE_FILE"], "rb") as f:
            before = f.read()
        self.gh.fx["fail"] = True
        p, out = self.run_script(MW, now=NOW + 900)
        self.assertNotEqual(p.returncode, 0)
        self.assertIsNone(out)
        with open(self.files["STATE_FILE"], "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertNotIn("ghs_", p.stderr)

    def test_missing_key_fails_without_output(self):
        env_less = dict(self.files)
        p = subprocess.run([sys.executable, MW], env={"PATH": os.environ["PATH"], "GITHUB_API_URL": self.gh.url, **env_less},
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("PRAISEDEVIN_PRIVATE_KEY_B64 is not set", p.stderr)
        self.assertFalse(os.path.exists(self.files["OUTPUT_FILE"]))


class SC3CatchUp(PreflightCase):
    def test_no_gap_outputs_empty_items(self):
        self.gh.fx["pulls"] = [pull(1, 7200), pull(2, 20 * 3600, base="integration/a")]
        self.gh.fx["reports"] = {"PR-1.json": report(1, sha(1)), "PR-2.json": report(2, sha(2))}
        p, out = self.run_script(CU)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(out, {"items": []})
        self.assert_clean_state()

    def test_gaps_give_one_item_listing_every_gap_pr(self):
        self.gh.fx["pulls"] = [
            pull(10, 2 * 3600),                      # missing report
            pull(11, 3 * 3600),                      # tested_sha != merge sha
            pull(12, 4 * 3600),                      # docs-only merged_sha != merge sha
            pull(13, 5 * 3600),                      # unreadable report
            pull(14, 6 * 3600),                      # qa-error
            pull(15, 7 * 3600),                      # ok
            pull(16, 8 * 3600, base="probe-a"),      # skipped base
            pull(17, 600),                           # merged 10 min ago: mergewatch's job
            pull(18, 4 * 86400),                     # outside yesterday/today
            pull(19, 30 * 3600, base="release/9"),   # yesterday, missing
        ]
        self.gh.fx["reports"] = {"PR-11.json": report(11, "f" * 40),
                                 "PR-12.json": report(12, None, verdict="docs-only", merged_sha="e" * 40),
                                 "PR-13.json": "{not json", "PR-14.json": report(14, None, verdict="qa-error"),
                                 "PR-15.json": report(15, sha(15))}
        p, out = self.run_script(CU)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(out["items"]), 1)
        item = out["items"][0]
        self.assertEqual(item["prs"], [19, 14, 13, 12, 11, 10])
        self.assertEqual(item["days"], ["2026-11-22", "2026-11-23"])
        self.assertTrue(item["id"].startswith("catchup-2026-11-23@"))
        self.assertEqual(item["version"], "try-1")
        reasons = {g["pr"]: g["reason"] for g in item["gaps"]}
        self.assertEqual(reasons[10], "missing")
        self.assertTrue(reasons[11].startswith("stale"))
        self.assertTrue(reasons[12].startswith("stale"))
        self.assertEqual(reasons[13], "unreadable")
        self.assertEqual(reasons[14], "unfinished (qa-error)")
        self.assertEqual({g["sha"] for g in item["gaps"] if g["pr"] == 11}, {sha(11)})
        for n in (11, 14, 19):
            self.assertIn(f"#{n}", item["title"])
        self.assert_clean_state()

    def test_gap_given_up_after_three_tries_and_busy_run_waits(self):
        self.gh.fx["pulls"] = [pull(20, 2 * 3600)]
        ids = []
        for k in range(3):
            p, out = self.run_script(CU, now=NOW + k * 3600)
            self.assertEqual(out["items"][0]["version"], f"try-{k + 1}")
            ids.append(out["items"][0]["id"])
        self.assertEqual(len(set(ids)), 1)
        p, out = self.run_script(CU, now=NOW + 3 * 3600, last_run={"items": [{"key": ids[0], "status": "running"}]})
        self.assertEqual(out, {"items": []})
        self.assertEqual(self.gh.count("/pulls"), 0)
        p, out = self.run_script(CU, now=NOW + 3 * 3600)
        self.assertEqual(out, {"items": []})
        self.assertIn(f"PR-20@{sha(20)[:12]}", self.state()["gave_up"])

    def test_github_error_exits_nonzero(self):
        self.gh.fx["fail"] = True
        p, out = self.run_script(CU)
        self.assertNotEqual(p.returncode, 0)
        self.assertIsNone(out)
        self.assertFalse(os.path.exists(self.files["STATE_FILE"]))


class SC4Bundles(PreflightCase):
    def test_check_passes_and_build_is_deterministic(self):
        p = subprocess.run([sys.executable, BUILD, "--check"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        out_dirs = [os.path.join(self.tmp, "a"), os.path.join(self.tmp, "b")]
        for d in out_dirs:
            subprocess.run([sys.executable, BUILD, "--out-dir", d], check=True, capture_output=True)
        for name in ("mergewatch.bundle.py", "catchup.bundle.py"):
            with self.subTest(bundle=name):
                blobs = []
                for d in out_dirs + [DIST]:
                    with open(os.path.join(d, name), "rb") as f:
                        blobs.append(f.read())
                self.assertEqual(blobs[0], blobs[1])
                self.assertEqual(blobs[0], blobs[2])
                self.assertLessEqual(len(blobs[2]), 262144)

    def test_check_flags_a_stale_bundle(self):
        d = os.path.join(self.tmp, "stale")
        subprocess.run([sys.executable, BUILD, "--out-dir", d], check=True, capture_output=True)
        with open(os.path.join(d, "catchup.bundle.py"), "a") as f:
            f.write("# edited\n")
        p = subprocess.run([sys.executable, BUILD, "--check", "--out-dir", d], capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("catchup.bundle.py: out of date", p.stderr)

    def isolated_bundle(self, name):
        box = tempfile.mkdtemp(prefix="preflight-empty-")
        self.addCleanup(shutil.rmtree, box, True)
        shutil.copy(os.path.join(DIST, name), box)
        return box, os.path.join(box, name)

    def test_mergewatch_bundle_runs_alone(self):
        box, bundle = self.isolated_bundle("mergewatch.bundle.py")
        self.gh.fx["pulls"] = [pull(51, 120), pull(52, 240, base="probe-z")]
        p, out = self.run_script(bundle, cwd=box, isolated=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([i["id"] for i in out["items"]], [f"PR-51@{sha(51)[:12]}"])
        self.assertEqual(os.listdir(box), ["mergewatch.bundle.py"])
        self.assert_clean_state()

    def test_catchup_bundle_runs_alone(self):
        box, bundle = self.isolated_bundle("catchup.bundle.py")
        self.gh.fx["pulls"] = [pull(61, 7200), pull(62, 9000)]
        self.gh.fx["reports"] = {"PR-62.json": report(62, sha(62))}
        p, out = self.run_script(bundle, cwd=box, isolated=True, args=["--stdout"])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(out["items"][0]["prs"], [61])
        self.assertEqual(json.loads(p.stdout), out)
        self.assertEqual(os.listdir(box), ["catchup.bundle.py"])
        self.gh.fx["reports"]["PR-61.json"] = report(61, sha(61))
        p, out = self.run_script(bundle, cwd=box, isolated=True, now=NOW + 3600)
        self.assertEqual(out, {"items": []})


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(KEYS, ignore_errors=True)
