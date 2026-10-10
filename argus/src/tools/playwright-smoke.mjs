// 黑盒冒烟（Playwright + 无头 Chromium）：只从外部打开插件构建产物，不看源码。
// 用法：node playwright-smoke.mjs <outDir> [distDir=cindyplugin/dist] [page=panel.html,settings.html]
// 构建产物缺失时先跑 cindyplugin/build.sh（对照版本只重跑失败步骤时也能自给自足）。
// 输出 <outDir>/report.json：{ status, checks:[{id,status,reason,evidence}] }，供 Argus 的 checks 报告解析。
// 退出码：0 全部通过；1 有检查失败；2 环境不可用（没有 Playwright / 浏览器 / 构建失败）。
import { createServer } from 'node:http'
import { existsSync, mkdirSync, readFileSync, writeFileSync, statSync } from 'node:fs'
import { join, extname, resolve, relative, isAbsolute } from 'node:path'
import { spawnSync } from 'node:child_process'
import { pathToFileURL } from 'node:url'

const [outDir = '_tmp/playwright-smoke', distArg = 'cindyplugin/dist', pagesArg = 'panel.html,settings.html'] = process.argv.slice(2)
const dist = resolve(distArg)
mkdirSync(outDir, { recursive: true })
const checks = []
const check = (id, status, reason, evidence = {}) => checks.push({ id, status, reason, evidence })
const finish = (code) => {
  const status = checks.some((c) => c.status === 'fail') ? 'fail' : checks.length && checks.every((c) => c.status === 'pass') ? 'pass' : 'unavailable'
  writeFileSync(join(outDir, 'report.json'), JSON.stringify({ status, checks }, null, 2))
  for (const c of checks) console.log(`${c.status === 'pass' ? '✔' : c.status === 'fail' ? '✖' : '-'} ${c.id}${c.status === 'pass' ? '' : ` — ${c.reason}`}`)
  if (code === 2) console.log(checks.find((c) => c.status === 'unavailable')?.reason ?? 'smoke unavailable')
  process.exit(code)
}

if (!existsSync(join(dist, pagesArg.split(',')[0]))) {
  const b = spawnSync('bash', ['cindyplugin/build.sh'], { stdio: 'inherit', env: process.env })
  if (b.status !== 0 || !existsSync(join(dist, pagesArg.split(',')[0]))) { check('pw-build-artifact', 'unavailable', `plugin build did not produce ${pagesArg.split(',')[0]} (exit ${b.status})`); finish(2) }
}

let chromium
try {
  const mod = process.env.ARGUS_PLAYWRIGHT_MODULE
  ;({ chromium } = mod ? await import(pathToFileURL(mod).href) : await import('playwright'))
} catch (err) { check('pw-runtime', 'unavailable', `playwright not importable: ${err.message}`); finish(2) }

const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.woff2': 'font/woff2', '.wasm': 'application/wasm' }
const server = createServer((req, res) => {
  const path = decodeURIComponent(new URL(req.url, 'http://x').pathname)
  const file = resolve(dist, '.' + path)
  const rel = relative(dist, file)
  if (rel.startsWith('..') || isAbsolute(rel) || !existsSync(file) || !statSync(file).isFile()) { res.writeHead(404); res.end('not found'); return }
  res.writeHead(200, { 'content-type': TYPES[extname(file)] ?? 'application/octet-stream' })
  res.end(readFileSync(file))
})
await new Promise((r) => server.listen(0, '127.0.0.1', r))
const base = `http://127.0.0.1:${server.address().port}`

let browser
try {
  browser = await chromium.launch({ headless: true, args: ['--disable-gpu', '--disable-dev-shm-usage'] })
} catch (err) { server.close(); check('pw-browser-launch', 'unavailable', `chromium failed to launch: ${String(err.message).split('\n')[0]}`); finish(2) }

for (const page of pagesArg.split(',').filter(Boolean)) {
  const tag = page.replace(/\.html$/, '')
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } })
  const p = await ctx.newPage()
  const pageErrors = []
  const consoleErrors = []
  const failedRequests = []
  p.on('pageerror', (e) => pageErrors.push(String(e.message).slice(0, 300)))
  p.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 300)) })
  p.on('requestfailed', (r) => failedRequests.push(`${r.url().replace(base, '')} ${r.failure()?.errorText ?? ''}`.slice(0, 200)))
  p.on('response', (r) => { if (r.url().startsWith(base) && r.status() >= 400) failedRequests.push(`${r.url().replace(base, '')} HTTP ${r.status()}`) })
  try {
    const resp = await p.goto(`${base}/${page}`, { waitUntil: 'load', timeout: 30_000 })
    check(`pw-${tag}-loads`, resp?.ok() ? 'pass' : 'fail', resp?.ok() ? 'ok' : `assert: HTTP ${resp?.status()}`, { url: `/${page}` })
    await p.waitForTimeout(4000)
    const dom = await p.evaluate(() => ({ title: document.title, rootChildren: document.querySelector('#root')?.childElementCount ?? null, bodyText: (document.body?.innerText ?? '').trim().length, elements: document.querySelectorAll('body *').length }))
    const rendered = (dom.rootChildren ?? 0) > 0 || dom.elements > 3
    check(`pw-${tag}-renders`, rendered ? 'pass' : 'fail', rendered ? 'ok' : 'assert: page rendered no visible DOM', dom)
    check(`pw-${tag}-no-uncaught-errors`, pageErrors.length ? 'fail' : 'pass', pageErrors.length ? `assert: ${pageErrors.length} uncaught page error(s): ${pageErrors[0]}` : 'ok', { pageErrors: pageErrors.slice(0, 5), consoleErrors: consoleErrors.slice(0, 5) })
    // 只看静态资源（带扩展名的同源文件）；/wake、/app-context 这类无扩展名路径是 Cindy 宿主接口，脱离宿主时 404 属预期
    const local = failedRequests.filter((r) => !/^https?:/.test(r) && /^\/[^?\s]*\.[a-z0-9]{1,6}(\?|\s|$)/i.test(r))
    check(`pw-${tag}-assets-load`, local.length ? 'fail' : 'pass', local.length ? `assert: ${local.length} same-origin asset request(s) failed: ${local[0]}` : 'ok', { failedRequests: failedRequests.slice(0, 10) })
    await p.screenshot({ path: join(outDir, `${tag}.png`) }).catch(() => {})
  } catch (err) {
    check(`pw-${tag}-loads`, 'fail', `assert: navigation failed: ${String(err.message).split('\n')[0]}`)
  }
  await ctx.close()
}
await browser.close()
server.close()
finish(checks.some((c) => c.status === 'fail') ? 1 : 0)
