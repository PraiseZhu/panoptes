// 日报：结论 → 扫描范围和昨日合并入账 → PR 结果 → issue → 下一步 → 验证明细 → AI 提示和运行状态。
// 不出现代码、命令、路径、提交号；没有测试证据的 AI 猜测只进附录。
import { jobsFinishedBetween, getMeta } from './db.mjs'
import { toReadableTitle, toShanghaiClock, toShanghaiDay, toPlainZh, log } from './util.mjs'
import { toDimensionLabel } from './matrix.mjs'

const HEARTBEAT_STALE_MS = 30 * 60_000
const SCAN_STALE_MS = 6 * 3600_000
const DAY_MS = 86400_000
const MAX_RISKS_SHOWN = 2

// 结论和下一步里不许出现的含糊说法：它们会让人以为要去查（2026-09-24 #742 误判）
export const VAGUE_PHRASES = ['值得你看一眼', '确认是否符合', '是不是符合你的本意', '需留意', '仍需留意', '可能有问题', '建议确认', '看一眼']

export const VERDICTS = {
  ok: { icon: '✅', zh: '已执行检查未见新问题' },
  defect: { icon: '❌', zh: '有问题，要你处理' },
  pending: { icon: '⏸', zh: '未完成验证' },
  unsure: { icon: '❓', zh: '拿不准，要你看' },
}

// AI 给的每条提示自带句号，拼接前去掉，避免「。；」
const toClause = (text) => toPlainZh(text).replace(/[。；;.,，\s]+$/u, '')

function joinNames(names) {
  if (names.length <= 1) return names.join('')
  return `${names.slice(0, -1).join('、')}和${names[names.length - 1]}`
}

const toDetail = (j) => (j.detail_json ? JSON.parse(j.detail_json) : {})

// 项目后来声明「不做」的方面，旧任务存档里可能还记成没测到；出日报时按当前配置拿掉，避免继续催你接这项测试
function dropNotApplicableCoverage(d, profile) {
  const na = new Set(Object.keys(profile?.notApplicable ?? {}))
  if (!na.size || !d.coverage) return d
  const keep = (keys) => (keys ?? []).filter((k) => !na.has(k))
  const applicable = keep(d.coverage.applicable)
  const covered = keep(d.coverage.covered)
  return {
    ...d,
    coverage: {
      ...d.coverage,
      applicable,
      covered,
      missed: keep(d.coverage.missed),
      undetermined: keep(d.coverage.undetermined),
      undeterminedMissed: d.coverage.undeterminedMissed ? keep(d.coverage.undeterminedMissed) : undefined,
      coverage: applicable.length ? covered.length / applicable.length : null,
    },
  }
}

// 已取消的 Cindy 窗口实测，旧 AI 提示里还会催「真实宿主」；出日报时丢掉这些句子
const DROPPED_HOST_RE = /Cindy 宿主|真实 Cindy 宿主|真实插件宿主|真实窗口里装插件|冷启、导出|冷启动未验证/
function dropCancelledHostRisks(d) {
  if (d.ai?.status !== 'ok' || !d.ai.risksZh?.length) return d
  const risksZh = d.ai.risksZh.filter((t) => !DROPPED_HOST_RE.test(t))
  return { ...d, ai: { ...d.ai, risksZh } }
}

const toPrRef = (job, names) => `${names[job.repo] ?? job.repo} #${job.pr_number}`
const toPrLabel = (job, names) => `${toPrRef(job, names)}「${toPlainZh(toReadableTitle(job.pr_title))}」`
const toDayZh = (date) => {
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric' }).formatToParts(date).map((x) => [x.type, x.value]))
  return `${p.month}月${p.day}日`
}

// 每个 PR 归到四种判定之一，并给出一句理由；reasonZh 里不重复判定本身
export function toVerdict(job, d) {
  const failedNames = (d.failedSteps ?? []).map((id) => (d.results ?? []).find((r) => r.id === id)?.nameZh ?? id)
  const ran = (d.results ?? []).filter((r) => r.kind !== 'setup' && !r.unavailable).length
  const gaps = verificationGaps(d)
  if (['passed', 'passed_known_failures', 'fixed_later'].includes(job.outcome) && gaps.length) {
    return { key: 'pending', incomplete: true, reasonZh: `已执行 ${ran} 项检查；${gaps.join('；')}，不能确认全部适用检查完成` }
  }
  switch (job.outcome) {
    case 'passed': return ran ? { key: 'ok', reasonZh: `跑了 ${ran} 项检查，全部通过` } : { key: 'pending', incomplete: true, reasonZh: '没有可核实的已执行检查记录' }
    case 'passed_known_failures': return { key: 'ok', reasonZh: `跑了 ${ran} 项检查，没发现这个 PR 带来的新问题` }
    case 'docs_only': return { key: 'ok', labelZh: '仅文档，未运行测试', reasonZh: '只改了说明文档，按配置跳过测试' }
    case 'fixed_later': return { key: 'ok', reasonZh: `合并后${joinNames(failedNames)}失败过，但最新主干已经好了` }
    case 'flaky': return { key: 'unsure', reasonZh: `${joinNames(failedNames)}第一次失败、重跑通过，原因尚未确认，不能排除产品间歇性问题` }
    case 'defect':
      if (d.issueAction === 'suppressed') return { key: 'ok', reasonZh: `${joinNames(failedNames)}失败，但这个问题你之前标记过「不修」` }
      return { key: 'defect', reasonZh: `合并后${joinNames(failedNames)}失败，已确认是这个 PR 带来的` }
    case 'needs_review': return { key: 'unsure', reasonZh: toClause(d.reasonZh ?? '失败了，但系统没法确认是不是这个 PR 造成的') + (d.aiFound ? `（${(d.aiTests?.tests ?? []).filter((t) => t.result === 'fail').map((t) => t.purposeZh).join('；')}）` : '') }
    case 'not_enrolled': return { key: 'pending', reasonZh: '这个项目还没接入自动测试' }
    case 'expired': return { key: 'pending', reasonZh: '排队太久没测上，已放弃' }
    case 'infra_failed': return { key: 'pending', reasonZh: `测试环境出了问题，没测成（${toClause(d.reasonZh ?? '原因不明')}）` }
    default: return { key: 'pending', reasonZh: toClause(d.reasonZh ?? '暂时无法测试') }
  }
}

// 执行没发现新增失败，与所有适用测试均已完成，是两件独立的事实。
export function verificationGaps(d) {
  const gaps = []
  if (!d.coverage && (d.results ?? []).some((r) => r.kind !== 'setup')) gaps.push('缺少测试类型覆盖证据')
  if (d.coverage?.missed?.length) gaps.push(`${d.coverage.missed.length} 个涉及方面未覆盖`)
  const ran = (d.results ?? []).some((r) => r.kind !== 'setup' && !r.unavailable)
  // 已跑过有效检查时，Jev 灰区不再把通过任务升级成「未完成验证」去催办。
  if (!ran) {
    const unsure = d.coverage?.undeterminedMissed ?? d.coverage?.undetermined ?? []
    if (unsure.length) gaps.push(`${unsure.length} 个方面适用性未确认且缺少覆盖证据`)
  }
  const unavailable = new Set([...(d.unavailableSteps ?? []).map((s) => s.id), ...(d.results ?? []).filter((r) => r.unavailable).map((r) => r.id)])
  if (unavailable.size) gaps.push(`${unavailable.size} 项检查没跑成`)
  if ((d.aiTests?.tests ?? []).some((t) => t.result !== 'pass')) gaps.push('AI 补测未全部通过有效性验证')
  return gaps
}

export function previousDayCoverage({ db, reg, now = new Date() }) {
  const end = new Date(`${toShanghaiDay(now)}T00:00:00+08:00`)
  const start = new Date(end.getTime() - DAY_MS)
  const projects = new Map(reg.projects.filter((p) => !p.canary).map((p) => [p.repo, p]))
  const jobs = db.prepare('SELECT * FROM jobs WHERE merged_at >= ? AND merged_at < ?').all(start.toISOString(), end.toISOString())
    .filter((j) => projects.has(j.repo) && j.author_login === reg.owner.login)
  const stats = { day: toShanghaiDay(start), observed: jobs.length, executed: 0, docs: 0, notEnrolled: 0, notRun: 0, incomplete: 0, coverageUnknown: 0 }
  for (const j of jobs) {
    const d = dropNotApplicableCoverage(toDetail(j), projects.get(j.repo).profileData)
    if (j.outcome === 'docs_only') stats.docs++
    else if (j.outcome === 'not_enrolled') stats.notEnrolled++
    else if ((d.results ?? []).some((r) => r.kind !== 'setup' && !r.unavailable)) {
      stats.executed++
      if (!d.coverage) stats.coverageUnknown++
      else if (verificationGaps(d).length) stats.incomplete++
    } else stats.notRun++
  }
  return stats
}

function toIssueRef(d) {
  if (d.issuePending || !d.issueUrl) return 'issue 还在发布中（GitHub 暂时没连上），下一份日报带上链接'
  if (d.issueAction === 'commented') return `同一问题之前提过，已在原 issue 下补记录：${d.issueUrl}`
  if (d.issueAction === 'reopened') return `之前修好又复发了，已重新打开原 issue：${d.issueUrl}`
  return `新提 issue：${d.issueUrl}`
}

// issue board：按来源项目汇总全部自动 QA issue 的实时状态（不只这段时间测到的）

const SOURCE_RE = /PR \[([\w.-]+\/[\w.-]+)#(\d+)\]/
const TITLE_RE = /#(\d+) 合并后「(.+)」失败/

// 「#742 合并后单元测试失败」；标题不是自动生成的格式时退回去技术痕迹的原标题
function toIssueSummary(issue) {
  const m = TITLE_RE.exec(issue.title ?? '')
  if (m) return `#${m[1]} 合并后${m[2]}失败`
  return toPlainZh(String(issue.title ?? '').replace(/^\[PR-QA\]\s*/, ''))
}

const toAgeDays = (createdAt, now) => Math.floor((now - new Date(createdAt)) / DAY_MS)

// 返回 null（没传 issue 数据）、{ error:true }，或按项目分组的结构化数据
export function toIssueBoard({ db, reg, issues, from, to, now, canaryRepos, displayNames }) {
  if (!issues) return null
  if (issues.error) return { error: true }
  const seen = new Map()
  for (const j of db.prepare("SELECT detail_json FROM jobs WHERE outcome = 'defect'").all()) {
    const d = j.detail_json ? JSON.parse(j.detail_json) : {}
    if (!d.issueUrl) continue
    seen.set(d.issueUrl, (seen.get(d.issueUrl) ?? 0) + 1)
  }
  const inWindow = (t) => t && Date.parse(t) > Date.parse(from) && Date.parse(t) <= Date.parse(to)
  const projects = new Map()
  for (const issue of issues.issues) {
    const repo = SOURCE_RE.exec(issue.body ?? '')?.[1] ?? '其它'
    if (canaryRepos.has(repo)) continue
    const open = issue.state === 'open'
    if (!open && !inWindow(issue.closed_at)) continue
    const g = projects.get(repo) ?? { name: displayNames[repo] ?? repo, open: [], fixed: [], wontfix: [], created: [] }
    const item = { summaryZh: toIssueSummary(issue), url: issue.html_url, ageDays: toAgeDays(issue.created_at, now), times: seen.get(issue.html_url) ?? 0, reopened: issue.state_reason === 'reopened' }
    if (open) g.open.push(item)
    else if (issue.state_reason === 'not_planned') g.wontfix.push(item)
    else g.fixed.push(item)
    if (inWindow(issue.created_at)) g.created.push(item)
    projects.set(repo, g)
  }
  const all = [...projects.values()]
  return {
    projects: all,
    openCount: all.reduce((n, g) => n + g.open.length, 0),
    oldestOpenDays: Math.max(0, ...all.flatMap((g) => g.open.map((i) => i.ageDays))),
  }
}

function toIssueLines(board) {
  if (!board) return []
  const lines = ['', '🐛 issue']
  if (board.error) return [...lines, '这次没读到 GitHub 上的 issue 状态（网络或登录问题），下一份日报会补上。']
  const created = board.projects.flatMap((g) => g.created.map((i) => `${g.name} ${i.summaryZh}`))
  const fixed = board.projects.flatMap((g) => g.fixed.map((i) => `${g.name} ${i.summaryZh}`))
  const wontfix = board.projects.flatMap((g) => g.wontfix.map((i) => `${g.name} ${i.summaryZh}`))
  if (!created.length && !fixed.length && !wontfix.length && board.openCount === 0) return [...lines, '没有新 issue，也没有没解决的 issue。']
  lines.push(`新提：${created.length ? created.join('；') : '没有'}`)
  if (fixed.length) lines.push(`已修好关掉：${fixed.join('；')}`)
  if (wontfix.length) lines.push(`已标记不修（以后同样的问题不再提醒）：${wontfix.join('；')}`)
  if (board.openCount === 0) lines.push('还没解决的：没有')
  else {
    lines.push(`还没解决的（${board.openCount}）：`)
    for (const g of board.projects) {
      for (const i of [...g.open].sort((a, b) => b.ageDays - a.ageDays)) {
        const facts = [i.ageDays < 1 ? '今天刚提' : `已挂 ${i.ageDays} 天`]
        if (i.times > 1) facts.push(`出现过 ${i.times} 次`)
        if (i.reopened) facts.push('修好后又复发过')
        lines.push(`• ${g.name} ${i.summaryZh}（${facts.join('，')}）：${i.url}`)
      }
    }
  }
  return lines
}

// 结论和下一步里出现含糊说法就记错误日志；测试会拦住模板里的含糊说法
export function findVaguePhrases(text) {
  const head = text.split('\n附录（AI 提示和运行状态）')[0]
  const hits = []
  for (const line of head.split('\n')) {
    if (!/^(结论|\d+\.【)/.test(line)) continue
    for (const p of VAGUE_PHRASES) if (line.includes(p)) hits.push(p)
  }
  return hits
}

export function buildDailyReport({ db, reg, now = new Date(), diskFreeGb = null, issues = null, from: fromOverride = null }) {
  const from = fromOverride ?? getMeta(db, 'last_report_cutoff') ?? new Date(reg.enabledSince).toISOString()
  const to = now.toISOString()
  // 金丝雀仓只用于 Argus 自检，不混进给人看的结论
  const canaryRepos = new Set(reg.projects.filter((p) => p.canary).map((p) => p.repo))
  const allJobs = jobsFinishedBetween(db, from, to)
  const jobs = allJobs.filter((j) => !canaryRepos.has(j.repo))
  const canaryCount = allJobs.length - jobs.length
  const names = Object.fromEntries(reg.projects.map((p) => [p.repo, p.displayName ?? p.repo]))
  const board = toIssueBoard({ db, reg, issues, from, to, now, canaryRepos, displayNames: names })

  const profiles = Object.fromEntries(reg.projects.map((p) => [p.repo, p.profileData]))
  const rows = jobs.map((j) => {
    const d = dropCancelledHostRisks(dropNotApplicableCoverage(toDetail(j), profiles[j.repo]))
    return { j, d, v: toVerdict(j, d) }
  })
  const of = (key) => rows.filter((r) => r.v.key === key)
  const pending = db.prepare("SELECT repo, status FROM jobs WHERE status IN ('queued', 'running')").all().filter((j) => !canaryRepos.has(j.repo))

  const heartbeat = getMeta(db, 'heartbeat_at')
  const serviceDown = !heartbeat || now - new Date(heartbeat) > HEARTBEAT_STALE_MS
  const pollError = getMeta(db, 'last_poll_error')
  const staleScans = reg.projects.filter((p) => {
    const t = getMeta(db, `scan_ok_at:${p.repo}`)
    return !t || now - new Date(t) > SCAN_STALE_MS
  })
  const openIssues = board && !board.error ? board.openCount : 0
  const newDefectUrls = new Set(of('defect').map((r) => r.d.issueUrl).filter(Boolean))
  const oldOpenIssues = board && !board.error ? board.projects.flatMap((g) => g.open).filter((i) => !newDefectUrls.has(i.url)).length : 0

  // 下一步：只放真要做的事。【要做】= 不做就会漏掉问题；【可选】必须写明不做会怎样
  const todo = []
  const optional = []
  if (serviceDown) todo.push({ shortZh: 'QA 服务停了', textZh: `QA 服务${heartbeat ? `从 ${toShanghaiClock(new Date(heartbeat))} 起` : ''}没在工作：看一下 Mini 是否开机、是否断网。不处理的话之后合并的 PR 都不会被测。` })
  for (const { j, d } of of('defect')) todo.push({ shortZh: `${toPrRef(j, names)} 有问题`, textZh: `打开 ${toPrRef(j, names)} 的 issue，决定修不修。修好后关掉；不修就关掉时选「不修」，以后不再提醒。${d.issueUrl ? d.issueUrl : ''}` })
  for (const { j, d } of of('unsure')) {
    todo.push(d.aiFound
      ? { shortZh: `${toPrRef(j, names)} AI 补测发现疑似问题`, textZh: `${toPrRef(j, names)} 的 AI 补测在合并后失败、合并前通过（试运行期，没自动提 issue）。让 AI 助手把失败详情整理给你，判断是真问题还是补测写错了；是真问题就说一声，我来提 issue。` }
      : { shortZh: `${toPrRef(j, names)} 拿不准`, textZh: `${toPrRef(j, names)} 有检查失败，但系统没法确认是不是这个 PR 造成的，所以没提 issue。让 AI 助手把失败详情整理给你，判断要不要修。` })
  }
  if (oldOpenIssues) todo.push({ shortZh: `${oldOpenIssues} 个之前的 issue 没解决`, textZh: `处理之前提的 ${oldOpenIssues} 个还没解决的 issue${board.oldestOpenDays >= 3 ? `（最久的已经挂了 ${board.oldestOpenDays} 天）` : ''}，清单见上面「issue」段。` })
  const disturbed = [...new Set(rows.flatMap(({ d }) => d.disturbedSteps ?? []))]
  if (disturbed.length) todo.push({ shortZh: '测试打扰了 Mini 桌面', textZh: `${disturbed.join('、')}运行时 Mini 上弹出了系统密码框（本该静默）。看一下 Mini 桌面，把弹框取消掉；然后让 AI 助手排查这一步为什么没静默。` })
  if (diskFreeGb != null && diskFreeGb < 12) todo.push({ shortZh: 'Mini 磁盘不够', textZh: `Mini 磁盘只剩 ${diskFreeGb} GB，测试已经自动暂停，清理后会自动恢复。` })
  else if (diskFreeGb != null && diskFreeGb < 20) optional.push(`Mini 磁盘只剩 ${diskFreeGb} GB，找时间清理一下。不清理的话，低于 12 GB 时测试会自动暂停。`)
  if (staleScans.length) optional.push(`${staleScans.map((p) => p.displayName ?? p.repo).join('、')} 超过 6 小时没从 GitHub 读到最新合并记录，系统会自动补扫。持续的话多半是 Mini 断网或 GitHub 登录过期，到时这期间合并的 PR 会晚测。`)
  else if (pollError) optional.push('最近一次读取 GitHub 失败了，系统会自动重试。持续失败的话，新合并的 PR 会一直排不上队，多半是 GitHub 登录过期。')
  const notEnrolled = [...new Set(of('pending').filter(({ j }) => j.outcome === 'not_enrolled').map(({ j }) => names[j.repo] ?? j.repo))]
  if (notEnrolled.length) optional.push(`${notEnrolled.join('、')} 有 PR 合并但还没接入自动测试，要的话让 AI 助手接入。不接的话这些项目的 PR 不会被测。`)
  const incomplete = of('pending').filter(({ v }) => v.incomplete)
  if (incomplete.length) todo.push({ shortZh: `${incomplete.length} 个 PR 验证不完整`, textZh: `为上面 ${incomplete.length} 个验证不完整的 PR 补齐未覆盖或没跑成的检查，再确认结果。已结束任务不会因后续接入测试而自动补跑。` })

  // 结论
  let conclusion
  const docCount = rows.filter(({ j }) => j.outcome === 'docs_only').length
  const checkedOk = of('ok').length - docCount
  if (todo.length === 0) {
    conclusion = '今天不用你处理。'
    if (rows.length === 0) conclusion += '这段时间没有新测完的 PR'
    else if (of('ok').length === rows.length && !docCount) conclusion += `更新了 ${rows.length} 个 PR 的结果，已执行检查未见新问题`
    else conclusion += `更新了 ${rows.length} 个 PR 的结果：${checkedOk} 个已执行检查未见新问题，${of('pending').length} 个未完成验证${docCount ? `，${docCount} 个仅文档未测试` : ''}`
    conclusion += board && !board.error ? '，没有新 issue，也没有没解决的 issue。' : '，没有新 issue。'
  } else {
    conclusion = `今天有 ${todo.length} 件事要你处理：${todo.map((t) => t.shortZh).join('；')}。`
    if (checkedOk) conclusion += `另有 ${checkedOk} 个 PR 的已执行检查未见新问题。`
    if (docCount) conclusion += `${docCount} 个 PR 仅文档未测试。`
  }

  const out = []
  out.push(`Argus QA 日报 · ${toDayZh(now)}（${toShanghaiClock(new Date(from))} 以来）`)
  out.push('')
  out.push(`结论：${conclusion}`)
  const previous = previousDayCoverage({ db, reg, now })
  out.push(`范围：仅登记仓库中 ${reg.owner.login} 的已合并 PR；未登记仓库及尚未合并的 PR 不在扫描范围。下方结果按上次日报以来的完成时间统计。`)
  out.push(`昨日合并入账（${previous.day}，上海时间）：${previous.observed} 个；跑过测试 ${previous.executed} 个（其中 ${previous.incomplete} 个验证不完整${previous.coverageUnknown ? `，${previous.coverageUnknown} 个缺少类型覆盖证据` : ''}），仅文档未测试 ${previous.docs} 个，未接入 ${previous.notEnrolled} 个，尚未执行 ${previous.notRun} 个。${staleScans.length || pollError ? '扫描有缺口，入账数不代表完整合并数。' : ''}`)
  const undelivered = getMeta(db, 'undelivered_report_day')
  if (undelivered) out.push(`（${Number(undelivered.slice(5, 7))}月${Number(undelivered.slice(8, 10))}日的日报可能没送到你手上，那份的内容已经合并进这份。）`)

  if (rows.length) {
    out.push('', `📋 PR 结果更新（${rows.length}）`)
    const order = ['defect', 'unsure', 'pending', 'ok']
    for (const key of order) {
      for (const { j, d, v } of of(key)) {
        out.push(`${VERDICTS[key].icon} ${toPrLabel(j, names)}：${v.labelZh ?? VERDICTS[key].zh}。${v.reasonZh}`)
        if (key === 'defect') out.push(`   ${toIssueRef(d)}`)
        if (d.transientFailureCount > 0) out.push(`   重跑结果：${d.transientFailureCount} 项首次失败、重跑通过，原因尚未确认。`)
        const cov = d.coverage
        if (cov?.applicable?.length) out.push(`   覆盖：这次改动涉及 ${cov.applicable.length} 个方面，现有检查覆盖 ${cov.covered.length} 个${cov.missed.length ? '（缺口见验证明细）' : ''}`)
      }
    }
  }

  out.push(...toIssueLines(board))

  out.push('', '👉 下一步')
  if (todo.length === 0 && optional.length === 0) out.push('不用操作。')
  let n = 0
  for (const t of todo) out.push(`${++n}.【要做】${t.textZh}`)
  for (const t of optional) out.push(`${++n}.【可选】${t}`)

  // AI 提示和运行状态；实际漏测另列验证明细。
  const appendix = []
  for (const { j, d } of rows) {
    if (d.ai?.status === 'ok' && d.ai.risksZh?.length) appendix.push(`测试没覆盖到的地方（${toPrRef(j, names)}）：${d.ai.risksZh.slice(0, MAX_RISKS_SHOWN).map(toClause).join('；')}`)
  }
  const suspects = rows.filter(({ d }) => d.ai?.status === 'ok' && d.ai.suspectZh)
  for (const { j, d } of suspects) appendix.push(`AI 的猜测（${toPrRef(j, names)}，没有测试证据）：${toClause(d.ai.suspectZh)}`)
  const MAX_MISSED = 6
  const verification = []
  for (const { j, d } of rows) {
    const missed = d.coverage?.missed ?? []
    if (missed.length) verification.push(`没测到的方面（${toPrRef(j, names)}）：${missed.slice(0, MAX_MISSED).map(toDimensionLabel).join('、')}${missed.length > MAX_MISSED ? ` 等 ${missed.length} 个` : ''}。需要补测，当前任务不会自动重跑`)
    for (const s of d.skippedSteps ?? []) appendix.push(`分诊跳过的检查（${toPrRef(j, names)}）：${s.nameZh}，${toClause(s.reasonZh)}`)
    if (d.triage?.status === 'skipped' && (d.results ?? []).length) appendix.push(`这次没做分诊（${toPrRef(j, names)}）：${toClause(d.triage.reasonZh ?? '原因不明')}，所以可选检查全部照跑`)
  }
  for (const { j, d } of rows) {
    const at = d.aiTests
    if (!at) continue
    const ref = toPrRef(j, names)
    const passed = at.tests.filter((t) => t.result === 'pass')
    const cov = passed.filter((t) => t.kind === 'coverage')
    if (cov.length) appendix.push(`AI 补测（${ref}）：补了 ${cov.length} 个测试，都通过，而且植入错误后能发现问题：${cov.map((t) => t.purposeZh).join('；')}`)
    for (const t of passed.filter((x) => x.kind === 'suspicion')) appendix.push(`AI 怀疑的地方已写测试验证，没发现问题（${ref}）：${t.purposeZh}`)
    for (const t of at.tests.filter((x) => x.result === 'invalid')) appendix.push(`AI 补测没能证明有效，已丢弃（${ref}）：${t.purposeZh}`)
    if (!at.tests.length && at.gaps?.length && at.status !== 'skipped') appendix.push(`AI 补测没写出来（${ref}）：${toClause(at.reasonZh ?? '原因不明')}`)
  }
  const known = new Map()
  for (const { j, d } of rows) if (j.outcome === 'passed_known_failures') known.set(names[j.repo] ?? j.repo, d.knownFailureCount ?? known.get(names[j.repo] ?? j.repo))
  if (known.size) appendix.push(`老失败：${[...known].map(([p, c]) => `${p}${c ? ` ${c} 个` : ''}`).join('、')}，合并前就有，和新 PR 无关，不影响发现新问题`)
  for (const { j, d } of rows) {
    for (const u of d.unavailableSteps ?? []) verification.push(`没跑成的检查（${toPrRef(j, names)}）：${u.nameZh}，${toClause(u.reasonZh ?? '原因不明')}`)
  }
  if (pending.length) {
    const running = pending.filter((p) => p.status === 'running').length
    appendix.push(`还有 ${pending.length} 个 PR 没测完（${running ? `${running} 个正在测、` : ''}${pending.length - running} 个在排队），会出现在下一份日报里`)
  }
  if (canaryCount) appendix.push(`系统自检任务 ${canaryCount} 个（金丝雀仓），不计入结论`)
  appendix.push(`系统状态：${serviceDown ? '服务没在工作' : '服务正常'}${diskFreeGb != null ? `；磁盘剩 ${diskFreeGb} GB` : ''}`)
  if (verification.length) out.push('', '验证明细（尚未完成的检查）', ...verification.map((v) => `· ${v}`))
  out.push('', '附录（AI 提示和运行状态）')
  for (const a of appendix) out.push(`· ${a}`)
  if (suspects.length) out.push('· 上面「AI 的猜测」只是读代码的判断，没有测试证据；AI 补测会针对怀疑的地方写测试验证，确认有问题才会进结论')

  const text = out.join('\n')
  const vague = findVaguePhrases(text)
  if (vague.length) log('error', 'daily report contains vague phrases', { phrases: vague })
  return { text, cutoff: to, jobCount: jobs.length, defects: of('defect').length, todo: todo.length }
}
