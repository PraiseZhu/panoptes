#!/usr/bin/env python3
"""Read-only quality metrics for exact replay reports.

The command consumes report JSON/JSONL files and never edits them.  A finding
is counted once per fingerprint.  Missing evidence is reported as ``unknown``
and is excluded from precision denominators; it is never treated as a zero.

Examples::

    python3 pi/bin/quality_metrics.py /workspace/mivo-qa/reports/merged \
      --out /workspace/mivo-qa/out/ops/quality-metrics.json
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import sys

SHA = re.compile(r"^[0-9a-f]{40}$")
REPLAY_RESULTS = {"pass", "fail", "inconclusive", "blocked"}
EXACT_METHODS = {"check", "exact-replay", "exact_replay", "fix-retest-evidence"}


def _files(paths):
    for raw in paths or []:
        if os.path.isdir(raw):
            yield from sorted(glob.glob(os.path.join(raw, "**", "*.json"), recursive=True))
            yield from sorted(glob.glob(os.path.join(raw, "**", "*.jsonl"), recursive=True))
        elif os.path.isfile(raw):
            yield raw
        else:
            yield from sorted(glob.glob(raw))


def load_reports(paths):
    """Load report documents from files/directories, ignoring unrelated JSON."""
    out = []
    seen = set()
    for path in _files(paths):
        if path in seen:
            continue
        seen.add(path)
        try:
            if path.endswith(".jsonl"):
                with open(path, encoding="utf-8") as handle:
                    docs = [json.loads(line) for line in handle if line.strip()]
            else:
                with open(path, encoding="utf-8") as handle:
                    docs = [json.load(handle)]
        except (OSError, ValueError):
            continue
        for doc in docs:
            if isinstance(doc, dict) and (isinstance(doc.get("prs"), list) or "number" in doc):
                out.append((path, doc))
    return out


def _prs(doc):
    prs = doc.get("prs")
    return prs if isinstance(prs, list) else [doc] if "number" in doc else []


def _environment(doc, pr, finding):
    for obj in (finding, finding.get("evidence") or {}, pr, doc, doc.get("system_health") or {}):
        for key in ("environment", "environment_evidence", "env", "environment_status"):
            value = obj.get(key) if isinstance(obj, dict) else None
            if value not in (None, {}, [], ""):
                return value
    return None


def _sha(value):
    return bool(SHA.fullmatch(str(value or "")))


def _finding_records(reports):
    rows = []
    for path, doc in reports:
        for pr in _prs(doc):
            number = pr.get("number")
            values = []
            for kind in ("findings", "pending_verification"):
                for finding in pr.get(kind) or []:
                    if isinstance(finding, dict):
                        values.append((kind, finding))
            for index, (kind, finding) in enumerate(values):
                ev = finding.get("evidence") or {}
                rows.append({
                    "fingerprint": finding.get("fingerprint"),
                    "source_pr": number,
                    "kind": kind,
                    "source": finding.get("source"),
                    "tested_sha": finding.get("tested_sha") or ev.get("tested_sha") or pr.get("tested_sha"),
                    "base_sha": finding.get("base_sha") or ev.get("base_sha") or pr.get("base_sha"),
                    "run_id": finding.get("run_id") or ev.get("run_id") or pr.get("run_id"),
                    "artifact_refs": finding.get("artifact_refs") or ev.get("artifact_refs"),
                    "evidence_manifest": finding.get("evidence_manifest") or pr.get("evidence_manifest"),
                    "environment": _environment(doc, pr, finding),
                    "report": path,
                    "index": index,
                })
    return rows


def _replay_records(reports):
    rows = []
    for path, doc in reports:
        for pr in _prs(doc):
            for entry in pr.get("retest") or []:
                if not isinstance(entry, dict) or entry.get("result") not in REPLAY_RESULTS:
                    continue
                method = str(entry.get("method") or entry.get("source") or "").lower()
                detail = entry.get("detail") or {}
                exact = bool(detail.get("exact_replay") or detail.get("kind") == "fix-retest-evidence" or method in EXACT_METHODS)
                if not exact:
                    continue
                rows.append({
                    "fix_pr": pr.get("number"), "original_pr": entry.get("original_pr"),
                    "fingerprint": entry.get("fingerprint"), "result": entry.get("result"),
                    "at": entry.get("at"), "attempts": entry.get("attempts"),
                    "regression": bool(detail.get("regression") or detail.get("regression_escape") or detail.get("related_regression")),
                    "detail": detail, "report": path,
                })
    return rows


def _at(value):
    try:
        value = str(value).replace("Z", "+00:00")
        parsed = dt.datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return dt.datetime.min.replace(tzinfo=dt.timezone.utc)


def _latest_replay(rows):
    """Use fail over pass, then the latest replay, matching opsfix's rule."""
    if not rows:
        return None
    fails = [x for x in rows if x["result"] == "fail"]
    return max(fails or rows, key=lambda x: _at(x.get("at")))


def _evidence_missing(row):
    missing = []
    if not _sha(row.get("tested_sha")):
        missing.append("tested_sha")
    if not _sha(row.get("base_sha")):
        missing.append("base_sha")
    if not row.get("source_pr"):
        missing.append("source_pr")
    if not row.get("run_id") and not row.get("evidence_manifest") and not row.get("artifact_refs"):
        missing.append("replay_evidence")
    if row.get("environment") in (None, {}, [], ""):
        missing.append("environment")
    return missing


def compute_metrics(reports):
    """Return JSON-serializable quality metrics for ``load_reports`` output.

    ``reports`` may be the ``[(path, document), ...]`` result of
    :func:`load_reports`, or a list of plain report dictionaries (useful for
    unit tests).
    """
    if reports and isinstance(reports[0], dict):
        reports = [("<memory>", x) for x in reports]
    reports = list(reports or [])
    findings = _finding_records(reports)
    replays = _replay_records(reports)
    by_fp = {}
    for row in findings:
        fp = row.get("fingerprint")
        if fp:
            by_fp.setdefault(fp, []).append(row)
    unique = []
    for fp, rows in by_fp.items():
        # The first record carries the source PR and the most complete evidence.
        unique.append(max(rows, key=lambda x: (len(_evidence_missing(x)) * -1, x.get("source_pr") or 0)))
    replay_by = {}
    for row in replays:
        if row.get("fingerprint"):
            replay_by.setdefault(row["fingerprint"], []).append(row)
    known, unknown = [], []
    for row in unique:
        missing = _evidence_missing(row)
        replay = _latest_replay(replay_by.get(row["fingerprint"], []))
        if not replay:
            missing.append("exact_replay")
        elif replay.get("result") not in {"pass", "fail"}:
            missing.append(f"exact_replay:{replay.get('result')}")
        if missing:
            unknown.append({"fingerprint": row["fingerprint"], "missing": sorted(set(missing))})
        else:
            known.append((row, replay))
    pass_rows = [(f, r) for f, r in known if r["result"] == "pass"]
    fail_rows = [(f, r) for f, r in known if r["result"] == "fail"]
    fixed_or_failed = len(pass_rows) + len(fail_rows)
    first_pass = 0
    regression = 0
    for finding, latest in known:
        history = sorted(replay_by.get(finding["fingerprint"], []), key=lambda x: _at(x.get("at")))
        if history and history[0]["result"] == "pass":
            first_pass += 1
        if any(x.get("regression") or (x.get("detail") or {}).get("rows") and any(y.get("check") == "related" and y.get("fix") == "fail" for y in (x.get("detail") or {}).get("rows", [])) for x in history):
            regression += 1
    duplicate_records = max(0, len(findings) - len(unique))
    duplicate_fingerprints = sum(1 for rows in by_fp.values() if len(rows) > 1)
    environment_unknown = sum(1 for x in unknown if "environment" in x["missing"])
    denominator = fixed_or_failed
    ratio = lambda n, d: round(n / d, 4) if d else None
    metrics = {
        "precision": ratio(len(pass_rows), denominator),
        "false_positive": ratio(len(fail_rows), denominator),
        "first_pass_fix": ratio(first_pass, denominator),
        "regression_escape": ratio(regression, len(known)),
        "environment_loss": ratio(environment_unknown, len(unique)),
        "duplicate_fingerprint": ratio(duplicate_records, len(findings)),
    }
    return {
        "schema": "qa.quality-metrics/v1",
        "metrics": metrics,
        "metric_details": {
            "precision": {"numerator": len(pass_rows), "denominator": denominator},
            "false_positive": {"numerator": len(fail_rows), "denominator": denominator},
            "first_pass_fix": {"numerator": first_pass, "denominator": denominator},
            "regression_escape": {"numerator": regression, "denominator": len(known)},
            "environment_loss": {"numerator": environment_unknown, "denominator": len(unique)},
            "duplicate_fingerprint": {"numerator": duplicate_records, "denominator": len(findings)},
        },
        "counts": {"finding_records": len(findings), "unique_findings": len(unique), "known": len(known),
                   "unknown": len(unknown), "unknown_denominator": len(unknown), "exact_replays": len(replays),
                   "duplicate_fingerprints": duplicate_fingerprints, "duplicate_records": duplicate_records},
        "unknown_findings": unknown,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+", help="report JSON/JSONL files, directories, or globs")
    ap.add_argument("--out", help="write JSON to this path; stdout remains unchanged")
    args = ap.parse_args(argv)
    result = compute_metrics(load_reports(args.paths))
    raw = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        parent = os.path.dirname(os.path.abspath(args.out))
        os.makedirs(parent, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(raw)
    print(raw, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
