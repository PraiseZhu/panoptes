#!/usr/bin/env python3
"""Merge-to-test-start latency of per-PR merged reports (SLA: p95 <= 120 s), over a fixed denominator.

Usage: sla-report.py [--reports-dir DIR] [--last N] [--since ISO8601] [--trigger NAME|all] [--merged-list FILE]

Reads only DIR/PR-<digits>.json (not recursive; history/ is never read, docs/report-schema.md *Read rule*).
Latency = prs[].test_started_at - prs[].merged_at, in seconds.
Denominator (target PRs), fixed before any report is checked:
  --merged-list FILE  GitHub's merged PR list (JSON array of {number, mergedAt|merged_at}, e.g. from
                      `gh pr list --state merged --json number,mergedAt`); every listed PR is a target.
  otherwise           every report whose run.trigger matches --trigger (default event; reports without run.trigger
                      count as manual, as in docs/report-schema.md; `all` disables the filter).
  --since keeps targets merged at or after the cut-over time; --last N keeps the N most recently merged targets.
Every target must have a report (with the --trigger trigger) with a valid merged_at, test_started_at and a
non-negative latency. A bad target is never skipped or replaced by an older PR: the problem PRs are listed, exit 2.
Default DIR: $MIVO_QA_REPORTS/merged (MIVO_QA_REPORTS defaults to /workspace/mivo-qa/reports).
p95 uses the nearest-rank method. Exit: 0 p95 <= 120 s, 1 p95 > 120 s, 2 incomplete evidence or usage error.
"""
import argparse
import datetime as dt
import json
import math
import os
import re
import statistics
import sys

SLA_S = 120
REPORT_NAME = re.compile(r"^PR-(\d+)\.json$")
DEFAULT_TRIGGER = "manual"


class InputError(Exception):
    pass


def default_reports_dir():
    return os.path.join(os.environ.get("MIVO_QA_REPORTS") or "/workspace/mivo-qa/reports", "merged")


def parse_time(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else None


def select_record(reports_dir, name, number):
    """-> {number, file, trigger, merged, latency, problem}; problem is None for a usable sample."""
    rec = {"number": number, "file": name, "trigger": None, "merged": None, "latency": None, "problem": None}
    try:
        with open(os.path.join(reports_dir, name), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        rec["problem"] = f"无法读取 JSON：{e}"
        return rec
    run = data.get("run") if isinstance(data, dict) else None
    rec["trigger"] = (run.get("trigger") if isinstance(run, dict) else None) or DEFAULT_TRIGGER
    prs = data.get("prs") if isinstance(data, dict) else None
    entry = next((p for p in prs or [] if isinstance(p, dict) and p.get("number") == number), None)
    if entry is None:
        rec["problem"] = f"prs[] 里没有 #{number}"
        return rec
    merged, started = entry.get("merged_at"), entry.get("test_started_at")
    rec["merged"] = parse_time(merged)
    missing = [k for k, v in (("merged_at", merged), ("test_started_at", started)) if v is None]
    if missing:
        rec["problem"] = "缺字段 " + "、".join(missing)
        return rec
    started_t = parse_time(started)
    if rec["merged"] is None or started_t is None:
        rec["problem"] = f"时间格式无法解析：merged_at={merged!r} test_started_at={started!r}"
        return rec
    latency = (started_t - rec["merged"]).total_seconds()
    if latency < 0:
        rec["problem"] = f"开测早于合并（{latency:.0f} 秒）"
        return rec
    rec["latency"] = latency
    return rec


def select_records(reports_dir):
    records = {}
    for name in sorted(os.listdir(reports_dir)):
        m = REPORT_NAME.match(name)
        if m and os.path.isfile(os.path.join(reports_dir, name)):
            records[int(m.group(1))] = select_record(reports_dir, name, int(m.group(1)))
    return records


def select_merged_list(path):
    """-> [(number, merged_at)] from a GitHub merged PR list JSON."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise InputError(f"--merged-list: cannot read JSON from {path}: {e}")
    if not isinstance(data, list):
        raise InputError(f"--merged-list: {path} must be a JSON array of {{number, mergedAt}}")
    out = []
    for item in data:
        number = item.get("number") if isinstance(item, dict) else None
        merged = parse_time(item.get("mergedAt") or item.get("merged_at")) if isinstance(item, dict) else None
        if not isinstance(number, int) or merged is None:
            raise InputError(f"--merged-list: entry needs an integer number and an ISO 8601 mergedAt, got {item!r}")
        out.append((number, merged))
    return out


def select_targets(records, merged_list, trigger, since, last):
    """-> (targets [(number, merged_at|None)], unplaced [record]) fixed before reports are checked."""
    unplaced = []
    if merged_list is not None:
        targets = merged_list
    else:
        matching = [r for r in records.values() if trigger == "all" or r["trigger"] in (None, trigger)]
        unplaced = [r for r in matching if r["merged"] is None]
        targets = [(r["number"], r["merged"]) for r in matching if r["merged"] is not None]
    if since is not None:
        targets = [t for t in targets if t[1] >= since]
    targets.sort(key=lambda t: t[1], reverse=True)
    if last is not None:
        targets = targets[:last]
    return targets, unplaced


def to_problem(record, number, trigger):
    if record is None:
        return f"#{number}：缺报告 PR-{number}.json"
    if record["problem"]:
        return f"#{number}：{record['file']} {record['problem']}"
    if trigger != "all" and record["trigger"] != trigger:
        return f"#{number}：{record['file']} 的 run.trigger 是 {record['trigger']}，要求 {trigger}"
    return None


def to_p95(values):
    ordered = sorted(values)
    return ordered[max(1, math.ceil(0.95 * len(ordered))) - 1]


def to_text(samples, scope, reports_dir):
    latencies = [lat for _, lat in samples]
    median, p95, worst = statistics.median(latencies), to_p95(latencies), max(latencies)
    over = sorted((s for s in samples if s[1] > SLA_S), key=lambda s: -s[1])
    lines = [
        f"# 合并到开测时效（{reports_dir}）",
        "",
        f"分母：{scope}",
        f"样本数：{len(samples)}",
        f"中位数：{median:.1f} 秒",
        f"p95：{p95:.1f} 秒（阈值 {SLA_S} 秒，{'达标' if p95 <= SLA_S else '超标'}）",
        f"最大值：{worst:.1f} 秒",
        f"超过 {SLA_S} 秒的 PR（{len(over)} 个）：" + ("、".join(f"#{n}（{lat:.0f} 秒）" for n, lat in over) or "无"),
    ]
    return "\n".join(lines), p95


def fail(message, details=()):
    print(f"sla-report: {message}", file=sys.stderr)
    for line in details:
        print(f"  {line}", file=sys.stderr)
    return 2


def main(argv=None):
    ap = argparse.ArgumentParser(description="merge-to-test-start SLA report")
    ap.add_argument("--reports-dir", default=None, help="merged reports directory (default $MIVO_QA_REPORTS/merged)")
    ap.add_argument("--last", type=int, default=None, help="only the N most recently merged target PRs")
    ap.add_argument("--since", default=None, help="only PRs merged at or after this ISO 8601 time (with time zone)")
    ap.add_argument("--trigger", default="event", help="run.trigger of the reports to use (default event; all = any)")
    ap.add_argument("--merged-list", default=None, help="GitHub merged PR list JSON used as the denominator")
    args = ap.parse_args(argv)
    if args.last is not None and args.last < 1:
        return fail(f"--last must be >= 1, got {args.last}")
    since = None
    if args.since is not None:
        since = parse_time(args.since)
        if since is None:
            return fail(f"--since must be ISO 8601 with a time zone (e.g. 2026-11-20T00:00:00+08:00), got {args.since!r}")
    reports_dir = args.reports_dir or default_reports_dir()
    if not os.path.isdir(reports_dir):
        return fail(f"reports dir not found: {reports_dir}")
    try:
        merged_list = select_merged_list(args.merged_list) if args.merged_list else None
    except InputError as e:
        return fail(str(e))
    records = select_records(reports_dir)
    targets, unplaced = select_targets(records, merged_list, args.trigger, since, args.last)
    scope = (f"{'--merged-list ' + args.merged_list if merged_list is not None else '报告'}，trigger={args.trigger}"
             f"{'，since=' + args.since if since else ''}{'，最近 ' + str(args.last) + ' 个' if args.last else ''}")
    problems = [f"#{r['number']}：{r['file']} {r['problem']}（无法确定合并时间，不能排除出分母）" for r in unplaced]
    problems += [p for p in (to_problem(records.get(n), n, args.trigger) for n, _ in targets) if p]
    if problems:
        return fail(f"evidence incomplete: {len(problems)} target PR(s) without a valid sample ({scope}); "
                    "they are not skipped or replaced by older PRs", problems)
    if args.last is not None and len(targets) < args.last:
        return fail(f"not enough target PRs: need {args.last}, got {len(targets)} ({scope}) in {reports_dir}")
    if not targets:
        return fail(f"no target PRs ({scope}) in {reports_dir}")
    text, p95 = to_text([(n, records[n]["latency"]) for n, _ in targets], scope, reports_dir)
    print(text)
    return 0 if p95 <= SLA_S else 1


if __name__ == "__main__":
    sys.exit(main())
