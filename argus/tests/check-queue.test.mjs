// mini/check-queue.sh：dedup_key 解析（新格式）与旧 issue（没有 dedup_key）的向后兼容
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, chmodSync, rmSync } from 'node:fs'
import { join, resolve, dirname } from 'node:path'
import { tmpdir } from 'node:os'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const SCRIPT = resolve(dirname(fileURLToPath(import.meta.url)), '../../mini/check-queue.sh')
const hasJq = spawnSync('jq', ['--version']).status === 0
const SHA = 'a'.repeat(40)
const block = (f) => ['x', '```json mivo-qa-finding', JSON.stringify(f, null, 2), '```', `<!-- mivo-qa-fingerprint: ${f.fingerprint} -->`].join('\n')
const finding = (extra = {}) => ({ schema: 'mivo-qa-finding/v1', fingerprint: 'mq1-abc', repo: 'xindong/mivo-canvas-plugin', pr: { tested_sha: SHA }, source: 'check', check_id: 'unit', severity: 'P1', dimension: 'gray-box', ...extra })

// 假 gh：读出 --jq 程序，用真 jq 跑在固定的 issue 列表上（gh 内置 gojq，语法同 jq）
function run(issues) {
  const dir = mkdtempSync(join(tmpdir(), 'cq-'))
  try {
    const data = join(dir, 'issues.json')
    writeFileSync(data, JSON.stringify(issues))
    const gh = join(dir, 'gh')
    writeFileSync(gh, `#!/usr/bin/env bash\nprog=""\nwhile [ $# -gt 0 ]; do [ "$1" = --jq ] && { prog="$2"; shift; }; shift; done\nexec jq -r "$prog" "${data}"\n`)
    chmodSync(gh, 0o755)
    const r = spawnSync('bash', [SCRIPT], { encoding: 'utf8', env: { ...process.env, GH_BIN: gh, MIVO_QA_EVIDENCE_DIR: join(dir, 'ev') } })
    return { status: r.status, out: r.stdout, err: r.stderr }
  } finally { rmSync(dir, { recursive: true, force: true }) }
}
const issue = (n, f) => ({ number: n, title: 't', url: `https://github.com/PraiseZhu/pr-qa-agent-deploy/issues/${n}`, createdAt: `2026-10-0${n}T00:00:00Z`, labels: [], body: block(f) })

test('check-queue：新 issue 输出 MIVO_QA_DEDUP_KEY=指纹@合并提交', { skip: !hasJq && 'jq not installed' }, () => {
  const r = run([issue(1, finding({ dedup_key: `mq1-abc@${SHA}` }))])
  assert.equal(r.status, 0, r.err)
  assert.match(r.out, new RegExp(`^MIVO_QA_DEDUP_KEY=mq1-abc@${SHA}$`, 'm'))
  assert.match(r.out, /^MIVO_QA_FINGERPRINT=mq1-abc$/m)
})

test('check-queue：旧 issue 没有 dedup_key 时退回指纹（向后兼容）', { skip: !hasJq && 'jq not installed' }, () => {
  const r = run([issue(1, finding())])
  assert.equal(r.status, 0, r.err)
  assert.match(r.out, /^MIVO_QA_DEDUP_KEY=mq1-abc$/m)
})

test('check-queue：dedup_key 不以指纹开头 / 有非法字符 → 跳过该 issue（bad-dedup-key）', { skip: !hasJq && 'jq not installed' }, () => {
  const r = run([issue(1, finding({ dedup_key: 'mq1-other@x' })), issue(2, finding({ dedup_key: 'mq1-abc@"; rm' }))])
  assert.equal(r.status, 4)
  assert.match(r.err, /#1:bad-dedup-key/); assert.match(r.err, /#2:bad-dedup-key/)
  const ok = run([issue(1, finding({ dedup_key: 'mq1-other@x' })), issue(2, finding({ dedup_key: 'mq1-abc@day:2026-10-10' }))])
  assert.equal(ok.status, 0, ok.err)
  assert.match(ok.out, /^MIVO_QA_DEDUP_KEY=mq1-abc@day:2026-10-10$/m)
  assert.match(ok.out, /^MIVO_QA_SKIPPED_UNPARSEABLE=#1:bad-dedup-key$/m)
})
