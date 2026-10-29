// 10-29 验收 v2 非阻塞项：VERIFY_PROMPT_VERSION /3 让旧缓存失效；grep 模式长度上限；read 的 end 可省；call_path minItems。全部桩，无真实模型调用
import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createHash } from 'node:crypto'
process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-vsf-'))
const V = await import('../src/review-verify.mjs')
const { VERIFY_RESPONSE_SCHEMA, GREP_PATTERN_MAX } = V

// 只实现 VERIFY_RESPONSE_SCHEMA 用到的关键字
function valid(schema, v) {
  if (schema.anyOf) return schema.anyOf.some((s) => valid(s, v))
  if (schema.type === 'object') {
    if (!v || typeof v !== 'object' || Array.isArray(v)) return false
    if ((schema.required ?? []).some((k) => !(k in v))) return false
    if (schema.additionalProperties === false && Object.keys(v).some((k) => !(k in schema.properties))) return false
    return Object.entries(v).every(([k, x]) => !schema.properties[k] || valid(schema.properties[k], x))
  }
  if (schema.type === 'array') return Array.isArray(v) && v.length >= (schema.minItems ?? 0) && v.every((x) => valid(schema.items, x))
  if (schema.type === 'string') return typeof v === 'string' && (!schema.enum || schema.enum.includes(v)) && v.length <= (schema.maxLength ?? Infinity)
  if (schema.type === 'integer') return Number.isInteger(v)
  return true
}
const S = VERIFY_RESPONSE_SCHEMA.schema

test('VERIFY_PROMPT_VERSION /4: cache entries keyed under /2 or /3 are not hit', () => {
  assert.equal(V.VERIFY_PROMPT_VERSION, 'panoptes-review-verify/4')
  const dir = mkdtempSync(join(tmpdir(), 'argus-vcache-'))
  const finding = { fingerprint: 'fp', title: 't', file: 'a.sh', line: 126 }
  const args = { sha: 's'.repeat(40), finding, model: 'anthropic/claude-opus-5-5', std: { version: 'v' }, contextHash: 'c' }
  const newKey = V.verifyCacheKey(args)
  assert.equal(V.lookupVerification(newKey, dir), null)
  // 和 verifyCacheKey 相同的组成，只换版本号：/2（#99 之前，无 reasoning 的 refuted）、/3（#99，旧 schema 下的 undetermined 等）
  for (const v of ['panoptes-review-verify/2', 'panoptes-review-verify/3']) {
    const oldKey = createHash('sha256').update(JSON.stringify({ v, std: 'v', sha: args.sha, fp: 'fp', title: 't', file: 'a.sh', line: 126, model: args.model, ctx: 'c' })).digest('hex')
    mkdirSync(dir, { recursive: true })
    writeFileSync(join(dir, `${oldKey}.json`), JSON.stringify({ key: oldKey, result: { status: 'undetermined', reasoning: '' } }))
    assert.ok(V.lookupVerification(oldKey, dir), `sanity: the ${v} entry itself is readable`)
    assert.notEqual(newKey, oldKey, v)
  }
  assert.equal(V.lookupVerification(newKey, dir), null)
})

test('grep pattern maxLength in schema and enforced in code (not run, hint returned)', async () => {
  assert.equal(GREP_PATTERN_MAX, 200)
  assert.ok(valid(S, { action: 'grep', pattern: 'x'.repeat(200) }))
  assert.ok(!valid(S, { action: 'grep', pattern: 'x'.repeat(201) }))
  assert.ok(!valid(S, { actions: [{ action: 'grep', pattern: 'a|'.repeat(700) }] }))
  const greps = []
  const reader = { grep: (p) => { greps.push(p); return { ok: true, text: '(no matches)' } }, read: () => ({ ok: false, text: '' }), ls: () => ({ ok: true, text: '' }) }
  const replies = [{ ok: true, json: { actions: [{ action: 'grep', pattern: 'a|'.repeat(700) }, { action: 'grep', pattern: 'usermod' }] } }, { ok: true, json: { action: 'final', verdict: 'cannot-determine', reasoning: 'r' } }]
  const msgs = []
  const r = await V.verifyReviewFinding({ secrets: {}, finding: { file: 'a', line: 1, title: 't' }, reader, context: {}, std: { version: 'x', text: '' }, chat: async ({ messages }) => { msgs.push(messages.at(-1)?.content); return replies.shift() }, model: 'm' })
  assert.deepEqual(greps, ['usermod'])
  assert.deepEqual(r.steps.map((s) => s.ok), [false, true])
  assert.match(msgs[1], /pattern too long \(1400 > 200 chars\)/)
})

test('read end is optional in the schema; omitted / 0 end reads the default range', () => {
  assert.ok(valid(S, { action: 'read', path: 'a', start: 1 }))
  assert.ok(valid(S, { action: 'read', path: 'a', start: 1, end: 0 }))
  assert.ok(!valid(S, { action: 'read', path: 'a' }), 'start still required')
  const read = S.anyOf.find((v) => v.properties?.action?.enum?.[0] === 'read')
  assert.ok(!read.required.includes('end'))
})

test('reachable final with empty call_path fails the schema (minItems) and is not confirmed', () => {
  const fin = { action: 'final', verdict: 'reachable', reasoning: 'r', call_path: [] }
  assert.ok(!valid(S, fin))
  assert.ok(valid(S, { ...fin, call_path: [{ file: 'a.sh', line: 1 }] }))
  assert.equal(V.settleVerdict(fin, new Map()).status, 'undetermined')
})

test('repoReaderFor.read: end omitted or 0 reads start..start+READ_MAX_LINES-1', async () => {
  const { execFileSync } = await import('node:child_process')
  const repo = mkdtempSync(join(tmpdir(), 'argus-vsf-git-'))
  writeFileSync(join(repo, 'a.txt'), Array.from({ length: 500 }, (_, i) => `l${i + 1}`).join('\n') + '\n')
  const g = (...a) => execFileSync('git', ['-C', repo, '-c', 'user.name=t', '-c', 'user.email=t@t', ...a], { encoding: 'utf8' })
  g('init', '-q'); g('add', '.'); g('commit', '-qm', 'x')
  const reader = V.repoReaderFor(repo, g('rev-parse', 'HEAD').trim())
  for (const end of [undefined, 0]) { const r = reader.read('a.txt', 10, end); assert.equal(r.ok, true); assert.equal(r.start, 10); assert.equal(r.end, 229) }
  assert.equal(reader.read('a.txt', 10, 20).end, 20)
})
