// umask 固定为 022：QA 在 umask 002 下跑会让测试夹具目录变成 0775（mivo #1385 的 RUNNER_OWNER_MISMATCH 假失败）
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const RUNNER = new URL('../src/runner.mjs', import.meta.url).href
const NIGHTLY = new URL('../src/nightly.mjs', import.meta.url).href
const SCRIPT = fileURLToPath(new URL('../../run-nightly.sh', import.meta.url))

const probe = (umask) => JSON.parse(execFileSync('bash', ['-c', `umask ${umask}; exec node --input-type=module -e "$0"`,
  `const m = await import(${JSON.stringify(RUNNER)}); const { mkdtempSync, mkdirSync, statSync } = await import('node:fs'); const { join } = await import('node:path'); const { tmpdir } = await import('node:os');
   const d = mkdtempSync(join(tmpdir(), 'argus-umask-')); mkdirSync(join(d, 'x'));
   const c = (await import('node:child_process')).execFileSync('bash', ['-c', 'umask'], { encoding: 'utf8' }).trim();
   console.log(JSON.stringify({ inherited: m.INHERITED_UMASK, current: m.currentUmask(), child: c, dirMode: (statSync(join(d, 'x')).mode & 0o777).toString(8) }))`], { encoding: 'utf8' }))

for (const u of ['002', '077', '022']) {
  test(`runner pins umask 0022 when started under umask ${u}`, () => {
    const r = probe(u)
    assert.equal(r.inherited, u.padStart(4, '0'))
    assert.equal(r.current, '0022')
    assert.equal(r.child, '0022')
    assert.equal(r.dirMode, '755')
  })
}

test('checkUmask: 0022 is fine, anything else is an env_problem', async () => {
  const { checkUmask } = await import(NIGHTLY)
  const ok = { environment: { umask: '0022' }, env_problems: [] }
  checkUmask(ok)
  assert.deepEqual(ok.env_problems, [])
  for (const u of ['0002', null]) {
    const bad = { environment: { umask: u }, env_problems: [] }
    checkUmask(bad)
    assert.equal(bad.env_problems.length, 1)
    assert.match(bad.env_problems[0].message, new RegExp(`umask is ${u ?? 'unknown'} \\(expected 0022\\)`))
  }
})

test('run-nightly.sh pins umask 022 before any node call and exports the inherited value', () => {
  const s = readFileSync(SCRIPT, 'utf8')
  const pin = s.indexOf('\numask 022\n')
  assert.ok(pin > 0)
  assert.ok(s.indexOf('PANOPTES_INHERITED_UMASK="$(umask)"') < pin)
  assert.ok(pin < s.search(/\n[^#\n]*\bnode\b/))
  // 真跑一段：脚本头到固定 umask 为止，在 umask 002 下执行后应是 0022
  const head = s.slice(0, pin + '\numask 022\n'.length)
  const out = execFileSync('bash', ['-c', `umask 002; ${head.replace(/^#!.*\n/, '').replace('set -euo pipefail', 'set -eu')}\necho "$PANOPTES_INHERITED_UMASK $(umask)"`], { encoding: 'utf8', env: { ...process.env, QA_HOME: '/tmp/argus-umask-qahome' } })
  assert.equal(out.trim().split('\n').pop(), '0002 0022')
})
