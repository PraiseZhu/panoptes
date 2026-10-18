// 风险分级（审计 2026-10-18 S4）：智能体说明文档单独一类、测试文件和 ghost.json 不推到 C、C 级只强制跑测到高风险文件的可选步骤
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-risk-'))
const { classifyChange } = await import('../src/classify.mjs')
const { toStepPlan } = await import('../src/matrix.mjs')
const { loadProfile } = await import('../src/config.mjs')
const F = (...names) => names.map((filename) => ({ filename }))
const mivo = loadProfile('mivo-canvas-plugin')

test('AGENTS.md / CLAUDE.md（可夹带普通文档）→ A + change_kind agent-docs；夹带代码仍是 B', () => {
  const r = classifyChange(F('AGENTS.md', 'cindyplugin/CLAUDE.md', 'docs/a.md'), mivo)
  assert.equal(r.riskClass, 'A'); assert.equal(r.changeKind, 'agent-docs'); assert.match(r.reasonZh, /智能体说明文档/)
  assert.equal(classifyChange(F('docs/a.md'), mivo).changeKind, undefined)
  assert.equal(classifyChange(F('AGENTS.md', 'src/x.ts'), mivo).riskClass, 'B')
  assert.equal(classifyChange(F('scripts/AGENTS.md'), mivo).riskClass, 'B', 'scripts/** 仍不能进 A')
})

test('测试文件和 ghost.json 落在 cPaths 里也不推到 C；真正的高风险源文件照旧 C', () => {
  assert.equal(classifyChange(F('cindyplugin/ghost.json', 'public/changelog.json'), mivo).riskClass, 'B')
  assert.equal(classifyChange(F('src/store/persistQueue.test.ts'), mivo).riskClass, 'B')
  assert.equal(classifyChange(F('cindyplugin/src/__tests__/depositBindingLock.test.ts'), mivo).riskClass, 'B')
  assert.equal(classifyChange(F('cindyplugin/main.js'), mivo).riskClass, 'C')
  assert.equal(classifyChange(F('cindyplugin/ghost.json', 'server/index.ts'), mivo).riskClass, 'C')
})

test('C 级：只强制跑 exercises 碰到高风险文件的可选步骤，其余按分诊', () => {
  const rows = ['performance', 'e2e', 'api', 'unit', 'regression', 'data', 'compatibility', 'data-consistency', 'db', 'ui', 'sideEffects', 'visual', 'api-oracle', 'permission', 'negative-path', 'permission-boundary', 'negativeAssertions', 'state-transition'].map((key) => ({ key, verdict: 'looks_unrelated' }))
  const triage = { status: 'ok', rows }
  const plan = toStepPlan({ profile: mivo, riskClass: 'C', triage, files: F('cindyplugin/main.js') })
  assert.ok(plan.stepIds.includes('e2e-browser'))
  assert.ok(!plan.stepIds.includes('bench'), 'cindyplugin 改动不强制 bench')
  assert.ok(!plan.stepIds.includes('api-inproc'))
  const server = toStepPlan({ profile: mivo, riskClass: 'C', triage, files: F('server/routes.ts') })
  assert.ok(server.stepIds.includes('api-inproc') && server.stepIds.includes('api-probe'))
  const legacy = { steps: [{ id: 'unit', kind: 'product' }, { id: 'e2e', kind: 'product', optional: true, covers: ['e2e'] }] }
  assert.deepEqual(toStepPlan({ profile: legacy, riskClass: 'C', triage, files: F('a.ts') }).stepIds, ['unit', 'e2e'], '没写 exercises 的步骤：C 级照旧全跑')
})

test('只改注释（#1076）→ change_kind comment-only，不强制也不跑 bench；有真实代码改动照常跑 bench', () => {
  const rows = ['performance'].map((key) => ({ key, verdict: 'undetermined' }))
  const triage = { status: 'ok', rows }
  const comment = [
    { filename: 'src/store/persistQueue.ts', patch: '@@ -1,3 +1,3 @@\n-// 结构守卫 120 行\n+// 结构守卫 150 行\n const a = 1\n-  * old\n+  * new\n+' },
    { filename: 'cindyplugin/src/agent/generationSourceEntry.ts', patch: '@@ -1 +1 @@\n-/* old */\n+/* new */' },
    { filename: 'docs/a.md', patch: '@@ -1 +1 @@\n-x\n+y' },
  ]
  const r = classifyChange(comment, mivo)
  assert.equal(r.changeKind, 'comment-only'); assert.equal(r.riskClass, 'B')
  for (const rc of ['B', 'C']) {
    const plan = toStepPlan({ profile: mivo, riskClass: rc, triage, files: comment })
    assert.ok(!plan.stepIds.includes('bench'), `${rc}: 只改注释不跑 bench`)
    assert.ok(plan.skipped.some((s) => s.id === 'bench'))
  }
  const code = [...comment, { filename: 'src/store/persistQueue.ts', patch: '@@ -1 +1 @@\n-const a = 1\n+const a = 2' }]
  assert.equal(classifyChange(code, mivo).riskClass, 'C'); assert.equal(classifyChange(code, mivo).changeKind, undefined)
  assert.ok(toStepPlan({ profile: mivo, riskClass: 'C', triage: { status: 'ok', rows: [{ key: 'performance', verdict: 'looks_unrelated' }] }, files: code }).stepIds.includes('bench'), '真实代码改动照常强制 bench')
  assert.equal(classifyChange([{ filename: 'src/x.ts', patch: '@@ -1 +1 @@\n-#a = 1\n+#a = 2' }], mivo).changeKind, undefined, '#private 字段不是注释')
  assert.equal(classifyChange([{ filename: 'server/x.ts', patch: null }], mivo).riskClass, 'C', '拿不到 patch 按真实改动算')
})
