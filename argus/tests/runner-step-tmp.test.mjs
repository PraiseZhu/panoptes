// 每步独立 TMPDIR：前一步留在 $TMPDIR 的 node_modules 不能成为后一步临时目录的祖先
import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdirSync, mkdtempSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { tmpdir } from 'node:os'
import { stepTmpDir } from '../src/runner.mjs'

const hasAncestorNodeModules = (dir, stop) => {
  for (let a = dir; a !== dirname(stop); a = dirname(a)) if (existsSync(join(a, 'node_modules'))) return true
  return false
}

test('上一步在自己 TMPDIR 留下 node_modules，下一步的 TMPDIR 祖先里看不到', () => {
  const base = mkdtempSync(join(tmpdir(), 'argus-steptmp-'))
  const a = stepTmpDir(base, 'plugin')
  mkdirSync(join(a, 'node_modules'))
  const b = stepTmpDir(base, 'unit')
  assert.notEqual(a, b)
  assert.equal(dirname(b), base)
  assert.equal(hasAncestorNodeModules(join(b, 'x'), base), false)
})

test('同一步重跑时清空旧内容；step id 里的特殊字符被转义', () => {
  const base = mkdtempSync(join(tmpdir(), 'argus-steptmp-'))
  const a = stepTmpDir(base, 'ai test/1')
  mkdirSync(join(a, 'node_modules'))
  const again = stepTmpDir(base, 'ai test/1')
  assert.equal(again, a)
  assert.equal(existsSync(join(again, 'node_modules')), false)
  assert.equal(dirname(a), base)
})
