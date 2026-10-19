import { test } from 'node:test'
import assert from 'node:assert/strict'
import { matchesItem, shortTitle } from '../../report/followup-match.mjs'

const review = { problems: ['同名附件无法可靠保持节点 ID 与附件顺序的对应关系'], files: ['src/agent/canvasAgentVerbs.ts'], coverage: false }
const cov = { problems: ['new-code coverage 47.54% < 90%'], files: [], coverage: true }
const c = (text, extra) => ({ type: 'pr-comment', text, ...extra })
const c995 = c('不用改。\n\n同名不同内容的附件不会把节点 ID 对错，也不会拿错改图源。回执再按附件 hash 顺序回填 nodeIds。hash 前 8 位相同、完成顺序颠倒时 ID 仍按原附件顺序，已有回归。')

test('#995: comment on review finding closes review item only', () => {
  assert.equal(matchesItem(c995, review), true)
  assert.equal(matchesItem(c995, cov), false)
})
test('coverage item: only coverage words', () => {
  assert.equal(matchesItem(c('覆盖率这次先不补'), cov), true)
  assert.equal(matchesItem(c('已有回归测试'), cov), false)
  assert.equal(matchesItem(c('修复后全部通过'), cov), false)
  assert.equal(matchesItem(c('CI 全部通过，都不会再出现'), cov), false)
})
test('explicit all phrases', () => {
  assert.equal(matchesItem(c('这几条都不用改'), cov), true)
  assert.equal(matchesItem(c('全部已修'), review), true)
  assert.equal(matchesItem(c('both are fixed'), review), true)
  assert.equal(matchesItem(c('all good'), review), false)
})
test('file and title rules', () => {
  assert.equal(matchesItem({ type: 'pr-review-comment', text: 'ok', path: 'src/agent/canvasAgentVerbs.ts' }, review), true)
  const m = { problems: ['取消只停止面板等待'], files: ['cindyplugin/main.js'], coverage: false }
  assert.equal(matchesItem(c('改了 main.js'), m), false)
  assert.equal(matchesItem(c('改了 cindyplugin/main.js'), m), true)
  assert.equal(matchesItem(c('update github workflow'), { problems: ['GitHub workflow 失败'], files: [] }), false)
})
test('sole open non-coverage item: any reply counts; fix PR closes', () => {
  assert.equal(matchesItem(c('不用改，误报'), { ...review, sole: true }), true)
  assert.equal(matchesItem(c('不用改，误报'), review), false)
  assert.equal(matchesItem(c('不用改，误报'), { ...cov, sole: true }), false)
  assert.equal(matchesItem(c('维护者确认：覆盖率这条不改。'), { ...review, sole: true }), false)
  assert.equal(matchesItem({ type: 'pr-references' }, cov), true)
})

test('shortTitle: punctuation cut, length cut without splitting English words', () => {
  assert.equal(shortTitle('部分目标校验失败绕过图片操作的 Debug Log'), '部分目标校验失败绕过图片操作的 Debug Log')
  assert.equal(shortTitle('部分目标校验失败后会绕过图片操作与画布节点写入的 Debug Logging'), '部分目标校验失败后会绕过图片操作与画布节点写入的 Debug…')
  assert.equal(shortTitle('文本槽名称栏的 pointerdown 未阻止事件传播'), '文本槽名称栏的 pointerdown 未阻止事件传播')
  assert.equal(shortTitle('多个文本槽同时编辑时名称栏的 pointerdown 未阻止事件传播'), '多个文本槽同时编辑时名称栏的 pointerdown 未阻止…')
  assert.equal(shortTitle('滚动条拇指的移动范围按正文高度计算，而不是按实际轨道高度计算'), '滚动条拇指的移动范围按正文高度计算')
  assert.equal(shortTitle('new-code coverage 47.54% < 90%'), 'new-code coverage 47.54% < 90%')
})
test('shortTitle: never splits an English word at the cut', () => {
  assert.equal(shortTitle('部分目标校验失败绕过图片操作的处理逻辑和 DebugLogging'), '部分目标校验失败绕过图片操作的处理逻辑和…')
})
