# QA fleet rollout

Deployment is performed by the existing `opsdeploy` path after review. This PR does not SSH to the Grokbot host, edit cron, change `$QA_HOME`, or switch the production mode. `qa-fleet-preflight.mjs` is a read only gate: it requires full source/runtime SHAs, all lane entries, cron inventory, host permissions and the `git`, Node 24, Python and bubblewrap dependencies. The host inventory is supplied by the operator and is never inferred from a passing CI job.

Run the rollout in this order:

1. Keep `qa-standard.json` in `shadow`. Run the existing QA and Pi schedulers and retain one receipt per lane each day.
2. Run `qa-fleet-shadow-compare.mjs` with seven paired, consecutive natural dates. Every P0/P1 difference needs a non-empty explanation. Missing, duplicate, invalid, stale or differently bound inputs fail closed.
3. Prepare canary evidence for each lane. The scheduler receipt, report, `fleet_status`, `stdcheck` and outbound audit must share run id, source SHA, runtime SHA, policy version, manifest hash and canary mode. Reports must state `all_discovered_tested=true` and outbound missing-evidence count must be zero.
4. Exercise rollback using a complete previous source SHA. The drill records a receipt and preserves v2 manifests, shadow history and cutover records; it does not delete evidence.
5. Only the operator with the current deployment authorization may write a cutover artifact and change mode. `qa-fleet-preflight.mjs` then validates the complete evidence bundle before `strict` is considered.

After the first strict run, the operator records who checked `fleet_status`, `stdcheck`, the outbound audit and the report, with timestamps and links to the receipts. Seven days of shadow and the canary/rollback receipts are operational evidence; GitHub Actions status alone cannot satisfy those checks.
