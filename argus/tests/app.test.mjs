// GitHub App 身份：App 装了的仓走安装令牌，没装的仓走 gh；令牌缓存复用
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { generateKeyPairSync, createVerify } from 'node:crypto'

const here = dirname(fileURLToPath(import.meta.url))
const root = mkdtempSync(join(tmpdir(), 'argus-app-'))
process.env.ARGUS_ROOT = root
process.env.ARGUS_GH_BIN = join(here, 'fake-gh.mjs')
process.env.ARGUS_GH_RETRY_MS = '1'
process.env.ARGUS_GH_API = 'https://api.test'
const statePath = join(root, 'gh.json')
process.env.FAKE_GH_STATE = statePath
writeFileSync(statePath, JSON.stringify({ issues: [{ number: 7, state: 'open', title: 'via gh', body: '', user: { login: 'PraiseZhu' } }], comments: {}, faults: [] }))

const { publicKey, privateKey } = generateKeyPairSync('rsa', { modulusLength: 2048 })
const keyFile = join(root, 'app.pem')
writeFileSync(keyFile, privateKey.export({ type: 'pkcs1', format: 'pem' }))
const secretsFile = join(root, 'secrets.env')
writeFileSync(secretsFile, `ARGUS_GH_APP_ID=42\nARGUS_GH_APP_INSTALLATION_ID=99\nARGUS_GH_APP_SLUG=argus-qa\nARGUS_GH_APP_KEY_FILE=${keyFile}\n`, { mode: 0o600 })
process.env.ARGUS_SECRETS_FILE = secretsFile

const calls = []
globalThis.fetch = async (url, init) => {
  const u = String(url)
  calls.push({ url: u, method: init.method, auth: init.headers.authorization })
  const ok = (obj) => ({ ok: true, status: 200, text: async () => JSON.stringify(obj) })
  if (u.endsWith('/app/installations/99/access_tokens')) {
    const [h, p, sig] = init.headers.authorization.replace('Bearer ', '').split('.')
    const valid = createVerify('RSA-SHA256').update(`${h}.${p}`).verify(publicKey, sig, 'base64url')
    assert.ok(valid, 'JWT 签名必须能用 App 公钥验证')
    assert.equal(JSON.parse(Buffer.from(p, 'base64url')).iss, '42')
    return ok({ token: 'inst-token', expires_at: new Date(Date.now() + 3600_000).toISOString() })
  }
  if (u.includes('/installation/repositories')) return ok({ repositories: [{ full_name: 'PraiseZhu/project-akb2' }, { full_name: 'PraiseZhu/pr-qa-agent-deploy' }] })
  if (u.endsWith('/repos/PraiseZhu/pr-qa-agent-deploy/issues') && init.method === 'POST') return ok({ number: 1, html_url: 'h', user: { login: 'argus-qa[bot]' } })
  if (u.includes('/repos/PraiseZhu/project-akb2/pulls/5')) return ok({ number: 5, via: 'app' })
  return { ok: false, status: 404, text: async () => 'nope' }
}

const gh = await import('../src/github.mjs')

test('App 覆盖的仓走安装令牌，令牌复用不重复换取', async () => {
  const a = await gh.getPull('PraiseZhu/project-akb2', 5)
  const b = await gh.getPull('PraiseZhu/project-akb2', 5)
  assert.equal(a.via, 'app')
  assert.equal(b.via, 'app')
  assert.equal(calls.filter((c) => c.url.endsWith('/access_tokens')).length, 1)
  assert.ok(calls.filter((c) => c.url.includes('/pulls/5')).every((c) => c.auth === 'token inst-token'))
})

test('App 没装的仓仍走 gh 登录', async () => {
  const issue = await gh.getIssue('xindong/mivo-canvas-plugin', 7)
  assert.equal(issue.title, 'via gh')
  assert.ok(JSON.parse(readFileSync(statePath, 'utf8')).log.some((l) => l.includes('xindong/mivo-canvas-plugin/issues/7')))
})

test('issue 由 App 机器人身份创建；回查按机器人账号过滤', async () => {
  const created = await gh.createIssue('PraiseZhu/pr-qa-agent-deploy', { title: 't', body: 'b', labels: [] })
  assert.equal(created.user.login, 'argus-qa[bot]')
  assert.equal(await gh.publisherLogin('PraiseZhu/pr-qa-agent-deploy', 'PraiseZhu'), 'argus-qa[bot]')
  assert.equal(await gh.publisherLogin('xindong/mivo-canvas-plugin', 'PraiseZhu'), 'PraiseZhu')
  assert.equal(await gh.gitTokenFor('PraiseZhu/project-akb2'), 'inst-token')
  assert.equal(await gh.gitTokenFor('makecindy/cindy'), null)
})
