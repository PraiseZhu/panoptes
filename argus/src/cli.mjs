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
//   node src/cli.mjs nightly [--date YYYY-MM-DD] [--pr N ...] [--limit N] [--no-review] [--no-ai-tests] [--skip-step ID ...] [--out PATH] [--no-reuse] [--pr-timeout MIN] [--deadline HH:MM] [--write-merged | --no-write-merged] [--write-open] [--no-issues | --create-issues] [--trigger event|catch-up|manual] [--burst-cutoff [HH:MM]] [--no-step-cache]
//                                          Linux 夜间 QA：上海时区前一天的 PR → reports/YYYY-MM-DD.json（默认不写 GitHub）
//   node src/cli.mjs catch-up [--wait-lock S] [--repo owner/name] [--trigger catch-up|manual|event] [--create-issues]   补测昨天+今天（上海）合并但 reports/merged 缺口的 PR（由 run-nightly.sh --catch-up 调用）
//   node src/cli.mjs store-merged --from REPORT [--pr N ...]   把 --out 运行里测完的已合并 PR 存进 reports/merged/（旧文件 → PR-N.vN.json）
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
import { catchUp, runNightlyFor, catchUpDir } from './catchup.mjs'
import { LOGS_DIR } from './paths.mjs'
import { openSync, closeSync } from 'node:fs'
import { join } from 'node:path'
import { writeReport, normalizeTrigger, BURST_CUTOFF_DEFAULT, writePerPrReports } from './nightly.mjs'
import { readFileSync } from 'node:fs'
import { resolve as resolvePath } from 'node:path'
import { toShanghaiIso as shIso } from './util.mjs'
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
      let deadline = opt('deadline') ?? process.env.ARGUS_DEADLINE ?? null
      // --burst-cutoff [HH:MM]（默认 08:30 上海）：到点还没测完的 PR 记 not-tested（burst-cutoff），补测之后接着测
      let deadlineKind = null
      const burstArg = rest.includes('--burst-cutoff') ? (opt('burst-cutoff') && !opt('burst-cutoff').startsWith('--') ? opt('burst-cutoff') : BURST_CUTOFF_DEFAULT) : (process.env.ARGUS_BURST_CUTOFF || null)
      if (burstArg) {
        if (rest.includes('--deadline')) throw new Error('--burst-cutoff 与 --deadline 不能同时使用')
        deadline = burstArg; deadlineKind = 'burst-cutoff'
      }
      const trigger = normalizeTrigger(opt('trigger') ?? process.env.PANOPTES_TRIGGER ?? null)
      // --no-step-cache：不复用步骤结果缓存（--no-reuse 只管 merged 报告复用）
      if (flag('no-step-cache')) process.env.ARGUS_STEP_CACHE = '0'
      const { file, report } = await runNightly({ trigger, deadlineKind, date, prs, out, reuseMerged: !flag('no-reuse'), limit: opt('limit') ? Number(opt('limit')) : null, noReview: flag('no-review'), noAiTests: flag('no-ai-tests'), skipSteps: many('skip-step'), createIssues: flag('create-issues'), writeMerged: flag('write-merged') ? true : flag('no-write-merged') ? false : undefined, writeOpen: flag('write-open'), prTimeoutMin: prTimeout != null ? Number(prTimeout) : null, deadline })
      console.log(JSON.stringify({ report: file, date: report.date, prs: report.prs.map((p) => ({ number: p.number, severity: p.severity, verdict: p.verdict })), system_health: report.system_health.status }, null, 2))
      if (report.system_health.status === 'failed') process.exitCode = 3
      return
    }
    case 'catch-up': {
      const waitLock = Number(opt('wait-lock', 14400))
      if (!Number.isInteger(waitLock) || waitLock < 0) throw new Error('--wait-lock 需要整秒数')
      const repo = opt('repo', 'xindong/mivo-canvas-plugin')
      if (flag('create-issues') && flag('no-issues')) throw new Error('--create-issues 与 --no-issues 不能同时使用')
      // 子运行带上 --create-issues 只在调用方同时导出了 ARGUS_ALLOW_ISSUES=1 时才有效（run-nightly.sh 负责把关）
      const createIssues = flag('create-issues') && process.env.ARGUS_ALLOW_ISSUES === '1'
      const trigger = normalizeTrigger(opt('trigger') ?? 'catch-up')
      let logFd = null
      const runOne = (pr) => {
        if (logFd == null) logFd = openSync(join(LOGS_DIR, `catch-up-${shIso().slice(0, 19).replace(/[-:]/g, '')}.log`), 'a')
        process.stdout.write(`[catch-up] PR #${pr.number} (${pr.day}, ${pr.gap.reason}) …\n`)
        return runNightlyFor(pr, { waitLock, log: logFd, createIssues, trigger })
      }
      const r = catchUp({ repo, runOne })
      if (r.status === 'ran') { r.trigger = trigger; r.create_issues = createIssues }
      if (logFd != null) closeSync(logFd)
      if (r.status === 'nothing-to-do') return // 安静退出
      const file = writeReport(r, join(catchUpDir(), `run-${shIso().slice(0, 19).replace(/[-:]/g, '')}.json`))
      for (const run of r.runs) process.stdout.write(`[catch-up] PR #${run.number} attempt ${run.attempt}: exit ${run.exit_code}, ${run.filled ? 'filled' : `still missing (${run.remaining_gap})`}\n`)
      for (const g of r.system_health.gave_up) process.stdout.write(`[catch-up] gave up on PR #${g.pr} after ${g.attempts} attempts\n`)
      if (r.stopped) process.stdout.write(`[catch-up] stopped: ${r.stopped}; remaining gaps retry on the next invocation\n`)
      process.stdout.write(`[catch-up] result: ${file}\n`)
      if (r.system_health.status !== 'ok') process.exitCode = 3
      return
    }
    case 'store-merged': {
      // store-merged --from REPORT [--pr N ...]：把一次 --out 运行（例如 --pr N --out scratch/…）里测完的已合并 PR，
      // 用夜间同一个写入函数存成 reports/merged/PR-N.json；已有文件按版本机制改名为 PR-N.vN.json，新文件记 previous_version。
      const from = opt('from')
      if (!from) throw new Error('store-merged 需要 --from REPORT')
      const only = rest.flatMap((a, i) => (a === '--pr' ? [Number(rest[i + 1])] : []))
      const report = JSON.parse(readFileSync(from, 'utf8'))
      if (only.length) report.prs = report.prs.filter((p) => only.includes(p.number))
      // --from 可以就是 reports/merged/PR-N.json 本身（重新存成单 PR 形式：只含这个 PR、date = 合并日）；原件 → PR-N.vN.json
      const written = writePerPrReports(report, { opts: { writeMerged: true, allowSelf: true }, outFile: resolvePath(from), kind: 'merged' })
      const fails = report.system_health.qa_tool_failures.filter((f) => String(f.stage).startsWith('write-merged'))
      console.log(JSON.stringify({ from, written, failures: fails }, null, 2))
      if (fails.length || !written.length) process.exitCode = 3
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
