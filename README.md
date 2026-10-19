# panoptes

**Mivo QA，Linux 版。** Argus 的继任者：`xindong/mivo-canvas-plugin` 的 PR 合并后立即（on-merge）在隔离沙盒里
真跑测试、三重确认失败归因、用 ming-qa 做白盒代码审查，产出机器可读的 JSON（`reports/merged/PR-N.json`）；
按天的发现运行用来补漏和覆盖开放 PR。日报按 `merged_at`（换算成上海时间）挑选 PR。

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
├── scripts/send-feishu-dm.mjs  sends one Feishu DM (P0/P1 author notification), idempotent per dedupe key
├── report/                 daily report: send-feishu-report.mjs (sender), REPORT-STYLE.md, build-sample-*.mjs,
│                           allowed-chats.example.json ($QA_HOME/report/send-feishu-report.mjs is a symlink here)
├── llm-models.json         models available on the gateway (reference only)
└── secrets.env.example
```

Code and data are separate. Everything the pipeline writes lives in **`$QA_HOME`** (default `/workspace/mivo-qa`):

| `$QA_HOME/…` | What |
|---|---|
| `argus-root/` | `ARGUS_ROOT`: `app/` (sqlite ledger, evidence), `cache/` (git mirrors, npm/uv caches, run dirs), `logs/`, `nightly.lock` |
| `reports/YYYY-MM-DD.json`, `reports/merged/PR-N.json`, `reports/open/PR-N.json` | nightly, on-merge and open-PR (`--write-open`) reports |
| `cache/ms-playwright/` | Playwright browsers |
| `.venv/` | Python for ming-qa |
| `author-map.json` | roster output of `scripts/refresh-roster.py` (personal data, never committed) |
| `feishu-sent.json` | dedupe log of `scripts/send-feishu-dm.mjs` / `report/send-feishu-report.mjs` (`{ key: { open_id, sent_at, message_id } }`) |
| `report/allowed-chats.json` | group allow-list of the daily-report sender (real chat ids; never committed — format in `report/allowed-chats.example.json`) |
| `report/send-feishu-report.mjs` etc. | **symlinks** into `panoptes/report/` (the 09:04 daily job calls this exact path); generated `content-*.json` / `sample-*` stay here |
| `reports/catch-up/` | catch-up child reports, `run-<ts>.json` results, `state.json` attempt counters |

## Setup / 安装

```sh
git clone --recurse-submodules https://github.com/PraiseZhu/panoptes.git /workspace/panoptes
/workspace/panoptes/scripts/bootstrap-box.sh                # everything below except secrets; idempotent
install -m 600 secrets.env.example /home/box/.config/mivo-qa/secrets.env   # then fill it in
```

`scripts/bootstrap-box.sh` (`--check` only reports) installs whatever is missing: Node 24 via nvm in
`/workspace/_tools/nvm`, `npm ci` for argus, Playwright Chromium in `$QA_HOME/cache/ms-playwright` plus its system
libraries, the ming-qa venv `$QA_HOME/.venv`, and via apt (passwordless sudo) Debian `chromium`, `bubblewrap`, `jq`,
`git`, `util-linux`, `python3-venv`, `cron`, then the `/usr/local/bin/chromium` launcher below and the catch-up timer
(`/etc/cron.d/panoptes-catch-up`, cron daemon started if not running). Everything that can live under
`/workspace` does, so a box replacement (which wipes `$HOME` and system packages) only costs the apt part.
**`run-nightly.sh` runs the check before every run and calls the bootstrap automatically** when something is missing
(`PANOPTES_BOOTSTRAP=0` disables that); the outcome is in `system_health.bootstrap`, unresolved gaps in
`qa_tool_failures` (stage `bootstrap`) and `env_problems`, what it fixed in `system_health.bootstrap.self_repaired`. A held
apt/dpkg lock (e.g. a package install at boot) is waited for and retried, up to `PANOPTES_APT_LOCK_WAIT` s (default
600). Without Node 24 the wrapper still writes a minimal report
(`system_health.status: "failed"`, never over an existing file) and exits 3.

Memory: the coverage round runs in parallel with the first run only when `MemAvailable` ≥
`ARGUS_PARALLEL_MIN_FREE_GB` (default 8) and `ARGUS_MAX_PARALLEL` ≠ 1; otherwise it runs after it (`run_mode` per PR).
Every run, including manual ones, goes through the single-run lock (`--wait-lock` to queue).

Requirements: Linux with **bubblewrap** (`/usr/bin/bwrap`), Node 24 via nvm (`$PANOPTES_NVM_DIR`, default `/workspace/_tools/nvm`, fallback `~/.nvm`),
`git`, `gh`, Python 3, Debian Chromium (`apt install chromium`) plus the panoptes launcher for Mivo's browser journey:

```sh
sudo install -m 755 scripts/chromium-linux.sh /usr/local/bin/chromium   # profile linux BD2_CHROME points here
```

> Why a wrapper: Debian's `/etc/chromium.d/dev-shm` (sourced by `/usr/bin/chromium`) runs `[ $shm_avail -lt … ]` on
> raw `findmnt` output, which has one line per mount when `/dev/shm` is stacked (inside bubblewrap `--dev /dev`), and
> prints `/usr/bin/chromium: 9: [: …: unexpected operator`. `scripts/chromium-linux.sh` does what the Debian launcher
> does (sources `/etc/chromium.d/*`, same flags, execs `/usr/lib/chromium/chromium`) but runs a fixed `/dev/shm`
> check instead of `dev-shm`. Debian's package files stay untouched. `/usr/local/bin/chromium` is a path Mivo's
> `browser-layer.mjs` trusts (program `chromium` under `/usr/bin` or `/usr/local/bin`). Without the wrapper, panoptes
> falls back to `/usr/bin/chromium` (works, but logs the warning).

Secrets (`secrets.env`, mode 0600): `GITHUB_TOKEN`, `QA_LLM_BASE_URL`, `QA_LLM_API_KEY`, optionally
`ARGUS_TYPESAFE_API_KEY` (Jev triage), `MIVO_FEISHU_APP_ID` / `MIVO_FEISHU_APP_SECRET` (Feishu roster + DMs). See [`secrets.env.example`](secrets.env.example).

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
run-nightly.sh --catch-up [--wait-lock SECONDS] [--trigger catch-up|manual] [--create-issues]
run-nightly.sh [--trigger event|catch-up|manual] [--date YYYY-MM-DD] [--pr N ...] [--limit N] [--no-review] [--skip-step ID ...]
               [--out PATH] [--no-reuse] [--no-step-cache] [--wait-lock [SECONDS]] [--pr-timeout MIN] [--deadline HH:MM | --burst-cutoff [HH:MM]]
               [--write-merged | --no-write-merged] [--write-open] [--no-issues | --create-issues] [--quota-pool test|production] [--fresh-review]
```

| Flag | Meaning |
|---|---|
| `--catch-up` | Pure-script gap filler, safe to run from cron every few minutes (quiet and fast when there is nothing to do). Lists PRs merged yesterday + today (Shanghai) via `gh`, compares with `reports/merged/PR-N.json` (gap = missing / unfinished / `tested_sha` ≠ merge commit) and tests the gaps oldest-first with `--pr N --date <merge day> --write-merged --wait-lock S --no-issues --out reports/catch-up/PR-N.json`. A lock timeout (75) stops and retries next invocation; after **3** attempts a PR is given up and recorded in `system_health` (`reports/catch-up/run-*.json` and the next nightly report). One catch-up at a time (`catch-up.lock`). Exit `0` ok / nothing to do, `3` something still missing or given up. |
| `--trigger T` | Who started the run, recorded as **`run.trigger`**: `event` (on-merge trigger), `catch-up`, `manual`. Default `manual`; `--catch-up` defaults to `catch-up` and passes it to its children. |
| `--date D` | Asia/Shanghai day to report on. Default: yesterday in Shanghai; **with `--pr` the PR's merge time converted to a Shanghai date** (open PRs: `updated_at`; several PRs: the latest such day), recorded in `run.args.date_source`. |
| `--pr N` | Test only these PRs (repeatable). The report's `prs[]` contains **only** these PRs; discovery still runs and is recorded in `system_health.discovery` (other PRs of that day in `discovery.excluded_by_pr_filter`, not as `not-tested` entries). |
| `--limit N` | Test at most N discovered PRs. |
| `--no-review` | Skip the ming-qa LLM code review. |
| `--skip-step ID` | Skip one profile step (repeatable), e.g. a slow bench. |
| `--out PATH` | Write the report to PATH instead of `reports/D.json` (a daily-report filename is refused). |
| `--no-reuse` | Re-test merged PRs even if `reports/merged/PR-N.json` already covers the same merge commit. |
| `--no-step-cache` | Run every step even if a passing result for identical inputs is cached (see *Burst capacity*). |
| `--wait-lock [S]` | Queue behind a running instance for up to S seconds (default 14400) instead of exiting 75. |
| `--pr-timeout MIN` | Per-PR time budget (env `ARGUS_PR_TIMEOUT_MIN`). Every child process is capped by the remaining budget and no new step starts after it; the PR is reported `not-tested` (P2, `pr-timeout`). Default: no limit. |
| `--burst-cutoff [HH:MM]` | Overnight burst cutoff (default `08:30` Shanghai): like `--deadline`, but untested PRs get `not_tested_code: "burst-cutoff"` and are picked up later by the catch-up timer. See `docs/report-schema.md` › *Burst capacity* (unit sharding, step-result reuse). |
| `--deadline HH:MM` | Shanghai wall-clock deadline or ISO time (env `ARGUS_DEADLINE`). PRs not started by then → `not-tested` (P2, `deadline`); a running PR is stopped. |
| `--write-merged` / `--no-write-merged` | Also write each actually-tested merged PR to `reports/merged/PR-N.json` (on-merge format). Default on without `--pr`, off with `--pr`. |
| `--write-open` | Also write each **open** PR of the run to `reports/open/PR-N.json` (same per-PR format; untested ones too, with `not_tested_code` / reason). Default off. |
| `--quota-pool P` | Daily model-call quota pool, `production` or `test`. Default: `--trigger manual` → `test` (manual runs, drills, acceptance reruns never consume production quota), `event` / `catch-up` → `production`. Separate daily caps per kind: ai-test 50, ai-review 70, jev 150, pr-summary 40 (`ARGUS_QUOTA_<KIND>`, `ARGUS_QUOTA_TEST_<KIND>`, or `$ARGUS_ROOT/quota.json`). A hit cap → `status: "capped"` (额度用尽未补测), see `docs/report-schema.md` › *Model-call quotas*. |
| `--fresh-review` | Do not reuse a cached code-review verdict. By default a review of the same commit + diff + reviewer prompt/version + model is reused (`code_review.reused`, `reused_from`), so re-runs give the same review. If a cached verdict exists, `--fresh-review` only compares (`code_review.fresh_comparison`); the cache is not overwritten and the cached verdict stays authoritative. See `docs/report-schema.md` › *Code review reuse*. |
| `--no-issues` | Default. Accepted for explicitness. |
| `--create-issues` | Open issues — **only if `ARGUS_ALLOW_ISSUES=1` is also exported by the caller.** The secrets file cannot switch this on. |
| `-h`, `--help` | Print usage. |

Exit codes: `0` ok / degraded · `3` pipeline failed (see `system_health`) · `1` usage or config error · `75` another run is active.

**Temp files.** Each `run-nightly.sh` run uses its own `TMPDIR=/tmp/argus-run-XXXXXX`, removed on exit (trap); `npm test`
does the same with `/tmp/argus-testrun-XXXXXX` (`argus/run-tests.sh`). At every start (so also from the catch-up cron)
`scripts/sweep-tmp.sh` removes leftover `/tmp/argus-*` dirs that are older than 24 h (nothing inside modified for 24 h),
owned by the current user and not held by a live process (cwd / root / open fd); result lines in
`$ARGUS_ROOT/logs/tmp-sweep.log` (only when something was removed). `PANOPTES_TMP_SWEEP=0` disables it;
`scripts/sweep-tmp.sh --dry-run [--max-age-hours H]` previews.

**Catch-up timer.** `scripts/bootstrap-box.sh` installs `/etc/cron.d/panoptes-catch-up` (from
`scripts/catch-up.cron`; the box has no systemd, so it also installs and starts the `cron` daemon). Every 2 hours
(minute 7 of even hours, box time) it runs, without any model call:

```sh
ARGUS_ALLOW_ISSUES=1 run-nightly.sh --catch-up --trigger catch-up --create-issues --wait-lock 14400
```

With nothing missing it exits 0 in about a second (bootstrap self-check + one `gh pr list`) and writes nothing
(output: `$QA_HOME/argus-root/logs/catch-up-cron.log`). A box replacement wipes `/etc` and the daemon; the self-check
reports `catch-up-timer` / `cron-daemon` as missing and the next bootstrap (any `run-nightly.sh` call) reinstalls and
restarts them. `PANOPTES_CATCHUP_TIMER=0` opts out.

Discovery (all authors): PRs **merged** during the day, plus PRs **open** with activity during the day.

Typical calls:

```sh
run-nightly.sh --pr 893 --trigger event --wait-lock \
    --out "$QA_HOME/reports/merged/PR-893.json"                     # on-merge trigger (primary)
run-nightly.sh --date 2026-10-10 --deadline 08:45                   # day-level catch-up / open PRs
```

Combined day report from per-PR files (later `--from` wins for the same PR; `summary` recomputed, `system_health`
aggregated, see `docs/report-schema.md` › *Combined reports*):

```sh
cd argus && node src/cli.mjs assemble --date 2026-10-10 --out "$QA_HOME/reports/full-2026-10-10.json" \
    --from "$QA_HOME/reports/merged-batch-2026-10-10.json" --from "$QA_HOME/reports/merged/PR-897.json" \
    --from "$QA_HOME/reports/open/PR-878.json" --from "$QA_HOME/reports/open/PR-888.json" --from "$QA_HOME/reports/open/PR-731.json"
```

Nightly reuse: a merged PR whose `reports/merged/PR-N.json` has `tested_sha` = its merge commit (and a finished
verdict) is not re-tested; the entry is copied with `reused: true` and listed in `summary.reused_from_on_merge`.

## Per-PR pipeline / 单个 PR 的流程

1. Classify the change (docs-only PRs skip tests but still get the ming-qa code review on the merge commit,
   `coverage_gate: {status: "not-applicable"}`); Jev triage gives every one of the 29 QA dimensions an explicit
   applicable / not-applicable decision with a reason (`prs[].triage`; without a key or when Jev fails the note
   `维度适用性未判定（Jev 未配置）` / `…（Jev 分诊失败）` is added and failures go to `system_health`).
2. Fresh git worktree from a local bare mirror: merged → merge commit, open → `refs/pull/N/head`.
3. Run the profile steps (`argus/config/profiles/mivo-canvas-plugin.json`) inside the sandbox.
4. Triple-check failures: clean rerun → base revision → latest main (merged PRs). Status per failure:
   `introduced | pre-existing | flaky | fixed-later | unverified | timeout | setup-failed`.
5. ming-qa white-box review of the diff, validated by ming-qa's own `assert-code-review` gate.
6. Plain-language PR summary.

Dimensions: **white-box** (unit tests, `tsc -b`, eslint, guards, `verify:design`, internal/endpoint literal guards,
gitleaks secret scan, code review) · **gray-box** (API tests, real-server probe, plugin build, web build
`npm run build`, plugin dist scans host-locale / vendor-split, perf bench) · **black-box** (Mivo CDP browser journey,
legacy-data migration + IndexedDB recovery journey, Playwright smoke of the built plugin).

Target-CI parity (commands taken verbatim from `xindong/mivo-canvas-plugin` `.github/workflows/ci.yml` and
`package.json`):

| Step | Command | Kind → introduced failure |
|---|---|---|
| `web-build` | `npm run build` (`tsc -b && vite build`); only applies when the revision has `index.html` — the web entry was retired and the target CI no longer runs it, so current revisions list it in `skipped_steps` as not applicable | product → P1 open / P0 merged |
| `design` | `npm run verify:design` with `MIVO_GUARD_BASE_SHA=<base_sha>`, `MIVO_LITERAL_RATCHET_ENFORCE=1` | quality → P2 |
| `internal-literals` | `node scripts/ci/check-internal-literals.mjs` | quality → P2 |
| `endpoint-literals` | `node scripts/ci/check-endpoint-literals.mjs` | quality → P2 |
| `host-locale` | `node cindyplugin/check-host-locale.mjs cindyplugin/dist` (builds dist if missing; not applicable to revisions without the script) | quality → P2 |
| `vendor-split` | `node cindyplugin/check-vendor-split.mjs cindyplugin/dist` (builds dist if missing; not applicable to revisions without the script) | quality → P2 |
| `secret-scan` | `gitleaks detect --source . [--config .gitleaks.toml] --log-opts "<base_sha>..HEAD" --no-banner --redact=100` (binary: `ARGUS_GITLEAKS_BIN`, `/usr/local/bin/gitleaks`, `/workspace/_tools/gitleaks` or PATH; missing → unavailable) | product → P1 open / P0 merged; no latest-main recheck (a leaked secret stays in history) |

Diff-scoped checks (`design`, `secret-scan`) get the PR's `base_sha` as `${DIFF_BASE_SHA}` in every run (first,
rerun, base, latest).

API probe (`api-probe`): every route under `server/routes` — status codes, error codes for bad input, idempotency
(repeat + same result) where applicable. Signed-in probes (projects / canvas / nodes / attach-detach / user-state)
use a **locally signed Cindy JWT**: when the revision exports the test seam `setCindyJwksLoader`
(`server/lib/assetAuth.ts`), the probe generates a throw-away RSA key, starts the server through
`probe-local-auth-server.mjs` with that public JWKS, and signs tokens for two local users
(issuer `https://auth.cindy.com.cn`, audience `argus-qa:mivo-canvas`). Assets are also probed through the host
exchange with a freshly generated `MIVO_CINDY_EXCHANGE_SECRET`. No production account is ever used. Fallback for
revisions without the seam: `MIVO_QA_TEST_TOKEN`, or `MIVO_QA_TEST_USER` + `MIVO_QA_TEST_PASSWORD` +
`MIVO_QA_TEST_TOKEN_URL` from `secrets.env`; with neither, signed-in probes are listed in `skipped_steps` with reason
`no test account` — never counted as passed.

Severity: **P0** introduced failure on a merged PR that still fails on latest main · **P1** introduced reproducible
product-check failure on an open PR, or LLM review P0/P1 (LLM-only capped at P1) · **P2** quality-guard failure,
unverified, timeout, setup failure, flaky (unstable), review P2 · **P3** pre-existing / fixed-later, or nothing found.
Flaky = failed, then the same test passed on a clean rerun of the same commit: P2 "unstable", not attributed to the PR,
`test_backed: false` — it never triggers notifications or issues.

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
- **system_health:** QA tool failures, environment problems and LLM errors become issues (any author), aggregated into
  **one** issue per run with a list and a ```` ```json mivo-qa-health ```` block — only for problems still present
  after the run's self-repair (bootstrap retry); self-repaired / `transient` ones stay in the report only. An open
  system_health issue gets a comment instead of a new issue.
- **Dedup and recurrence** (as in argus's publisher): dedup by the body marker `<!-- mivo-qa-fingerprint: mq1-… -->`
  over `qa-auto` issues, open and closed. Open issue with the same fingerprint → comment with the new evidence (once
  per run × PR); closed as fixed → reopen + comment; closed as won't-fix (`state_reason: not_planned` or a `wontfix`
  label) → stay silent; none → create. Code-review fingerprints include a hash of the normalized finding title.
- Finding bodies include a ```` ```json mivo-qa-finding ```` block (`schema: mivo-qa-finding/v1`) that
  [`mini/check-queue.sh`](mini/README.md) turns into a lifeline observation on the Mac mini.

## Feishu DMs / 飞书私信: `scripts/send-feishu-dm.mjs`

Sends one plain-text DM from the Mivo Feishu bot, used for P0/P1 author notifications. It does not decide *whether*
to notify — the caller does; it only sends safely and once.

```sh
node scripts/send-feishu-dm.mjs --open-id ou_xxx --text-file msg.txt [--dedupe-key KEY] [--allow-owner]
node scripts/send-feishu-dm.mjs --help
```

- **Credentials:** `MIVO_FEISHU_APP_ID` / `MIVO_FEISHU_APP_SECRET` from the environment, else from
  `$QA_SECRETS_FILE` (default `/home/box/.config/mivo-qa/secrets.env`). Never printed or logged.
- **Guardrails:** the open_id must be in `$QA_HOME/author-map.json` (`mivo_authors` or `roster`); entries with
  `notify_via_feishu: false` are refused unless `--allow-owner`.
- **Idempotency:** with `--dedupe-key`, a key already in `$QA_HOME/feishu-sent.json` is skipped (`skipped: true`);
  the key is recorded after a successful send. The key's sha256 is also passed as Feishu's request `uuid`, so a retry
  within an hour is deduplicated server-side even if the local log write failed. An empty `--dedupe-key` value means
  no dedupe (original behaviour) and the result JSON carries a `warning`.
- **Paths:** `QA_HOME` (legacy `MIVO_QA_ROOT`), or per file `MIVO_QA_AUTHOR_MAP`, `MIVO_QA_FEISHU_SENT`.
- **Output:** one JSON object on stdout (`ok`, `message_id` | `skipped` | `error`/`stage`/`code`/`msg`).
  Exit `0` sent or skipped · `1` Feishu/API/network error · `2` usage/config error · `3` recipient refused.

## Coverage gate / 覆盖率门禁 (white-box)

A dedicated coverage run (`cov`: own checkout of the tested revision, unit step only, in parallel with the first
verdict run) adds `vitest --coverage` (v8, JSON reports). Verdict runs never carry coverage — v8 instrumentation makes
a few Mivo tests fail — and the coverage run's own pass/fail never affects the verdict. **Only new-code coverage is
gated (90)**; repo-wide lines / branches / functions are recorded as baseline trend data (`coverage_gate.baseline`,
reference lines 80 / 70 / 80) and never produce a finding. New code = added/modified lines of
`git diff base_sha..tested_sha` in non-test JS/TS sources, intersected with v8 statement lines (a changed line inside a multi-line statement or branch range counts as the innermost one); files no test loads
count as uncovered. New code < 90% → one **P2** finding (`source: "coverage-gate"`), which by the issue rules never
becomes an issue. Result in
`prs[].coverage_gate`. `ARGUS_COVERAGE=0` turns it off.

## Report / 报告格式

**Produced reports are never rewritten.** Re-classification = a re-run; when a run writes to an existing report path
the old file is moved to `history/<name>.vN.json` in the same directory (e.g. `reports/merged/history/PR-902.v1.json`)
and the new one records `previous_version: "history/PR-902.v1.json"`. Readers read only `reports/merged/PR-<digits>.json`.
Do not patch reports with ad-hoc scripts; `store-merged` / `normalize` re-store through the same writer.

Full field reference (for the daily-report agent): **[`docs/report-schema.md`](docs/report-schema.md)** (copied to
`$QA_HOME/reports/SCHEMA.md`). Key points: every `prs[]` entry has `tested_at` (ISO 8601, `+08:00`); discovered
but untested PRs stay in `prs[]` as `verdict: "not-tested"`; `qa-error` is P2; tests and code review use the same
commit (`tested_sha` = `reviewed_sha`); `system_health.heartbeat` and `system_health.complete`.

`$QA_HOME/reports/YYYY-MM-DD.json`:

```
{ schema_version, tool, date, timezone, repo, generated_at,
  run: { id, started_at, finished_at, started_at_shanghai, finished_at_shanghai, duration_s, args,
         argus_commit, ming_qa_commit, llm_model, evidence_root },
  dimensions, severity_scale,
  summary: { pr_count, prs_by_severity, findings_by_severity, findings_total, reused_from_on_merge,
             tested_count, not_tested[], qa_errors[] },
  prs: [ { number, title, author, url, state, draft, discovery_reason, tested_sha, base_sha,
           reviewed_sha, review_base_sha, sha_consistent, test_started_at, tested_at,
           risk_class, severity, verdict, outcome, outcome_reason, not_tested_code?, not_tested_reason?,
           reused?, reused_from?,
           checks: [ { id, name, dimension, qa_dimensions, status, duration_ms, … } ],
           failed_checks: [ { check_id, dimension, test, status, severity, attribution,
                              fingerprint, pr_fingerprint, evidence{ log_tail, rerun, base_run, … } } ],
           code_review: { status, gate, summary, findings_count, file },
           findings: [ … each with source, dimension, severity, fingerprint, evidence ],
           ai_summary, coverage, coverage_gate, evidence_dir } ],
  issues: { enabled, repo, labels, policy, results? }, merged_reports: [ { pr, file } ],
  system_health: { status: ok|degraded|failed, qa_tool_failures[], env_problems[], llm_errors[],
                   environment{…}, discovery{…}, notes[], heartbeat{…}, complete, all_discovered_tested } }
```

`fingerprint` (`mq1-…`) ignores PR number, SHA, durations, temp paths and line numbers, so it deduplicates across
PRs and nights; `pr_fingerprint` adds `-pr<N>`. `run.argus_commit` is the panoptes commit. Full logs for every run
(first / rerun / base / latest) are kept under each PR's `evidence_dir`.

## Development

```sh
cd argus && npm test        # node --test tests/*.test.mjs
```

CI (`.github/workflows/ci.yml`, push/PR to `main`, no secrets): `bash -n` + shellcheck on `run-nightly.sh` and
`mini/check-queue.sh`, `node --check scripts/*.mjs`, `py_compile scripts/*.py`, and `npm ci && npm test` in `argus/`
on Node 24. If the runner can't run bubblewrap, the tests run with `ARGUS_SANDBOX=none ARGUS_ALLOW_UNSANDBOXED=1`
(the bwrap isolation test then skips itself).

More detail on the port: [`argus/README-linux.md`](argus/README-linux.md). Original Argus docs: [`argus/README.md`](argus/README.md).
