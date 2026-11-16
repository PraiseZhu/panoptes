# Exact replay quality metrics

`python3 pi/bin/quality_metrics.py` reads merged reports and writes a JSON
summary. The command is read-only unless `--out` is used to create the new
summary file. It accepts report files, report directories, and JSONL files.

The unit of measurement is one finding fingerprint. A finding that appears in
several reports is counted once; the report keeps the latest complete evidence
for that fingerprint. A replay is eligible only when the finding has a
40-character `tested_sha`, `base_sha`, a source PR, environment evidence, and
an exact replay entry. Missing evidence is listed under `unknown_findings` and
is excluded from error denominators.

| Metric | Formula | Meaning |
|---|---|---|
| `precision` | exact replay `pass` / (`pass` + `fail`) | Fraction of replayed fixes that resolved the source failure |
| `false_positive` | exact replay `fail` / (`pass` + `fail`) | Fraction of replayed fixes that still fail |
| `first_pass_fix` | findings whose first replay is `pass` / known findings | Fixes that worked on the first replay |
| `regression_escape` | findings with a related-test regression / known findings | Fixes whose replay evidence reports a related test failing on the fix SHA |
| `environment_loss` | unknown findings missing environment evidence / all unique findings | Evidence lost because the environment was not recorded |
| `duplicate_fingerprint` | duplicate records / all finding records | Repeated report rows suppressed by fingerprint deduplication |

`blocked` and `inconclusive` replays remain visible in the unknown list. A
`blocked` result means the replay could not run or lacked a target. An
`inconclusive` result means the original failure did not reproduce, so the
fix cannot be marked successful. Neither result is silently converted to
`fail` or `pass`.

The exact replay writer records `original_sha`, `fix_sha`, the original case
command, per-side run results, and the overall `status` (`pass`, `fail`,
`blocked`, or `inconclusive`). `opsfix_retest.py` writes that status to the
fix PR report and leaves a failed or inconclusive replay open for another run.
