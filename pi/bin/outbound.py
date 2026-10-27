#!/usr/bin/env python3
"""Pi line outbound executor. Reads out/<job>/<date>.actions.json and executes pending actions ONLY when
fleet-mode == pi AND $PI_STATE/outbound-enabled exists (PI_FLEET_MODE env may override the mode
for simulation; the flag file has no override). Idempotent: out/outbound/ledger.json done-ledger + each handler
checks the target's real state before acting. Never prints secrets.
Usage: outbound.py [--dry-run] [--days N (default 2)] [--job JOB] [--key KEY]
       outbound.py [--dry-run] --resend-key KEY [--date YYYY-MM-DD]
  --dry-run: print what would go to whom; validate every target with read-only calls only.
  --resend-key: one-shot 补发 of exactly that key (10-26): ignores the --days window (--date picks the actions day when the key
    is in more than one) and the relay staleness TTL, and the Slack channel-history check for a 【补发】 daily body. Still refuses
    a key the ledger has as done/sending/uncertain/gave_up, or skipped for any reason but staleness; records done after the send,
    so a second run is a no-op."""
import argparse, urllib.request, urllib.parse, tempfile, shutil
from lib import *
import merger
import dm_compat, jsonrec
import praise_notice as PN
import workroot as W
QA_SLACK = f"{QA}/report/slack"
SLACK_CHANNEL = "C0CAH3W61N2"           # Mivo QA channel; the only channel send-slack-report.mjs allows
PRAISE_SLACK = "U01D851JXL7"
LEDGER = f"{BASE}/out/outbound/ledger.json"
HANDOFF_REPO = {"aj0928": "aj0928/aj0928-handoff", "zhongxingtian-ai": "zhongxingtian-ai/handover-mivo"}
GROK_ONLY = ()   # 10-23: nothing is dropped any more; Grok-only types are relayed to Praise by Slack DM (RELAY_TYPES)
# Praise 10-23: messages that used to go to Grok chats/groups/agents go to his Slack DM instead. Urgent ones get their own DM
# right away; the rest are batched into one digest DM per run. Older than RELAY_TTL_H -> skipped as stale, never back-filled.
RELAY_TYPES = {"SendToAgent": "原发 Grok agent", "grokbot.chat-to-Praise": "原发 Grok 私聊", "grokbot.group": "原发 Grok 群",
               "grok.group": "原发 Grok 群", "group-post": "原发群", "nudge": "原发 Praise PR 催办", "feishu.dm+group": "原发飞书私信+群",
               "grok.chat.praise": "原发 Grok 私聊"}
URGENT_RELAY = {"grok.chat.praise"}   # Cindy keep-alive / sign-out alerts
RELAY_TTL_H = 6
RESEND_PREFIX = "【补发】"
DAILY_HEAD_RE = re.compile(r"^(?:【补发】)?Mivo 播报 \d+ 月 \d+ 日（北京时间）：")
def resend_blocker(e):
    """--resend-key: why the ledger entry e forbids a 补发 (None = may send). Only a stale-relay skip is overridable."""
    st = (e or {}).get("status")
    if not st or st == "failed": return None
    if st == "skipped" and str(e.get("reason", "")).startswith("stale relay"): return None
    return f"ledger has it as {st}" + (f" ({e.get('reason')})" if e.get("reason") else "") + "; not resent"
FAIL_GIVE_UP = 5                      # failures (with backoff) before an action is given up and Praise is told once
GATES = f"{PI_CODE}/conf/gates.json"     # {"no_fix_pr": {"923": "reason"}} -- shared with prfix.py
GROK_SENT = f"{QA}/report/sent"          # Grok 日报 line per-day send records: slack-sent.json, run-notes.txt, created.tsv
DISABLED_TYPES = f"{BASE}/outbound-disabled-types"   # one action type per line (e.g. feishu.dm) -> never sent
GIT_NAME, GIT_EMAIL = "PraiseZhu", "zhuzan@xd.com"   # matches PraiseZhu commits in mivo-canvas-plugin
MERGER_MARK = "<!-- mivo-merger -->"
MERGE_DAY_CAP, MERGE_ROUND_CAP = 2, 1                  # policy maxMergesPerDay (canary) / maxMergesPerRound
TERMINAL = ("done", "skipped", "gave_up", "uncertain")  # ledger statuses never retried (uncertain = a send was interrupted:
                                                       # Slack/Feishu cannot be checked remotely, so never re-sent blindly)
def backoff_left(e):
    """all jobs (10-23; was merger only): after n failures wait 10min*2^(n-1) (cap 6h) from last failure. Minutes left (0 = go)."""
    if not e or e.get("status") != "failed": return 0
    n = e.get("fails", 1); wait = min(10 * 2 ** (n - 1), 360)
    try: past = (now_cst() - dt.datetime.fromisoformat(e["at"])).total_seconds() / 60
    except Exception: return 0
    return max(0, round(wait - past))
def merges_today(ledger):
    d = today()
    return sum(1 for k, v in ledger.items() if k.startswith("merge:") and v.get("status") == "done" and v.get("by") == "pi-line" and str(v.get("at", "")).startswith(d))
DRAFT_MARKERS = ("SHADOW", "NOT SENT", "<!--")
SHADOW_RE = re.compile(r"<!--.*?-->\s*", re.S)

MEMINFO = "/proc/meminfo"
LOWMEM = lambda: os.path.join(os.path.dirname(LEDGER), "lowmem.json")   # present while sends are held back for low memory (watchdog alerts)
MIN_MEM_MB, MIN_SWAP_MB = 1536, 2048            # 10-27 outbound floor: MemAvailable < 1.5G or SwapFree < 2G -> send nothing this run
def mem_floor():
    """None if memory is fine, else why. Unreadable meminfo -> None (do not block sends on a probe error)."""
    try: m = {l.split(":")[0]: int(l.split()[1]) // 1024 for l in open(MEMINFO) if l.split(":")[0] in ("MemAvailable", "SwapFree")}
    except Exception: return None
    if m.get("MemAvailable", MIN_MEM_MB) < MIN_MEM_MB or m.get("SwapFree", MIN_SWAP_MB) < MIN_SWAP_MB:
        return f"MemAvailable {m.get('MemAvailable')}MB (floor {MIN_MEM_MB}), SwapFree {m.get('SwapFree')}MB (floor {MIN_SWAP_MB})"
    return None

def disabled_types():
    try: return {l.strip() for l in open(DISABLED_TYPES) if l.strip() and not l.startswith("#")}
    except FileNotFoundError: return set()
def clean_body(path):
    """Strip the shadow header, then HARD-refuse if any draft marker survives. Returns a temp file path."""
    t = SHADOW_RE.sub("", open(path).read()).lstrip()
    bad = [m for m in DRAFT_MARKERS if m in t]
    if bad: raise RuntimeError(f"refused: body contains draft marker(s) {bad}")
    f = tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt"); f.write(t); f.close(); return f.name
def guard_text(path):
    t = open(path).read(); bad = [m for m in DRAFT_MARKERS if m in t]
    if bad: raise RuntimeError(f"refused: outbound body contains draft marker(s) {bad}")
    return path
def grok_day(day):
    """What the Grok 日报 line already sent for data-day `day` (from report/sent/<day>/)."""
    d = f"{GROK_SENT}/{day}"; notes = ""
    try: notes = open(f"{d}/run-notes.txt").read()
    except Exception: pass
    dm_line = next((l for l in notes.splitlines() if l.startswith("私信")), "")
    dm_prs = {int(x) for x in re.findall(r"#(\d+)", re.sub(r"[（(]含?交接[^）)]*[）)]|交接 ?#\d+", "", dm_line))}   # drop "(含交接 #6 #7)" issue numbers
    return {"slack": jload(f"{d}/slack-sent.json"), "dm_prs": dm_prs,
            "dm_files": {int(m) for m in (re.findall(r"dm-(\d+)\.txt", " ".join(os.listdir(d))) if os.path.isdir(d) else [])}}
def slack_history_has(ch, day):
    """conversations.history: does the Mivo bot already have a '<M> 月 <D> 日' daily report in ch?"""
    y, m, dd = map(int, day.split("-")); want = f"Mivo 播报 {m} 月 {dd} 日"
    me = slack("auth.test").get("user_id")
    oldest = (dt.datetime(y, m, dd, tzinfo=dt.timezone(dt.timedelta(hours=8))) + dt.timedelta(days=1)).timestamp()
    h = slack("conversations.history", {"channel": ch, "oldest": f"{oldest:.0f}", "limit": 200})
    return next((x["ts"] for x in h.get("messages", []) if x.get("user") == me and x.get("text", "").startswith(want)), None)

def jload(f, d=None):
    try: return json.load(open(f))
    except Exception: return d
def slack(method, params=None, post=False):
    tok = os.environ.get("MIVO_SLACK_BOT_TOKEN")
    if not tok: raise RuntimeError("MIVO_SLACK_BOT_TOKEN unset")
    url = f"https://slack.com/api/{method}" + ("" if post else "?" + urllib.parse.urlencode(params or {}))
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"},
                                 data=json.dumps(params).encode() if post else None)
    if post: req.add_header("Content-Type", "application/json; charset=utf-8")
    j = json.load(urllib.request.urlopen(req, timeout=30))
    if not j.get("ok"): raise RuntimeError(f"slack {method}: {j.get('error')}")
    return j
def feishu_token():
    aid, sec = os.environ.get("MIVO_FEISHU_APP_ID"), os.environ.get("MIVO_FEISHU_APP_SECRET")
    if not (aid and sec): raise RuntimeError("MIVO_FEISHU_APP_ID/SECRET unset")
    req = urllib.request.Request("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=json.dumps({"app_id": aid, "app_secret": sec}).encode(), headers={"Content-Type": "application/json"})
    j = json.load(urllib.request.urlopen(req, timeout=30))
    if j.get("code") != 0: raise RuntimeError(f"feishu token code={j.get('code')}")
    return j["tenant_access_token"]
def authors():
    d = jload(f"{QA}/author-map.json", {}); a = d.get("mivo_authors", {})
    return list(a.values()) if isinstance(a, dict) else a
def author_by_name(name):
    return next((x for x in authors() if name in (x.get("name"), x.get("github_login"))), None)
def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, **kw)
    if r.returncode != 0: raise RuntimeError(f"{os.path.basename(cmd[0])} rc={r.returncode}: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout
def body_of(a):
    f = a.get("body_file")
    if f and os.path.exists(f): return f
    if a.get("text"):
        t = tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt"); t.write(a["text"]); t.close(); return t.name
    return None
def feishu_text(a):
    n, lvl = a.get("pr"), a.get("level", "")
    f, r = load_report(n) if n else (None, None)
    t = f"【Mivo QA】你的 PR #{n} 在合并后测试里有 {lvl} 问题，请查看：https://github.com/{REPO}/pull/{n}"
    if a.get("handoff_url"): t += f"\n交接 issue：{a['handoff_url']}"
    t += "\n\n" + feedback_block(n)
    tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt"); tmp.write(t); tmp.close(); return tmp.name

# 私信 / 提醒固定结尾（2026-10-23 回执统一成一种；和 panoptes report/REPORT-STYLE.md、track-followups.mjs feedbackBlock 同文）
def feedback_block(n):
    return ("怎么告诉我处理好了（选一种）：\n"
            f"· 修了：开修复 PR，标题或描述里写「QA #{n}」，合并后自动算已修\n"
            "· 不修 / 误报：在原 PR 下留言，第一行写「结论：不修，原因…」或「结论：误报，原因…」（多条时写明是哪条）\n"
            "· 飞书私信回复我收不到，别回这里\n"
            "这几种我每天都会自动查，看到了就不再提醒 🙃")

def gates():
    try: return {int(k): v for k, v in json.load(open(GATES)).get("no_fix_pr", {}).items()}
    except Exception: return {923: "Praise 闸：#923 修复 PR 不得创建"}
def action_age_h(a, f):
    try: t = dt.datetime.fromisoformat(a["at"])
    except Exception: t = dt.datetime.fromtimestamp(os.path.getmtime(f), CST)   # pre-10-23 actions carry no "at"
    return (now_cst() - t).total_seconds() / 3600
# 外发执行器出问题时，用人话说是哪一类消息 / 操作（key 前缀 → 说法）
WHAT_OF = {"finding": "「你的 PR 测出新问题」的通知", "cired": "「修复 PR 测试没过」的通知", "norepro": "「问题复现不了」的通知", "needs-human": "「要你拍板」的通知",
           "fixpr": "开修复 PR 的操作", "ready": "把草稿 PR 转正式的操作", "rerun": "重跑测试的操作", "update-branch": "同步 main 的操作", "merge": "合并 PR 的操作",
           "revise-needs-human": "「修复 PR 的 CI 修不动了」的通知", "revise": "把 CI 修正推到修复 PR 上的操作",
           "dm": "给作者的飞书私信", "handoff": "开交接 issue 的操作", "mivo-daily-report": "QA 日报", "followup-remind": "提醒作者的私信",
           "followup-thanks": "感谢作者的私信", "watchdog": "看门狗的通知", "boxhealth": "机器体检的通知", "opsenv": "测试环境自检的通知", "infra": "测试系统自修的通知"}
def what_of(k, t=""):
    for p, w in WHAT_OF.items():
        if k.startswith(p): return w
    return "消息" if t.startswith(("slack.", "feishu.")) or t in RELAY_TYPES else "自动操作"
def relay_text(a):
    for f in (a.get("body_file"), a.get("text_file")):
        if f:
            p = f if os.path.isabs(f) else os.path.join(BASE, f)
            if os.path.exists(p): return open(p).read()
    if a.get("text") or a.get("summary"): return a.get("text") or a.get("summary")
    if str(a.get("key", "")).startswith("overdue:") and a.get("issue"):   # actions recorded before handoff.py wrote "text"
        return f"交接 issue {a['key'][len('overdue:'):]} 开了 {a.get('age_days', '?')} 天还没有回执，负责人 {a.get('owner') or '?'}。\n{a['issue']}"
    return ""
# 原来发给 Grok / 别的 bot / 群的消息转给 Praise 时的标题（2026-10-24 Praise：私信看不懂 —— 不再用「【job · 原发 Grok 私聊 → X】」这种内部抬头）
# 先按 key 前缀（同一个 out/<job>/ 目录里有几种），再按 job（= out/ 下的目录名，pmnudge 也写在 out/pm/）
RELAY_KEY_TITLES = {"pm-chief:": "白天催办汇总", "nudge:": "白天催办", "pm-cos:": "测试项目经理的核对汇总", "pm-retro:": "测试项目经理的核对汇总"}
RELAY_TITLES = {"jasper": "今天的工作简报", "pm": "测试项目经理的核对汇总",
                "dailyretro": "日报复盘", "prretro": "修 PR 的复盘", "opsreview": "测试系统运维复盘", "followup": "作者跟进", "daily": "QA 日报",
                "p2": "P2 裁决建议", "compare": "Pi 线和 Grok 线的对比"}
def relay_item(a, job):
    t = re.sub(r"[（(](?:草稿，?)?未发送[）)]|（草稿）", "", SHADOW_RE.sub("", relay_text(a))).strip()   # shadow-era labels
    if not t: raise RuntimeError("relay: empty body")   # draft markers are not refused here: private DM to Praise, not a public post
    if t.startswith(tuple(PN.KINDS.values())): return t   # already written in the praise_notice skeleton
    to = "" if (a.get("to") or "Praise") == "Praise" else ("原来是发给 Jasper（Chief of Staff）的" if str(a.get("to")).startswith("Jasper") else f"原来是发给 {a.get('to')} 的")
    body = t if len(t) <= 1200 else t[:1200] + "\n…（太长截断了，全文在机器上的 " + (a.get('body_file') or a.get('text_file') or 'actions.json') + "）"
    title = next((v for p, v in RELAY_KEY_TITLES.items() if str(a.get("key", "")).startswith(p)), None) or RELAY_TITLES.get(job, f"「{PN.job_name(job)}」的汇总")
    return PN.render(PN.item("fyi", title,
                             (to + "，按你 10-23 的要求转给你看。" if to else "按你 10-23 的要求转给你看。") + "内容如下：\n" + body))

# ---- handlers: state(a) -> "done"/"pending"/"stale:<why>"; validate(a) -> str (read-only); do(a) -> str ----
def h_slack_post(a):
    ch = a.get("channel", SLACK_CHANNEL)
    day = (re.search(r"(\d{4}-\d\d-\d\d)", a["key"]) or [None, None])[1]
    is_daily = a["key"].startswith("mivo-daily-report:")
    def state():
        if a["key"] in jload(f"{QA_SLACK}/sent.json", {}): return "done"
        if is_daily and day:
            if grok_day(day)["slack"]: return "done"
            # --resend-key with a 【补发】 body: main() checked the key is not done in the ledger; an older message for the same day
            # in the channel is exactly what is being re-sent, so the history check must not swallow it (sent.json still applies)
            if a.get("_resend") and open(body_of(a)).read().lstrip().startswith(RESEND_PREFIX): return "pending"
            if ch.startswith("C") and slack_history_has(ch, day): return "done"
        return "pending"
    def payload():
        f = clean_body(body_of(a))
        if is_daily and not DAILY_HEAD_RE.match(open(f).read()):
            raise RuntimeError("refused: daily body does not start like the Grok report ('[【补发】]Mivo 播报 M 月 D 日（北京时间）：')")
        return f
    def validate():
        payload()
        if not (ch == SLACK_CHANNEL or re.match(r"^U[A-Z0-9]+$", ch)): raise RuntimeError(f"channel {ch} not allowed")
        if not body_of(a): raise RuntimeError("no body_file")
        who = slack("auth.test")
        if ch.startswith("C"):
            try: info = slack("conversations.info", {"channel": ch})["channel"]; return f"bot={who.get('user')} #{info.get('name')} member={info.get('is_member')}"
            except RuntimeError as e:
                if "missing_scope" not in str(e): raise
                prev = sum(1 for v in jload(f"{QA_SLACK}/sent.json", {}).values() if isinstance(v, dict) and v.get("channel", SLACK_CHANNEL) == ch)
                return f"bot={who.get('user')} auth ok; channel info needs channels:read (prior sends to {ch} in sent.json: {prev})"
        return f"bot={who.get('user')} user={slack('users.info', {'user': ch})['user'].get('real_name')}"
    def do():
        f = payload()   # daily -> main message + thread replies, same builder the Grok line uses (--content-file)
        return run(["node", f"{QA_SLACK}/send-slack-report.mjs", "--content-file" if is_daily else "--text-file", f, "--dedupe-key", a["key"], "--channel", ch]).strip()[-200:]
    return f"Slack {ch}", state, validate, do
def h_slack_dm(a):
    ids = jload(f"{QA_SLACK}/slack-ids.json", {})
    uid = a.get("user") or next((v["slack_id"] for k, v in ids.items() if k != "_meta" and a.get("to") in (k, v.get("name"))), None)
    if a.get("to") == "Praise": uid = PRAISE_SLACK
    a = {**a, "channel": uid or "UNKNOWN"}
    return h_slack_post(a)
def h_feishu_dm(a):
    au = author_by_name(a.get("to", "")) or {}
    oid = au.get("feishu_open_id")
    def state():
        sent = jload(f"{QA}/feishu-sent.json", {})   # Grok line keys are "<repo>#<pr>:<fingerprint>"
        if a["key"] in sent or any(k.startswith(f"{REPO}#{a.get('pr')}:") for k in sent): return "done"
        day = (re.search(r"(\d{4}-\d\d-\d\d)", a["key"]) or [None, None])[1]
        g = grok_day(day) if day else {"dm_prs": set(), "dm_files": set()}
        if a.get("pr") in g["dm_prs"] | g["dm_files"]: return "done"   # Grok run-notes / dm-<pr>.txt for that day
        return "pending"
    def validate():
        if not oid: raise RuntimeError(f"no feishu_open_id for {a.get('to')}")
        if au.get("notify_via_feishu") is False: raise RuntimeError("recipient notify_via_feishu=false")
        feishu_token(); return f"tenant token ok, open_id …{oid[-6:]}"
    def do():
        if not body_of(a) and not a.get("pr"):   # 10-23: feishu_text() needs a PR; without body and pr it would send "PR #None"
            raise RuntimeError("refused: feishu.dm action has neither body/text nor pr")
        res = run(["node", f"{QA}/scripts/send-feishu-dm.mjs", "--open-id", oid, "--text-file", guard_text(clean_body(body_of(a)) if body_of(a) else feishu_text(a)), "--dedupe-key", a["key"]]).strip()[-200:]
        if a["key"].startswith("dm:"):           # 10-23: also the Grok-form record, so build-ledger / track-followups see this DM
            try: res += f" compat={dm_compat.record(f'{QA}/feishu-sent.json', a['key'])}"
            except Exception as e: res += f" compat-failed={str(e)[:80]}"   # never fails a DM that was sent
        return res[-200:]
    return f"飞书私信 {a.get('to')}", state, validate, do
def h_feishu_group(a):
    chat = a.get("chat_id"); post = a.get("msg_type") == "post"   # post (10-24): followup --group-overdue notice, @ the authors
    def state(): return "done" if a["key"] in jload(f"{QA}/feishu-sent.json", {}) else "pending"
    def post_file():
        f = a.get("content_file")
        if not (f and os.path.exists(f)): raise RuntimeError("no content_file for the group post")
        json.load(open(f)); return guard_text(f)
    def validate():
        if not chat: raise RuntimeError("no chat_id (日报群 id not configured in action)")
        if post:
            allowed = {x if isinstance(x, str) else (x or {}).get("chat_id") for x in jload(f"{QA}/report/allowed-chats.json", [])}
            if chat not in allowed: raise RuntimeError(f"chat {chat} not in report/allowed-chats.json")
            post_file()
        tok = feishu_token()
        req = urllib.request.Request(f"https://open.feishu.cn/open-apis/im/v1/chats/{chat}", headers={"Authorization": f"Bearer {tok}"})
        j = json.load(urllib.request.urlopen(req, timeout=30))
        if j.get("code") != 0: raise RuntimeError(f"chat info code={j.get('code')}")
        return f"group {j['data'].get('name')}"
    def do():
        if post:   # panoptes' own group sender: report/allowed-chats.json allowlist + feishu-sent.json dedupe on the same key
            out = run(["node", f"{QA}/report/send-feishu-report.mjs", "--chat-id", chat, "--msg-type", "post", "--content-file", post_file(),
                       "--dedupe-key", a["key"]])
            try: r = json.loads(out.strip().splitlines()[-1])
            except (ValueError, IndexError): raise RuntimeError(f"group post: unparseable sender output {out[-160:]}")
            if not r.get("ok"): raise RuntimeError(f"group post: {r.get('error') or r}")
            return "skipped (already sent)" if r.get("skipped") else "sent"
        tok = feishu_token(); text = open(clean_body(body_of(a))).read()
        req = urllib.request.Request("https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            data=json.dumps({"receive_id": chat, "msg_type": "text", "content": json.dumps({"text": text})}).encode(),
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
        j = json.load(urllib.request.urlopen(req, timeout=30))
        if j.get("code") != 0: raise RuntimeError(f"send code={j.get('code')}")
        s = jload(f"{QA}/feishu-sent.json", {}); s[a["key"]] = {"chat_id": chat, "at": now_cst().isoformat(), "by": "pi-line"}
        write_atomic(f"{QA}/feishu-sent.json", json.dumps(s, ensure_ascii=False, indent=1)); return "sent"
    return f"飞书群 {chat}", state, validate, do
def h_issue(a):
    repo = a.get("repo") or HANDOFF_REPO.get(a.get("route_author", ""), "")
    fp = a["key"].split(":")[-1]
    def state():
        iss = jload(f"{QA}/handoff/{a.get('route_author')}/issues.json", {})
        if fp in json.dumps(iss): return "done"
        hits = gh(["issue", "list", "-R", repo, "--state", "all", "--search", f"{fp} in:body", "--json", "number"])
        return "done" if hits else "pending"
    def validate():
        if not repo: raise RuntimeError(f"no handoff repo for {a.get('route_author')}")
        v = gh(["repo", "view", repo, "--json", "nameWithOwner,hasIssuesEnabled,viewerPermission"])
        return f"{v['nameWithOwner']} issues={v['hasIssuesEnabled']} perm={v['viewerPermission']}"
    def do():
        body = (open(a["body_file"]).read() if a.get("body_file") and os.path.exists(a["body_file"]) else f"QA 报告 PR #{a.get('pr')}: https://github.com/{REPO}/pull/{a.get('pr')}")
        body = open(clean_body(body_of({"text": body}))).read() + f"\n\n<!-- pi-line {a['key']} fingerprint={fp} -->"
        return gh(["issue", "create", "-R", repo, "--title", a.get("title", f"PR #{a.get('pr')} QA 交接"), "--body", body], js=False).strip()
    return f"gh issue {repo}", state, validate, do
def h_ready(a):
    n = a["pr"]
    def state():   # 10-23: re-verify at execution time (head unchanged, every check but review-pr green), not only when planned
        p = gh(["pr", "view", str(n), "-R", REPO, "--json", "isDraft,state,headRefOid,statusCheckRollup"])
        if not p["isDraft"]: return "done"
        if p["state"] != "OPEN": return "stale:closed"
        if a.get("head") and a["head"] != p["headRefOid"]: return "stale:head moved"
        bad = [c.get("name") or c.get("context") for c in p.get("statusCheckRollup") or []
               if "review-pr" not in (c.get("name") or c.get("context") or "")
               and (c.get("conclusion") or c.get("state") or "") not in ("SUCCESS", "SKIPPED", "NEUTRAL")]
        return f"stale:checks not green {bad[:3]}" if bad else "pending"
    def validate(): return f"#{n} " + json.dumps(gh(["pr", "view", str(n), "-R", REPO, "--json", "isDraft,state,statusCheckRollup"])["isDraft"] and "draft" or "ready")
    def do(): return gh(["pr", "ready", str(n), "-R", REPO], js=False).strip()
    return f"gh pr ready #{n}", state, validate, do
def h_fixpr(a):
    patch, must = a.get("patch"), a.get("body_must_contain", "")
    def state():
        hits = gh(["pr", "list", "-R", REPO, "--state", "all", "--search", f"\"{must}\" in:body author:@me", "--json", "number,body"]) if must else []
        return "done" if any(must in (h.get("body") or "") for h in hits) else "pending"
    def validate():
        why = fixpr_gate_reason(a)                                                  # (1) gate
        if why: raise RuntimeError(f"gated: {why}")
        if not (patch and os.path.exists(patch)): raise RuntimeError("patch missing")
        v = gh(["repo", "view", REPO, "--json", "viewerPermission,defaultBranchRef"])
        return f"patch {os.path.getsize(patch)}B, perm={v['viewerPermission']}, base={v['defaultBranchRef']['name']}, docs={a.get('docs_dir_predicted') or '-'}"
    def do():
        why = fixpr_gate_reason(a)                                                  # (1) gate, re-read right before pushing
        if why: raise RuntimeError(f"refused: gated ({why})")
        d = W.mkdtemp("pi-fixpr-")   # PI_WORK/tmp: janitor removes what a killed run leaves
        try:
            run(["gh", "repo", "clone", REPO, d, "--", "--depth", "50", "-q"])
            br = "qa-fix/" + a["key"].replace(":", "-")[:60]
            run(["git", "-C", d, "config", "user.name", GIT_NAME]); run(["git", "-C", d, "config", "user.email", GIT_EMAIL])  # repo-local
            run(["git", "-C", d, "checkout", "-b", br]); apply_fix_patch(d, patch, a.get("base"), br)
            md = patch[:-6] + ".md" if patch.endswith(".patch") else None
            title = a.get("title") or f"fix: QA follow-up ({must})"
            run(["git", "-C", d, "commit", "-m", title]); run(["git", "-C", d, "push", "-u", "origin", br])
            body = (open(md).read() if md and os.path.exists(md) else "") + f"\n\n{must}\n<!-- pi-line {a['key']} -->"
            created = gh(["pr", "create", "-R", REPO, "--draft", "--head", br, "--title", title, "--body", body], js=False).strip()
            real = fixpr_real_number(created)                                       # (2) docs/pr/<pred> -> docs/pr/<real>
            try: note = fixpr_docs_rename(d, br, a, real) if real else "docs rename skipped: no PR number in gh output"
            except Exception as e:   # the PR exists now: never fail the action, queue a retry instead
                note = f"docs rename FAILED ({str(e)[:160]}); queued gh.pr.docs-fix"
                record_actions("praise-pr", today(), [{"key": f"fixpr-docs:{real}", "type": "gh.pr.docs-fix", "pr": real, "head": br,
                    "docs_dir_predicted": a.get("docs_dir_predicted") or (f"docs/pr/{a['pred_pr']}" if a.get("pred_pr") else None),
                    "from": a["key"]}])
            return f"{created} | {note}"
        finally: shutil.rmtree(d, ignore_errors=True)
    return f"gh draft fix PR ({must})", state, validate, do
class GiveUp(RuntimeError):
    """an action that can never succeed as written: mark it gave_up at once (no 5 retries); its producer re-plans."""
def apply_fix_patch(d, patch, base=None, br=None):
    """prfix verified the patch on its base; main may have moved since (10-24: a plain `git apply` onto the new main failed 5x ->
    gave_up, ticket stuck). Try main, then a 3-way apply. If neither works, give up at once: a PR branched from the old base would
    conflict with main (DIRTY, GitHub runs no checks), so prfix fixes the ticket again on the current main instead."""
    if subprocess.run(["git", "-C", d, "apply", "--index", patch], capture_output=True).returncode == 0: return "main"
    r = subprocess.run(["git", "-C", d, "apply", "--3way", "--index", patch], capture_output=True, text=True)
    if r.returncode == 0 and not subprocess.run(["git", "-C", d, "diff", "--name-only", "--diff-filter=U"], capture_output=True, text=True).stdout.strip():
        return "main-3way"
    subprocess.run(["git", "-C", d, "reset", "-q", "--hard", "HEAD"], capture_output=True)
    raise GiveUp(f"patch no longer applies on main (verified base {str(base or '?')[:9]}): {(r.stderr or '').strip()[-160:]}")
def h_push_fix(a):
    """gh.pr.push-fix {pr, branch, head, patch, title, message}: prfix --revise fixed red CI on our own draft fix PR (10-24). Plain
    push (never force) onto the PR's qa-fix/* branch, only while the PR is still our open draft and its head is the one that was fixed."""
    n, br, head, patch, key = a["pr"], a.get("branch") or "", a.get("head") or "", a.get("patch"), a["key"]
    def view(): return gh(["pr", "view", str(n), "-R", REPO, "--json", "state,isDraft,author,headRefName,headRefOid,body"])
    def state():
        p = view()
        if p["state"] != "OPEN": return f"stale:{p['state'].lower()}"
        if (p.get("author") or {}).get("login") != "PraiseZhu" or p.get("headRefName") != br or not br.startswith("qa-fix/") \
                or "<!-- pi-line fixpr:" not in (p.get("body") or ""): return "stale:not our bot fix PR"
        if p["headRefOid"] == head: return "pending" if p.get("isDraft") else "stale:no longer a draft"
        msg = gh(["api", f"repos/{REPO}/commits/{p['headRefOid']}", "-q", ".commit.message"], js=False)
        return "done" if f"Pi-Revise: {key}" in msg else "stale:head moved"
    merge = a.get("mode") == "merge"   # prfix --revise merged main into the draft (conflicts): push that merge commit from the bundle
    def validate():
        f = a.get("bundle") if merge else patch
        if not (f and os.path.exists(f)): raise RuntimeError(f"{'bundle' if merge else 'patch'} missing")
        return f"#{n} {br}@{head[:9]} + {os.path.getsize(f)}B{' merge ' + str(a.get('new_head'))[:9] if merge else ''}"
    def do():
        d = W.mkdtemp("pi-pushfix-")   # PI_WORK/tmp: janitor removes what a killed run leaves
        try:
            run(["gh", "repo", "clone", REPO, d, "--", "--depth", "30", "--branch", br, "-q"])
            run(["git", "-C", d, "config", "user.name", GIT_NAME]); run(["git", "-C", d, "config", "user.email", GIT_EMAIL])
            got = run(["git", "-C", d, "rev-parse", "HEAD"]).strip()
            if got != head: raise RuntimeError(f"branch head is {got[:9]}, fixed head was {head[:9]}; refusing")
            if merge:
                new, main = a["new_head"], a["main"]
                run(["git", "-C", d, "fetch", "-q", "--depth", "1", "origin", main])
                run(["git", "-C", d, "fetch", "-q", a["bundle"], "HEAD"])
                if run(["git", "-C", d, "rev-parse", "FETCH_HEAD"]).strip() != new: raise RuntimeError("bundle HEAD is not the merge commit prfix tested")
                parents = run(["git", "-C", d, "rev-list", "--parents", "-n", "1", new]).split()[1:]
                if parents != [head, main]: raise RuntimeError(f"merge commit parents {[x[:9] for x in parents]} != [head, main]; refusing")
                run(["git", "-C", d, "push", "origin", f"{new}:refs/heads/{br}"])     # plain push: a fast-forward of the PR branch
                return f"pushed merge {new[:9]} (main {main[:9]}) onto {br}"
            run(["git", "-C", d, "apply", "--index", patch])
            run(["git", "-C", d, "commit", "-q", "-m", a.get("title") or "fix: CI", "-m", a.get("message") or f"Pi-Revise: {key}"])
            run(["git", "-C", d, "push", "origin", f"HEAD:refs/heads/{br}"])          # plain push: rejected if the branch moved
            return f"pushed {run(['git', '-C', d, 'rev-parse', 'HEAD']).strip()[:9]} onto {br}"
        finally: shutil.rmtree(d, ignore_errors=True)
    return f"gh push fix onto #{n}", state, validate, do
def fixpr_gate_reason(a):
    """Reason string if this gh.pr.create action targets a gated report PR, else None. Fail closed on an unreadable file."""
    path = globals().get("GATES") or f"{PI_CODE}/conf/gates.json"
    n = a.get("qa")
    if n is None:
        m = re.search(r"QA[:：]?\s*#(\d+)", a.get("body_must_contain") or "") or re.match(r"fixpr:(\d+):", a.get("key") or "")
        n = int(m.group(1)) if m else None
    if not os.path.exists(path): gates = {923: "Praise 闸：#923 修复 PR 不得创建"}
    else:
        try:
            with open(path) as f: gates = {int(k): str(v) for k, v in (json.load(f).get("no_fix_pr") or {}).items()}
        except Exception as e: return f"gates file {path} unreadable ({e}); refusing"
    if n is None: return "cannot tell which QA PR this action fixes; refusing"
    return gates.get(int(n))

def fixpr_real_number(created):
    """PR number from `gh pr create` output (the PR URL)."""
    m = re.search(r"/pull/(\d+)", created or "")
    return int(m.group(1)) if m else None

def _fx_git(d, *args):
    r = subprocess.run(["git", "-C", d, *args], capture_output=True, text=True, timeout=600)
    if r.returncode != 0: raise RuntimeError(f"git {args[0]} rc={r.returncode}: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout

def fixpr_docs_rename(d, br, a, real, push=True):
    """In clone d (branch br checked out): docs/pr/<pred> -> docs/pr/<real> + front matter, commit, plain push. Idempotent."""
    pred_dir = (a.get("docs_dir_predicted") or (f"docs/pr/{a['pred_pr']}" if a.get("pred_pr") else "")).strip().rstrip("/")
    m = re.fullmatch(r"docs/pr/(\d+)", pred_dir)
    if not m: return "docs rename: action has no docs_dir_predicted"
    pred, real = int(m.group(1)), int(real)
    if pred == real: return f"docs/pr/{real}: prediction was right"
    src, dst = os.path.join(d, pred_dir), os.path.join(d, f"docs/pr/{real}")
    if not os.path.isdir(src):
        return f"docs/pr/{real}: already renamed" if os.path.isdir(dst) else f"docs rename: {pred_dir} not on {br}"
    if os.path.exists(dst): raise RuntimeError(f"docs/pr/{real} already exists on {br}; refusing to merge two handoff dirs")
    _fx_git(d, "mv", pred_dir, f"docs/pr/{real}")
    for kind in ("review.md", "qa.md"):
        p = os.path.join(dst, kind)
        if not os.path.isfile(p): continue
        with open(p, encoding="utf-8") as f: t = f.read()
        t2 = re.sub(rf"(?m)^(pr:\s*[\"']?){pred}([\"']?\s*)$", rf"\g<1>{real}\g<2>", t, count=1)
        t2 = re.sub(rf"(?m)^(#\s*PR\s*#){pred}(?!\d)", rf"\g<1>{real}", t2)
        if t2 != t:
            with open(p, "w", encoding="utf-8") as f: f.write(t2)
            _fx_git(d, "add", f"docs/pr/{real}/{kind}")
    _fx_git(d, "commit", "-q", "-m", f"docs(pr): 交接目录改为实际 PR 号 #{real}（预测 #{pred}）")
    if push: _fx_git(d, "push", "origin", f"HEAD:refs/heads/{br}")     # plain push, never --force
    return f"docs/pr/{pred} -> docs/pr/{real} committed{' and pushed' if push else ''}"

def h_fixpr_docs(a):
    """Follow-up action gh.pr.docs-fix {pr, head, docs_dir_predicted}: retry the rename on an existing fix PR."""
    n, br = a["pr"], a.get("head")
    pred_dir = (a.get("docs_dir_predicted") or "").rstrip("/")
    def view(): return gh(["pr", "view", str(n), "-R", REPO, "--json", "state,headRefName,files"])
    def state():
        p = view()
        if p["state"] != "OPEN": return f"stale:{p['state'].lower()}"
        fs = [f.get("path", "") for f in p.get("files") or []]
        if any(f.startswith(f"docs/pr/{n}/") for f in fs) and not any(f.startswith(pred_dir + "/") for f in fs): return "done"
        return "pending"
    def validate():
        p = view()
        if p["headRefName"] != br: raise RuntimeError(f"head branch is {p['headRefName']}, action says {br}")
        return f"#{n} {p['state']} head {br}: rename {pred_dir} -> docs/pr/{n}"
    def do():
        d = W.mkdtemp("pi-fixpr-docs-")   # PI_WORK/tmp: janitor removes what a killed run leaves
        try:
            run(["gh", "repo", "clone", REPO, d, "--", "--depth", "20", "--branch", br, "-q"])
            run(["git", "-C", d, "config", "user.name", GIT_NAME]); run(["git", "-C", d, "config", "user.email", GIT_EMAIL])
            return fixpr_docs_rename(d, br, a, n)
        finally: shutil.rmtree(d, ignore_errors=True)
    return f"gh fix PR #{n} docs/pr rename", state, validate, do
def h_merge(a):
    n, sha = a["pr"], a["key"].split(":")[-1]
    def cur(): return gh(["pr", "view", str(n), "-R", REPO, "--json", merger.FIELDS])
    def state():
        p = cur()
        if p["state"] == "MERGED": return "done"
        if p["state"] != "OPEN": return f"stale:{p['state']}"
        if p["headRefOid"] != sha: return "stale:head moved"
        return "pending"
    def validate():
        why = merger.blockers(cur()); return "8 checks ok" if not why else "would refuse: " + "；".join(why)
    def do():
        if os.path.exists("/workspace/mivo-merger/PAUSED"): raise RuntimeError("merger PAUSED")
        why = merger.blockers(cur())
        if why: raise RuntimeError("blockers: " + "；".join(why))
        return gh(["pr", "merge", str(n), "-R", REPO, "--squash", "--match-head-commit", sha], js=False).strip()
    return f"gh squash-merge #{n}@{sha[:7]}", state, validate, do
def h_comment(a):
    n, mark = a["pr"], f"<!-- pi-line {a['key']} -->"
    def state():
        cs = gh(["pr", "view", str(n), "-R", a.get("repo", REPO), "--json", "comments"])["comments"]
        return "done" if any(mark in c.get("body", "") for c in cs) else "pending"
    def validate():
        if not body_of(a): raise RuntimeError("no body")
        return f"#{n} " + gh(["pr", "view", str(n), "-R", a.get("repo", REPO), "--json", "state"])["state"]
    def do():
        body = open(clean_body(body_of(a))).read()          # clean_body strips every <!-- -->; re-add the merger marker
        if MERGER_MARK in (a.get("text") or ""): body = body.rstrip() + "\n" + MERGER_MARK
        return gh(["pr", "comment", str(n), "-R", a.get("repo", REPO), "--body", body + "\n" + mark], js=False).strip()
    return f"gh comment #{n}", state, validate, do

def h_issue_state(a):
    """gh.issue.close / gh.issue.reopen — handoff issue after a fix-PR retest (opsfix_retest.handoff_actions; the rule of
    mivo-qa/ledger/apply-fix-retests.mjs). Action: repo, issue, comment, add_labels, remove_labels, record{file,set,stamp}.
    Idempotent on GitHub's real state: done = issue already CLOSED (close) / OPEN (reopen) with the labels in place; if it is
    already in that state but labels differ, only the labels are fixed (no second comment). Then the local
    handoff/<owner>/issues.json record gets status + closed_at|reopened_at (+ fix_pr|fix_round), like the Grok tool."""
    repo, n = a.get("repo") or "", int(a.get("issue") or 0)
    close = a.get("type", "").startswith("gh.issue.close"); want = "CLOSED" if close else "OPEN"
    add, rem = list(a.get("add_labels") or []), list(a.get("remove_labels") or [])
    def cur():
        v = gh(["issue", "view", str(n), "-R", repo, "--json", "state,labels"])
        return v["state"], {l["name"] for l in v.get("labels") or []}
    def labels_ok(ls): return all(x in ls for x in add) and not any(x in ls for x in rem)
    def state():
        st, ls = cur()
        return "done" if st == want and labels_ok(ls) else "pending"
    def validate():
        if not (repo and n and a.get("comment")): raise RuntimeError("need repo, issue and comment")
        if repo not in HANDOFF_REPO.values(): raise RuntimeError(f"{repo} is not a handoff repo")
        st, ls = cur(); return f"{repo}#{n} {st} labels={sorted(ls)} -> {want} +{add} -{rem}"
    def do():
        validate(); st, ls = cur(); out = []
        lab = sum([["--add-label", x] for x in add] + [["--remove-label", x] for x in rem if x in ls], [])
        if st == want:                     # someone already closed/reopened it: only fix labels, no second comment
            if lab: gh(["issue", "edit", str(n), "-R", repo] + lab, js=False); out.append("labels fixed")
        elif close:                        # apply-fix-retests order: labels, then close with the comment
            if lab: gh(["issue", "edit", str(n), "-R", repo] + lab, js=False)
            out.append(gh(["issue", "close", str(n), "-R", repo, "--comment", a["comment"] + f"\n\n<!-- pi-line {a['key']} -->"], js=False).strip())
        else:                              # reopen with the comment, then labels
            out.append(gh(["issue", "reopen", str(n), "-R", repo, "--comment", a["comment"] + f"\n\n<!-- pi-line {a['key']} -->"], js=False).strip())
            if lab: gh(["issue", "edit", str(n), "-R", repo] + lab, js=False)
        rec = a.get("record") or {}
        if rec.get("file") and os.path.exists(rec["file"]):
            def upd(d):
                it = next((i for i in d.get("issues", []) if i.get("issue") == n and (not i.get("url") or f"/{repo}/issues/" in i["url"])), None)
                if not it: return None
                ts = now_cst().isoformat(timespec="seconds"); it.update(rec.get("set") or {}); it[rec.get("stamp") or ("closed_at" if close else "reopened_at")] = ts
                d["updated_at"] = ts; return True
            out.append("issues.json " + ("updated" if jsonrec.update(rec["file"], upd) else "no record"))
        return " ".join(out)[-200:]
    return f"gh issue {'close' if close else 'reopen'} {repo}#{n}", state, validate, do

def h_rerun_failed(a):
    """gh.run.rerun-failed {pr, head, names}: re-run, once, the failed jobs of whole-PR gates (pr-format-gate, conflict sentinel)
    that judged before the rest of the checks finished. Only our own draft fix PRs (prwatch), only those check names."""
    n, head = a["pr"], a.get("head")
    # whole-PR gates only -- or any failed check a ruling (prfix R2, Opus) found unrelated to the PR (10-24)
    allow = tuple(x for x in (a.get("names") or []) if a.get("ruled") or x.startswith(("pr-format-gate", "conflict sentinel", "pr-ci-freshness")))
    def failing():
        cs = gh(["pr", "checks", str(n), "-R", REPO, "--json", "name,bucket,link"])
        return [c for c in cs if c.get("bucket") == "fail" and c.get("name", "").startswith(allow)]
    def state():
        p = gh(["pr", "view", str(n), "-R", REPO, "--json", "headRefOid,state,isDraft,author"])
        if p["state"] != "OPEN": return "stale:closed"
        if head and p["headRefOid"] != head: return "stale:head moved"
        if (p.get("author") or {}).get("login") != "PraiseZhu": return "stale:not our PR"
        return "pending" if failing() else "done"
    def validate():
        if not allow: raise RuntimeError("no rerunnable gate names")
        return f"#{n} rerun {[c['name'] for c in failing()]}"
    def do():
        runs = sorted({re.search(r"/runs/(\d+)/", c.get("link") or "").group(1) for c in failing() if re.search(r"/runs/(\d+)/", c.get("link") or "")})
        if not runs: raise RuntimeError("no run id in check links")
        for r in runs: gh(["run", "rerun", r, "-R", REPO, "--failed"], js=False)
        return f"reran failed jobs of runs {runs}"
    return f"gh rerun #{n}", state, validate, do

def h_update_branch(a):
    """gh.pr.update-branch {pr, head}: merge the current base into our own draft fix PR (GitHub update-branch, no force, no code
    change) so CI re-runs on the current base/head merge. prwatch sends it at most twice per PR."""
    n, head = a["pr"], a.get("head")
    def view(): return gh(["pr", "view", str(n), "-R", REPO, "--json", "headRefOid,state,isDraft,author,mergeStateStatus"])
    def state():
        p = view()
        if p["state"] != "OPEN": return "stale:closed"
        if (p.get("author") or {}).get("login") != "PraiseZhu" or not p.get("isDraft"): return "stale:not our draft"
        return "done" if head and p["headRefOid"] != head else "pending"
    def validate(): p = view(); return f"#{n} head {p['headRefOid'][:9]} mergeState {p.get('mergeStateStatus')}"
    def do():
        if a.get("head"): return gh(["api", "-X", "PUT", f"repos/{REPO}/pulls/{n}/update-branch", "-f", f"expected_head_sha={head}"], js=False).strip()[-160:]
        return gh(["api", "-X", "PUT", f"repos/{REPO}/pulls/{n}/update-branch"], js=False).strip()[-160:]
    return f"gh update-branch #{n}", state, validate, do

def handler(a):
    t = a.get("type", "")
    if t == "slack.post": return h_slack_post(a)
    if t == "slack.dm": return h_slack_dm(a)
    if t == "feishu.dm": return h_feishu_dm(a)
    if t == "feishu.group": return h_feishu_group(a)
    if t.startswith("gh.issue.create"): return h_issue(a)
    if t.startswith(("gh.issue.close", "gh.issue.reopen")): return h_issue_state(a)
    if t.startswith("gh.run.rerun-failed"): return h_rerun_failed(a)
    if t.startswith("gh.pr.update-branch"): return h_update_branch(a)
    if t.startswith("gh.pr.push-fix"): return h_push_fix(a)
    if t.startswith("gh.pr.ready"): return h_ready(a)
    if t.startswith("gh.pr.docs-fix"): return h_fixpr_docs(a)
    if t.startswith("gh.pr.create"): return h_fixpr(a)
    if t.startswith("gh.pr.merge"): return h_merge(a)
    if t.startswith("gh.pr.comment"): return h_comment(a)
    if t.startswith("gh.infra."):   # 10-23 infrafix line: panoptes PR / guarded merge / pr-qa-agent-deploy issue (bin/infra_outbound.py)
        import infra_outbound, infrafix
        return infra_outbound.handler(a, gh, lambda text, key: h_slack_dm({"key": key, "type": "slack.dm", "to": "Praise", "text": text})[3](),
                                      sensitive=(infrafix.files_from_diff, infrafix.sensitive))
    return None

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--job"); ap.add_argument("--key")
    ap.add_argument("--resend-key", help="one-shot 补发 of this dedupe key (bypasses the day window and the stale-relay TTL)")
    ap.add_argument("--date", help="with --resend-key: the YYYY-MM-DD actions file to take the key from")
    o = ap.parse_args()
    if o.date and not o.resend_key: ap.error("--date needs --resend-key")
    if o.resend_key and o.key: ap.error("--resend-key and --key are exclusive")
    if o.date and not re.fullmatch(r"\d{4}-\d\d-\d\d", o.date): ap.error("--date must be YYYY-MM-DD")
    secret_env()
    gate = mode() == "pi" and os.path.exists(OUTBOUND_FLAG)
    log(f"mode={mode()} flag={os.path.exists(OUTBOUND_FLAG)} gate={'OPEN' if gate else 'closed'} dry_run={o.dry_run}")
    for k in ("MIVO_SLACK_BOT_TOKEN", "MIVO_FEISHU_APP_ID", "MIVO_FEISHU_APP_SECRET"): log(f"cred {k}: {'set' if os.environ.get(k) else 'unset'}")
    if not gate and not o.dry_run: log("gate closed -> nothing to do"); return 0
    if not o.dry_run:   # memory floor: send nothing, ledger untouched -> every pending action goes out on a later run
        why = mem_floor()
        if why:
            log(f"ALERT lowmem: {why} -> outbound skipped this run, nothing sent, ledger unchanged")
            write_atomic(LOWMEM(), json.dumps({"at": now_cst().isoformat(timespec="seconds"), "why": why}, ensure_ascii=False)); return 0
        if os.path.exists(LOWMEM()): os.remove(LOWMEM())
    if not o.dry_run:   # one executor at a time even when called directly (run.sh's flock only covers cron runs)
        import fcntl, time as _t
        _lk = open("/tmp/pi-fallback-outbound.exec.lock", "w")
        for _ in range(60):
            try: fcntl.flock(_lk, fcntl.LOCK_EX | fcntl.LOCK_NB); break
            except BlockingIOError: _t.sleep(5)
        else: log("another outbound run holds the exec lock for 5 min -> leave it to that run"); return 0
    days = {(now_cst().date() - dt.timedelta(days=i)).isoformat() for i in range(o.days)}
    ledger = jload(LEDGER, None)
    if ledger is None:
        if os.path.exists(LEDGER): log(f"FATAL {LEDGER} unreadable -> abort (never treat a broken ledger as empty)"); return 1
        ledger = {}
    resend = o.resend_key
    if resend:   # exactly one action with that key, from --date or from whichever day holds it
        hits = [(f, a) for f in sorted(glob.glob(f"{BASE}/out/*/*.actions.json"))
                if (not o.date or os.path.basename(f)[:10] == o.date) and (not o.job or f.split("/")[-2] == o.job)
                for a in jload(f, {}).get("actions", []) if a.get("key") == resend]
        if not hits: log(f"resend: no action with key {resend}" + (f" on {o.date}" if o.date else "")); return 1
        if len({os.path.basename(f)[:10] for f, _ in hits}) > 1:
            log(f"resend: {resend} is in several days {sorted({os.path.basename(f)[:10] for f, _ in hits})} -> pass --date"); return 1
        why = resend_blocker(ledger.get(resend))
        if why:
            print(f"# outbound resend {resend}: {why}"); return 0
        days = {os.path.basename(hits[0][0])[:10]}
    rows = []; fails = 0; unsupported = 0; merged_run = 0; changes = {}; relays = []; notices = []
    def persist():   # 10-23: every job, right after each change; merge into the file as it is now (keeps manual edits)
        if o.dry_run: return
        cur = jload(LEDGER, None)
        if cur is None: cur = dict(ledger)
        cur.update(changes); write_atomic(LEDGER, json.dumps(cur, ensure_ascii=False, indent=1))
    def setk(k, v): ledger[k] = v; changes[k] = v; persist()
    for f in sorted(glob.glob(f"{BASE}/out/*/*.actions.json")):
        job = f.split("/")[-2]; day = os.path.basename(f)[:10]
        if day not in days or (o.job and job != o.job): continue
        for a in jload(f, {}).get("actions", []):
            k, t = a["key"], a.get("type", "")
            if o.key and k != o.key: continue
            if resend:
                if k != resend: continue
                a = {**a, "_resend": True}
            if ledger.get(k, {}).get("status") in TERMINAL and not (resend and not resend_blocker(ledger.get(k))) or a.get("status") == "done":
                rows.append((job, k, t, "-", f"{ledger.get(k, {}).get('status', 'done')}(ledger)", ledger.get(k, {}).get("reason", ""))); continue
            is_merge = t.startswith("gh.pr.merge"); prev = ledger.get(k, {})
            if prev.get("status") == "sending":   # the previous run died mid-send (crash or VM snapshot fork)
                if t.startswith(("slack.", "feishu.")) or t in RELAY_TYPES:
                    if not o.dry_run: setk(k, {**prev, "status": "uncertain", "at": now_cst().isoformat(timespec="seconds")})
                    notices.append(PN.item("action", f"有一条{what_of(k, t)}不确定发出去没有", f"「消息外发」发{what_of(k, t)}时被打断了（机器重启或进程被杀），不知道对方收到没有。为了不重复发，系统没有重发。",
                        "如果其实没发出去，对方就收不到。", todo=PN.ask_session(f"outbound 有一条发送中断、状态 uncertain：{k}，去对方那边（Slack / 飞书 / GitHub）核对发没发出去，没发就重发")))
                    rows.append((job, k, t, "-", "uncertain", "not re-sent")); continue
                # gh.* handlers check GitHub itself below, so they can safely continue
            if a.get("expires_at") and str(a["expires_at"]) < now_cst().isoformat():
                if not o.dry_run: setk(k, {"status": "skipped", "reason": "expired", "at": now_cst().isoformat(timespec="seconds")})
                rows.append((job, k, t, "-", "skipped(expired)", a["expires_at"])); continue
            if prev.get("status") == "failed" and prev.get("fails", 0) >= FAIL_GIVE_UP:
                if not o.dry_run: setk(k, {**prev, "status": "gave_up"})
                notices.append(PN.item("action", f"有一条{what_of(k, t)}发不出去，已经放弃", f"「消息外发」试了 {prev['fails']} 次都失败（{prev.get('last_error', '')[:80]}），不再重试。",
                    "这条消息 / 操作不会再发了。", todo=PN.ask_session(f"outbound 放弃了 {k}（失败 {prev['fails']} 次：{prev.get('last_error', '')[:100]}），查原因，修好后把 out/outbound/ledger.json 里这条改回去重发")))
                rows.append((job, k, t, "-", "gave_up", "")); continue
            if backoff_left(prev):
                rows.append((job, k, t, "-", "backoff", f"上次失败，{backoff_left(prev)} 分钟后重试")); continue
            if t in disabled_types():
                h = handler(a)
                try: st = h[1]() if h else "?"
                except Exception as e: st = f"state?:{str(e)[:80]}"
                rows.append((job, k, t, a.get("to", "-"), "BLOCKED(type disabled)", f"{t} in outbound-disabled-types; already-sent check={st}")); continue
            if t.startswith("gh.pr.create"):
                qa = next((int(x) for x in re.findall(r"QA:? ?#(\d+)", a.get("body_must_contain", "") + " " + k.replace("fixpr:", "QA: #"))), None)
                if qa in gates():
                    if not o.dry_run: setk(k, {"status": "skipped", "reason": f"gated: {gates()[qa]}", "at": now_cst().isoformat(timespec="seconds")})
                    rows.append((job, k, t, "-", "skipped(gated)", gates()[qa])); continue
            if t in RELAY_TYPES:   # Praise 10-23: deliver to his Slack DM (urgent: own DM now; others: one digest per run)
                age = action_age_h(a, f)
                if age > RELAY_TTL_H and not resend:
                    if not o.dry_run: setk(k, {"status": "skipped", "reason": f"stale relay ({age:.0f}h old; relay enabled 10-23)", "at": now_cst().isoformat(timespec="seconds")})
                    rows.append((job, k, t, "Praise", "skipped(stale)", f"{age:.0f}h old")); continue
                if job == "pm" and k.startswith("pm-chief:") and re.search(r"共催 0 次", relay_text(a)):   # 10-24：什么都没发生的催办汇总不转（Praise：看不懂的私信别发）
                    if not o.dry_run: setk(k, {"status": "skipped", "reason": "nothing to report (共催 0 次)", "at": now_cst().isoformat(timespec="seconds")})
                    rows.append((job, k, t, "Praise", "skipped(empty)", "共催 0 次")); continue
                try: relays.append((job, k, t, a, relay_item(a, job)))
                except Exception as e:
                    if not o.dry_run: setk(k, {"status": "skipped", "reason": str(e)[:160], "at": now_cst().isoformat(timespec="seconds")})
                    rows.append((job, k, t, "Praise", "skipped", str(e)[:120]))
                continue
            if t in GROK_ONLY: rows.append((job, k, t, a.get("to", "Grok"), "grok-only", "bot-to-bot/Grok chat stays on Grok")); continue
            h = handler(a)
            if not h: rows.append((job, k, t, "?", "unsupported", "no executor; not counted as a failure")); unsupported += 1; continue
            who, state, validate, do = h
            try: st = state()
            except Exception as e: st = f"state?:{str(e)[:120]}"
            if st == "done":
                if not o.dry_run: setk(k, {"status": "done", "at": now_cst().isoformat(timespec="seconds"), "by": "target-state"})
                rows.append((job, k, t, who, "done(target)", ""))
                continue
            if is_merge and merged_run >= MERGE_ROUND_CAP: rows.append((job, k, t, who, "round-cap", f"本轮已合 {merged_run}")); continue
            if is_merge and merges_today(ledger) >= MERGE_DAY_CAP: rows.append((job, k, t, who, "daily-cap", f"今日已合 {merges_today(ledger)}")); continue
            if st.startswith("stale"): rows.append((job, k, t, who, st, "")); continue
            if o.dry_run or not gate:
                try: rows.append((job, k, t, who, "would-do", validate()))
                except Exception as e: rows.append((job, k, t, who, "INVALID", str(e)[:160])); fails += 1
                continue
            try:
                if is_merge: merged_run += 1   # count the attempt, success or not: at most 1 merge call per round
                setk(k, {**prev, "status": "sending", "at": now_cst().isoformat(timespec="seconds")})   # on disk BEFORE the side effect
                res = do(); setk(k, {"status": "done", "at": now_cst().isoformat(timespec="seconds"), "by": "resend" if resend else "pi-line", "result": res[-200:]})
                rows.append((job, k, t, who, "SENT", res[-120:]))
            except GiveUp as e:   # the producer re-plans (prfix: a fix PR whose patch no longer applies is fixed again on main)
                setk(k, {**prev, "status": "gave_up", "last_error": str(e)[:200], "at": now_cst().isoformat(timespec="seconds")})
                # no DM (Praise 10-24): prfix re-fixes it on main by itself and only a second give-up reaches him
                rows.append((job, k, t, who, "GAVE_UP", str(e)[:160]))
            except Exception as e:
                setk(k, {**prev, "status": "failed", "last_error": str(e)[:200], "at": now_cst().isoformat(timespec="seconds"),
                         "fails": prev.get("fails", 0) + 1 if prev.get("status") == "failed" else 1})
                rows.append((job, k, t, who, "FAILED", str(e)[:160])); fails += 1
    # ---- relays + notices -> Praise's Slack DM (urgent ones alone, the rest as one digest) ----
    def dm(key, text, keys):
        st = {"status": "sending", "at": now_cst().isoformat(timespec="seconds")}
        if o.dry_run or not gate:
            rows.append(("relay", key, "slack.dm", "Praise", "would-do", f"{len(keys)} item(s), {len(text)} chars")); return
        for kk in keys: setk(kk, {**ledger.get(kk, {}), **st})
        try:
            r = h_slack_dm({"key": key, "type": "slack.dm", "to": "Praise", "text": text})[3]()
            for kk in keys: setk(kk, {"status": "done", "at": now_cst().isoformat(timespec="seconds"), "by": "resend" if resend else "relay-dm", "result": key})
            rows.append(("relay", key, "slack.dm", "Praise", "SENT", r[-80:]))
        except Exception as e:
            nonlocal_fail.append(1)
            for kk in keys:
                pv = ledger.get(kk, {}); setk(kk, {**pv, "status": "failed", "last_error": str(e)[:200], "at": now_cst().isoformat(timespec="seconds"),
                                                  "fails": pv.get("fails", 0) + 1 if pv.get("status") in ("failed", "sending") else 1})
            rows.append(("relay", key, "slack.dm", "Praise", "FAILED", str(e)[:120]))
    nonlocal_fail = []
    stamp = now_cst().strftime("%Y-%m-%d:%H%M")
    for job, k, t, a, txt in [r for r in relays if r[2] in URGENT_RELAY or resend]:   # a 补发 relay goes alone, now
        dm(f"relay:{k}", txt, [k])
    batch = [r for r in relays if r[2] not in URGENT_RELAY and not resend]
    if batch or notices:
        # 2026-10-24：一条私信 = 「要你处理」的在前，其余在后；不再加「Pi 线转发（原来发到 Grok 的消息…）」抬头
        parts = [PN.render(n) for n in sorted(notices, key=lambda n: PN.ORDER.index(n["kind"]))] + [txt for *_, txt in batch]
        n_act = sum(1 for n in notices if n["kind"] == "action") + sum(1 for *_, txt in batch if txt.startswith(PN.KINDS["action"]))
        head = [f"{len(parts)} 件事" + (f"，其中 {n_act} 件要你处理" if n_act else "，都不用你动手") + "："] if len(parts) > 1 else []
        full = "\n\n".join(head + parts)
        if not o.dry_run: write_atomic(f"{BASE}/out/outbound/relay-{today()}-{stamp[-4:]}.md", full + "\n")
        text = full if len(full) <= 3800 else full[:3600] + f"\n…（截断，全文 out/outbound/relay-{today()}-{stamp[-4:]}.md）"
        dm(f"relay-digest:{stamp}", text, [k for _, k, *_ in batch])
    fails += len(nonlocal_fail)
    out = [f"# outbound {'dry-run' if o.dry_run else 'run'} {now_cst().isoformat(timespec='seconds')} gate={'OPEN' if gate else 'closed'}", "",
           "| job | key | type | to | result | detail |", "|---|---|---|---|---|---|"] + [f"| {' | '.join(str(x).replace('|','/') for x in r)} |" for r in rows]
    print("\n".join(out))
    tag = f".resend-{re.sub(r'[^A-Za-z0-9_.-]+', '_', resend)}" if resend else ""   # never overwrite the cron run's report
    write_atomic(f"{BASE}/out/outbound/{today()}{tag}{'.dry-run' if o.dry_run else ''}.md", "\n".join(out) + "\n")
    if unsupported: log(f"{unsupported} action(s) have no executor yet (listed above; not a failure)")
    return 1 if fails and not o.dry_run else 0
if __name__ == "__main__": sys.exit(main())
