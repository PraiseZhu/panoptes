// qa.evidence/v2 stores metadata only. Callers retain sanitized artifacts separately.
export const EVIDENCE_SCHEMA = 'qa.evidence/v2'
export const RUN_STATUSES = Object.freeze(['pending', 'running', 'passed', 'failed', 'blocked', 'timeout', 'runner_error', 'environment_unavailable', 'cancelled'])
export const STEP_STATUSES = Object.freeze(['pending', 'running', 'pass', 'fail', 'timeout', 'runner_error', 'environment_unavailable', 'skipped', 'invalid'])
export const FINDING_STATUSES = Object.freeze(['confirmed', 'pending_verification', 'pre-existing', 'flaky', 'fixed-later'])
export const ARTIFACT_KINDS = Object.freeze(['test-result', 'environment', 'coverage', 'replay', 'sanitized-log'])

/**
 * @typedef {{run_id: string, tested_sha: string, base_sha: string, artifact_refs: string[]}} EvidenceBinding
 * @typedef {{id: string, path: string, sha256: string, kind: string}} EvidenceArtifact
 * @typedef {EvidenceBinding & {id: string, status: string, phase?: string, revision_sha?: string, exit_code?: number|null, signal?: string|null, duration_ms?: number, reason_code?: string}} EvidenceStep
 * @typedef {EvidenceBinding & {id: string, check_id: string, fingerprint: string, status: string, severity?: string|null}} EvidenceFinding
 * @typedef {EvidenceBinding & {schema: 'qa.evidence/v2', status: string, artifacts: EvidenceArtifact[], steps: EvidenceStep[], findings: EvidenceFinding[], previous_version?: string}} EvidenceManifest
 */

const BINDING = ['run_id', 'tested_sha', 'base_sha', 'artifact_refs']
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/
const SHA_RE = /^[a-f0-9]{40}$/
const HASH_RE = /^[a-f0-9]{64}$/
// Defense in depth for otherwise valid identifiers and paths; never echo rejected values.
const SECRET_RE = /(?:gh[pousr]_|github_pat_|sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|xox[baprs]-|AKIA[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]+\.eyJ|-----BEGIN .*PRIVATE KEY)/i
const PRIVATE_PATH_RE = /(?:^|[/.\-_])(?:auth|credentials?|secrets?|tokens?|prompts?|uploads?|user[-_]?(?:material|content|assets?))(?:[/.\-_]|$)|(?:^|\/)\.env(?:[./]|$)/i

function reject(field, rule) {
  throw new TypeError(`Invalid qa.evidence/v2 ${field}: ${rule}`)
}

function object(value, allowed, required, field) {
  if (!value || Object.getPrototypeOf(value) !== Object.prototype) reject(field, 'expected a plain object')
  // Getters/toJSON can change the bytes after validation. Accept JSON data properties only.
  for (const key of Reflect.ownKeys(value)) {
    const descriptor = Object.getOwnPropertyDescriptor(value, key)
    if (!allowed.includes(key) || !descriptor.enumerable || !('value' in descriptor)) reject(field, 'unknown or non-data field')
  }
  for (const key of required) if (!Object.hasOwn(value, key)) reject(`${field}.${key}`, 'required')
}

function string(value, regex, field) {
  if (typeof value !== 'string' || !regex.test(value) || SECRET_RE.test(value)) reject(field, 'invalid metadata')
}

function enumeration(value, choices, field) {
  if (!choices.includes(value)) reject(field, 'unsupported status or kind')
}

function list(value, field) {
  if (!Array.isArray(value) || Object.getPrototypeOf(value) !== Array.prototype) reject(field, 'expected an array')
  // Sparse arrays or custom serializers would not round-trip as validated JSON.
  if (Reflect.ownKeys(value).length !== value.length + 1) reject(field, 'expected a dense data array')
  for (let i = 0; i < value.length; i++) {
    if (!Object.hasOwn(value, i) || !('value' in Object.getOwnPropertyDescriptor(value, String(i)))) reject(field, 'expected a dense data array')
  }
}

function refs(value, artifacts, field, required = false) {
  list(value, field)
  if (required && !value.length) reject(field, 'at least one artifact required')
  const seen = new Set()
  for (const ref of value) {
    string(ref, ID_RE, field)
    if (!artifacts.has(ref) || seen.has(ref)) reject(field, 'unknown or duplicate artifact ref')
    seen.add(ref)
  }
}

function binding(value, manifest, artifacts, field, required = false) {
  string(value.run_id, ID_RE, `${field}.run_id`)
  for (const key of ['tested_sha', 'base_sha']) string(value[key], SHA_RE, `${field}.${key}`)
  if (manifest && BINDING.slice(0, 3).some((key) => value[key] !== manifest[key])) reject(field, 'run/SHA binding mismatch')
  refs(value.artifact_refs, artifacts, `${field}.artifact_refs`, required)
}

function uniqueId(value, seen, field) {
  string(value, ID_RE, field)
  if (seen.has(value)) reject(field, 'duplicate id')
  seen.add(value)
}

/** Validate without coercion, defaults, file reads or mutation; return the original manifest. */
export function validateRunManifest(manifest) {
  object(manifest, ['schema', ...BINDING, 'status', 'artifacts', 'steps', 'findings', 'previous_version'],
    ['schema', ...BINDING, 'status', 'artifacts', 'steps', 'findings'], 'manifest')
  if (manifest.schema !== EVIDENCE_SCHEMA) reject('manifest.schema', 'unsupported schema')
  enumeration(manifest.status, RUN_STATUSES, 'manifest.status')
  list(manifest.artifacts, 'manifest.artifacts')
  const artifacts = new Set()
  for (const artifact of manifest.artifacts) {
    object(artifact, ['id', 'path', 'sha256', 'kind'], ['id', 'path', 'sha256', 'kind'], 'artifact')
    uniqueId(artifact.id, artifacts, 'artifact.id')
    string(artifact.path, /^[A-Za-z0-9_][A-Za-z0-9._/-]{0,511}$/, 'artifact.path')
    if (artifact.path.split('/').some((part) => !part || part === '.' || part === '..') || PRIVATE_PATH_RE.test(artifact.path)) reject('artifact.path', 'unsafe or private path')
    string(artifact.sha256, HASH_RE, 'artifact.sha256')
    enumeration(artifact.kind, ARTIFACT_KINDS, 'artifact.kind')
  }
  binding(manifest, null, artifacts, 'manifest', ['passed', 'failed'].includes(manifest.status))
  const steps = new Set()
  list(manifest.steps, 'manifest.steps')
  for (const step of manifest.steps) {
    object(step, [...BINDING, 'id', 'status', 'phase', 'revision_sha', 'exit_code', 'signal', 'duration_ms', 'reason_code'],
      [...BINDING, 'id', 'status'], 'step')
    uniqueId(step.id, steps, 'step.id')
    enumeration(step.status, STEP_STATUSES, 'step.status')
    binding(step, manifest, artifacts, 'step', ['pass', 'fail'].includes(step.status))
    if (Object.hasOwn(step, 'phase')) enumeration(step.phase, ['head', 'rerun', 'base', 'latest', 'replay'], 'step.phase')
    if (Object.hasOwn(step, 'revision_sha')) {
      string(step.revision_sha, SHA_RE, 'step.revision_sha')
      const expected = step.phase === 'base' ? manifest.base_sha : ['head', 'rerun'].includes(step.phase) ? manifest.tested_sha : null
      if (expected && step.revision_sha !== expected) reject('step.revision_sha', 'phase/SHA mismatch')
    }
    if (Object.hasOwn(step, 'exit_code') && step.exit_code !== null && !Number.isSafeInteger(step.exit_code)) reject('step.exit_code', 'expected integer or null')
    if (Object.hasOwn(step, 'signal') && step.signal !== null) string(step.signal, /^SIG[A-Z0-9]{1,16}$/, 'step.signal')
    if (Object.hasOwn(step, 'duration_ms') && (!Number.isSafeInteger(step.duration_ms) || step.duration_ms < 0)) reject('step.duration_ms', 'expected nonnegative integer')
    if (Object.hasOwn(step, 'reason_code')) string(step.reason_code, /^[a-z][a-z0-9_]{0,63}$/, 'step.reason_code')
  }
  const findings = new Set()
  list(manifest.findings, 'manifest.findings')
  for (const finding of manifest.findings) {
    object(finding, [...BINDING, 'id', 'check_id', 'fingerprint', 'status', 'severity'],
      [...BINDING, 'id', 'check_id', 'fingerprint', 'status'], 'finding')
    uniqueId(finding.id, findings, 'finding.id')
    binding(finding, manifest, artifacts, 'finding', true)
    enumeration(finding.status, FINDING_STATUSES, 'finding.status')
    string(finding.check_id, ID_RE, 'finding.check_id')
    if (!steps.has(finding.check_id)) reject('finding.check_id', 'unknown step')
    string(finding.fingerprint, HASH_RE, 'finding.fingerprint')
    if (Object.hasOwn(finding, 'severity') && finding.severity !== null) enumeration(finding.severity, ['P0', 'P1', 'P2', 'P3'], 'finding.severity')
  }
  if (Object.hasOwn(manifest, 'previous_version')) {
    string(manifest.previous_version, /^history\/[A-Za-z0-9][A-Za-z0-9._-]*\.v[1-9][0-9]*\.json$/, 'manifest.previous_version')
  }
  return manifest
}

/** Construct a detached JSON snapshot; only the schema tag is supplied by the constructor. */
export function createRunManifest(input) {
  const manifest = { schema: EVIDENCE_SCHEMA, ...input }
  validateRunManifest(manifest)
  return JSON.parse(JSON.stringify(manifest))
}

/** Pure metadata summary; absent legacy evidence is never silently counted as zero. */
export function summarizeRunManifest(manifest) {
  validateRunManifest(manifest)
  const count = (items) => items.reduce((out, item) => ({ ...out, [item.status]: (out[item.status] ?? 0) + 1 }), {})
  return {
    schema: manifest.schema, run_id: manifest.run_id, tested_sha: manifest.tested_sha, base_sha: manifest.base_sha,
    status: manifest.status, artifacts: manifest.artifacts.length, steps: count(manifest.steps), findings: count(manifest.findings),
  }
}
