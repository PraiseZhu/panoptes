// 唯一发布器：确认缺陷 → outbox → issue；同一缺陷只追加评论；POST 结果不明先回查再最多补发一次
import { enqueueOutbox, updateOutbox, getDefect, saveDefect, pendingOutbox, patchJobDetail } from './db.mjs'
import { createIssue, commentIssue, findIssueByMarker, findCommentByMarker, findIssueByFingerprint, getIssue, reopenIssue, publisherLogin } from './github.mjs'
import { redact, sha1, tail, toReadableTitle, log } from './util.mjs'

export function defectFingerprint(repo, decision, results) {
  // 不含 PR 号、SHA、时间。认得出失败用例时直接用用例名；否则取失败输出末尾几行、归一化数字后作签名
  const named = (decision.failureKeys ?? []).filter((k) => !k.endsWith('::*'))
  if (named.length) return `v1-${sha1(`${repo}|${[...named].sort().join('||')}`).slice(0, 16)}`
  const sig = decision.failedSteps.map((id) => {
    const r = results.find((x) => x.id === id)
    const lines = String(r?.tail ?? '').split('\n').filter((l) => /fail|error|✗|×|assert/i.test(l)).slice(-5)
    return `${id}:${lines.map((l) => l.replace(/\d+(\.\d+)?(ms|s)?/g, 'N').replace(/\s+/g, ' ').trim()).join('|')}`
  })
  return `v1-${sha1(`${repo}|${sig.join('||')}`).slice(0, 16)}`
}

export function buildIssue({ job, ownerLogin, decision, results, ai, fingerprint, publicationId, displayName }) {
  const failed = decision.failedSteps.map((id) => results.find((r) => r.id === id)).filter(Boolean)
  const title = `[PR-QA] ${displayName} #${job.pr_number} 合并后「${failed.map((f) => f.nameZh).join('、')}」失败`
  const body = [
    `@${ownerLogin}`,
    '',
    '## 发生了什么',
    `PR [${job.repo}#${job.pr_number}](${job.pr_url})（${toReadableTitle(job.pr_title)}）合并后，自动 QA 在合并版本上跑项目自带检查，下列检查失败：${failed.map((f) => f.nameZh).join('、')}。`,
    '',
    `判定依据：${decision.reasonZh}。`,
    decision.failedTests?.length ? `\n新失败的用例（${decision.failedTests.length} 个）：\n${decision.failedTests.slice(0, 20).map((t) => `- ${t}`).join('\n')}` : '',
    decision.knownFailureCount ? `\n另有 ${decision.knownFailureCount} 个失败在合并前就存在，不算在这个 PR 头上。` : '',
    ai?.status === 'ok' && ai.failureZh ? `\nAI 解读（仅供参考）：${ai.failureZh}` : '',
    '',
    '## 怎么复现',
    `1. 检出合并版本 \`${job.merge_sha.slice(0, 12)}\``,
    `2. 按项目说明安装依赖后运行：${failed.map((f) => `\`${f.cmdText}\``).join('、')}`,
    '',
    '## 失败输出（尾部，已脱敏）',
    ...failed.map((f) => `<details><summary>${f.nameZh}（退出码 ${f.exitCode}${f.timedOut ? '，超时' : ''}）</summary>\n\n\`\`\`\n${redact(tail(f.tail, 3500)).replace(/```/g, "'''")}\n\`\`\`\n</details>`),
    '',
    '---',
    `argus-fingerprint: ${fingerprint}`,
    `argus-publication: ${publicationId}`,
    '由 Argus 自动 QA 生成；建好后不会再改正文，同一问题的新观察会追加评论。',
  ].filter((l) => l !== '').join('\n')
  return { title: redact(title), body: redact(body) }
}

// 返回 { issueUrl, issueNumber, action: 'created' | 'commented' | 'reopened' | 'suppressed' | 'pending' }
// 读 GitHub 失败等临时错误不会降级成「新开 issue」：整件事记进 defect 发件箱，下一轮重试
export async function publishDefect({ db, reg, job, decision, results, ai, displayName }) {
  const fingerprint = defectFingerprint(job.repo, decision, results)
  const ctx = { job, decision, results: slimResults(results), ai, displayName, fingerprint }
  const ob = enqueueOutbox(db, 'defect', `defect:${job.id}`, ctx)
  try {
    const res = await publishCore(db, reg, ctx)
    // 新建 issue 的补发由 issue 发件箱自己负责，这里就算交接完成，避免两条路径重复处理
    if (res.action !== 'pending' || res.via === 'issue') updateOutbox(db, ob.id, 'sent', res)
    return res
  } catch (err) {
    updateOutbox(db, ob.id, 'unknown', { error: redact(err.message).slice(0, 300) })
    log('error', 'defect publish deferred', { job: job.id, err: err.message })
    return { action: 'pending', fingerprint }
  }
}

const slimResults = (results) => results.map((r) => ({ id: r.id, nameZh: r.nameZh, kind: r.kind, cmdText: r.cmdText, ok: r.ok, exitCode: r.exitCode, timedOut: r.timedOut, tail: r.ok ? '' : tail(r.tail, 4000) }))

async function publishCore(db, reg, { job, decision, results, ai, displayName, fingerprint }) {
  let existing = getDefect(db, fingerprint)
  if (!existing) {
    // 本地没绑定时，先到目标仓按指纹找一遍：换机器、重建账本或多套部署时不重复开 issue
    const found = await findIssueByFingerprint(reg.issueRepo, fingerprint)
    if (found) {
      saveDefect(db, { fingerprint, issueRepo: reg.issueRepo, issueNumber: found.number, issueUrl: found.html_url, jobId: job.id })
      existing = getDefect(db, fingerprint)
    }
  }
  if (existing) {
    const issue = await getIssue(existing.issue_repo, existing.issue_number)
    const ref = { issueUrl: existing.issue_url, issueNumber: existing.issue_number, fingerprint }
    if (issue.state === 'closed' && issue.state_reason === 'not_planned') return { ...ref, action: 'suppressed' }
    const failedNames = decision.failedSteps.map((id) => results.find((r) => r.id === id)?.nameZh ?? id).join('、')
    if (issue.state === 'closed') {
      // 之前修好关掉的问题又出现：重开同一条（Jev reopen_old 0.70），历史集中在一处
      await reopenIssue(existing.issue_repo, existing.issue_number)
      await commentOnce(db, existing, job, fingerprint, `这个问题又出现了（之前已关闭）：[${job.repo}#${job.pr_number}](${job.pr_url})（${toReadableTitle(job.pr_title)}）合并后，${failedNames} 再次失败，已重新打开。`)
      return { ...ref, action: 'reopened' }
    }
    await commentOnce(db, existing, job, fingerprint, `又观察到一次同样的问题：[${job.repo}#${job.pr_number}](${job.pr_url})（${toReadableTitle(job.pr_title)}）合并后，${failedNames} 仍失败。`)
    return { ...ref, action: 'commented' }
  }
  const publicationId = `argus-pub-${job.id}-${fingerprint}`
  const { title, body } = buildIssue({ job, ownerLogin: reg.owner.login, decision, results, ai, fingerprint, publicationId, displayName })
  const ob = enqueueOutbox(db, 'issue', publicationId, { repo: reg.issueRepo, title, body, labels: ['pr-qa'], jobId: job.id, fingerprint })
  return deliverIssue(db, reg, ob)
}

// 每个任务对每条 issue 最多一条观察评论；上次结果不明时先按标记回查，查不了就不发
async function commentOnce(db, existing, job, fingerprint, text) {
  const marker = `argus-observation: job-${job.id}`
  const ob = enqueueOutbox(db, 'issue_comment', `comment:${fingerprint}:${job.id}`, { repo: existing.issue_repo, number: existing.issue_number })
  if (ob.status === 'sent') return
  if (ob.status === 'unknown' || ob.tries > 0) {
    const found = await findCommentByMarker(existing.issue_repo, existing.issue_number, marker)
    if (found) { updateOutbox(db, ob.id, 'sent', { commentUrl: found.html_url }); return }
  }
  try {
    const c = await commentIssue(existing.issue_repo, existing.issue_number, redact(`${text}\n\nargus-fingerprint: ${fingerprint}\n${marker}`))
    updateOutbox(db, ob.id, 'sent', { commentUrl: c?.html_url })
  } catch (err) {
    updateOutbox(db, ob.id, err.httpStatus && err.httpStatus < 500 ? 'failed' : 'unknown', { error: redact(err.message).slice(0, 300) })
    throw err
  }
}

export async function deliverIssue(db, reg, ob) {
  const p = JSON.parse(ob.payload_json)
  const marker = `argus-publication: ${ob.dedupe_key}`
  if (ob.status === 'sent') {
    const r = JSON.parse(ob.result_json)
    return { issueUrl: r.issueUrl, issueNumber: r.issueNumber, action: 'created', fingerprint: p.fingerprint }
  }
  if (ob.status === 'unknown' || ob.tries > 0) {
    // 上次结果不明：先按创建者 + 时间窗回查 marker；回查本身失败就保持不明，绝不盲目补发（Jev hold_and_retry 0.98）
    let found
    try {
      found = await findIssueByMarker(p.repo, await publisherLogin(p.repo, reg.owner.login), marker, new Date(Date.parse(ob.created_at) - 3600_000).toISOString())
    } catch (err) {
      log('error', 'issue lookup failed, holding', { dedupe: ob.dedupe_key, err: err.message })
      return { action: 'pending', via: 'issue', fingerprint: p.fingerprint }
    }
    if (found) return bind(db, ob, p, found)
    if (ob.tries >= 2) {
      updateOutbox(db, ob.id, 'failed', { reasonZh: '发布结果两次不明，已停止补发' })
      return { action: 'pending', via: 'issue', fingerprint: p.fingerprint }
    }
  }
  try {
    const issue = await createIssue(p.repo, { title: p.title, body: p.body, labels: p.labels })
    return bind(db, ob, p, issue)
  } catch (err) {
    const definite = err.httpStatus && err.httpStatus >= 400 && err.httpStatus < 500
    updateOutbox(db, ob.id, definite ? 'failed' : 'unknown', { error: redact(err.message).slice(0, 300) })
    log('error', 'issue publish failed', { dedupe: ob.dedupe_key, status: err.httpStatus })
    return { action: 'pending', via: 'issue', fingerprint: p.fingerprint }
  }
}

function bind(db, ob, p, issue) {
  updateOutbox(db, ob.id, 'sent', { issueUrl: issue.html_url, issueNumber: issue.number })
  saveDefect(db, { fingerprint: p.fingerprint, issueRepo: p.repo, issueNumber: issue.number, issueUrl: issue.html_url, jobId: p.jobId })
  patchJobDetail(db, p.jobId, { issueUrl: issue.html_url, issueNumber: issue.number, issueAction: 'created', issuePending: false })
  return { issueUrl: issue.html_url, issueNumber: issue.number, action: 'created', fingerprint: p.fingerprint }
}

// control 每轮补发：先补建 issue，再重做因临时错误推迟的整条发布（评论 / 重开）
export async function retryPendingIssues(db, reg) {
  for (const ob of pendingOutbox(db, 'issue')) {
    try { await deliverIssue(db, reg, ob) } catch (err) { log('error', 'retry issue failed', { err: err.message }) }
  }
  for (const ob of pendingOutbox(db, 'defect')) {
    const ctx = JSON.parse(ob.payload_json)
    try {
      const res = await publishCore(db, reg, ctx)
      if (res.action === 'pending' && res.via !== 'issue') continue
      updateOutbox(db, ob.id, 'sent', res)
      if (res.action === 'pending') continue
      patchJobDetail(db, ctx.job.id, { issueUrl: res.issueUrl ?? null, issueNumber: res.issueNumber ?? null, issueAction: res.action, issuePending: false })
    } catch (err) {
      updateOutbox(db, ob.id, ob.tries >= 20 ? 'failed' : 'unknown', { error: redact(err.message).slice(0, 300) })
    }
  }
}
