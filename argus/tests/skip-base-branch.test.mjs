// 2026-10-30：目标分支是临时探针分支（probe-*，#1411/#1413/#1419/#1422）的已合并 PR 不测，写 skipped（non-main-base）报告；
// integration/* 等长期分支照常测。跳过名单是 registry.skipBaseBranches 一个 glob 列表。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, rmSync, mkdirSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = mkdtempSync(join(tmpdir(), 'argus-skipbase-'))
process.env.ARGUS_ROOT = root
process.env.ARGUS_GH_BIN = join(here, 'fixtures/argus-fake-gh.mjs')
process.env.ARGUS_GH_RETRY_MS = '1'
process.env.ARGUS_E2E_GH_STATE = join(root, 'github-fixture.json')
const reg = { owner: { login: 'o', id: 1 }, issueRepo: 'o/i', enabledSince: '2026-09-23T00:00:00+08:00', projects: [{ repo: 'fixture/a' }] }
process.env.ARGUS_REGISTRY = join(root, 'registry.json')
writeFileSync(process.env.ARGUS_REGISTRY, JSON.stringify(reg))
after(() => rmSync(root, { recursive: true, force: true }))

const C = await import('../src/config.mjs')
const N = await import('../src/nightly.mjs')
const { gapOf } = await import('../src/catchup.mjs')
const { MIRRORS_DIR } = await import('../src/paths.mjs')

test('matchesSkipBase: probe-* 匹配含 / 的探针分支，main / integration/* 不匹配', () => {
  for (const b of ['probe-1410/strict/base-t', 'probe-1410/status/base', 'probe-x']) assert.equal(C.matchesSkipBase(b), true, b)
  for (const b of ['main', 'integration/20261018', 'qa/integration-20261018', 'xprobe-1', '', null]) assert.equal(C.matchesSkipBase(b), false, String(b))
  assert.equal(C.matchesSkipBase('integration/x', ['integration/*', 'probe-*']), true)
})

test('loadRegistry: 默认 skipBaseBranches=[probe-*]，下发到每个项目；可覆盖；非法值报错', () => {
  assert.deepEqual(C.loadRegistry().projects[0].skipBaseBranches, ['probe-*'])
  writeFileSync(process.env.ARGUS_REGISTRY, JSON.stringify({ ...reg, skipBaseBranches: ['probe-*', 'tmp/*'] }))
  assert.deepEqual(C.loadRegistry().projects[0].skipBaseBranches, ['probe-*', 'tmp/*'])
  writeFileSync(process.env.ARGUS_REGISTRY, JSON.stringify({ ...reg, skipBaseBranches: 'probe-*' }))
  assert.throws(() => C.loadRegistry(), /skipBaseBranches/)
  writeFileSync(process.env.ARGUS_REGISTRY, JSON.stringify(reg))
})

test('processPr: base=probe-* 的已合并 PR 不检出不测，得到 skipped / non-main-base；catch-up 不再当缺口', async () => {
  const number = 1411
  const repo = 'fixture/probe1411'
  const pr = { number, title: 'probe', html_url: 'https://example.invalid', user: { login: 'f' }, base: { ref: 'probe-1410/strict/base-t', sha: 'b'.repeat(40) }, head: { sha: 'f'.repeat(40) }, merged_at: new Date().toISOString(), merge_commit_sha: 'a'.repeat(40), state: 'closed' }
  writeFileSync(process.env.ARGUS_E2E_GH_STATE, JSON.stringify({ pr, baseSha: pr.base.sha, files: [{ filename: 'probe-t.txt', additions: 1, deletions: 0 }], calls: [] }))
  const profile = { steps: [{ id: 'install', kind: 'setup', cmd: ['false'] }, { id: 'unit', kind: 'product', nameZh: '单元测试', covers: ['unit'], cmd: ['false'] }] }
  const h = { status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], notes: [] }
  // 没有镜像：如果它尝试检出或跑步骤，会报 qa-error 而不是 skipped
  const out = await N.processPr({ repo, project: { repo, profileData: profile, skipBaseBranches: ['probe-*'] }, number, discovery: 'merged', secrets: {}, db: null, health: h, evidenceRoot: join(root, 'ev'), opts: { noReview: true } })
  assert.equal(out.verdict, 'skipped')
  assert.equal(out.tested, false)
  assert.equal(out.skip_reason, 'non-main-base')
  assert.equal(out.outcome_reason, 'non-main-base')
  assert.equal(out.severity, 'P3')
  assert.match(out.tested_at, /\+08:00$/)
  assert.deepEqual(out.checks, [])
  assert.equal(out.base_ref, 'probe-1410/strict/base-t')
  assert.equal(existsSync(join(MIRRORS_DIR, 'fixture__probe1411.git')), false)
  const dir = join(root, 'merged'); mkdirSync(dir)
  writeFileSync(join(dir, `PR-${number}.json`), JSON.stringify({ schema_version: '1.0', prs: [out] }))
  assert.equal(gapOf({ number, merge_sha: pr.merge_commit_sha }, dir), null)
  // 其它原因的 skipped 仍按原规则判断
  writeFileSync(join(dir, `PR-${number}.json`), JSON.stringify({ schema_version: '1.0', prs: [{ ...out, skip_reason: undefined }] }))
  assert.notEqual(gapOf({ number, merge_sha: pr.merge_commit_sha }, dir), null)
})
