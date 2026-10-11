// 浏览器旅程：旧格式数据迁移 + IndexedDB 恢复（Argus 自带，不改被测仓）。
// 用法（在被测仓 checkout 根目录）：node legacy-migration-journey.mjs <outDir>
// 做法：在 _tmp/argus-legacy-journey/ 写一个最小 harness，用被测仓自己的 vite.config.ts 起 Vite（middleware 模式），
// harness 只 import 仓里现有的迁移 / 持久化代码（src/lib/persistIdbStorage.ts、src/lib/persistUserId.ts、
// src/kernel/persistMigration.ts、src/store/canvasStore.ts 的 migratePersistedState），用无头 Chromium 打开：
//   1. 播种：清空 IDB / localStorage，往 localStorage 写旧版（persist v7 flat-state）canvas blob —— 形状取自仓里
//      src/store/canvasStoreMigrate.test.ts 的 imageNode / task 夹具
//   2. 加载（真实刷新页面）：idbStateStorage.getItem 触发一次性 localStorage → IDB 迁移；旧 key 必须被删、IDB 里有原值
//   3. migratePersistedState(旧 state, 7) 把 flat-state 迁进当前 scene（节点带 transform / asset）
//   4. dryRunMigration(rawIdbStorage) 读出来源版本 7、项目数一致，且不写任何东西
//   5. 登录后（setPersistUserId）认领匿名旧 key → <name>:<uid>；再刷新页面仍能读回（IDB 恢复）
//   6. 坏 blob：dryRunMigration 返回 ok:false + corrupt blob 诊断，不抛
//   7. IndexedDB 不可用（新上下文里删掉 window.indexedDB）：读写退回 localStorage，数据不丢
// 输出 <outDir>/report.json：{ status, checks:[{id,status,reason,evidence}] }（Argus 的 checks 报告格式）
// 退出码：0 全部通过；1 有检查失败；2 环境不可用（没有 Playwright / 浏览器 / Vite / 仓里缺这些模块）
import { createServer } from 'node:http'
import { existsSync, mkdirSync, writeFileSync, rmSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const [outDir = '_tmp/legacy-migration-journey'] = process.argv.slice(2)
mkdirSync(outDir, { recursive: true })
const ROOT = process.cwd()
const checks = []
const check = (id, status, reason = null, evidence = {}) => checks.push({ id, status, reason, evidence })
let cleanup = async () => {}
const finish = async (code) => {
  try { await cleanup() } catch { /* ignore */ }
  const status = checks.some((c) => c.status === 'fail') ? 'fail' : checks.length && checks.every((c) => c.status === 'pass') ? 'pass' : 'unavailable'
  writeFileSync(join(outDir, 'report.json'), JSON.stringify({ status, checks }, null, 2))
  for (const c of checks) console.log(`${c.status === 'pass' ? '✔' : c.status === 'fail' ? '✖' : '-'} ${c.id}${c.status === 'pass' ? '' : ` — ${c.reason}`}`)
  if (code === 2) console.log(checks.find((c) => c.status === 'unavailable')?.reason ?? 'journey unavailable')
  process.exit(code === 2 ? 2 : status === 'fail' ? 1 : code)
}

const MODULES = ['src/lib/persistIdbStorage.ts', 'src/lib/persistUserId.ts', 'src/kernel/persistMigration.ts', 'src/store/canvasStore.ts', 'vite.config.ts']
const missing = MODULES.filter((m) => !existsSync(join(ROOT, m)))
if (missing.length) { check('lm-prerequisites', 'unavailable', `this revision lacks ${missing.join(', ')}`); await finish(2) }
const source = (f) => readFileSync(join(ROOT, f), 'utf8')
for (const [file, sym] of [['src/lib/persistIdbStorage.ts', 'export const idbStateStorage'], ['src/lib/persistIdbStorage.ts', 'export const rawIdbStorage'], ['src/kernel/persistMigration.ts', 'export const dryRunMigration'], ['src/lib/persistUserId.ts', 'export const setPersistUserId']]) {
  if (!source(file).includes(sym)) { check('lm-prerequisites', 'unavailable', `${file} has no ${sym.replace('export const ', '')}`); await finish(2) }
}

let chromium
try {
  const mod = process.env.ARGUS_PLAYWRIGHT_MODULE
  ;({ chromium } = mod ? await import(pathToFileURL(mod).href) : await import('playwright'))
} catch (err) { check('lm-runtime', 'unavailable', `playwright not importable: ${err.message}`); await finish(2) }
let createViteServer
try {
  ;({ createServer: createViteServer } = await import(pathToFileURL(join(ROOT, 'node_modules/vite/dist/node/index.js')).href))
} catch (err) { check('lm-runtime', 'unavailable', `vite not importable from the checkout: ${String(err.message).split('\n')[0]}`); await finish(2) }

// ── harness（只放在被测仓已忽略的 _tmp/ 下，结束删除）──
const HARNESS_DIR = join(ROOT, '_tmp', 'argus-legacy-journey')
rmSync(HARNESS_DIR, { recursive: true, force: true })
mkdirSync(HARNESS_DIR, { recursive: true })
writeFileSync(join(HARNESS_DIR, 'harness.ts'), `
import { idbStateStorage, rawIdbStorage } from '/src/lib/persistIdbStorage.ts'
import { setPersistUserId, namespacedKey } from '/src/lib/persistUserId.ts'
import { dryRunMigration } from '/src/kernel/persistMigration.ts'
import { migratePersistedState } from '/src/store/canvasStore.ts'
const raw = rawIdbStorage as never
;(window as any).__argusLegacy = {
  idbAvailable: () => typeof indexedDB !== 'undefined' && indexedDB !== null,
  get: (name: string) => idbStateStorage.getItem(name),
  set: (name: string, value: string) => idbStateStorage.setItem(name, value),
  rawGet: (key: string) => rawIdbStorage.getItem(key),
  rawSet: (key: string, value: string) => rawIdbStorage.setItem(key, value),
  setUser: (id: string | null) => setPersistUserId(id),
  key: (name: string) => namespacedKey(name),
  dryRun: (key: string) => dryRunMigration(raw, key),
  migrate: (state: unknown, version: number) => {
    const out = migratePersistedState(state as never, version) as any
    return { sceneId: out.sceneId, selectedNodeId: out.selectedNodeId ?? null,
      nodes: (out.nodes ?? []).map((n: any) => ({ id: n.id, type: n.type, transform: n.transform ?? null, asset: n.asset ?? null })),
      tasks: (out.tasks ?? []).map((t: any) => t.id) }
  },
}
;(window as any).__argusLegacyReady = true
`)
const HTML = '<!doctype html><html><head><meta charset="utf-8"><title>argus legacy journey</title></head><body><div id="root"></div><script type="module" src="/_tmp/argus-legacy-journey/harness.ts"></script></body></html>'

let vite, server, browser
cleanup = async () => {
  try { await browser?.close() } catch { /* ignore */ }
  try { server?.close() } catch { /* ignore */ }
  try { await vite?.close() } catch { /* ignore */ }
  rmSync(HARNESS_DIR, { recursive: true, force: true })
}
try {
  vite = await createViteServer({ root: ROOT, appType: 'custom', logLevel: 'error', server: { middlewareMode: true, hmr: false, watch: null }, configFile: join(ROOT, 'vite.config.ts') })
  server = createServer((req, res) => {
    if (req.url === '/' || req.url?.startsWith('/journey.html')) {
      vite.transformIndexHtml('/journey.html', HTML).then((html) => { res.writeHead(200, { 'content-type': 'text/html' }); res.end(html) }, (e) => { res.writeHead(500); res.end(String(e)) })
      return
    }
    vite.middlewares(req, res, () => { res.writeHead(404); res.end('not found') })
  })
  await new Promise((r) => server.listen(0, '127.0.0.1', r))
} catch (err) { check('lm-vite', 'unavailable', `vite dev server failed: ${String(err.message).split('\n')[0]}`); await finish(2) }
const base = `http://127.0.0.1:${server.address().port}`
try {
  browser = await chromium.launch({ headless: true, args: ['--disable-gpu', '--disable-dev-shm-usage'] })
} catch (err) { check('lm-browser-launch', 'unavailable', `chromium failed to launch: ${String(err.message).split('\n')[0]}`); await finish(2) }

const NAME = 'mivo-canvas-demo'
// 旧版 v7 flat-state：节点 / 任务形状同 src/store/canvasStoreMigrate.test.ts 的 imageNode() / task() 夹具
const LEGACY_STATE = {
  sceneId: 'character-flow',
  nodes: [{ id: 'img-1', type: 'image', title: 'Image', x: 10, y: 20, width: 300, height: 200, status: 'ready', assetUrl: '/a.png' }],
  tasks: [{ id: 'task-1', label: 'task', status: 'done', progress: 100, nodeIds: ['img-1'] }],
  selectedNodeId: 'img-1', selectedNodeIds: ['img-1'],
  projects: [{ id: 'argus-legacy-project', name: 'Legacy project', createdAt: '2025-01-01T00:00:00.000Z' }],
}
const LEGACY_BLOB = JSON.stringify({ state: LEGACY_STATE, version: 7 })
const pageErrors = []

async function openHarness(ctx) {
  const page = await ctx.newPage()
  page.on('pageerror', (e) => pageErrors.push(String(e.message).slice(0, 300)))
  await page.goto(`${base}/journey.html`, { waitUntil: 'load', timeout: 60_000 })
  await page.waitForFunction(() => window.__argusLegacyReady === true, null, { timeout: 60_000 })
  return page
}
const call = (page, fn, ...args) => page.evaluate(([f, a]) => window.__argusLegacy[f](...a), [fn, args])

async function step(id, fn) {
  try {
    const r = await fn()
    if (r === true || r?.ok === true) check(id, 'pass', null, r?.evidence ?? {})
    else check(id, 'fail', r?.reason ?? 'assertion failed', r?.evidence ?? {})
  } catch (err) { check(id, 'fail', `assert: ${String(err.message).split('\n')[0].slice(0, 300)}`) }
}

try {
  const ctx = await browser.newContext()
  let page = await openHarness(ctx)
  // 1. 播种
  await page.evaluate(async ([name, blob]) => {
    localStorage.clear(); sessionStorage.clear()
    await new Promise((res) => { const r = indexedDB.deleteDatabase('mivo-canvas-persist'); r.onsuccess = r.onerror = r.onblocked = () => res() })
    localStorage.setItem(name, blob)
  }, [NAME, LEGACY_BLOB])
  // 2. 加载（刷新页面 = 新的模块实例）
  await page.reload({ waitUntil: 'load' })
  await page.waitForFunction(() => window.__argusLegacyReady === true, null, { timeout: 60_000 })
  await step('lm-legacy-localstorage-to-idb', async () => {
    const got = await call(page, 'get', NAME)
    const left = await page.evaluate((n) => localStorage.getItem(n), NAME)
    const inIdb = await call(page, 'rawGet', NAME)
    return { ok: got === LEGACY_BLOB && left === null && inIdb === LEGACY_BLOB, reason: `assert: read back ${got === LEGACY_BLOB ? 'ok' : 'different'}, legacy localStorage key ${left === null ? 'removed' : 'still present'}, IDB copy ${inIdb === LEGACY_BLOB ? 'ok' : 'missing'}` }
  })
  // 3. 旧 state 迁移
  await step('lm-legacy-state-migrated', async () => {
    const m = await call(page, 'migrate', LEGACY_STATE, 7)
    const node = m.nodes.find((n) => n.id === 'img-1')
    const ok = Boolean(node) && JSON.stringify(node.transform) === JSON.stringify({ x: 10, y: 20, width: 300, height: 200, rotation: 0 }) && node.asset?.url === '/a.png' && m.tasks.includes('task-1') && m.selectedNodeId === 'img-1'
    return { ok, reason: `assert: migrated v7 state ${JSON.stringify(m).slice(0, 300)}`, evidence: { sceneId: m.sceneId, nodes: m.nodes.length } }
  })
  // 4. dry-run 报告
  await step('lm-dry-run-report', async () => {
    const r = await call(page, 'dryRun', NAME)
    const after = await call(page, 'rawGet', NAME)
    return { ok: r.ok === true && r.sourceVersion === 7 && r.document.projectCount === 1 && r.session.selectionCount === 1 && after === LEGACY_BLOB, reason: `assert: dry-run ${JSON.stringify(r).slice(0, 300)}`, evidence: r }
  })
  // 5. 登录后认领 + 刷新后恢复
  await step('lm-namespace-claim', async () => {
    await call(page, 'setUser', 'argus-qa')
    const got = await call(page, 'get', NAME)
    const rawLeft = await call(page, 'rawGet', NAME)
    const claimed = await call(page, 'rawGet', `${NAME}:argus-qa`)
    return { ok: got === LEGACY_BLOB && rawLeft === null && claimed === LEGACY_BLOB, reason: `assert: claim read ${got === LEGACY_BLOB ? 'ok' : 'different'}, anonymous key ${rawLeft === null ? 'removed' : 'still present'}, namespaced copy ${claimed === LEGACY_BLOB ? 'ok' : 'missing'}` }
  })
  await page.close()
  page = await openHarness(ctx)
  await step('lm-idb-reload-recovery', async () => {
    await call(page, 'setUser', 'argus-qa')
    const got = await call(page, 'get', NAME)
    return { ok: got === LEGACY_BLOB, reason: `assert: after reload the namespaced state ${got == null ? 'is gone' : 'differs'}` }
  })
  // 6. 坏 blob
  await step('lm-corrupt-blob-diagnosed', async () => {
    await call(page, 'rawSet', 'argus-legacy-corrupt', '{"state": {not json')
    const r = await call(page, 'dryRun', 'argus-legacy-corrupt')
    return { ok: r.ok === false && /corrupt blob/.test(r.error ?? ''), reason: `assert: corrupt blob report ${JSON.stringify(r).slice(0, 200)}` }
  })
  await ctx.close()
  // 7. IndexedDB 不可用 → localStorage 兜底
  const ctx2 = await browser.newContext()
  await ctx2.addInitScript(() => { try { Object.defineProperty(window, 'indexedDB', { value: undefined, configurable: true }) } catch { /* ignore */ } })
  const p2 = await openHarness(ctx2)
  await step('lm-idb-unavailable-fallback', async () => {
    if (await call(p2, 'idbAvailable')) return { ok: false, reason: 'assert: could not disable indexedDB in the page' }
    await p2.evaluate(([n, b]) => { localStorage.clear(); localStorage.setItem(n, b) }, [NAME, LEGACY_BLOB])
    const first = await call(p2, 'get', NAME)
    await call(p2, 'set', NAME, JSON.stringify({ state: { ...LEGACY_STATE, sceneId: 'character-flow' }, version: 8 }))
    await p2.reload({ waitUntil: 'load' })
    await p2.waitForFunction(() => window.__argusLegacyReady === true, null, { timeout: 60_000 })
    const second = await call(p2, 'get', NAME)
    return { ok: first === LEGACY_BLOB && JSON.parse(second ?? 'null')?.version === 8, reason: `assert: localStorage fallback read ${first === LEGACY_BLOB ? 'ok' : 'failed'}, write+reload ${second ? 'ok' : 'lost'}` }
  })
  await ctx2.close()
  if (pageErrors.length) check('lm-no-page-errors', 'fail', `assert: uncaught page errors: ${pageErrors.slice(0, 3).join(' | ')}`)
  else check('lm-no-page-errors', 'pass')
} catch (err) {
  check('lm-journey', 'fail', `journey crashed: ${String(err.message).split('\n')[0].slice(0, 300)}`)
}
await finish(0)
