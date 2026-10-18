// AI 补测目标（审计 2026-10-18 S6）：把改动里没被执行到的行交给模型，而不只是维度名
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-aiunc-'))
const { generateTargetedTests, toUncoveredTargets, toLineRanges, toValidTests } = await import('../src/aitests.mjs')

const profile = { aiTests: { frameworkZh: 'vitest', pathGlobs: ['src/**/*.ai.test.ts'], run: ['x'] }, steps: [] }
const t = (file, covers = []) => ({ kind: 'coverage', purposeZh: 'p', covers, filePath: 'src/x.ai.test.ts', content: 'test()', mutation: { file, find: 'a', replace: 'b' } })

test('toUncoveredTargets：只取有未覆盖行的文件，按未覆盖行数排序；toLineRanges 压成区间', () => {
  const nc = { files: [{ file: 'src/a.ts', uncovered_lines: [3] }, { file: 'src/b.ts', uncovered_lines: [12, 13, 14, 15, 20] }, { file: 'src/c.ts', uncovered_lines: [] }, { file: 'main.js', not_measurable: true }] }
  assert.deepEqual(toUncoveredTargets(nc).map((u) => u.file), ['src/b.ts', 'src/a.ts'])
  assert.equal(toLineRanges([20, 12, 13, 14, 15]), '12-15, 20')
  assert.deepEqual(toUncoveredTargets(null), [])
})

test('toValidTests：没覆盖维度、但植入错误落在未覆盖文件上的测试保留；落在别的文件上照旧丢', () => {
  const uncovered = [{ file: 'src/b.ts', lines: [12] }]
  const out = toValidTests({ tests: [t('src/b.ts'), t('src/other.ts'), t('src/other.ts', ['boundary'])] }, { profile, gaps: ['boundary'], uncovered, maxTests: 5 })
  assert.deepEqual(out.map((x) => [x.mutation.file, x.targetsUncovered ?? null]), [['src/b.ts', 'src/b.ts'], ['src/other.ts', null]])
})

test('generateTargetedTests：没有缺口维度但有未覆盖行 → 照样调模型，提示里带文件和行号区间', async () => {
  let prompt = null
  const fetchImpl = async (_url, init) => { prompt = JSON.parse(init.body).messages[1].content; return { ok: true, status: 200, json: async () => ({ choices: [{ message: { content: JSON.stringify({ tests: [t('src/b.ts')] }) }, finish_reason: 'stop' }] }) } }
  const quota = { check: () => ({ ok: true }), consume: () => {} }
  const gen = await generateTargetedTests({ db: null, secrets: { ARGUS_LLM_BASE_URL: 'http://127.0.0.1:9', ARGUS_LLM_API_KEY: 'k' }, riskClass: 'B', pr: { title: 't', body: '' }, files: [], profile, gaps: [], uncovered: [{ file: 'src/b.ts', lines: [12, 13, 20] }], quota, fetchImpl })
  assert.equal(gen.status, 'ok'); assert.equal(gen.tests.length, 1)
  assert.match(prompt, /没被现有测试执行到的行/)
  assert.match(prompt, /src\/b\.ts: 12-13, 20/)
  assert.doesNotMatch(prompt, /需要补测的方面/)
  const none = await generateTargetedTests({ db: null, secrets: {}, riskClass: 'B', pr: { title: 't' }, files: [], profile, gaps: [], quota, fetchImpl })
  assert.equal(none.status, 'skipped')
})
