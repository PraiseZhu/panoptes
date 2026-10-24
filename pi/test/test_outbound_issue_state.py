"""python3 test/test_outbound_issue_state.py — outbound.py gh.issue.close / gh.issue.reopen handler + feishu.dm guards, with a
FAKE gh (no GitHub call, no send). Temp files only. PI_BIN overrides the code dir (staging review)."""
import sys, os, json, types, tempfile, importlib.util
B = os.environ.get("PI_BIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
sys.modules["merger"] = types.SimpleNamespace(FIELDS="number,title,headRefOid,state", blockers=lambda p: [])   # not under test
spec = importlib.util.spec_from_file_location("ob", f"{B}/outbound.py"); O = importlib.util.module_from_spec(spec); spec.loader.exec_module(O)
calls = []; issue = {"state": "OPEN", "labels": [{"name": "P1"}, {"name": "status:open"}]}
def fake_gh(args, js=True):
    calls.append(args)
    if args[:2] == ["issue", "view"]: return json.loads(json.dumps(issue))
    if args[:2] == ["issue", "close"]: issue["state"] = "CLOSED"; return "✓ Closed issue"
    if args[:2] == ["issue", "reopen"]: issue["state"] = "OPEN"; return "✓ Reopened issue"
    if args[:2] == ["issue", "edit"]:
        for i, x in enumerate(args):
            if x == "--add-label": issue["labels"].append({"name": args[i + 1]})
            if x == "--remove-label": issue["labels"] = [l for l in issue["labels"] if l["name"] != args[i + 1]]
        return "url"
    raise AssertionError(args)
O.gh = fake_gh
with tempfile.TemporaryDirectory() as d:
    rec = f"{d}/issues.json"
    json.dump({"updated_at": None, "issues": [{"key": "mq1-a-pr11", "pr": 11, "issue": 1, "url": "https://github.com/aj0928/aj0928-handoff/issues/1"}]}, open(rec, "w"))
    close = {"key": "handoff-close:aj0928/aj0928-handoff#1:fix21", "type": "gh.issue.close", "repo": "aj0928/aj0928-handoff", "issue": 1, "comment": "复测通过：…关闭。",
             "add_labels": ["status:fixed"], "remove_labels": ["status:open"], "record": {"file": rec, "set": {"status": "fixed", "fix_pr": 21}, "stamp": "closed_at"}}
    who, state, validate, do = O.handler(close)
    assert state() == "pending" and "OPEN" in validate()
    calls.clear(); out = do()
    ops = [c[:2] for c in calls if c[1] != "view"]
    assert ops == [["issue", "edit"], ["issue", "close"]], ops                       # labels first, then close + comment
    assert any("<!-- pi-line handoff-close:aj0928/aj0928-handoff#1:fix21 -->" in x for c in calls for x in c)
    assert "issues.json updated" in out and state() == "done"
    r = json.load(open(rec))["issues"][0]; assert r["status"] == "fixed" and r["fix_pr"] == 21 and r["closed_at"] and json.load(open(rec))["updated_at"]
    # reopen on a CLOSED issue: reopen + comment, then labels
    reopen = {**close, "key": "handoff-reopen:aj0928/aj0928-handoff#1:fix22", "type": "gh.issue.reopen", "comment": "复测未通过：…",
              "add_labels": ["status:open"], "remove_labels": ["status:fixed"], "record": {"file": rec, "set": {"status": "open", "fix_round": 2}, "stamp": "reopened_at"}}
    who, state, validate, do = O.handler(reopen)
    assert state() == "pending"; calls.clear(); do()
    assert [c[:2] for c in calls if c[1] != "view"] == [["issue", "reopen"], ["issue", "edit"]] and state() == "done"
    r = json.load(open(rec))["issues"][0]; assert r["status"] == "open" and r["fix_round"] == 2 and r["reopened_at"]
    # already in the wanted state but labels wrong -> labels only, no second comment
    issue["labels"] = [{"name": "status:fixed"}]; calls.clear(); assert state() == "pending"; do()
    assert [c[:2] for c in calls if c[1] != "view"] == [["issue", "edit"]]
    # only handoff repos
    try: O.handler({**close, "repo": "xindong/mivo-canvas-plugin"})[2](); raise SystemExit("validate should refuse")
    except RuntimeError as e: assert "not a handoff repo" in str(e)
    # feishu.dm guards: no body and no pr -> refused before any send; dm: key -> Grok-form record added after the send
    O.QA = d; sent = f"{d}/feishu-sent.json"; json.dump({}, open(sent, "w"))
    O.author_by_name = lambda n: {"feishu_open_id": "ou_x", "notify_via_feishu": True}
    ran = []
    def fake_run(cmd, **kw):
        ran.append(cmd); s = json.load(open(sent)); s[cmd[cmd.index("--dedupe-key") + 1]] = {"open_id": "ou_x", "sent_at": "2026-10-24T00:40:00Z", "message_id": "om_1"}
        json.dump(s, open(sent, "w")); return '{"ok": true}'
    O.run = fake_run
    try: O.handler({"key": "followup:2026-10-24:aj0928:reminded", "type": "feishu.dm", "to": "aj0928"})[3](); raise SystemExit("should refuse")
    except RuntimeError as e: assert "neither body/text nor pr" in str(e) and not ran
    # a 10-24 DM stands for unified-rule problems only: an AI-only P1 needs the repo-wide verification to have been in it
    O.dm_compat.load_report = lambda pr: {"findings": [{"severity": "P1", "source": "code-review", "verification": {"status": "confirmed"}, "fingerprint": "mq1-aaaa"}]}
    orig_record = O.dm_compat.record
    O.dm_compat.record = lambda path, key: orig_record(path, key, O.dm_compat.load_report)
    res = O.handler({"key": "dm:2026-10-24:1300", "type": "feishu.dm", "to": "aj0928", "pr": 1300, "text": "你的 PR #1300 …"})[3]()
    assert "compat=['xindong/mivo-canvas-plugin#1300:mq1-aaaa']" in res, res
    assert json.load(open(sent))["xindong/mivo-canvas-plugin#1300:mq1-aaaa"]["alias_of"] == "dm:2026-10-24:1300"
print("ok test_outbound_issue_state")
