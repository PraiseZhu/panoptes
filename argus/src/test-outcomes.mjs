import { isAbsolute, relative, resolve } from 'node:path'

const validStatuses = new Set(['passed', 'failed', 'skipped', 'todo'])
const invalid = () => { throw new Error('invalid-test-report') }
const count = (n) => Number.isInteger(n) && n >= 0
const text = (s) => typeof s === 'string' && s.trim().length > 0

function sourcePath(file, root) {
  if (!text(file)) invalid()
  const path = relative(root, isAbsolute(file) ? file : resolve(root, file)).replaceAll('\\', '/')
  if (!path || path === '..' || path.startsWith('../') || isAbsolute(path)) invalid()
  return path
}

function vitestOutcomes(report, root) {
  if (!Array.isArray(report.testResults) || !count(report.numTotalTests)
    || !count(report.numPassedTests) || !count(report.numFailedTests) || !count(report.numPendingTests)
    || (report.unhandledErrors?.length ?? 0) > 0 || (report.numRuntimeErrorTestSuites ?? 0) > 0) invalid()
  const rows = []
  for (const file of report.testResults) {
    const path = sourcePath(file.name, root)
    if (!Array.isArray(file.assertionResults)) invalid()
    if (file.status === 'failed' && !file.assertionResults.some((t) => t.status === 'failed')) invalid()
    for (const t of file.assertionResults) {
      if (!text(t.title) || !Array.isArray(t.ancestorTitles) || !t.ancestorTitles.every(text)) invalid()
      const status = t.status === 'pending' ? 'skipped' : t.status
      if (!validStatuses.has(status)) invalid()
      const name = [path, ...t.ancestorTitles, t.title].join(' > ')
      rows.push({ caseId: name, failureName: name, status })
    }
  }
  const passed = rows.filter((r) => r.status === 'passed').length
  const failed = rows.filter((r) => r.status === 'failed').length
  if (rows.length !== report.numTotalTests || passed !== report.numPassedTests || failed !== report.numFailedTests
    || rows.length - passed - failed !== report.numPendingTests + (report.numTodoTests ?? 0)) invalid()
  return rows
}

function nodeOutcomes(raw, root) {
  const events = raw.trim().split('\n').filter(Boolean).map((line) => JSON.parse(line))
  const summaries = events.filter((e) => e.kind === 'summary')
  const global = summaries.filter((e) => !e.file)
  if (global.length !== 1 || events.at(-1) !== global[0] || summaries.some((s) => s.counts?.cancelled !== 0)) invalid()
  const rows = []
  for (const e of events) {
    if (e.kind === 'summary') continue
    if (e.kind !== 'case' || !validStatuses.has(e.status) || !text(e.name)) invalid()
    if (e.type === 'suite') continue
    if (e.type !== 'test') invalid()
    const path = sourcePath(e.file, root)
    rows.push({ caseId: `${path} > ${e.name}`, failureName: e.name, status: e.status })
  }
  const counts = global[0].counts
  if (!['tests', 'passed', 'failed', 'skipped', 'todo'].every((k) => count(counts?.[k]))) invalid()
  if (counts.tests !== rows.length || counts.passed !== rows.filter((r) => r.status === 'passed').length
    || counts.failed !== rows.filter((r) => r.status === 'failed').length
    || counts.skipped !== rows.filter((r) => r.status === 'skipped').length
    || counts.todo !== rows.filter((r) => r.status === 'todo').length) invalid()
  return rows
}

export function normalizeTestOutcomes({ format, raw, sourceRoot, runId, stepId, checkedOutSha }) {
  try {
    const rows = format === 'vitest-json' ? vitestOutcomes(JSON.parse(raw), sourceRoot)
      : format === 'node-events' ? nodeOutcomes(raw, sourceRoot) : invalid()
    return { testOutcomes: rows, testOutcomeStatus: 'complete', testOutcomeSource: { format, runId, stepId, checkedOutSha } }
  } catch {
    return { testOutcomes: [], testOutcomeStatus: 'incomplete', testOutcomeReason: 'invalid-test-report' }
  }
}

export function findTestOutcome(result, failureName) {
  if (result?.testOutcomeStatus !== 'complete' || !Array.isArray(result.testOutcomes)) return { status: 'unknown' }
  const matches = result.testOutcomes.filter((r) => r.failureName === failureName)
  if (matches.length !== 1 || !text(matches[0].caseId) || !validStatuses.has(matches[0].status)
    || result.testOutcomes.filter((r) => r.caseId === matches[0].caseId).length !== 1) return { status: 'unknown' }
  return matches[0]
}
