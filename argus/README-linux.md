# Mivo QA nightly on Linux (project-argus + ming-qa)

> **panoptes note:** these are the original Linux-port notes from `/workspace/mivo-qa/project-argus`. In panoptes the
> code lives in `argus/`, ming-qa is the `ming-qa/` submodule, and the entrypoint is the top-level `run-nightly.sh`.
> See the top-level [README](../README.md) for the current layout.

Deployed on the Grok Bot box under `/workspace/mivo-qa`. This is the Linux port of Argus, used as a
nightly QA pipeline for `xindong/mivo-canvas-plugin`. It never writes to GitHub unless issue creation
is explicitly enabled (see below).

## Layout

| Path | What |
|---|---|
| `/workspace/mivo-qa/run-nightly.sh` | single entrypoint (cron / scheduler calls this) |
| `/workspace/mivo-qa/project-argus` | this repo (Linux changes are local, uncommitted) |
| `/workspace/mivo-qa/ming-qa` | symlink → `/workspace/ming-qa` (mingdui/ming-qa @ origin/main) |
| `/workspace/mivo-qa/argus-root` | `ARGUS_ROOT`: `app/` (sqlite, evidence), `cache/` (git mirrors, npm cache, run dirs), `logs/` |
| `/workspace/mivo-qa/reports/YYYY-MM-DD.json` | nightly report |
| `/workspace/mivo-qa/cache/ms-playwright` | Playwright browsers (`PLAYWRIGHT_BROWSERS_PATH`) |
| `/workspace/mivo-qa/.venv` | Python venv for ming-qa (`pytest` for its own tests; runtime is stdlib only) |
| `/home/box/.config/mivo-qa/secrets.env` | `GITHUB_TOKEN`, `QA_LLM_BASE_URL`, `QA_LLM_API_KEY` (0600, never copied) |

Runtime: Node 24 via nvm (`/workspace/_tools/nvm`, installed by `scripts/bootstrap-box.sh`; the box's system Node 20 is left alone), bubblewrap 0.12,
Debian Chromium 154 (`/usr/bin/chromium`), Playwright (headless Chromium), Python 3.13.

## Usage

```
/workspace/mivo-qa/run-nightly.sh [--date YYYY-MM-DD] [--pr N ...] [--limit N]
                                  [--no-review] [--skip-step ID ...]
                                  [--no-issues | --create-issues]
```

* `--date` — Asia/Shanghai calendar day; default = yesterday in Shanghai.
* Discovery (all authors): PRs **merged** during that day + PRs **currently open** that had activity
  during that day (`updated_at` inside the day; if it was updated again later, the issue timeline
  is checked for an event inside the day).
* `--out PATH` writes the report to PATH instead of `reports/YYYY-MM-DD.json` (a daily-report filename
  in `reports/` is refused). On-merge trigger: `run-nightly.sh --pr N --out /workspace/mivo-qa/reports/merged/PR-N.json --wait-lock`.
* Reuse: for a merged PR, if `reports/merged/PR-N.json` exists with `tested_sha` equal to the PR's merge commit
  (and a completed verdict), the nightly run does not re-test it; the PR entry is copied into the daily report with
  `reused: true` / `reused_from`, and `summary.reused_from_on_merge` lists them. `--no-reuse` forces re-testing.
  `ARGUS_MERGED_REPORTS_DIR` overrides the merged dir.
* `--wait-lock [SECONDS]` (handled by `run-nightly.sh`): queue behind a running instance for up to SECONDS
  (default 14400 = 4 h) instead of exiting 75; exit 75 if the wait times out.
* `--pr` restricts the run to specific PRs (discovery is still recorded in `system_health.discovery`).
* Issue eligibility: only P0/P1 findings backed by a failing check, on PRs authored by PraiseZhu; findings that come solely from the LLM code review never become issues (they stay in the report); system_health entries (QA tool failures, env problems, LLM errors) always do (body has a `json mivo-qa-health` block).
* Issue creation is **off** by default. `--create-issues` does nothing unless `ARGUS_ALLOW_ISSUES=1`
  is also exported. Then, for every P0/P1 finding, it opens an issue in the **private repo
  `PraiseZhu/pr-qa-agent-deploy`** (never in the repo under test) with labels `qa-auto` and
  `cindy-dispatch`, after checking (open + closed issues labelled `qa-auto`) that no issue already
  carries the same `<!-- mivo-qa-fingerprint: … -->` marker. The body includes PR/SHA/run status,
  evidence, log tail and a machine-readable ```` ```json mivo-qa-finding ```` block
  (`schema: mivo-qa-finding/v1`, fingerprint, pr, check, dimension, severity, status, evidence,
  evidence_dir, report file) for the downstream lifeline/doctor.mjs ingest. Not exercised during deployment.

Suggested schedule (Shanghai 03:00 = 12:00 PT the previous day, PDT):
`0 19 * * * /workspace/mivo-qa/run-nightly.sh >> /workspace/mivo-qa/argus-root/logs/cron.log 2>&1` (UTC crontab).

## Per-PR pipeline

1. Classify change (docs-only PRs skip tests). Optional Jev triage (needs `ARGUS_TYPESAFE_API_KEY`;
   without it every optional check runs).
2. Check out the tested revision in a fresh git worktree from a local bare mirror:
   merged PR → merge commit; open PR → PR head (`refs/pull/N/head`, works for forks).
3. Run the profile's steps inside the sandbox (below).
4. Failures are triple-checked (Argus' existing logic): clean rerun → base revision
   (merged: first parent of the merge commit; open: merge-base with the target branch) →
   latest main for merged PRs. Each failing test/check gets a status:
   `introduced | pre-existing | flaky | fixed-later | unverified | timeout | setup-failed`.
5. ming-qa code review (white-box): `openai/gpt-6-luna` reviews the diff using ming-qa's
   `qa-code-reviewer` focus list; output is written in ming-qa's `code-review.json` schema and
   validated with ming-qa's own gate `qa_agent.py assert-code-review`.
6. Argus' plain-language PR summary (same model).

### Dimensions (ming-qa)

| Dimension | Checks |
|---|---|
| white-box | unit tests, `tsc -b`, eslint, logging guard, i18n guard, cycle guard, structure guard, ming-qa code review |
| gray-box | in-process API tests, real-server API probe, plugin build, perf bench (trial) |
| black-box | Mivo's own CDP browser journey (persist / reopen / export) on Chromium, Playwright smoke of the built plugin (`panel.html`, `settings.html`) |

Each check additionally lists the ming-qa 29-dimension keys it covers (`qa_dimensions`).

### Severity

* **P0** – introduced, reproducible product-check failure on a *merged* PR that still fails on latest main.
* **P1** – introduced, reproducible product-check failure on an open PR; LLM code-review P0/P1
  (LLM-only findings are capped at P1; the original level is kept in `llm_severity`).
* **P2** – introduced quality-guard failure, unverified failure, timeout, setup failure, flaky (unstable; `test_backed: false`), review P2,
  trial checks (bench) at most P2.
* **P3** – pre-existing / fixed-later failures, review P3, or nothing found.
* PR severity = most severe finding (P3 when there are none; `verdict` says `clean`).

### Fingerprints

* `fingerprint` (`mq1-…`) is independent of PR number, SHA, durations, temp paths and line/column
  numbers: `sha256(repo | check | test-name-or-error-signature)`; for code review
  `sha256(repo | code-review | file | symbol | category)`. Use it to deduplicate issues across PRs/nights.
* `pr_fingerprint` = `fingerprint` + `-pr<N>` for per-PR dedup.

## Sandbox choice: bubblewrap

Replaces macOS `sandbox-exec`. Every test/install command runs as
`bwrap --ro-bind / / --tmpfs /home --tmpfs /root --tmpfs /workspace --tmpfs /tmp … --bind <run dir> --bind <argus cache>`:

* root filesystem read-only; `/home`, `/root`, `/workspace`, `/tmp`, `/mnt`, `/media`, `/srv` and the
  agent-infra dirs are replaced by empty tmpfs, so tests cannot read `gh` credentials, `secrets.env`,
  SSH keys or other agents' data;
* only the run directory, Argus cache (mirrors / npm cache) and a short per-run tmp dir are writable;
  the Node install (nvm), Playwright browsers and Argus' `node_modules` are re-mounted read-only;
* new pid / ipc / uts (and cgroup when available) namespaces, `--die-with-parent`, `--new-session`;
* environment is the existing Argus minimal env (no tokens, `HOME`/`TMPDIR` inside the run dir);
* network is not restricted (same as the Mac deployment; `npm ci` needs it).

Chromium's own sandbox works nested inside bwrap (no `--no-sandbox` needed).
If bwrap is missing the run refuses to test; `ARGUS_ALLOW_UNSANDBOXED=1` explicitly falls back to
"minimal env + private HOME/TMPDIR only", and the report's `system_health` flags it.
`ARGUS_SANDBOX_EXTRA_RW` (colon-separated) exists only for test fixtures.

## Report shape (`reports/YYYY-MM-DD.json`)

```
{ schema_version, date, timezone, repo, run{…}, dimensions, severity_scale, summary{…},
  prs: [ { number, title, author, url, state, draft, discovery_reason, tested_sha, base_sha,
           risk_class, severity, verdict, outcome, outcome_reason,
           checks:        [ { id, name, dimension, qa_dimensions, status, duration_ms, … } ],
           failed_checks: [ { check_id, dimension, test, status, severity, attribution,
                              fingerprint, pr_fingerprint, evidence{ log_tail, rerun, base_run, … } } ],
           code_review:   { status, gate, summary, findings_count, file },
           findings:      [ checks + code-review findings, each with dimension/severity/fingerprint/evidence ],
           ai_summary, coverage, evidence_dir } ],
  issues: { enabled:false },
  system_health: { status, qa_tool_failures[], env_problems[], llm_errors[], environment{…}, discovery{…}, notes[] } }
```

Full logs for every run (first / rerun / base / latest) are kept under `evidence_dir`.
