import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRunManifest, validateRunManifest, summarizeRunManifest, EVIDENCE_SCHEMA, RUN_STATUSES, STEP_STATUSES, FINDING_STATUSES, ARTIFACT_KINDS } from '../src/run-contract.mjs'

const fixture = () => JSON.parse(readFileSync(new URL('./fixtures/evidence-v2.json', import.meta.url), 'utf8'))

test('v2 fixture constructs a detached manifest and produces only metadata summary', () => {
  const input = fixture()
  delete input.schema
  const manifest = createRunManifest(input)
  assert.equal(manifest.schema, EVIDENCE_SCHEMA)
  assert.equal(validateRunManifest(manifest), manifest)
  assert.deepEqual(summarizeRunManifest(manifest), {
    schema: EVIDENCE_SCHEMA, run_id: input.run_id, tested_sha: input.tested_sha, base_sha: input.base_sha,
    status: 'failed', artifacts: 2, steps: { fail: 1, pass: 1 }, findings: { pending_verification: 1 },
  })
  input.steps[0].status = 'pass'
  input.artifacts[0].path = 'changed.json'
  assert.equal(manifest.steps[0].status, 'fail')
  assert.notEqual(manifest.artifacts[0].path, input.artifacts[0].path)
})

test('run, step and finding require explicit run_id/tested_sha/base_sha without defaults', () => {
  for (const location of ['manifest', 'step', 'finding']) {
    for (const key of ['run_id', 'tested_sha', 'base_sha', 'artifact_refs']) {
      const doc = fixture()
      const entry = location === 'manifest' ? doc : location === 'step' ? doc.steps[0] : doc.findings[0]
      delete entry[key]
      const before = structuredClone(doc)
      assert.throws(() => createRunManifest(doc), /required/)
      assert.deepEqual(doc, before, 'failure must not fill any evidence')
    }
  }
  for (const bad of [null, '', 'HEAD', 'abc123', 'A'.repeat(40), 0]) {
    for (const key of ['tested_sha', 'base_sha']) {
      const doc = fixture(); doc[key] = bad
      assert.throws(() => validateRunManifest(doc), /invalid metadata/)
    }
  }
})

test('all documented state enums work; unknown states and kinds fail closed', () => {
  for (const status of RUN_STATUSES) {
    const doc = fixture(); doc.status = status
    validateRunManifest(doc)
  }
  for (const status of STEP_STATUSES) {
    const doc = fixture(); doc.steps[0].status = status
    validateRunManifest(doc)
  }
  for (const status of FINDING_STATUSES) {
    const doc = fixture(); doc.findings[0].status = status
    validateRunManifest(doc)
  }
  for (const kind of ARTIFACT_KINDS) {
    const doc = fixture(); doc.artifacts[0].kind = kind
    validateRunManifest(doc)
  }
  for (const mutate of [
    (d) => { d.status = 'clean' }, (d) => { d.steps[0].status = 'clean' },
    (d) => { d.findings[0].status = 'plausible' }, (d) => { d.artifacts[0].kind = 'raw-log' },
    (d) => { d.schema = 'qa.evidence/v3' }, (d) => { d.steps[0].phase = 'guess' },
  ]) {
    const doc = fixture(); mutate(doc)
    assert.throws(() => validateRunManifest(doc), /unsupported/)
  }
})

test('cross-run/SHA bindings and phase revision mismatches are rejected', () => {
  for (const location of ['steps', 'findings']) {
    for (const key of ['run_id', 'tested_sha', 'base_sha']) {
      const doc = fixture(); doc[location][0][key] = key === 'run_id' ? 'another-run' : '3'.repeat(40)
      assert.throws(() => validateRunManifest(doc), /binding mismatch/)
    }
  }
  const doc = fixture()
  doc.steps[1].revision_sha = doc.tested_sha
  assert.throws(() => validateRunManifest(doc), /phase\/SHA mismatch/)
  doc.steps[1].phase = 'latest'
  validateRunManifest(doc)
})

test('artifact references must resolve uniquely; executed results and findings require evidence', () => {
  for (const location of ['manifest', 'step', 'finding']) {
    for (const refs of [['missing'], ['head-result', 'head-result'], [{}], [], null]) {
      const doc = fixture()
      const entry = location === 'manifest' ? doc : location === 'step' ? doc.steps[0] : doc.findings[0]
      entry.artifact_refs = refs
      assert.throws(() => validateRunManifest(doc), /artifact|array|metadata/)
    }
  }
  for (const collection of ['steps', 'findings', 'artifacts']) {
    const doc = fixture(); doc[collection].push(structuredClone(doc[collection][0]))
    assert.throws(() => validateRunManifest(doc), /duplicate id/)
  }
  const unknown = fixture(); unknown.findings[0].check_id = 'missing-step'
  assert.throws(() => validateRunManifest(unknown), /unknown step/)
  const pending = fixture()
  pending.status = 'environment_unavailable'; pending.artifact_refs = []; pending.artifacts = []
  pending.steps = []; pending.findings = []
  validateRunManifest(pending) // unavailable records explicitly carry no evidence, not a fake pass.
})

test('artifact metadata never accepts absolute/traversal/secret paths or inline payloads', () => {
  for (const path of ['/Users/person/file', '../result.json', 'run/../result.json', 'run/./x', 'run//x',
    'C:\\users\\x', 'https://example.test/x', 'run/%2e%2e/x', 'run/result?key=x',
    'run/.env', 'run/auth.json', 'run/credentials.json', 'run/prompt.txt', 'uploads/image.png', 'run/user-material.png']) {
    const doc = fixture(); doc.artifacts[0].path = path
    assert.throws(() => validateRunManifest(doc), /artifact.path/)
  }
  for (const hash of ['abc', 'A'.repeat(64), null]) {
    const doc = fixture(); doc.artifacts[0].sha256 = hash
    assert.throws(() => validateRunManifest(doc), /sha256/)
  }
})

test('sensitive and unknown fields at every level are rejected without echoing their content', () => {
  const privateContent = 'private user image and full external error: do not persist'
  for (const select of [(d) => d, (d) => d.steps[0], (d) => d.findings[0], (d) => d.artifacts[0]]) {
    for (const key of ['token', 'api_key', 'prompt', 'user_material', 'error', 'stack', 'content', privateContent]) {
      const doc = fixture(); select(doc)[key] = privateContent
      assert.throws(() => validateRunManifest(doc), (err) => {
        assert.ok(!err.message.includes(privateContent))
        return /unknown or non-data field/.test(err.message)
      })
    }
  }
  // Synthetic values assembled in-memory, never a live credential.
  for (const secret of ['gh' + 'p_' + 'x'.repeat(36), 'sk' + '-proj-' + 'x'.repeat(32), 'eyJabc.eyJdef.sig']) {
    const doc = fixture(); doc.run_id = secret
    assert.throws(() => validateRunManifest(doc), (err) => !err.message.includes(secret))
  }
  const text = fixture(); text.steps[0].reason_code = privateContent
  assert.throws(() => validateRunManifest(text), /reason_code/)
})

test('only serializable JSON data is accepted; serializers cannot bypass payload rejection', () => {
  for (const mutate of [
    (d) => { d.toJSON = () => ({ prompt: 'private' }) },
    (d) => { d.steps.toJSON = () => [{ prompt: 'private' }] },
    (d) => { delete d.steps[0] },
    (d) => { d.steps[0].duration_ms = NaN },
    (d) => { d.steps[0].exit_code = Infinity },
    (d) => { d.steps[0].duration_ms = -1 },
    (d) => { Object.defineProperty(d.steps[0], 'signal', { get: () => 'SIGTERM', enumerable: true }) },
    (d) => { Object.setPrototypeOf(d.steps, { toJSON: () => ['private'] }) },
  ]) {
    const doc = fixture(); mutate(doc)
    assert.throws(() => validateRunManifest(doc), TypeError)
  }
})
