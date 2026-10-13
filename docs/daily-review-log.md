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
