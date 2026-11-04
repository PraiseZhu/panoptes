// 11-05: listTeamMembers (orgs/<org>/teams/<team>/members) must use the member PAT, never the App installation token
// (App has no org members permission -> 404). Mirrors gh_wrapper.py TEAM_MEMBERS / lib.pat_env. Fake tokens only, mock gh.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, chmodSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const d = mkdtempSync(join(tmpdir(), 'argus-teampat-'))
const mockGh = join(d, 'gh')
writeFileSync(mockGh, '#!/bin/sh\nprintf \'[{"login":"%s"}]\' "${GH_TOKEN:-none}"\n')
chmodSync(mockGh, 0o755)
Object.assign(process.env, { ARGUS_GH_BIN: mockGh, GH_TOKEN: 'ghs_app_fake', GITHUB_TOKEN: 'ghp_member_fake', GH_ENTERPRISE_TOKEN: 'x',
  PRAISE_QA_GH_TOKEN: 'ghs_app_fake', PRAISE_QA_GH_REPOS: 'xindong/mivo-canvas-plugin', PRAISE_QA_REFRESH_MS: '999999999' })
const { ghEnvFor, patEnv, listTeamMembers } = await import('../src/github.mjs')

test('orgs/<org>/teams paths get the member PAT, not the App token', () => {
  for (const p of ['orgs/xindong/teams/art-support', 'orgs/xindong/teams/art-support/members?per_page=100&page=1', '/orgs/xindong/teams/x/repos']) {
    const e = ghEnvFor(['api', '-H', 'Accept: application/vnd.github+json', p])
    assert.equal(e.GH_TOKEN, 'ghp_member_fake', p)
    assert.equal(e.GITHUB_TOKEN, undefined)
    assert.equal(e.GH_ENTERPRISE_TOKEN, undefined)
  }
  assert.equal(ghEnvFor(['api', 'repos/xindong/mivo-canvas-plugin/pulls/1']).GH_TOKEN, 'ghs_app_fake')   // repo calls unchanged
})

test('patEnv drops an installation token in GITHUB_TOKEN (falls back to gh login)', () => {
  assert.equal(patEnv({ GH_TOKEN: 'ghs_a', GITHUB_TOKEN: 'ghs_b' }).GH_TOKEN, undefined)
})

test('listTeamMembers calls gh with the PAT', async () => {
  assert.deepEqual(await listTeamMembers('xindong', 'art-support'), ['ghp_member_fake'])
})
