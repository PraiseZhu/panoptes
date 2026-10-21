import { test } from 'node:test'
import assert from 'node:assert/strict'
import { routeFor, routedRepos, overdueCc } from '../../report/handoff-routes.mjs'

test('aj0928 和 lubinyu-lab 仍进 aj0928-handoff，抄送不变', () => {
  assert.deepEqual(routeFor('aj0928'), { repo: 'aj0928/aj0928-handoff', owner: 'aj0928', cc: [] })
  assert.deepEqual(routeFor('lubinyu-lab'), { repo: 'aj0928/aj0928-handoff', owner: 'aj0928', cc: ['aj0928'] })
  assert.deepEqual(overdueCc(), { 'lubinyu-lab': ['aj0928'] })
})
test('zhongxingtian-ai 进 handover-mivo，其他作者不进交接仓', () => {
  assert.deepEqual(routeFor('zhongxingtian-ai'), { repo: 'zhongxingtian-ai/handover-mivo', owner: 'zhongxingtian-ai', cc: [] })
  assert.equal(routeFor('PraiseZhu'), null)
  assert.deepEqual(routedRepos(), ['aj0928/aj0928-handoff', 'zhongxingtian-ai/handover-mivo'])
})
