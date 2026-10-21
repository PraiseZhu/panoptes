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

## 2026-10-16 (data day 2026-10-15, Asia/Shanghai)

**Reconciliation.** 17 PRs merged on 10-15 (#915 #935 #970 #972 #973 #975 #977 #979 #980 #981 #983 #986 #987 #988
#989 #990 #991); all 17 have a valid `reports/merged/PR-N.json`, schema 1.0, `run.trigger` `event` (16) and `manual`
(#970, the 10-15 re-run after the box reset). No miss, no duplicate: the three catch-up runs (14:07/16:07/18:07 PT
crons) only reused existing reports (2 s each). No issue created; #915's code-review P1 correctly `not-eligible`
(not Praise's PR, review-only).

**Consistency.** Global check over all merged reports (PR severity = max finding severity = summary
`prs_by_severity`) found one hand-calibration error: **#972** — the 10-15 calibration lowered the code-review finding
P2→P3 and also set the PR severity to P3, but the coverage-gate finding (new code 89.33% < 90%) is still P2, so the PR
is P2 (summary already said P2). Fixed by hand (`prs[0].severity` P2, `calibration.note`), old file
`PR-972.json.bak-20261016-severity`. Lesson: after a calibration, recompute PR severity from all findings.

**Stability.** system_health ok on every run; no qa_errors, timeouts, capped quota. Memory pressure: #991 waited the
full 30 min for MemAvailable ≥ 3 GiB and ran with 1 unit shard (other agents' processes hold ~12 GB); #975 waited 1 min.
Durations 16–21 min for product PRs, queueing up to 41 min (#977 behind #979/#980). env-guard all ok, nothing restored.
Issue #16 (system_health, reopened 10-15 01:13 for the chromium-missing reset) closed today as fixed: every run since
the 00:55 bootstrap is healthy.

**Accuracy.** #915 AI test `canvasSyncRuntime.accountSwitch.argus.test.ts` failed with vitest
`No "namespacedKey" export is defined on the "../../lib/persistUserId" mock` — the test's own `vi.mock` was
incomplete, so it was a test defect reported as a P2 "fails on the tested commit". #915 review P1 (sync progress
persisted before asset side effects → crash in between loses the side effects on replay) is plausible, kept.
P2 reviews on #979/#980/#987/#991 plausible. #990 AI-test failure (eval of `REVEAL_DOM_WAIT_SCRIPT` in the test) was
already calibrated to P3 on 10-15; left.

**Changes pushed.**
- `0ef42d9` fix(ai-tests): vitest's "No "x" export is defined on the "…" mock" now classified 测试无效
  (`invalid_kind: missing-module`, existing value — no field change). Regression case in `qa-items.test.mjs`.
- `a9c4578` committed the 日报 bot's uncommitted `report/track-followups.mjs` fix (sender prints pretty JSON → parse
  whole stdout; dedupe PR numbers in the thanks key).
- `03d6f82` (日报 bot's REPORT-STYLE edit, committed locally earlier) pushed with them. Unit 262/263 pass (1 skipped),
  gitleaks clean, CI green on `a9c4578`. No schema change.

**Re-runs (manual, test pool, `--no-reuse --no-issues`, `--wait-lock`).**
- #915: needs-attention P1 → **needs-attention P1**, but the P2 AI-test finding is gone (new AI tests: 4 kept,
  0 failing). Old: `history/PR-915.v1.json`. The regenerated tests didn't hit the mock gap, so the new rule was not
  exercised live; the regression test covers it.

**For the 日报 bot.** #915: drop the P2 "AI-generated test fails on the tested commit" (it was the test's own mock
mistake); still P1 from code review. #972: PR level is P2 (coverage 89.33% < 90%), not P3. No field changes.

## 2026-10-17 (data day 2026-10-16, Asia/Shanghai)

**Reconciliation.** 20 PRs merged on 10-16 (#971 #982 #992 #993 #995 #1005 #1006 #1008 #1009 #1010 #1011 #1012 #1015
#1016 #1017 #1018 #1024 #1026 #1028 #1029); all 20 have a valid `reports/merged/PR-N.json`, schema 1.0. `run.trigger`
`event` for 17, `catch-up` for 3: the on-merge routine missed #1028 (merged 21:33, caught at 22:07) and #971 + #1017
(merged together 22:12, caught only at 03:24 / 03:43 — the catch-up cron's `gh pr list` failed on the exhausted GraphQL
quota of the shared PraiseZhu token from 00:07 on). No duplicates (the other "missing" lines in `catch-up-cron.log` were
event runs still in progress; reused). Docs-only #1005 #1012 #1016 in 12–15 s. No issue created on 10-16 — correct:
the only P1 on Praise's PRs (#993, #1006) are review-only.

**Stability.** system_health ok on every 10-16 run; no qa_errors, timeouts, capped quota. Product PRs 900–1520 s; #1018
waited 1 min for memory. Box rebooted ~07:11 10-17 (logs/reports restored 07:14, env-guard repaired chromium at 08:30,
see journal). 10-17 ~11:00: outbound network is flaky (model gateway `fetch failed` / `UND_ERR_SOCKET other side closed`
on ~1 in 3 requests, `git push` TLS reset once, `gh` "unexpected EOF") — two #1008 re-runs lost their AI tests to it.

**Accuracy.**
- #1008 AI test (P2 "fails on the tested commit"): `TypeError: renderAutomationBody is not a function` at the test's own
  line 8 — the test imported `renderAutomationBody` from `org-market-pack.mjs`, it lives in
  `org-market-open-company-pr.mjs`. Test defect → new rule (below) and re-run.
- #1017 AI test (P2): `effects[kind].get is not a function` — the test passed the journal shape (arrays) where
  `settleOwedAssetEffects` takes Maps; with Maps the account check returns false as the test expects. Calibrated by
  hand to P3 false positive → **#1017 review-suggested P2 → clean P3** (`PR-1017.json.bak-20261017-calibration`). Not
  covered by the new rule (error is inside the code under test).
- #971 review P1 ("company repo snapshot uploaded as source-repo artifact"): conditional on the source repo being public;
  both repos are `internal` in the same org, so the artifact widens nobody's access → P3 false positive. **#971
  needs-attention P1 → review-suggested P2** (coverage 62.5% < 90% remains). `PR-971.json.bak-20261017-calibration`.
- #993 was already calibrated in the 10:15 group review (P1 → P3/P2).
- #995 / #1006 review P1 plausible, kept. #1006 AI-test P3s calibrated 10-16 19:20, fine.
- `shadowRoundTrip` bench timeout again on #992 #1018 #1028 #1029 (P2). On #1018/#1029 it also failed on base
  (`pre_existing: true`) but stays P2 because flaky entries keep P2 by design, while the same test failing twice + on
  base is P3 (#959 rule) — inconsistent; proposed to Praise, not changed.

**Changes pushed.**
- `d5f15bc` ai-tests: `TypeError: x is not a function` whose first frame is in the generated test and `x` is a name the
  test imports with `import { x }` → 测试无效 (`invalid_kind: missing-module`, existing value; no field change).
  catch-up: `gh pr list` failure → fall back to REST `pulls?state=closed&sort=updated` (separate quota); verified live
  (same 20 PRs). Regression tests for both. SCHEMA (both copies) updated.
- `66181c8` network errors from the model gateway are `retryable` (next round if the per-PR call cap allows).
- `bbe1bea` network errors retried in place (3 tries, 2 s / 4 s) for AI tests and PR summary.
- `3661387` (日报 bot's REPORT-STYLE line, committed locally at 10:02) pushed with them. Unit 265/266 pass (1 skipped),
  gitleaks clean, CI green.

**Re-runs (manual, test pool, `--no-reuse --no-issues`, `--wait-lock`).**
- #1008: review-suggested P2 → **clean P3**, system_health ok (`bbe1bea`). The model again imported
  `renderAutomationBody` from the wrong module and the new rule marked it 测试无效 live; the other AI test was kept.
  Old: `history/PR-1008.v1.json` (original). `v2` / `v3` are two re-runs at 11:09 / 11:12 whose AI tests failed on the
  flaky network (degraded, never reported) — ignore them.

**For the 日报 bot.** Conclusion changes vs the 10-17 日报: #1008 P2 → clean P3 (AI-test failure was the test's own
wrong import); #1017 P2 → clean P3 (AI test passed data in the wrong shape); #971 P1 → P2 (review P1 does not hold,
both repos internal; new-code coverage 62.5% still P2). No field changes.

**For Praise.** (1) On-merge routine missed 3 of 20 merges again (#1028, #971, #1017). (2) Should a flaky check that also
fails on base (`pre_existing: true`) be P3 like other old problems? Today it stays P2 (#1018, #1029).

## 2026-10-18 (data day 2026-10-17, Asia/Shanghai)

**Reconciliation.** 10 PRs merged on 10-17 (#1014 #1031 #1032 #1043 #1037 #1076 #1059 #1075 #943 #1081); all 10 have a valid `reports/merged/PR-N.json`, schema 1.0. `run.trigger` was `event` for all on-merge launches; catch-up also touched #1076 / #943 while the event run was still in progress (reused, no duplicate test). Current files: #1014/#1075 re-ran today as `manual` after the agent-docs fix; #943 current tip is a later `manual` re-run (CR/AI network). No miss. No issue opened for test-backed P0/P1 (none); #1037/#1081 only commented on system_health #16. Correct per rules.

**Stability.** Product runs 120–1166 s (docs-only #1059 = 22 s; mis-classified #1014/#1075 originally ~800 s). MemAvailable usually ≥8 GB (parallel coverage) except #1031 (6.3 GB). Outbound model gateway flaky again: AI `fetch failed` on #1037 (×2 rounds) and #943 (400 + CR `fetch failed`); #1081 CR + pr-summary `fetch failed` (AI still kept 2 tests). `system_health: degraded` on #1037/#943/#1081. Recurring bench `shadowRoundTrip` flaky on #1076 (fail then pass; base pass → P2 flaky). Box reboot ~17:29 PT 10-17 / 08:29 CST 10-18 (after the data day); env-guard restored chromium/cron/Node; catch-up found nothing pending for 10-17 after on-merge. Code-review in-place network retry shipped `10fca0b` (10-18 10:05 CST) — was missing from `bbe1bea` which only covered AI tests / PR summary.

**Accuracy.**
- #1031 review P1/P2 (ruleset required context; `pull_request_target` base checkout) already calibrated 10-16 → P3; coverage 0% on unloaded `.github/scripts/pr-gate-rerun.cjs` also → P3 (`false_positive`). Acceptance under current main: new-code 100% (node-test / measurable path).
- #1043 structure-guard new-code 0% (source unloaded by vitest despite `structure-guard.test.mjs`) calibrated P2→P3. Root: coverage blind spot for script/`node --test` style suites — fixed on main (`0d7831a` / `a2a90aa`).
- #1075 review P2 (auto-approve still requires review account report) calibrated → P3; kept after today's docs-only re-run.
- #1081 coverage 79.94% calibrated → P3 (`false_positive`); subprocess / temp-dir V8 credit landed on main (`f1a852c`/`2f558e3`); acceptance re-run new-code **92.48% pass**. 10-18 CR re-review P1×2 on #1081 were false positives (Bearer JWT / absolute keyPath) → leave P3.
- #943 large canvas PR: published report still CR/AI error (gateway); 10-18 acceptance re-review found **P1** (cancel batch may leave library images) + P2 (hidden-stack recount) + coverage 87% P2. Not Praise's PR → no issue. Merged file not overwritten (acceptance only).
- #1076 comment-only across 33 files (incl. cindyplugin) → risk C, ran bench → flaky P2. Correct assignment: `change_kind=comment-only`, skip bench (branch `qa/comment-only-no-bench` `a9bfb05`, **not on main**). Acceptance with that branch: risk B comment-only, bench skipped, P3.

**Test-assignment (10 reviewed).** Fully correct on the day: #1059 (docs-only), #1032 (scripts/loops, cov 100%, AI on gap), #943 (product XL full suite — env failure ≠ assignment). Mis-assignment / blind spots:
- agent-docs over-tested (AGENTS.md was `NEVER_A` → risk B full suite): #1014 #1075 — **fixed on main** `491e304`; re-ran both → docs-only / `change_kind=agent-docs` (2 s).
- coverage 0% / unloaded own tests: #1031 #1043 — **fixed on main** runners + not_measurable.
- comment-only still runs bench / risk C: #1076 — **handoff** Praise PR (`qa/comment-only-no-bench`).
- env/network degraded (not assignment): #1037 #943 #1081.

**Changes today (already on main before/during this review; not authored here).** `491e304` agent-docs; `0d7831a` node-test/pytest + coverage credit by touched steps; `a2a90aa` not_measurable plain-JS; `dfecd70` AI targets uncovered lines; `10fca0b` CR network retry; `f1a852c`/`2f558e3` subprocess / temp-dir V8 coverage. This review only re-ran #1014/#1075 into `reports/merged/` and re-applied #1075 P3 calibration.

**Re-runs.** `#1014` partially-verified P3 (~770 s full suite) → **docs-only P3** (2 s). `#1075` partially-verified P3 → **docs-only P3** (2 s, review finding kept at calibrated P3). Old tips under `history/PR-1075.v1.json` / bak files.

**For the 日报 bot.** Conclusion deltas vs anything already sent for 10-17: #1014 / #1075 are docs-only (agent-docs), not full-suite partially-verified; #1076 stays flaky P2 in the published file until comment-only lands; #943 published file still lacks the P1 from the 10-18 CR re-review (see `reports/acceptance/review-PR-943.json`); #1081 coverage gate would pass under current main (92%) — published file still shows calibrated P3 coverage finding. Field notices already in REPORT-STYLE / schema (10-18): `change_kind`, `own-tests`, `not_measurable_files`, `ai_tests.uncovered_targets`; `not_a_pr_finding` reserved (bench branch not deployed).

**For Praise.** (1) Merge `qa/comment-only-no-bench` (`a9bfb05`) — unblocks #1076-class PRs. (2) Bench flaky vs `pre_existing` P3 rule still open (from 10-16 review). (3) system_health #16 still open (LLM gateway noise). (4) On-merge catch-up "missing" while event in flight (#1076/#943) is cosmetic.

## 2026-10-19 (data day 2026-10-18, Asia/Shanghai)

**Reconciliation.** 15 PRs merged on 10-18 (#997 #998 #999 #1001 #1002 #1013 #1084 #1025 #1086 #1023 #1033 #1091 #1093 #1036 #1096); all 15 have a valid `reports/merged/PR-N.json`, schema 1.0, `run.trigger=event` (catch-up only re-touched #1013/#1084 while their event runs were queued). No miss, no duplicate. No issue opened (no test-backed P0/P1); all runs only commented on system_health #16. Correct per rules.

**Stability.**
- Model gateway key expired 2026-10-18 03:37 UTC (11:37 CST). Every run after that (#1023 #1033 #1091 #1093 #1036 #1096) got 401 on AI tests, code review and PR summary. Key works again today (probe 200). env-guard does not probe the key, so nobody was told for ~21 h.
- Code review also failed with bare `fetch failed` on #997 #1001 #1002 #1013 #1084 (before the expiry). #1013/#1084 failed again in the 10-18 acceptance re-review and #1013 again in today's 10:53 re-run (CR took ~98 s for 6 tries). The same #1013 review called from a plain shell with the same code and key succeeded in 65 s — so it is something in the run context, not the PR. Net: 11 of 15 PRs have no code review in the published file.
- Unit `gh-app-router.test.mjs > 新建当天日志时清掉 14 天前的旧文件` failed with `expected 2019 to be 2020` on #998 #999 #1001 #1025 #1086 (and on base) — test subprocess inherited America/Los_Angeles; fixed on main by `a9e8694` (TZ pinned to Asia/Shanghai). Graded P3 pre_existing, so not blamed on the PRs, but it left new-code coverage `not-evaluated` on #999 #1025 #1086.
- Durations 4–1684 s; #1013/#1002 waited ~1.5 h for the lock behind the 00:40–01:02 burst.

**Accuracy.**
- #1013 AI-test P2 ("403 rate-limit → retryable read failure"): the generated test calls `transport(c, …)` without `c.budgets`; product reads `c.budgets.apiRequests` (`scripts/loops/issue-agent/transport.mjs:12`) → TypeError before the 403 path. Test fixture incomplete → 测试无效. Calibrated **#1013 review-suggested P2 → clean P3** (`PR-1013.json.bak-20261019-calibration`). Reproduced twice (event + 10:53 re-run), so the generator keeps missing required config it cannot see in the diff.
- #1023 (84.82%) / #1036 (87.67%) new-code coverage P2 stand.

**Test-assignment (15 reviewed, 10 fully correct).** Correct: #997 #998 #1001 #1084 #1013 #1023 #1033 #1093 #1036 (product/scripts full suite, AI tier matches size), #1002 #1091 docs-only (`public/changelog.json` is in profile aPaths by design). Problems:
- #999 (`src/kernel/shadowRoundTrip.bench.test.ts` only) skipped bench — the one step that runs the changed file. Old code (72edb59); `runsFiles` forcing (`0d7831a`) is on main now. Needs re-run to verify the bench timeout fix.
- #999 #1025 #1086 new-code coverage not evaluated because of the TZ unit failure (env, fixed `a9e8694`).
- #1096 new-code coverage `not-evaluated` with 0 executable lines although it changes logic (`hideCount || layerDecomposition ? …` inside spread/JSX, `countMax: 4` in an object literal): line-start-only counting misses changed lines inside a multi-line statement. Blind spot, P3, proposed.

**Changes pushed.** mingqa `chat()`: a network error now records the undici cause (code/message) and elapsed ms, so the next run-context `fetch failed` says what broke. Regression test in `review-network-retry.test.mjs`. Unit 292/293 pass (1 skipped), gitleaks clean.

**Re-runs.** A batch re-run started 10:53 CST (`scratch/rerun-20261019/run.sh`: #1013 #1084 #1023 #1033 #1036 #1091 #1093 #1096 #1076 #970 #981, manual/test pool/`--wait-lock`) — recovers AI/CR lost to the key expiry. Queued after it (`scratch/rerun-20261019/queue2.sh`): #999 #1025 #1086 #997 #1001.

**For the 日报 bot.** #1013 P2 → clean P3 (AI-test failure is the test's own missing config). Batch re-run results to follow. No field changes.

**For Praise.** (1) Renewing the model key should be followed by an env-guard probe; whether env-guard may alert on 401 is a trigger/notification rule change. (2) Bench flaky vs `pre_existing` P3 rule still open (since 10-16). (3) `qa/bench-after-unit-env-timeouts` (3 commits, 10-18 11:15) still waiting for 运维 acceptance.

## 2026-10-20 (data day 2026-10-19, Asia/Shanghai)

Review file: `/workspace/review-ops/daily/2026-10-19.md` (cross-bot-review format).

**Reconciliation.** 7 PRs merged on 10-19 (#1100 #1101 #1099 #1098 #1104 #978 #1107); all 7 have a valid `reports/merged/PR-N.json`, schema 1.0. First run of every PR was the on-merge event (history v1 = event for #1098/#1099/#1100; current file event for #1101/#1104/#978/#1107). Current #1098/#1099/#1100 are manual re-runs from the LLM-retry re-run queue (10-19 17:20 CST), not missed events — the collector's "event missed, manual 98–262 min late" lead is wrong (it reads only the current file). Catch-up at 16:07 CST flagged #1101/#1099 "missing" while their event runs were in flight (cosmetic, reused, wrote only `reports/catch-up/`). No issue opened (no test-backed P0/P1); system_health #16 commented 4×. Correct.

**Accuracy.**
- #978 AI-test P2 (`ai-test-4`): the failing case is the second one in the file (note text), which calls `isMarkupTextCoveredByHigherSection` without importing it → `ReferenceError … is not defined` at `sectionStackOcclusion.argus.test.ts:62:5`; the case named in the finding title (image caption) passed. Test invalid → calibrated to P3 false_positive (`PR-978.json.bak-20261020-calibration`). PR stays P2 on new-code coverage 83.01%.
- #1098 code-review P1 (`abandonUnreferencedGenerationExtras` only logs; `cindyGenerationPort.ts:255` at the merge commit) — the code matches the finding; not test-backed, level debatable, no issue (author lubinyu-lab).
- #1107 code-review P2 / #1036 code-review P1: Praise PR fix PRs #1114 (open, CI green) and #1112 / #1121 (draft, CI green).

**Test-assignment (7 reviewed, 4 fully correct).** Correct: #1100 #1104 (docs-only), #1098, #1107 (shell coverage not measurable — known blind spot; its .test.mjs ran in unit). Problems:
- #978: AI-test classifier missed a test-own ReferenceError (fixed below).
- #1101 (only edits `gh-app-router.test.mjs`): triage marked all 29 dimensions not-applicable (unit p=0.08) and all black-box steps were triage-skipped, so the PR became `partially-verified` P3 solely because black-box is "missing". The changed test did run in unit.
- #1099 (3 new test files + 1-line export): the PR's own tests for the notify failure branch and the lock-reclaim race are not credited, so exception/async/concurrency show as gaps (coverage 0.25) and the AI call targeted gaps the PR already covers (it then failed on network).
- All PRs: the unit run under v8 coverage has 5–20 failing tests (5 s timeouts in `cindyplugin/src/__tests__/library*`/`depositBindingLock`, ECONNREFUSED :3000) — informational only, none touched these PRs' changed files, but new-code coverage could be under-counted when they do.

**Changes pushed.** `aitests.mjs` `toInvalidTest`: `ReferenceError: X is not defined` whose first frame is in the AI test file and where X is neither imported nor declared in the test → 测试无效 (missing-module). Regression + negatives in `qa-items.test.mjs` (real #978 log reproduces as discarded/invalid). Unit 340/341 pass (1 skipped). No report field change.

**For the 日报 bot.** #978: the AI-test lead is withdrawn (test's own mistake); level unchanged P2 (coverage). No field changes.

**For Praise / Praise PR.** Test-assignment candidates: #1101-type (test-only PR: unit/regression applicable; triage-skipped dimension should not make a PR partially-verified), #1099-type (credit the PR's own added tests before computing AI gaps), coverage-run timeouts. Relative `--out` on manual re-runs still lands in `/workspace/panoptes/argus/reports/` (#1098 20:36 run, hand-copied).

## 2026-10-21 (data day 2026-10-20, Asia/Shanghai)

Review file: `/workspace/review-ops/daily/2026-10-20.md` (cross-bot-review format).

**Reconciliation.** 14 PRs merged on 10-20 (#1120 #1105 #1114 #1112 #1125 #1121 #934 #1117 #1077 #1131 #1116 #1133 #1103 #1106); all 14 have a valid `reports/merged/PR-N.json`, schema 1.0. First run of every PR was the on-merge event (#1105's history v1/v2 are both the 11:27 event run; current is the 11:51 manual re-run after the json_object LLM fix b693abb). No miss, no duplicate. No issue opened; system_health #16 commented once (#1105 gateway 400).

**Accuracy.**
- #1121 AI-test P2 (`ai-test-1`): `TypeError: client.now is not a function` at `collect.mjs:91` — the test built its own `client` without `now` and passed it to `collectGlobal(client, …)`. Test invalid → P3 (fixed below; re-run follows).
- #1077/#1103/#1106/#1116/#1133 unit failure: `scripts/ops/org-market-company-no-dependencies.test.mjs` asserts no `node_modules` above its tmp dir; it fails first run + rerun in panoptes on 10-20, but passes in GitHub CI (Linux/Windows unit shards on #1077) and passes in panoptes on 10-21 (#1137). Environment-dependent; root cause not found (plain bwrap probe sees no node_modules on the tmp ancestors). #1077's test-backed P2 from this check is likely a false positive; level stays P2 from two code-review P2s. Routed to Praise PR.

**Changes pushed.** `aitests.mjs` `fixtureMissingMethod`: `TypeError: X.M is not a function` where X is the first parameter of a function the test imports, M never appears in the test, and the source never assigns X.M → 测试无效 (fixture-gap). Regression + 5 negatives in `qa-items.test.mjs`; the real #1121 log reproduces as discarded/invalid. No report field change.
