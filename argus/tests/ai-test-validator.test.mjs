import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync, symlinkSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import { validateAiTest, classifyAiTestFailure, readValidationResult } from '../src/ai-test-validator.mjs'

const repo = fileURLToPath(new URL('../../', import.meta.url))
const scratch = join(repo, '_tmp')
mkdirSync(scratch, { recursive: true })
const runner = fileURLToPath(new URL('../src/ai-test-run.mjs', import.meta.url))
const source = 'export const sum = (xs) => xs.reduce((a, b) => a + b, 0)\n'
const content = "import test from 'node:test'\nimport assert from 'node:assert/strict'\nimport { sum } from './sum.mjs'\ntest('empty', () => assert.equal(sum([]), 0))\n"
const good = { filePath: 'sum.argus.test.mjs', content, mutation: { file: 'sum.mjs', find: ', 0)', replace: ', 1)' }, runCmd: [process.execPath, '--test', '{file}'], purposeZh: 'sum' }
function fixture(t) {
  const root = mkdtempSync(join(scratch, 'ai-validator-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  writeFileSync(join(root, 'sum.mjs'), source)
  return root
}
function check(root, spec = good, options = {}) { return validateAiTest(spec, { root, isolated: true, ...options }) }
function run(root, spec = good) {
  const path = join(root, 'spec.json')
  writeFileSync(path, JSON.stringify(spec))
  const { NODE_TEST_CONTEXT, ...env } = process.env
  const r = spawnSync(process.execPath, [runner, path], { cwd: root, env, encoding: 'utf8', timeout: 30_000 })
  return { ...r, validation: readValidationResult(r.stdout) }
}

test('preflight parses JS and TypeScript without executing target or test code', (t) => {
  const root = fixture(t)
  assert.equal(check(root).status, 'valid')
  assert.equal(check(root, { ...good, filePath: 'sum.argus.test.ts', content: content.replace('sum([])', 'sum([] as number[])') }).status, 'valid')
  writeFileSync(join(root, 'sum.mjs'), source + "throw new Error('must never execute during preflight')\n")
  assert.equal(check(root).status, 'valid')
  assert.equal(check(root, { ...good, content: 'const = broken' }).kind, 'parse')
  assert.equal(check(root, { ...good, content: '' }).kind, 'parse')
  assert.equal(existsSync(join(root, good.filePath)), false)
})

test('missing import/export and wrong target are invalid before writing a test', (t) => {
  const root = fixture(t)
  assert.equal(check(root, { ...good, content: content.replace('./sum.mjs', './missing.mjs') }).kind, 'missing-module')
  assert.equal(check(root, { ...good, content: content.replaceAll('sum', 'missing') .replace('./missing.mjs', './sum.mjs') }).kind, 'missing-module')
  writeFileSync(join(root, 'other.mjs'), source)
  assert.equal(check(root, { ...good, mutation: { ...good.mutation, file: 'other.mjs' } }).kind, 'target')
  assert.equal(check(root, good, { allowedTargets: ['other.mjs'] }).kind, 'target')
  const r = run(root, { ...good, content: content.replace('./sum.mjs', './missing.mjs') })
  assert.equal(r.status, 4, r.stdout + r.stderr)
  assert.equal(r.validation.status, 'invalid')
  assert.equal(existsSync(join(root, good.filePath)), false)
})

test('preflight blocks unisolated roots, path traversal, private paths and symlink escapes', (t) => {
  const root = fixture(t)
  assert.equal(check(root, good, { isolated: false }).status, 'blocked')
  // These files deliberately do not exist; no private runtime content is accessed.
  for (const path of ['../auth.json', '/home/prod/source.mjs', '.env', '.codex/auth.json', 'secrets/key.pem', 'nested/../../sum.mjs', 'bad\\path.mjs']) {
    assert.equal(check(root, { ...good, mutation: { ...good.mutation, file: path } }).kind, 'target', path)
    assert.equal(check(root, { ...good, filePath: path }).kind, 'target', path)
  }
  symlinkSync(dirname(root), join(root, 'outside'))
  assert.equal(check(root, { ...good, mutation: { ...good.mutation, file: 'outside/no-read.mjs' } }).kind, 'target')
  assert.equal(check(root, { ...good, filePath: 'outside/new.test.mjs' }).kind, 'target')
})

test('network, token, external paths and child process requests never execute', (t) => {
  const root = fixture(t)
  for (const prefix of ["fetch('https://example.invalid')", "import http from 'node:http'", "import { exec } from 'child_process'", 'const key = process.env.API_KEY', 'const key = process["env"].API_KEY', "const x = '/Users/prod/private'", "import '/home/prod/source.mjs'"]) {
    const spec = { ...good, content: prefix + '\n' + content }
    assert.equal(check(root, spec).kind, 'side-effect', prefix)
    const r = run(root, spec)
    assert.equal(r.status, 4, r.stdout + r.stderr)
    assert.equal(r.validation.kind, 'side-effect')
    assert.equal(existsSync(join(root, good.filePath)), false)
  }
  assert.equal(readFileSync(join(root, 'sum.mjs'), 'utf8'), source)
})

test('all invalid runtime signatures and assertion counterexamples', () => {
  const cases = [
    ["ENOENT: no such file or directory", 'missing-file'],
    ["Error: Cannot find module 'missing'", 'missing-module'],
    ["Error: Cannot find package 'missing'", 'missing-module'],
    ['Failed to resolve import "missing"', 'missing-module'],
    ['Failed to load url missing', 'missing-module'],
    ['Does the file exist?', 'missing-module'],
    ["does not provide an export named 'missing'", 'missing-module'],
    ['No "now" export is defined on the "clock" mock', 'missing-module'],
    ['TypeError [ERR_INVALID_ARG_TYPE]: argument must be an array', 'wrong-arguments'],
    ['TypeError [ERR_INVALID_ARG_VALUE]: bad value', 'wrong-arguments'],
    ['TypeError [ERR_MISSING_ARGS]: argument is required', 'wrong-arguments'],
    ['ReferenceError: fixture is not defined\n at sum.argus.test.mjs:5:1', 'initialization'],
    ['TypeError: fixture.read is not a function\n at sum.argus.test.mjs:5:1', 'initialization'],
  ]
  for (const [tail, kind] of cases) {
    assert.equal(classifyAiTestFailure(tail, { ...good, content: good.content + '\nfixture.read()' })?.kind, kind, tail)
    assert.equal(classifyAiTestFailure('AssertionError: expected result to equal 1\n' + tail, good), null, tail)
  }
  assert.equal(classifyAiTestFailure('TypeError: value.read is not a function\n at product.mjs:5:1\n at sum.argus.test.mjs:5:1', good), null)
})

test('missing fixture method is discarded; genuine assertion failure stays a candidate', (t) => {
  const root = fixture(t)
  writeFileSync(join(root, 'sum.mjs'), 'export function sum(client) { return client.now() }\n')
  const spec = { ...good, content: content.replace('sum([])', 'sum({})'), mutation: { file: 'sum.mjs', find: 'client.now()', replace: '1' } }
  const bad = run(root, spec)
  assert.equal(bad.status, 4, bad.stdout + bad.stderr)
  assert.equal(bad.validation.kind, 'fixture-gap')
  writeFileSync(join(root, 'sum.mjs'), source)
  const failed = run(root, { ...good, content: content.replace('sum([]), 0', 'sum([]), 5') })
  assert.equal(failed.status, 1, failed.stdout + failed.stderr)
  assert.equal(failed.validation.status, 'valid')
  assert.equal(failed.validation.product_candidate, true)
})

test('controlled mutation must be caught by an assertion, restores source and removes test', (t) => {
  const root = fixture(t)
  const r = run(root)
  assert.equal(r.status, 0, r.stdout + r.stderr)
  assert.deepEqual(r.validation, { status: 'valid', mutation: { status: 'caught' } })
  assert.equal(readFileSync(join(root, 'sum.mjs'), 'utf8'), source)
  assert.equal(existsSync(join(root, good.filePath)), false)
  const inert = run(root, { ...good, mutation: { ...good.mutation, replace: ', 0 /* unchanged */)' } })
  assert.equal(inert.status, 4)
  assert.equal(inert.validation.kind, 'mutation')
  const broken = check(root, { ...good, mutation: { ...good.mutation, replace: ', ) } broken' } })
  assert.equal(broken.status, 'invalid')
  assert.equal(broken.kind, 'mutation')
})

test('mutation not_applicable records a reason; infrastructure failure is blocked', (t) => {
  const root = fixture(t)
  const mutation = { status: 'not_applicable', file: 'sum.mjs', reason: 'target needs an unavailable native runtime for mutation replay' }
  const r = run(root, { ...good, mutation })
  assert.equal(r.status, 0, r.stdout + r.stderr)
  assert.deepEqual(r.validation.mutation, { status: 'not_applicable', reason: mutation.reason })
  assert.equal(check(root, { ...good, mutation: { ...mutation, reason: '' } }).kind, 'mutation')
  const blocked = run(root, { ...good, runCmd: ['nonexistent-ai-test-command', '{file}'] })
  assert.equal(blocked.status, 2)
  assert.equal(blocked.validation.status, 'blocked')
  assert.equal(readFileSync(join(root, 'sum.mjs'), 'utf8'), source)
})
