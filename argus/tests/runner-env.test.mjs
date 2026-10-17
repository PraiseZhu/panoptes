// 测试子进程的环境：TZ 固定为上海（或沿用 nightly 导出的 TZ），不跟随本机时区
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { baseEnv, stepEnv } from '../src/runner.mjs'

test('baseEnv / stepEnv 带 TZ：未设置时兜底 Asia/Shanghai，设置时沿用', () => {
  const saved = process.env.TZ
  try {
    delete process.env.TZ
    assert.equal(baseEnv().TZ, 'Asia/Shanghai')
    const runDir = mkdtempSync(join(tmpdir(), 'argus-envtest-'))
    assert.equal(stepEnv(runDir, {}).TZ, 'Asia/Shanghai')
    process.env.TZ = 'UTC'
    assert.equal(baseEnv().TZ, 'UTC')
    assert.equal(stepEnv(runDir, { env: { TZ: 'Europe/Berlin' } }).TZ, 'Europe/Berlin', 'profile env 仍可覆盖')
  } finally {
    if (saved === undefined) delete process.env.TZ; else process.env.TZ = saved
  }
})
