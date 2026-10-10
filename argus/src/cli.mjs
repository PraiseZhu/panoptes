#!/usr/bin/env node
// 用法：
//   node src/cli.mjs serve                 常驻 control（launchd 拉起）
//   node src/cli.mjs daily-report [--dry-run]
//   node src/cli.mjs status                队列与健康
//   node src/cli.mjs runs [--limit N]      最近任务
//   node src/cli.mjs poll-once             只轮询入账，不执行
//   node src/cli.mjs once                  跑完下一个排队任务即退出（手动验收）
//   node src/cli.mjs notify --text "..."   用 Doki 给主人发一条消息
//   node src/cli.mjs doctor                只读自检（GitHub / 模型 / 飞书 / 磁盘）
import { mkdirSync } from 'node:fs'
import { loadRegistry, loadSecrets } from './config.mjs'
import { openDb, countByStatus, getMeta } from './db.mjs'
import { serve, pollOnce, diskFreeGb, processNextOnce } from './control.mjs'
import { sendDailyReport, sendNotice } from './daily.mjs'
import { currentUser, appFor } from './github.mjs'
import { probeJev } from './jev.mjs'
import { ALL_DIRS } from './paths.mjs'
import { OUTCOMES } from './decide.mjs'

const [cmd, ...rest] = process.argv.slice(2)
const flag = (name) => rest.includes(`--${name}`)
const opt = (name, dflt = null) => {
  const i = rest.indexOf(`--${name}`)
  return i >= 0 ? rest[i + 1] : dflt
}

async function main() {
  for (const d of ALL_DIRS) mkdirSync(d, { recursive: true })
  switch (cmd) {
    case 'serve':
      return serve()
    case 'daily-report': {
      const res = await sendDailyReport({ db: openDb(), reg: loadRegistry(), secrets: loadSecrets(), dryRun: flag('dry-run') })
      if (res.text) console.log(res.text)
      console.log(`[${res.status}] ${res.day}`)
      return
    }
    case 'status': {
      const db = openDb()
      console.log(JSON.stringify({
        jobs: countByStatus(db),
        heartbeatAt: getMeta(db, 'heartbeat_at'),
        lastPollOkAt: getMeta(db, 'last_poll_ok_at'),
        lastPollError: getMeta(db, 'last_poll_error'),
        pausedReason: getMeta(db, 'paused_reason'),
        diskFreeGb: diskFreeGb(),
        lastReportCutoff: getMeta(db, 'last_report_cutoff'),
      }, null, 2))
      return
    }
    case 'runs': {
      const db = openDb()
      const rows = db.prepare('SELECT id, repo, pr_number, status, outcome, risk_class, merged_at, finished_at FROM jobs ORDER BY id DESC LIMIT ?').all(Number(opt('limit', 20)))
      for (const r of rows) console.log(`${r.id}\t${r.repo}#${r.pr_number}\t${r.status}\t${r.outcome ? OUTCOMES[r.outcome] ?? r.outcome : ''}\t${r.risk_class ?? ''}\t${r.finished_at ?? ''}`)
      return
    }
    case 'poll-once': {
      const added = await pollOnce(openDb(), loadRegistry())
      console.log(`新入账 ${added} 个任务`)
      return
    }
    case 'once': {
      const res = await processNextOnce()
      console.log(res ? JSON.stringify(res, null, 2) : '队列为空')
      return
    }
    case 'notify': {
      const text = opt('text')
      if (!text) throw new Error('notify 需要 --text')
      console.log(JSON.stringify(await sendNotice({ secrets: loadSecrets(), text, uuid: opt('uuid') })))
      return
    }
    case 'doctor': {
      const secrets = loadSecrets()
      const reg = loadRegistry()
      const me = await currentUser().catch((e) => ({ error: e.message }))
      const llm = secrets.ARGUS_LLM_BASE_URL
        ? await fetch(`${secrets.ARGUS_LLM_BASE_URL.replace(/\/$/, '')}/v1/models`, { headers: { authorization: `Bearer ${secrets.ARGUS_LLM_API_KEY}` }, signal: AbortSignal.timeout(20_000) })
          .then(async (r) => ({ status: r.status, hasLuna: JSON.stringify(await r.json()).includes('openai/gpt-6-luna') }))
          .catch((e) => ({ error: e.message }))
        : { error: '未配置' }
      console.log(JSON.stringify({
        github: me.error ? me : { login: me.login, matchesOwner: me.id === reg.owner.id },
        githubApp: await appFor(reg.issueRepo).then((a) => (a ? { botLogin: a.botLogin, repos: [...a.repos] } : '未配置或未覆盖 issue 仓')).catch((e) => ({ error: e.message })),
        llm,
        typesafe: await probeJev(secrets),
        feishu: { configured: Boolean(secrets.ARGUS_FEISHU_APP_ID && secrets.ARGUS_FEISHU_APP_SECRET && secrets.ARGUS_FEISHU_OPEN_ID) },
        diskFreeGb: diskFreeGb(),
        projects: reg.projects.map((p) => ({ repo: p.repo, enrolled: Boolean(p.profileData) })),
      }, null, 2))
      return
    }
    default:
      console.error('未知命令。可用：serve | daily-report [--dry-run] | status | runs | poll-once | notify --text | doctor')
      process.exitCode = 2
  }
}

main().catch((err) => {
  console.error(err.message)
  process.exitCode = 1
})
