import { test } from 'node:test'
import assert from 'node:assert/strict'
import { matchesItem } from './followup-match.mjs'

const review = { problems: ['同名附件无法可靠保持节点 ID 与附件顺序的对应关系'], files: ['src/agent/canvasAgentVerbs.ts'], coverage: false }
const cov = { problems: ['new-code coverage 47.54% < 90%'], files: [], coverage: true }
const c995 = { type: 'pr-comment', text: '不用改。\n\n同名不同内容的附件不会把节点 ID 对错，也不会拿错改图源。回执再按附件 hash 顺序回填 nodeIds。hash 前 8 位相同、完成顺序颠倒时 ID 仍按原附件顺序，已有回归。' }

test('#995: comment on review finding closes review item only', () => {
  assert.equal(matchesItem(c995, review), true)
  assert.equal(matchesItem(c995, cov), false)
})
test('coverage keywords close coverage item', () => {
  assert.equal(matchesItem({ type: 'pr-comment', text: '覆盖率这次先不补' }, cov), true)
})
test('file reference, explicit all, fix PR', () => {
  assert.equal(matchesItem({ type: 'pr-review-comment', text: 'ok', path: 'src/agent/canvasAgentVerbs.ts' }, review), true)
  assert.equal(matchesItem({ type: 'pr-comment', text: '这几条都不用改' }, cov), true)
  assert.equal(matchesItem({ type: 'pr-references', text: '' }, cov), true)
  assert.equal(matchesItem({ type: 'pr-comment', text: '收到' }, review), false)
})
