# panoptes report schema (`schema_version: "1.0"`)

Applies to every report panoptes writes:

**Testing happens right after each merge** (on-merge trigger: `run-nightly.sh --pr N --wait-lock --out
$QA_HOME/reports/merged/PR-N.json`). The per-PR file is the primary result. A day-level discovery run (no `--pr`)
still exists as a catch-up for merges the trigger missed and for open PRs; it reuses on-merge results for the same
merge commit and writes `merged/PR-N.json` for every merged PR it had to test itself.

| File | Written by |
|---|---|
| `$QA_HOME/reports/merged/PR-N.json` | on-merge run for PR N, or a day-level run that tested PR N itself (`written_by: "nightly"`) |
| `$QA_HOME/reports/YYYY-MM-DD.json` | day-level discovery run for Shanghai day `YYYY-MM-DD` |
| any `--out PATH` | manual / ad-hoc runs (e.g. `reports/full-2026-10-10.json`) |

**The daily report selects PRs by `prs[].merged_at` converted to Asia/Shanghai** (`prs[].merged_at_shanghai` has
it pre-converted): "merged yesterday" = `merged_at_shanghai` falls on yesterday's Shanghai date. `merged_at` is present
on every `prs[]` entry (`null` for open PRs), including `not-tested` and `qa-error` entries.

All files have the same shape. `schema_version` stays `"1.0"`; fields are only ever **added**, never renamed or
removed, so readers must ignore unknown fields. Canonical copy: `docs/report-schema.md` in
[PraiseZhu/panoptes](https://github.com/PraiseZhu/panoptes); `$QA_HOME/reports/SCHEMA.md` is a copy.

## Timestamps — which field to filter on

| Field | Format | Meaning |
|---|---|---|
| **`prs[].merged_at`** | ISO 8601 UTC (`Z`), from GitHub | Merge time; **the daily report's selection key** (convert to Asia/Shanghai). `null` for open PRs. |
| **`prs[].merged_at_shanghai`** | ISO 8601 `+08:00` | `merged_at` converted to Asia/Shanghai. |
| **`prs[].tested_at`** | ISO 8601 with `+08:00`, e.g. `2026-10-11T03:52:10+08:00` | When this PR's result was produced (verdict reached). Present on every `prs[]` entry; `null` when the PR was not tested (`not-tested`, `qa-error`). For a reused on-merge result (`reused: true`) it is the time of the original on-merge test. |
| `prs[].test_started_at` | ISO 8601 `+08:00` | When processing of this PR started. |
| `prs[].created_at`, `updated_at` | ISO 8601 UTC (`Z`), from GitHub | PR lifecycle times. |
| `date` | `YYYY-MM-DD` | For day-level runs: the Asia/Shanghai day of the discovery window. Not the test time, not the selection key. |
| `generated_at` | ISO 8601 UTC (`Z`) | When the JSON was assembled. |
| `run.started_at`, `run.finished_at` | ISO 8601 UTC (`Z`) | Run start / end. |
| `run.started_at_shanghai`, `run.finished_at_shanghai` | ISO 8601 `+08:00` | Same instants in Shanghai time. |
| `system_health.heartbeat.*` | ISO 8601 `+08:00` | See *Heartbeat*. |

Reports written before 2026-10-11 (commit `78184f9`) do not have `tested_at`; fall back to `run.started_at`.

## Top level

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `"1.0"` | |
| `tool` | string | `panoptes (argus + ming-qa)` |
| `date` | `YYYY-MM-DD` | Shanghai day reported on |
| `timezone` | `"Asia/Shanghai"` | |
| `repo` | string | `xindong/mivo-canvas-plugin` |
| `generated_at` | ISO UTC | |
| `merged_sha` | string | only in `reports/merged/PR-N.json` written by nightly: the merge commit |
| `written_by` | `"nightly"` | only in merged reports written by a nightly run |
| `run` | object | see below |
| `dimensions` | object | `white-box` / `gray-box` / `black-box` → description |
| `severity_scale` | object | `P0`…`P3` → description |
| `summary` | object | see below |
| `prs` | array | one entry per discovered (or `--pr`-requested) PR, see below |
| `issues` | object | issue creation result (off by default) |
| `merged_reports` | array | `[{ pr, file }]` merged PR reports this run wrote (empty for `--pr` runs) |
| `system_health` | object | see below |

### `run`

`id` (uuid), `started_at`, `finished_at`, `started_at_shanghai`, `finished_at_shanghai`, `duration_s`,
`args` (`date`, `prs`, `out`, `reuse_merged`, `create_issues`, `limit`, `pr_timeout_min`, `deadline`, `write_merged`),
`argus_commit` (panoptes commit), `ming_qa_commit`, `llm_model`, `evidence_root`.
Merged reports written by nightly also carry `run.source_report` (path of the nightly report).

### `summary`

| Field | Notes |
|---|---|
| `pr_count` | length of `prs` (includes `not-tested` entries) |
| `prs_by_severity` | `{P0, P1, P2, P3, untested: count}` over `prs[].severity` |
| `findings_by_severity`, `findings_total` | over all `prs[].findings` |
| `reused_from_on_merge` | PR numbers whose result was copied from `reports/merged/PR-N.json` |
| `tested_count` | PRs with a real verdict (not `not-tested`, not `qa-error`) |
| `not_tested` | `[{ number, code, reason }]` |
| `qa_errors` | PR numbers whose pipeline crashed |
| `test_backed_findings_by_severity` | `{P0..P3: count}` over findings with `test_backed: true` |
| `prs_test_backed` | PR numbers with at least one test-backed finding |

## `prs[]`

| Field | Notes |
|---|---|
| `number`, `title`, `author`, `url`, `state` (`merged`/`open`/`closed`), `draft` | |
| `discovery_reason` | `merged` / `open-updated` / `manual (--pr)` |
| `created_at`, `updated_at`, `merged_at` | GitHub, UTC |
| `base_ref`, `head_sha`, `merge_sha` | GitHub |
| **`tested_sha`** | commit the checks ran on: merged → merge commit, open → PR head |
| `base_sha` | comparison revision: merged → first parent of the merge commit (= merge-base), open → merge-base(head, target) |
| `latest_main_sha` | only when a merged PR's failure was re-checked on latest main |
| **`reviewed_sha`** | commit the LLM code review looked at — equals `tested_sha` |
| `review_base_sha` | base of the reviewed diff (= `base_sha`) |
| `review_diff_source` | `local git diff base_sha..tested_sha`, or a GitHub fallback with the reason |
| `sha_consistent` | `true` when `reviewed_sha` is null or equals `tested_sha`; `false` also adds a `system_health.notes` entry |
| `test_started_at`, **`tested_at`** | see *Timestamps* |
| `changed_files`, `additions`, `deletions` | |
| `risk_class` (`A` docs-only / `B` / `C` high-risk), `risk_reason` | |
| `severity` | `P0`…`P3`, or **`"untested"`** for `verdict: not-tested`. Never `null`. An untested PR is never shown as P3. |
| **`tested`** | `true` when checks actually ran for this PR; `false` for `not-tested`, `qa-error`, `docs-only`, `skipped` |
| **`dimensions_run`** | `{ "white-box": [check ids], "gray-box": [...], "black-box": [...] }` — checks that really executed (status pass or fail) |
| **`dimensions_missing`** | dimensions with no executed check; non-empty ⇒ verdict can never be `clean` |
| `verdict` | see table below |
| `outcome`, `outcome_reason` | engine outcome (`passed`, `defect`, `flaky`, `infra_failed`, `docs_only`, …) |
| `not_tested_code`, `not_tested_reason` | only for `verdict: not-tested` |
| `partial_result` | only for `not-tested` because of a time budget: `{ verdict, checks, evidence_dir }` of the interrupted run; not used for the verdict |
| `error` | only for `qa-error` (redacted message); the entry also has one finding `{ source: "qa-error", test_backed: false, severity: "P2" }` |
| `checks[]` | `{ id, name, kind, dimension, qa_dimensions, status: pass/fail/timeout/unavailable, exit_code, duration_ms, failure_count, unavailable_reason? }` |
| `failed_checks[]` | `{ check_id, check_name, dimension, qa_dimensions, test, failure_type, status, severity, attribution, fingerprint, pr_fingerprint, evidence{ tested_sha, base_sha, exit_code, timed_out, first_run, rerun, base_run, latest_main_run?, command, log_tail, log_file } }`; `status` ∈ `introduced, pre-existing, flaky, fixed-later, unverified, timeout, setup-failed` |
| `code_review` | `{ status, summary, gate, model, tokens, findings_count, residual_risks, file }` or `{ status, error }` |
| `findings[]` | everything that can raise severity. Every finding has **`source`** ∈ `check` (a failed check, same fields as `failed_checks`), `code-review` (LLM; capped at P1, original in `llm_severity`), `coverage-gate` (P2), `qa-error` (P2, the pipeline crashed) and boolean **`test_backed`** (see *test_backed*) |
| **`test_backed`** | PR-level: `true` if at least one finding is test-backed |
| `test_backed_severity` | highest severity among test-backed findings, or `null` |
| `finding_sources` | distinct `source` values of this PR's findings |
| `coverage` | QA-dimension coverage of the matrix (`applicable`, `covered`, `missed`, `undetermined`, `passRate`, `coverage`) — *not* code coverage |
| **`coverage_gate`** | white-box code-coverage gate, see below; `null` when no unit run happened (docs-only, not-tested, qa-error) |
| **`skipped_steps`** | **every** profile step that did not run, each `{ id, nameZh, reason, reasonZh?, dimension }`; reasons: `triage: …` (optional step not relevant to the change), `skipped by --skip-step`, `not run: dependency install failed`, `not run: run stopped before this step …`, `docs-only change: no checks run` |
| `ai_summary` | `{ summary, risks, suspect, failure_explanation }` |
| `evidence_dir`, `duration_s` | |
| `reused`, `reused_from`, `reused_report_generated_at`, `reused_report_system_health` | only for reused on-merge results |

### `verdict`

| Verdict | Severity | Meaning |
|---|---|---|
| `clean` | P3 | nothing found, every planned check ran and passed, **and all three dimensions (white / gray / black) actually ran** |
| `review-suggested` | P2 | P2 findings (quality guard, unverified failure, review P2, coverage below gate) |
| `needs-attention` | P0/P1 | at least one P0/P1 finding |
| `partially-verified` | P3 | nothing found, but some checks were unavailable / timed out **or a dimension did not run** (`dimensions_missing`) |
| `qa-env-problem` | per findings | the environment failed (e.g. install failed twice) |
| `docs-only` | P3 | only docs changed; no checks run |
| `skipped` | P3 | closed without merge |
| `not-tested` | **`untested`** | discovered but not tested in this run (`tested: false`, `tested_at: null`); `not_tested_code` ∈ `excluded-by-pr-filter` (deliberate `--pr`), `over-limit`, `pr-timeout`, `deadline`. Time-budget cases are also in `system_health.qa_tool_failures`. Look for this PR's own `merged/PR-N.json` before concluding it was never tested. |
| `qa-error` | **P2** | the QA pipeline itself crashed for this PR; also listed in `system_health.qa_tool_failures` (`stage: "pipeline"`) |

Severity rules: P0 introduced failure on a merged PR that still fails on latest main · P1 introduced reproducible
product-check failure, or LLM review P0/P1 · P2 quality guard, unverified, timeout, setup failure, review P2,
coverage below gate, QA error · P3 pre-existing / flaky / fixed-later / nothing · `untested` not tested at all.

### `test_backed` — what notification / issue rules key off

`findings[].test_backed` is `true` **only** when the finding is backed by a failing check that actually ran
(`source: "check"`). Review-only (`code-review`), `coverage-gate` and `qa-error` findings are always `false`.

**Downstream notification and issue rules must key off `test_backed`, not severity alone.** A P1 with
`test_backed: false` is an LLM opinion and must not be presented or escalated as a verified defect. Suggested rule:
notify authors / open issues for P0/P1 only when `test_backed: true`; list `test_backed: false` items as
"review suggestions". panoptes' own issue creation (only with `--create-issues` + `ARGUS_ALLOW_ISSUES=1`) follows
this: P0/P1 findings with `test_backed: true` on PRs by PraiseZhu, plus `system_health` failures; nothing else.
Older reports without the field: treat `source == "check"` as test-backed.

### `coverage_gate`

```json
{
  "step": "unit", "provider": "v8 (vitest --coverage)", "thresholds_source": "ming-qa G2 …",
  "thresholds": { "lines": 80, "branches": 70, "functions": 80, "new_code": 90 },
  "values":     { "lines": 83.1, "branches": 71.4, "functions": 80.2, "new_code": 75.0 },
  "results":    { "lines": "pass", "branches": "pass", "functions": "pass", "new_code": "fail" },
  "status": "fail", "pass": false, "failed": ["new_code"],
  "new_code": { "pct": 75.0, "executable_lines": 40, "covered_lines": 30, "unloaded_files": [],
                "files": [{ "file": "src/x.ts", "executable": 40, "covered": 30, "uncovered_lines": [12, 13] }] },
  "new_code_method": "ming-qa newCode = line coverage of new code. Changed lines: git diff …",
  "reason": null
}
```

- `status`: `pass` | `fail` | `unavailable` (unit step failed to produce coverage, timed out, or coverage disabled —
  `reason` says why; **unavailable is not a failure**).
- `values.*` come from a **dedicated coverage run** of the tested revision (label `cov`: own checkout, unit step only,
  `vitest --coverage` v8, run in parallel with the first verdict run). Verdict runs (first / rerun / base / latest)
  never carry coverage, because v8 instrumentation changes the behaviour of a few Mivo tests.
  `unit_run_under_coverage` `{ exit_code, timed_out, failed_tests, note }` records how the unit suite did under
  instrumentation — informational only, never part of the verdict. Global metrics cover the files the unit tests
  load (vitest v8 default scope).
- The four metrics and thresholds are ming-qa's G2 *Unit Tests And Coverage* gate
  (`ming-qa/skills/quality-assurance-agent/references/quality-gates.md`, config `qualityGates.coverage`:
  `lines 80 · branches 70 · functions 80 · newCode 90`); `thresholds_source` names it.
- `new_code` = the 4th metric, ming-qa **newCode** — G2 lists it as "New-code coverage: 90" (config
  `qualityGates.coverage.newCode: 90`) and gives no formula. panoptes measures it as line coverage restricted to the
  PR's new code (the same line definition as `lines`): lines added/modified by `git diff base_sha..tested_sha` in
  non-test JS/TS source files; a line is executable if it starts a v8 statement (istanbul's line metric uses the same
  rule) and covered if that statement executed. Files no test loads count their code-like added lines as uncovered
  (`unloaded_files`). `null` / `not-evaluated` when the diff has no executable lines. Threshold 90 (pass at ≥ 90).
- Any metric below threshold → `status: fail` and one finding `{ source: "coverage-gate", severity: "P2" }`.

## `system_health`

| Field | Notes |
|---|---|
| `status` | `ok` · `degraded` (any qa_tool_failures / env_problems / llm_errors) · `failed` (discovery failed or any `qa-error`) |
| **`complete`** | `true` when discovery worked and every PR this run set out to test has a final verdict (no `qa-error`, no `pr-timeout` / `deadline`). `--pr` exclusions don't make it false. |
| `all_discovered_tested` | `true` when additionally every discovered PR was tested (no `not-tested` at all) |
| `qa_tool_failures[]` | `{ pr?, stage, message, dimension?, detail? }`; stages include `pipeline` (qa-error), `setup`, `<check id>` (unavailable / timeout), `pr-timeout`, `deadline`, `discovery`, `janitor`, `issues`, `write-merged-report`, `ming-qa assert-code-review` |
| `env_problems[]` | `{ pr?, message }` |
| `llm_errors[]` | `{ pr, stage, model, … }` |
| `environment` | `{ host, platform, node, sandbox, bwrap, chromium, playwright, python, disk_free_gb }` |
| `discovery` | `{ window_start, window_end, found: [{ number, reason }] }` |
| `notes[]` | strings (reuse notes, `tested_sha`/`reviewed_sha` mismatches, …) |
| **`heartbeat`** | see below |

### `heartbeat`

```json
{
  "run_started_at": "2026-10-11T03:38:36+08:00", "run_finished_at": "2026-10-11T06:10:02+08:00", "duration_s": 9086,
  "last_beat_at": "2026-10-11T06:10:02+08:00", "pr_timeout_min": null, "deadline": null,
  "prs": [{ "number": 888, "started_at": "…+08:00", "finished_at": "…+08:00", "duration_s": 905, "verdict": "clean" }]
}
```

One entry per PR the run started, in order (not-tested PRs that never started are absent; see `summary.not_tested`).
