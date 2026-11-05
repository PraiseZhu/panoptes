// 11-05 PR-1740: getBranchHead 遇 HTTP 401 时强制重铸 App token 一次再读；
// 仍失败则抛出可识别错误（消息含 branch + status），调用方按「主干没复查」降级，不拖垮整轮。
// mocks only — 不联网、不读真 token / app.pem。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const root = mkdtempSync(join(tmpdir(), 'argus-branch-401-'))
const GH_BIN = join(root, 'fake-gh.mjs')
const GHTOKEN = join(root, 'ghtoken.py')
const MINT_STATE = join(root, 'mint-state.json')
const GH_STATE = join(root, 'gh-state.json')

writeFileSync(GHTOKEN, `import json, sys
from pathlib import Path
p = Path(${JSON.stringify(MINT_STATE)})
s = json.loads(p.read_text())
s["mints"] = s.get("mints", 0) + 1
p.write_text(json.dumps(s))
print("ghs_minted_" + str(s["mints"]), end="")
`)

writeFileSync(GH_BIN, `#!/usr/bin/env node
import { readFileSync, writeFileSync } from 'node:fs'
const file = process.env.ARGUS_E2E_GH_STATE
const state = JSON.parse(readFileSync(file, 'utf8'))
const args = process.argv.slice(2)
const route = args.find((a) => a.startsWith('repos/')) ?? null
state.calls.push({ route, token: process.env.GH_TOKEN || null })
if (/\\/branches\\//.test(route || '')) {
  state.branchAttempts = (state.branchAttempts || 0) + 1
  writeFileSync(file, JSON.stringify(state))
  if (state.branchAttempts <= (state.failCount || 0)) {
    process.stderr.write('gh: Bad credentials (HTTP 401)')
    process.exit(1)
  }
  process.stdout.write(JSON.stringify({ commit: { sha: state.sha } }))
  process.exit(0)
}
writeFileSync(file, JSON.stringify(state))
process.stderr.write('fake gh: undeclared route ' + route)
process.exit(2)
`, { mode: 0o755 })

process.env.ARGUS_GH_BIN = GH_BIN
process.env.ARGUS_GH_RETRY_MS = '1'
process.env.ARGUS_SECRETS_FILE = join(root, 'intentionally-no-secrets.env')
process.env.ARGUS_E2E_GH_STATE = GH_STATE
process.env.PANOPTES_GHTOKEN = GHTOKEN
// 大节流：普通 refresh 不会重铸；只有 getBranchHead 的 force:true 才会
process.env.PRAISE_QA_REFRESH_MS = '999999999'
process.env.PRAISE_QA_GH_TOKEN = 'ghs_stale'
process.env.GH_TOKEN = 'ghs_stale'
process.env.PRAISE_QA_GH_REPOS = 'xindong/mivo-canvas-plugin'

const { getBranchHead } = await import('../src/github.mjs')

after(() => rmSync(root, { recursive: true, force: true }))

function reset({ failCount, sha = 'abc123deadbeef' }) {
  writeFileSync(MINT_STATE, JSON.stringify({ mints: 0 }))
  writeFileSync(GH_STATE, JSON.stringify({ failCount, sha, calls: [], branchAttempts: 0 }))
  process.env.PRAISE_QA_GH_TOKEN = 'ghs_stale'
  process.env.GH_TOKEN = 'ghs_stale'
}

const REPO = 'xindong/mivo-canvas-plugin'
const BRANCH = 'integration/canvas-s2'

test('401 then 200 succeeds after one re-mint', async () => {
  reset({ failCount: 1 })
  const sha = await getBranchHead(REPO, BRANCH)
  assert.equal(sha, 'abc123deadbeef')
  const mint = JSON.parse(readFileSync(MINT_STATE, 'utf8'))
  assert.equal(mint.mints, 1, `exactly one re-mint, got ${mint.mints}`)
  const gh = JSON.parse(readFileSync(GH_STATE, 'utf8'))
  assert.equal(gh.branchAttempts, 2, `one retry (2 attempts), got ${gh.branchAttempts}`)
  assert.equal(gh.calls[1]?.token, 'ghs_minted_1', `retry must use reminted token, got ${gh.calls[1]?.token}`)
})

test('401 twice gives recognizable error, no crash, exactly one retry', async () => {
  reset({ failCount: 99 })
  let threw = null
  try {
    await getBranchHead(REPO, BRANCH)
  } catch (err) {
    threw = err
  }
  assert.ok(threw, 'must surface a recognizable error (not crash / not silent null)')
  assert.match(String(threw.message), /integration\/canvas-s2/)
  assert.match(String(threw.message), /401/)
  assert.equal(threw.branchHeadAuthFailed, true)
  assert.equal(threw.httpStatus, 401)
  assert.equal(threw.branch, BRANCH)
  const mint = JSON.parse(readFileSync(MINT_STATE, 'utf8'))
  assert.equal(mint.mints, 1, `exactly one re-mint on persistent 401, got ${mint.mints}`)
  const gh = JSON.parse(readFileSync(GH_STATE, 'utf8'))
  assert.equal(gh.branchAttempts, 2, `exactly one retry (2 attempts), got ${gh.branchAttempts}`)
})
