# QA fleet contract

`pi/conf/qa-fleet.json`, `qa-standard.json` and `qa-standard-brief.md` are one versioned contract. The five lanes are `qa`, `repair`, `retest`, `ops` and `outbound`; each entry names its scheduler, permissions and health signals. The files contain no credentials, prompt text or host specific paths.

Every scheduler invocation creates `out/qa-fleet/runs/fleet-<uuid>.json` before work starts. The receipt freezes `run_id`, lane, job, trigger, source SHA, runtime SHA, standard version, mode and the SHA-256 hash of the two JSON policies. Completion adds the exit code, completion time and, when available, a hash bound report with `all_discovered_tested` and report run id. A zero exit code without a report remains incomplete.

`fleet_status`, `stdcheck` and `watchdog` consume the same receipts. Missing lanes, stale or running receipts, source/runtime/policy drift, report hash mismatch, missing outbound audit, rejected outbound actions and strict failures are visible errors. Outbound checks re-read the original v2 manifest and artifact hashes at the action boundary.

The mode is `shadow` by default. `canary` requires seven consecutive paired natural days of legacy/current receipts with a shared source SHA and policy version. `strict` additionally requires a complete receipt bundle for every lane and a rollback drill. `rollback` disables product outbound and preserves manifests, shadow receipts and cutover records.
