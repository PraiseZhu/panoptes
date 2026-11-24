#!/usr/bin/env python3
"""Stage the daily handoff Markdown or queue an approved backfill. No outbound calls."""

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pi/bin"))
from lib import CST, parse_ts, today, write_atomic

TERMINAL = {"done", "blocked", "refused"}


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def local_time(value, seconds=False):
    stamp = parse_ts(value) if isinstance(value, str) else value
    if not stamp:
        return "时间未记录"
    return stamp.astimezone(CST).isoformat(sep=" ", timespec="seconds" if seconds else "minutes")[:-6]


def daily_details(markdown):
    prs, findings = {}, {}
    level = pr = detail = None
    for line in markdown.splitlines():
        if m := re.match(r"^(P[0-3]) .+（\d+ 个）$", line):
            level, pr, detail = m[1], None, None
        elif not line:
            level = pr = detail = None
        elif level and (m := re.match(r"^#(\d+) (.+) — (.+)$", line)):
            pr, detail = int(m[1]), None
            prs[pr] = (level, m[2], m[3].lstrip("@"))
        elif pr is not None and (m := re.match(r"^· 问题：(.+)$", line)):
            detail = findings.setdefault((pr, m[1]), {})
        elif pr is not None and (m := re.match(r"^· 另有 1 条 P[0-3] 审查意见：(.+)。需要修", line)):
            findings[(pr, m[1])] = {"source": "审查意见"}
        elif detail is not None and line.startswith(("· 测试复现", "· 审查结论")):
            detail["source"] = line[2:]
        elif detail is not None and line.startswith("· 下一步："):
            detail["next"] = line.removeprefix("· 下一步：")
    return prs, findings


def ledger_text(ledger, key):
    entry = ledger.get(key, {})
    status = entry.get("status", "未记录")
    if entry.get("at"):
        status += f"，{local_time(entry['at'], seconds=True)} 北京时间"
    return f"ledger `{key}`，{status}"


def ledger_url(entry):
    result = entry.get("result", "")
    if isinstance(result, str) and (m := re.search(r"https://[^\s<>\"，。]+", result)):
        return m[0]
    return "URL 未记录"


def feedback(day, data_day, repo, authors, actions, ledger, markdown, meta):
    key = f"mivo-daily-report:{data_day}"
    sent = parse_ts(ledger[key].get("at"))
    deadline = local_time(sent + dt.timedelta(days=3)) if sent else "时间未记录"
    prs, findings = daily_details(markdown)
    lines = [f"# {day} 发到本仓的问题", "",
             f"来源：{local_time(sent)}（北京时间）发出的 Mivo QA 日报（数据日 {data_day}，"
             f"pi/out/daily/{data_day}.md）。发送记录：pi/out/outbound/ledger.json（{ledger_text(ledger, key)}）。"]
    for action in actions:
        pr = action["pr"]
        action_key = action["key"]
        fields = dict(re.findall(r"^- ([^：\n]+)：(.*)$", action["text"], re.M))
        issue_title = fields.get("问题", action["title"])
        level = fields.get("级别", action.get("level", "未知"))
        issue_url = ledger_url(ledger[action_key])
        prefix = f"https://github.com/{repo}/issues/"
        issue_ref = f"#{issue_url.removeprefix(prefix)}" if issue_url.startswith(prefix) else f"PR #{pr}"
        login = action.get("route_author")
        who = prs.get(pr, ("", "", ""))[2]
        author = " ".join(x for x in (f"@{login}" if login else "", who) if x)
        detail = findings.get((pr, issue_title), {})
        dm_key = f"dm:{data_day}:{pr}"
        lines.extend([
            "", f"## {issue_ref} {issue_title}（{level}{'，' + author if author else ''}）",
            f"- 问题：{issue_title}",
            f"- 位置：{fields.get('位置', '未知')}",
            f"- 测试的合并提交：{fields.get('测试的合并提交', '未知')}",
            f"- 指纹：{fields.get('指纹', action.get('handoff_key') or '未知')}",
            f"- 来源：{detail.get('source', '日报未记录')}",
            f"- 下一步：{detail.get('next', '日报未记录')}",
            f"- 回执截止：{deadline}（北京时间，日报发出时间 + 3 天）。",
            f"- 通知：飞书私信（{ledger_text(ledger, dm_key)}；{ledger_url(ledger.get(dm_key, {}))}）。",
            f"- 交接 issue：{issue_url}（{ledger_text(ledger, action_key)}）。",
        ])
    if not actions:
        lines.extend(["", "本次没有发到本仓的问题。"])
        for pr in meta["merged_prs"]:
            if (pr.get("author") or {}).get("login") in authors:
                n = pr["number"]
                level, title, _ = prs.get(n, ("日报未评级", pr["title"], ""))
                lines.append(f"- #{n} {title}（{level}）")
    fault, health = meta["fault"], meta["health"]
    count = len(meta["merged_prs"])
    lines.extend(["", f"数据{'不齐全' if fault else '齐全'}：本期 {count} 个合并 PR，"
                  f"{len(fault)} 个没有合格的 QA 报告，{len(health)} 个测试过程有异常（日报 health）。"])
    return "\n".join(lines) + "\n"


def receipts(day, repo, scan):
    lines = [f"# {day} 回执", "", f"来源：pi/out/handoff/{day}.md（Pi line 只读扫描）。"]
    time, rows = None, []
    for line in scan.splitlines():
        if m := re.match(r"^## 扫描 (\d\d:\d\d) 北京时间", line):
            if rows:
                lines.extend(["", f"## {time} 扫描（北京时间）", *rows])
            rows = []
            time = m[1]
        elif time is not None and line.startswith((f"- {repo}#", f"- {repo}: ")):
            rows.append(receipt_line(line, repo))
    if rows:
        lines.extend(["", f"## {time} 扫描（北京时间）", *rows])
    return "\n".join(lines) + "\n"


def receipt_line(line, repo):
    row = line.removeprefix(f"- {repo}")
    if row.startswith(": "):
        summary = row.removeprefix(": ")
        if not summary.startswith("未核实"):
            summary = f"合计：{summary}。"
        return f"- {summary}"
    return f"- {row}"


def stage(qa, state, day):
    data_day = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
    routes = load_json(ROOT / "report/handoff-routes.json")["routes"]
    scan = state / "out/handoff" / f"{day}.md"
    scan_text = scan.read_text(encoding="utf-8") if scan.exists() else ""
    root = (qa / "handoff/staging").resolve()
    destinations = {}
    for route in routes:
        repo = route["repo"]
        dest = root / repo.split("/", 1)[1] / "daily" / day[:4] / day[5:7] / day
        for name in ("receipts.md", "feedback.md"):
            if not (dest / name).resolve().is_relative_to(root):
                raise ValueError(f"staging path outside {root}: {dest / name}")
        destinations[repo] = dest
        write_atomic(dest / "receipts.md", receipts(day, repo, scan_text))
    ledger_path = state / "out/outbound/ledger.json"
    ledger = load_json(ledger_path) if ledger_path.exists() else {}
    if ledger.get(f"mivo-daily-report:{data_day}", {}).get("status") != "done":
        return
    daily = state / "out/daily"
    actions = load_json(daily / f"{data_day}.actions.json")["actions"]
    authors = {login: route["repo"] for route in routes for login in route["authors"]}
    handoffs = {repo: [] for repo in destinations}
    for action in actions:
        if action["type"] == "gh.issue.create" and action["key"].startswith("handoff:"):
            repo = action.get("repo") or authors.get(action.get("route_author"))
            if repo in handoffs:
                handoffs[repo].append(action)
    markdown = (daily / f"{data_day}.md").read_text(encoding="utf-8")
    meta = load_json(daily / f"{data_day}.json")
    for route in routes:
        repo = route["repo"]
        rows = handoffs[repo]
        if all(ledger.get(a["key"], {}).get("status") in TERMINAL for a in rows):
            write_atomic(destinations[repo] / "feedback.md",
                         feedback(day, data_day, repo, route["authors"], rows, ledger, markdown, meta))


def backfill_apply(state, day, prs):
    checklist = load_json(state / "out/handoff" / f"backfill-{day}.json")
    actions = []
    for row in checklist["rows"]:
        if row["status"] != "would-open" or (prs and row["pr"] not in prs):
            continue
        action = row["action"]
        if (action["type"] != "gh.issue.create" or not row["key"].startswith("handoff:")
                or any(action[field] != row[field] for field in ("key", "repo", "pr"))):
            raise ValueError(f"handoff action does not match checklist row: {row['key']}")
        actions.append(action)
    path = state / "out/handoff-backfill" / f"{day}.actions.json"
    write_atomic(path, json.dumps({"job": "handoff-backfill", "day": day, "actions": actions},
                                  ensure_ascii=False, indent=1) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("stage").add_argument("--day", required=True, type=lambda s: dt.date.fromisoformat(s).isoformat())
    sub.add_parser("backfill-apply").add_argument("--pr", action="append", type=int, default=[])
    args = parser.parse_args()
    qa = Path(os.environ.get("QA_HOME") or "/workspace/mivo-qa")
    state = Path(os.environ.get("PI_STATE") or qa / "pi")
    try:
        if args.command == "stage":
            stage(qa, state, args.day)
        else:
            backfill_apply(state, today(), set(args.pr))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"handoff-stage: {exc}\n")


if __name__ == "__main__":
    main()
