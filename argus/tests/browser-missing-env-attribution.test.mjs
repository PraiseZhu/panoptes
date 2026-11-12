// 11-13 PR-2146（catch-up，签名 inf1-9dadf6ac0ad6121df63a）：箱子上的 Debian Chromium 和 /usr/local/bin/chromium
// 包装脚本都没了，bootstrap-box.sh 也装不上（/workspace/mivo-qa/argus-root/logs/bootstrap-20261113T044201.log：
// apt 的 trixie InRelease 未签名 / 代理 500），于是 Mivo 的浏览器层如实报
//   no trusted Chrome/Chromium executable on this platform; browser layer unavailable
// （/workspace/mivo-qa/reports/catch-up/PR-2146.json → prs[0].checks 里 e2e-browser 的 unavailable_reason）。
// panoptes 把这条记成普通的 qa_tool_failures[stage=e2e-browser]（nightly.mjs:405），收尾复查发现 Chromium 在运行
// 期间被补上（bootstrap.resolved_later = apt:chromium / chromium / chromium-wrapper），把 bootstrap 那条
// qa_tool_failures 和 "/usr/bin/chromium missing: browser E2E will be unavailable" 这条 env_problems 一起删掉
// （nightly.mjs:150），只剩那条工具故障：报告说「panoptes 的检查跑不了」，却不说原因是箱子没浏览器。
// 下游 pi/bin/infrafix.py:232 收 qa_tool_failures 当 panoptes 自己的故障（1 次就开票；只有标了 transient /
// self_repaired 的条目才会被跳过，:243），env_problems 才按箱子问题处理，而且会被同一次的 bootstrap 票吸收（:245），
// 于是这张票报到 panoptes 自己身上。
// 约定：箱子没有浏览器是环境问题，不是 panoptes 的检查坏了 —— 记进 env_problems，或标成 transient / self_repaired；
// 无论如何不能作为「持续存在的 QA 工具故障」留下（persistentHealthEntries 决定它会不会变成 issue）。
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = mkdtempSync(join(tmpdir(), 'argus-browser-missing-'))
process.env.ARGUS_ROOT = root
process.env.ARGUS_GH_BIN = join(here, 'fixtures/argus-fake-gh.mjs')
process.env.ARGUS_GH_RETRY_MS = '1'
process.env.ARGUS_E2E_GH_STATE = join(root, 'github-fixture.json')
after(() => rmSync(root, { recursive: true, force: true }))

const N = await import('../src/nightly.mjs')
const { MIRRORS_DIR } = await import('../src/paths.mjs')

const REPO_REAL = 'xindong/mivo-canvas-plugin'
const BROWSER_MISSING = 'no trusted Chrome/Chromium executable on this platform; browser layer unavailable'

// PR-2146 h1 的 report.browser.json，四个检查原样照抄
const BROWSER_CHECKS = [
  { id: 'browser-host-probe', status: 'unavailable', reason: BROWSER_MISSING, evidence: 'browser-probe.json' },
  { id: 'browser-product-roundtrip', status: 'unavailable', reason: 'Chrome missing; product persist/export not executed', evidence: 'browser-probe.json' },
  { id: 'browser-not-cindy', status: 'pass', reason: 'browser layer does not claim Cindy plugin install or live profile access', evidence: 'layer=browser' },
  { id: 'candidate-snapshot-stable', status: 'pass', reason: 'candidate files unchanged during layer run', evidence: 'candidate-snapshot.json' },
]
const BROWSER_REPORT = JSON.stringify({ schemaVersion: 1, layer: 'browser', status: 'unavailable', checks: BROWSER_CHECKS }, null, 2)

// 照 Mivo 的 scripts/e2e/bug-doctor-images/run.mjs --layer browser 在缺浏览器时的行为：写报告、stdout 打 JSON 摘要、
// 以 2 退出（profile 里 e2e-browser 的 unavailableExitCodes: [2]）
const e2eBrowserCmd = ['sh', '-c', [
  'run="$1"',
  'mkdir -p "$run/e2e-browser"',
  `cat > "$run/e2e-browser/report.browser.json" <<'JSON'\n${BROWSER_REPORT}\nJSON`,
  `printf '{\\n  "status": "unavailable",\\n  "layer": "browser"\\n}\\n'`,
  'exit 2',
].join('\n'), 'sh', '${RUN}']

const profile = () => ({
  sandbox: false,
  steps: [
    { id: 'install', kind: 'setup', nameZh: '安装依赖', cmd: ['true'] },
    {
      id: 'e2e-browser', nameZh: '浏览器端到端', kind: 'product', optional: true,
      covers: ['e2e', 'ui', 'data', 'sideEffects', 'visual'],
      cmd: e2eBrowserCmd,
      unavailableExitCodes: [2],
      failureReport: { format: 'checks', path: '${RUN}/e2e-browser/report.browser.json' },
      timeoutMin: 10,
    },
  ],
})

// PR-2146 开跑时的自检结论（报告里的 system_health.bootstrap）
const MISSING = ['missing: apt:chromium', 'missing: chromium — /usr/lib/chromium/chromium',
  'missing: chromium-wrapper — /usr/local/bin/chromium differs from scripts/chromium-linux.sh']
const BOOT = { ran: true, checked_at: '2026-11-13T04:42:03+08:00', missing_before: MISSING, missing_after: MISSING,
  log: '/workspace/mivo-qa/argus-root/logs/bootstrap-20261113T044201.log' }

// 开跑时 runNightly 组出来的 system_health：这台箱子没有 Chromium（envSnapshot 读到 chromium: null →
// nightly.mjs:1899 那条 env_problems），自举也没装上（recordBootstrap → env_problems + qa_tool_failures[bootstrap]）
const runStartHealth = () => {
  const h = { status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], notes: [],
    environment: { chromium: null, chromium_path: '/nonexistent/chromium' } }
  N.recordBootstrap(h, BOOT)
  h.env_problems.push({ message: '/usr/bin/chromium missing: browser E2E will be unavailable' })
  return h
}

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
  execFileSync('git', ['clone', '-q', '--bare', src, join(MIRRORS_DIR, `fixture__pr${number}.git`)])
  const pr = { number, title: 'code', html_url: 'https://example.invalid', user: { login: 'f' }, base: { ref: 'main', sha: baseSha }, head: { sha: 'f'.repeat(40) }, merged_at: new Date().toISOString(), merge_commit_sha: mergeSha, state: 'closed' }
  writeFileSync(process.env.ARGUS_E2E_GH_STATE, JSON.stringify({ pr, baseSha, files: [{ filename: 'value.mjs', additions: 1, deletions: 1 }], calls: [] }))
  return repo
}

async function runPr(number) {
  const repo = fixture(number)
  const h = runStartHealth()
  const out = await N.processPr({
    repo, project: { repo, profileData: profile() }, number, discovery: 'merged',
    secrets: {}, db: null, health: h, evidenceRoot: join(root, `ev-${number}`), opts: { noReview: true, reuseMerged: false },
  })
  return { out, h }
}

// 会变成 issue 的条目（persistentHealthEntries 跳过 transient / self_repaired，infrafix.py:243 只看 qa_tool_failures）
const persistentToolFailures = (h) => N.persistentHealthEntries({ repo: REPO_REAL, date: '2026-11-13', system_health: h })
  .filter((it) => it.category === 'qa_tool_failures' && it.entry.stage === 'e2e-browser')
// 报告里必须能看出「这一轮没跑浏览器检查是因为箱子没有浏览器」
const explainsBrowserMissing = (h) => [...h.env_problems.map((e) => e.message), ...h.notes]
  .some((m) => /chrome|chromium|浏览器|browser/i.test(m) && /e2e|browser|浏览器/i.test(m))
const dumped = (h) => JSON.stringify({ qa_tool_failures: h.qa_tool_failures, env_problems: h.env_problems, notes: h.notes, resolved_later: h.bootstrap?.resolved_later })

test('箱子缺 Chromium、运行期间才补上：e2e-browser 没跑成是环境问题，不能留成持续存在的 QA 工具故障（PR-2146）', async () => {
  const { out, h } = await runPr(2146)
  // 复现：这一轮 e2e-browser 确实没跑成，原因就是箱子上没有浏览器（Mivo 的原话）。
  // 运行前就认出「本机没有浏览器」、直接把这一步判成环境问题（或跳过）也算正确，所以检查在场时才断言这几项。
  const check = out.checks.find((c) => c.id === 'e2e-browser')
  if (check) {
    assert.equal(check.status, 'unavailable', `没跑成的检查要如实记下：${JSON.stringify(out.checks)}`)
    assert.equal(check.unavailable_reason, BROWSER_MISSING)
  }
  // 收尾复查：Chromium 在运行期间被补上，报告里的 bootstrap.resolved_later 就是这三项
  N.recheckBootstrap(h, () => [], () => new Date('2026-11-12T21:57:17Z'))
  assert.deepEqual(h.bootstrap.resolved_later, ['apt:chromium', 'chromium', 'chromium-wrapper'], dumped(h))
  assert.deepEqual(persistentToolFailures(h), [],
    `缺浏览器是箱子的问题，不是 panoptes 的检查坏了，不能进 qa_tool_failures 变成 panoptes 自己的票：${dumped(h)}`)
  assert.ok(explainsBrowserMissing(h), `报告仍要说明 e2e-browser 为什么没跑成（箱子缺浏览器）：${dumped(h)}`)
})

test('Chromium 到报告时还缺着：e2e-browser 的没跑成同样归到环境问题，不单列成 QA 工具故障', async () => {
  const { out, h } = await runPr(2147)
  const check = out.checks.find((c) => c.id === 'e2e-browser')
  if (check) assert.equal(check.status, 'unavailable', JSON.stringify(out.checks))
  // 收尾复查：Chromium 仍然缺着（运行期间的修复没发生）
  N.recheckBootstrap(h, () => MISSING, () => new Date('2026-11-12T21:57:17Z'))
  assert.deepEqual(h.bootstrap.resolved_later, [], dumped(h))
  assert.deepEqual(persistentToolFailures(h), [],
    `箱子一直缺浏览器时，这条要归到 bootstrap / env（那张票本来就是箱子问题），不是另一张 panoptes 故障票：${dumped(h)}`)
  assert.ok(explainsBrowserMissing(h), dumped(h))
})
