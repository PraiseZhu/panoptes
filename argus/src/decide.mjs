// 判定：只有「合并版本失败 + 重跑仍失败 + 合并前版本通过 + 最新主干仍失败」且每一步都能核实，才算确认缺陷。
// 优先按具体用例比较；普通命令完整退出但没有用例名时可比较整步。超时、不可用、缺失的对照不构成失败重现证据。
// 这样既不会把老失败算到新 PR 头上，也不会让新失败被老失败掩盖成假绿。

import { findTestOutcome } from './test-outcomes.mjs'

export const OUTCOMES = {
  passed: '没发现问题',
  passed_known_failures: '没发现这个 PR 引入的新问题（有合并前就存在的失败）',
  docs_only: '只改了说明文档',
  defect: '发现问题，已提 issue',
  needs_review: '有失败但没法完全核实，待人工确认',
  flaky: '测试结果不稳定，重跑后通过',
  fixed_later: '合并后出现过问题，但最新主干已经好了',
  blocked: '暂时无法测试',
  infra_failed: '测试环境出了问题',
  not_enrolled: '这个项目还没接入自动测试',
  expired: '排队太久，已过期未测',
}

const WHOLE = '*'

// 「没跑成」的步骤不是产品失败；不触发归因，日报按覆盖缺口呈现。
export const failedIds = (results) => (results ?? []).filter((r) => r.kind !== 'setup' && (!r.ok || r.testOutcomeStatus === 'incomplete') && !r.unavailable).map((r) => r.id)
export const setupFailed = (results) => (results ?? []).some((r) => r.kind === 'setup' && !r.ok)

const completeRun = (r) => r && !r.unavailable && !r.timedOut && r.exitCode != null && r.testOutcomeStatus !== 'incomplete' && r.failureParseStatus !== 'incomplete' && (r.ok || ['complete', 'not_detected'].includes(r.failureParseStatus))
const hasFailures = (r) => (r?.failures?.length ?? 0) > 0
const typeOf = (key, r) => r?.failureDetails?.find((f) => f.name === key.slice(key.indexOf('::') + 2))?.type ?? 'unknown'
const confirmable = (type) => ['assertion', 'test_error'].includes(type)

// 失败键保留诊断：有名字用 `步骤id::用例名`，没有名字用 `步骤id::*`；能否确认另行检查。
export function failureKeys(results) {
  const keys = []
  for (const r of results ?? []) {
    if (r.kind === 'setup' || (r.ok && r.testOutcomeStatus !== 'incomplete') || r.unavailable) continue
    if (hasFailures(r)) keys.push(...r.failures.map((f) => `${r.id}::${f}`))
    else keys.push(`${r.id}::${WHOLE}`)
  }
  return keys
}

const stepOf = (key) => key.slice(0, key.indexOf('::'))
const isWhole = (key) => key.endsWith(`::${WHOLE}`)

// 某个失败键在另一轮结果里：same 同样失败 / absent 没有失败 / unknown 无法判断
export function compareKey(key, otherResults, referenceResults = null) {
  const step = stepOf(key)
  const reference = (referenceResults ?? []).find((x) => x.id === step)
  const r = (otherResults ?? []).find((x) => x.id === step)
  if (!completeRun(reference) || !completeRun(r)
    || reference.testOutcomeStatus === 'incomplete' || r.testOutcomeStatus === 'incomplete') return 'unknown'
  if (isWhole(key)) {
    if (r.ok) return 'absent'
    return !hasFailures(reference) && !hasFailures(r) && reference.failureParseStatus === 'not_detected' && r.failureParseStatus === 'not_detected' ? 'same' : 'unknown'
  }
  const name = key.slice(key.indexOf('::') + 2)
  if (!r.failures?.includes(name)) {
    const observed = findTestOutcome(r, name)
    const original = findTestOutcome(reference, name)
    return observed.status === 'passed' && (reference.testOutcomeStatus !== 'complete' || original.caseId === observed.caseId) ? 'absent' : 'unknown'
  }
  if (reference.failureParseStatus !== 'complete' || r.failureParseStatus !== 'complete'
    || reference.testOutcomeStatus === 'incomplete' || r.testOutcomeStatus === 'incomplete') return 'unknown'
  if (reference.testOutcomeStatus === 'complete' && r.testOutcomeStatus === 'complete') {
    const a = findTestOutcome(reference, name), b = findTestOutcome(r, name)
    if (a.status !== 'failed' || b.status !== 'failed' || a.caseId !== b.caseId) return 'unknown'
  }
  const type = typeOf(key, reference)
  return confirmable(type) && type === typeOf(key, r) ? 'same' : 'unknown'
}

const stepsOf = (keys) => [...new Set(keys.map(stepOf))]
const testsOf = (keys) => keys.filter((k) => !isWhole(k)).map((k) => k.slice(k.indexOf('::') + 2))

function needsReview(reasonZh, keys, extra = {}) {
  const unverified = extra.unverifiedFailures ?? []
  if (unverified.some((f) => f.reason === 'failure_set_changed')) reasonZh = '重跑或最新主干出现了新的失败，尚未完成对照核实，不能认定检查已经通过'
  else if (unverified.some((f) => ['test_not_executed', 'test_execution_unverified'].includes(f.reason))) reasonZh = '对照版本缺少目标用例实际执行并通过的证据，不能确认是这个 PR 引入的'
  else if (unverified.some((f) => f.type === 'test_timeout')) reasonZh = '用例内部超时，尚无可比较的断言失败证据，不能确认是这个 PR 引入的'
  else if (unverified.some((f) => f.reason === 'failure_type_changed')) reasonZh = '同名用例的失败类型发生变化，无法确认同一问题重现'
  else if (unverified.some((f) => f.type === 'runner_error')) reasonZh = '测试运行器或检查流程未完成，不能确认是产品回归'
  else if (unverified.some((f) => f.reason === 'failure_type_unknown')) reasonZh = '只有失败用例名，缺少明确失败原因，不能确认是这个 PR 引入的'
  return { outcome: 'needs_review', reasonZh, failedSteps: stepsOf(keys), failedTests: testsOf(keys), knownOnlyStepIds: [], ...extra }
}

// 各阶段结果都是 runSteps 的数组；缺失的阶段传 null。latestIsSame=true 表示最新主干就是合并版本本身
export function decideOutcome({ first, rerun, base, latest, baseKnown = true, latestIsSame = false }) {
  if (setupFailed(first)) return { outcome: 'infra_failed', reasonZh: '安装依赖没成功，测试没跑起来' }
  if (!(first ?? []).some((r) => r.kind !== 'setup' && !r.unavailable)) {
    return { outcome: 'blocked', reasonZh: '没有实际执行的检查，不能判定通过', knownOnlyStepIds: [] }
  }
  const firstKeys = failureKeys(first)
  const unverifiedFailures = []
  const extraKeys = []
  const observedKeys = new Set(firstKeys)
  for (const [phase, results] of [['rerun', rerun], ['latest', latest]]) {
    for (const key of failureKeys(results)) {
      if (!observedKeys.has(key)) {
        extraKeys.push(key)
        unverifiedFailures.push({ key, type: typeOf(key, results.find((r) => r.id === stepOf(key))), phase, reason: 'failure_set_changed' })
        observedKeys.add(key)
      }
    }
  }
  if (firstKeys.length === 0) return extraKeys.length ? needsReview('', extraKeys, { unverifiedFailures }) : { outcome: 'passed', reasonZh: '所有检查都通过' }

  const transient = { transientFailureCount: 0, transientFailures: [] }
  const seen = new Set()
  const recordUnknown = (key, results, phase) => {
    const reference = first.find((r) => r.id === stepOf(key))
    const r = results?.find((r) => r.id === stepOf(key))
    const type = typeOf(key, r)
    const referenceType = typeOf(key, reference)
    let reason = 'failure_type_unknown'
    if (!r || r.unavailable || r.timedOut || r.exitCode == null) reason = 'comparison_unavailable'
    else if (!completeRun(r)) reason = 'failure_evidence_incomplete'
    else if (!isWhole(key) && !r.failures?.includes(key.slice(key.indexOf('::') + 2))) {
      const status = findTestOutcome(r, key.slice(key.indexOf('::') + 2)).status
      reason = ['skipped', 'todo'].includes(status) ? 'test_not_executed' : 'test_execution_unverified'
    }
    else if (referenceType !== 'unknown' && type !== 'unknown' && referenceType !== type) reason = 'failure_type_changed'
    else if (type === 'test_timeout') reason = 'internal_timeout'
    else if (type === 'runner_error') reason = 'runner_error'
    const identity = `${key}|${phase}|${reason}`
    if (!seen.has(identity) && unverifiedFailures.length < 1000) { seen.add(identity); unverifiedFailures.push({ key, type, phase, reason }) }
  }
  const compare = (key, results, phase) => {
    const result = compareKey(key, results, first)
    if (result === 'unknown') {
      const reference = first.find((r) => r.id === stepOf(key))
      if (!completeRun(reference)) recordUnknown(key, first, 'first')
      else recordUnknown(key, results, phase)
    }
    return result
  }
  const review = (reason, keys, extra = {}) => needsReview(reason, keys, { unverifiedFailures, ...transient, ...extra })

  if (!rerun || setupFailed(rerun)) return { outcome: 'infra_failed', reasonZh: '第一次有检查失败，但重跑时环境没装好，无法确认', failedSteps: stepsOf(firstKeys) }
  // 首次和重跑都要有完整结果；未知只能继续核实，不能当成「同一失败已重现」。
  const vsRerun = firstKeys.map((k) => [k, compare(k, rerun, 'rerun')])
  // 只记录完整对照中已经消失的失败；混合老失败或新回归时也保留这段事实。
  transient.transientFailures = vsRerun.filter(([, v]) => v === 'absent').map(([key]) => ({ key, type: typeOf(key, first.find((r) => r.id === stepOf(key))) }))
  transient.transientFailureCount = transient.transientFailures.length
  const still = vsRerun.filter(([, v]) => v !== 'absent').map(([k]) => k)
  if (still.length === 0) return extraKeys.length ? review('', extraKeys) : { outcome: 'flaky', reasonZh: '第一次有检查失败，换新环境重跑就通过了', failedSteps: stepsOf(firstKeys), failedTests: testsOf(firstKeys), ...transient }
  const reproduced = vsRerun.filter(([, v]) => v === 'same').map(([k]) => k)
  const rerunUnclear = vsRerun.filter(([, v]) => v === 'unknown').map(([k]) => k)
  if (!reproduced.length) return review('首次或重跑的检查没有完整结果，无法确认同一失败是否重现', still)

  if (!baseKnown) return review('合并后检查失败并能重现，但找不到合并前的版本做对照，没法确定是不是这个 PR 引入的', still)
  if (!base || setupFailed(base)) return review('合并后检查失败，但合并前的版本没能跑起来，没法确定是不是这个 PR 引入的', still)

  const vsBase = reproduced.map((k) => [k, compare(k, base, 'base')])
  const candidates = vsBase.filter(([, v]) => v === 'absent').map(([k]) => k)
  const known = vsBase.filter(([, v]) => v === 'same').map(([k]) => k)
  const unclear = [...extraKeys, ...rerunUnclear, ...vsBase.filter(([, v]) => v === 'unknown').map(([k]) => k)]
  // 只把该步骤所有首次失败都已证实为老失败的步骤交给覆盖统计，不能把未知项一起算入。
  const knownSet = new Set(known)
  let knownOnlyStepIds = stepsOf(known).filter((id) => !unclear.some((k) => stepOf(k) === id) && firstKeys.filter((k) => stepOf(k) === id).every((k) => knownSet.has(k)))
  if (candidates.length === 0) {
    if (unclear.length) return review('有检查失败，但重跑或合并前的对照结果不完整，没法判断有没有新增失败', unclear, { knownFailureCount: known.length, knownOnlyStepIds })
    return {
      outcome: 'passed_known_failures',
      reasonZh: '失败的检查在合并前的版本上同样失败，不是这个 PR 引入的',
      failedSteps: stepsOf(known), knownFailureCount: known.length, knownTests: testsOf(known).slice(0, 20), knownOnlyStepIds,
      ...transient,
    }
  }

  let confirmed = candidates
  if (!latestIsSame) {
    if (!latest || setupFailed(latest)) return review('合并后新出现了失败，但最新主干没能跑起来，没法确认问题现在还在不在', candidates, { knownFailureCount: known.length, knownOnlyStepIds })
    for (const key of failureKeys(latest)) {
      if (candidates.includes(key) || (known.includes(key) && compareKey(key, latest, first) === 'same')) continue
      if (!unverifiedFailures.some((f) => f.key === key && f.phase === 'latest')) {
        unverifiedFailures.push({ key, type: typeOf(key, latest.find((r) => r.id === stepOf(key))), phase: 'latest', reason: 'failure_set_changed' })
      }
      if (!unclear.includes(key)) unclear.push(key)
    }
    knownOnlyStepIds = knownOnlyStepIds.filter((id) => !unclear.some((k) => stepOf(k) === id))
    const vsLatest = candidates.map((k) => [k, compare(k, latest, 'latest')])
    confirmed = vsLatest.filter(([, v]) => v === 'same').map(([k]) => k)
    const latestUnclear = vsLatest.filter(([, v]) => v === 'unknown').map(([k]) => k)
    unclear.push(...latestUnclear)
    if (confirmed.length === 0) {
      if (unclear.length) return review('部分失败的对照结果不完整，没法确认是否都已被后续修复', unclear, { knownFailureCount: known.length, knownOnlyStepIds })
      return { outcome: 'fixed_later', reasonZh: '合并后确实失败过，但最新主干已经通过', failedSteps: stepsOf(candidates), failedTests: testsOf(candidates), knownOnlyStepIds, ...transient }
    }
  }
  return {
    outcome: 'defect',
    reasonZh: '合并后检查失败，合并前通过，最新主干仍然失败',
    failedSteps: stepsOf(confirmed),
    failedTests: testsOf(confirmed),
    failureKeys: confirmed,
    knownFailureCount: known.length,
    knownOnlyStepIds,
    unclearCount: unclear.length,
    unverifiedFailures,
    ...transient,
    attributed: true,
  }
}
