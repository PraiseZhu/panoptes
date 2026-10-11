# panoptes report schema (`schema_version: "1.0"`)

Applies to every report panoptes writes:

**Testing happens right after each merge** (on-merge trigger: `run-nightly.sh --pr N --wait-lock --out
$QA_HOME/reports/merged/PR-N.json`). The per-PR file is the primary result. A day-level discovery run (no `--pr`)
still exists as a catch-up for merges the trigger missed and for open PRs; it reuses on-merge results for the same
merge commit and writes `merged/PR-N.json` for every merged PR it had to test itself.

| File | Written by |
|---|---|
| `$QA_HOME/reports/merged/PR-N.json` | on-merge run for PR N, or a day-level run that tested PR N itself (`written_by: "nightly"`) |
| `$QA_HOME/reports/open/PR-N.json` | a run with `--write-open`: one file per **open** PR of the run, tested or not (`not-tested` entries carry `not_tested_code` / `not_tested_reason`; PRs excluded by `--pr` are not written) |
| `$QA_HOME/reports/YYYY-MM-DD.json` | day-level discovery run for Shanghai day `YYYY-MM-DD` |
| any `--out PATH` | manual / ad-hoc runs |
| `$QA_HOME/reports/catch-up/PR-N.json` | `run-nightly.sh --catch-up` child run for PR N (it also writes `merged/PR-N.json` via `--write-merged`) |
| `$QA_HOME/reports/catch-up/run-<ts>.json` | one catch-up invocation that had work to do (`schema: "mivo-qa-catch-up/v1"`, see *Catch-up*); `catch-up/state.json` holds attempt counters |
| `<name>.vN.json` next to any of the above | the **original** of a report that was later re-written by a re-run (see *Report preservation*) |
| `$QA_HOME/reports/full-YYYY-MM-DD.json` | combined day report assembled from the per-PR files (merged + open); carries `assembled_from` (see *Combined reports*) |

**The daily report selects PRs by `prs[].merged_at` converted to Asia/Shanghai** (`prs[].merged_at_shanghai` has
it pre-converted): "merged yesterday" = `merged_at_shanghai` falls on yesterday's Shanghai date. `merged_at` is present
on every `prs[]` entry (`null` for open PRs), including `not-tested` and `qa-error` entries.

All files have the same shape. `schema_version` stays `"1.0"`; fields are only ever **added**, never renamed or
removed, so readers must ignore unknown fields. Canonical copy: `docs/report-schema.md` in
[PraiseZhu/panoptes](https://github.com/PraiseZhu/panoptes); `$QA_HOME/reports/SCHEMA.md` is a copy.

## Report preservation — never rewrite a produced report

A report that has been written is never edited in place, and never "fixed up" with an ad-hoc script.
**Re-classification means a re-run** of the same pipeline (`run-nightly.sh --pr N …`); when the re-run writes to a
path that already exists, panoptes first renames the existing file to `<name>.vN.json` (next free `N`:
`PR-902.v1.json`, `PR-902.v2.json`, …) and then writes the new file, which records `previous_version`
(file name of the kept original). This applies to every writer (nightly, per-PR files, `--out`, `assemble`).
Readers that glob a directory must match `PR-<n>.json` exactly (`^PR-\d+\.json$`) so kept originals are not read
as current results. The 2026-10-10 reports are left as they are (not retroactively patched).

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
| `head_sha` | string | only in `reports/open/PR-N.json`: the PR head that was tested (`null` when not tested) |
| `written_by` | string | only in per-PR files: `"nightly"` (written by a multi-PR run) or `"backfill (panoptes processPr)"` (one PR re-run through the same pipeline function) |
| `run` | object | see below |
| `dimensions` | object | `white-box` / `gray-box` / `black-box` → description |
| `severity_scale` | object | `P0`…`P3` → description |
| `summary` | object | see below |
| `prs` | array | one entry per discovered (or `--pr`-requested) PR, see below |
| `issues` | object | issue creation result (off by default) |
| `merged_reports` | array | `[{ pr, file }]` merged PR reports this run wrote (empty for `--pr` runs) |
| `open_reports` | array | only with `--write-open`: `[{ pr, file }]` open PR reports this run wrote |
| `system_health` | object | see below |

### `run`

`id` (uuid), `started_at`, `finished_at`, `started_at_shanghai`, `finished_at_shanghai`, `duration_s`,
`args` (`date`, `prs`, `out`, `reuse_merged`, `create_issues`, `limit`, `pr_timeout_min`, `deadline`, `write_merged`, `write_open`),
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
| `review_diff_source` | `local git diff base_sha..tested_sha` (docs-only: `base_sha..merge_sha` / `base_sha..head_sha`), or a GitHub fallback with the reason |
| `sha_consistent` | `true` when `reviewed_sha` is null or equals `tested_sha` (docs-only, where nothing is tested: equals `merge_sha` for merged / `head_sha` for open PRs); `false` also adds a `system_health.notes` entry |
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
| `checks[]` | `{ id, name, kind, dimension, qa_dimensions, status: pass/fail/timeout/unavailable, exit_code, duration_ms, failure_count, unavailable_reason?, sub_checks? }`; `sub_checks` only for probe steps (`api-probe`): `{ total, passed, failed, skipped, auth: { account, account_via, host_exchange } }` — `status: pass` means every probe that **ran** passed; skipped probes are listed in `skipped_steps`, never counted as passed |
| `failed_checks[]` | `{ check_id, check_name, dimension, qa_dimensions, test, failure_type, status, severity, attribution, fingerprint, pr_fingerprint, evidence{ tested_sha, base_sha, exit_code, timed_out, first_run, rerun, base_run, latest_main_run?, command, log_tail, log_file } }`; `status` ∈ `introduced, pre-existing, flaky, fixed-later, unverified, timeout, setup-failed`; also boolean **`flaky`** (`true` iff `status: "flaky"`), `related_to_pr: false` for flaky entries, and, in reports written before 2026-10-11 only, `reclassified { at, from, reason }` (legacy in-place re-classification; no longer done — see *Report preservation*) |
| `code_review` | `{ status, summary, gate, model, tokens, findings_count, residual_risks, file }` or `{ status, error }`. **Also present for docs-only PRs** (reviewed on the same commit: merged → `base_sha` (first parent = merge-base) `..merge_sha`); `null` only with `--no-review` |
| `findings[]` | everything that can raise severity. Every finding has **`source`** ∈ `check` (a failed check, same fields as `failed_checks`), `code-review` (LLM; capped at P1, original in `llm_severity`), `coverage-gate` (P2), `qa-error` (P2, the pipeline crashed) and boolean **`test_backed`** (see *test_backed*) |
| **`test_backed`** | PR-level: `true` if at least one finding is test-backed |
| `test_backed_severity` | highest severity among test-backed findings, or `null` |
| `finding_sources` | distinct `source` values of this PR's findings |
| `coverage` | QA-dimension coverage of the matrix (`applicable`, `covered`, `missed`, `undetermined`, `passRate`, `coverage`) — *not* code coverage |
| **`coverage_gate`** | white-box code-coverage gate, see below; **docs-only: `{ status: "not-applicable", reason: "docs-only change" }`**; `null` when no unit run happened for another reason (not-tested, qa-error) |
| **`skipped_steps`** | **every** profile step that did not run, each `{ id, nameZh, reason, reasonZh?, dimension }`; reasons: `triage: …` (optional step not relevant to the change), `skipped by --skip-step`, `not run: dependency install failed`, `not run: run stopped before this step …`, `docs-only change: no checks run`. **Sub-probes of a probe step that did not run** are listed too, as `{ id: "api-probe/<probe id>", nameZh, reason, parent_step: "api-probe", dimension }` — e.g. reason `no test account` for signed-in probes when neither local sign-in nor `MIVO_QA_TEST_*` is available |
| **`triage`** | dimension applicability for this PR, see *Triage* |
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
| `docs-only` | P3 (or the highest code-review finding) | only docs changed; no checks run, `tested: false`, `tested_sha: null`; the ming-qa code review still runs on the merge commit / head (`code_review`, `reviewed_sha`), `coverage_gate.status: "not-applicable"`. Review findings are `test_backed: false`. |
| `skipped` | P3 | closed without merge |
| `not-tested` | **`untested`** | discovered but not tested in this run (`tested: false`, `tested_at: null`); `not_tested_code` ∈ `excluded-by-pr-filter` (deliberate `--pr`), `over-limit`, `pr-timeout`, `deadline`. Time-budget cases are also in `system_health.qa_tool_failures`. Look for this PR's own `merged/PR-N.json` before concluding it was never tested. |
| `qa-error` | **P2** | the QA pipeline itself crashed for this PR; also listed in `system_health.qa_tool_failures` (`stage: "pipeline"`) |

Severity rules: P0 introduced failure on a merged PR that still fails on latest main · P1 introduced reproducible
product-check failure, or LLM review P0/P1 · P2 quality guard, unverified, timeout, setup failure, **flaky (unstable)**,
review P2, new-code coverage below gate, QA error · P3 pre-existing / fixed-later / nothing · `untested` not tested at all.

### Flaky (unstable) failures

A failed check is **`status: "flaky"`** when it failed on the first run and the **same test passed on a clean rerun of
the same commit** (per-test evidence: the step's JSON test report; the `bench` step writes one since 2026-10-11).
It is reported as **P2 "unstable"** (worth fixing the test / budget), but it is **not attributed to the PR**
(`related_to_pr: false`, PR `outcome: "flaky"` when nothing else failed) and **`test_backed: false`** with `flaky: true`:
a failure that did not reproduce is not a verified defect and must **never** trigger author notifications or issues.
Example: 2026-10-10 `bench` › `shadowRoundTrip` 20k round-trip hit vitest's 5000 ms test timeout on #892 and #898 and
passed on the rerun (4.8 s / 4.9 s).
If the rerun has no per-test result for the failing test (no JSON report), the failure stays `unverified` (P2,
`test_backed: true`) — panoptes does not guess.

### `test_backed` — what notification / issue rules key off

`findings[].test_backed` is `true` **only** when the finding is backed by a failing check that actually ran
(`source: "check"`) **and was not `flaky`**. Flaky checks (failed, then passed on the rerun), review-only
(`code-review`), `coverage-gate` and `qa-error` findings are always `false`.

**Downstream notification and issue rules must key off `test_backed`, not severity alone.** A P1 with
`test_backed: false` is an LLM opinion and must not be presented or escalated as a verified defect. Suggested rule:
notify authors / open issues for P0/P1 only when `test_backed: true`; list `test_backed: false` items as
"review suggestions". panoptes' own issue creation (only with `--create-issues` + `ARGUS_ALLOW_ISSUES=1`) follows
this: P0/P1 findings with `test_backed: true` on PRs by PraiseZhu, plus `system_health` failures; nothing else.
Older reports without the field: treat `source == "check"` and `status != "flaky"` as test-backed.

### `coverage_gate`

```json
{
  "step": "unit", "provider": "v8 (vitest --coverage)", "thresholds_source": "ming-qa G2 …",
  "thresholds": { "new_code": 90 }, "gated_metrics": ["new_code"],
  "baseline":   { "role": "trend-only …", "lines": 63.1, "branches": 61.4, "functions": 70.2, "reference": { "lines": 80, "branches": 70, "functions": 80 } },
  "values":     { "lines": 63.1, "branches": 61.4, "functions": 70.2, "new_code": 75.0 },
  "results":    { "lines": "baseline", "branches": "baseline", "functions": "baseline", "new_code": "fail" },
  "status": "fail", "pass": false, "failed": ["new_code"],
  "new_code": { "pct": 75.0, "executable_lines": 40, "covered_lines": 30, "unloaded_files": [],
                "files": [{ "file": "src/x.ts", "executable": 40, "covered": 30, "uncovered_lines": [12, 13] }] },
  "new_code_method": "ming-qa newCode = line coverage of new code. Changed lines: git diff …",
  "reason": null
}
```

- `status`: `pass` | `fail` | `not-evaluated` | `unavailable` (unit step failed to produce coverage, timed out, or coverage disabled —
  `reason` says why; **unavailable is not a failure**) | `not-applicable` (docs-only PR: the object is exactly
  `{ "status": "not-applicable", "reason": "docs-only change" }`; not a failure, no finding).
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
- **Only new-code coverage is gated.** `thresholds` is `{ "new_code": 90 }`; `gated_metrics: ["new_code"]`.
  Repo-wide `lines` / `branches` / `functions` are **baseline trend data only**: recorded in
  `baseline { role, lines, branches, functions, reference: { lines: 80, branches: 70, functions: 80 } }`
  (ming-qa's G2 numbers kept as reference lines), `results.<metric> = "baseline"`, never a failure, never a finding.
  (Since 2026-10-11; earlier reports gated all four.)
- `new_code` < 90 → `status: fail`, `failed: ["new_code"]` and one finding
  `{ source: "coverage-gate", severity: "P2", title: "new-code coverage X% < 90%" }`.
- `status: not-evaluated` (`pass: null`) when new-code coverage could not be computed (diff unavailable, no per-line
  data); `reason` says why. Not a failure, no finding.

### Triage (`prs[].triage`) — dimension applicability

Every tested PR carries an explicit decision for each of the 29 QA dimensions of the matrix
(`argus/src/matrix.mjs` `DIMENSIONS`), made by Jev (`api.typesafe.ai/v1/systemone`, model `jev-1.13.0`, key
`ARGUS_TYPESAFE_API_KEY`) plus the profile:

```json
{
  "status": "ok", "model": "jev-1.13.0", "reason": null, "total": 29,
  "applicable": ["unit", "exception", …], "not_applicable": ["money-reward-settlement", …], "undetermined": [],
  "thresholds": { "applicable_high": 0.7, "cutoff": 0.5, "not_applicable_high": 0.3 },
  "dimensions": {
    "unit": { "label": "单元", "group": "…", "applicability": "applicable", "source": "jev", "confidence": "high", "p": 0.87, "reason": "Jev p=0.87 ≥ 0.7: …" },
    "provably-fair": { "applicability": "not-applicable", "source": "profile", "confidence": "high", "reason": "profile notApplicable: …" }
  },
  "duration_ms": 190, "usage": { "input_tokens": 2573, "output_tokens": 466 }
}
```

- `applicability` ∈ `applicable` | `not-applicable` | `undetermined`; every entry has a `reason`.
- `source`: `profile` (profile `notApplicable`), `path` (profile path hints matched a changed file → applicable),
  `jev` (Jev probability `p`), `none` (no decision possible).
- Jev bands: `p ≥ 0.7` applicable (high) · `0.5 ≤ p < 0.7` applicable (low, conservative) ·
  `0.3 < p < 0.5` not-applicable (low) · `p ≤ 0.3` not-applicable (high). With `status: "ok"` nothing is `undetermined`.
- `status`: `ok` · `not-configured` (no key) · `failed` (Jev error; `reason` says why) · `not-run` (docs-only PR).
  Only for `not-configured` / `failed` are dimensions left `undetermined`; then `system_health.notes` gets
  `维度适用性未判定（Jev 未配置）` (no key) or `维度适用性未判定（Jev 分诊失败）：#N …` (failure), and failures are also in
  `system_health.llm_errors` (`stage: "triage(jev)"`) and `system_health.triage.failed`.
- Which optional checks run still follows the conservative step plan (anything not clearly unrelated runs).

## Issue dedup (`--create-issues` only)

Issue bodies carry `<!-- mivo-qa-fingerprint: <fp> -->` and, since 2026-10-11, `<!-- mivo-qa-dedup: <fp>@<sha> -->`;
the ```` ```json mivo-qa-finding ```` block gains `dedup_key` and `merge_sha`. Dedup key = fingerprint + merge commit
(open PRs: tested head; `system_health` issues: `day:<date>`). An **open** issue with the same fingerprint always
dedups; a **closed** one dedups only for the same key, so a problem that recurs on a new merge commit after its issue
was closed opens a new issue (`action: "created-recurred"`, `previous_issues`). Old issues without the dedup marker
behave as "commit unknown". Code-review fingerprints include a 12-hex hash of the normalized finding title, so two
different findings on the same file / symbol / category no longer collide. `mini/check-queue.sh` validates
`dedup_key` when present (must be `<fingerprint>@[A-Za-z0-9:-]+`) and prints `MIVO_QA_DEDUP_KEY=` (falls back to the
fingerprint for old issues).

## Catch-up (`run-nightly.sh --catch-up`)

Lists PRs merged yesterday + today (Asia/Shanghai) with `gh pr list` (read-only), and treats a PR as a gap when
`reports/merged/PR-N.json` is missing / unreadable / has no final verdict, or its `tested_sha` (docs-only:
`merged_sha`) differs from the merge commit. Gaps are tested oldest-first via
`run-nightly.sh --pr N --date <merge day> --write-merged --wait-lock S --no-issues --out reports/catch-up/PR-N.json`.
A lock timeout (exit 75) stops the invocation; the next one retries. After 3 attempts a PR is given up:
`reports/catch-up/run-<ts>.json` has `system_health.gave_up[]` / `qa_tool_failures[]`
(`{ pr, stage: "catch-up", merge_sha, attempts, message }`), and the next nightly report lists it in
`system_health.qa_tool_failures` (`stage: "catch-up"`, for 48 h). With nothing to do it exits 0 silently and writes nothing.

## `system_health`

| Field | Notes |
|---|---|
| `status` | `ok` · `degraded` (any qa_tool_failures / env_problems / llm_errors) · `failed` (discovery failed or any `qa-error`) |
| **`complete`** | `true` when discovery worked and every PR this run set out to test has a final verdict (no `qa-error`, no `pr-timeout` / `deadline`). `--pr` exclusions don't make it false. |
| `all_discovered_tested` | `true` when additionally every discovered PR was tested (no `not-tested` at all) |
| `qa_tool_failures[]` | `{ pr?, stage, message, dimension?, detail? }`; stages include `pipeline` (qa-error), `setup`, `<check id>` (unavailable / timeout), `pr-timeout`, `deadline`, `discovery`, `janitor`, `issues`, `write-merged-report`, `write-open-report`, `ming-qa assert-code-review`, `catch-up` (a gap given up after 3 attempts) |
| `env_problems[]` | `{ pr?, message }` |
| `llm_errors[]` | `{ pr, stage, model, … }` |
| `environment` | `{ host, platform, node, sandbox, bwrap, chromium, playwright, python, disk_free_gb }` |
| `discovery` | `{ window_start, window_end, found: [{ number, reason }] }` |
| `notes[]` | strings (reuse notes, `tested_sha`/`reviewed_sha` mismatches, triage notes `维度适用性未判定（…）`, …) |
| `triage` | `{ model, prs: [{ pr, status, applicable, not_applicable, undetermined, duration_ms }], failed: [{ pr, reason }] }` — present when at least one PR was triaged |
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

## Combined reports (`reports/full-YYYY-MM-DD.json`)

Same top-level shape as a day report, assembled from per-PR files instead of one run: `prs[]` holds each PR's entry
from `reports/merged/PR-N.json` / `reports/open/PR-N.json` unchanged; `summary` is recomputed over those entries;
`system_health` is aggregated — `status` = worst of the sources (`failed` > `degraded` > `ok`), `qa_tool_failures`,
`env_problems`, `llm_errors` and `notes` concatenated (each item keeps or gains `pr`), `heartbeat.prs` concatenated,
`complete` = every source complete, `all_discovered_tested` = no `not-tested` entry. Extra fields:
`assembled_from: [{ pr, file, generated_at, run_id, system_health }]` and `run.kind: "assembled"`.

