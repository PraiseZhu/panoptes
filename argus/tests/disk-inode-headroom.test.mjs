// inf1-017aa4a1f1c8125d96a1 回归保护（11-05 02:42，PR #1729）：
// 报错是 `ENOSPC: no space left on device, mkdir '/tmp/argus-run-M95tCP/argus-n1729-h1-mw2grl7o/step-plugin'`，
// 但同一轮 system_health.environment.disk_free_gb = 53 —— 箱子的 /dev/vdb 是「字节还多、inode 用满」：
// 2,097,152 个 inode 里只剩 20,671（100% 用满），于是 mkdir 拿不到 inode 就返回 ENOSPC（同一轮留证据时
// mkdir '/workspace/mivo-qa/argus-root/app/evidence/.../pr-1729/h1' 也 ENOSPC，同一个文件系统）。
//
// 现有机制只看字节：envSnapshot 记 disk_free_gb，nightly 用 `< 12 GB` 判低磁盘，statfs 的 files/ffree 从没读过。
// 结果是「inode 用满」这半台机器的事实永远不出现在报告里，PR 每次只剩一条裸 ENOSPC 的 qa_tool_failures/pipeline，
// 诊断和原因对不上，同一个签名就一直复发。这里钉住两条：
//   1) system_health.environment 必须记 inode（只剩字节的诊断会一直说「53 GB 还剩着」）；
//   2) inode 用满必须是 env_problem（箱子要清理），且消息里带数字，不能让整轮看起来像正常环境。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

// envSnapshot 对 $ARGUS_ROOT/cache（paths.mjs 的 CACHE_DIR）做 statfs，目录不存在时会静默跳过测量
process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-inode-'))
mkdirSync(join(process.env.ARGUS_ROOT, 'cache'), { recursive: true })
const { envSnapshot, checkDiskInodes, finalizeHealth } = await import('../src/nightly.mjs')

// 11-05 02:42 那台箱子的真实数字（df -i /dev/vdb：2097152 个 inode，2076481 已用；df -h：53 GB avail，
// 与报告里的 system_health.environment.disk_free_gb 一致）
const BOX_1105 = { disk_free_gb: 53, disk_free_inodes: 20671, disk_inodes_total: 2097152 }
const num = (s) => Number(String(s).replace(/[,\s]/g, ''))
const hasNumber = (msg, n) => [...String(msg).matchAll(/\d[\d,]*/g)].some((m) => num(m[0]) === n)

test('system_health.environment 记 inode：只剩字节的诊断看不出「建不出目录」', () => {
  const env = envSnapshot()
  assert.equal(typeof env.disk_free_inodes, 'number', 'environment 缺 disk_free_inodes：inode 用满时报告只能说「53 GB 还剩着」')
  assert.equal(typeof env.disk_inodes_total, 'number', 'environment 缺 disk_inodes_total')
  assert.ok(env.disk_inodes_total > 0)
  assert.ok(env.disk_free_inodes >= 0 && env.disk_free_inodes <= env.disk_inodes_total)
})

test('inode 用满（20,671 / 2,097,152）而字节还剩 53 GB → env_problem，并说清 mkdir 会 ENOSPC', () => {
  const health = { status: 'ok', qa_tool_failures: [], env_problems: [], llm_errors: [], environment: { ...BOX_1105 }, notes: [] }
  checkDiskInodes(health)
  assert.equal(health.env_problems.length, 1, `期望 1 条 env_problem，实际 ${JSON.stringify(health.env_problems)}`)
  const msg = health.env_problems[0].message
  assert.match(msg, /inode/i, `消息要点名 inode：${msg}`)
  assert.ok(hasNumber(msg, 20671), `消息要带剩余 inode 数：${msg}`)
  assert.ok(hasNumber(msg, 2097152), `消息要带 inode 总数：${msg}`)
  assert.match(msg, /enospc|no space/i, `消息要说清后果是 ENOSPC：${msg}`)
  // 这一轮不能算「环境正常」：env_problem 必须把 system_health 拉出 ok
  finalizeHealth(health, [], new Date('2026-11-04T18:42:06Z'))
  assert.notEqual(health.status, 'ok')
})

test('inode 充足、或环境里没有 inode 数据（旧报告 / statfs 读不到）时不报警', () => {
  const ok = { environment: { disk_free_gb: 53, disk_free_inodes: 800000, disk_inodes_total: 2097152 }, env_problems: [] }
  checkDiskInodes(ok)
  assert.deepEqual(ok.env_problems, [])
  const legacy = { environment: { disk_free_gb: 53 }, env_problems: [] }
  checkDiskInodes(legacy)
  assert.deepEqual(legacy.env_problems, [])
  const unreadable = { environment: { disk_free_gb: 53, disk_free_inodes: null, disk_inodes_total: null }, env_problems: [] }
  checkDiskInodes(unreadable)
  assert.deepEqual(unreadable.env_problems, [])
})
