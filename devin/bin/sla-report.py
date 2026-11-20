#!/usr/bin/env python3
"""Merge-to-test-start latency of per-PR merged reports (SLA: p95 <= 120 s).

Usage: sla-report.py [--reports-dir DIR] [--last N]

Reads only DIR/PR-<digits>.json (not recursive; history/ is never read, docs/report-schema.md *Read rule*).
Latency = prs[].test_started_at - prs[].merged_at, in seconds. Samples missing either field, with an unparsable
time or with a negative latency are skipped and listed. --last N keeps the N most recently merged samples.
Default DIR: $MIVO_QA_REPORTS/merged (MIVO_QA_REPORTS defaults to /workspace/mivo-qa/reports).
p95 uses the nearest-rank method. Exit: 0 p95 <= 120 s, 1 p95 > 120 s, 2 not enough samples or usage error.
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


def select_samples(reports_dir):
    """-> (samples [(pr, merged_at, latency_s)], skipped [(file, reason)])"""
    samples, skipped = [], []
    for name in sorted(os.listdir(reports_dir)):
        m = REPORT_NAME.match(name)
        path = os.path.join(reports_dir, name)
        if not m or not os.path.isfile(path):
            continue
        number = int(m.group(1))
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            skipped.append((name, f"无法读取 JSON：{e}"))
            continue
        prs = data.get("prs") if isinstance(data, dict) else None
        entry = next((p for p in prs or [] if isinstance(p, dict) and p.get("number") == number), None)
        if entry is None:
            skipped.append((name, f"prs[] 里没有 #{number}"))
            continue
        merged, started = entry.get("merged_at"), entry.get("test_started_at")
        if merged is None or started is None:
            missing = [k for k, v in (("merged_at", merged), ("test_started_at", started)) if v is None]
            skipped.append((name, "缺字段 " + "、".join(missing)))
            continue
        merged_t, started_t = parse_time(merged), parse_time(started)
        if merged_t is None or started_t is None:
            skipped.append((name, f"时间格式无法解析：merged_at={merged!r} test_started_at={started!r}"))
            continue
        latency = (started_t - merged_t).total_seconds()
        if latency < 0:
            skipped.append((name, f"开测早于合并（{latency:.0f} 秒）"))
            continue
        samples.append((number, merged_t, latency))
    return samples, skipped


def to_p95(values):
    ordered = sorted(values)
    return ordered[max(1, math.ceil(0.95 * len(ordered))) - 1]


def to_text(samples, skipped, reports_dir):
    latencies = [s[2] for s in samples]
    median, p95, worst = statistics.median(latencies), to_p95(latencies), max(latencies)
    over = sorted((s for s in samples if s[2] > SLA_S), key=lambda s: -s[2])
    lines = [
        f"# 合并到开测时效（{reports_dir}）",
        "",
        f"样本数：{len(samples)}",
        f"中位数：{median:.1f} 秒",
        f"p95：{p95:.1f} 秒（阈值 {SLA_S} 秒，{'达标' if p95 <= SLA_S else '超标'}）",
        f"最大值：{worst:.1f} 秒",
        f"超过 {SLA_S} 秒的 PR（{len(over)} 个）：" + ("、".join(f"#{n}（{lat:.0f} 秒）" for n, _, lat in over) or "无"),
    ]
    if skipped:
        lines.append(f"跳过的报告（{len(skipped)} 份）：")
        lines += [f"- {name}：{reason}" for name, reason in skipped]
    return "\n".join(lines), p95


def main(argv=None):
    ap = argparse.ArgumentParser(description="merge-to-test-start SLA report")
    ap.add_argument("--reports-dir", default=None, help="merged reports directory (default $MIVO_QA_REPORTS/merged)")
    ap.add_argument("--last", type=int, default=None, help="only the N most recently merged PRs")
    args = ap.parse_args(argv)
    if args.last is not None and args.last < 1:
        print(f"sla-report: --last must be >= 1, got {args.last}", file=sys.stderr)
        return 2
    reports_dir = args.reports_dir or default_reports_dir()
    if not os.path.isdir(reports_dir):
        print(f"sla-report: reports dir not found: {reports_dir}", file=sys.stderr)
        return 2
    samples, skipped = select_samples(reports_dir)
    samples.sort(key=lambda s: s[1], reverse=True)
    if args.last is not None:
        if len(samples) < args.last:
            print(f"sla-report: not enough samples: need {args.last}, got {len(samples)} usable "
                  f"({len(skipped)} skipped) in {reports_dir}", file=sys.stderr)
            for name, reason in skipped:
                print(f"  skipped {name}: {reason}", file=sys.stderr)
            return 2
        samples = samples[:args.last]
    if not samples:
        print(f"sla-report: no usable samples in {reports_dir} ({len(skipped)} skipped)", file=sys.stderr)
        return 2
    text, p95 = to_text(samples, skipped, reports_dir)
    print(text)
    return 0 if p95 <= SLA_S else 1


if __name__ == "__main__":
    sys.exit(main())
