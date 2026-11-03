// 11-03 integration-branch QA: JS half of the shared base-branch contract (Python half: pi/test/test_basegate_contract.py).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { matchesSkipBase, shouldTestBase, mainOnly } from '../src/config.mjs'

const cases = JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'fixtures', 'skip-base-cases.json'), 'utf8')).cases

test('shared skip-base cases: matchesSkipBase / shouldTestBase agree with the Python side', () => {
  for (const c of cases) {
    assert.equal(matchesSkipBase(c.base, c.globs), c.skip, JSON.stringify(c))
    assert.equal(shouldTestBase(c.base, c.globs, { mainOnly: false }), c.test, JSON.stringify(c))
    assert.equal(shouldTestBase(c.base, c.globs, { mainOnly: true }), c.test_main_only, JSON.stringify(c))
  }
})

test('PANOPTES_MAIN_ONLY=1 or $PI_STATE/panoptes-main-only switches back to main only', () => {
  const d = mkdtempSync(join(tmpdir(), 'argus-mainonly-'))
  assert.equal(mainOnly({ PI_STATE: d }), false)
  assert.equal(mainOnly({ PI_STATE: d, PANOPTES_MAIN_ONLY: '1' }), true)
  writeFileSync(join(d, 'panoptes-main-only'), '')
  assert.equal(mainOnly({ PI_STATE: d }), true)
  rmSync(join(d, 'panoptes-main-only'))
  assert.equal(mainOnly({ PI_STATE: d }), false)
})
