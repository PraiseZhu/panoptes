// inf1-c4a8d21597336b7c604c 回归保护（11-05 02:42–03:04 北京时间，PR #1729 / #1788 / #1789，3 次运行）：
//   control.log: {"level":"error","msg":"keep evidence failed","runDir":"…/cache/runs/n1729-h1-…",
//                 "err":"ENOSPC: no space left on device, mkdir '…/evidence/nightly/2026-11-05/56ffa8ab/pr-1729/h1'"}
// 箱子建不出证据子目录（磁盘 / inode 用满）本身是环境问题，上两层已经修过两次（c98d974 记 inode、c2b32e2 把 ENOSPC 记
// env_problems）。但留证据这条路上，错误仍然只在 runner 里被吞成一行日志（src/runner.mjs:724），进程照常返回、PR 照常
// 定级，报告里没有任何地方说「这次的证据没留下来」：evidence_dir 指向的目录还在，failed_checks[].evidence.log_file
// （src/nightly.mjs:508）指向 h1/logs/<step>.log —— 文件根本不存在。读报告的人 / 出 issue 的人看到的是一份有证据
// 的结果，实际是空目录（真机 2026-11-05/adf3ae28/pr-1789/ 就是这个状态：目录存在、里面是空的）。
//
// 同一次箱子耗尽因此还会继续产出「panoptes 机制有 bug」工单：日志里那一行按签名成了 log 类故障（本工单就是它），
// 报告里却一条 env_problems / qa_tool_failures 都没有。
//
// 正确收场：留证据失败要进这一轮的报告（env_problems / qa_tool_failures / notes 任一条，说清是哪次、哪个目录），
// 不冒充测试工具故障（stage=pipeline 会把没测到的 PR 记成 qa-error、整轮 failed），也不是作者的发现。
//
// 测试无法让本机的某个文件系统真的返回 ENOSPC（不挂文件系统、不填满磁盘），所以用同一个 mkdir 失败来复现：
// 把 pr-<n>/h1 先占成普通文件，mkdirSync 抛 EEXIST。留证据的坑不在 errno 上——任何一次 cp/mkdir 失败都会让
// 证据悄悄消失、报告照旧指向它；只按 ENOSPC 分支处理仍然会漏掉 EACCES / EEXIST / ENAMETOOLONG 这些同形故障。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, existsSync, readdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = mkdtempSync(join(tmpdir(), 'argus-kept-evidence-'))
process.env.ARGUS_ROOT = root
process.env.ARGUS_GH_BIN = join(here, 'fixtures/argus-fake-gh.mjs')
process.env.ARGUS_GH_RETRY_MS = '1'
process.env.ARGUS_E2E_GH_STATE = join(root, 'github-fixture.json')
process.env.ARGUS_STEP_CACHE = '0'   // 真跑每一步（复用会跳过步骤，证据目录里就没有这一步的日志）
after(() => rmSync(root, { recursive: true, force: true }))

const N = await import('../src/nightly.mjs')
const { MIRRORS_DIR } = await import('../src/paths.mjs')

const health = () => ({ status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], notes: [] })
// 一轮真跑：真 git 镜像 + 一个通过的 product 步骤（证据目录里会有 h1/logs/unit.log）；GitHub 走进程级 fake，不联网
const profile = { sandbox: false, steps: [
  { id: 'install', kind: 'setup', nameZh: '准备', cmd: [process.execPath, '-e', ''] },
  { id: 'unit', kind: 'product', nameZh: '单元测试', covers: ['unit'], cmd: [process.execPath, '-e', ''] },
] }

function fixture(number) {
  const repo = `fixture/kept${number}`
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
  return { repo, number, mergeSha }
}

test('建不出证据子目录（真机上是 ENOSPC）必须进报告，不能只留一行 control.log', async () => {
  const f = fixture(2401)
  const evidenceRoot = join(root, 'ev-keep-fail')
  const evDir = join(evidenceRoot, `pr-${f.number}`)
  mkdirSync(evDir, { recursive: true })
  // 占住 pr-<n>/h1：keepEvidence 的 mkdirSync 抛 EEXIST，和真机上 ENOSPC 是同一个失败点（证据一个字节也没留下）
  writeFileSync(join(evDir, 'h1'), 'occupied\n')

  const h = health()
  const out = await N.processPr({ repo: f.repo, project: { repo: f.repo, profileData: profile }, number: f.number, discovery: 'merged', secrets: {}, db: null, health: h, evidenceRoot, opts: { noReview: true, reuseMerged: false } })

  assert.equal(existsSync(join(evDir, 'h1', 'logs')), false, '前提：这一轮的证据确实没留下来')
  assert.equal(out.evidence_dir, evDir, '报告仍然把 evidence_dir 指到这个目录（所以「没留下来」这件事必须另外说清）')

  const recorded = [...h.env_problems, ...h.qa_tool_failures, ...(h.notes ?? [])].map((x) => String(x?.message ?? x)).join(' | ')
  assert.ok(recorded.length > 0,
    `留证据失败要进这一轮的报告（env_problems / qa_tool_failures / notes 任一条）：现在只有 control.log 里一行，报告里什么都没有 ${JSON.stringify({ env_problems: h.env_problems, qa_tool_failures: h.qa_tool_failures, notes: h.notes })}`)
  assert.match(recorded, /evidence|证据/i, `要能看出来说的是证据没留下来：${recorded}`)
  assert.ok(recorded.includes(`pr-${f.number}`) || recorded.includes('h1'),
    `要说清是哪一轮 / 哪个目录（错误消息里本来就有这个路径）：${recorded}`)
  assert.ok(!h.qa_tool_failures.some((x) => x?.stage === 'pipeline'),
    `留证据失败不是 QA 工具自己崩了：stage=pipeline 会把这个 PR 记成 qa-error、整轮 failed（那正是这张工单的来源）：${JSON.stringify(h.qa_tool_failures)}`)
  assert.deepEqual(out.findings, [], '证据没留下来不是作者的发现')
})

test('证据目录写得下时照旧留证据（别把这条修成「不再留证据」）', async () => {
  const f = fixture(2402)
  const evidenceRoot = join(root, 'ev-keep-ok')
  const h = health()
  const out = await N.processPr({ repo: f.repo, project: { repo: f.repo, profileData: profile }, number: f.number, discovery: 'merged', secrets: {}, db: null, health: h, evidenceRoot, opts: { noReview: true, reuseMerged: false } })
  const evDir = join(evidenceRoot, `pr-${f.number}`)
  assert.equal(out.evidence_dir, evDir)
  const left = existsSync(evDir) ? readdirSync(evDir, { recursive: true }).map(String) : []
  assert.ok(existsSync(join(evDir, 'h1', 'logs', 'unit.log')),
    `首轮日志要留到证据目录（真机上的证据就是这么留下的）：${JSON.stringify(left)}`)
  assert.ok(!existsSync(join(evDir, 'h1', 'src')), '证据里不含源码（keepEvidence 的排除项照旧生效）')
  const recorded = [...h.env_problems, ...h.qa_tool_failures].map((x) => String(x?.message ?? x)).join(' | ')
  assert.doesNotMatch(recorded, /evidence|证据/i, `证据真的留下来了，不该报成留证据失败：${recorded}`)
})
