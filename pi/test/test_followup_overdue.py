"""python3 test/test_followup_overdue.py — 3-day overdue group notice on the Pi line (10-24): the queue stub
bin/followup_post_sender.mjs (never sends; dedupes like the real senders), followup.to_post_actions, and outbound's
feishu.group post path (allowlist + panoptes' own sender). Temp files only, no network. PI_BIN overrides the code dir."""
import sys, os, json, types, tempfile, subprocess, importlib.util
B = os.environ.get("PI_BIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
STUB = f"{B}/followup_post_sender.mjs"
CHAT = "oc_test00000000000000000000000000000"   # fake: real chat ids never go into git (panoptes rule)

def stub(env, *args):
    r = subprocess.run(["node", STUB, *args], capture_output=True, text=True, env=env, timeout=60)
    return r.returncode, r.stdout.strip()

with tempfile.TemporaryDirectory() as d:
    qa = f"{d}/qa"; os.makedirs(f"{qa}/report/slack")
    queue = f"{d}/q.jsonl"; open(queue, "w").close()
    env = dict(os.environ, QA_HOME=qa, PI_FOLLOWUP_QUEUE=queue)
    post = {"zh_cn": {"title": "Mivo QA · 逾期未回应通报 · 10月27日", "content": [[{"tag": "at", "user_id": "ou_a", "user_name": "A"}]]}}
    json.dump(post, open(f"{d}/post.json", "w")); open(f"{d}/s.txt", "w").write("*Mivo QA · 逾期*\n#1 @A\n")
    # Feishu mode: queued, JSON on stdout (track-followups parses it)
    rc, out = stub(env, "--chat-id", CHAT, "--msg-type", "post", "--content-file", f"{d}/post.json", "--dedupe-key", "mivo-overdue-notice:2026-10-27")
    assert rc == 0 and json.loads(out)["queued"], out
    # Slack mode: "ok …" (the tool checks /^(ok|skip)\b/m)
    rc, out = stub(env, "--text-file", f"{d}/s.txt", "--dedupe-key", "mivo-overdue-notice:2026-10-27")
    assert rc == 0 and out.startswith("ok "), out
    q = [json.loads(l) for l in open(queue)]
    assert [x["kind"] for x in q] == ["feishu.group", "slack.post"], q
    assert q[0]["content"] == post and q[0]["chat_id"] == CHAT and q[1]["text"].startswith("*Mivo QA")
    # already sent -> skipped, nothing queued (Feishu: feishu-sent.json key; Slack: key or key:slack in report/slack/sent.json)
    json.dump({"mivo-overdue-notice:2026-10-28": {}}, open(f"{qa}/feishu-sent.json", "w"))
    json.dump({"mivo-overdue-notice:2026-10-28:slack": {}}, open(f"{qa}/report/slack/sent.json", "w"))
    rc, out = stub(env, "--chat-id", CHAT, "--msg-type", "post", "--content-file", f"{d}/post.json", "--dedupe-key", "mivo-overdue-notice:2026-10-28")
    assert rc == 0 and json.loads(out)["skipped"], out
    rc, out = stub(env, "--text-file", f"{d}/s.txt", "--dedupe-key", "mivo-overdue-notice:2026-10-28")
    assert rc == 0 and out.startswith("skip "), out
    assert len(open(queue).read().splitlines()) == 2
    # bad args / no queue -> error (non-zero: the tool records an error, never "sent")
    assert stub(env, "--chat-id", CHAT, "--msg-type", "text", "--content-file", f"{d}/post.json", "--dedupe-key", "k")[0] == 2
    assert stub(dict(env, PI_FOLLOWUP_QUEUE=""), "--text-file", f"{d}/s.txt", "--dedupe-key", "k")[0] == 2

    # followup.to_post_actions
    spec = importlib.util.spec_from_file_location("fu", f"{B}/followup.py"); fu = importlib.util.module_from_spec(spec); spec.loader.exec_module(fu)
    fu.QA = qa; od = f"{d}/out"; os.makedirs(od)
    acts = fu.to_post_actions(q + [{"kind": "slack.post", "text": "x"}], od, "2026-10-27-remind-1527")
    assert [(a["key"], a["type"]) for a in acts] == [("mivo-overdue-notice:2026-10-27", "feishu.group"), ("mivo-overdue-notice:2026-10-27:slack", "slack.post")], acts
    assert acts[0]["msg_type"] == "post" and json.load(open(acts[0]["content_file"])) == post and acts[0]["chat_id"] == CHAT
    assert open(acts[1]["body_file"]).read().startswith("*Mivo QA") and "channel" not in acts[1]

    # outbound feishu.group post: allowlist, then panoptes' sender with the same dedupe key
    sys.modules["merger"] = types.SimpleNamespace(FIELDS="number", blockers=lambda p: [])
    spec = importlib.util.spec_from_file_location("ob", f"{B}/outbound.py"); O = importlib.util.module_from_spec(spec); spec.loader.exec_module(O)
    O.QA = qa; O.feishu_token = lambda: "t"
    class R:
        def __init__(self, j): self.j = j
        def read(self): return json.dumps(self.j).encode()
    O.urllib.request.urlopen = lambda req, timeout=30: R({"code": 0, "data": {"name": "MivoCanvas"}})
    calls = []; O.run = lambda cmd, **kw: (calls.append(cmd), '{"ok": true, "message_id": "om_1"}')[1]
    a = acts[0]
    who, state, validate, do = O.handler(a)
    try: validate(); raise AssertionError("chat not on the allowlist must be refused")
    except RuntimeError as e: assert "allowed-chats" in str(e), e
    json.dump([{"chat_id": CHAT, "name": "MivoCanvas"}], open(f"{qa}/report/allowed-chats.json", "w"))
    assert "MivoCanvas" in validate() and state() == "pending"
    assert do() == "sent"
    c = calls[-1]
    assert c[1].endswith("report/send-feishu-report.mjs") and c[c.index("--msg-type") + 1] == "post" and c[c.index("--dedupe-key") + 1] == a["key"]
    O.run = lambda cmd, **kw: '{"ok": true, "skipped": true}'
    assert do() == "skipped (already sent)"
    O.run = lambda cmd, **kw: '{"ok": false, "error": "chat_not_allowed"}'
    try: do(); raise AssertionError("a failed send must raise")
    except RuntimeError as e: assert "chat_not_allowed" in str(e)
print("ok test_followup_overdue")
