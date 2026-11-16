#!/usr/bin/env node
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { ROOT, SHA, HASH, readJson, validateFleet, validateStandard } from './qa-fleet-preflight.mjs'
export function sha256File(file) { return createHash('sha256').update(readFileSync(file)).digest('hex') }
export function configHash(fleetFile, standardFile) { return createHash('sha256').update(readFileSync(fleetFile)).update(Buffer.from([0])).update(readFileSync(standardFile)).digest('hex') }
export function verify({ fleet, standard, manifest, expectedSourceSha, expectedRuntimeSha, expectedManifestHash, root = ROOT } = {}) {
  const errors = [...validateFleet(fleet, { root }), ...validateStandard(standard)]
  const bad = code => errors.push({ code, detail: code })
  if (!SHA.test(expectedSourceSha) || !SHA.test(expectedRuntimeSha) || !HASH.test(expectedManifestHash)) bad('expected_binding_missing')
  if (manifest?.schema !== 'qa.fleet.run/v1' || !manifest.run_id) bad('manifest_missing')
  if (!SHA.test(manifest?.source_sha) || manifest?.source_sha !== expectedSourceSha) bad('source_sha_drift')
  if (!SHA.test(manifest?.runtime_sha) || manifest?.runtime_sha !== expectedRuntimeSha || manifest?.runtime_sha !== manifest?.source_sha) bad('runtime_sha_drift')
  if (!HASH.test(manifest?.manifest_hash) || manifest?.manifest_hash !== expectedManifestHash) bad('manifest_hash_drift')
  if (manifest?.standard_version !== standard?.version || fleet?.standard_version !== standard?.version || manifest?.mode !== standard?.mode) bad('standard_version_drift')
  return { ok: errors.length === 0, errors, run_id: manifest?.run_id, source_sha: manifest?.source_sha, runtime_sha: manifest?.runtime_sha, standard_version: manifest?.standard_version }
}
export function main(argv = process.argv.slice(2)) {
  const arg = (k, fallback) => argv.includes(k) ? argv[argv.indexOf(k) + 1] : fallback
  try {
    const root = resolve(arg('--root', ROOT)), ff = arg('--fleet', resolve(root, 'pi/conf/qa-fleet.json')), sf = arg('--standard', resolve(root, 'pi/conf/qa-standard.json'))
    const result = verify({ fleet: readJson(ff), standard: readJson(sf), manifest: readJson(arg('--manifest', '')), root, expectedSourceSha: arg('--source-sha'), expectedRuntimeSha: arg('--runtime-sha'), expectedManifestHash: configHash(ff, sf) })
    process.stdout.write(JSON.stringify(result, null, 2) + '\n'); return result.ok ? 0 : 1
  } catch { process.stdout.write('{"ok":false,"errors":[{"code":"config_unavailable"}]}\n'); return 1 }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) process.exitCode = main()
