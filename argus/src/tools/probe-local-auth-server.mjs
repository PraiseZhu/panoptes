// 接口探针用的本地登录态：在被测服务进程里，经被测仓自己导出的测试接缝 setCindyJwksLoader
// （server/lib/assetAuth.ts）把 Cindy JWKS 换成探针现生成的一次性公钥，再按原样启动 server/index.ts。
// 探针用对应私钥签 RS256 的 Cindy Connection JWT（iss/aud/exp/sub 都走服务端真实校验），
// 于是 projects / canvas / user-state / assets 的登录态读写全在本机内存服务上完成，不碰任何生产账号。
// 公钥经环境变量 ARGUS_LOCAL_JWKS 传入（只有公钥）；私钥只在探针进程内存里。
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const root = process.cwd()
const auth = await import(pathToFileURL(join(root, 'server/lib/assetAuth.ts')).href)
if (typeof auth.setCindyJwksLoader !== 'function') {
  console.error('[argus] server/lib/assetAuth.ts has no setCindyJwksLoader; local sign-in unavailable')
  process.exit(3)
}
const jwks = JSON.parse(process.env.ARGUS_LOCAL_JWKS ?? 'null')
auth.setCindyJwksLoader(async () => jwks)
await import(pathToFileURL(join(root, 'server/index.ts')).href)
