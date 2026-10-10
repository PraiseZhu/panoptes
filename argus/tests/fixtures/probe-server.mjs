// 探针测试用的假服务：端口取自 PORT；FAKE_MODE=ok | wrong | never-ready；把自己的 pid 写进 FAKE_PID_FILE
import { createServer } from 'node:http'
import { writeFileSync } from 'node:fs'

if (process.env.FAKE_PID_FILE) writeFileSync(process.env.FAKE_PID_FILE, String(process.pid))
const mode = process.env.FAKE_MODE ?? 'ok'
createServer((req, res) => {
  if (mode === 'never-ready') { res.writeHead(503); return res.end() }
  if (req.url === '/healthz') { res.writeHead(200, { 'content-type': 'application/json' }); return res.end(JSON.stringify({ ok: true })) }
  if (req.url === '/items') { res.writeHead(200, { 'content-type': 'application/json' }); return res.end(JSON.stringify({ items: mode === 'wrong' ? [] : [1] })) }
  if (req.url === '/secret') { res.writeHead(mode === 'wrong' ? 200 : 401); return res.end('no') }
  res.writeHead(404); res.end()
}).listen(Number(process.env.PORT), '127.0.0.1')
