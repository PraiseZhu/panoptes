# QA fleet rollout

Deployment is performed by the existing `opsdeploy` path after review. This PR does not SSH to the Grokbot host, edit cron, change `$QA_HOME`, or switch the production mode. `qa-fleet-preflight.mjs` is a read only gate: it requires full source/runtime SHAs, all lane entries, cron inventory, host permissions and the `git`, Node 24, Python and bubblewrap dependencies. The host inventory is supplied by the operator and is never inferred from a passing CI job.

Run the rollout in this order:

1. Keep `qa-standard.json` in `shadow`. Run the existing QA and Pi schedulers and retain one receipt per lane each day.
2. Run `qa-fleet-shadow-compare.mjs` with seven paired, consecutive natural dates. Every P0/P1 difference needs a non-empty explanation. Missing, duplicate, invalid, stale or differently bound inputs fail closed.
3. Prepare canary evidence for each lane. The scheduler receipt, report, `fleet_status`, `stdcheck` and outbound audit must share run id, source SHA, runtime SHA, policy version, manifest hash and canary mode. Reports must state `all_discovered_tested=true` and outbound missing-evidence count must be zero.
4. Exercise rollback using a complete previous source SHA. The drill records a receipt and preserves v2 manifests, shadow history and cutover records; it does not delete evidence.
5. Only the operator with the current deployment authorization may write a cutover artifact and change mode. `qa-fleet-preflight.mjs` then validates the complete evidence bundle before `strict` is considered.

After the first strict run, the operator records who checked `fleet_status`, `stdcheck`, the outbound audit and the report, with timestamps and links to the receipts. Seven days of shadow and the canary/rollback receipts are operational evidence; GitHub Actions status alone cannot satisfy those checks.

## Operator commands and retained artifacts

Use full 40 character SHAs. `host-inventory.json` contains `dependencies` booleans (`git`, `node24`, `python3`, `bubblewrap`), `permissions` keyed by lane and permission, and `tasks: [{job, path}]` from the installed scheduler inventory. Do not include credentials or raw environment values.

```sh
node scripts/ops/qa-fleet-preflight.mjs --source-sha <source> --runtime-sha <runtime> --inventory host-inventory.json
node scripts/ops/qa-fleet-verify.mjs --source-sha <source> --runtime-sha <runtime> --manifest <qa-fleet-run-manifest.json>
node scripts/ops/qa-fleet-shadow-compare.mjs --legacy legacy-days.json --current new-days.json --explanations explanations.json
python3 pi/bin/qa_standard.py capture --state <pi-state> --file <completed-lane-run.json> --fleet-status <fleet-status.json> --stdcheck <pi-state/out/stdcheck/state.json>
node scripts/ops/qa-fleet-preflight.mjs --source-sha <source> --runtime-sha <runtime> --inventory host-inventory.json --transition strict --evidence cutover-evidence.json
```

The last command returns `proposed_standard`; it does not write the config. Preserve its output as the cutover record, obtain the current deployment authorization and use the repository deployment path. A captured bundle is an observation of the selected scheduler run: each observer must already contain that run id. Capture each lane after `fleet_status` and enabled `stdcheck` have observed it. A missing or failed observer refuses capture.

Shadow input arrays contain `{date, run_id, source_sha, standard_version, mode: "shadow", decisions: [{fingerprint, severity, decision: {status}}]}`. Explanations contain `{date, fingerprint, reason}`. Cutover evidence contains the paired arrays under `shadow`, the five captured bundles under `canary.<lane>`, and `source_sha`, `standard_version`, `manifest_hash` from canary. Its `rollback` receipt includes `ok`, `source_sha`, `previous_source_sha`, `preserved_evidence` and a real `receipt_id`. This is an operator evidence contract, not an attestation or proof that JSON supplied by an operator is authentic.

Pi model calls prepend the lane brief in `lib.pi`. `grokline` returns `qa_standard_brief` with its job metadata; the Grokbot operator must prepend that returned section to the cloud task prompt. Verify the cloud prompt on the authorized host before canary acceptance; the repository cannot change an external cloud task configuration.

The existing `opsdeploy` fast-forwards main; it does not check out an earlier SHA. The rollback plan names the previous complete source SHA for an operator authorized to restore the runtime. Keep product actions disabled in rollback until that restoration is verified. This PR neither performs that restoration nor claims a fast-forward is an exact-SHA rollback.

## Exact source restoration (authorized operator only)

Run this procedure on the authorized host after stopping new scheduler admissions,
draining active jobs and pausing `opsdeploy`/`opsenv` auto-update. Keep outbound
product actions disabled and the active standard in `rollback` throughout. Record
who paused the jobs and when. Do not resume them until the checks below pass.
This is an operator procedure; none of these host writes run in CI or this PR.

Use `CONTROL` for a retained checkout of the reviewed fleet tools, `RUNTIME` for
the installed panoptes checkout and `EVIDENCE` for a new directory under the
existing QA data root, outside either source checkout. The active fleet/standard
files must also be outside the runtime checkout (`PI_QA_FLEET_FILE` and
`PI_QA_STANDARD_FILE`), so restoring source cannot undo rollback mode. Save the
current files, runtime HEAD, v2 manifests, shadow receipts and cutover records
there before changing source; keep the originals in place. Never use `git clean`,
reset the data root or delete evidence. The previous SHA comes from the recorded
last working deployment, not a guessed branch tip.

```sh
set -euo pipefail
git -C "$RUNTIME" rev-parse HEAD > "$EVIDENCE/before-source-sha.txt"
node "$CONTROL/scripts/ops/qa-fleet-preflight.mjs" --rollback-plan "$PREVIOUS_SOURCE_SHA" > "$EVIDENCE/rollback-plan.json"
# A dirty runtime tree must be resolved by the operator before proceeding.
test -z "$(git -C "$RUNTIME" status --porcelain)"
PREVIOUS_SOURCE_SHA=$(node -p 'JSON.parse(require("fs").readFileSync(process.argv[1])).previous_source_sha' "$EVIDENCE/rollback-plan.json")
git -C "$RUNTIME" cat-file -e "$PREVIOUS_SOURCE_SHA^{commit}"
# Hold the same two locks used by deployment for the exact-SHA checkout.
exec 8>"$QA_HOME/argus-root/catch-up.lock"
exec 9>"$QA_HOME/argus-root/nightly.lock"
flock -n 8
flock -n 9
git -C "$RUNTIME" checkout --detach "$PREVIOUS_SOURCE_SHA"
test "$(git -C "$RUNTIME" rev-parse HEAD)" = "$PREVIOUS_SOURCE_SHA"
flock -u 9; flock -u 8
```

Keep the schedulers paused. Run the restored runtime's real QA command for a
known PR, with the external rollback policy inherited by the process. For example,
set `QA_PR` to that PR number and use a new output path for this drill:

```sh
"$RUNTIME/run-nightly.sh" --trigger manual --pr "$QA_PR" --no-issues --out "$EVIDENCE/restored-qa-report.json"
RUNTIME_SHA=$(git -C "$RUNTIME" rev-parse HEAD)
node "$CONTROL/scripts/ops/qa-fleet-verify.mjs" --root "$RUNTIME" --fleet "$PI_QA_FLEET_FILE" --standard "$PI_QA_STANDARD_FILE" --source-sha "$PREVIOUS_SOURCE_SHA" --runtime-sha "$RUNTIME_SHA" --manifest "$QA_HOME/argus-root/qa-fleet-run-manifest.json" > "$EVIDENCE/runtime-verification.json"
python3 "$RUNTIME/pi/bin/fleet_status.py" --json "$EVIDENCE/fleet-status.json" --md "$EVIDENCE/fleet-status.md"
python3 "$RUNTIME/pi/bin/stdcheck.py" > "$EVIDENCE/stdcheck.log"
cp "$PI_STATE/out/stdcheck/state.json" "$EVIDENCE/stdcheck.json"
```

Use a previous release compatible with the fleet contract. If the restored
runtime cannot produce a fresh manifest or any observer reports drift, remain in
rollback and investigate; do not synthesize a successful receipt. Review the
observer outputs as well as their exit codes.

Create `restoration-evidence.json` with `rollback_plan` containing the saved plan,
`restored_source_sha` containing the full restored HEAD, and `runtime_verification`
containing the **unchanged JSON output** from `qa-fleet-verify`. Then run:

```sh
node "$CONTROL/scripts/ops/qa-fleet-preflight.mjs" --root "$RUNTIME" --fleet "$PI_QA_FLEET_FILE" --standard "$PI_QA_STANDARD_FILE" --source-sha "$PREVIOUS_SOURCE_SHA" --runtime-sha "$RUNTIME_SHA" --inventory "$EVIDENCE/host-inventory.json" --transition legacy --evidence "$EVIDENCE/restoration-evidence.json" > "$EVIDENCE/return-to-legacy.json"
```

Only a matching plan, restored SHA and successful runtime verification permit
the transition. This command proposes the standard; it does not install it.
After operator authorization, install `proposed_standard` through the active
configuration path and resume the paused jobs. Retain `cutover_history` as well
as the new `cutover`, plan, verification output, v2 manifests and shadow receipts.
Detached HEAD prevents the existing main-only deployer from immediately undoing
the restoration; returning to main requires a separate authorized rollout.
