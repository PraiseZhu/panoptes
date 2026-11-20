#!/usr/bin/env python3
"""Compare a box report with a Devin report of the same PR(s), check id by check id.

Usage: parity-diff.py --box BOX.json --devin DEVIN.json --mode full-steps|production

Prints a Markdown table (检查 ID | box 结果 | Devin 结果 | 判定). Verdicts: same, allowed:<reason>, DIFF.
Checked first: the Devin report's system_health.status must be "ok", and a PR's tested_sha must be the same on both sides.
Allowed differences (closed list):
  model         ai-test-* checks (count and content); code_review, ai_summary, triage text and conclusion, only when
                both sides have the step and Devin's status is not error / failed (failed is allowed only if box also failed)
  full-steps-disabled  full-steps mode only: model review / triage switched off on Devin (missing, not-run, capped, ...)
  bench         bench performance numbers and non-comparable bench status, only when both sides have bench
  box-flaky     box marked the check / test flaky or it did not finish there, Devin ran it and it passed
  latest-main   production mode only: attribution that depends on latest main (fixed-later, latest_main_run)
  metadata      machine and timing fields (duration_ms, ended_at, shards, reused, machine_load, ...) are never compared
Any deterministic check or bench missing on either side or with a different result (incl. a different failing-test
set) is DIFF. In production mode a model step box has but Devin lacks (or has with status error / failed) is DIFF.
Exit: 0 no DIFF, 1 at least one DIFF, 2 usage or file error.
"""
import argparse
import json
import sys

MODES = ("full-steps", "production")
MODEL_FIELDS = ("code_review", "ai_summary", "triage")
MODEL_FAILED = {"error", "failed"}
MODEL_NOT_RUN = {"not-run", "disabled", "skipped", "capped"}
BENCH_IDS = {"bench"}
NOT_FINISHED_STATUSES = {"timeout", "unavailable"}
NOT_FINISHED_FAILURES = {"flaky", "timeout", "setup-failed"}
NOT_FINISHED_TYPES = {"runner_error", "test_timeout"}
LATEST_MAIN_STATUSES = {"fixed-later"}
WHOLE_CHECK = "(整项)"


class InputError(Exception):
    pass


def load_report(path, label):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise InputError(f"--{label}: file not found: {path}")
    except (OSError, ValueError) as e:
        raise InputError(f"--{label}: cannot read JSON from {path}: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("prs"), list):
        raise InputError(f"--{label}: {path} has no prs[] array (expected a panoptes report, docs/report-schema.md)")
    prs = {}
    for entry in data["prs"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("number"), int):
            raise InputError(f"--{label}: {path} has a prs[] entry without an integer number")
        prs[entry["number"]] = entry
    if not prs:
        raise InputError(f"--{label}: {path} has an empty prs[] array")
    return data, prs


def is_model_check(check_id, check):
    return check_id.startswith("ai-test-") or (check or {}).get("source") == "ai-test"


def select_checks(pr):
    return {c["id"]: c for c in pr.get("checks") or [] if isinstance(c, dict) and c.get("id")}


def select_failures(pr):
    """{check_id: {test: failed_check}}"""
    out = {}
    for f in pr.get("failed_checks") or []:
        if not isinstance(f, dict) or not f.get("check_id"):
            continue
        out.setdefault(f["check_id"], {})[f.get("test") or WHOLE_CHECK] = f
    return out


def is_not_finished(failure):
    evidence = failure.get("evidence") or {}
    return (failure.get("status") in NOT_FINISHED_FAILURES or failure.get("failure_type") in NOT_FINISHED_TYPES
            or evidence.get("timed_out") is True)


def is_latest_main_attribution(a, b):
    if a.get("status") in LATEST_MAIN_STATUSES or b.get("status") in LATEST_MAIN_STATUSES:
        return True
    return any((x.get("evidence") or {}).get("latest_main_run") is not None for x in (a, b))


def to_result(check, failures):
    if check is None:
        return "缺失"
    status = check.get("status") or "?"
    if failures:
        return f"{status}（{len(failures)} 个失败用例）"
    return status


def judge_failed_tests(box_fail, devin_fail, mode):
    """Verdict for two checks with the same status, by failing-test set and attribution."""
    allowed = None
    for test in sorted(set(box_fail) | set(devin_fail)):
        b, d = box_fail.get(test), devin_fail.get(test)
        if b is None:
            return f"DIFF（Devin 多失败用例：{test}）"
        if d is None:
            if is_not_finished(b):
                allowed = "allowed:box-flaky"
                continue
            return f"DIFF（Devin 少失败用例：{test}）"
        if b.get("status") == d.get("status"):
            continue
        if mode == "production" and is_latest_main_attribution(b, d):
            allowed = allowed or "allowed:latest-main"
            continue
        return f"DIFF（归因不同：{test} box={b.get('status')} Devin={d.get('status')}）"
    return allowed or "same"


def judge_check(check_id, box_check, devin_check, box_fail, devin_fail, mode):
    if is_model_check(check_id, box_check or devin_check):
        return "allowed:model"
    if check_id in BENCH_IDS:
        box_has, devin_has = box_check is not None or bool(box_fail), devin_check is not None or bool(devin_fail)
        if not devin_has:
            return "DIFF（Devin 缺 bench）"
        if not box_has:
            return "DIFF（box 无 bench）"
        if box_check == devin_check and box_fail == devin_fail:
            return "same"
        return "allowed:bench"
    if devin_check is None:
        return "DIFF（Devin 缺检查）"
    if box_check is None:
        return "DIFF（box 无此检查）"
    box_status, devin_status = box_check.get("status"), devin_check.get("status")
    if box_status != devin_status:
        box_not_finished = box_status in NOT_FINISHED_STATUSES or (
            box_fail and all(is_not_finished(f) for f in box_fail.values()))
        if devin_status == "pass" and box_not_finished:
            return "allowed:box-flaky"
        return f"DIFF（结果不同：box={box_status} Devin={devin_status}）"
    return judge_failed_tests(box_fail, devin_fail, mode)


def select_model_status(pr, field):
    """None = step missing; otherwise its status ("ok" when the step has no status field, e.g. ai_summary)."""
    value = pr.get(field)
    if value is None:
        return None
    if isinstance(value, dict):
        return str(value.get("status") or "ok")
    return "ok"


def to_model_result(status):
    return "缺失" if status is None else status


def judge_model_field(field, box_status, devin_status, mode):
    devin_ran = devin_status is not None and devin_status not in MODEL_NOT_RUN
    box_ran = box_status is not None and box_status not in MODEL_NOT_RUN
    if mode == "full-steps" and not devin_ran:
        return "allowed:full-steps-disabled"
    if devin_status == "error":
        return f"DIFF（Devin {field} 出错：status=error）"
    if devin_status == "failed" and box_status != "failed":
        return f"DIFF（Devin {field} 失败：box={to_model_result(box_status)} Devin=failed）"
    if box_ran and not devin_ran:
        return f"DIFF（Devin 缺 {field}：box={box_status} Devin={to_model_result(devin_status)}）"
    return "allowed:model"


def compare_health(devin_data):
    health = devin_data.get("system_health")
    status = health.get("status") if isinstance(health, dict) else None
    if status == "ok":
        return ("system_health", "—", "ok", "same")
    return ("system_health", "—", to_model_result(status), f"DIFF（Devin system_health.status={status!r}，应为 'ok'）")


def compare_pr(number, box_pr, devin_pr, mode):
    rows = []
    if devin_pr is None:
        return [(f"(PR #{number})", "有", "缺失", "DIFF（Devin 报告缺此 PR）")]
    if box_pr is None:
        return [(f"(PR #{number})", "缺失", "有", "DIFF（box 报告缺此 PR）")]
    box_sha, devin_sha = box_pr.get("tested_sha"), devin_pr.get("tested_sha")
    if box_sha != devin_sha:
        rows.append(("tested_sha", box_sha or "缺失", devin_sha or "缺失", "DIFF（测的不是同一提交）"))
    box_checks, devin_checks = select_checks(box_pr), select_checks(devin_pr)
    box_fails, devin_fails = select_failures(box_pr), select_failures(devin_pr)
    ids = list(box_checks)
    ids += [i for i in devin_checks if i not in box_checks]
    ids += sorted((set(box_fails) | set(devin_fails)) - set(ids))
    for check_id in ids:
        b, d = box_checks.get(check_id), devin_checks.get(check_id)
        bf, df = box_fails.get(check_id, {}), devin_fails.get(check_id, {})
        verdict = judge_check(check_id, b, d, bf, df, mode)
        rows.append((check_id, to_result(b, bf), to_result(d, df), verdict))
    for field in MODEL_FIELDS:
        b, d = select_model_status(box_pr, field), select_model_status(devin_pr, field)
        if b is None and d is None:
            continue
        rows.append((field, to_model_result(b), to_model_result(d), judge_model_field(field, b, d, mode)))
    return rows


def to_markdown(sections, mode):
    lines = [f"# parity-diff（mode={mode}）", ""]
    for title, rows in sections:
        lines += [f"## {title}", "", "| 检查 ID | box 结果 | Devin 结果 | 判定 |", "|---|---|---|---|"]
        for row in rows:
            lines.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in row) + " |")
        lines.append("")
    verdicts = [r[3] for _, rows in sections for r in rows]
    diff = sum(v.startswith("DIFF") for v in verdicts)
    allowed = sum(v.startswith("allowed:") for v in verdicts)
    same = sum(v == "same" for v in verdicts)
    lines.append(f"合计：same {same}，allowed {allowed}，DIFF {diff}（机器与耗时元数据不参与对比）")
    return "\n".join(lines), diff


def main(argv=None):
    ap = argparse.ArgumentParser(description="box vs Devin report parity diff")
    ap.add_argument("--box", required=True, help="box report JSON")
    ap.add_argument("--devin", required=True, help="Devin report JSON")
    ap.add_argument("--mode", required=True, choices=MODES)
    args = ap.parse_args(argv)
    try:
        _, box_prs = load_report(args.box, "box")
        devin_data, devin_prs = load_report(args.devin, "devin")
    except InputError as e:
        print(f"parity-diff: {e}", file=sys.stderr)
        return 2
    numbers = list(box_prs) + [n for n in devin_prs if n not in box_prs]
    sections = [("报告", [compare_health(devin_data)])]
    sections += [(f"PR #{n}", compare_pr(n, box_prs.get(n), devin_prs.get(n), args.mode)) for n in numbers]
    text, diff = to_markdown(sections, args.mode)
    print(text)
    return 1 if diff else 0


if __name__ == "__main__":
    sys.exit(main())
