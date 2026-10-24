#!/usr/bin/env python3
"""Mivo QA 日报 followup (Grok routines mivo-qa-followup 15:17, mivo-qa-followup-scan 06:17) with the SAME tool,
panoptes report/track-followups.mjs: per DM'd problem (feishu-sent.json "<repo>#<pr>:<fp>" records, PraiseZhu excluded by the
tool) it looks for the author's response since the DM (PR comment/review, a fix PR quoting #N, a handoff-repo receipt) and
drafts a thanks DM (responded; item closed), a reminder DM (no response; at most one per author per day; after 7 reminders
the item is escalated instead), and records handoff receipts / evidence in followup/state.json (build-ledger reads them).
Modes (10-23):
  live   (fleet-mode=pi AND outbound-enabled AND feishu.dm not in outbound-disabled-types): the tool runs for real on the
         live followup/state.json (backup first in out/followup/), with MIVO_QA_DM_SENDER=bin/followup_sender.mjs, which never
         sends — it queues each DM (already-sent dedupe keys come back as skipped, like the real sender) so the state moves
         exactly as on the Grok line; the queue becomes feishu.dm actions keyed by the tool's own dedupe key
         (followup-remind:<login>:<日> / followup-thanks:<login>:<pr:fp,...>) with the tool's text, sent by bin/outbound.py.
  shadow (anything else): --dry-run against a COPY of state.json (MIVO_QA_FOLLOWUP_STATE), live state never written; the
         tool's drafts become the same actions (only executed if the gate opens later).
  3-day overdue group notice (10-24; fleet-standards/Mivo QA 日报.md: "运维合并 followup/group-overdue 之后，要在 15:17 的定时任务
         里加上 --group-overdue"), OFF unless takeover/group-overdue exists (see main): then the live remind run passes
         --group-overdue, with MIVO_QA_GROUP_SENDER / MIVO_QA_SLACK_SENDER
         = bin/followup_post_sender.mjs (queues, never sends). The queued post becomes a feishu.group action (msg_type post, @ the
         authors, key mivo-overdue-notice:<日>) and the Slack copy a slack.post action (#mivo-canvas-qa, key <same>:slack).
usage: followup.py [scan|remind] [--no-remind] [--shadow]   (scan = 06:17 = reply check only = --no-remind; --shadow forces shadow)"""
import shutil
from lib import *
import praise_notice as PN
TOOL = "/workspace/panoptes/report/track-followups.mjs"
SENDER = f"{PI_CODE}/bin/followup_sender.mjs"
POST_SENDER = f"{PI_CODE}/bin/followup_post_sender.mjs"
DISABLED = f"{BASE}/outbound-disabled-types"

def live_ok():
    try: off = {l.strip() for l in open(DISABLED) if l.strip() and not l.startswith("#")}
    except FileNotFoundError: off = set()
    return mode() == "pi" and os.path.exists(OUTBOUND_FLAG) and "feishu.dm" not in off

def login_of(open_id):
    am = json.load(open(f"{QA}/author-map.json"))
    return next((r.get("github_login") for r in am.get("mivo_authors", []) + am.get("roster", []) if r.get("feishu_open_id") == open_id), None)

def to_actions(dms, kind):
    """[{open_id, dedupe_key, text}] -> feishu.dm actions; key = the tool's dedupe key (Grok-compatible feishu-sent record)."""
    acts = []
    for x in dms:
        who = login_of(x.get("open_id"))
        if not who: log(f"skip {x.get('dedupe_key')}: open_id not in author-map"); continue
        acts.append({"key": x["dedupe_key"], "type": "feishu.dm", "to": who, "text": x["text"], "run": kind,
                     "dedupe_against": [f"{QA}/feishu-sent.json (key {x['dedupe_key']})"], "source": "track-followups"})
    return acts

def to_post_actions(items, od, tag):
    """--group-overdue queue entries -> feishu.group (post with @) / slack.post actions. The tool uses one dedupe key for both
    messages; outbound keys are unique, so the Slack copy is <key>:slack (the queue stub dedupes against both forms)."""
    acts = []
    for i, x in enumerate(items):
        k = x.get("dedupe_key")
        if not k: log(f"skip queued {x.get('kind')} without dedupe_key"); continue
        if x.get("kind") == "feishu.group":
            p = f"{od}/{tag}-overdue-{i}.post.json"; write_atomic(p, json.dumps(x["content"], ensure_ascii=False))
            acts.append({"key": k, "type": "feishu.group", "chat_id": x.get("chat_id"), "msg_type": "post", "content_file": p,
                         "dedupe_against": [f"{QA}/feishu-sent.json (key {k})"], "source": "track-followups --group-overdue"})
        elif x.get("kind") == "slack.post":
            p = f"{od}/{tag}-overdue-{i}.slack.txt"; write_atomic(p, x["text"])
            acts.append({"key": f"{k}:slack", "type": "slack.post", "body_file": p, **({"channel": x["channel"]} if x.get("channel") else {}),
                         "dedupe_against": [f"{QA}/report/slack/sent.json (key {k}:slack)"], "source": "track-followups --group-overdue"})
        else: log(f"skip queued entry of unknown kind {x.get('kind')}")
    return acts

def main():
    a = [x for x in sys.argv[1:] if not x.startswith("--")]; kind = a[0] if a else ("scan" if now_cst().hour < 12 else "remind")
    d = today(); od = f"{BASE}/out/followup"; os.makedirs(od, exist_ok=True); hm = now_cst().strftime("%H%M")
    live = live_ok() and "--shadow" not in sys.argv
    no_remind = kind == "scan" or "--no-remind" in sys.argv
    # 3-day overdue group notice: live remind run, and only while takeover/group-overdue exists (OFF by default). 10-24 dry-run
    # preview would have named Praise in the group for #884/#898/#910/#923, whose fixes are merged and wait for retest (the tool
    # does not count a fix PR that covers several reports as a response) -- switch it on once the tool's detection is right.
    overdue = live and not no_remind and os.path.exists(f"{BASE}/takeover/group-overdue")
    args = ["node", TOOL] + ([] if live else ["--dry-run"]) + (["--no-remind"] if no_remind else []) + (["--group-overdue"] if overdue else [])
    env = dict(os.environ)
    if live:
        bak = f"{od}/state-before-{d}-{kind}-{hm}.json"; shutil.copy2(f"{QA}/followup/state.json", bak); log("backup", bak)
        queue = f"{od}/{d}-{kind}-{hm}.queue.jsonl"; open(queue, "w").close()
        env.update(MIVO_QA_DM_SENDER=SENDER, PI_FOLLOWUP_QUEUE=queue, MIVO_QA_GROUP_SENDER=POST_SENDER, MIVO_QA_SLACK_SENDER=POST_SENDER)
    else:
        st = f"{od}/state-copy-{d}-{kind}.json"; shutil.copy(f"{QA}/followup/state.json", st); env["MIVO_QA_FOLLOWUP_STATE"] = st
    log(f"mode={'live' if live else 'shadow'}: {' '.join(args[1:])}")
    r = subprocess.run(args, capture_output=True, text=True, timeout=900, env=env)
    log("track-followups rc", r.returncode, r.stderr[-300:])
    try: s = json.loads(r.stdout[r.stdout.index("{"):])
    except Exception: s = {"parse_error": r.stdout[-500:]}
    write_atomic(f"{od}/{d}-{kind}.json", json.dumps(s, ensure_ascii=False, indent=1))
    if live: queued = [json.loads(l) for l in open(queue) if l.strip()]
    else: queued = s.get("drafts") or []
    acts = to_actions([x for x in queued if not x.get("kind")], kind) + to_post_actions([x for x in queued if x.get("kind")], od, f"{d}-{kind}-{hm}")
    # 7 reminders without a response -> the tool escalates (stops reminding); tell Praise once per item (relay -> his Slack DM)
    for e in s.get("escalated") or []:
        acts.append({"key": f"followup-escalated:{e.get('pr')}:{e.get('login')}", "type": "grokbot.chat-to-Praise", "to": "Praise", "at": now_cst().isoformat(timespec="seconds"),
                     "text": PN.text(PN.item("action", f"作者一直没回 PR #{e.get('pr')} 的问题",
                         f"QA 在 #{e.get('pr')} 上测出的问题，已经私信提醒 @{e.get('login')} {e.get('reminders')} 次，对方一直没回应（没在 PR 下留言、没开修复 PR、交接仓里也没回）。系统已经停止提醒。",
                         "这些问题没人处理，也没有结论。", todo="决定下一步：当面找作者问一下，或者让修测试的会话把这条标成「不修」或转给别人",
                         link=f"https://github.com/{REPO}/pull/{e.get('pr')}")), "source": "track-followups"})
    log(f"DM actions {len(acts)} (reminded {len(s.get('reminded') or [])}, thanked {len(s.get('thanked') or [])}, escalated {len(s.get('escalated') or [])}, errors {len(s.get('errors') or [])})")
    record_actions("followup", d, acts)
    if r.returncode: sys.exit(1)
if __name__ == "__main__": main()
