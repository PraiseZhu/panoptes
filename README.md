# panoptes

**Mivo 夜间 QA，Linux 版。** Argus 的继任者：每天对 `xindong/mivo-canvas-plugin` 前一天合并 / 活跃的 PR，在隔离沙盒里
真跑测试、三重确认失败归因、用 ming-qa 做白盒代码审查，最后产出一份机器可读的日报 JSON。

*Nightly QA for Mivo on Linux — the successor to [Argus](https://github.com/PraiseZhu/project-argus). For every PR merged or
active on a given Shanghai day it checks out the exact revision, runs the project's real test steps inside a bubblewrap
sandbox, triple-checks any failure (rerun → base → latest main), adds a ming-qa code review, and writes one JSON report.
By default it never writes to GitHub.*

> Panoptes was the hundred-eyed watchman; Argus was his other name.

## Provenance / 来源

| Part | Source |
|---|---|
| `argus/` | Vendored from [`PraiseZhu/project-argus`](https://github.com/PraiseZhu/project-argus) at **`ad4977e`** (`ad4977ed5aeb2649abc108111a2581fc391e42e8`), plus the Linux port (nightly CLI, bubblewrap sandbox, ming-qa integration, Playwright smoke). The first commit is the untouched snapshot, so `git diff HEAD~1 -- argus/` is exactly the port. |
| `ming-qa/` | Git submodule → [`mingdui/ming-qa`](https://github.com/mingdui/ming-qa) pinned at **`3ae36dd`**. |
| `run-nightly.sh`, `mini/`, `scripts/`, `llm-models.json` | Written for the Linux box deployment. |

panoptes is **not** a fork and does not sync with project-argus. The Mac mini keeps running project-argus unchanged;
nothing here is pushed upstream.

## Layout

```
panoptes/
├── run-nightly.sh          entrypoint (cron / scheduler / on-merge trigger call this)
├── argus/                  QA engine (Node ≥ 24): src/, config/registry.json, config/profiles/, tests/
├── ming-qa/                submodule: code-review focus + `qa_agent.py assert-code-review` gate
├── mini/                   Mac mini side: cindy schedule that pulls the issue queue into lifeline
├── scripts/refresh-roster.py   rebuilds $QA_HOME/author-map.json (GitHub roster + Feishu open_ids)
├── llm-models.json         models available on the gateway (reference only)
└── secrets.env.example
```

Code and data are separate. Everything the pipeline writes lives in **`$QA_HOME`** (default `/workspace/mivo-qa`):

| `$QA_HOME/…` | What |
|---|---|
| `argus-root/` | `ARGUS_ROOT`: `app/` (sqlite ledger, evidence), `cache/` (git mirrors, npm/uv caches, run dirs), `logs/`, `nightly.lock` |
| `reports/YYYY-MM-DD.json`, `reports/merged/PR-N.json` | nightly and on-merge reports |
| `cache/ms-playwright/` | Playwright browsers |
| `.venv/` | Python for ming-qa |
| `author-map.json` | roster output of `scripts/refresh-roster.py` (personal data, never committed) |

## Setup / 安装

```sh
git clone --recurse-submodules https://github.com/PraiseZhu/panoptes.git /workspace/panoptes
cd /workspace/panoptes/argus && npm ci                      # Node 24 (nvm); installs playwright
python3 -m venv "$QA_HOME/.venv"                            # ming-qa runtime is stdlib-only
PLAYWRIGHT_BROWSERS_PATH="$QA_HOME/cache/ms-playwright" npx playwright install chromium
install -m 600 ../secrets.env.example /home/box/.config/mivo-qa/secrets.env   # then fill it in
```

Requirements: Linux with **bubblewrap** (`/usr/bin/bwrap`), Node 24 via nvm (`$NVM_DIR`, default `~/.nvm`),
`git`, `gh`, Python 3, Debian Chromium at `/usr/bin/chromium` (Mivo's browser journey).

Secrets (`secrets.env`, mode 0600): `GITHUB_TOKEN`, `QA_LLM_BASE_URL`, `QA_LLM_API_KEY`, optionally
`ARGUS_TYPESAFE_API_KEY` (Jev triage). See [`secrets.env.example`](secrets.env.example).

Environment overrides for `run-nightly.sh`:

| Var | Default |
|---|---|
| `QA_HOME` | `/workspace/mivo-qa` |
| `QA_SECRETS_FILE` | `/home/box/.config/mivo-qa/secrets.env` |
| `PANOPTES_DIR` | directory of `run-nightly.sh` (symlinks resolved) |
| `ARGUS_MINGQA_DIR` | `$PANOPTES_DIR/ming-qa` |
| `ARGUS_PYTHON` | `$QA_HOME/.venv/bin/python` |
| `ARGUS_LLM_MODEL` | `openai/gpt-6-luna` |
| `PLAYWRIGHT_BROWSERS_PATH` | `$QA_HOME/cache/ms-playwright` |

## Usage / 用法

```
run-nightly.sh [--date YYYY-MM-DD] [--pr N ...] [--limit N] [--no-review] [--skip-step ID ...]
               [--out PATH] [--no-reuse] [--wait-lock [SECONDS]] [--no-issues | --create-issues]
```

| Flag | Meaning |
|---|---|
| `--date D` | Asia/Shanghai day to report on. Default: yesterday in Shanghai. |
| `--pr N` | Test only these PRs (repeatable). Discovery still runs and is recorded in `system_health.discovery`. |
| `--limit N` | Test at most N discovered PRs. |
| `--no-review` | Skip the ming-qa LLM code review. |
| `--skip-step ID` | Skip one profile step (repeatable), e.g. a slow bench. |
| `--out PATH` | Write the report to PATH instead of `reports/D.json` (a daily-report filename is refused). |
| `--no-reuse` | Re-test merged PRs even if `reports/merged/PR-N.json` already covers the same merge commit. |
| `--wait-lock [S]` | Queue behind a running instance for up to S seconds (default 14400) instead of exiting 75. |
| `--no-issues` | Default. Accepted for explicitness. |
| `--create-issues` | Open issues — **only if `ARGUS_ALLOW_ISSUES=1` is also exported by the caller.** The secrets file cannot switch this on. |
| `-h`, `--help` | Print usage. |

Exit codes: `0` ok / degraded · `3` pipeline failed (see `system_health`) · `1` usage or config error · `75` another run is active.

Discovery (all authors): PRs **merged** during the day, plus PRs **open** with activity during the day.

Typical calls:

```sh
run-nightly.sh                                                      # nightly, 03:00 Shanghai
ARGUS_ALLOW_ISSUES=1 run-nightly.sh --pr 893 --wait-lock \
    --out "$QA_HOME/reports/merged/PR-893.json" --create-issues     # on-merge trigger
```

Nightly reuse: a merged PR whose `reports/merged/PR-N.json` has `tested_sha` = its merge commit (and a finished
verdict) is not re-tested; the entry is copied with `reused: true` and listed in `summary.reused_from_on_merge`.

## Per-PR pipeline / 单个 PR 的流程

1. Classify the change (docs-only PRs skip tests); optional Jev triage.
2. Fresh git worktree from a local bare mirror: merged → merge commit, open → `refs/pull/N/head`.
3. Run the profile steps (`argus/config/profiles/mivo-canvas-plugin.json`) inside the sandbox.
4. Triple-check failures: clean rerun → base revision → latest main (merged PRs). Status per failure:
   `introduced | pre-existing | flaky | fixed-later | unverified | timeout | setup-failed`.
5. ming-qa white-box review of the diff, validated by ming-qa's own `assert-code-review` gate.
6. Plain-language PR summary.

Dimensions: **white-box** (unit tests, `tsc -b`, eslint, guards, code review) · **gray-box** (API tests, real-server
probe, plugin build, perf bench) · **black-box** (Mivo CDP browser journey, Playwright smoke of the built plugin).

Severity: **P0** introduced failure on a merged PR that still fails on latest main · **P1** introduced reproducible
product-check failure on an open PR, or LLM review P0/P1 (LLM-only capped at P1) · **P2** quality-guard failure,
unverified, timeout, setup failure, review P2 · **P3** pre-existing / flaky / fixed-later, or nothing found.

## Sandbox / 沙盒: bubblewrap

Every install / test command runs as
`bwrap --ro-bind / / --tmpfs /home --tmpfs /root --tmpfs /workspace --tmpfs /tmp … --bind <run dir> --bind <argus cache>`:

- root filesystem read-only; `/home`, `/root`, `/workspace`, `/tmp`, `/mnt`, `/media`, `/srv` replaced by empty tmpfs —
  tests can't see `gh` credentials, `secrets.env`, SSH keys or other agents' files;
- only the run dir, the Argus cache and a per-run tmp are writable; Node, Playwright browsers and `argus/node_modules`
  are re-mounted read-only;
- new pid / ipc / uts (+ cgroup) namespaces, `--die-with-parent`, `--new-session`; minimal env with no tokens;
- network is not restricted (`npm ci` needs it).

No bwrap → the run refuses to test. `ARGUS_ALLOW_UNSANDBOXED=1` falls back to "minimal env + private HOME/TMPDIR"
and `system_health` flags it.

## Issues / 提 issue 规则

Off unless `--create-issues` **and** `ARGUS_ALLOW_ISSUES=1`. When on:

- **Target:** only the private repo **`PraiseZhu/pr-qa-agent-deploy`** — never the repo under test.
  Labels `qa-auto` + `cindy-dispatch`.
- **PR findings:** only **P0/P1** findings **backed by a failing check** (`source: "check"`) on PRs **authored by
  PraiseZhu**. Findings that come only from the LLM code review **never** become issues, at any severity — they stay in
  the report. Skipped candidates are listed with a reason.
- **system_health:** QA tool failures, environment problems and LLM errors always become issues (any author), with a
  ```` ```json mivo-qa-health ```` block.
- **Dedup:** each body carries `<!-- mivo-qa-fingerprint: mq1-… -->`; existing `qa-auto` issues (open **and** closed)
  with the same marker are skipped — no comment, no reopen.
- Finding bodies include a ```` ```json mivo-qa-finding ```` block (`schema: mivo-qa-finding/v1`) that
  [`mini/check-queue.sh`](mini/README.md) turns into a lifeline observation on the Mac mini.

## Report / 报告格式

`$QA_HOME/reports/YYYY-MM-DD.json`:

```
{ schema_version, tool, date, timezone, repo, generated_at,
  run: { id, started_at, finished_at, duration_s, args, argus_commit, ming_qa_commit, llm_model, evidence_root },
  dimensions, severity_scale,
  summary: { pr_count, prs_by_severity, findings_by_severity, findings_total, reused_from_on_merge },
  prs: [ { number, title, author, url, state, draft, discovery_reason, tested_sha, base_sha,
           risk_class, severity, verdict, outcome, outcome_reason, reused?, reused_from?,
           checks: [ { id, name, dimension, qa_dimensions, status, duration_ms, … } ],
           failed_checks: [ { check_id, dimension, test, status, severity, attribution,
                              fingerprint, pr_fingerprint, evidence{ log_tail, rerun, base_run, … } } ],
           code_review: { status, gate, summary, findings_count, file },
           findings: [ … each with source, dimension, severity, fingerprint, evidence ],
           ai_summary, coverage, evidence_dir } ],
  issues: { enabled, repo, labels, policy, results? },
  system_health: { status: ok|degraded|failed, qa_tool_failures[], env_problems[], llm_errors[],
                   environment{…}, discovery{…}, notes[] } }
```

`fingerprint` (`mq1-…`) ignores PR number, SHA, durations, temp paths and line numbers, so it deduplicates across
PRs and nights; `pr_fingerprint` adds `-pr<N>`. `run.argus_commit` is the panoptes commit. Full logs for every run
(first / rerun / base / latest) are kept under each PR's `evidence_dir`.

## Development

```sh
cd argus && npm test        # node --test tests/*.test.mjs
```

More detail on the port: [`argus/README-linux.md`](argus/README-linux.md). Original Argus docs: [`argus/README.md`](argus/README.md).
