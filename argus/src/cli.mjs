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
//   node src/cli.mjs nightly [--date YYYY-MM-DD] [--pr N ...] [--limit N] [--no-review] [--skip-step ID ...] [--out PATH] [--no-reuse] [--pr-timeout MIN] [--deadline HH:MM] [--write-merged | --no-write-merged] [--write-open] [--no-issues | --create-issues]
//                                          Linux 夜间 QA：上海时区前一天的 PR → reports/YYYY-MM-DD.json（默认不写 GitHub）
//   node src/cli.mjs assemble --date D --out PATH --from FILE ... [--order N,N]   多份报告合成 full-D.json
import { mkdirSync } from 'node:fs'
import { loadRegistry, loadSecrets } from './config.mjs'
import { openDb, countByStatus, getMeta } from './db.mjs'
import { serve, pollOnce, diskFreeGb, processNextOnce } from './control.mjs'
import { sendDailyReport, sendNotice } from './daily.mjs'
import { currentUser, appFor } from './github.mjs'
import { probeJev } from './jev.mjs'
import { ALL_DIRS } from './paths.mjs'
import { OUTCOMES } from './decide.mjs'
import { runNightly } from './nightly.mjs'
import { assembleFiles } from './assemble.mjs'
import { killAllActive } from './exec.mjs'

const [cmd, ...rest] = process.argv.slice(2)
const flag = (name) => rest.includes(`--${name}`)
const opt = (name, dflt = null) => {
  const i = rest.indexOf(`--${name}`)
  return i >= 0 ? rest[i + 1] : dflt
}

async function main() {
  for (const d of ALL_DIRS) mkdirSync(d, { recursive: true })
  switch (cmd) {
    case 'nightly': {
      // 被终止时先收掉子进程组（bwrap 自带 --die-with-parent，git 等直接子进程靠这里）
      for (const sig of ['SIGTERM', 'SIGINT']) process.once(sig, () => { killAllActive(); process.exit(143) })
      const many = (name) => rest.flatMap((a, i) => (a === `--${name}` ? [rest[i + 1]] : []))
      const date = opt('date')
      if (date && !/^\d{4}-\d{2}-\d{2}$/.test(date)) throw new Error('--date 需要 YYYY-MM-DD')
      const prs = many('pr').map((n) => { const v = Number(n); if (!Number.isInteger(v) || v <= 0) throw new Error(`--pr 需要正整数：${n}`); return v })
      if (flag('create-issues') && flag('no-issues')) throw new Error('--create-issues 与 --no-issues 不能同时使用')
      const out = opt('out')
      if (rest.includes('--out') && (!out || out.startsWith('--'))) throw new Error('--out 需要文件路径')
      const prTimeout = opt('pr-timeout') ?? process.env.ARGUS_PR_TIMEOUT_MIN ?? null
      if (prTimeout != null && !(Number(prTimeout) > 0)) throw new Error(`--pr-timeout 需要正数分钟：${prTimeout}`)
      const deadline = opt('deadline') ?? process.env.ARGUS_DEADLINE ?? null
      const { file, report } = await runNightly({ date, prs, out, reuseMerged: !flag('no-reuse'), limit: opt('limit') ? Number(opt('limit')) : null, noReview: flag('no-review'), skipSteps: many('skip-step'), createIssues: flag('create-issues'), writeMerged: flag('write-merged') ? true : flag('no-write-merged') ? false : undefined, writeOpen: flag('write-open'), prTimeoutMin: prTimeout != null ? Number(prTimeout) : null, deadline })
      console.log(JSON.stringify({ report: file, date: report.date, prs: report.prs.map((p) => ({ number: p.number, severity: p.severity, verdict: p.verdict })), system_health: report.system_health.status }, null, 2))
      if (report.system_health.status === 'failed') process.exitCode = 3
      return
    }
    case 'assemble': {
      // assemble --date D --out PATH --from FILE [--from FILE ...] [--order 1,2,3]：后面的 --from 覆盖前面同号 PR
      const files = rest.flatMap((a, i) => (a === '--from' ? [rest[i + 1]] : []))
      const date = opt('date'); const out = opt('out')
      if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date) || !out || !files.length) throw new Error('assemble 需要 --date YYYY-MM-DD --out PATH --from FILE ...')
      const order = opt('order') ? opt('order').split(',').map(Number) : null
      const doc = assembleFiles({ date, files, out, order })
      console.log(JSON.stringify({ out, prs: doc.prs.map((p) => ({ number: p.number, verdict: p.verdict, severity: p.severity })), system_health: doc.system_health.status, complete: doc.system_health.complete }, null, 2))
      return
    }
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
      console.error('未知命令。可用：nightly | assemble | serve | daily-report [--dry-run] | status | runs | poll-once | notify --text | doctor')
      process.exitCode = 2
  }
}

main().catch((err) => {
  console.error(err.message)
  process.exitCode = 1
})
