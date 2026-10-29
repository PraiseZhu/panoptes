// 内存护栏共用：/proc/meminfo 的 MemAvailable（GB，一位小数）
import { readFileSync } from 'node:fs'
import { loadavg, cpus } from 'node:os'
export function memAvailableGb(meminfo) {
  try {
    const text = meminfo ?? readFileSync('/proc/meminfo', 'utf8')
    const kb = Number(text.match(/^MemAvailable:\s+(\d+)\s*kB/m)?.[1])
    return Number.isFinite(kb) ? Math.round(kb / 1048576 * 10) / 10 : null
  } catch { return null }
}

// MemAvailable in MiB (integer), null when unreadable
export function memAvailableMb(meminfo) {
  try {
    const kb = Number((meminfo ?? readFileSync('/proc/meminfo', 'utf8')).match(/^MemAvailable:\s+(\d+)\s*kB/m)?.[1])
    return Number.isFinite(kb) ? Math.floor(kb / 1024) : null
  } catch { return null }
}

// 10-28 运行中内存低水位：每 intervalMs 读一次 MemAvailable（一次 /proc 读取，定时器 unref 不拖住进程），stop() 返回最低值 MiB
export function startMemLowWater({ intervalMs = Number(process.env.ARGUS_MEM_SAMPLE_MS) || 5000, read = memAvailableMb } = {}) {
  let min = null
  const sample = () => { const v = read(); if (v != null && (min == null || v < min)) min = v }
  sample()
  const t = setInterval(sample, intervalMs); t.unref?.()
  return { min: () => min, stop: () => { clearInterval(t); sample(); return min } }
}

// 10-28：运行中 MemAvailable 跌破这个值（默认 1536 MiB = 1.5G）→ 失败记为环境问题（env_problem / infra_failed），不算测试失败
export const lowMemMb = (env = process.env) => Number(env.ARGUS_LOW_MEM_MB) || 1536
export const lowMemoryHit = (lowWaterMb, env = process.env) => lowWaterMb != null && lowWaterMb < lowMemMb(env)

// 机器负载快照（写进报告当证据）：1/5/15 分钟 loadavg、核数、MemAvailable
export const machineLoad = () => ({ loadavg: loadavg().map((x) => Math.round(x * 100) / 100), cores: cpus().length, mem_available_gb: memAvailableGb(), at: new Date().toISOString() })
// 空闲：1 分钟 loadavg ≤ 核数 × 0.5 且 MemAvailable ≥ 3 GB
export const isIdle = (m) => m.loadavg[0] <= m.cores * 0.5 && (m.mem_available_gb ?? 0) >= 3
