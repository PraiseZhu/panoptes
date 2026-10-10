# Mivo QA queue → lifeline intake (for mini)

The nightly Mivo QA pipeline (project-argus `nightly.mjs --create-issues`) opens P0/P1 findings as
issues in the private repo `PraiseZhu/pr-qa-agent-deploy`, labelled `qa-auto` + `cindy-dispatch`.
Each body has a fenced ```` ```json mivo-qa-finding ```` block (`schema: mivo-qa-finding/v1`) and a
`<!-- mivo-qa-fingerprint: mq1-… -->` marker.

On mini, one cindy schedule pulls that queue into lifeline. It does nothing else.

| file | role |
|---|---|
| `check-queue.sh` | cindy `preRunHook`. Bash + `gh` only (no model, no jq needed; bash 3.2 OK). If the queue is empty it exits 2 and cindy skips the fire (0 tokens). Otherwise it writes the **oldest** usable issue as a lifeline observation and exits 0. |
| `schedules.json` | cindy project-automation file (`{"version":1,"schedules":[…]}`) with one entry, `mivo-qa-queue-ingest`. |

Each fire's agent runs `doctor.mjs ingest`, comments the result on the issue, and swaps
`cindy-dispatch` for `cindy-ingested` (or `cindy-ingest-failed`). From there the
existing lifeline → Vigil → keel → review-pr chain takes over.

## What check-queue.sh writes

Both files go in `<evidence dir>/issue-<N>/`. The evidence dir must be inside the lifeline config's
`evidenceRoot`, because `doctor.mjs ingest` rejects paths outside it and any symlinked
ancestor. On macOS that means it can't be under `/tmp` or `/var`.

* `evidence-<sha16>.json`: the immutable payload `{schema:"mivo-qa-lifeline-evidence/v1", queue:{repo,issue,url,title,createdAt}, finding:<mivo-qa-finding block>, issueBody}`. The name comes from its own sha256, and it is never rewritten. **Do not delete these files.** Lifeline and the Vigil mivo watcher re-hash them later.
* `observation-<sha16>.json`: the `--input` for `doctor.mjs ingest`, in the shape `validateObservation` in `scripts/loops/lifeline/state.mjs` expects:
  `schemaVersion:1, id:"mivo-qa-issue-<N>", repo:<finding.repo>, source:"mivo-qa", sourceSha:<finding.pr.tested_sha>, caseId:"mivo-qa.<check_id>", outcomeCode:"MIVO_QA_<FINGERPRINT>", status, sourceSeverity, observedAt:<issue createdAt>, evidence:{path,sha256}, semantic:{assertionId:<fingerprint>, failureDimension:<dimension>, oracleRevision:"mivo-qa-finding/v1"}`, plus an informational `queueIssue`.
  The status is `PRODUCT_FAIL`, except `ENV_BLOCKED` for setup-failed or timeout findings and `NOT_RUN` for unavailable ones. `sourceSeverity` is the finding's P-level for check findings and `unverified` for LLM code-review findings.

stdout (cindy appends it to the agent prompt as untrusted data): line 1 is the observation path, then
`MIVO_QA_OBSERVATION=`, `MIVO_QA_EVIDENCE=`, `MIVO_QA_ISSUE=`, `MIVO_QA_ISSUE_URL=`, `MIVO_QA_FINGERPRINT=`,
`MIVO_QA_LIFELINE_CONFIG=`, `MIVO_QA_QUEUE_DEPTH=`, (`MIVO_QA_SKIPPED_UNPARSEABLE=`), `CINDY_PRECHECK_OK`.

Exit codes: `0` an issue is ready (run) · `2` queue empty (skip) · `3` gh failed (not installed, not logged in, network or API error) ·
`4` there are open `cindy-dispatch` issues but none has a usable finding block (needs a human) · `5` local error
(evidence dir, sha256 tool, write). With any code other than 0 or 2, cindy blocks the run and notifies you.

Env: `MIVO_QA_LIFELINE_CONFIG` (sets the evidence dir to `<evidenceRoot>/mivo-qa`; read with jq or node) ·
`MIVO_QA_EVIDENCE_DIR` (explicit override; the fallback is `$HOME/mivo-ops/evidence/mivo-qa`) · `MIVO_QA_QUEUE_REPO` ·
`MIVO_QA_QUEUE_LABEL` · `MIVO_QA_SOURCE` · `GH_BIN`. The script adds `/opt/homebrew/bin` and `/usr/local/bin` to
PATH because GUI-launched cindy often has a minimal PATH.

## Install on mini

1. **Prerequisites.** You need `gh` logged in as an account that can read and write issues and labels on
   `PraiseZhu/pr-qa-agent-deploy` (check with `gh issue list -R PraiseZhu/pr-qa-agent-deploy`). You also need node for doctor.mjs, and
   the lifeline config already in use on mini. Use the same file that Vigil's mivo watcher has as
   `lifelineConfigPath` / `MIVO_WATCHER_LIFELINE_CONFIG`.
2. **Copy the script** to a stable directory outside any repo checkout, e.g. an ops dir next to the lifeline config:
   `install -m 0755 check-queue.sh <MINI_QA_DIR>/check-queue.sh`
3. **Dry-run the hook**: `MIVO_QA_LIFELINE_CONFIG=<config.json> /bin/bash <MINI_QA_DIR>/check-queue.sh; echo $?`.
   Expect `2` while the queue is empty. With an issue queued, expect `0`, plus an observation path inside `evidenceRoot`.
4. **Create the labels once** (the agent can also create them on first use):
   `gh label create cindy-ingested -R PraiseZhu/pr-qa-agent-deploy` and `gh label create cindy-ingest-failed -R PraiseZhu/pr-qa-agent-deploy`.
5. **Register the schedule.** Copy the entry from `schedules.json` into
   `<project>/.cindy/automations/schedules.json` and replace `__LIFELINE_CONFIG__` and `__MINI_QA_DIR__` with absolute paths.
   If that file already exists, append the entry to its `schedules` array. `<project>` must be the mivo-canvas-plugin checkout on mini, because the prompt runs
   `scripts/loops/lifeline/doctor.mjs` from the worktree root, and it must be a git repo since `useWorktree` is true. The file is
   project-local config: keep it out of commits by adding `.cindy/` to that checkout's `.git/info/exclude`.
   Cindy loads `.cindy/automations/schedules.json` for projects it knows about. You can also create the same task in the UI.
6. Leave `silentWhenIdle: true`. Skipped fires and successful ingests stay quiet; a hook failure (exit 3/4/5) or a failed run notifies you.

Rollback: delete the entry (or pause it in cindy). Already-ingested defects and evidence stay in lifeline.
