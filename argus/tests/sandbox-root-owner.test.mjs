// inf1-13fa895fbdc98651fe00：单元测试「…launcher.native.test.mjs > 父链可信但模块可写时…」在基线版本上也失败，
// 7 天内 3 个 PR（#1424 / #1447 / #1449）各报一次。
//
// 现场：mivo-canvas-plugin 的 scripts/ops/review-capacity-check/run.sh 有一道路径属主闸 trusted_path——路径链每级
// 必须属主是 root 或当前用户、且不能 group/other 可写；launcher.native.test.mjs 拿这道闸做断言。GitHub CI（/ 属 root）
// 绿，panoptes 里同一批用例 10 条红 7 条。真因是 QA 自己的沙箱：步骤跑在 bwrap 的非特权 user namespace 里，
// 宿主 root（uid 0）在 namespace 里没有映射，root 属主的 /（/usr、/etc 同理）被显示成溢出 uid 65534，
// 于是这道闸在「首跑」和「基线版本」上都必然拒绝 -> 判定 pre-existing(P3)，日报天天说仓库基线坏了，infrafix 按
// 「同一检查项 7 天 ≥3 次在基线也失败」开工单，而仓库那边（真机 / CI）根本没有这个问题。
//
// 这份回归测试要求 panoptes 做到：沙箱看不出 root 属主时，基线也失败的属主校验类检查项不再算仓库老问题——
// 记成环境问题（system_health.env_problems）+ 待核实（QA 自查）并写清原因，同时不放松对本 PR 引入的失败的定级。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync, spawnSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = mkdtempSync(join(tmpdir(), 'argus-sandbox-owner-'))
process.env.ARGUS_ROOT = root
process.env.ARGUS_GH_BIN = join(here, 'fixtures/argus-fake-gh.mjs')
process.env.ARGUS_GH_RETRY_MS = '1'
process.env.ARGUS_E2E_GH_STATE = join(root, 'github-fixture.json')
after(() => rmSync(root, { recursive: true, force: true }))

const N = await import('../src/nightly.mjs')
const { sandboxMode, bwrapArgs } = await import('../src/runner.mjs')
const { MIRRORS_DIR } = await import('../src/paths.mjs')

const health = () => ({ status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], notes: [] })

// 步骤在沙箱里看到的 / 属主。属主仍是 root(0) 的机器（例如以 root 跑测试）没有这个假失败，跳过而不是假红。
const sandboxRootUid = (() => {
  if (process.platform !== 'linux' || sandboxMode({}) !== 'bwrap') return null
  const dir = join(root, 'probe-run')
  mkdirSync(dir, { recursive: true })
  const r = spawnSync('/usr/bin/bwrap', [...bwrapArgs(dir), '--', '/usr/bin/stat', '-c', '%u', '/'], { encoding: 'utf8' })
  return r.status === 0 ? r.stdout.trim() : null
})()
const sandboxHidesRootOwner = sandboxRootUid != null && sandboxRootUid !== '0'

const stepOf = (id, nameZh, file) => ({
  id, kind: 'product', nameZh, covers: ['unit'],
  cmd: [process.execPath, '--test', '--test-reporter=spec', '--test-reporter=${NODE_TEST_REPORTER}', '--test-reporter-destination=stdout', '--test-reporter-destination=${TEST_REPORT}', file],
  testReport: { format: 'node-events', path: `\${RUN}/reports/${id}.jsonl` },
})

// 夹具仓：base 与 head 里都有「属主校验」用例（沙箱里必然失败，基线也一样），另有只依赖本 PR 内容的老用例（head 才失败）
function fixture(number) {
  const repo = `fixture/pr${number}`
  const src = join(root, `source-${number}`)
  mkdirSync(src)
  const git = (...args) => execFileSync('git', ['-C', src, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()
  git('init', '-q', '-b', 'main')
  writeFileSync(join(src, 'value.mjs'), 'export const value = 1\n')
  // 和 run.sh 的 trusted_path 同一条规则：路径链每级属主必须是 root 或自己（沙箱里 / 报 65534，两个都不是）
  writeFileSync(join(src, 'native.test.mjs'), [
    "import { test } from 'node:test'",
    "import assert from 'node:assert/strict'",
    "import { execFileSync } from 'node:child_process'",
    "test('父链可信：/ 的属主是 root 或自己', () => {",
    "  const uid = execFileSync('stat', ['-c', '%u', '/'], { encoding: 'utf8' }).trim()",
    "  process.stderr.write('root-uid=' + uid + '\\n')",
    "  assert.ok(uid === '0' || uid === String(process.getuid()), '控制器启动路径不可信，拒绝加载模块: ' + uid)",
    "})",
    '',
  ].join('\n'))
  writeFileSync(join(src, 'intro.test.mjs'), [
    "import { test } from 'node:test'",
    "import assert from 'node:assert/strict'",
    "import { value } from './value.mjs'",
    "test('value 仍然是 1', () => assert.equal(value, 1))",
    '',
  ].join('\n'))
  git('add', '.')
  git('commit', '-qm', 'base')
  const baseSha = git('rev-parse', 'HEAD')
  writeFileSync(join(src, 'value.mjs'), 'export const value = 2\n')
  git('commit', '-qam', `change (#${number})`)
  const mergeSha = git('rev-parse', 'HEAD')
  mkdirSync(MIRRORS_DIR, { recursive: true })
  execFileSync('git', ['clone', '-q', '--bare', src, join(MIRRORS_DIR, `fixture__pr${number}.git`)], { stdio: 'pipe' })
  const pr = { number, title: 'fix: 夹具', html_url: 'https://example.invalid', user: { login: 'fixture' }, base: { ref: 'main', sha: baseSha }, head: { sha: 'f'.repeat(40) }, merged_at: new Date().toISOString(), merge_commit_sha: mergeSha, state: 'closed' }
  writeFileSync(process.env.ARGUS_E2E_GH_STATE, JSON.stringify({ pr, baseSha, files: [{ filename: 'value.mjs', additions: 1, deletions: 1 }], calls: [] }))
  const profile = {
    pathHints: {},
    notApplicable: {},
    steps: [
      { id: 'install', kind: 'setup', nameZh: '准备', cmd: ['true'] },
      stepOf('unit', '单元测试', 'intro.test.mjs'),
      stepOf('unit-native', '单元测试（原生，路径属主校验）', 'native.test.mjs'),
    ],
  }
  return { repo, pr, profile }
}

test('沙箱看不出 root 属主时，基线也失败的属主校验项不算仓库老问题（记环境问题 + 待核实）', { skip: !sandboxHidesRootOwner }, async () => {
  const number = 2101
  const { repo, profile } = fixture(number)
  const h = health()
  const out = await N.processPr({ repo, project: { repo, profileData: profile }, number, discovery: 'merged', secrets: {}, db: null,
    health: h, evidenceRoot: join(root, `ev-${number}`), opts: { noReview: true, noAiTests: true } })

  const native = out.failed_checks.find((c) => c.check_id === 'unit-native')
  assert.ok(native, `属主校验步骤必须在首跑失败（实际 failed_checks: ${JSON.stringify(out.failed_checks.map((c) => [c.check_id, c.status, c.evidence?.base_run]))}）`)
  // 事实要留着：首跑、重跑、基线都失败（沙箱在三个轮次里都一样掩盖属主）
  assert.equal(native.evidence.first_run, 'fail')
  assert.equal(native.evidence.base_run, 'fail')

  // 关键：沙箱自己造出来的失败不能当成「仓库基线坏了」——那样每个 PR 都会报一次，infrafix 就会一直开工单
  assert.notEqual(native.pre_existing, true, `沙箱看不出 root 属主（步骤看到 / 属主 ${sandboxRootUid}）时，不能把「基线也失败」记成仓库老问题`)
  assert.ok(native.env_suspected, '失败的检查项要标出是环境造成的（env_suspected），不能只写成 attribution 文字')
  assert.ok(!out.findings.some((f) => f.check_id === 'unit-native'), '环境假失败不能进 prs[].findings')
  assert.ok((out.pending_verification ?? []).some((p) => JSON.stringify(p).includes('unit-native')), '环境假失败要进待核实（QA 自查）')
  assert.ok(
    h.env_problems.some((p) => /sandbox|沙箱|bwrap/i.test(p.message) && /65534|uid 0|root|属主/i.test(p.message)),
    `system_health.env_problems 要写明沙箱看不出 root 属主（实际：${JSON.stringify(h.env_problems)}）`,
  )

  // 不许顺手把一切都降级：本 PR 引入的失败仍然是 P1 finding
  const intro = out.findings.find((f) => f.check_id === 'unit')
  assert.ok(intro, '本 PR 引入的失败必须留在 findings')
  assert.equal(intro.severity, 'P1')
  assert.equal(intro.status, 'introduced')
})
