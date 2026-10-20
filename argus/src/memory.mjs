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

// 机器负载快照（写进报告当证据）：1/5/15 分钟 loadavg、核数、MemAvailable
export const machineLoad = () => ({ loadavg: loadavg().map((x) => Math.round(x * 100) / 100), cores: cpus().length, mem_available_gb: memAvailableGb(), at: new Date().toISOString() })
// 空闲：1 分钟 loadavg ≤ 核数 × 0.5 且 MemAvailable ≥ 3 GB
export const isIdle = (m) => m.loadavg[0] <= m.cores * 0.5 && (m.mem_available_gb ?? 0) >= 3
