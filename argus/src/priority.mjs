// 合并即测插队（scripts/qa-slot.sh 的 Node 一侧）：占着 QA 槽位的运行（夜间 / 补测子运行）在两个 PR 之间看一眼
// $ARGUS_ROOT/priority/req-<pid>；有活的插队请求就写 yield-<pid> 让出槽位，等到没有活请求再接着测下一个 PR。
// 槽位上限仍是 1：让出期间本进程不测任何东西。插队运行自己（PANOPTES_PRIORITY=1）不让。
import { existsSync, mkdirSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const alive = (pid) => { try { process.kill(pid, 0); return true } catch (e) { return e.code === 'EPERM' } }

export function liveRequests(dir) {
  if (!existsSync(dir)) return 0
  let n = 0
  for (const f of readdirSync(dir)) {
    const m = /^req-(\d+)$/.exec(f)
    if (!m) continue
    if (alive(Number(m[1]))) n++
    else rmSync(join(dir, f), { force: true })
  }
  return n
}

export async function yieldToPriority({
  dir = process.env.ARGUS_ROOT ? join(process.env.ARGUS_ROOT, 'priority') : null,
  env = process.env, pollMs = 2000, maxWaitMs = 4 * 3600_000, sleep = (ms) => new Promise((r) => setTimeout(r, ms)), onYield = () => {},
} = {}) {
  if (!dir || env.PANOPTES_PRIORITY === '1' || env.PANOPTES_YIELD === '0') return { yielded: false, waitedMs: 0 }
  if (!liveRequests(dir)) return { yielded: false, waitedMs: 0 }
  mkdirSync(dir, { recursive: true })
  const ack = join(dir, `yield-${process.pid}`)
  writeFileSync(ack, '')
  onYield()
  const t0 = Date.now()
  try {
    while (liveRequests(dir) && Date.now() - t0 < maxWaitMs) await sleep(pollMs)
  } finally { rmSync(ack, { force: true }) }
  return { yielded: true, waitedMs: Date.now() - t0 }
}
