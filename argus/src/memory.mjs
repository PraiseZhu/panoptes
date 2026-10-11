// 内存护栏共用：/proc/meminfo 的 MemAvailable（GB，一位小数）
import { readFileSync } from 'node:fs'
export function memAvailableGb(meminfo) {
  try {
    const text = meminfo ?? readFileSync('/proc/meminfo', 'utf8')
    const kb = Number(text.match(/^MemAvailable:\s+(\d+)\s*kB/m)?.[1])
    return Number.isFinite(kb) ? Math.round(kb / 1048576 * 10) / 10 : null
  } catch { return null }
}
