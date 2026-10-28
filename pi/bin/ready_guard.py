"""ready_guard — Pi 转 ready 前的两条硬闸（10-29，mivo #1351：review-pr 已红、且有人转回草稿后，Pi 仍把它转成 ready）。
标准不变：除 review-pr 外的 CI 全绿即可转 ready（review-pr 没跑 / 在跑不拦）。另加：
 (a) 有人手动转回过草稿（PR 时间线里有 convert_to_draft 事件，且不是 Pi 自己记录的动作）→ 这个 PR 永远不再由 Pi 转 ready，换 head 也不转。
 (b) 同一 head 上 review-pr 已经是失败结论 → 不转。
「是不是 Pi 做的」：所有 bot（Pi、Praise 的会话 bot、Mac mini 盯梢）都以 PraiseZhu 身份调 gh，登录名分不出来。所以只认 Pi 自己的
outbound 台账（ledger.json）：Pi 执行过的转草稿动作的 key 是 draft:<PR>[:...]（type gh.pr.draft，目前 Pi 不做这个动作），
记录时间与时间线事件相差 ≤ PI_MATCH_SEC 才算 Pi 的；其余任何 convert_to_draft 一律当作人（或别的 bot）的决定。"""
import datetime as dt
REVIEW = "review-pr"
BAD = ("FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR", "FAIL", "CANCEL")
PI_MATCH_SEC = 300

def _ts(s):
    try: return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception: return None

def _name(c): return c.get("name") or c.get("context") or ""

def review_pr_failed(rollup):
    """rollup = 当前 head 的 statusCheckRollup（或 gh pr checks 的 name/bucket）。review-pr 有失败结论 → True；没有 / 在跑 / 绿 → False。"""
    for c in rollup or []:
        if REVIEW not in _name(c): continue
        v = (c.get("conclusion") or c.get("state") or c.get("bucket") or "").upper()
        if v in BAD: return True
    return False

def pi_draft_times(n, ledger):
    out = []
    for k, v in (ledger or {}).items():
        if k == f"draft:{n}" or k.startswith(f"draft:{n}:"):
            t = _ts((v or {}).get("at"))
            if t: out.append(t)
    return out

def human_draft_events(n, events, ledger):
    """events = 时间线（含 event / created_at）。返回不是 Pi 记录的 convert_to_draft 事件时间列表。"""
    mine = pi_draft_times(n, ledger)
    hits = []
    for e in events or []:
        if e.get("event") != "convert_to_draft": continue
        t = _ts(e.get("created_at"))
        if t and any(abs((t - m).total_seconds()) <= PI_MATCH_SEC for m in mine): continue
        hits.append(e.get("created_at"))
    return hits

def timeline(gh, repo, n):
    return gh(["api", f"repos/{repo}/issues/{n}/timeline?per_page=100", "--paginate", "--jq",
               '[.[] | select(.event=="convert_to_draft" or .event=="ready_for_review") | {event, created_at}]'], js=False)

def _events(gh, repo, n):
    import json
    raw = timeline(gh, repo, n)
    if isinstance(raw, list): return raw
    out = []
    for line in str(raw).splitlines():          # --paginate 每页一个 JSON 数组
        line = line.strip()
        if line: out += json.loads(line)
    return out

def block_reason(gh, repo, n, rollup, ledger, events=None):
    """不该转 ready 的原因（字符串），可以转则 None。时间线读不到时保守地拦下（fail closed）。"""
    if review_pr_failed(rollup): return "review-pr failed on this head"
    try: ev = _events(gh, repo, n) if events is None else events
    except Exception as e: return f"timeline unreadable ({str(e)[:80]}); not readying"
    h = human_draft_events(n, ev, ledger)
    if h: return f"converted back to draft by someone else at {h[-1]} (not in Pi's ledger); Pi never readies it again"
    return None
