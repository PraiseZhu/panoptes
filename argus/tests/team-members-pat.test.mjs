// 11-05: listTeamMembers (orgs/<org>/teams/<team>/members) must use the member PAT, never the App installation token
// (App has no org members permission -> 404). #242 follow-up: in the real nightly env GITHUB_TOKEN is unset (gh-app-env.sh) and the
// `gh` on PATH is the App-minting gh_wrapper, so team calls must go to the REAL gh with patEnv; no PAT -> explicit error.
// Fake tokens and mock gh binaries only.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, chmodSync, existsSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const d = mkdtempSync(join(tmpdir(), 'argus-teampat-'))
const wrapperHit = join(d, 'wrapper-called')
const mk = (name, body) => { const f = join(d, name); writeFileSync(f, `#!/bin/sh\n${body}\n`); chmodSync(f, 0o755); return f }
// stands in for /usr/local/bin/gh (gh_wrapper.py): it would mint the App token
const wrapper = mk('gh-wrapper', `touch ${wrapperHit}; printf '[{"login":"app-%s"}]' "\${GH_TOKEN:-none}"`)
// stands in for the real gh: `auth status` succeeds only when LOGGED_IN=1 (the box login); api prints the token it got
const real = mk('gh-real', 'if [ "$1" = auth ]; then [ "$LOGGED_IN" = 1 ]; exit $?; fi\nprintf \'[{"login":"real-%s"}]\' "${GH_TOKEN:-login}"')
const realEnv = { ARGUS_GH_BIN: wrapper, PRAISE_QA_REAL_GH: real, GH_TOKEN: 'ghs_app_fake', PRAISE_QA_GH_TOKEN: 'ghs_app_fake',
  PRAISE_QA_GH_REPOS: 'xindong/mivo-canvas-plugin', PRAISE_QA_REFRESH_MS: '999999999', ARGUS_GH_RETRY_MS: '1' }
Object.assign(process.env, realEnv)
delete process.env.GITHUB_TOKEN; delete process.env.GH_ENTERPRISE_TOKEN
const { ghEnvFor, patEnv, listTeamMembers, NO_MEMBER_PAT } = await import('../src/github.mjs')
const reset = () => { rmSync(wrapperHit, { force: true }); delete process.env.GITHUB_TOKEN; delete process.env.LOGGED_IN }

test('orgs/<org>/teams paths get the member PAT env, not the App token', () => {
  process.env.GITHUB_TOKEN = 'ghp_member_fake'
  for (const p of ['orgs/xindong/teams/art-support', 'orgs/xindong/teams/art-support/members?per_page=100&page=1', '/orgs/xindong/teams/x/repos']) {
    const e = ghEnvFor(['api', '-H', 'Accept: application/vnd.github+json', p])
    assert.equal(e.GH_TOKEN, 'ghp_member_fake', p)
    assert.equal(e.GITHUB_TOKEN, undefined)
  }
  assert.equal(ghEnvFor(['api', 'repos/xindong/mivo-canvas-plugin/pulls/1']).GH_TOKEN, 'ghs_app_fake')   // repo calls unchanged
  reset()
})

test('patEnv drops an installation token in GITHUB_TOKEN (falls back to gh login)', () => {
  assert.equal(patEnv({ GH_TOKEN: 'ghs_a', GITHUB_TOKEN: 'ghs_b' }).GH_TOKEN, undefined)
})

test('listTeamMembers uses the real gh with the PAT, never the App-minting wrapper', async () => {
  process.env.GITHUB_TOKEN = 'ghp_member_fake'
  assert.deepEqual(await listTeamMembers('xindong', 'art-support'), ['real-ghp_member_fake'])
  assert.equal(existsSync(wrapperHit), false)
  reset()
})

test('real nightly env (GITHUB_TOKEN unset, GH_TOKEN=ghs_): listTeamMembers uses the real gh box login, not the wrapper', async () => {
  process.env.LOGGED_IN = '1'
  assert.deepEqual(await listTeamMembers('xindong', 'art-support'), ['real-login'])
  assert.equal(existsSync(wrapperHit), false)
  reset()
})

test('no member PAT obtainable: listTeamMembers fails with an explicit "no member PAT available" error (lands in notes), no App token call', async () => {
  await assert.rejects(listTeamMembers('xindong', 'art-support'), (err) => {
    assert.ok(err.message.startsWith(NO_MEMBER_PAT))
    assert.equal(NO_MEMBER_PAT, 'team members: no member PAT available')
    // nightly.mjs pushes `maintainers team ... not readable (${err.message.slice(0, 120)})` into health.notes
    assert.match(`not readable (${err.message.slice(0, 120)})`, /team members: no member PAT available/)
    return true
  })
  assert.equal(existsSync(wrapperHit), false)
  reset()
})
