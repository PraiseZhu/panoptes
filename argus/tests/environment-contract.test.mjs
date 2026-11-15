import test from 'node:test'
import assert from 'node:assert/strict'
import { checkEnvironment, ENVIRONMENT_CAPABILITIES, executionStatus } from '../src/environment-contract.mjs'

const good = {
  required: ENVIRONMENT_CAPABILITIES,
  env: { GH_TOKEN: 'fixture-only', ARGUS_GATEWAY_URL: 'https://example.invalid' },
  execute: async () => ({ ok: true }),
  filesystem: () => ({ bavail: 1_000_000, bsize: 4096, files: 100_000, ffree: 50_000 }),
  fetchImpl: async () => ({ ok: true }),
}
test('all declared environment capabilities have a passing contract', async () => {
  const result = await checkEnvironment(good)
  assert.equal(result.status, 'available')
  assert.equal(result.checks.length, ENVIRONMENT_CAPABILITIES.length)
  assert.ok(!JSON.stringify(result).includes('fixture-only'))
  assert.ok(!JSON.stringify(result).includes('example.invalid'))
})
for (const capability of ENVIRONMENT_CAPABILITIES) {
  test(`${capability} unavailable is an environment state, never product_fail`, async () => {
    const result = await checkEnvironment({ ...good, required: [capability], env: {},
      execute: async () => ({ ok: false, tail: 'private external error' }),
      filesystem: () => ({ bavail: 0, bsize: 4096, files: 100, ffree: 0 }),
      fetchImpl: async () => { throw new Error('private external error') },
    })
    assert.equal(result.status, 'environment_unavailable')
    assert.equal(executionStatus({ environment: result, ok: false, exitCode: 1 }), 'environment_unavailable')
    assert.ok(!JSON.stringify(result).includes('private'))
  })
}
test('missing local binary and filesystem permission failures fail closed', async () => {
  const result = await checkEnvironment({ required: ['python', 'disk', 'inode'],
    commands: { python: '/nonexistent/argus-python' }, filesystem: () => { throw new Error('permission denied') } })
  assert.equal(result.status, 'environment_unavailable')
})
test('gateway request has a real deadline and auth failures remain environment states', async () => {
  const { createServer } = await import('node:http')
  const server = createServer(() => {})
  await new Promise((r) => server.listen(0, '127.0.0.1', r))
  try {
    const started = Date.now()
    const result = await checkEnvironment({ required: ['gateway'], gatewayUrl: `http://127.0.0.1:${server.address().port}`, timeoutMs: 100 })
    assert.equal(result.status, 'environment_unavailable')
    assert.ok(Date.now() - started < 2000)
    assert.equal((await checkEnvironment({ ...good, required: ['gateway'], fetchImpl: async () => ({ ok: false, status: 401 }) })).status, 'environment_unavailable')
  } finally { server.closeAllConnections(); await new Promise((r) => server.close(r)) }
})
test('execution statuses preserve timeout, runner error, pass and assertion failure', () => {
  assert.equal(executionStatus({ timedOut: true }), 'timeout')
  assert.equal(executionStatus({ ok: false, exitCode: null }), 'runner_error')
  assert.equal(executionStatus({ ok: true, exitCode: 0 }), 'pass')
  assert.equal(executionStatus({ ok: false, exitCode: 1 }), 'product_fail')
})
