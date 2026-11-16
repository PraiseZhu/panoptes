#!/usr/bin/env node
// Read-only post-deploy consistency check. It never edits a runtime or a receipt.
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { readJson, validateFleet, validateStandard } from './qa-fleet-preflight.mjs'

export function sha256File(file) { return createHash('sha256').update(readFileSync(file)).digest('hex') }

export function verify({ fleet, standard, manifest, expectedSourceSha, expectedRuntimeSha, expectedManifestHash } = {}) {
  const errors = [...validateFleet(fleet, { root: process.cwd() }), ...validateStandard(standard)]
  if (!manifest || typeof manifest !== 'object') errors.push({ code: 'manifest_missing', detail: '缺 runtime manifest' })
  if (expectedSourceSha && manifest?.source_sha !== expectedSourceSha) errors.push({ code: 'source_sha_drift', detail: `source SHA ${manifest?.source_sha ?? 'missing'} != ${expectedSourceSha}` })
  if (expectedRuntimeSha && manifest?.runtime_sha !== expectedRuntimeSha) errors.push({ code: 'runtime_sha_drift', detail: `runtime SHA ${manifest?.runtime_sha ?? 'missing'} != ${expectedRuntimeSha}` })
  if (expectedManifestHash && manifest?.manifest_hash !== expectedManifestHash) errors.push({ code: 'manifest_hash_drift', detail: `manifest hash ${manifest?.manifest_hash ?? 'missing'} != ${expectedManifestHash}` })
  if (manifest?.standard_version !== standard?.version) errors.push({ code: 'standard_version_drift', detail: `runtime=${manifest?.standard_version ?? 'missing'} config=${standard?.version ?? 'missing'}` })
  return { ok: errors.length === 0, errors, source_sha: manifest?.source_sha ?? null, runtime_sha: manifest?.runtime_sha ?? null, standard_version: manifest?.standard_version ?? null }
}

function arg(args, name, fallback) { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : fallback }
export function main(argv = process.argv.slice(2)) {
  const fleetFile = resolve(arg(argv, '--fleet', new URL('../../pi/conf/qa-fleet.json', import.meta.url).pathname))
  const standardFile = resolve(arg(argv, '--standard', new URL('../../pi/conf/qa-standard.json', import.meta.url).pathname))
  const manifestFile = arg(argv, '--manifest', null)
  const result = verify({ fleet: readJson(fleetFile), standard: readJson(standardFile), manifest: manifestFile ? readJson(resolve(manifestFile)) : null,
    expectedSourceSha: arg(argv, '--source-sha', null), expectedRuntimeSha: arg(argv, '--runtime-sha', null), expectedManifestHash: arg(argv, '--manifest-hash', null) })
  process.stdout.write(JSON.stringify(result, null, 2) + '\n'); return result.ok ? 0 : 1
}
if (import.meta.url === `file://${process.argv[1]}`) process.exitCode = main()
