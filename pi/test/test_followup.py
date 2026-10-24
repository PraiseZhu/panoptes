"""python3 test/test_followup.py — followup.py pure parts: DM actions carry the tool's text + Grok dedupe key (10-23 bug: they
carried neither, so outbound fell back to feishu_text() and would have sent "你的 PR #None ..."), escalation relay, live gate.
PI_BIN overrides the code dir (staging review); default ../bin next to this file."""
import sys, os, json, tempfile, importlib.util
B = os.environ.get("PI_BIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
spec = importlib.util.spec_from_file_location("fu", f"{B}/followup.py"); fu = importlib.util.module_from_spec(spec); spec.loader.exec_module(fu)
with tempfile.TemporaryDirectory() as d:
    os.makedirs(f"{d}/qa"); json.dump({"mivo_authors": [{"github_login": "aj0928", "feishu_open_id": "ou_a"}], "roster": []}, open(f"{d}/qa/author-map.json", "w"))
    fu.QA = f"{d}/qa"
    acts = fu.to_actions([{"open_id": "ou_a", "dedupe_key": "followup-remind:aj0928:2026-10-24", "text": "之前私聊提过的这处…"},
                          {"open_id": "ou_unknown", "dedupe_key": "followup-thanks:x:1:mq1-a", "text": "t"}], "remind")
    assert [(a["key"], a["type"], a["to"], a["text"]) for a in acts] == [("followup-remind:aj0928:2026-10-24", "feishu.dm", "aj0928", "之前私聊提过的这处…")]
    assert "pr" not in acts[0]           # outbound h_feishu_dm: no pr -> no "<repo>#None:" match, body = text
    # live gate = fleet-mode pi + outbound-enabled + feishu.dm not disabled
    fu.OUTBOUND_FLAG, fu.DISABLED = f"{d}/outbound-enabled", f"{d}/disabled"
    os.environ["PI_FLEET_MODE"] = "pi"; assert not fu.live_ok()
    open(fu.OUTBOUND_FLAG, "w").close(); assert fu.live_ok()
    open(fu.DISABLED, "w").write("# c\nfeishu.dm\n"); assert not fu.live_ok()
    open(fu.DISABLED, "w").write("gh.pr.merge\n"); assert fu.live_ok()
    os.environ["PI_FLEET_MODE"] = "grok"; assert not fu.live_ok()
print("ok test_followup")
