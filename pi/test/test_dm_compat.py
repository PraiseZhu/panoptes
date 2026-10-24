"""python3 test/test_dm_compat.py — Grok-form DM records for dm:<day>:<pr> keys (pure parts + apply on a temp file).
PI_BIN overrides the code dir (staging review); default ../bin next to this file."""
import sys, os, json, tempfile; sys.path.insert(0, os.environ.get("PI_BIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import dm_compat as C
R = "xindong/mivo-canvas-plugin"
rep = {"findings": [
    {"severity": "P2", "source": "coverage-gate", "fingerprint": "mq1-c"},
    {"severity": "P1", "source": "code-review", "fingerprint": "mq1-a"},
    {"severity": "P2", "source": "code-review", "fingerprint": "mq1-b"},
    {"severity": "P2", "source": "ai-test", "fingerprint": "mq1-lead"},
    {"severity": "P2", "source": "check", "status": "unverified", "test_backed": True, "fingerprint": "mq1-probe"},
    {"severity": "P2", "source": "check", "status": "flaky", "test_backed": True, "fingerprint": "mq1-flaky"},
    {"severity": "P2", "source": "check", "test_backed": True, "fingerprint": "mq1-unit"},
    {"severity": "P3", "source": "code-review", "fingerprint": "mq1-p3"}]}
# The rule follows when the DM was sent (Praise 2026-10-23 23:18): from 2026-10-24 00:00 Asia/Shanghai the unified rule for a new
# notice (= what daily.py put in the DM); before that the rule of the time -- problems already sent keep the old flow.
NEW, OLD = "2026-10-24T01:00:00Z", "2026-10-23T03:50:08Z"
assert C.fingerprints(rep, NEW) == ["mq1-b", "mq1-unit"]     # main problem first; unverified AI-only P1, leads / 待确认 / flaky / P3 / coverage-only P2 out
assert C.fingerprints(rep, OLD) == ["mq1-a", "mq1-b", "mq1-unit", "mq1-c"]   # old flow: the AI P1 and the coverage P2 that DM carried stay
assert C.fingerprints(rep) == C.fingerprints(rep, OLD)          # no send time -> old flow
PL = {"findings": [{"severity": "P1", "source": "code-review", "verdict": "PLAUSIBLE", "fingerprint": "mq1-pl"}]}   # #1191 shape
assert C.fingerprints(PL, NEW) == [] and C.fingerprints(PL, OLD) == ["mq1-pl"]   # sent 10-23 -> its follow-up goes on (the author's bot re-checks)
for t in (NEW, OLD):
    assert C.fingerprints({"findings": [], "pending_verification": [{"severity_if_confirmed": "P1", "pending_kind": "review-p1-unconfirmed", "fingerprint": "mq1-pv"}]}, t) == []
    assert C.fingerprints({"findings": [{"severity": "P2", "source": "check", "status": "unverified", "test_backed": True, "fingerprint": "x"}]}, t) == []  # #434 shape
assert C.fingerprints({"findings": [{"severity": "P1", "source": "code-review", "verification": {"status": "confirmed"}, "fingerprint": "mq1-v"}]}, NEW) == ["mq1-v"]
assert C.first_sent_at({"sent_at": "2026-10-24T03:50:05Z", "grok_line_send": {"sent_at": "2026-10-23T15:38:00.000Z"}}) == "2026-10-23T15:38:00.000Z"
assert C.first_sent_at({"sent_at": "2026-10-24T00:10:00+08:00"}) == "2026-10-24T00:10:00+08:00" and C.first_sent_at({}) is None
k, rec = C.compat_entry("dm:2026-10-22:1156", {"open_id": "ou_x", "sent_at": "2026-10-23T03:50:08Z", "message_id": "om_1"}, ["mq1-a", "mq1-b"])
assert k == f"{R}#1156:mq1-a" and rec == {"open_id": "ou_x", "sent_at": "2026-10-23T03:50:08Z", "message_id": "om_1", "covered_fingerprints": ["mq1-b"], "alias_of": "dm:2026-10-22:1156", "by": "pi-line dm-compat"}
assert C.compat_entry("dm:2026-10-22:1196", {"open_id": "o", "sent_at": "2026-10-23T03:50:05Z", "message_id": "om_pi", "grok_line_send": {"sent_at": "2026-10-23T00:38:00.000Z", "message_id": None}}, ["f"])[1]["sent_at"] == "2026-10-23T00:38:00.000Z"
assert C.compat_entry("dm:adhoc:abc", {"sent_at": "x"}, ["mq1-a"]) is None and C.compat_entry("dm:2026-10-22:1", {"sent_at": "x"}, []) is None
sent = {f"{R}#900:mq1-z": {"open_id": "o", "sent_at": "t"}, "dm:2026-10-22:900": {"sent_at": "t"},       # PR already has a Grok record
        "dm:2026-10-22:1156": {"open_id": "ou_x", "sent_at": "2026-10-23T03:50:08Z", "message_id": "om_1"},
        "dm:2026-10-22:434": {"open_id": "ou_y", "sent_at": "2026-10-23T03:50:13Z"}, "mivo-daily-report:2026-10-22": {}}
loader = lambda pr: {1156: rep, 900: rep, 434: {"findings": [{"severity": "P2", "source": "check", "status": "unverified", "test_backed": True, "fingerprint": "p"}]}}.get(pr)
rows = C.plan(sent, loader)
assert [(r[0], r[1], r[3] is None) for r in rows] == [("dm:2026-10-22:900", None, False), ("dm:2026-10-22:1156", f"{R}#1156:mq1-a", True), ("dm:2026-10-22:434", None, False)]
assert [r[0] for r in C.plan(sent, loader, skip={1156}) if r[3] is None] == []
with tempfile.TemporaryDirectory() as d:
    p = f"{d}/feishu-sent.json"; json.dump(sent, open(p, "w"))
    assert C.apply(p, rows) == [f"{R}#1156:mq1-a"] and C.apply(p, rows) == []          # idempotent, never overwrites
    s2 = json.load(open(p)); assert s2[f"{R}#1156:mq1-a"]["covered_fingerprints"] == ["mq1-b", "mq1-unit", "mq1-c"] and s2["dm:2026-10-22:1156"] == sent["dm:2026-10-22:1156"]
    assert not os.path.exists(p + ".lock")
    s2["dm:2026-10-24:1300"] = {"open_id": "ou_z", "sent_at": "2026-10-24T01:00:00Z", "message_id": "om_9"}; json.dump(s2, open(p, "w"))
    # outbound hook: only that key; a DM sent on 10-24 never mentioned the unverified AI P1, so it is not its main problem
    assert C.record(p, "dm:2026-10-24:1300", lambda pr: rep) == [f"{R}#1300:mq1-b"]
    assert json.load(open(p))[f"{R}#1300:mq1-b"]["covered_fingerprints"] == ["mq1-unit"]
    assert C.record(p, "followup-remind:aj0928:2026-10-24") == []
print("ok test_dm_compat")
