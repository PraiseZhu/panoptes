# panoptes daily review log

One entry per daily retrospective (newest last). Each entry: data day (Asia/Shanghai), reconciliation, stability,
accuracy spot-check, what changed in panoptes, and what the 日报 bot has to know.

## 2026-10-13 review — data day 2026-10-12

**Reconcile.** GitHub: 11 PRs merged into `xindong/mivo-canvas-plugin` on 2026-10-12 (Shanghai): #911 #912 #913 #914
#918 #920 #924 #928 #931 #932 #936. Every one has `reports/merged/PR-N.json` (schema 1.0, `date` 2026-10-12,
`run.trigger: event`, production pool), no duplicates, no history versions, no catch-up needed. Merge → test start
15–82 s (#924 waited ~70 s for #918's lock; #931 82 s right after the box replacement). Field consistency checked
(verdict ↔ findings ↔ severity, `test_backed` ↔ issue action, `coverage` totals ↔ `dimension_status`, ai_tests counts,
`dimensions_missing`, reviewed_sha = tested_sha): no contradictions.
Verdicts: P1 #918 (code review only, not test-backed) · P2 #912 (new-code coverage 68.42 % < 90) · P2 #928 (bench
`shadowRoundTrip` flaky) · P3 clean #924 #931 #932 #936 · P3 partially-verified #911 #913 (tests-only PRs, black-box
steps skipped by triage) · docs-only #914 #920.

**Stability.** Code-PR runs 711–1038 s, docs-only 13–14 s; unit 242–342 s with 2–4 shards. MemAvailable 3.6–6.9 GB all
day (< 8 GB), so the coverage round never ran in parallel (expected on this 15 GB box with agent desktops). No
qa_errors, timeouts, unavailable checks, capped quotas (ai-test used 16/40 production by 22:28). Box replaced at ~18:31:
#931's run started with chromium/cron missing (apt lock held by another apt-get); bootstrap could not install them,
the run opened 8 per-entry system_health issues (#8–#15 in pr-qa-agent-deploy, 18:46) which were closed 18:48 after a
manual bootstrap fixed everything (`bootstrap-manual-20261012.log`). Fixed the same evening by `2d50df0` (apt-lock wait,
one aggregated issue) and on 10-13 by `172c395` (re-check before writing → `resolved_later`). #931 itself was not
affected (e2e-browser skipped by triage, Playwright smoke uses its bundled browser). Catch-up cron: 10-13 02:07 tick saw
#930 as "missing" while its event run was still in progress, waited for the lock and reused the event result (1 s, no
second test) — harmless. env-guard hourly checks on 10-13 morning: healthy, nothing repaired. bench
`shadowRoundTrip` 20k timed out on the first run on #918, #928 (passes on base) and #938 (10-13, also fails on base):
a recurring mivo test-budget issue, not PR-related.

**Accuracy.**
- #918 code-review P1 (workflow_dispatch from a non-main ref runs that ref's YAML, so the new "refuse non-main" step can
  be bypassed): technically correct, but anyone who can push a branch can already run workflows with these secrets, so
  it is a hardening gap rather than a new hole — P1 is on the high side (P2 would fit). Correctly not test-backed, no
  issue (`not-eligible`).
- #918 AI test `ai-test-1` (fails-on-current, "32 MiB evidence rejected before open"): **false positive**. The test
  passed an observation without `schemaVersion` / `repo`, so `validateObservation` threw `invalid-observation-identity`
  before the size check; the code under test does check size via `lstat` before `open`. The 10-13 日报 listed it as
  "AI 补测线索，待确认" — which is the right wording; nothing to correct in Feishu.
- Issues: only system_health issues #8–#15 (allowed type, auto-closed). No PR issue; the only P1 (#918) was review-only.
  Correct per rules.

**Change: AI tests failing on missing fixtures → 测试无效** (`argus/src/aitests.mjs` `toInvalidTest`). A case that
fails before any assertion with ENOENT / missing module / missing export is now `result: "discarded"` + `invalid: true`,
`invalid_kind`, `display_zh: "测试无效"` (no finding); `ai_tests.invalid` count (included in `discarded`);
`checks[].ai_invalid`. Assertion failures stay `fails-on-current`. Dry run against every stored fails-on-current case:
only #904 `ai-test-4` (10-11, ENOENT `src/lifeline.json`) would change; #884 ×2 and #918 stay (assertion failures).
No 10-12 PR is affected, so nothing was re-run. #904 was not re-run either: a re-run regenerates different AI tests,
so it cannot re-classify that case, and #904's severity (P1, code review) would not change.
SCHEMA (`docs/report-schema.md` + `$QA_HOME/reports/SCHEMA.md`) and REPORT-STYLE updated.

**For the 日报 bot.** New additive fields `ai_tests.invalid`, `ai_tests.tests[].invalid / invalid_kind / display_zh`,
`checks[].ai_invalid / display_zh`. Existing readers need no change (invalid cases are `discarded`); do not mention
测试无效 cases in the group.

**Ideas, not done.** Catch-up could skip a PR whose event run currently holds the lock (cosmetic). Semantic
"expected error X, got earlier validation error Y" (#918, #884 ai-test-2) still needs a human; not auto-classified.

## 2026-10-14 review — data day 2026-10-13

**Reconcile.** GitHub: 7 PRs merged into `xindong/mivo-canvas-plugin` on 2026-10-13 (Shanghai): #930 #938 #940 #925 #923 #951 #948 (merge order). Every one has `reports/merged/PR-N.json` (schema 1.0). Triggers: event for all except **#923 = catch-up** (no event-run log or evidence dir; catch-up cron at 22:23 CST found it missing and filled it — on-merge wake likely never started or aborted before logging; needs routine-run history to confirm). No duplicates. #918 merged 10-12; #956 merged 10-14 01:03 CST (out of window). Issue rule: only system_health #16 opened on 10-13 (allowed); no PR issues for review-only P1s (#923/#930/#951). Correct.

**Stability.** Runs 821–1241 s (docs-only #940 = 10 s). #951 `system_health: degraded` from AI-test `AbortSignal.timeout(299999.5)` (odd remaining budget `left/2`). Catch-up repaired the #923 gap. env-guard hourly on 10-14 morning: healthy, nothing repaired. Recurring bench `shadowRoundTrip` timeout on #925/#930/#938 (also fails on base; root cause: no explicit vitest timeout, loops ~6.6s > 5s default).

**Accuracy.**
- #918 review P1 (workflow bypass): only exploitable with repo write → should be P2 (grading rule updated).
- #923 review P1 (emergency bypass vs ruleset): not valid in current config → should be P3; annotated on report (`verified_note`, disposition). P2 (old review markers) confirmed (fixed later by #956; report not altered to mention the fix).
- #918 AI-test-1: already noted 10-13 as false positive (assertion before size check).
- ENOENT→测试无效 already shipped in `5b7c439` (10-13).

**Changes pushed this review.**
- AI-test delay integer floor (`left/2` → `Math.floor`) + AbortSignal/exec floors; regression test; close #16.
- `pre_existing` boolean on check findings (+ upgrade unverified→pre-existing P3 when `base_run=fail`); SCHEMA both copies.
- Code-review grading: write-access-only → P2; hypothetical-config → P3.
- Commit pending REPORT-STYLE (e821f64) + track-followups feedbackBlock; daily-review-log.

**For the 日报 bot.** New field `prs[].findings[]` / `failed_checks[]` **`pre_existing`** (boolean): true when status is `pre-existing` or `evidence.base_run === "fail"`. Read it to label 老问题/合并前就有. Severity for such upgraded cases is P3 going forward.


**Rerun.** Manual `--pr 938 --quota-pool test --wait-lock` after the fix: bench passed (was timeout on 10-13), verdict **review-suggested P2 → clean P3**. Old report at `history/PR-938.v1.json`. Conclusion change vs what the 日报 already sent for 10-13.

## 2026-10-15 review — data day 2026-10-14

**Reconcile.** GitHub: 12 PRs merged into `xindong/mivo-canvas-plugin` on 2026-10-14 (Shanghai; `merged_at`
2026-10-13T16:00Z–2026-10-14T15:59:59Z): #956 #959 #962 #941 #926 #945 #964 #968 #967 #950 #966 #927 (merge order).
Every one has `reports/merged/PR-N.json` (schema 1.0, parses, `sha_consistent`). Triggers: event for 10; **#967 and
#966 = catch-up** — no event run at all (no `nightly pr start` in control.log at merge time; both by zhongxingtian-ai;
#966's matches the on-merge routine failure at 21:40 CST noted before the reset). #967 filled by the 20:07 catch-up
tick (tested 20:21); #966's 22:07 catch-up attempt was killed by the box reset (~23:25), filled by the 23:56 catch-up
(tested 00:25 10-15). #927 event run started 51 min after merge (23:55, right after the box came back). #945 waited
8 min for #926's lock. Catch-up reused in-flight event results for #964/#968/#927 (1–2 s, no second test) — harmless.
No duplicates. #970 (00:27 10-15) is out of window.
Verdicts as sent / current: #956 #964 partially-verified P3 (black-box triaged out) · #962 docs-only · #945 clean P3 ·
#967 clean P3 (bench pre-existing P3) · #941 needs-attention P1 (code review only; new-code 82.52 %) · #926
needs-attention P1 (2 review P1; new-code 52.62 %; bench pre-existing) · #966 needs-attention P1 (2 review P1) · #927
review-suggested P2 (review P2; new-code 68.93 %) · #950 P1 → P2 and #968 P2 → P3 (calibrated 10-14 21:27 / 18:25,
already told) · **#959 review-suggested P2 → clean P3 after re-run (below)**.

**Field consistency.** No verdict ↔ findings ↔ severity, coverage totals ↔ dimension_status, or test_backed ↔ issue
contradictions, except: (1) `PR-950.json` `issues.results[]` still lists the two review findings as `P1 not-eligible`
after the hand calibration to P2 (action unchanged, so no effect); (2) #950 and #968 were calibrated by editing the
file in place with a `.bak-*` copy left in `reports/merged/` — no `history/` version, no `previous_version`, which is
not what *Report preservation* in the schema says. Readers ignore `.bak` (filename rule), so nothing breaks. Not changed.

**Stability.** Code-PR runs 824–1177 s, docs-only 9 s. MemAvailable 3.5–9.5 GB (< 8 GB on #941 #967 #950 #966 #927 →
coverage serial). No qa_errors, timeouts, capped quotas, not-tested. `system_health: degraded` on #959 (AI-test
`delay 299999.5`, fixed by `eb5469b` on 10-14 after #959 ran), #950 (pr-summary LiteLLM 400, fixed `7ff0704`), #966
(AI-test `Unexpected end of JSON input`, fixed today). Two box resets: ~23:25 10-14 (killed #966 catch-up; its
4-shard unit run had one 30 s timeout in `task-plugin/control.test.mjs` under load, never reported; the final 2-shard
run passed) and ~00:41 10-15 (logs restored 00:41, bootstrap 00:55 reinstalling chromium; #970's first launch died,
the relaunch ran while chromium was missing → e2e-browser unavailable, issue #16 reopened with 6 env items).
env-guard hourly since then: healthy. Coverage-run unit shards exit 1 on every PR (v8 instrumentation; documented,
never part of the verdict).

**Accuracy.**
- #959 bench `shadowRoundTrip` timeout: first run, rerun and base all failed → old problem; graded P2 `unverified`
  only because the `pre_existing` upgrade (`base_run=fail` → P3) shipped after #959 ran. Same case as #938.
- #966 review P1 ×2 look high: (a) branch-renamed candidate PR → duplicate PR needs a maintainer renaming the bot's
  branch in the company repo (repo-write only → P2 by the grading rule); (b) "verify then `gh pr edit`" window is
  milliseconds (verify runs right before the edit) and only loses a maintainer note → P2/P3. Not calibrated (not
  Praise's PR, no issue effect); left for the group review.
- #941 / #926 review P1 (data-consistency in legacy migration; inline image data vs text length cap) plausible, kept.
- Issues: none created on 10-14 in pr-qa-agent-deploy. #16 (system_health) commented 01:38 (#959), closed 11:11 (`eb5469b`),
  reopened 21:20 (#950) and closed 21:25 (`7ff0704`), reopened 01:13 10-15 (#970 env). No PR issues: all P1 are review-only / not Praise's. Correct per rules.

**Changes pushed this review.**
- `5750e29` fix(ai-tests): empty / truncated model reply → clear reason with finish_reason, retried when calls are
  left; kept in `llm_errors` only if no later round produced tests. Regression test `ai-test-empty-content.test.mjs`;
  report-schema `ai_tests.status` note. No new report fields.
- `58383c6` committed the 日报 bot's uncommitted `report/track-followups.mjs` change (references-only follow-up,
  `--no-remind`).
- `$QA_HOME/reports/SCHEMA.md` re-synced (it had missed `118113d`'s shard text).
- `c5d4b8a` panoptes CI had been red since `d66c082` (shellcheck SC2016 on the intentionally single-quoted
  `EXTRA_EXIT_CLEANUP`, eval'd in the EXIT trap); added `# shellcheck disable=SC2016`. CI green again.

**Re-runs (manual, test pool, `--no-reuse --no-issues`, `--wait-lock`).**
- #959: review-suggested P2 → **clean P3** (bench passed; AI tests no error, 1 call, nothing usable). Old: `history/PR-959.v1.json`.
  Conclusion change vs the 10-15 日报.
- #970 (data day 10-15): partially-verified P3 / degraded → **clean P3 / ok** (e2e-browser ran). Old:
  `history/PR-970.v1.json`. The on-merge summary already sent had the old result.

**For the 日报 bot.** #959 correction (P2 → clean P3, bench timeout is an old problem); #970 read the current file
for the 10-15 report. No field changes.

**For Praise.** On-merge routine missed #967 and #966 (catch-up covered both, with 45 min / 2 h 45 min delay); routine
run history is not visible from the box. #16 still open though env-guard has been healthy since ~01:30.
