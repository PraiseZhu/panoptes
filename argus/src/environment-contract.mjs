// Read-only capability checks. Reports contain capability names/reason codes, never
// command output, endpoints or credentials. Consumers opt into the capabilities needed.
import { statfsSync } from 'node:fs'
import { run } from './exec.mjs'

export const ENVIRONMENT_CAPABILITIES = ['node', 'python', 'bwrap', 'chromium', 'disk', 'inode', 'token', 'github_auth', 'gateway']

export async function checkEnvironment({ required = ['node', 'disk', 'inode'], cwd = process.cwd(), env = process.env,
  timeoutMs = 3000, minDiskBytes = 64 * 1024 * 1024, minFreeInodes = 1000,
  tokenNames = ['GH_TOKEN', 'GITHUB_TOKEN'], gatewayUrl = env.ARGUS_GATEWAY_URL,
  commands = {}, execute = run, filesystem = statfsSync, fetchImpl = fetch } = {}) {
  const deadline = Date.now() + Math.max(1, timeoutMs)
  const command = async (name, fallback, args) => {
    const r = await execute(commands[name] ?? fallback, args, { cwd, env, timeoutMs: Math.max(1, deadline - Date.now()) })
    return r.ok
  }
  const checks = []
  for (const name of [...new Set(required)]) {
    if (!ENVIRONMENT_CAPABILITIES.includes(name)) throw new Error(`unknown environment capability: ${name}`)
    let available = false
    try {
      if (Date.now() >= deadline) { checks.push({ capability: name, status: 'environment_unavailable', reason: 'capability_check_timeout' }); continue }
      if (name === 'node') available = await command(name, process.execPath, ['-e', 'process.exit(Number(process.versions.node.split(".")[0]) >= 24 ? 0 : 1)'])
      if (name === 'python') available = await command(name, 'python3', ['-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'])
      if (name === 'bwrap') available = await command(name, env.ARGUS_BWRAP_BIN ?? 'bwrap', ['--ro-bind', '/', '/', '--dev', '/dev', '--proc', '/proc', '--unshare-pid', 'true'])
      if (name === 'chromium') available = await command(name, env.CHROMIUM_PATH ?? 'chromium', ['--version'])
      if (name === 'github_auth') available = await command(name, env.ARGUS_GH_BIN ?? 'gh', ['auth', 'status'])
      if (name === 'token') available = tokenNames.some((key) => Boolean(env[key]?.trim()))
      if (name === 'disk' || name === 'inode') {
        const fs = filesystem(cwd)
        available = name === 'disk' ? Number(fs.bavail) * Number(fs.bsize) >= minDiskBytes
          : Number(fs.files) > 0 && Number(fs.ffree) >= minFreeInodes
      }
      if (name === 'gateway' && gatewayUrl) {
        const r = await fetchImpl(gatewayUrl, { method: 'HEAD', signal: AbortSignal.timeout(Math.max(1, deadline - Date.now())) })
        await r.body?.cancel()
        available = r.ok
      }
    } catch { /* Missing programs, permissions, network and filesystem errors are environment states. */ }
    checks.push({ capability: name, status: available ? 'available' : 'environment_unavailable', reason: available ? null : 'capability_unavailable' })
  }
  return { status: checks.every((c) => c.status === 'available') ? 'available' : 'environment_unavailable', checks }
}

export function executionStatus(result) {
  if (result.timedOut || result.status === 'timeout' || result.failureDetails?.some((f) => f.type === 'test_timeout')) return 'timeout'
  if (result.unavailable || result.environment?.status === 'environment_unavailable') return 'environment_unavailable'
  if (result.runnerError || result.status === 'runner_error' || result.signal || result.exitCode == null && !result.ok) return 'runner_error'
  return result.ok ? 'pass' : 'product_fail'
}
