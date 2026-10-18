// 测试子进程的环境：TZ 硬性固定为上海，继承来的 TZ（本机 America/Los_Angeles）不能覆盖；只有显式 PANOPTES_TEST_TZ 能改
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { baseEnv, stepEnv } from '../src/runner.mjs'

const withEnv = (vars, fn) => {
  const saved = Object.fromEntries(Object.keys(vars).map((k) => [k, process.env[k]]))
  try {
    for (const [k, v] of Object.entries(vars)) if (v === undefined) delete process.env[k]; else process.env[k] = v
    fn()
  } finally {
    for (const [k, v] of Object.entries(saved)) if (v === undefined) delete process.env[k]; else process.env[k] = v
  }
}

test('baseEnv / stepEnv：TZ 未设置时为 Asia/Shanghai', () => {
  withEnv({ TZ: undefined, PANOPTES_TEST_TZ: undefined }, () => {
    assert.equal(baseEnv().TZ, 'Asia/Shanghai')
    assert.equal(stepEnv(mkdtempSync(join(tmpdir(), 'argus-envtest-')), {}).TZ, 'Asia/Shanghai')
  })
})

test('继承的 TZ 不覆盖：父进程 TZ=America/Los_Angeles 时子进程仍是 Asia/Shanghai', () => {
  withEnv({ TZ: 'America/Los_Angeles', PANOPTES_TEST_TZ: undefined }, () => {
    assert.equal(baseEnv().TZ, 'Asia/Shanghai')
    assert.equal(stepEnv(mkdtempSync(join(tmpdir(), 'argus-envtest-')), {}).TZ, 'Asia/Shanghai')
  })
})

test('显式 PANOPTES_TEST_TZ 可覆盖；profile env 仍可按项目覆盖', () => {
  withEnv({ TZ: 'America/Los_Angeles', PANOPTES_TEST_TZ: 'UTC' }, () => {
    const runDir = mkdtempSync(join(tmpdir(), 'argus-envtest-'))
    assert.equal(baseEnv().TZ, 'UTC')
    assert.equal(stepEnv(runDir, {}).TZ, 'UTC')
    assert.equal(stepEnv(runDir, { env: { TZ: 'Europe/Berlin' } }).TZ, 'Europe/Berlin')
  })
})
