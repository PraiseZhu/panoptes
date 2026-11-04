#!/usr/bin/env node
// Mivo QA 飞书通知台账。只读所有源文件；只写 ledger/daily/。不发任何消息。
import fs from 'node:fs'; import path from 'node:path'; import { execFileSync } from 'node:child_process';

// 11-04 task B: the Praise QA App token (PRAISE_QA_GH_TOKEN, set by the Python caller while its gh-app-<job>-enabled flag is on) is used
// ONLY for gh calls whose target repo is in PRAISE_QA_GH_REPOS; every other call, and the flag off, inherit the default login (PAT).
function ghTargetRepo(args) {
  for (let i = 0; i < args.length; i++) {
    if ((args[i] === '-R' || args[i] === '--repo') && args[i + 1]) return args[i + 1]
    const m = /^\/?repos\/([^/\s]+\/[^/?\s]+)/.exec(args[i]); if (args[0] === 'api' && m) return m[1]
  }
  return null
}
function ghExec(args, opts = {}) { return execFileSync('gh', args, { ...opts, env: ghEnvFor(args) }) }
function ghEnvFor(args) {
  const tok = process.env.PRAISE_QA_GH_TOKEN, repos = (process.env.PRAISE_QA_GH_REPOS || '').split(',').filter(Boolean)
  const r = ghTargetRepo(args), own = process.env.PRAISE_QA_GH_OWN_TOKEN
  const t = tok && r && repos.includes(r) ? tok : own && r && /^PraiseZhu\/[^/]+$/.test(r) ? own : null   // own = every PraiseZhu/* repo
  if (!t) return process.env
  const e = { ...process.env, GH_TOKEN: t }; delete e.GITHUB_TOKEN; return e
}
const USAGE = `usage: build-ledger.mjs [YYYY-MM-DD] [--dry-run]
  Rebuilds the Mivo QA ledger for the day (default: today, Beijing) into $QA_HOME/ledger/daily/<day>.json/.md
  (and deletes daily files older than 60 days). --dry-run writes $QA_HOME/scratch/ledger-dry/ instead, no retention.
  --help / -h: print this and exit; nothing is read or written. Any other argument is an error (exit 2).
`;
const args = process.argv.slice(2);
if (args.includes('--help') || args.includes('-h')) { process.stdout.write(USAGE); process.exit(0); }
const badArgs = args.filter(a => a !== '--dry-run' && !/^\d{4}-\d{2}-\d{2}$/.test(a));
if (badArgs.length || args.filter(a => a !== '--dry-run').length > 1) { process.stderr.write(`build-ledger: bad argument(s) ${JSON.stringify(badArgs.length ? badArgs : args)}\n${USAGE}`); process.exit(2); }
const QA = process.env.QA_HOME || '/workspace/mivo-qa';
const REPO = 'xindong/mivo-canvas-plugin';
const OUT = args.includes('--dry-run') ? path.join(QA, 'scratch/ledger-dry') : path.join(QA, 'ledger/daily'); const KEEP_DAYS = 60;
const now = new Date();
const sh = d => new Date(d.getTime() + 8 * 3600e3).toISOString().slice(0, 10);
const shTime = s => s ? new Date(new Date(s).getTime() + 8 * 3600e3).toISOString().slice(0, 16).replace('T', ' ') : null;
const DRY = args.includes('--dry-run'); // --dry-run: write to scratch/ledger-dry/ instead of ledger/daily/, no retention
const today = args.find(a => /^\d{4}-\d{2}-\d{2}$/.test(a)) || sh(now);
const rj = (p, d) => { try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch { return d; } };
const gaps = [];
const sent = rj(path.join(QA, 'feishu-sent.json'), {});
const state = rj(path.join(QA, 'followup/state.json'), { items: {} }).items || {};
const holds = rj(path.join(QA, 'followup/holds.json'), []);

// reports: fingerprint -> finding
const reports = {}; const reportAt = {};
for (const f of fs.readdirSync(path.join(QA, 'reports/merged')).filter(f => /^PR-\d+\.json$/.test(f))) {
  const r = rj(path.join(QA, 'reports/merged', f)); if (!r) { gaps.push(`报告无法解析: ${f}`); continue; }
  for (const p of r.prs || []) { reports[p.number] = p; reportAt[p.number] = r.generated_at || r.run?.finished_at || null; }
}
const hist = {};
const hdir = path.join(QA, 'reports/merged/history');
for (const f of fs.existsSync(hdir) ? fs.readdirSync(hdir).filter(f => /^PR-\d+\.v\d+\.json$/.test(f)).sort() : []) {
  const r = rj(path.join(hdir, f)); for (const p of r?.prs || []) for (const x of p.findings || []) hist[`${p.number}:${x.fingerprint}`] = { ...x, _from_history: f };
}
// acceptance re-reviews (JSON only): reports/acceptance/review-PR-N.json — used when merged report lacks the fingerprint
const acc = {};
const adir = path.join(QA, 'reports/acceptance');
for (const f of fs.existsSync(adir) ? fs.readdirSync(adir).filter(f => /^review-PR-\d+\.json$/.test(f)) : []) {
  const r = rj(path.join(adir, f)); if (!r) { gaps.push(`复审报告无法解析: acceptance/${f}`); continue; }
  for (const p of r.prs || []) acc[p.number] = { ...p, _file: f };
}
const byFp = (list, pr, fp) => (list || []).find(x => x.fingerprint === fp || x.pr_fingerprint === `${fp}-pr${pr}`);
function findFinding(pr, fp) {
  const m = byFp(reports[pr]?.findings, pr, fp); if (m) return m;
  // 统一分级（2026-10-23）：待核实条目在 pending_verification，没有 severity（等级在 severity_if_confirmed）
  const pv = byFp(reports[pr]?.pending_verification, pr, fp); if (pv) return { ...pv, severity: pv.severity ?? pv.severity_if_confirmed ?? null, _pending: true };
  const a = acc[pr]; const h = hist[`${pr}:${fp}`];
  let x = a && byFp(a.findings, pr, fp);
  // fallback: same file+line as the historical finding with this fingerprint
  if (!x && a && h?.file && h.line != null) x = (a.findings || []).find(y => y.file === h.file && y.line === h.line);
  if (x) return { ...x, _from_acceptance: a._file };
  return h;
}

// group archives: PR -> [dates sent in group]
const groupDays = {};
const sentDir = path.join(QA, 'report/sent');
for (const d of fs.existsSync(sentDir) ? fs.readdirSync(sentDir) : []) {
  const t = path.join(sentDir, d, 'group-content.txt'); if (!fs.existsSync(t)) continue;
  let sec = '';
  for (const line of fs.readFileSync(t, 'utf8').split('\n')) {
    const h = line.match(/^(P[0-3]) /); if (h) sec = h[1];
    const m = line.match(/^#(\d+) .*— @/); if (m && /P[012]/.test(sec)) (groupDays[m[1]] ||= new Set()).add(d);
  }
}

// GitHub: recent PRs, to find fix PRs referencing #N
let ghPRs = null, ghErr = null;
for (let i = 0; i < 3 && !ghPRs; i++) {
  try {
    ghPRs = JSON.parse(ghExec(['pr', 'list', '-R', REPO, '--state', 'all', '--limit', '400',
      '--json', 'number,title,body,state,mergedAt,createdAt'], { encoding: 'utf8', timeout: 120000, maxBuffer: 256 * 1024 * 1024, stdio: ['ignore', 'pipe', 'pipe'] }));
    ghErr = null;
  } catch (e) { ghErr = String(e.stderr || e.message).split('\n')[0].slice(0, 200); execFileSync('sleep', [String(5 * (i + 1))]); }
}
if (ghErr) gaps.push(`GitHub 查询失败（重试 3 次：${ghErr}），修复状态标为 unknown`);
const ghByNum = Object.fromEntries((ghPRs || []).map(p => [p.number, p]));
// changed files / text of candidate fix PRs (to tie a fix PR to the specific finding it addresses)
const prInfoCache = {};
function prInfo(n) {
  if (n in prInfoCache) return prInfoCache[n];
  let v = null;
  try { v = JSON.parse(ghExec(['pr', 'view', String(n), '-R', REPO, '--json', 'number,title,body,files'], { encoding: 'utf8', timeout: 60000, maxBuffer: 64 * 1024 * 1024, stdio: ['ignore', 'pipe', 'pipe'] })); }
  catch (e) { gaps.push(`#${n} 修复 PR 详情查询失败，无法核对其对应的问题`); }
  return (prInfoCache[n] = v);
}
const COVERAGE = /coverage|覆盖率/i;
// true = fix PR demonstrably addresses this finding; false/null = not shown (row stays open)
function addresses(fix, f, fileAnchored) {
  if (fileAnchored) return true; // followup already tied this PR to the finding's file(s)
  if (!f) return null;
  const info = prInfo(fix.pr); if (!info) return null;
  if (f.file) return (info.files || []).some(x => x.path === f.file);
  if (f.source === 'coverage-gate') return COVERAGE.test(`${info.title || ''}\n${info.body || ''}`);
  return null;
}
const NOISE = /更新日志|changelog|版本说明|release notes|补扫|准备 \d|^chore\(release/i;
const FIXCTX = /修复|修正|fix|回访|QA|跟进|follow|原 ?PR|针对|处理|关联|审查意见/i;
function fixPRsFor(n, sinceIso) {
  if (!ghPRs) return null;
  const re = new RegExp(`#${n}(?!\\d)`);
  return ghPRs.filter(p => p.number > n && p.state !== 'CLOSED' && !NOISE.test(p.title || '') && p.createdAt >= sinceIso &&
      [p.title || '', ...(p.body || '').split('\n')].some(l => re.test(l) && FIXCTX.test(l)))
    .map(p => ({ pr: p.number, merged: p.state === 'MERGED' || !!p.mergedAt, state: p.state.toLowerCase(), via: 'github-reference' }));
}


// handoff repo receipts: '结论：已修 #N' => fixed; 误报/不修 (+原因) => concluded. Closed issue with status:false-positive => 误报.
// 交接仓列表与 PR 作者映射统一来自 panoptes report/handoff-routes.json
const HANDOFF_ROUTES = JSON.parse(fs.readFileSync(process.env.MIVO_QA_HANDOFF_ROUTES || '/workspace/panoptes/report/handoff-routes.json', 'utf8'));
const HANDOFF_REPOS = HANDOFF_ROUTES.routes.map(r => r.repo);
const HANDOFF_REPO = HANDOFF_REPOS[0];
let hoIssues = null;
for (const repo of HANDOFF_REPOS) {
  try { for (const i of JSON.parse(ghExec(['issue', 'list', '-R', repo, '--state', 'all', '--limit', '200', '--json', 'number,state,labels,url'], { encoding: 'utf8', timeout: 60000, stdio: ['ignore', 'pipe', 'pipe'] }))) { hoIssues ??= {}; hoIssues[`${repo}#${i.number}`] = i; if (repo === HANDOFF_REPO) hoIssues[i.number] = i; } }
  catch (e) { gaps.push(`交接仓 ${repo} issue 查询失败，回执只按 followup/state.json`); }
}
function handoffReceipt(st, fp) {
  if (!st) return null;
  const rc = st.handoff_receipt && (!st.handoff_receipt.fingerprint || st.handoff_receipt.fingerprint === fp) ? st.handoff_receipt : null;
  const ev = (st.evidence || []).find(e => e.type === 'handoff-receipt' && (!rc || e.issue === rc.issue));
  if (!rc && !ev) return null;
  if (!rc && (st.fingerprints || []).length > 1) return null; // evidence without fingerprint is ambiguous for multi-finding items
  const issue = rc?.issue || ev?.issue; const num = +String(issue || '').split('#')[1];
  const isRepo = String(issue || '').includes('/') ? String(issue).split('#')[0] : HANDOFF_REPO; const iss = hoIssues?.[`${isRepo}#${num}`]; const fpLabel = iss && iss.state === 'CLOSED' && (iss.labels || []).some(l => l.name === 'status:false-positive');
  const kindText = `${rc?.conclusion || ev?.kind || ''}`;
  const url = ev?.url || iss?.url || (num ? `https://github.com/${isRepo}/issues/${num}` : null);
  // 回执的修复 PR 号：panoptes track-followups（2026-10-23 起）在 evidence 里直接带 fix_pr；旧证据只有文字
  const fixM = kindText.match(/已修\s*(?:[\w.-]+\/[\w.-]+)?#?(\d+)/) || String(rc?.processing || '').match(/已修\s*#(\d+)/);
  const fixN = ev?.fix_pr ?? rc?.fix_pr ?? (fixM ? +fixM[1] : null);
  if (fixN) return { kind: 'fixed', fix_pr: +fixN, url };
  if (fpLabel || /误报/.test(kindText)) return { kind: 'concluded', label: `误报（${issue}${fpLabel ? ' 已关闭，status:false-positive' : ''}）`, reason: rc?.reason, url };
  if (/不修/.test(kindText)) return { kind: 'concluded', label: `不修（${issue}）`, reason: rc?.reason, url };
  return null;
}

// state by fingerprint
const stateByFp = {};
for (const v of Object.values(state)) for (const fp of v.fingerprints || []) {
  const k = `${v.pr}:${fp}`; if (!stateByFp[k] || v.status === 'closed') stateByFp[k] = v;
}

// collect notified fingerprints. Three sources (2026-10-23), first one wins per row key:
//  1 feishu-sent.json "<repo>#<pr>:<fp>" (+ covered_fingerprints) — Grok-line DM records
//  2 feishu-sent.json "dm:<day>:<pr>" — Pi line / send-dm-recorded.mjs records: one DM per PR → the P0–P2 that DM stood for
//    when it was sent (dmStandsFor: unified rule from 10-24, the rule of the time before)
//  3 reports/merged for OWNERS = the Pi fix authors (same as pi/bin/finding_policy.py FIX_AUTHORS, Praise 11-02: PraiseZhu +
//    kirozeng) — their P0–P2 must be tracked for prfix/retest even with no DM record (PraiseZhu is never DM'd by rule; a kirozeng
//    finding that was DM'd is already a row from 1/2, first source wins). Rollback, same flag as fix_authors(), re-read every run:
//    touch $PI_STATE/fix-authors-praise-only (default /workspace/mivo-qa/pi/) -> PraiseZhu only.
//    (env MIVO_QA_LEDGER_OWNERS="login,login" overrides; empty string disables)
const FIX_AUTHORS = ['PraiseZhu', 'kirozeng'];
const SELF_BOTS = ['app/praise-qa', 'praise-qa[bot]', 'praise-qa'];   // = pi/bin/lib.py SELF_LOGINS (the Praise QA App)
const praiseOnly = fs.existsSync(path.join(process.env.PI_STATE || path.join(QA, 'pi'), 'fix-authors-praise-only'));
const OWNERS = (process.env.MIVO_QA_LEDGER_OWNERS ?? (praiseOnly ? FIX_AUTHORS.slice(0, 1) : FIX_AUTHORS).join(',')).split(',').map(s => s.trim()).filter(Boolean);
const loginOf = p => (p?.author && typeof p.author === 'object' ? p.author.login : p?.author) || null;
// a finding a PR-level DM sent from 10-24 (newNotice) / an owner row stands for — unified severity (Praise 2026-10-23), the
// same rule as panoptes report/finding-policy.mjs notifiable() and pi-fallback bin/finding_policy.py (DMs sent before 10-24:
// legacyNotified below). Drops 待核实 (pending_verification) items, coverage-only P2 (coverage-gate; a missed target
// the PR stated itself is stated-acceptance and counts), unverified AI-test leads, own-tests, qa-error, unverified / flaky /
// timeout / setup-failed checks, pre-existing, PLAUSIBLE review findings, unconfirmed failed author cases, 误报
function notifiable(f, { newNotice = false } = {}) {
  if (!f || !['P0', 'P1', 'P2'].includes(f.severity) || f.pending_kind) return false;
  if (f.qa_status === 'failed' && f.confirmed === false) return false;
  if (f.pre_existing || f.not_a_pr_finding) return false;
  if ((f.calibration || {}).to === '误报' || /false_positive|misreport|REJECTED/i.test(f.verdict || '')) return false;
  if (/flaky|qa-error|false_positive|misreport|pre-existing|unconfirmed/.test(`${f.status || ''} ${f.attribution || ''}`)) return false;
  switch (f.source ?? 'check') {
    case 'code-review': // newNotice: an old report's AI-only P0/P1 (no verification) was not put in a new DM
      if (String(f.verdict || '').toUpperCase() === 'PLAUSIBLE') return false;
      if (f.verification) return f.verification.status === 'confirmed';
      return !(newNotice && (f.severity === 'P0' || f.severity === 'P1'));
    case 'stated-acceptance': return true;
    case 'ai-test': return f.verified === true;
    case 'coverage-gate': case 'own-tests': case 'qa-error': return false;
    default: return f.status == null ? f.test_backed !== false && f.flaky !== true : f.status === 'introduced';
  }
}
// 统一分级只管北京时间 10-24 起发出的私信（Praise 2026-10-23 23:18）：在那之前已经发出去的私信照老流程，台账按发的时候的口径记行
// （legacyNotified = 统一分级前台账和 dm_compat 用的规则，原样保留）；之后的私信按新发提醒口径（notifiable newNotice），只记私信里实际写了的问题。
// 同 panoptes report/finding-policy.mjs UNIFIED_SINCE、pi-fallback bin/finding_policy.py sent_under_unified / legacy_notified、dm_compat.notifiable
const UNIFIED_SINCE = process.env.MIVO_QA_UNIFIED_SINCE || '2026-10-23T16:00:00.000Z';
const sentUnderUnified = at => Boolean(at) && Date.parse(at) >= Date.parse(UNIFIED_SINCE);
function legacyNotified(f) {
  if (!f || !['P0', 'P1', 'P2'].includes(f.severity)) return false;
  if (f.source === 'ai-test' || (f.qa_status === 'failed' && f.confirmed === false) || f.pre_existing) return false;
  if ((f.calibration || {}).to === '误报' || /false_positive|misreport|REJECTED/i.test(f.verdict || '')) return false;
  if (/flaky|qa-error|false_positive|misreport|pre-existing|unconfirmed/.test(`${f.status || ''} ${f.attribution || ''}`)) return false;
  return f.source === 'code-review' || f.source === 'coverage-gate' || (f.test_backed === true && f.status !== 'unverified');
}
const dmStandsFor = at => sentUnderUnified(at) ? f => notifiable(f, { newNotice: true }) : legacyNotified;
const rows = new Map();
const addRow = o => { if (!rows.has(o.key)) rows.set(o.key, o); };
for (const [key, v] of Object.entries(sent)) {
  const m = key.match(/^(.+?)#(\d+):(mq1-[0-9a-f]+)$/);
  if (m) {
    const pr = +m[2];
    for (const fp of new Set([m[3], ...(v.covered_fingerprints || [])])) addRow({ key: `${m[1]}#${pr}:${fp}`, repo: m[1], pr, fingerprint: fp, sent_at: v.sent_at, via: 'feishu-sent' });
    continue;
  }
  const d = key.match(/^dm:(\d{4}-\d{2}-\d{2}):(\d+)$/); if (!d) continue;
  const pr = +d[2]; const rep = reports[pr];
  const at = [v?.sent_at, v?.grok_line_send?.sent_at].filter(Boolean).sort()[0] || null; // earliest real send of this DM
  if (!at) { gaps.push(`私信 ${key} 无发送时间，未入账`); continue; }
  if (!rep) { gaps.push(`私信 ${key}：#${pr} 无 reports/merged/PR-${pr}.json，未入账`); continue; }
  const fs_ = (rep.findings || []).filter(dmStandsFor(at));
  if (!fs_.length) { gaps.push(`私信 ${key}：#${pr} 报告里没有可入账的 P0–P2（只有线索/待确认），未入账`); continue; }
  for (const f of fs_) addRow({ key: `${REPO}#${pr}:${f.fingerprint}`, repo: REPO, pr, fingerprint: f.fingerprint, sent_at: at, via: 'dm-key', dedupe_key: key });
}
// 自己的 PR（Praise 2026-10-23：只修自己的 PR，测出问题立刻修，待核实的也修 —— 复现测试本身就是核实）：收录规则和
// prfix.py own_sev() = pi-fallback bin/finding_policy.py own_pr_ticket() 一致（JS 副本，改一边要改另一边）。
// findings ∪ pending_verification；等级取 severity，没有就取 severity_if_confirmed；去掉误报 / REJECTED / pre-existing /
// not_a_pr_finding / flaky / qa-error / 非 introduced 的检查。P0/P1 不论是否待核实都收。P2：确认成立的（在 findings 里、不是待核实、
// 审查结论不是 PLAUSIBLE）收，除非该 PR 在 p2-rulings.md 判了 不修 / 产品代码不修 / 误报 / P3 / 已修（10-23 起确认成立的 P2 要修）；
// 待核实 / 没确认的 P2 不派活但台账留行（no_ticket）。只差覆盖率（coverage-gate / own-tests）只在 P2 且判「修」时收。
// 待核实的行带 pending_verification: true
const RULINGS = process.env.MIVO_QA_P2_RULINGS || '/workspace/fleet-standards/p2-rulings.md';
// nofix / closed hold a PR number (row without 指纹: the whole PR) or "<PR>:<fingerprint>" (row with 指纹, p2.py record --fp:
// | 日期 | 报告 PR | 指纹 | 问题 | 裁决 | …; that finding only) -- same rows as finding_policy.ruling_rows. Rollback switch: the file
// $PI_STATE/p2-fp-rulings-off (re-read every run) -> fingerprint rows count for their whole PR again.
const fix2 = new Set(), nofix = new Set(), closed = new Set(), drop = new Set();  // drop = 不修 / 产品代码不修 / 误报 / P3: P2 row removed (已修 / 结案… keep the row, no_ticket)  // closed = 「结案…」: out of needs_you (PM 2026-11-02 16:22 PST)
const NOFIX = ['不修', '产品代码不修', '误报', 'P3', '已修'];
const DROP = ['不修', '产品代码不修', '误报', 'P3'];  // 11-05 #1409: 已修 / 结案… = fixed elsewhere -> keep the row (no_ticket), never drop it  // + any 「结案…」(already fixed, no new ticket; p2.py record 结案)
const FP_OFF = fs.existsSync(path.join(process.env.PI_STATE || '/workspace/mivo-qa/pi', 'p2-fp-rulings-off'));
const signed = (set, n, fp) => set.has(n) || set.has(`${n}:${fp}`);
try { for (const line of fs.readFileSync(RULINGS, 'utf8').split('\n')) { const c = line.trim().replace(/^\||\|$/g, '').split('|').map(x => x.trim()); if (c.length >= 4 && /^#?\d+$/.test(c[1] || '')) {
  const n = +c[1].replace('#', ''), fpRow = /^mq1-[0-9a-z]+(-pr\d+)?$/.test(c[2]); if (fpRow && c.length < 5) continue;
  const v = fpRow ? c[4] : c[3], k = fpRow && !FP_OFF ? `${n}:${c[2].replace(/-pr\d+$/, '')}` : n;
  if (v === '修') fix2.add(n); if (NOFIX.includes(v) || v.startsWith('结案')) nofix.add(k); if (v.startsWith('结案')) closed.add(k); if (DROP.includes(v)) drop.add(k); } } }
catch { gaps.push(`p2-rulings.md 读不到（${RULINGS}），按没有裁决处理`); }
// -> { sev, ticket }：sev = 台账要不要留这一行（null = 不留）；ticket = prfix 会不会派活（own_pr_ticket 非空）
function ownTicket(x, n, pending) {
  const sev = x.severity || x.severity_if_confirmed;
  if (!['P0', 'P1', 'P2'].includes(sev)) return null;
  if (x.pre_existing || x.not_a_pr_finding || x.flaky === true) return null;
  if ((x.calibration || {}).to === '误报' || /false_positive|misreport|REJECTED/i.test(String(x.verdict || ''))) return null;
  const src = x.source || 'check';
  if (src === 'qa-error') return null;
  if (src === 'coverage-gate' || src === 'own-tests') return sev === 'P2' && fix2.has(n) ? { sev, ticket: true } : null;
  if (src === 'check' && ![undefined, null, 'introduced'].includes(x.status)) return null;
  if (nofix.has(`${n}:${x.fingerprint}`)) return null;  // 11-04: signed per-fingerprint no-fix row exempts that finding at any level; PR-wide rows stay P2-only (finding_policy.own_pr_ticket)
  if (sev === 'P2') {
    const unconfirmed = pending || !!x.pending_kind || String(x.verdict || '').toUpperCase() === 'PLAUSIBLE';
    if (signed(drop, n, x.fingerprint)) return null;
    if (signed(nofix, n, x.fingerprint)) return { sev, ticket: false };  // 已修 / 结案…: keep the row, no ticket
    return { sev, ticket: !unconfirmed };
  }
  return { sev, ticket: true };
}
for (const [n, rep] of Object.entries(reports)) {
  if (!OWNERS.includes(loginOf(rep)) && !(OWNERS.includes('PraiseZhu') && SELF_BOTS.includes(loginOf(rep)))) continue;   // task B: App-authored fix PRs are Praise's too
  const at = reportAt[n] || rep.merged_at || null; if (!at) { gaps.push(`#${n}（${loginOf(rep)}）报告无生成时间，本人问题未入账`); continue; }
  const pv = new Set(rep.pending_verification || []);
  for (const f of [...(rep.findings || []), ...(rep.pending_verification || [])]) {
    const pending = pv.has(f) || !notifiable(f);
    const t = f.fingerprint && ownTicket(f, +n, pending); if (!t) continue;
    addRow({ key: `${REPO}#${n}:${f.fingerprint}`, repo: REPO, pr: +n, fingerprint: f.fingerprint, sent_at: at, via: 'owner-report', ...(pending ? { pending: true } : {}), ...(t.ticket ? {} : { no_ticket: true }) });
  }
}
// original-case retest evidence: fix PR reports carrying prs[].retest[] for <orig PR>:<fp> (opsfix retest / panoptes) tie that
// fix PR to the row even when the GitHub-reference heuristics miss it (same rule opsfix_retest.apply_ledger uses)
const retestFixes = {};
for (const [n, rep] of Object.entries(reports)) for (const x of [...(rep.retest || []), ...(rep.fix_retest || [])])
  if (x?.original_pr && x?.fingerprint) (retestFixes[`${x.original_pr}:${x.fingerprint}`] ||= new Set()).add(+n);

const lastChecked = now.toISOString();
const out = [];
for (const r of rows.values()) {
  const f = findFinding(r.pr, r.fingerprint); const st = stateByFp[`${r.pr}:${r.fingerprint}`];
  const rep = reports[r.pr];
  if (!rep) gaps.push(`#${r.pr} 无 reports/merged/PR-${r.pr}.json`);
  else if (!f) gaps.push(`#${r.pr} ${r.fingerprint} 在报告中找不到对应 finding`);
  const idx = st ? (st.fingerprints || []).indexOf(r.fingerprint) : -1;
  // problems[] is only trusted when aligned 1:1 with fingerprints[]; otherwise use the matched finding's own text
  const aligned = st && idx >= 0 && (st.problems || []).length === (st.fingerprints || []).length;
  const summary = (aligned && st.problems[idx]) || f?.title_zh || f?.title || f?.check_name || '(无摘要)';
  const notice_date = sh(new Date(r.sent_at));
  const inGroup = [...(groupDays[r.pr] || [])].sort();
  const isDm = r.via !== 'owner-report'; // owner rows: no DM by rule; notice = the report (and the group report @ if any)
  const row = {
    key: r.key, repo: r.repo, pr: r.pr, fingerprint: r.fingerprint,
    author: st?.login || rep?.author?.login || rep?.author || null,
    level: f?.severity || null, severity: f?.severity || null, source: f?.source || null, ...(r.pending || f?._pending ? { pending_verification: true } : {}), ...(r.no_ticket ? { no_ticket: true } : {}),
    summary, notice: { dm_sent_at: isDm ? r.sent_at : null, date: notice_date, channels: [...(isDm ? ['dm'] : []), ...(inGroup.length ? ['group'] : []), ...(isDm ? [] : ['report'])], group_dates: inGroup,
      via: r.via, ...(r.dedupe_key ? { dedupe_key: r.dedupe_key } : {}), ...(isDm ? {} : { report_at: r.sent_at }) },
    reminders: st?.reminders ?? null,
    status: 'open', status_zh: '未修无结论', fix_pr: null, merged: null, fix_prs: [], concluded_reason: null, evidence: [],
    last_checked: lastChecked,
  };
  if (f?._from_acceptance) { row.level_source = 'acceptance-review'; gaps.push(`${r.key} 合并报告无此 finding，级别/摘要取自 acceptance/${f._from_acceptance}`); }
  if (f?._from_history) { row.level_source = 'history'; gaps.push(`${r.key} 当前报告已无此 finding，级别取自 history/${f._from_history}`); }
  if (!row.level) gaps.push(`${r.key} 缺级别`);
  const hold = holds.find(h => h.pr === r.pr);
  // fix-pr-merged（2026-10-23）：作者修复 PR 写了「QA #原PR号」并已合并，和 pr-references 一样当修复 PR 证据
  const refs = (st?.evidence || []).filter(e => e.type === 'pr-references' || e.type === 'fix-pr-merged');
  const comment = (st?.evidence || []).find(e => e.type === 'pr-comment');
  // 原 PR 下首行「结论：…」的回执（统一格式，2026-10-23）：已修 #N → 修复 PR；不修 / 误报 → 有结论（带原因）
  const prReceipt = (st?.evidence || []).find(e => ['pr-comment', 'pr-review', 'pr-review-comment'].includes(e.type) && e.receipt?.kind);
  // window starts at the original PR's merge (QA report time), not the DM: fix PRs are often opened before the DM is sent
  const mergedAt = ghByNum[r.pr]?.mergedAt; const since = mergedAt && mergedAt < r.sent_at ? mergedAt : r.sent_at;
  const gh = fixPRsFor(r.pr, since);
  const stFilesFor = e => (e.files || []).length ? e.files : (aligned || (st?.fingerprints || []).length === 1 ? (st?.files || []) : []);
  const allFixes = [...refs.map(e => ({ pr: e.pr, merged: ghByNum[e.pr] ? ghByNum[e.pr].state === 'MERGED' : null, state: ghByNum[e.pr]?.state?.toLowerCase() || 'unknown', via: 'followup-evidence', _anchored: (fs_ => fs_.length > 0 && (!f?.file || fs_.includes(f.file)))(stFilesFor(e)) })),
    ...(gh || []).filter(g => !refs.some(e => e.pr === g.pr)).map(g => ({ ...g, _anchored: false }))];
  for (const n of [...(retestFixes[`${r.pr}:${r.fingerprint}`] || [])].sort((a, b) => a - b)) {  // an original-case retest of THIS row anchors the fix PR
    const c = allFixes.find(x => x.pr === n);
    if (c) c._anchored = true;
    else allFixes.push({ pr: n, merged: ghByNum[n] ? (ghByNum[n].state === 'MERGED' || !!ghByNum[n].mergedAt) : true, state: ghByNum[n]?.state?.toLowerCase() || 'merged', via: 'qa-ref(retest)', _anchored: true }); // has a merged report → merged
  }
  // a fix PR is evidence only for the finding it addresses (file anchored by followup, or PR touches the finding's file / is a coverage fix)
  const fixes = [], rejected = [];
  for (const { _anchored, ...x } of allFixes) (addresses(x, f, _anchored) === true ? fixes : rejected).push(x);
  if (rejected.length) row.fix_candidates_rejected = rejected.map(x => x.pr);
  // handoff receipts (followup/state.json handoff_receipt / evidence type handoff-receipt; issue label checked via gh when available)
  const hr = handoffReceipt(st, r.fingerprint);
  if (hold) Object.assign(row, { status: 'concluded', status_zh: '有结论', concluded_reason: `误报/搁置：${hold.reason}` });
  else if (f && (/false_positive|misreport/.test(f.status || ''))) Object.assign(row, { status: 'concluded', status_zh: '有结论', concluded_reason: '误报（报告已校准为 false_positive）' });
  else if (hr?.kind === 'fixed') Object.assign(row, { status: 'fixed', status_zh: '已修复', fix_pr: hr.fix_pr, merged: ghByNum[hr.fix_pr] ? ghByNum[hr.fix_pr].state === 'MERGED' : null, fix_prs: [{ pr: hr.fix_pr, merged: ghByNum[hr.fix_pr]?.state === 'MERGED', state: ghByNum[hr.fix_pr]?.state?.toLowerCase() || 'unknown', via: 'handoff-receipt' }], evidence: [hr.url] });
  else if (hr) Object.assign(row, { status: 'concluded', status_zh: '有结论', concluded_reason: `${hr.label}（交接仓回执${hr.reason ? '：' + hr.reason : ''}）：${hr.url}`, evidence: [hr.url] });
  else if (fixes.length) { const best = fixes.find(x => x.merged) || fixes[0];
    Object.assign(row, { status: 'fixed', status_zh: '已修复', fix_pr: best.pr, merged: best.merged, fix_prs: fixes }); }
  else if (prReceipt?.receipt.kind === '已修' && prReceipt.receipt.fix_pr) { const n = prReceipt.receipt.fix_pr;
    Object.assign(row, { status: 'fixed', status_zh: '已修复', fix_pr: n, merged: ghByNum[n] ? ghByNum[n].state === 'MERGED' : null, fix_prs: [{ pr: n, merged: ghByNum[n]?.state === 'MERGED', state: ghByNum[n]?.state?.toLowerCase() || 'unknown', via: 'pr-comment-receipt' }], evidence: [prReceipt.url] }); }
  else if (prReceipt && ['不修', '误报'].includes(prReceipt.receipt.kind)) Object.assign(row, { status: 'concluded', status_zh: '有结论', concluded_reason: `${prReceipt.receipt.kind}（原 PR 下回执${prReceipt.receipt.reason ? '：' + prReceipt.receipt.reason : ''}）：${prReceipt.url}`, evidence: [prReceipt.url] });
  else if (comment) Object.assign(row, { status: 'concluded', status_zh: '有结论', concluded_reason: '作者在原 PR 留言说明（不修/已处理）', evidence: [comment.url] });
  else if (!gh && ghErr) Object.assign(row, { status: 'open', status_zh: '未修无结论', fix_check: 'unknown' });
  if (st?.status === 'closed' && row.status === 'open') gaps.push(`${r.key} followup 已关闭但未识别出修复/结论`);
  row.evidence.push(...refs.map(e => e.url));
  out.push(row);
}
out.sort((a, b) => a.pr - b.pr || a.fingerprint.localeCompare(b.fingerprint));

// manual overrides (ledger/overrides.json), applied last: { "<row key>": {status, fix_pr, merged?, concluded_reason?, summary?, note} }
const STZH = { fixed: '已修复', fix_merged_pending_retest: '修复已合并，待复测', fix_pr_open: '修复 PR 未合并', concluded: '有结论', open: '未修无结论' };
const overrides = rj(path.join(QA, 'ledger/overrides.json'), {});
const notes = [];
for (const [k, o] of Object.entries(overrides)) {
  if (k.startsWith('_')) continue;
  const row = out.find(r => r.key === k); if (!row) { gaps.push(`overrides.json 键 ${k} 无对应行`); continue; }
  if (o.status && STZH[o.status]) {
    Object.assign(row, { status: o.status, status_zh: STZH[o.status] });
    if (o.status === 'fixed' || o.status === 'fix_merged_pending_retest') { const pr = o.fix_pr ?? row.fix_pr;
      // 合并状态只从 GitHub 实时读（overrides 里的 merged 字段已弃用，避免冻结旧状态）
      const g = ghByNum[pr]; const merged = g ? (g.state === 'MERGED' || !!g.mergedAt) : null;
      Object.assign(row, { status: 'fixed', status_zh: STZH.fixed, fix_pr: pr, merged, fix_prs: [{ pr, merged, state: g ? g.state.toLowerCase() : 'unknown', via: o.via || 'manual-override' }], concluded_reason: null }); }
    else if (o.status === 'concluded') Object.assign(row, { concluded_reason: o.concluded_reason || row.concluded_reason || '人工结论', fix_pr: null, merged: null, fix_prs: [] });
    else Object.assign(row, { fix_pr: null, merged: null, fix_prs: [], concluded_reason: null });
  }
  if (o.summary) row.summary = o.summary;
  row.override = { note: o.note || null, at: o.at || null };
  if (o.note) notes.push(`${o.at || ''} 人工更正 #${row.pr}（${row.level || '?'}）：${o.note}`.trim());
}

// 修复 PR 复测回写 + 轮次（runbook「修复 PR 的测试规则」，2026-10-21）
// retest: 用修复 PR 自己的 reports/merged/PR-<fix>.json 判断原问题是否解决。
//   fail    = 修复 PR 报告里仍有原 fingerprint（同文件的新问题不算：那是修复 PR 自己的问题）→ 行改回 open，仍记原 PR 作者
//   pass    = 报告显式写了针对原问题的复测通过（prs[].retest / fix_retest 里 fingerprint 匹配且 result=pass）
//   pending = 修复 PR 未合并 / 尚无报告 / 报告未做原用例复测（panoptes 复测功能上线前的常态）——不改状态
// fix_round = 该问题已合并的自动修复 PR 个数（不含手工结论）；最多两轮，第三轮（≥3，或第 2 轮复测仍失败）→ needs_human
// 第三轮不论状态都进「需要你做」（2026-11-02 审计第 4 项：和 pm_extra「第三轮未报出」同一口径，修好了也要人确认结案、不开第 4 轮）
const HO_RECORDS = HANDOFF_ROUTES.routes.map(rt => ({ repo: rt.repo, file: path.join(QA, 'handoff', rt.repo.split('/')[0], 'issues.json') }));
const hoByKey = {};
for (const h of HO_RECORDS) for (const i of (rj(h.file, { issues: [] }).issues || [])) hoByKey[i.key] = { ...i, repo: h.repo };
const MAX_AUTO_ROUNDS = 2;
function retestOf(fixPr, row, f) {
  const rep = reports[fixPr];
  if (!rep) return { pr: fixPr, result: 'pending', reason: '修复 PR 无合并报告（未合并或未测）' };
  // match THIS row: fingerprint + original PR; a fingerprint-only / PR-only entry counts only when the other field is absent
  // (2026-10-23: the old `fp || original_pr` let one finding's retest stand for every finding of the same PR, and the
  //  generic coverage fingerprint stand for other PRs' coverage rows)
  const all = [...(rep.retest || []), ...(rep.fix_retest || [])];
  const explicit = all.find(x => x.fingerprint === row.fingerprint && x.original_pr === row.pr)
    || all.find(x => x.fingerprint === row.fingerprint && x.original_pr == null) || all.find(x => x.original_pr === row.pr && !x.fingerprint);
  if (explicit) {
    const res = /pass/i.test(explicit.result || '') ? 'pass' : /fail/i.test(explicit.result || '') ? 'fail' : 'pending'; // blocked/unsure ≠ fail
    return { pr: fixPr, result: res, reason: res === 'pending' ? `修复 PR 报告复测未出结论（${explicit.result}）` : '修复 PR 报告显式复测', report: `reports/merged/PR-${fixPr}.json`, ...(explicit.at ? { at: explicit.at } : {}) };
  }
  const same = (rep.findings || []).find(x => x.fingerprint === row.fingerprint);
  if (same) return { pr: fixPr, result: 'fail', reason: `修复 PR 报告仍有同一问题（${same.fingerprint}）`, report: `reports/merged/PR-${fixPr}.json`, ...(reportAt[fixPr] ? { at: reportAt[fixPr] } : {}) };
  return { pr: fixPr, result: 'pending', reason: '修复 PR 报告未包含原用例复测结果', report: `reports/merged/PR-${fixPr}.json` };
}
// one row, several fix PRs → which retest decides (2026-10-23, same rule as pi-fallback opsfix_retest.effective_retest):
// any fail wins (latest fail by `at`); otherwise the latest `at` (entries without `at` count as oldest; ties → later in list)
const atMs = s => { const t = Date.parse(s || ''); return Number.isFinite(t) ? t : -Infinity; };
function effectiveRetest(rts) {
  if (!rts || !rts.length) return null;
  const fails = rts.filter(x => x.result === 'fail'); const pool = fails.length ? fails : rts;
  return pool.reduce((best, x) => (atMs(x.at) >= atMs(best.at) ? x : best));
}
const needsYou = [];
for (const row of out) {
  const f = findFinding(row.pr, row.fingerprint);
  const fixPrs = [...new Set((row.fix_prs || []).filter(x => x.merged === true || reports[x.pr]).map(x => x.pr))].sort((a, b) => a - b);
  row.fix_round = fixPrs.length;
  row.retests = fixPrs.map(n => retestOf(n, row, f));
  const last = effectiveRetest(row.retests);
  row.retest = last ? last.result : null;
  row.retest_pr = last ? last.pr : null;
  const ho = hoByKey[`${row.fingerprint}-pr${row.pr}`];
  row.handoff_issue = ho ? { repo: ho.repo, issue: ho.issue, url: ho.url } : null;
  if (row.status === 'fixed' && last?.result === 'fail') {
    Object.assign(row, { status: 'open', status_zh: '未修无结论', retest_reopened: true, merged: null });
    gaps.push(`${row.key} 修复 PR #${last.pr} 复测未通过，已改回未修（仍记原作者 ${row.author || '?'}）`);
  } else if (row.status === 'fixed' && last?.result === 'pass') row.retest_confirmed = true;
  // 规则（2026-10-22）：复测通过前不算已修复。有修复 PR（无论是否合并）但复测未通过 → 修复已合并，待复测（未合并的留在 fixed 下由 fixed_not_merged 统计）
  if (row.status === 'fixed' && !row.retest_confirmed && row.merged === true) Object.assign(row, { status: 'fix_merged_pending_retest', status_zh: STZH.fix_merged_pending_retest });
  else if (row.status === 'fixed' && !row.retest_confirmed) Object.assign(row, { status: 'fix_pr_open', status_zh: STZH.fix_pr_open });
  // a 「结案」 ruling settles the PR: checked before the third-round rule, so a closed PR never lands in needs_you
  row.needs_human = !signed(closed, row.pr, row.fingerprint) && (row.fix_round > MAX_AUTO_ROUNDS || (row.status === 'open' && row.fix_round === MAX_AUTO_ROUNDS && row.retest === 'fail'));
  if (row.needs_human) needsYou.push({ key: row.key, pr: row.pr, author: row.author, level: row.level, severity: row.level, summary: row.summary, fix_round: row.fix_round, fix_prs: fixPrs, handoff_issue: row.handoff_issue?.url || null,
    reason: row.status === 'open' ? `已自动修 ${row.fix_round} 轮仍未通过复测，转人工` : `已修到第 ${row.fix_round} 轮（${row.status_zh}），请确认结案、不再开第 ${row.fix_round + 1} 轮` });
}
const counts = { fixed: 0, fix_merged_pending_retest: 0, fix_pr_open: 0, concluded: 0, open: 0 };
const openBy = { P0: 0, P1: 0, P2: 0, P3: 0, unknown: 0 };
for (const r of out) { counts[r.status]++; if (r.status === 'open') openBy[r.level in openBy ? r.level : 'unknown']++; }
const fixedUnmerged = out.filter(r => r.status === 'fixed' && r.merged !== true).length;
const doc = { schema_version: '1.0', kind: 'mivo-qa-feishu-ledger', date: today, timezone: 'Asia/Shanghai', repo: REPO,
  generated_at: lastChecked, github_check: ghErr ? { ok: false, error: ghErr } : { ok: true, prs_scanned: ghPRs.length },
  summary: { total: out.length, by_status: counts, open_by_level: openBy, fixed_not_merged: fixedUnmerged, fix_round: Object.fromEntries([...new Set(out.map(r => r.fix_round))].sort().map(k => [k, out.filter(r => r.fix_round === k).length])), retest: { pass: out.filter(r => r.retest === 'pass').length, fail: out.filter(r => r.retest === 'fail').length, pending: out.filter(r => r.retest === 'pending').length }, needs_you: needsYou.length,
    by_via: out.reduce((m, r) => (m[r.notice.via] = (m[r.notice.via] || 0) + 1, m), {}) },
  needs_you: needsYou,
  rows: out, gaps: [...new Set(gaps)] };
fs.mkdirSync(OUT, { recursive: true });
fs.writeFileSync(path.join(OUT, `${today}.json`), JSON.stringify(doc, null, 2) + '\n');

const esc = s => String(s).replace(/\|/g, '\\|').replace(/ou_[0-9a-f]+|oc_[0-9a-f]+/g, '[id]');
const ch = r => r.notice.channels.includes('dm') ? (r.notice.channels.includes('group') ? '私信+群' : '私信') : (r.notice.channels.includes('group') ? '群（未私信）' : '报告（未私信）');
let md = `# Mivo QA 飞书通知台账 ${today}（北京时间）\n\n生成于 ${shTime(lastChecked)}（北京时间）。共 ${out.length} 条已通知问题：已修复（复测通过）${counts.fixed}，修复已合并待复测 ${counts.fix_merged_pending_retest}，修复 PR 未合并 ${counts.fix_pr_open}，有结论 ${counts.concluded}，未修无结论 ${counts.open}。\n\n`;
md += `未修无结论按级别：P0 ${openBy.P0} / P1 ${openBy.P1} / P2 ${openBy.P2} / P3 ${openBy.P3}${openBy.unknown ? ` / 未知 ${openBy.unknown}` : ''}。`;
const ownerRows = out.filter(r => r.notice.via === 'owner-report').length;
if (ownerRows) md += `其中 ${ownerRows} 条是 Pi 修复范围（${OWNERS.join('、')}）PR 的 P0–P2，没有私信记录，直接从合并报告入账（PraiseZhu 本人按规则不私信）。`;
md += ghErr ? `\n\n⚠️ GitHub 查询失败，修复 PR 未能核对（标为未知）。\n` : `\n`;
const sect = (title, st, cols, fn) => { const rs = out.filter(r => r.status === st);
  md += `\n## ${title}（${rs.length} 条）\n\n`; if (!rs.length) { md += '无\n'; return; }
  md += `| ${cols.join(' | ')} |\n|${cols.map(() => '---').join('|')}|\n`; for (const r of rs) md += `| ${fn(r).map(esc).join(' | ')} |\n`; };
const base = r => [`#${r.pr}`, r.author || '?', r.level || '?', r.summary, `${r.notice.date} ${ch(r)}`];
md += `\n## 需要你做（${needsYou.length} 条）\n\n` + (needsYou.length ? needsYou.map(n => `- #${n.pr} @${n.author || '?'} ${n.level || '?'} ${esc(n.summary)}：${n.reason}（修复 PR ${n.fix_prs.map(x => '#' + x).join('、')}${n.handoff_issue ? '，' + n.handoff_issue : ''}）`).join('\n') + '\n' : '无\n');
sect('未修无结论', 'open', ['PR', '作者', '级别', '问题', '通知', '提醒次数', '轮次'], r => [...base(r), r.reminders ?? '-', r.fix_round]);
sect('修复已合并，待复测', 'fix_merged_pending_retest', ['PR', '作者', '级别', '问题', '通知', '修复 PR', '复测'], r => [...base(r), `#${r.fix_pr}（已合并）`, r.retest || '-']);
sect('修复 PR 未合并', 'fix_pr_open', ['PR', '作者', '级别', '问题', '通知', '修复 PR'], r => [...base(r), `#${r.fix_pr}${r.merged === false ? '（未合并）' : '（合并状态未知）'}`]);
sect('已修复', 'fixed', ['PR', '作者', '级别', '问题', '通知', '修复 PR'], r => [...base(r), `#${r.fix_pr}${r.merged ? '（已合并）' : r.merged === false ? '（未合并）' : '（合并状态未知）'}`]);
sect('有结论', 'concluded', ['PR', '作者', '级别', '问题', '通知', '结论'], r => [...base(r), r.concluded_reason]);
if (notes.length) md += `\n## 人工更正（ledger/overrides.json）\n\n${notes.map(n => `- ${esc(n)}`).join('\n')}\n`;
if (doc.gaps.length) md += `\n## 数据缺口\n\n${doc.gaps.map(g => `- ${esc(g)}`).join('\n')}\n`;
fs.writeFileSync(path.join(OUT, `${today}.md`), md);

// retention
const cutoff = sh(new Date(new Date(today + 'T00:00:00+08:00').getTime() - KEEP_DAYS * 864e5));
const removed = [];
if (!DRY) for (const f of fs.readdirSync(OUT)) { const m = f.match(/^(\d{4}-\d{2}-\d{2})\.(json|md)$/); if (m && m[1] < cutoff) { fs.unlinkSync(path.join(OUT, f)); removed.push(f); } }
console.log(JSON.stringify({ date: today, rows: out.length, by_status: counts, open_by_level: openBy, gaps: doc.gaps.length, removed }));
