// 11-05 02:47（北京）catch-up 的 PR #1788（xindong/mivo-canvas-plugin，run 09487732）：
// 首轮安装（h1，n1788-h1-mw2gu1qb）在 src/node_modules/.bin 上撞到 ENOSPC：
//   npm error code ENOSPC / npm error nospc ENOSPC: no space left on device, mkdir '…/node_modules/.bin'
// nightly.mjs:366-368 在重试之前就写了一条 system_health.qa_tool_failures：
//   { pr: 1788, stage: 'setup', message: 'dependency install failed, retrying once', detail: <npm ENOSPC 尾巴> }
// 同一行的重试（h1r）13 秒后装好了：report/catch-up/PR-1788.json 里 install 检查 status=pass，
// PR 结论 passed（outcome_reason「所有检查都通过」），可那条「dependency install failed」没有 transient / self_repaired
// 标记，永远留在报告里。panoptes 自己只把没标记的条目当成持久问题（nightly.mjs:1420 persistentHealthEntries：
// 「transient / self_repaired 的条目只留在报告里」，system_health issue 的正文也写「still present after self-repair」），
// pi/bin/infrafix.py:243 读 report 时同样只跳过带 transient / self_repaired 的条目 —— 于是这一次已经自愈的磁盘满
// 被登记成 panoptes 自己的依赖安装故障（inf1-bbd8c19a…）。
//
// 正确行为（这里断言的部分）：安装失败一次、重试成功 → 报告里不能留下「持久」的安装失败条目
// （persistentHealthEntries 为空、不会变成 system_health issue）；报告仍可以用 transient / self_repaired 条目或 notes
// 保留「这次装过一次」的事实。反过来，重试也失败（真的装不上，例如依赖 404）时必须照常留下持久条目 ——
// 否则就把真正的安装故障藏起来了。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = mkdtempSync(join(tmpdir(), 'argus-setup-retry-'))
process.env.ARGUS_ROOT = root
process.env.ARGUS_STEP_CACHE = '0'            // 每个用例都真的跑安装步骤，不复用结果
process.env.ARGUS_GH_BIN = join(here, 'fixtures/argus-fake-gh.mjs')
process.env.ARGUS_GH_RETRY_MS = '1'
process.env.ARGUS_E2E_GH_STATE = join(root, 'github-fixture.json')
after(() => rmSync(root, { recursive: true, force: true }))

const N = await import('../src/nightly.mjs')
const { MIRRORS_DIR } = await import('../src/paths.mjs')
const { persistentHealthEntries, healthIssueCandidate } = N

const ENOSPC_TAIL = [
  'npm error code ENOSPC',
  "npm error nospc ENOSPC: no space left on device, mkdir '/workspace/mivo-qa/argus-root/cache/runs/n1788-h1-mw2gu1qb/src/node_modules/.bin'",
  'npm error nospc There appears to be insufficient space on your system to finish.',
]
// 第一次调用：照 npm 的 ENOSPC 失败（并写下标记），之后（h1r 重试）成功
const installOnceFails = (marker) => ['sh', '-c',
  `if [ -e ${JSON.stringify(marker)} ]; then exit 0; fi; printf '%s\\n' ${ENOSPC_TAIL.map((l) => JSON.stringify(l)).join(' ')} >&2; : > ${JSON.stringify(marker)}; exit 1`]
// 每次调用都失败：真的装不上（依赖 404）
const installAlwaysFails = () => ['sh', '-c',
  `printf '%s\\n' ${['npm error code E404', 'npm error 404 Not Found - GET https://registry.npmjs.org/vitest'].map((l) => JSON.stringify(l)).join(' ')} >&2; exit 1`]

const profileFor = (installCmd) => ({
  displayName: 'fixture',
  sandbox: false,
  steps: [
    { id: 'install', nameZh: '安装依赖', kind: 'setup', cmd: installCmd, timeoutMin: 1 },
    { id: 'unit', nameZh: '单元测试', kind: 'product', covers: ['unit'], cmd: ['true'], timeoutMin: 1 },
  ],
})

const health = () => ({ status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], notes: [] })

// 本地 Git 夹具 → 裸镜像（origin 指向本地源仓，fetch 不出网）
function fixture(number) {
  const repo = `fixture/pr${number}`
  const src = join(root, `source-${number}`)
  mkdirSync(src)
  const git = (...args) => execFileSync('git', ['-C', src, '-c', 'user.name=F', '-c', 'user.email=f@example.invalid', ...args], { encoding: 'utf8' }).trim()
  git('init', '-q', '-b', 'main')
  writeFileSync(join(src, 'value.mjs'), 'export const v = 1\n')
  git('add', '.'); git('commit', '-qm', 'base')
  const baseSha = git('rev-parse', 'HEAD')
  writeFileSync(join(src, 'value.mjs'), 'export const v = 2\n'); git('commit', '-qam', `code (#${number})`)
  const mergeSha = git('rev-parse', 'HEAD')
  mkdirSync(MIRRORS_DIR, { recursive: true })
  execFileSync('git', ['clone', '-q', '--bare', src, join(MIRRORS_DIR, `${repo.replace('/', '__')}.git`)])
  const pr = { number, title: 'code', html_url: 'https://example.invalid', user: { login: 'f' }, base: { ref: 'main', sha: baseSha }, head: { sha: 'f'.repeat(40) }, merged_at: new Date().toISOString(), merge_commit_sha: mergeSha, state: 'closed' }
  writeFileSync(process.env.ARGUS_E2E_GH_STATE, JSON.stringify({ pr, baseSha, files: [{ filename: 'value.mjs', additions: 1, deletions: 1 }], calls: [] }))
  return { repo, number, src }
}

async function runPr({ repo, number, installCmd }) {
  const h = health()
  const ev = join(root, `ev-${number}`)
  const processOne = (t) => N.processPr({ repo, project: { repo, profileData: profileFor(installCmd) }, number: t.number, discovery: t.reason, secrets: {}, db: null, health: h, evidenceRoot: ev, opts: { noReview: true } })
  const { prs } = await N.processTargets([{ number, reason: 'merged' }], { health: h, opts: { yieldToPriority: false }, processOne, repo })
  return { pr: prs[0], h }
}

test('安装撞 ENOSPC、重试成功：不得留下持久的「dependency install failed」QA 工具故障', async () => {
  const f = fixture(1790)
  const { pr, h } = await runPr({ ...f, installCmd: installOnceFails(join(f.src, 'install-attempted')) })
  // 行为本身没变：重试装好了，检查照常跑，PR 照常给结论 —— 这次运行不是「一次都没测」
  assert.equal(pr.outcome, 'passed', `安装重试成功后 PR 应照常判定（${pr.outcome_reason}）`)
  assert.equal(pr.checks.find((c) => c.id === 'install')?.status, 'pass', '重试成功后 install 检查是 pass')
  assert.equal(pr.checks.find((c) => c.id === 'unit')?.status, 'pass', '重试成功后后续检查照常执行')
  // 缺陷：重试前写下的条目没有 transient / self_repaired，所以成了「持久」故障
  const durable = persistentHealthEntries({ repo: f.repo, system_health: h })
  assert.deepEqual(durable.map((it) => ({ category: it.category, stage: it.entry.stage, message: it.entry.message })), [],
    '已自愈的安装失败不能算持久的 QA 工具故障（pi/bin/infrafix.py 会按它开 inf1 ticket）')
  assert.equal(healthIssueCandidate({ repo: f.repo, system_health: h }), null, '不应由这次自愈的安装重试生成 system_health issue')
  // 真的还有条目的话，只能是明确标记过的（transient / self_repaired）或 notes
  for (const e of h.qa_tool_failures) assert.ok(e.transient === true || e.self_repaired === true, `qa_tool_failures 里的安装重试条目必须标记为已恢复：${JSON.stringify(e).slice(0, 200)}`)
})

test('安装重试也失败：真正的安装故障必须照常留在报告里（持久条目）', async () => {
  const f = fixture(1791)
  const { pr, h } = await runPr({ ...f, installCmd: installAlwaysFails() })
  assert.equal(pr.outcome, 'infra_failed', `装不上时不能继续判定（${pr.outcome_reason}）`)
  assert.ok(pr.failed_checks.some((c) => c.status === 'setup-failed'), 'setup 失败要进 failed_checks（QA 自查）')
  const durable = persistentHealthEntries({ repo: f.repo, system_health: h })
  assert.ok(durable.length > 0, '重试也失败的安装故障不能从健康清单里消失')
  assert.ok(durable.some((it) => it.entry.stage === 'setup' || /install/i.test(String(it.entry.message ?? ''))),
    `持久条目要指向安装步骤：${JSON.stringify(durable.map((it) => it.entry)).slice(0, 400)}`)
  assert.ok(healthIssueCandidate({ repo: f.repo, system_health: h }), '重试也失败时要能生成 system_health issue')
})
