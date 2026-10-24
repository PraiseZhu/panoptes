# CI rules brief: xindong/mivo-canvas-plugin (read before you change anything)
<!-- Sources (sync when they change): mivo repo .githooks/pre-push, .github/workflows/ci.yml, pr-hygiene.yml (pr-format-gate),
     pr-handoff.yml, agent-use/docs/pr-rules.json, .github/pull_request_template.md, AGENTS.md, docs/pr/README.md;
     check -> command table kept in step with PraiseZhu/vigil watchers/mivo/bin/mivo-ci-triage.mjs (Mac mini watcher).
     Checked against mivo main d72564dd (2026-10-25). Keep under 60 lines. -->

## Know before you start
- Required on a PR: `verify` (aggregates lint+tsc+unit+logging, Linux unit shards 1-4, structure guard, design verification,
  lifeline spec, cindyplugin build, secret scan, Windows unit shards 1-4, Windows plugin smoke), plus `pr-format-gate`,
  `pr-ci-freshness`, `review-pr`. Windows runs every unit test: hard-coded `/` paths, `\n` vs `\r\n`, case-sensitive
  paths, file locks and timing make Windows red while Linux is green. Build paths with `path.join`, compare normalized.
- The harness runs `.githooks/pre-push` after you (7 steps, in order): `npx tsc -b` -> `npm run lint` ->
  `node scripts/verify-debug-logging.mjs` -> `node scripts/check-i18n.mjs` -> `node scripts/ci/cycle-guard.mjs` ->
  `node scripts/ci/structure-guard.mjs` -> `npm run test:unit -- --maxWorkers=1 --no-file-parallelism` (full suite, serial).
  CI runs more than the hook: internal/endpoint literal guards, `verify:design`, plugin build, gitleaks, Windows.
- CI and the hook run tests through `npm run test:unit` (scripts/run-vitest.mjs adds `--no-experimental-webstorage`);
  if plain `npx vitest run` shows `localStorage.clear is not a function`, use `npm run test:unit -- <files>`.
  Default vitest timeout (5 s per test): no real sleeps, no network, fake timers for waits.
- Never change: package.json / package-lock.json / vitest config / tsconfig / .github/ / CODEOWNERS / `.npmrc`
  (Windows control paths: any change there needs maintainer approval and blocks the PR), version in cindyplugin/ghost.json.
- `docs/pr/<N>/` is written by the harness's docs step only; `docs/pr/<other N>/` is history, never edit it.

## Run yourself before you answer (targeted, fast)
- `npx tsc -b` (or `npx tsc --noEmit -p tsconfig.app.json`) and `npx eslint <changed files>`.
- `npm run test:unit -- <test files>`; `npx vitest related --run <changed source>` for neighbours.
- Touched logging / user-visible strings / imports: `node scripts/verify-debug-logging.mjs`, `node scripts/check-i18n.mjs`,
  `node scripts/ci/cycle-guard.mjs`. Touched a URL / host / internal name literal:
  `node scripts/ci/check-internal-literals.mjs` and `node scripts/ci/check-endpoint-literals.mjs`.
- Touched CSS / theme / design tokens / font sizes: `MIVO_LITERAL_RATCHET_ENFORCE=1 npm run verify:design`.
- Added a big file or function: `node scripts/ci/structure-guard.mjs` (size ratchets against origin/main).

## PR title / body / handoff (the harness writes them; keep your output consistent with them)
- Title `<type>(<scope>)?: <描述>`, type in feat/fix/refactor/perf/chore/docs/test/revert/build/ci; bot fixes are `fix(...)`.
- `fix`/`feat` body must have `## 这次改了什么`, `## 怎么验证的`, `## 风险` headings and >=80% of `### 提交前检查` ticked;
  the ```mivo-platform-evidence``` block must be valid JSON or deleted entirely (half-filled = red).
- Handoff: `docs/pr/<N>/review.md` + `qa.md`, checked by `node scripts/pr-handoff/check.mjs <N> --type fix`
  (gate `pr-handoff-gate`). A fix must state the counter-proof: which test fails when the fix is reverted.
  Never write "审查通过" / "QA 通过" / "只审这里" in them.

## Most common reasons a fix gets sent back (Pi line history 10-23..10-25 first, then repo-wide)
1. Full serial `test:unit` red: the fix broke an EXISTING case in the same module (QA1188), or a test near the 5 s
   limit times out under the serial full run (QA1198) -> run the whole test file(s) of every module you touched.
2. CI-only checks the hook does not run: changing scripts/loops/lifeline/{machine,ledger,tick,report}.mjs,
   owner-prompt.md or spec/ triggers the TLA+ model check (Java, 20 min limit; not runnable here). Never change model
   bounds / constants (#1263 hard-coded `PrIds`, the state space blew up, the job timed out).
3. Test green on Linux, red on a Windows shard (path separators, EOL, timing) -> fix how it runs, never skip it.
4. Editing the locked regression test from the repro step, or weakening tests (skip/only/todo, deleted asserts,
   `toBeDefined` instead of real values) -> the harness reverts it and you lose a round.
5. `npm run lint` / `tsc -b` errors in a file your targeted run did not cover; a new user-visible string missing from
   one of the four locales (verify:i18n); a log line breaking verify:logging.
6. Edits outside the fix (lockfile, config, docs/pr) -> dropped or blocked; keep the diff minimal.
7. A red that only waits on another check (`pr-ci-freshness`, the platform-evidence step of `pr-format-gate`), or
   the same failure on main, is not your code: say so and change nothing.
