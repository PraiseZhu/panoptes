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
| `$QA_HOME/reports/catch-up/run-<ts>.json` | one catch-up invocation that had work to do (`schema: "mivo-qa-catch-up/v1"`, `trigger`, `create_issues`, see *Catch-up*); `catch-up/state.json` holds attempt counters |
| `history/<name>.vN.json` under the directory of any of the above (e.g. `reports/merged/history/PR-902.v1.json`) | the **original** of a report that was later re-written by a re-run or re-store (see *Report preservation*). Before 2026-10-11 these sat next to the current file; all were moved into `history/` |
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
path that already exists, panoptes first moves the existing file to **`history/<name>.vN.json`** in the same directory
(next free `N`: `reports/merged/history/PR-902.v1.json`, `…v2.json`, …) and then writes the new file, which records
**`previous_version`** = the kept original's path **relative to the directory of the current file**
(`"history/PR-902.v1.json"`; the same string is used inside history files, so `reports/merged/` + `previous_version`
always resolves). This applies to every writer (nightly, per-PR files, `--out`, `assemble`, `store-merged`, `normalize`).

**Read rule: readers read only `reports/merged/PR-<digits>.json` (`^PR-\d+\.json$`, not recursive)** — likewise
`reports/open/PR-<digits>.json`. `history/` holds superseded versions only; never select PRs from it. Older files may
have `previous_version` as a bare name or `run.source_report` pointing at the old flat location; the 2026-10-11
migration (`cli.mjs migrate-history --dir reports/merged`) moved every `*.vN.json` into `history/` and rewrote only
those two path fields.

**Storing a re-run as the current per-PR result.** A re-run written elsewhere (e.g. `--pr N --out scratch/…`) becomes
the current `reports/merged/PR-N.json` through the same writer the nightly uses, never by copying or editing:
`cd argus && node src/cli.mjs store-merged --from <that report> --pr N` (env `ARGUS_ROOT`, `ARGUS_REPORTS_DIR` as in
`run-nightly.sh`). The existing file becomes `PR-N.vN.json`; the new one is the per-PR form of the re-run
(`written_by: "nightly"`, `run.source_report` = the re-run file) with `previous_version`. Alternatively re-run with
`--write-merged`, which does the same at the end of the run. `--from` may also be the current
`reports/merged/PR-N.json` itself (re-store it in single-PR form: only PR N, `date` = its Shanghai merge day); the
original is kept as `PR-N.vN.json` and `run.source_report` points at that kept file.

**Normalization on re-store.** `store-merged` and `cli.mjs normalize --file REPORT` (for day / `full-` /
`merged-batch-` files) re-store a report through the same writer and apply one normalization: a `flaky` entry without
`base_comparison` (written before the base comparison existed) gets
`{ compared: false, result: "not-compared", reason: "report predates feature", note_zh: "未与合并前版本对比（该报告早于此功能生成）", base_sha, base_run }`.
The change is listed in the new file's **`normalized[]`** `[{ at, by, changes: [{ pr, field, check_id, test, change }] }]`;
the original stays in `history/`. Applied on 2026-10-11 to `merged/PR-892.json`, `full-2026-10-10.json` and
`merged-batch-2026-10-10.json` (the only current reports with such entries).

### Older reports (fields that did not exist yet)

| Written | Missing fields — readers must treat them as absent, not as empty / zero |
|---|---|
| 2026-10-10 (all `reports/merged/PR-*.v1.json` and the 10-10 originals still current: #892, #894, #897 and the 10-10 day / `full-` / `merged-batch-` files) | `ai_tests`, `coverage.dimension_status`, `coverage.applicable_total` / `covered_total` / `gaps` / `ai_covered`, `triage.dimensions` decisions (older triage only), `failed_checks[].base_comparison` (→ say **未与合并前版本对比**), `tested_at` (before `78184f9`), `run.trigger`, `checks[].shards` / `reused` |
| 2026-10-11 before the dimension_status-derived totals (`coverage.basis: "triage (prs[].triage)"`) | `coverage.applicable` / `covered` / `missed` / `undetermined` follow the old p ≥ 0.7 rule and may contradict `dimension_status`; use `applicable_total` / `covered_total` / `gaps` / `dimension_status` only |
| before 2026-10-18 (argus before the qa/integration-20261018 merge) | no `change_kind`, no `own-tests` findings, no `node-test` / `pytest` checks, no `coverage_gate.new_code.not_measurable_files` (plain-JS files then counted as 0 % in `unloaded_files`), no `ai_tests.uncovered_targets`. Treat missing as absent / empty |

For a "N applicable, M covered" sentence on an old report without `applicable_total`: say the dimension statistics
were not produced for that run; do not compute them from the legacy `coverage.applicable` list.

## Timestamps — which field to filter on

| Field | Format | Meaning |
|---|---|---|
| **`prs[].merged_at`** | ISO 8601 UTC (`Z`), from GitHub | Merge time; **the daily report's selection key** (convert to Asia/Shanghai). `null` for open PRs. |
| **`prs[].merged_at_shanghai`** | ISO 8601 `+08:00` | `merged_at` converted to Asia/Shanghai. |
| **`prs[].tested_at`** | ISO 8601 with `+08:00`, e.g. `2026-10-11T03:52:10+08:00` | When this PR's result was produced (verdict reached). Present on every `prs[]` entry; `null` when the PR was not tested (`not-tested`, `qa-error`). For a reused on-merge result (`reused: true`) it is the time of the original on-merge test. |
| `prs[].test_started_at` | ISO 8601 `+08:00` | When processing of this PR started. |
| `prs[].created_at`, `updated_at` | ISO 8601 UTC (`Z`), from GitHub | PR lifecycle times. |
| `date` | `YYYY-MM-DD` | For day-level runs: the Asia/Shanghai day of the discovery window. For `--pr` runs without `--date` and for every `reports/merged/PR-N.json`: the PR's `merged_at` as a Shanghai date (open PRs: `updated_at`). Not the test time, not the selection key. |
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
| `date` | `YYYY-MM-DD` | Shanghai day reported on. Day-level run: `--date` or yesterday. `--pr` run without `--date` (since 2026-10-11): the PR's merge time converted to a Shanghai date (open PRs `updated_at`; several PRs: the latest); per-PR merged files always use the merge day. Source in `run.args.date_source`. |
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
| `prs` | array | one entry per discovered PR; for `--pr` runs **only the requested PRs** (since 2026-10-11; older `--pr` reports also listed the day's other PRs as `not-tested` / `excluded-by-pr-filter`, e.g. the 10-11 PR-905/906/907 originals kept as `.v1.json`) |
| `issues` | object | issue creation result (off by default) |
| `merged_reports` | array | `[{ pr, file }]` merged PR reports this run wrote (empty for `--pr` runs) |
| `open_reports` | array | only with `--write-open`: `[{ pr, file }]` open PR reports this run wrote |
| `system_health` | object | see below |

### `run`

**`trigger`** (`event` = on-merge trigger, `catch-up` = the 2-hourly catch-up timer or `--catch-up`, `manual` = run by
hand; default `manual`; reports before 2026-10-11 have no `trigger`), `id` (uuid), `started_at`, `finished_at`, `started_at_shanghai`, `finished_at_shanghai`, `duration_s`,
`args` (`date` (the `--date` argument or `null`), `date_source` (how `date` was chosen: `--date`, `pr merged_at (Asia/Shanghai)`, `pr updated_at (Asia/Shanghai)`, `yesterday (Asia/Shanghai)`), `prs`, `out`, `reuse_merged`, `create_issues`, `limit`, `pr_timeout_min`, `deadline`, `burst_cutoff`, `step_cache`, `write_merged`, `write_open`),
`ai_tests`, `ai_test_caps` (effective caps; since 2026-10-12 the tiered caps of the PR, or of the highest-tier PR in a multi-PR run), **`ai_test_tier`** (see *AI-test tiers*; `null` when no PR was tested), **`quota_pool`** (`production` | `test`, see *Model-call quotas*), **`quota_caps`** (`{ ai-test, jev, ai-review, pr-summary }` daily caps of that pool), `fresh_review` (see *Code review reuse*)),
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
| `risk_class` (`A` docs-only / `B` / `C` high-risk), `risk_reason`, `change_kind` | `change_kind: "agent-docs"` (only present then) = an `A` diff that touches agent instruction docs (`AGENTS.md` / `CLAUDE.md`): code review only, no checks. Test files, `ghost.json` and agent docs inside `cPaths` do not make a PR `C`; on `C`, optional steps are forced only when a high-risk file is within the step's `exercises` |
| `severity` | `P0`…`P3`, or **`"untested"`** for `verdict: not-tested`. Never `null`. An untested PR is never shown as P3. |
| **`tested`** | `true` when checks actually ran for this PR; `false` for `not-tested`, `qa-error`, `docs-only`, `skipped` |
| **`dimensions_run`** | `{ "white-box": [check ids], "gray-box": [...], "black-box": [...] }` — checks that really executed (status pass or fail) |
| **`dimensions_missing`** | dimensions with no executed check; non-empty ⇒ verdict can never be `clean` |
| `dimensions_not_applicable` | (since 2026-10-20) dimensions with no check at all whose steps were **all** skipped by triage (`skipped_steps[].reason` starting `triage:`), e.g. `black-box` for a tests-only PR (#1101). They are **not** in `dimensions_missing` and do not make the PR `partially-verified`. A step that was planned but did not run (`not run: …`) or an `unavailable` check keeps the dimension in `dimensions_missing` |
| `verdict` | see table below |
| `outcome`, `outcome_reason` | engine outcome (`passed`, `defect`, `flaky`, `infra_failed`, `docs_only`, …) |
| `not_tested_code`, `not_tested_reason` | only for `verdict: not-tested` |
| `partial_result` | only for `not-tested` because of a time budget: `{ verdict, checks, evidence_dir }` of the interrupted run; not used for the verdict |
| `error` | only for `qa-error` (redacted message); the entry also has one finding `{ source: "qa-error", test_backed: false, severity: "P2" }` |
| `run_mode` | `{ coverage_parallel, reason }`: whether the coverage round ran in parallel with the first run (memory guard: `MemAvailable` ≥ `ARGUS_PARALLEL_MIN_FREE_GB`, default 8, and `ARGUS_MAX_PARALLEL` ≠ 1) |
| `checks[]` | `{ id, name, kind, dimension, qa_dimensions, status: pass/fail/timeout/unavailable, exit_code, signal, ended_at, duration_ms, failure_count, unavailable_reason?, sub_checks?, shards?, reused?, reused_from? }` (`shards` / `reused` / `reused_from`: see *Burst capacity*); `sub_checks` only for probe steps (`api-probe`): `{ total, passed, failed, skipped, auth: { account, account_via, host_exchange } }` — `status: pass` means every probe that **ran** passed; skipped probes are listed in `skipped_steps`, never counted as passed |
| `failed_checks[]` | `{ check_id, check_name, dimension, qa_dimensions, test, failure_type, status, severity, attribution, fingerprint, pr_fingerprint, evidence{ tested_sha, base_sha, exit_code, signal, ended_at, timed_out, first_run, rerun, base_run, latest_main_run?, command, log_tail, log_file } }`; `status` ∈ `introduced, pre-existing, flaky, fixed-later, unverified, timeout, setup-failed`; boolean **`pre_existing`** (`true` when `status` is `pre-existing` **or** `evidence.base_run` is `"fail"` — 日报 bot reads this to label 老问题 / 合并前就有, e.g. recurring `shadowRoundTrip` bench timeout); also boolean **`flaky`** (`true` iff `status: "flaky"`), `related_to_pr: false` and **`base_comparison`** (see *Flaky*) for flaky entries, `evidence.base_log_file?` when base ran, `not_a_pr_finding: true` + `env_suspected: "trial-timeout"` (severity P3, **not copied into `findings[]`**, noted in `system_health.env_problems`) when a `test_timeout` case of a trial-only check (bench) got rerun result exactly `pass` after the machine went idle (1-min loadavg ≤ 0.5 × cores and MemAvailable ≥ 3 GB, waited at most 10 min; `rerun_idle_wait` on the PR); a whole-step timeout, `status: introduced`, a rerun that is not exactly `pass` (incl. `not-failing`), or a wait that hit 10 min with the machine still busy (`rerun_idle_wait.idle: false`) keeps its severity; if the time budget runs out during the wait, no rerun happens; trial checks carry `evidence.machine_load { first_run, rerun_wait, rerun }` (`loadavg`, `cores`, `mem_available_gb`), and, in reports written before 2026-10-11 only, `reclassified { at, from, reason }` (legacy in-place re-classification; no longer done — see *Report preservation*) |
| `code_review` | `{ status, summary, gate, model, tokens, findings_count, residual_risks, file, reused, reused_from, fresh_requested, fresh_comparison, sampling, cache_key, verdict }` (see *Code review reuse*) or `{ status, error }`, or **`{ status: "capped", reason, display_zh: "额度用尽未补测" }`** when the day's ai-review quota was used up (no review happened; see *Model-call quotas*). **Also present for docs-only PRs** (reviewed on the same commit: merged → `base_sha` (first parent = merge-base) `..merge_sha`); `null` only with `--no-review` |
| `findings[]` | everything that can raise severity. Every finding has **`source`** ∈ `check` (a failed check, same fields as `failed_checks`), `code-review` (LLM; capped at P1, original in `llm_severity`), `coverage-gate` (P2), `qa-error` (P2, the pipeline crashed), `own-tests` (P2, `check_id: "own-tests-not-run"`, `test_backed: false`: a test file the PR changed appears in no check's test report, `evidence.files`; not raised when a report that could contain it is incomplete), `ai-test` (P2, an AI-generated test that fails on the tested commit; `test_backed: false`, `status: "unconfirmed"`, `reproduced`) and boolean **`test_backed`** (see *test_backed*) |
| `not_a_pr_finding` | Emitted since 2026-10-20 (branch `qa/bench-after-unit-env-timeouts` merged) as `failed_checks[].not_a_pr_finding: true` + `env_suspected: "trial-timeout"` for trial-only (bench) test timeouts that passed an idle-machine rerun; such entries are left out of `findings[]` (see `failed_checks[]`). Readers should treat a missing field as `false`. |
| **`test_backed`** | PR-level: `true` if at least one finding is test-backed |
| `test_backed_severity` | highest severity among test-backed findings, or `null` |
| `finding_sources` | distinct `source` values of this PR's findings |
| `coverage` | QA-dimension coverage of the matrix — *not* code coverage. See *Reading `coverage` and `dimension_status`* below. **`dimension_status`** `{ <key>: { applicability, status: covered \| covered-by-ai-test \| gap \| capped \| not-applicable \| undetermined, checks: [step ids], ai_tests: [ai-test ids], display_zh?, capped_by? } }` for all 29 dimensions, and every total derived from it: `applicable`, `covered`, `missed` (= `gaps`), `undetermined`, `undeterminedMissed`, `coverage`, **`applicable_total`**, **`covered_total`**, `covered_dimensions`, `ai_covered`, `not_applicable_total`, `undetermined_total`, **`capped`**, **`capped_total`**; `passRate` = share of checks that ran and passed; `basis` names the source |
| `coverage.dimension_status.<key>.pr_tests` | (since 2026-10-20, only when non-empty) test files **the PR itself** added or modified that ran (pass / fail) and exercise this dimension (keywords in their added lines; `unit` / `regression` always). Such a dimension is `status: covered` and is not an AI-test gap (#1099) |
| **`ai_tests`** | AI-generated tests for this PR, see *AI tests* |
| **`coverage_gate`** | white-box code-coverage gate, see below; **docs-only: `{ status: "not-applicable", reason: "docs-only change" }`**; `null` when no unit run happened for another reason (not-tested, qa-error) |
| **`skipped_steps`** | **every** profile step that did not run — **each step id at most once** (the first, most specific reason wins: not applicable / triage / `--skip-step` before `not run: …`; reports before 2026-10-11 could list e.g. `web-build` twice), each `{ id, nameZh, reason, reasonZh?, dimension }`; reasons: `triage: …` (optional step not relevant to the change), `skipped by --skip-step`, `not run: dependency install failed`, `not run: run stopped before this step …`, `docs-only change: no checks run`, `not applicable to this revision: …` (the profile step's `appliesIf` file is absent, e.g. `web-build` when the revision has no web entry `index.html`, or a dist scan whose script the revision does not have yet — neither a pass nor an unavailable check). **Sub-probes of a probe step that did not run** are listed too, as `{ id: "api-probe/<probe id>", nameZh, reason, parent_step: "api-probe", dimension }` — e.g. reason `no test account` for signed-in probes when neither local sign-in nor `MIVO_QA_TEST_*` is available |
| **`triage`** | dimension applicability for this PR, see *Triage* |
| `ai_summary` | `{ summary, risks, suspect, failure_explanation }`, or `{ status: "capped", reason }` (pr-summary quota used up) |
| **`quota_capped`** | `[{ pr, kind, pool, cap, used, day, dimensions, message }]` — what this PR did not get because a daily model-call quota was used up (`kind` ∈ `jev`, `ai-test`, `ai-review`, `pr-summary`; `dimensions` = the QA dimensions marked `capped`). Empty when nothing was capped; absent in reports before 2026-10-11 |
| `evidence_dir`, `duration_s` | |
| `reused`, `reused_from`, `reused_report_generated_at`, `reused_report_system_health` | only for reused on-merge results |
| **`handoff`** | Mivo PR handoff (`docs/pr/<N>/review.md`, `qa.md`), see *PR handoff*. Absent in reports before 2026-10-20 and in reused results produced before that |

### `verdict`

| Verdict | Severity | Meaning |
|---|---|---|
| `clean` | P3 | nothing found, every planned check ran and passed, **and all three dimensions (white / gray / black) actually ran** |
| `review-suggested` | P2 | P2 findings (quality guard, unverified failure, review P2, coverage below gate) |
| `needs-attention` | P0/P1 | at least one P0/P1 finding |
| `partially-verified` | P3 | nothing found, but some checks were unavailable / timed out **or a dimension did not run** (`dimensions_missing`), **or the code review was skipped because the ai-review quota was used up** (`code_review.status: "capped"`) |
| `qa-env-problem` | per findings | the environment failed (e.g. install failed twice) |
| `docs-only` | P3 (or the highest code-review finding) | only docs changed; no checks run, `tested: false`, `tested_sha: null`; the ming-qa code review still runs on the merge commit / head (`code_review`, `reviewed_sha`), `coverage_gate.status: "not-applicable"`. Review findings are `test_backed: false`. |
| `skipped` | P3 | closed without merge |
| `not-tested` | **`untested`** | discovered but not tested in this run (`tested: false`, `tested_at: null`); `not_tested_code` ∈ `excluded-by-pr-filter` (deliberate `--pr`; only in reports written before 2026-10-11 — current `--pr` reports leave other PRs out of `prs[]` and list them in `system_health.discovery.excluded_by_pr_filter`), `over-limit`, `pr-timeout`, `deadline`, `burst-cutoff` (see *Burst capacity*). Time-budget cases are also in `system_health.qa_tool_failures`. Look for this PR's own `merged/PR-N.json` before concluding it was never tested. |
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

**Base comparison (since 2026-10-11).** Every step that failed on the first run — including the ones that then passed on
the rerun — is also run once on the base (pre-merge / merge-base) revision in the same `b` round. Each flaky entry
carries **`base_comparison`** `{ compared, base_sha, base_run, result, note, note_zh }`:

| `result` | meaning |
|---|---|
| `also-fails-on-base` | the same test also failed on base in one run: the instability predates the PR |
| `passes-on-base` | passed on base in one run; a single run cannot rule out the same instability on base |
| `not-compared` | no base result (base unknown, base run missing / timed out / unavailable); `note` starts with **`未与合并前版本对比`** |

The note is appended to `attribution`; `evidence.base_run` holds the raw result and `evidence.base_log_file` the base
log. Status stays `flaky`, severity stays P2, `test_backed` stays false. Reports must say **未与合并前版本对比**
whenever `base_comparison.compared` is false or the field is absent (reports written before 2026-10-11). Normalized
pre-feature entries have `base_comparison.reason: "report predates feature"` and `note_zh`
**未与合并前版本对比（该报告早于此功能生成）** — use that text.

### `test_backed` — what notification / issue rules key off

`findings[].test_backed` is `true` **only** when the finding is backed by a failing check that actually ran
(`source: "check"`) **and was not `flaky`**. Flaky checks (failed, then passed on the rerun), review-only
(`code-review`), `coverage-gate`, `qa-error` and `ai-test` findings are always `false`.

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
  `unit_run_under_coverage` `{ exit_code, signal, ended_at, timed_out, failed_tests, note }` records how the unit suite did under
  instrumentation — informational only, never part of the verdict. Global metrics cover the files the unit tests
  load (vitest v8 default scope).
  Since 2026-10-20 the coverage run passes `--testTimeout` / `--hookTimeout` (30000 ms, env `ARGUS_COVERAGE_TEST_TIMEOUT_MS`) to vitest,
  so slow-under-instrumentation tests no longer hit the 5 s default; and `unit_run_under_coverage` also carries
  **`new_code_maybe_low`** (bool) and **`failed_tests_importing_changes`** `[{ test_file, imports: [changed files] }]`: a test
  file that failed in the coverage run imports (relative `import` / `import()` / `require`) a non-test file the PR changed, so
  `new_code` for those files may read lower than it really is. Informational only, never part of the verdict.
- **`signal` / `ended_at`** (since 2026-10-21, panoptes #11) appear on `checks[]`, `checks[].shards.per_shard[]`,
  `failed_checks[].evidence` and `coverage_gate.unit_run_under_coverage` (and its `shards.per_shard[]`). `signal` is the
  name of the signal that terminated the step's direct child (e.g. `"SIGTERM"`), `null` when it exited normally — a
  wrapper (bwrap / npm) that turns a signal into exit code 143 still gives `signal: null`, so read `exit_code` too.
  `ended_at` is the ISO-8601 UTC time the child closed (for a sharded step: the latest shard), `null` for steps that
  never ran (not-applicable, unavailable, budget exhausted) and in reports written before 2026-10-21. `timed_out: true`
  means argus' own step timeout sent the signal; `exit_code` 143 / `signal: "SIGTERM"` with `timed_out: false` means
  something outside argus killed it. Use `ended_at` to line up kills across parallel runs (`h1` vs `cov` shards).
- The four metrics and thresholds are ming-qa's G2 *Unit Tests And Coverage* gate
  (`ming-qa/skills/quality-assurance-agent/references/quality-gates.md`, config `qualityGates.coverage`:
  `lines 80 · branches 70 · functions 80 · newCode 90`); `thresholds_source` names it.
- `new_code` = the 4th metric, ming-qa **newCode** — G2 lists it as "New-code coverage: 90" (config
  `qualityGates.coverage.newCode: 90`) and gives no formula. panoptes measures it as line coverage restricted to the
  PR's new code (the same line definition as `lines`): lines added/modified by `git diff base_sha..tested_sha` in
  non-test JS/TS source files; a line is executable if it starts a v8 statement (istanbul's line metric uses the same
  rule) and covered if that statement executed. Files vitest did not load are filled from raw `NODE_V8_COVERAGE` data
  collected in the same coverage round (steps with `coverage.kind: "v8"` such as `node-test` / `logging`, and child
  processes of the unit run that inherit the env; copies run from a temp dir are mapped back by unique path suffix):
  code-like lines, covered when the line's last non-blank character lies in a V8 block with count > 0; such
  `files[]` rows carry `source: "v8"`. Files with no data at all count their code-like added lines as uncovered
  (`unloaded_files`), except plain `.js/.mjs/.cjs` (node entry scripts, host entry such as `cindyplugin/main.js`):
  those are listed in `not_measurable_files` (row `not_measurable: true`, `covered: null`) and left out of `pct`.
  Tests that spawn children with a hand-built env (only PATH/HOME) cannot pass `NODE_V8_COVERAGE` on. `null` / `not-evaluated` when the diff has no executable lines. Threshold 90 (pass at ≥ 90).
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
- `source: "tests-only"` (since 2026-10-20): every changed file is a test file (`*.test.*`, `*.spec.*`, `*.node-test.*`,
  `__tests__/`, `test_*.py`, `*_test.py`) → `unit` and `regression` are `applicable` (high) whatever Jev says (#1101).
- Jev bands: `p ≥ 0.7` applicable (high) · `0.5 ≤ p < 0.7` applicable (low, conservative) ·
  `0.3 < p < 0.5` not-applicable (low) · `p ≤ 0.3` not-applicable (high). With `status: "ok"` nothing is `undetermined`.
- `status`: `ok` · `not-configured` (no key) · `failed` (Jev error; `reason` says why) · `not-run` (docs-only PR).
  Only for `not-configured` / `failed` are dimensions left `undetermined`; then `system_health.notes` gets
  `维度适用性未判定（Jev 未配置）` (no key) or `维度适用性未判定（Jev 分诊失败）：#N …` (failure), and failures are also in
  `system_health.llm_errors` (`stage: "triage(jev)"`) and `system_health.triage.failed`.
- Which optional checks run still follows the conservative step plan (anything not clearly unrelated runs).

### PR handoff (`prs[].handoff`, since 2026-10-20)

Read from the tested commit (merged → `merge_sha`, open → `head_sha`) via the contents API; only front matter
`schema: mivo-pr-handoff/v1` is accepted. Nothing in the target repo is executed. The handoff is the author's lead,
not a result: `author_status` is recorded only and feeds no verdict.

`{ present, reason, cases: [{ id, key, level, covers[], author_status, qa_status, confirmed, reason, evidence }], manual: [{ id, level, why }] }`

- `present: false` → `reason` says why (no files, no / other schema, read failed); `cases` and `manual` are `[]`;
  every other field of the PR is what it would be without the feature. A path that is not a regular file
  (symlink, directory) is ignored and logged in `system_health.env_problems`.
- `key` = `handoff:<PR>:<id>` — stable dedup key (use it, not `fingerprint`). `id` is a `Q…` case from `qa.md` §3,
  or an item of `qa.md` §5 "留给独立 QA" (`level: null`).
- `qa_status`:
  - `verified` — an AI test for this case passed on the tested commit and failed with the injected bug
    (`evidence.check_id` = the `ai-test-N` check).
  - `failed` — **a lead only, not test-backed**: the AI test fails on the tested commit. `confirmed: false` until the
    matching finding is marked with `verify-lead` (same `check_id`), which sets `confirmed: true`. Do not report it
    as a confirmed bug while `confirmed` is false. `confirmed` is `null` for every other status.
  - `not-run` — `reason` ∈ `quota-exhausted` (daily ai-test quota), `no-reliable-test` (model wrote no test that
    passed validation), `ai-tests-<status>` (AI tests skipped/disabled for the PR; `evidence.detail` has the cause),
    `ai-tests-disabled` (`--no-ai-tests`), `docs-only` (A class), `unsupported-level <level>` / `no-level`
    (levels other than below).
  - `manual` — level `cindy-mac` / `cindy-windows` / `paid`; not run, also listed in `manual[]`.
- Levels `unit` / `contract` / `browser-fixture` and §5 items go to AI tests ahead of the coverage gaps, inside the
  existing per-PR tier and the daily `ai-test` quota (no extra calls or tests). The author's 反证 is preferred as the
  injected bug.
- `docs/pr/**` takes no part in risk class, code review, coverage or the tested-file lists; a PR that only adds a
  handoff is `risk_class: "A"` (docs-only) with `code_review: { status: "skipped", reason }`. `changed_files` still
  counts them.

### AI tests (`prs[].ai_tests`)

For B/C PRs with a decided triage, panoptes asks the model (argus `src/aitests.mjs`, `openai/gpt-6-luna`) for vitest
cases for the **gap dimensions**: Jev says applicable, the dimension is in the AI-test set (`integration, boundary,
exception, security, permission-boundary, state-transition, async-callback-retry, data-consistency, concurrency,
business-rule, privacy, db`), and no check that ran declares it in `covers`. High-risk (class-C path) files are listed
first. The **primary target** is the PR's uncovered changed lines from `coverage_gate.new_code.files[].uncovered_lines`
(top 10 files, `ai_tests.uncovered_targets: [{ file, lines }]`); the model runs even with no gap dimension when such lines
exist, and a case with no gap dimension is accepted when its injected bug is in one of those files (`tests[].targets_uncovered`). Each case comes with an injected bug (a one-occurrence text replacement in a source file) and runs in its own
sandboxed run (`ai1`, `ai2`, …, never part of the verdict runs). **Mutation discipline:** a case is **kept** only if it
passes on the tested commit **and** fails with the injected bug; otherwise it is **discarded**. A case that fails on
the tested commit is re-run once (`reproduced`) and becomes a `source: "ai-test"` finding: always P2,
`test_backed: false`; it is only upgraded by a person after the failure is reproduced and confirmed. Generated files
are deleted after the run; nothing is written to the target repo.

**Invalid tests — 测试无效 (since 2026-10-13).** A case that fails on the tested commit **before any assertion** because
the test's own precondition is missing is not evidence about the code: a missing fixture / data file (`ENOENT`,
`no such file or directory`) or a missing module / export (`Cannot find module`, `Failed to resolve import`,
`Failed to load url`, `does not provide an export named`, vitest `No "x" export is defined on the "…" mock` when the
test's own `vi.mock` factory lacks an export the code under test uses (since 2026-10-16), and — since 2026-10-17 —
`TypeError: x is not a function` whose first stack frame is in the generated test file itself and where `x` is a name the
test imports with `import { x } from '…'`, i.e. the test imported a name that module does not export, as on #1008
10-16; the same TypeError thrown inside the code under test is **not** matched). Such a case gets `result: "discarded"` (so it is never a
finding and never counts as coverage) plus **`invalid: true`**, **`invalid_kind`** (`missing-file` | `missing-module` | `fixture-gap`),
**`display_zh: "测试无效"`** and a `reason` starting with `test invalid (测试无效)` that quotes the matched line. If the
output contains an assertion failure (`AssertionError`, `expected … to …`) the case stays `fails-on-current` even when the
message mentions `ENOENT` (e.g. `toThrow('X')` received an ENOENT) — that may be a real defect and is left for a person.
Exception, since 2026-10-19 (`fixture-gap`, #1013 10-18): `Cannot read properties of undefined (reading 'P')` counts as
测试无效 even inside an assertion failure, but only when the source of `mutation.file` at the tested commit has
`function NAME(X…)` reading `X.K.P`, the test imports `NAME`, `K` never appears in the test, and the source never assigns `K`
— i.e. the undefined can only come from the object the test passed in. Anything else stays `fails-on-current`.
`ai_tests.invalid` counts these cases; they are **included in** `ai_tests.discarded` (so `kept + discarded +
fails_on_current = generated` still holds). The matching `checks[]` entry keeps `status: "discarded"` and adds
`ai_invalid: true`, `display_zh: "测试无效"`. The reason is also fed back to the next generation round. Reports written
before 2026-10-13 have no `invalid` fields: there such a case is a `fails-on-current` P2 finding (e.g. #904
`ai-test-4`, ENOENT on `src/lifeline.json`).

**Verified mark on AI-test leads (since 2026-10-13).** Every `source: "ai-test"` entry in `prs[].findings[]` (the
lead the daily report would surface) carries **`verified`** (boolean, default `false`), **`verified_at`** (ISO time or
`null`), **`verified_by`** (`null` or who checked, e.g. `daily-review`) and **`verified_note`** (`null` or free text).
Only `verified === true` counts as verified; missing / `null` / `false` (including all reports written before this
field existed) mean **not verified**. The daily report (sent 09:04 Asia/Shanghai) includes only leads with
`verified: true`; the daily review (10:57) verifies leads afterwards, and a lead verified after that day's report goes
into the **next** day's report, naming the PR. Set it without touching any other field with
`node scripts/verify-ai-lead.mjs --pr <N> --fingerprint <fingerprint|pr_fingerprint>` (or `--check-id ai-test-K`;
options `--by`, `--note`, `--unset`, `--dry-run`, `--file`; default file
`$MIVO_QA_REPORTS/merged/PR-<N>.json`, `MIVO_QA_REPORTS` defaults to `/workspace/mivo-qa/reports`). Exit 1 = no AI-test
lead matched. `verified` is independent of `status` (`unconfirmed`) and `reproduced`.

```json
{
  "status": "ok", "reason": null, "model": "openai/gpt-6-luna",
  "caps": { "max_model_calls": 2, "max_wall_min": 25, "max_tests_per_call": 3 },
  "model_calls": 2, "wall_s": 610, "gaps": ["boundary", "exception"],
  "generated": 5, "kept": 2, "discarded": 3, "invalid": 1, "fails_on_current": 0, "newly_covered": ["boundary"],
  "tests": [{ "id": "ai-test-1", "round": 1, "kind": "coverage", "purpose": "…", "covers": ["boundary"], "file": "src/x.argus.test.ts",
              "mutation_file": "src/x.ts", "result": "kept | discarded | fails-on-current", "reason": "…", "exit_code": 0, "duration_ms": 41000, "log_tail": "…", "reproduced": null,
              "invalid": "true (only on 测试无效 cases)", "invalid_kind": "missing-file | missing-module | fixture-gap", "display_zh": "测试无效" }],
  "rounds": [{ "round": 1, "gaps": ["boundary", "exception"], "status": "ok", "offered": 3, "rejected_by_validation": 0, "tokens": 23000, "tests": ["ai-test-1", "…"] }]
}
```

### Reading `coverage` and `dimension_status`

`prs[].triage.dimensions` is Jev's decision per dimension; `coverage.dimension_status` adds what actually covered
each one. **Every `coverage` total is counted from `dimension_status`** (since 2026-10-11, commit after `dc0b998`), so the
totals cannot contradict Jev:

| field | = keys of `dimension_status` with |
|---|---|
| `applicable` / `applicable_total` | `applicability: "applicable"` |
| `covered` / `covered_dimensions` / `covered_total` | applicable **and** `status` `covered` (a check that ran declares it in `covers` and — when the profile step has `exercises` globs — the diff touches one of them) or `covered-by-ai-test` (a **kept** AI test covers it) |
| `ai_covered` | applicable and `status: "covered-by-ai-test"` |
| `missed` = `gaps` | applicable and `status: "gap"` (nothing covered it; **capped dimensions are not gaps**) |
| `capped` / `capped_total` | `status: "capped"` — **额度用尽未补测**: an applicable gap the AI tests did not attempt because the ai-test quota was used up (`capped_by: "ai-test"`), or a dimension left undetermined because Jev was not called (quota; `capped_by: "jev"`). Display text `display_zh: "额度用尽未补测"`. |
| `undetermined` / `undetermined_total` | `applicability: "undetermined"` (only when Jev was not configured / failed) |
| `coverage` | `covered_total / applicable_total` (`null` when nothing is applicable) |

Report wording: "**N applicable, M covered**" = `applicable_total`, `covered_total`; name the `gaps` if any.
Reports written **before** this change (`basis: "triage (prs[].triage)"`) computed `applicable` / `covered` / `missed`
/ `undetermined` with an older p ≥ 0.7 rule and can disagree with `applicable_total` / `covered_total` (e.g. #906:
`applicable_total: 4`, `covered_total: 4`, but `missed: ["exception"]` and four `undetermined`); for those, trust
`dimension_status` and the `*_total` fields only. Reports from 2026-10-10 (written before `ai_tests` existed) have
**no** `ai_tests`, `dimension_status`, `applicable_total` or `covered_total` at all (see *Older reports*).

- `status`: `ok` · `empty` (model wrote nothing usable) · `error` (gateway error, also in `system_health.llm_errors`
  stage `ai-tests`; since 2026-10-15 also an empty or truncated model reply, reason `AI 补测失败（模型返回空内容，finish_reason=…）`
  / `（模型返回的 JSON 不完整…）` instead of the old bare `Unexpected end of JSON input` — such a round is retried when the
  PR still has model calls left, and is kept in `system_health.llm_errors` only if no later round produced tests;
  `rounds[]` always keeps the failed round) · `skipped` (no gap, triage not decided, no `aiTests` profile config, install failed, …) ·
  `disabled` (`--no-ai-tests` / `ARGUS_AITESTS=0`) · **`capped`** (the day's ai-test quota was used up before the first
  model call: nothing generated, every gap dimension gets `dimension_status` `capped`, display **额度用尽未补测**). When the
  quota runs out after round 1, `status` reflects round 1 and **`capped: true`** + **`capped_gaps`** list the dimensions
  left (also `capped` in `dimension_status`); `rounds[]` has an entry with `status: "capped"`.
- Caps per PR: **tiered by PR type and size since 2026-10-12** (see *AI-test tiers*; before that flat 2 calls x 3 tests,
  25 min). Explicit env `ARGUS_AITEST_MAX_CALLS` / `ARGUS_AITEST_MAX_MIN` / `ARGUS_AITEST_MAX_TESTS` override the tier
  value (noted in `ai_test_tier.reason`). `ai_tests.caps` holds the effective caps of the PR. The daily cap is the `ai-test` quota (see
  *Model-call quotas*; `ARGUS_AITEST_DAILY_CAP` is still read as the production ai-test cap). Round 2 only targets dimensions round 1 did not cover and gets round 1's discard reasons.
  Actuals: `model_calls`, `wall_s`. `run.args.ai_test_caps` records the effective caps of the run.

### AI-test tiers (since 2026-10-12)

Per PR, `prs[].ai_test_tier` = `{ tier, reason, product_lines, files, product_files, type, quota_remaining, degraded,
degraded_from_calls? }` and `prs[].ai_test_caps` = `{ enabled, maxCalls, maxTestsPerCall, maxWallMin }` (effective).
`run.args.ai_test_tier` = the highest-tier PR's object plus `pr` and `per_pr: [{ pr, tier, type, product_lines, files, degraded, caps }]`.
Absent on reports older than 2026-10-12 and on reused (on-merge) results that predate it.

`type` comes from the changed files: lockfiles and generated files (`dist/`, `build/`, `*.min.js`, `*.map`, `*.snap`,
`*.generated.*` …) are never product code. `product_lines` = additions + deletions of product files only.

| type | when | tier | maxCalls x maxTestsPerCall | maxWallMin |
|---|---|---|---|---|
| `docs` | only docs / changelog / config (or `risk_class` A) | `none` | 0 (no AI tests, `ai_tests.status: skipped`) | 0 |
| `ci` | only CI/workflow (`.github/` …) | `S` | 1 x 2 | 10 |
| `tests` | only test files | `S` | 1 x 2 | 10 |
| `deps` | lockfiles / dependency manifests / mix without product source | `S` | 1 x 2 | 10 |
| `product` | `product_lines` < 50 | `S` | 1 x 2 | 10 |
| `product` | 50–299 | `M` | 2 x 3 | 20 |
| `product` | 300–1000 | `L` | 3 x 3 | 30 |
| `product` | > 1000 lines or > 30 product files | `XL` | 4 x 4 | 40 |
| `unknown` | file list unavailable | `M` | 2 x 3 | 20 |

The daily `ai-test` quota (50) stays the global ceiling: if the remaining quota is below the tier's `maxCalls`, `maxCalls`
is lowered to what is left, `degraded: true`, `degraded_from_calls` = the tier value and `reason` says so; with 0 left
`ai_tests.status` is `capped` (额度用尽未补测).
- Each test also appears in `checks[]` with `source: "ai-test"`, `dimension: "white-box"`, `status` `pass` (kept) /
  `fail` (fails on current) / `discarded`, `ai_result`, `ai_reason`; 测试无效 cases add `ai_invalid: true`, `display_zh: "测试无效"`.

## Code review reuse (reproducible review, since 2026-10-11)

The LLM review is not deterministic across calls (e.g. #893 got P3, P2, P3 in three runs on the same commit). So a
review verdict is **cached** and re-runs reuse it:

- Key = sha256 of `reviewed_sha`, `review_base_sha`, **`diff_hash`** (hash of the diff text sent to the model),
  **`prompt_hash`** (system prompt + ming-qa review focus + `prompt_version` `panoptes-code-review/2` + reasoning effort
  + sampling) and **`model`**. Cache: `$ARGUS_ROOT/cache/review-verdicts/<key>.json`; only successful reviews are stored.
- A hit → no model call (and no `ai-review` quota): `code_review.reused: true`, **`reused_from`**
  `{ key, stored_at, pr, run_id, reviewed_sha, reviewed_at, review_file, original_tokens }` (the run that produced the
  verdict), `tokens: 0`. The findings, severities and fingerprints are those of the original review. ming-qa's
  `assert-code-review` gate still runs on the reused document.
- Miss → fresh review, stored for next time: `reused: false`, `reused_from: null`.
- **`--fresh-review`** (or `ARGUS_REVIEW_CACHE=0`) skips the lookup and reviews afresh (`fresh_requested: true`,
  `run.args.fresh_review: true`). With no cached entry the result is stored as usual. **When a cached entry exists the
  fresh review is a comparison only: the cache is never overwritten, and the cached verdict stays authoritative.**
  `code_review.fresh_comparison = { cached_key, cached_stored_at, cached_sampling, fresh_sampling, cached_verdict,
  fresh_verdict, same, authoritative: "cached", fresh_file, fresh_model, fresh_tokens, cache_overwritten: false }`.
  If `same` is false the report's `code_review` / findings / `verdict` come from the cached review (`reused: true`,
  `reused_from` set) and the fresh doc is kept as `code-review.fresh.json` in the evidence directory; if `same` is true
  the fresh review is used as is. The fresh call consumes ai-review quota either way.
- The gateway accepts `temperature: 0` + `seed` but does not guarantee determinism, so two fresh reviews of the same
  commit can differ; the cache (not the sampling parameters) is what makes re-runs reproducible.
- Requests use `temperature: 0` and a fixed `seed` (accepted by the gateway); `code_review.sampling` records them, or
  `"unsupported"` if the gateway rejected them and the request was repeated without.
- `code_review.cache_key` `{ key, reviewed_sha, review_base_sha, diff_hash, prompt_hash, prompt_version, model }`
  (hashes shortened to 16 hex) and `code_review.verdict` `{ status, max_severity, findings: [{ severity, verdict, file,
  title_hash }] }` — compare `verdict` to check that two reports carry the same review.
- Reports before 2026-10-11 have none of these fields (each run reviewed afresh).

## Model-call quotas (since 2026-10-11)

Daily (Asia/Shanghai day) caps on model calls, **one counter per kind**:

| kind | what | default cap / day | env (production pool) |
|---|---|---|---|
| `ai-test` | AI gap-test generation (`ai_tests`, one per round) | 50 (40 before 2026-10-12) | `ARGUS_QUOTA_AI_TEST` (legacy `ARGUS_AITEST_DAILY_CAP`) |
| `jev` | Jev dimension triage (one per tested code PR) | 150 | `ARGUS_QUOTA_JEV` |
| `ai-review` | ming-qa code review (one per PR; a reused cached verdict costs nothing) | 70 | `ARGUS_QUOTA_AI_REVIEW` |
| `pr-summary` | plain-language PR summary (`ai_summary`) | 40 | `ARGUS_QUOTA_PR_SUMMARY` (legacy `ARGUS_AI_DAILY_CAP`) |

**Two pools with separate counters.** `production` = `run.trigger` `event` / `catch-up`. `test` = `--trigger manual`
(the default for hand-started runs, drills, acceptance reruns) or `--quota-pool test`; test-pool runs **never consume
production quota**. `--quota-pool production|test` overrides the default. Test-pool caps: `ARGUS_QUOTA_TEST_<KIND>`
(same defaults). Both pools can also be set in `$ARGUS_ROOT/quota.json` (`ARGUS_QUOTA_FILE`):
`{ "production": { "ai-test": 50, … }, "test": { … } }`; env wins. The run records `run.args.quota_pool` / `quota_caps`.

Counters live in the sqlite `ai_usage` table under **`quota:<pool>:<kind>:<YYYY-MM-DD>`**. The pre-2026-10-11 keys
(`gen:<day>`, `jev:<day>`, bare `<day>`) are not read by the nightly flow (only by the Mac `control.mjs` flow, on its own
DB); `cli.mjs prune-quota-keys [--dry-run]` backs the DB up (`VACUUM INTO argus.sqlite.bak-<ts>`) and deletes them.

When a cap is hit the call is not made and the result says so — **`status: "capped"`**, display text
**额度用尽未补测**, distinct from a normal gap:

| kind | where it shows |
|---|---|
| `ai-test` | `ai_tests.status: "capped"` (or `capped: true` + `capped_gaps` after a partial run); those dimensions `dimension_status.status: "capped"` (`capped_by: "ai-test"`) |
| `jev` | `triage.status: "capped"` (reason names the pool and cap); every undetermined dimension `status: "capped"` (`capped_by: "jev"`); optional checks all run (as without Jev); AI tests `skipped` |
| `ai-review` | `code_review.status: "capped"` — no review, no review findings; verdict at best `partially-verified` (never `clean`); the 29 dimensions are unaffected (code review covers none of them) |
| `pr-summary` | `ai_summary: { status: "capped", reason }` |

Every case is also in `prs[].quota_capped[]` and **`system_health.quota`** `{ pool, day, caps, caps_source,
used_by_run, used_today, capped: [{ pr, kind, pool, cap, used, day, dimensions, message }] }` plus one
`system_health.notes` line `额度用尽未补测：#N <kind> …`. A capped run is `system_health.status: "degraded"`, but capping
is **not** a `qa_tool_failures` entry (no issue is opened for it).

## Issues: dedup and recurrence (`--create-issues` only)

Issue bodies carry `<!-- mivo-qa-fingerprint: <fp> -->`; dedup is by fingerprint only (open **and** closed
`qa-auto` issues).

**Fingerprints exclude the commit on purpose.** `fingerprint` (`mq1-…`) hashes repo, check / step, test name (or a
normalized failure signature) and, for code review, file / symbol / category / title hash — never the PR number, the
tested / merge / base SHA, durations, temp paths or line numbers. The same defect therefore gets the same fingerprint
on every PR and every night it shows up, which is what cross-PR de-duplication and recurrence (comment / reopen /
suppress) key off. The commit is **not lost**: every issue body and every evidence comment records `tested_sha`,
`base_sha` and `merge_sha` (also in the ```` ```json mivo-qa-finding ```` block), and `pr_fingerprint` (`<fp>-pr<N>`)
is available when a per-PR key is needed. Recurrence follows argus's own publisher (`argus/src/publish.mjs`):

| Existing issue with the same fingerprint | Action (`issues.results[].action`) |
|---|---|
| none | `created` |
| open | `commented` — a comment with the new evidence (date, PR, tested/base sha, status, runs, log tail), once per run × PR (`<!-- mivo-qa-observation: <run id>/pr<N> -->`) |
| closed as fixed (`state_reason` `completed` / null) | `reopened` — reopened plus the same evidence comment |
| closed as won't-fix (`state_reason: not_planned`, or a `wontfix` / `won't fix` label) | `suppressed` — no comment, no reopen, no new issue |

**system_health issues are aggregated:** one run opens at most **one** system_health issue, listing every
`qa_tool_failures` / `env_problems` / `llm_errors` entry that is still present after the run's self-repair (bootstrap
retry). Entries with `transient: true` or `self_repaired: true`, and items in `system_health.bootstrap.self_repaired`,
stay in the report but open no issue. The issue's fingerprint is fixed per repo (`system-health` / `aggregate-v1`), its
body has a ```` ```json mivo-qa-health ```` block (`schema: "mivo-qa-health/v2"`, `items[]` with each entry and its
own `entry_fingerprint`, `self_repaired[]`). If **any** system_health issue is open (including older per-entry ones),
the run comments its list there (once per run, `<!-- mivo-qa-observation: <run id>/system_health -->`) instead of
opening a new one; otherwise the table above applies to the aggregate fingerprint.

The ```` ```json mivo-qa-finding ```` block carries `merge_sha`. Code-review fingerprints include a 12-hex hash of the
normalized finding title, so different findings on the same file / symbol / category no longer collide.
(An intermediate design keyed issues on fingerprint + merge commit; it was never used to create issues and is
replaced by the rules above. `mini/check-queue.sh` still accepts an optional `dedup_key` in old blocks and prints
`MIVO_QA_DEDUP_KEY=`, which equals the fingerprint for all current issues.)

## Catch-up (`run-nightly.sh --catch-up`)

Lists PRs merged yesterday + today (Asia/Shanghai) with `gh pr list` (read-only), and treats a PR as a gap when
`reports/merged/PR-N.json` is missing / unreadable / has no final verdict, or its `tested_sha` (docs-only:
`merged_sha`) differs from the merge commit. Gaps are tested oldest-first via
`run-nightly.sh --pr N --date <merge day> --write-merged --wait-lock S --no-issues --trigger catch-up --out reports/catch-up/PR-N.json`
(`--create-issues` instead of `--no-issues` when the catch-up itself got `--create-issues` with `ARGUS_ALLOW_ISSUES=1`,
as the cron timer does). Timer: `/etc/cron.d/panoptes-catch-up`, every 2 hours, installed by `scripts/bootstrap-box.sh`.
A lock timeout (exit 75) stops the invocation; the next one retries. After 3 attempts a PR is given up:
`reports/catch-up/run-<ts>.json` has `system_health.gave_up[]` / `qa_tool_failures[]`
(`{ pr, stage: "catch-up", merge_sha, attempts, message }`), and the next nightly report lists it in
`system_health.qa_tool_failures` (`stage: "catch-up"`, for 48 h). With nothing to do it exits 0 silently and writes nothing.

## Burst capacity (many merges overnight)

Target: about 20 code PRs merged overnight, all tested before **08:30 Asia/Shanghai** (the daily report goes out at
09:04). Three mechanisms, all recorded in the report:

1. **Unit-test sharding.** The `unit` step (profile `shard: { max: 2, perShardGb }`) runs as N parallel
   `vitest --shard=i/N` processes (Mivo's own config is single-worker, so shards are separate processes) in the same
   sandboxed checkout, each with its own `TMPDIR`, JSON report and coverage directory. Afterwards the per-shard vitest
   JSON reports are merged into the usual `reports/unit.json` (counts summed, `testResults` concatenated) and the
   per-shard `coverage-final.json` files are merged by statement / function / branch location (hit counts added, so a
   source file loaded by several shards is counted once) and `coverage-summary.json` totals are recomputed (istanbul
   rules); `coverage_gate` and new-code coverage read the merged files exactly as before. **Memory guard:** one budget
   for all shard steps running at the same time (the verdict run `h1` and the coverage run `cov` may overlap):
   `min(ARGUS_SHARD_BUDGET_GB (3), MemAvailable − ARGUS_SHARD_RESERVE_GB (1.5)) − GB held by other shard steps`,
   N = `clamp(floor(budget / perShardGb), 1, min(max, CPU cores, ARGUS_SHARD_MAX (2)))`; `ARGUS_UNIT_SHARDS=N` forces N (1 = off),
   still capped by ARGUS_SHARD_MAX. Since 2026-10-14 (box froze from memory exhaustion: 15 GB, no swap, shared by
   several bots) the defaults keep one process at ≤ 3 shards (~3.6 GB) total across overlapping `h1` / `cov`; overlap between
   separate PR processes is bounded by run-nightly.sh's pre-run gate (wait while MemAvailable < 3 GiB, up to 30 min,
   then run anyway with `ARGUS_UNIT_SHARDS=1`; a PR is never skipped for memory). The `unit` step timeout is 120 min
   so a single-shard, memory-constrained run fits; a run that still dies or times out is reported not-tested.
   The existing coverage-parallel guard (`run_mode`, `ARGUS_PARALLEL_MIN_FREE_GB`) still decides whether `cov` overlaps `h1`.
   `checks[].shards` (and `coverage_gate.unit_run_under_coverage.shards`): `{ count, reason, mem_available_gb,
   budget_gb, per_shard_gb, per_shard: [{ shard, exit_code, signal, ended_at, timed_out, duration_ms }] }`; a single-process run has
   `{ count: 1, reason }`. Failure names, flaky rerun and base comparison work on the merged report as before; a missing
   shard report makes the merged report absent, i.e. `testOutcomeStatus: incomplete` (never guessed as passed).
2. **Step-result reuse.** Before a step runs in the first run (`h1`), the coverage run (`cov`) or the latest-main
   recheck (`l`), panoptes computes a key from the step definition, the substitution variables it uses (e.g.
   `DIFF_BASE_SHA` for `design`), the Node major, **the browser versions** (system Chromium `--version` of the panoptes
   launcher, the Playwright version in `argus/node_modules` and its bundled Chromium version / revision — since 2026-10-11;
   a browser upgrade invalidates every cached step), the panoptes commit for steps with bundled tools (api-probe, journeys),
   and the **git tree / blob hashes of the step's input paths plus `package-lock.json`** in the tested commit (profile
   `inputs`, e.g. `api-inproc` / `api-probe`: `server`, `shared`, `src`, `public`, configs; default: the whole tree).
   If a passing result with the same key exists (`$ARGUS_ROOT/cache/step-results/`, 14 days), it is reused instead of
   re-run: `checks[].reused: true`, **`checks[].reused_from`** `{ job: "n<PR>", label, sha, run_dir, stored_at, key,
   original_duration_ms, browser? }` (`browser` = `{ chromium, playwright, playwright_chromium }` the result was produced with), `duration_ms: 0`. Only passing, complete results are stored (coverage run: complete coverage
   data); failures always re-run and go through rerun / base / latest-main. Never reused: reruns (`h2`), base runs (`b`),
   AI tests, `bench` (`cache: false`). `--no-step-cache` (or `ARGUS_STEP_CACHE=0`) turns step reuse off (`--no-reuse` only concerns reusing whole `merged/PR-N.json` results);
   `run.args.step_cache` records it. Typical hits: an open PR tested at its head and later merged with the identical
   tree; a re-test after a burst cutoff; a PR that only touches `cindyplugin/` reusing `api-inproc` / `api-probe`.
3. **Cutoff.** `run-nightly.sh --burst-cutoff [HH:MM]` (default `08:30` Shanghai; env `ARGUS_BURST_CUTOFF`) works like
   `--deadline` but marks every PR still untested at the cutoff — not started, or stopped while running — as
   `verdict: "not-tested"`, **`not_tested_code: "burst-cutoff"`**, `not_tested_reason: "burst cutoff 08:30
   Asia/Shanghai reached …: not tested before the daily report; the catch-up timer (every 2 h) tests it later"`, also in
   `system_health.qa_tool_failures` (`stage: "burst-cutoff"`) and `run.args.burst_cutoff`. No `merged/PR-N.json` is
   written for such a PR, so the catch-up timer sees the gap and tests it later (steps that already passed before the
   cutoff are reused). The daily report should list these as "not tested yet (burst cutoff), follows later".

## `system_health`

| Field | Notes |
|---|---|
| `status` | `ok` · `degraded` (any qa_tool_failures / env_problems / llm_errors, or a model-call quota capped) · `failed` (discovery failed or any `qa-error`) |
| **`complete`** | `true` when discovery worked and every PR this run set out to test has a final verdict (no `qa-error`, no `pr-timeout` / `deadline`). `--pr` exclusions don't make it false. |
| `all_discovered_tested` | `true` when additionally every discovered PR was tested (no `not-tested` at all) |
| `qa_tool_failures[]` | `{ pr?, stage, message, dimension?, detail? }`; stages include `pipeline` (qa-error), `setup`, `<check id>` (unavailable / timeout), `pr-timeout`, `deadline`, `discovery`, `janitor`, `issues`, `write-merged-report`, `write-open-report`, `ming-qa assert-code-review`, `catch-up` (a gap given up after 3 attempts) |
| `env_problems[]` | `{ pr?, message }` |
| `llm_errors[]` | `{ pr, stage, model, … }` |
| `environment` | `{ host, platform, node, sandbox, bwrap, chromium, playwright, python, disk_free_gb }` |
| `bootstrap` | `{ ran, missing_before[], missing_after[], self_repaired[], log, checked_at }` from the wrapper's self-check (`scripts/bootstrap-box.sh`). `ran: true` = something was missing and the bootstrap ran (a note lists what it installed); `self_repaired` = item names in `missing_before` but not in `missing_after` (fixed within this run: recorded here only, never an issue). Before the report is written the wrapper's `--check` runs again: `recheck: { checked_at, missing[] }` and `resolved_later[]` (items in `missing_after` that were present by report time, e.g. installed by a manual bootstrap after an apt lock cleared; not counted as `self_repaired`); resolved items are dropped from `env_problems` / `qa_tool_failures` and a note records the re-check. Both fields are absent when `missing_after` was empty or the re-check itself failed (reports before 2026-10-13 never have them). A held apt/dpkg lock is waited for (up to `PANOPTES_APT_LOCK_WAIT`, default 600 s) instead of failing; every entry left in `missing_after` is also in `env_problems`, plus one `qa_tool_failures` entry with `stage: "bootstrap"`. A run that cannot start Node 24 writes a minimal report instead: `run.mode: "env-failed"`, `prs: []`, `system_health.status: "failed"` (to `--out`, or `<out>.env-failed-<ts>.json` if that file exists), exit 3. |
| `discovery` | `{ window_start, window_end, found: [{ number, reason }], excluded_by_pr_filter? }` — `excluded_by_pr_filter` (only `--pr` runs): PR numbers discovery found that day but the run did not report on |
| **`quota`** | model-call quota of this run, see *Model-call quotas* (absent before 2026-10-11) |
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

