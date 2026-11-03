#!/usr/bin/env python3
"""One handoff gh.issue.create action per author problem, shared by daily.py (the real one) and handoff.py plan / backfill
(what the daily report would open). Key = handoff:<PR>:<pr_fingerprint>, pr_fingerprint = "<fingerprint>-pr<N>" when the
report has none; outbound h_issue dedupes on its last segment. Before 2026-11-03 handoff.py plan keyed on the bare
fingerprint, so the same finding could get two keys."""
from lib import REPO

def handoff_issue_action(day, i, f):
    """gh.issue.create for one author problem (10-30): the body says what was found where, on which tested commit, under which
    fingerprint, and gets (via handoff_key, appended by outbound) the mivo-qa-handoff marker that handoff.py / handoff-receipts.mjs match receipts against
    (with the `handoff` label outbound adds). Unknown values are written as 未知, never guessed."""
    n = i["n"]; fp = f.get("fingerprint") or ""; pfp = f.get("pr_fingerprint") or (f"{fp}-pr{n}" if fp else "")
    loc = f.get("file") or "未知"
    if f.get("file") and f.get("line") is not None: loc += f":{f['line']}"
    sha = i.get("tested_sha") or i.get("merge_sha")
    title = f.get("title") or f.get("test") or f"PR #{n} QA 交接"
    body = "\n".join([
        f"QA 日报（数据日 {day}）在 PR #{n} 上测出的问题，请处理：https://github.com/{REPO}/pull/{n}", "",
        f"- 级别：{f.get('severity') or '未知'}",
        f"- 问题：{title}",
        f"- 位置：{loc}",
        f"- 测试的合并提交：{sha or '未知'}",
        f"- 指纹：{fp or '未知'}" + (f"（本 PR：{pfp}）" if pfp and pfp != fp else ""),
        ])   # outbound appends the mivo-qa-handoff marker from handoff_key (comments in a body are stripped as draft marks)
    return {"key": f"handoff:{n}:{pfp or fp}", "type": "gh.issue.create", "pr": n, "title": f"[{f.get('severity')}] {title}" if f.get("severity") else title,
            "text": body, "labels": ["handoff"], "handoff_key": pfp}
