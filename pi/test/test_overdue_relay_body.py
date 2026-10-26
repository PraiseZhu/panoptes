"""python3 test/test_overdue_relay_body.py — 10-26: overdue:aj0928/aj0928-handoff#6 was skipped by outbound with
「relay: empty body」 because handoff.py recorded the feishu.dm+group action with no text. No network, no sends."""
import sys, os, types, datetime as dt
B = os.environ.get("PI_BIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"); sys.path.insert(0, B)
sys.modules["merger"] = types.SimpleNamespace(FIELDS="number", blockers=lambda p: [])   # reads a box-only policy file; not under test
import outbound, handoff
# the action exactly as recorded on 10-26 (no text/summary/body_file)
old = {"key": "overdue:aj0928/aj0928-handoff#6", "type": "feishu.dm+group", "issue": "https://github.com/aj0928/aj0928-handoff/issues/6",
       "age_days": 3, "owner": "aj0928", "cc": {"lubinyu-lab": ["aj0928"]}}
t = outbound.relay_item(old, "handoff")          # raised RuntimeError("relay: empty body") before the fix
assert "aj0928/aj0928-handoff#6" in t and "issues/6" in t, t
# new actions carry their own text
rt = {"repo": "aj0928/aj0928-handoff", "owner": "aj0928"}
x = {"issue": {"number": 6}, "n": 1300, "title": "x", "author": None, "url": old["issue"],
     "since": handoff.now_cst() - dt.timedelta(days=3)}
nt = handoff.overdue_text(rt, x)
assert nt.strip() and "#6" in nt and "3 天" in nt, nt
assert "#6" in outbound.relay_item(dict(old, text=nt), "handoff")
# a truly empty relay still refuses
try: outbound.relay_item({"key": "nudge:x", "type": "nudge"}, "pm"); raise SystemExit("empty relay accepted")
except RuntimeError as e: assert "empty body" in str(e)
print("ok overdue relay body")
