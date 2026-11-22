"""devin/preflight/watchdog.py 的单元测试：假 fetcher 覆盖 4 项巡检、同日去重、全正常写空列表；
复制到空临时目录、对本地假 GitHub API 独立运行；体积与纯标准库约束；自带 RS256 JWT 用 openssl 验签。"""
import datetime as dt
import http.server
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

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "preflight", "watchdog.py")
SLOTS_JSON = os.path.join(os.path.dirname(HERE), "conf", "slots.json")
CST = dt.timezone(dt.timedelta(hours=8))
TODAY = dt.date(2026, 11, 23)
NOW = dt.datetime(2026, 11, 23, 9, 0, tzinfo=CST)   # 09:00: 00:05..08:35 slots are 45+ min old, 07:50 deadline passed
ALL_DUE = ["0005", "0620", "0700", "0735"]   # slots whose at + 45 min <= 09:00 (08:20 + 45 = 09:05 not yet)

spec = importlib.util.spec_from_file_location("watchdog", SCRIPT)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)
YDAY_DUE = [t.replace(":", "") for t in wd.SLOTS]   # yesterday: every slot recorded (the 24 h window reaches back into it)


def read(path):
    with open(path) as fh:
        return fh.read()


def write(path, data):
    with open(path, "wb") as fh:
        fh.write(data)


def jload(path):
    return json.loads(read(path))


def jdump(obj, path):
    with open(path, "w") as fh:
        json.dump(obj, fh)


class Fake:
    """Healthy by default; tests break one thing at a time."""

    def __init__(self):
        self.branch = "devin"
        self.prs = [{"number": 100, "merged_at": NOW - dt.timedelta(hours=3), "title": "a"},
                    {"number": 101, "merged_at": NOW - dt.timedelta(minutes=30), "title": "fresh"}]
        self.files = {"reports/merged/PR-100.json"}
        self.ledger = {"mivo-daily-report:2026-11-22": {"status": "done"}}
        self.head = NOW - dt.timedelta(hours=1)
        self.markers = {"2026-11-23": {f"{s}.json" for s in ALL_DUE}, "2026-11-22": {f"{s}.json" for s in YDAY_DUE}}
        self.calls = []

    def merged_prs(self, since):
        self.calls.append(("merged_prs", since))
        return [p for p in self.prs if p["merged_at"] >= since]

    def data_exists(self, rel):
        return rel in self.files

    def data_json(self, rel):
        assert rel == "pi/out/outbound/ledger.json", rel
        return self.ledger

    def data_head_time(self):
        return self.head

    def slot_markers(self, day):
        return set(self.markers.get(day, set()))


def ids(items):
    return sorted(i["id"] for i in items)


class ChecksTest(unittest.TestCase):
    def run_wd(self, fake, now=NOW, state=None, last_run=None):
        return wd.run(fake, now, state or {}, last_run or {})

    def test_all_normal_is_empty(self):
        items, state = self.run_wd(Fake())
        self.assertEqual(items, [])
        self.assertEqual(state["reported"], {})

    def test_merged_report(self):
        f = Fake()
        f.files = set()
        items, _ = self.run_wd(f)
        self.assertEqual(ids(items), ["merged-report:2026-11-23:PR-100"])   # PR-101 is only 30 min old
        f.files = {"reports/catch-up/PR-100.json"}
        self.assertEqual(self.run_wd(f)[0], [])                              # a catch-up report counts too
        f.prs[0]["merged_at"] = NOW - dt.timedelta(minutes=89)
        f.files = set()
        self.assertEqual(self.run_wd(f)[0], [])                              # under 90 min: not yet
        self.assertEqual(f.calls[-1][1], NOW - dt.timedelta(hours=24))

    def test_daily_sent(self):
        f = Fake()
        f.ledger = {"mivo-daily-report:2026-11-22": {"status": "sending"}}
        items, _ = self.run_wd(f)
        self.assertEqual(ids(items), ["daily-sent:2026-11-23:2026-11-22"])
        f.ledger = {}
        self.assertEqual(ids(self.run_wd(f)[0]), ["daily-sent:2026-11-23:2026-11-22"])
        early = dt.datetime(2026, 11, 23, 7, 49, tzinfo=CST)
        f.markers["2026-11-23"] = {f"{s}.json" for s in ALL_DUE}
        self.assertNotIn("daily-sent", json.dumps(self.run_wd(f, now=early)[0]))   # before 07:50 not checked

    def test_data_stale(self):
        f = Fake()
        f.head = NOW - dt.timedelta(hours=7, minutes=1)
        self.assertEqual(ids(self.run_wd(f)[0]), ["data-stale:2026-11-23:devin"])
        f.head = NOW - dt.timedelta(hours=6, minutes=59)
        self.assertEqual(self.run_wd(f)[0], [])

    def test_slot_missed(self):
        f = Fake()
        f.markers["2026-11-23"] = {f"{s}.json" for s in ALL_DUE if s not in ("0735", "0700")} | {"0700.shadow.json"}
                                                                             # a shadow run record counts as a run
        f.markers["2026-11-22"].discard("2340.json")                         # yesterday 23:40 is still in the window
        self.assertEqual(ids(self.run_wd(f)[0]), ["slot-missed:2026-11-22:2340", "slot-missed:2026-11-23:0735"])
        at_0819 = dt.datetime(2026, 11, 23, 8, 19, tzinfo=CST)                # 07:35 + 44 min: not due yet
        f.markers["2026-11-23"] = {f"{s}.json" for s in ("0005", "0620", "0700")}
        f.markers["2026-11-22"].add("2340.json")
        self.assertEqual(self.run_wd(f, now=at_0819)[0], [])

    def test_one_item_per_anomaly_and_same_day_dedup(self):
        f = Fake()
        f.files, f.ledger, f.head = set(), {}, NOW - dt.timedelta(hours=8)
        f.markers["2026-11-23"] = set()
        items, state = self.run_wd(f)
        want = ["daily-sent:2026-11-23:2026-11-22", "data-stale:2026-11-23:devin", "merged-report:2026-11-23:PR-100"] \
            + [f"slot-missed:2026-11-23:{s}" for s in ALL_DUE]
        self.assertEqual(ids(items), sorted(want))
        for i in items:
            self.assertRegex(i["id"], r"^[a-z-]+:\d{4}-\d{2}-\d{2}:[\w.-]+$")
            self.assertTrue(i["title"])
        f.markers["2026-11-23"] = {"0820.json", "0835.json"}                 # the slots due by 09:30 that did run
        again, state2 = self.run_wd(f, now=NOW + dt.timedelta(minutes=30), state=state)
        self.assertEqual(again, [])                                           # same day, state kept: nothing new
        self.assertEqual(self.run_wd(f, now=NOW + dt.timedelta(minutes=30), last_run={"items": [{"key": x} for x in want]})[0], [])
        f.prs.append({"number": 102, "merged_at": NOW - dt.timedelta(hours=2), "title": "b"})
        more, _ = self.run_wd(f, now=NOW + dt.timedelta(minutes=30), state=state2)
        self.assertEqual(ids(more), ["merged-report:2026-11-23:PR-102"])      # a new anomaly still comes through
        nxt = dt.datetime(2026, 11, 24, 9, 0, tzinfo=CST)
        f.markers = {"2026-11-24": {f"{s}.json" for s in ALL_DUE}, "2026-11-23": {f"{s}.json" for s in YDAY_DUE}}
        f.prs = [{"number": 103, "merged_at": nxt - dt.timedelta(hours=2), "title": "c"}]
        f.ledger = {}
        nd, st3 = self.run_wd(f, now=nxt, state=state2)
        self.assertIn("data-stale:2026-11-24:devin", ids(nd))                 # next day: reported again, new date
        self.assertTrue(all(v >= "2026-11-21" for v in st3["reported"].values()))

    def test_failing_check_reports_error_once(self):
        f = Fake()
        f.data_head_time = lambda: (_ for _ in ()).throw(RuntimeError("HTTP 502"))
        items, state = self.run_wd(f)
        self.assertEqual(ids(items), ["watchdog-error:2026-11-23:data-stale"])
        self.assertEqual(self.run_wd(f, state=state)[0], [])

    def test_slots_match_slots_json(self):
        conf = jload(SLOTS_JSON)
        self.assertEqual(list(wd.SLOTS), [s["at"] for s in conf["slots"]])
        self.assertEqual(len(wd.SLOTS), 14)


class StandaloneTest(unittest.TestCase):
    def test_size_and_stdlib_only(self):
        self.assertLessEqual(os.path.getsize(SCRIPT), 262144)
        src = read(SCRIPT)
        self.assertNotIn("gh_app_jwt", src)
        mods = re.findall(r"^\s*(?:import|from)\s+([\w.]+)", src, re.M)
        self.assertTrue(mods)
        for m in mods:
            self.assertIn(m.split(".")[0], sys.stdlib_module_names, m)

    def test_runs_alone_in_empty_dir_against_fake_github(self):
        lock_names = [{"name": f"{s}.json", "type": "file"} for s in ALL_DUE if s != "0700"]
        routes = {
            "/repos/o/prs/pulls": [{"number": 7, "merged_at": "2026-11-22T23:00:00Z", "updated_at": "2026-11-22T23:00:00Z", "title": "x"}],
            "/repos/o/data/contents/box/workspace/mivo-qa/pi/out/outbound/ledger.json": {"mivo-daily-report:2026-11-22": {"status": "done"}},
            "/repos/o/data/branches/devin": {"commit": {"commit": {"committer": {"date": "2026-11-23T00:30:00Z"}}}},
            "/repos/o/data/contents/runs/2026-11-23": lock_names,
            "/repos/o/data/contents/runs/2026-11-22": [{"name": f"{s}.json"} for s in YDAY_DUE],
        }
        seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                path = self.path.split("?")[0]
                seen.append((self.path, self.headers.get("Authorization"), self.headers.get("Accept")))
                body = routes.get(path)
                self.send_response(404 if body is None else 200)
                self.end_headers()
                if body is not None:
                    self.wfile.write(json.dumps(body).encode())

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        with tempfile.TemporaryDirectory() as empty, tempfile.TemporaryDirectory() as io:
            shutil.copy(SCRIPT, os.path.join(empty, "watchdog.py"))
            self.assertEqual(os.listdir(empty), ["watchdog.py"])
            files = {k: os.path.join(io, k.lower()) for k in ("EVENT_FILE", "STATE_FILE", "LAST_RUN_FILE", "OUTPUT_FILE")}
            jdump({"event_type": "schedule", "source": "cron", "payload": {}, "received_at": "2026-11-23T01:00:00Z"}, files["EVENT_FILE"])
            jdump({}, files["STATE_FILE"])
            jdump({"items": [], "truncated": False}, files["LAST_RUN_FILE"])
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **files, "WATCHDOG_API": f"http://127.0.0.1:{srv.server_port}",
                   "WATCHDOG_GH_TOKEN": "test-token", "WATCHDOG_PR_REPO": "o/prs", "WATCHDOG_DATA_REPO": "o/data",
                   "WATCHDOG_NOW": NOW.isoformat()}

            def go():
                r = subprocess.run([sys.executable, "-I", "watchdog.py"], cwd=empty, env=env, capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 0, r.stderr)
                return jload(files["OUTPUT_FILE"])

            out = go()
            self.assertEqual(ids(out["items"]), ["merged-report:2026-11-23:PR-7", "slot-missed:2026-11-23:0700"])
            self.assertEqual(set(out), {"items"})
            self.assertIn("merged-report:2026-11-23:PR-7", jload(files["STATE_FILE"])["reported"])
            self.assertEqual(go(), {"items": []})                                       # same day again: deduplicated
            self.assertTrue(all(a == "Bearer test-token" for _, a, _ in seen))
            self.assertTrue(any("raw" in (acc or "") for p, _, acc in seen if "ledger.json" in p))
            self.assertTrue(any("ref=devin-lock" in p for p, _, _ in seen if "/runs/" in p))
            routes["/repos/o/data/contents/box/workspace/mivo-qa/reports/merged/PR-7.json"] = {"name": "PR-7.json"}
            lock_names.append({"name": "0700.json"})
            jdump({}, files["STATE_FILE"])
            self.assertEqual(go(), {"items": []})                                       # all normal -> empty list
            del env["OUTPUT_FILE"]
            r = subprocess.run([sys.executable, "-I", "watchdog.py"], cwd=empty, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 2)


@unittest.skipUnless(shutil.which("openssl"), "openssl not installed")
class JwtTest(unittest.TestCase):
    def test_rs256_verifies_with_openssl(self):
        with tempfile.TemporaryDirectory() as d:
            for name, extra in (("pkcs8", []), ("pkcs1", ["-traditional"])):
                key, pub, sig, msg = (os.path.join(d, f"{name}.{x}") for x in ("pem", "pub", "sig", "msg"))
                subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", key],
                               check=True, capture_output=True)
                if extra:
                    subprocess.run(["openssl", "pkey", "-in", key, "-out", key + ".rsa", *extra], check=True, capture_output=True)
                    os.replace(key + ".rsa", key)
                subprocess.run(["openssl", "pkey", "-in", key, "-pubout", "-out", pub], check=True, capture_output=True)
                pem = read(key)
                self.assertIn("RSA PRIVATE KEY" if extra else "BEGIN PRIVATE KEY", pem)
                token = wd.app_jwt(5815034, pem, now=1_800_000_000)
                head, body, s = token.split(".")
                pad = lambda x: x + "=" * (-len(x) % 4)
                self.assertEqual(json.loads(wd.base64.urlsafe_b64decode(pad(head))), {"alg": "RS256", "typ": "JWT"})
                self.assertEqual(json.loads(wd.base64.urlsafe_b64decode(pad(body))), {"iat": 1_799_999_940, "exp": 1_800_000_540, "iss": "5815034"})
                write(msg, f"{head}.{body}".encode())
                write(sig, wd.base64.urlsafe_b64decode(pad(s)))
                r = subprocess.run(["openssl", "dgst", "-sha256", "-verify", pub, "-signature", sig, msg], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f"{name}: {r.stdout}{r.stderr}")


if __name__ == "__main__":
    unittest.main()
