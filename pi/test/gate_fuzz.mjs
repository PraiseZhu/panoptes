// Differential fuzz: generate snapshots with the real testkit, judge with the real gate.mjs, print JSONL.
const A = '/workspace/mivo-merger/app/scripts/loops/automerge'
const { evaluate } = await import(`${A}/lib/gate.mjs`)
const T = await import(`${A}/testkit.mjs`)
const { loadPolicy } = await import(`${A}/run.mjs`)
const policy = loadPolicy()
const { NOW, MIN, HOUR, HEAD, OLD_HEAD } = T
let seed = Number(process.argv[2] ?? 1); const N = Number(process.argv[3] ?? 3000)
const rnd = () => { seed = (seed + 0x6D2B79F5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296 }
const pick = (a) => a[Math.floor(rnd() * a.length)]
const setCheck = (pr, name, over) => { pr.checkRuns = pr.checkRuns.map((c) => (c.name === name ? { ...c, ...over } : c)) }
const REQ = policy.expectedRequiredChecks
const M = [
  // global
  (g) => { g.mode = pick([null, 'off', 'shadow', 'canary', 'on']) },
  (g) => { g.tokenLogin = 'x' }, (g) => { g.tokenScopes = pick([null, ['repo', 'workflow'], []]) },
  (g) => { g.incomplete = [pick(['identity', 'circuit', 'rules', 'mainCi'])] },
  (g) => { g.circuitOpen = true }, (g) => { g.pausedFile = true }, (g) => { g.host = 'other' },
  (g) => { g.now = Date.parse('2026-10-15T22:00:00Z') + Math.floor(rnd() * 90) * MIN },
  (g) => { g.skillShaUpdatedAt = pick([null, NOW - 59 * MIN, NOW - 60 * MIN, NOW - 61 * MIN, NOW - 5 * MIN]) },
  (g) => { g.prereviewHeartbeat = pick([null, NOW - 30 * MIN, NOW - 31 * MIN]) },
  (g) => { g.requiredChecks = g.requiredChecks.slice(1) }, (g) => { g.requiredChecks[0].integrationId = 1 },
  (g) => { g.requiredChecks.push({ ...g.requiredChecks[0] }) },
  (g) => { g.mergeMethodSets = pick([[], [['merge']], [['squash', 'merge']], [['squash'], ['squash', 'rebase']]]) },
  (g) => { g.mainCi = pick([null, { status: 'in_progress' }, { status: 'completed', conclusion: 'failure', runAttempt: pick([1, 2]), failedJobs: pick([[], ['verify'], ['Windows unit shard 1'], ['lint']]) }]) },
  (g) => { g.mainHeadIsBotMerge = true },
  (g) => { g.greenRef = 'd'.repeat(40) }, (g) => { g.mainHead = null },
  (g) => { g.mergesToday = pick([1, 2, 11, 12]) }, (g) => { g.activationAt = pick([null, NOW + HOUR, NOW - 29 * MIN]) },
  // pr scope
  (g, p) => { p.state = 'CLOSED' }, (g, p) => { p.isDraft = true }, (g, p) => { p.locked = true }, (g, p) => { p.baseRefName = 'dev' },
  (g, p) => { p.isCrossRepository = true }, (g, p) => { p.headSha = 'zz' },
  (g, p) => { p.headRefName = pick(['release/1.2', 'main', 'plugin/green', 'mainline', 'feat/release/x']) },
  (g, p) => { p.labels = [...p.labels, pick(policy.holdLabels)] }, (g, p) => { p.labels = [] },
  (g, p) => { p.labels = [...p.labels, policy.canaryLabel]; p.timeline = { ...p.timeline, canaryLabeledBy: pick(['PraiseZhu', 'evil', null]) } },
  (g, p) => { p.author = { login: 'PraiseZhu', isBot: false } },
  (g, p) => { p.files = [...(p.files ?? []), { filename: pick(['scripts/ops/x.sh', 'agent-use/a.md', 'server/x.ts', 'package.json', 'src/x/package.json', 'scripts/loops/automerge/a.mjs', 'docs/a.md']), previousFilename: null, status: 'modified' }] },
  (g, p) => { p.files = [{ filename: 'src/new.ts', previousFilename: '.github/workflows/x.yml', status: 'renamed' }] },
  (g, p) => { p.files = null }, (g, p) => { p.incomplete = [pick(['files', 'commits', 'threads', 'comments', 'timeline', 'checkRuns'])] },
  (g, p) => { p.changedFiles = 5000 },
  (g, p) => { p.files = [...(p.files ?? []), { filename: 'cindyplugin/ghost.json', previousFilename: null, status: pick(['modified', 'removed']) }]; const k = rnd(); if (k < 0.3) delete p.ghostJson; else p.ghostJson = pick([{ base: { description: 'a', x: 1 }, head: { description: 'b', x: 1 } }, { base: { a: 1 }, head: { a: 2 } }, { base: null, head: null }, { base: { a: 1 }, head: { a: 1, b: 2 } }]) },
  (g, p) => { p.commits = pick([null, { total: 300, messages: [] }, { total: 2, messages: ['a'] }, { total: 1, messages: ['fix: x [skip ci]'] }]) },
  (g, p) => { p.title = 'chore: [CI SKIP] x' },
  (g, p) => { p.checkRuns = p.checkRuns.filter((c) => c.name !== 'review-pr') },
  (g, p) => { setCheck(p, 'review-pr', pick([{ status: 'in_progress' }, { appId: 1 }, { conclusion: 'failure' }, { title: '受保护路径: x' }, { externalId: 'https://github.com/other/repo/actions/runs/1' }, { externalId: null }, { startedAt: null }])) },
  (g, p) => { p.checkRuns.push(T.check('review-pr', { startedAt: NOW - 10 * MIN, completedAt: pick([null, NOW - 5 * MIN]), status: pick(['completed', 'queued']), externalId: T.RUN_URL })) },
  (g, p) => { p.reviewRun = pick([null, { ...p.reviewRun, path: 'x' }, { ...p.reviewRun, event: 'push' }, { ...p.reviewRun, triggeringActor: 'x' }, { ...p.reviewRun, headBranch: 'dev' }, { ...p.reviewRun, status: 'queued' }, { ...p.reviewRun, displayTitle: 'review-pr #43 x' }, { ...p.reviewRun, id: 1 }]) },
  (g, p) => { const k = rnd(); if (k < 0.4) delete p.reviewResult; else p.reviewResult = pick([null, { ...p.reviewResult, pr: '42' }, { ...p.reviewResult, pr: 43 }, { ...p.reviewResult, head: OLD_HEAD }, { ...p.reviewResult, mivo: { runUrl: 'x', checkRun: { id: 1 } } }, { ...p.reviewResult, reviewEvidence: { baseRefName: 'dev' } }, { ...p.reviewResult, reviewClean: false }, { ...p.reviewResult, outcome: 'approval', approval: { status: 'blocked', code: 'independent-approval-required' } }, { ...p.reviewResult, approval: { status: 'verified', reviewId: '', head: HEAD } }, { ...p.reviewResult, approval: { status: 'verified', reviewId: '999', head: HEAD } }]) },
  (g, p) => { p.reviews = pick([[], [T.reportComment()], [T.approval({ commitId: OLD_HEAD })], [T.approval({ author: { login: 'rando', isBot: false } })], [T.approval(), T.reportComment(HEAD, { id: 700, submittedAt: NOW - 10 * MIN, body: '<!-- review-pr:findings x -->' })]]) },
  (g, p) => { p.reviews = [...p.reviews, { id: 801, state: pick(['CHANGES_REQUESTED', 'APPROVED', 'DISMISSED', 'COMMENTED']), commitId: HEAD, author: { login: pick(['kirozeng', 'PraiseZhu']), isBot: false }, submittedAt: NOW - pick([60, 20]) * MIN, body: pick(['', '请改一下', '<!-- mivo-automerge:x -->']) }] },
  (g, p) => { p.timeline = pick([null, { ...p.timeline, baseChangedAt: NOW - pick([20, 100]) * MIN }]); if (rnd() < 0.5) { p.checkOrigins = {}; for (const c of p.checkRuns) p.checkOrigins[c.id] = NOW - pick([10, 200]) * MIN } },
  (g, p) => { setCheck(p, pick(REQ), pick([{ status: 'in_progress' }, { appId: 7 }, { conclusion: 'failure' }])) },
  (g, p) => { p.checkRuns = p.checkRuns.filter((c) => c.name !== pick(REQ)) },
  (g, p) => { p.checkRuns.push(T.check(pick(['Greptile Review', 'extra']), { status: pick(['completed', 'in_progress']), conclusion: pick(['success', 'failure', 'neutral', 'cancelled']) })) },
  (g, p) => { p.combinedStatus = pick([null, { state: 'failure', total: 1 }, { state: 'success', total: 2 }]) },
  (g, p) => { p.mergeable = pick(['CONFLICTING', 'UNKNOWN']) }, (g, p) => { p.mergeStateStatus = pick(['BEHIND', 'BLOCKED', 'UNSTABLE', 'DIRTY']) },
  (g, p) => { p.threads = pick([null, [{ isResolved: false }], [{ isResolved: true }]]) },
  (g, p) => { p.comments = [{ author: { login: pick(['kirozeng', 'lubinyu-lab', 'bot', 'PraiseZhu']), isBot: rnd() < 0.2 }, createdAt: NOW - pick([40, 10]) * MIN, updatedAt: null, body: pick(['这里为什么?', '', '<!-- vigil-x a=b -->', '> Mivo 自动审查\n<!-- review-pr:x -->']) }] },
  (g, p) => { p.compare = pick([null, { behindBy: 1, mergeBaseDate: NOW - 80 * HOUR }, { behindBy: 0, mergeBaseDate: NOW - 80 * HOUR }]) },
  (g, p) => { p.local = { threadsStableSince: pick([null, NOW - 5 * MIN, NOW - 10 * MIN, NOW - 11 * MIN]) } },
  (g, p) => { p.timeline = { ...p.timeline, readyLabeledAt: NOW - pick([3, 9, 10, 30]) * MIN } },
  (g, p) => { setCheck(p, 'verify', { completedAt: NOW - pick([2, 10, 12]) * MIN }) },
]
for (let i = 0; i < N; i++) {
  const g = T.makeGlobal(); const p = T.makePr()
  const k = 1 + Math.floor(rnd() * 3); const used = []
  for (let j = 0; j < k; j++) { const idx = Math.floor(rnd() * M.length); used.push(idx); M[idx](g, p) }
  let out
  try { const r = evaluate(g, p, policy); out = { verdict: r.verdict, reason: r.reasons[0], settleFrom: r.settleFrom ?? null } } catch (e) { out = { error: String(e.message).slice(0, 80) } }
  process.stdout.write(JSON.stringify({ i, used, g, p, out }) + '\n')
}
