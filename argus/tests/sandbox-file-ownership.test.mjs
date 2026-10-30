// 沙箱改写宿主属主：步骤跑在 bwrap 用户命名空间里（只映射 QA 自己的 uid），宿主 root 拥有的路径 —— 含 / ——
// 在沙箱里读回来是 overflow uid（65534）。mivo 的 review-capacity-check 原生用例断言「临时目录的每个祖先都属于
// root 或控制器」（scripts/ops/review-capacity-check/workspace.mjs assertControllerDirectory，报错
// 「父目录必须由控制器独占管理」），在沙箱里必然失败，同一份代码在 GitHub CI 上却是通过的。
//
// 现在 panoptes 把它记成 pre_existing=true（报告里 "also fails on the base revision"）。infrafix 正是按
// failed_checks[].pre_existing=true 开单（pi/bin/infrafix.py report_occs → 「…在基线版本上也失败」），
// 所以 10-30 13:38 起同一条签名每轮都成单：仓库并没有坏，坏的是 QA 自己的沙箱。
//
// 这里钉住正确处置：沙箱改写属主造成的基线失败要记进 system_health.env_problems（说明原因），
// 并且不能当成「仓库在基线版本上就失败」的 old failure —— 否则每轮重新开单，永远修不掉。
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
process.env.ARGUS_SECRETS_FILE = join(root, 'intentionally-no-secrets.env')
after(() => rmSync(root, { recursive: true, force: true }))

const N = await import('../src/nightly.mjs')
const { bwrapArgs, sandboxMode } = await import('../src/runner.mjs')
const { MIRRORS_DIR } = await import('../src/paths.mjs')

// 夹具用例：只断言「宿主 / 属于 root」。真机 / CI 上都成立，panoptes 的步骤沙箱里不成立。
const OWNER_TEST = 'host root is owned by uid 0'
const health = () => ({ status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], notes: [] })

// 沙箱里 / 实际读回什么属主：和 panoptes 跑步骤用的是同一套 bwrapArgs
function probeSandboxRootUid() {
  if (sandboxMode() !== 'bwrap') return null
  const runDir = mkdtempSync(join(tmpdir(), 'argus-sandbox-probe-'))
  try {
    const res = spawnSync('/usr/bin/bwrap', [...bwrapArgs(runDir), '--', 'stat', '-c', '%u', '/'], { encoding: 'utf8' })
    return res.status === 0 ? res.stdout.trim() : null
  } finally { rmSync(runDir, { recursive: true, force: true }) }
}

const sandboxRootUid = probeSandboxRootUid()
const blind = sandboxRootUid !== null && sandboxRootUid !== '0'
const skip = sandboxMode() !== 'bwrap' ? 'bwrap 不可用：这个故障只在沙箱里出现'
  : sandboxRootUid === null ? '探测沙箱属主失败，无法确定前提' : false

// 一次真实运行（本地 Git 镜像 + 假 gh + 真 bwrap 沙箱），两条用例共用
let shared = null
function runFixture() {
  shared ??= (async () => {
    const number = 2101
    const repo = `fixture/pr${number}`
    const src = join(root, `source-${number}`)
    mkdirSync(src)
    const git = (...args) => execFileSync('git', ['-C', src, '-c', 'user.name=F', '-c', 'user.email=f@example.invalid', ...args], { encoding: 'utf8' }).trim()
    git('init', '-q', '-b', 'main')
    writeFileSync(join(src, 'value.mjs'), 'export const v = 1\n')
    writeFileSync(join(src, 'owner.test.mjs'), `import { test } from 'node:test'\nimport assert from 'node:assert/strict'\nimport { statSync } from 'node:fs'\ntest('${OWNER_TEST}', () => { assert.equal(statSync('/').uid, 0) })\n`)
    git('add', '.'); git('commit', '-qm', 'base')
    const baseSha = git('rev-parse', 'HEAD')
    writeFileSync(join(src, 'value.mjs'), 'export const v = 2\n'); git('commit', '-qam', `code (#${number})`)
    const mergeSha = git('rev-parse', 'HEAD')
    mkdirSync(MIRRORS_DIR, { recursive: true })
    execFileSync('git', ['clone', '-q', '--bare', src, join(MIRRORS_DIR, `fixture__pr${number}.git`)])
    const pr = { number, title: 'code', html_url: 'https://example.invalid', user: { login: 'f' }, base: { ref: 'main', sha: baseSha }, head: { sha: 'f'.repeat(40) }, merged_at: new Date().toISOString(), merge_commit_sha: mergeSha, state: 'closed' }
    writeFileSync(process.env.ARGUS_E2E_GH_STATE, JSON.stringify({ pr, baseSha, files: [{ filename: 'value.mjs', additions: 1, deletions: 1 }], calls: [] }))
    // 不加 sandbox:false：要的就是真实步骤沙箱
    const profile = { pathHints: { 'value.mjs': ['unit'] }, steps: [
      { id: 'install', kind: 'setup', nameZh: '准备', cmd: [process.execPath, '-e', ''] },
      { id: 'unit', kind: 'product', nameZh: '单元测试', covers: ['unit'],
        cmd: [process.execPath, '--test', '--test-reporter=spec', '--test-reporter=${NODE_TEST_REPORTER}', '--test-reporter-destination=stdout', '--test-reporter-destination=${TEST_REPORT}', 'owner.test.mjs'],
        testReport: { format: 'node-events', path: '${RUN}/reports/unit.jsonl' } },
    ] }
    const evidenceRoot = join(root, 'evidence')
    const h = health()
    const out = await N.processPr({ repo, project: { repo, profileData: profile }, number, discovery: 'merged', secrets: {}, db: null, health: h, evidenceRoot, opts: { noReview: true } })
    return { out, health: h }
  })()
  return shared
}

test('步骤沙箱把宿主 root 报成 overflow uid 时，必须记进 system_health.env_problems', { skip }, async () => {
  const { out, health: h } = await runFixture()
  const unit = out.checks?.find((c) => c.id === 'unit')
  if (!blind) {
    // 沙箱已经能如实呈现宿主属主（或本机 / 就归 root）：夹具用例通过，这个故障不存在
    assert.equal(unit?.status, 'pass', '沙箱不再改写属主时夹具用例应当通过')
    return
  }
  assert.ok(unit, 'unit 步骤应该跑过')
  // 前提：这条用例在沙箱里确实失败（本机真实执行，不是伪造的结果）
  assert.equal(unit.status, 'fail', `沙箱里 / 的属主是 ${sandboxRootUid}，夹具用例必然失败`)
  const envText = (h.env_problems ?? []).map((e) => JSON.stringify(e)).join('\n')
  assert.match(envText, new RegExp(`${sandboxRootUid}|属主|所有权|ownership`, 'i'),
    `沙箱把宿主 root 报成 uid ${sandboxRootUid}，报告必须在 system_health.env_problems 里说明这是环境原因，`
    + `否则同一个检查项每轮都只会显示成「仓库在基线版本上也失败」；实际 env_problems= ${envText || '(空)'}`)
})

test('沙箱造成的基线失败不能记成仓库老问题（pre_existing=true），否则每轮重新开单', { skip }, async () => {
  const { out } = await runFixture()
  if (!blind) {
    assert.equal((out.failed_checks ?? []).filter((c) => c.test === OWNER_TEST).length, 0, '故障不存在时不该有这条老失败')
    return
  }
  // 前提：沙箱把 / 报成非 root，夹具用例在合并版本上必然失败（基线轮次用的是同一个沙箱，所以同样失败）
  assert.equal(out.checks?.find((c) => c.id === 'unit')?.status, 'fail', `沙箱里 / 的属主是 ${sandboxRootUid}，夹具用例必然失败`)
  const reported = (out.failed_checks ?? []).filter((c) => c.test === OWNER_TEST)
  // 关键：pre_existing 是对仓库的断言，infrafix 只看这一个字段开单（pi/bin/infrafix.py report_occs）
  for (const c of reported) {
    assert.notEqual(c.pre_existing, true,
      `这条失败只在 panoptes 的沙箱里存在（/ 读回 uid ${sandboxRootUid}），不能记成 pre_existing=true；`
      + `infrafix 会据此每轮开一张「${c.check_name ?? c.check_id}「${c.test}」在基线版本上也失败」的单（fingerprint ${c.fingerprint}）`)
  }
})
