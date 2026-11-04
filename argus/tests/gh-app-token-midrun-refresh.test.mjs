// 11-04 P0 follow-up: a long run must not keep using the GH_TOKEN minted at slot time (PR-1729: 401 ~80 min into the run).
// Mock ghtoken prints a new fake token; no real token, no network.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

test('praiseQaTokenFor re-asks ghtoken once the refresh interval has passed', async () => {
  const d = mkdtempSync(join(tmpdir(), 'argus-midrun-'))
  const mock = join(d, 'ghtoken.py')
  writeFileSync(mock, 'import sys\nprint("ghs_fresh_" + sys.argv[2].split("/")[0], end="")\n')
  Object.assign(process.env, { PANOPTES_GHTOKEN: mock, PRAISE_QA_REFRESH_MS: '50', GH_TOKEN: 'ghs_stale', PRAISE_QA_GH_TOKEN: 'ghs_stale',
    PRAISE_QA_GH_REPOS: 'xindong/mivo-canvas-plugin', PRAISE_QA_GH_OWN_TOKEN: 'ghs_stale_own' })
  const { praiseQaTokenFor, ghEnvFor } = await import('../src/github.mjs')
  await new Promise((r) => setTimeout(r, 120))
  assert.equal(praiseQaTokenFor('xindong/mivo-canvas-plugin'), 'ghs_fresh_xindong')
  assert.equal(praiseQaTokenFor('PraiseZhu/panoptes'), 'ghs_fresh_PraiseZhu')
  assert.equal(ghEnvFor(['api', 'repos/xindong/mivo-canvas-plugin']).GH_TOKEN, 'ghs_fresh_xindong')
  assert.equal(process.env.GH_TOKEN, 'ghs_fresh_xindong')   // git / child processes spawned later inherit the fresh one
})
