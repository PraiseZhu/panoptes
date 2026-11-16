# QA standard brief

Version: `qa-standard/v1`. The active mode is read from `pi/conf/qa-standard.json`.

## qa

Run the existing QA command and keep the source SHA, runtime SHA, policy version,
run id and evidence manifest together. A report is evidence only when its required
fields are present and its artifacts are hash-bound.

## repair

Open a product fix only for a `confirmed` finding with complete head/base evidence.
Pending, environment, runner and invalid AI-test results are not product findings.

## retest

Replay the named case on the original and fix revisions. Record `pass`, `still-fails`,
`blocked` or `inconclusive` with the same run and artifact references.

## ops

Check lane wiring, source/runtime/policy consistency and recent receipts. Report drift;
do not silently repair a missing or stale manifest.

## outbound

Only confirmed findings may leave the box. In `strict`, reject pending, environment,
runner and invalid AI-test results and emit an alert for every rejected action.
