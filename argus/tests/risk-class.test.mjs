// 风险分级（审计 2026-10-18 S4）：智能体说明文档单独一类、测试文件和 ghost.json 不推到 C、C 级只强制跑测到高风险文件的可选步骤
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createRequire } from 'node:module'

// 「只改注释→comment-only」要靠 typescript 解析；没装时 classify.mjs 保守地按真实改动算（不改），这里把依赖 TS 的用例标 skip 并打一行说明
let hasTs = true
try { createRequire(new URL('../src/classify.mjs', import.meta.url))('typescript') } catch { hasTs = false }
const needsTs = hasTs ? {} : { skip: '缺依赖 typescript，跳过' }
if (!hasTs) console.log('risk-class: 缺依赖 typescript，跳过依赖 TS 解析的用例（只改注释→comment-only）')

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-risk-'))
const { classifyChange, isCommentOnly } = await import('../src/classify.mjs')
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

test('只改注释（#1076）→ change_kind comment-only，不强制也不跑 bench；有真实代码改动照常跑 bench', needsTs, () => {
  const rows = ['performance'].map((key) => ({ key, verdict: 'undetermined' }))
  const triage = { status: 'ok', rows }
  const comment = [
    { filename: 'src/store/persistQueue.ts', before: '// 结构守卫 120 行\nconst a = 1\n/**\n  * old\n */\n', after: '// 结构守卫 150 行\nconst a = 1\n/**\n  * new\n */\n\n' },
    { filename: 'cindyplugin/src/agent/generationSourceEntry.ts', status: 'added', before: '', after: '/* new */\n' },
    { filename: 'docs/a.md', patch: '@@ -1 +1 @@\n-x\n+y' },
  ]
  const r = classifyChange(comment, mivo)
  assert.equal(r.changeKind, 'comment-only'); assert.equal(r.riskClass, 'B')
  for (const rc of ['B', 'C']) {
    const plan = toStepPlan({ profile: mivo, riskClass: rc, triage, files: comment })
    assert.ok(!plan.stepIds.includes('bench'), `${rc}: 只改注释不跑 bench`)
    assert.ok(plan.skipped.some((s) => s.id === 'bench'))
  }
  const code = [...comment, { filename: 'src/store/persistQueue.ts', before: 'const a = 1\n', after: 'const a = 2\n' }]
  assert.equal(classifyChange(code, mivo).riskClass, 'C'); assert.equal(classifyChange(code, mivo).changeKind, undefined)
  assert.ok(toStepPlan({ profile: mivo, riskClass: 'C', triage: { status: 'ok', rows: [{ key: 'performance', verdict: 'looks_unrelated' }] }, files: code }).stepIds.includes('bench'), '真实代码改动照常强制 bench')
})

test('只改注释的反例：看起来像注释的代码行、拿不到全文、解析失败、不认识的语言都按真实改动算', () => {
  const real = (name, f) => {
    assert.equal(isCommentOnly(f), false, name)
    assert.equal(classifyChange([f], mivo).changeKind, undefined, name)
  }
  real('乘法续行 `  * 2`', { filename: 'src/store/persistQueue.ts', before: 'const n = a\n  * 2\n', after: 'const n = a\n  * 3\n' })
  assert.equal(classifyChange([{ filename: 'src/store/persistQueue.ts', before: 'const n = a\n  * 2\n', after: 'const n = a\n  * 3\n' }], mivo).riskClass, 'C', '改了代码的高风险文件仍是 C 级')
  real('`/* x */ doEvil()`', { filename: 'src/x.ts', before: 'a()\n', after: 'a()\n/* x */ doEvil()\n' })
  real('`*/ run()`', { filename: 'src/x.js', before: '/* a\n*/\n', after: '/* a\n*/ run()\n' })
  real('模板字符串里的 `// text`', { filename: 'src/x.ts', before: 'const s = `\na\n`\n', after: 'const s = `\na\n// text\n`\n' })
  real('#private 字段', { filename: 'src/x.ts', before: 'class A { #a = 1 }\n', after: 'class A { #a = 2 }\n' })
  real('ASI：`return /*换行*/ x` 返回 undefined', { filename: 'src/x.ts', before: 'function f() {\n  return x\n}\n', after: 'function f() {\n  return /*\n*/ x\n}\n' })
  real('ASI：`return` 后换行', { filename: 'src/x.ts', before: 'function f() {\n  return x\n}\n', after: 'function f() {\n  return\nx\n}\n' })
  real('只有 patch、没有全文', { filename: 'src/x.ts', patch: '@@ -1 +1 @@\n-// a\n+// b' })
  real('拿不到 patch 也拿不到全文', { filename: 'server/x.ts', patch: null })
  real('解析失败', { filename: 'src/x.ts', before: 'f(\n// a\n', after: 'f(\n// b\n' })
  real('不认识的语言', { filename: 'scripts/x.py', before: '# a\nx = 1\n', after: '# b\nx = 1\n' })
  assert.equal(classifyChange([{ filename: 'server/x.ts', patch: null }], mivo).riskClass, 'C')
})
