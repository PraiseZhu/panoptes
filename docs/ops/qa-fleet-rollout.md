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
