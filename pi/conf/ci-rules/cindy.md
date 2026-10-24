# CI rules brief: makecindy/cindy (Cindy client; read before you change anything)
<!-- Sources (sync when they change): cindy repo .github/workflows/ci.yml (client-ci), pr-design-basis.yml,
     pr-template-rules.yml, .github/PULL_REQUEST_TEMPLATE.md, .github/dco.yml, AGENTS.md (Git 与交付),
     docs/dev-rules/engineering-conventions.md §3.2, scripts/check-test-hygiene.mjs, scripts/check-pr-design-basis.mjs;
     check -> command table kept in step with PraiseZhu/vigil watchers/cindy/bin/cindy-ci-triage.mjs (Mac mini watcher).
     ~/.claude/skills/cindy-pr-preflight/preflight.sh copies an older, shorter step list: trust ci.yml.
     Checked against cindy main 345d719f (2026-10-25). Keep under 60 lines. -->

## Know before you start
- pnpm monorepo (`pnpm install --frozen-lockfile`); never npm/yarn, never touch pnpm-lock.yaml or package.json deps.
- Required checks: `DCO`, `verify` (= verify-checks job + Linux unit tests shards 1-4), `Windows unit tests`
  (Windows shards 1-4 + Windows runner and database checks). Also runs: `Desktop Git integration`,
  `check:pr-design-basis`, `check:pr-template-rules`, CodeQL, Greptile review (advisory).
- DCO: every commit needs `Signed-off-by:` matching the author (`git commit -s`); a pushed unsigned commit can only be
  fixed by rewriting history, so get it right the first time. Self-check: `pnpm check:dco`.
- Tests: only through a real production entry with an observable result; one module's cases go into its existing file;
  a new test file must run alone in < 5 s. `pnpm check:test-hygiene` blocks: `vi.resetModules()` in beforeEach/helpers,
  real waits >= 100 ms (use `vi.useFakeTimers()`), reading production source text in a unit test (guard tier only).
  The baseline (scripts/test-hygiene-baseline.json) only goes down; never edit it.
- i18n: every key in all locales under apps/desktop/src/renderer/i18n/locales/ (zh-CN/zh-TW/en/ja/ko); colours via design
  tokens, no new hex literals; SQLite migrations are append-only (never edit an old one); no credentials in the repo.

## Run yourself before you answer (verify-checks order; run the ones your change touches)
- `pnpm check:test-hygiene` · `pnpm test:runner` · `pnpm test:guard`
- `pnpm check:design-colors --base-ref origin/main --head-ref HEAD` · `pnpm check:design-inventory` · `pnpm check:endpoints`
- `pnpm check:i18n` · `pnpm check:brand-terminology` · `pnpm check:i18n-glossary`
- `pnpm --filter desktop typecheck` · `pnpm --filter mobile typecheck` (or `pnpm --filter <pkg> run --if-present typecheck`)
- DB change: `XDT_MIGRATION_BASE_REF=origin/main pnpm --filter desktop db:validate`
- `pnpm ci:scheduler-guard` · `pnpm --filter mobile test:scope`
- Tests: `pnpm test:unit:related` (default), single file `pnpm --filter desktop exec vitest run <file>`;
  a Linux shard: `XDT_UNIT_TEST_SHARD=<i>/4 pnpm run test:workspaces --tier unit`.

## PR title / body
- Title `<type>(<scope>)?: <描述>`, type in feat/fix/refactor/perf/chore/docs/test/revert/build/ci.
- Body keeps the template's three `##` sections exactly: `这次改了什么`, `怎么验证的`, `风险`; list the real commands run
  and their results (and, for a new / rewritten test, its §3.2 value fields and measured run time).
- Changes under apps/desktop/src/renderer/, apps/mobile/{app,src/...} UI dirs or any .css/.scss/.less: fill
  `- 引用的设计规范：` with a docs/design-rules/ section, or `不涉及：<理由>` (check:pr-design-basis). Editing the body
  re-runs it; no push needed.
- Feature PRs state the remote-connection / mobile adaptation conclusion (reviewers treat a missing one as P1).

## Most common reasons a fix gets sent back
1. A commit without a matching `Signed-off-by` (DCO red; history rewrite needed).
2. Windows unit shard red: path separators, drive letters, case-insensitive paths, file locks.
3. test-hygiene red: new real wait, resetModules per test, or a source-text assertion in a unit test.
4. Missing i18n key in one locale / new hex colour / brand term outside the glossary.
5. Typecheck of the other app (mobile vs desktop) not run; `Ineffective mark-compacts` in typecheck is runner memory, not code.
6. Weakened or skipped tests to get green: not allowed, the reviewer and the harness both reject it.
