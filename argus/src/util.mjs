import { appendFileSync, mkdirSync, renameSync, statSync, existsSync } from 'node:fs'
import { dirname } from 'node:path'
import { createHash } from 'node:crypto'
import { CONTROL_LOG } from './paths.mjs'

// logging

const LOG_ROTATE_BYTES = 5 * 1024 * 1024

export function log(level, msg, fields = {}) {
  const line = JSON.stringify({ t: new Date().toISOString(), level, msg, ...fields })
  try {
    mkdirSync(dirname(CONTROL_LOG), { recursive: true })
    if (existsSync(CONTROL_LOG) && statSync(CONTROL_LOG).size > LOG_ROTATE_BYTES) {
      renameSync(CONTROL_LOG, `${CONTROL_LOG}.1`)
    }
    appendFileSync(CONTROL_LOG, redact(line) + '\n')
  } catch {
    // 日志写不进去时退回 stderr，launchd 会收集
  }
  if (process.env.ARGUS_LOG_STDERR === '1' || level === 'error') process.stderr.write(redact(line) + '\n')
}

// redaction：对外（issue / 飞书 / 模型）文本统一过一遍

const REDACTIONS = [
  [/gh[pousr]_[A-Za-z0-9]{20,}/g, '<token>'],
  [/github_pat_[A-Za-z0-9_]{20,}/g, '<token>'],
  [/sk-[A-Za-z0-9_-]{16,}/g, '<key>'],
  [/(authorization|x-api-key|cookie|set-cookie)\s*[:=]\s*\S+/gi, '$1: <redacted>'],
  [/Bearer\s+[A-Za-z0-9._-]{12,}/g, 'Bearer <redacted>'],
  [/(app_secret|api_key|apikey|password|secret)(["']?\s*[:=]\s*["']?)[^\s"',}]+/gi, '$1$2<redacted>'],
  [/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, '<email>'],
  [/(file:\/\/)?\/Users\/[^/\s]+\/Library\/[^\s)'"]*/g, '<本机临时路径>'],
  [/(file:\/\/)?~\/Library\/[^\s)'"]*/g, '<本机临时路径>'],
  [/\/Users\/[^/\s]+/g, '~'],
  [/ou_[a-z0-9]{20,}/g, '<open_id>'],
]

export function redact(text) {
  let out = String(text ?? '')
  for (const [re, rep] of REDACTIONS) out = out.replace(re, rep)
  return out
}

export function sha1(text) {
  return createHash('sha1').update(String(text)).digest('hex')
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// 上海时区的日期字符串 YYYY-MM-DD
export function toShanghaiDay(date = new Date()) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(date)
}

// 「9月24日 09:00」
export function toShanghaiClock(date = new Date()) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(date).map((p) => [p.type, p.value]))
  return `${parts.month}月${parts.day}日 ${parts.hour}:${parts.minute}`
}

// 给人看的文本去技术痕迹：命令、文件路径、文件名、提交号、行内代码都换成中性说法（Jev sanitize_title 0.90）
const PLAIN_RULES = [
  [/`[^`]*`/g, (m) => (/[/.()=]|^\s*`(npm|npx|node|git|pnpm|yarn|uv|python3?|pytest|bash|sh)\b/.test(m) ? '相关代码' : m.slice(1, -1))],
  [/\b(npm|npx|pnpm|yarn|node|git|uv|uvx|python3?|pytest|bash|sh|make|gh)\s+(run\s+)?[\w:@./-]+(\s+--?[\w=./-]+)*/g, '相关命令'],
  // 路径：三段以上，或以常见代码目录开头（避免误伤「UI/UX」「A/B 测试」这类说法）
  [/(~|\.{1,2})?\/?\b[\w.-]+(\/[\w.@-]+){2,}\/?/g, '相关文件'],
  [/\b(src|scripts|tests?|docs|config|server|shared|cindyplugin|lib|packages|apps|\.github)\/[\w./@-]+/g, '相关文件'],
  [/\b[\w-]+\.(ts|tsx|js|jsx|mjs|cjs|py|json|md|sh|ya?ml|css|scss|html|toml|lock|sql)\b/g, '相关文件'],
  [/\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{6,40}\b/g, ''],
  [/\b(sha|SHA|commit)\s*(?=[，。；、,;\s]|$)/g, ''],
  [/(相关(命令|文件|代码))(\s*(、|和|与|及|,|，)?\s*\1)+/g, '$1'],
]

export function toPlainZh(text) {
  let out = String(text ?? '')
  for (const [re, rep] of PLAIN_RULES) out = out.replace(re, rep)
  return out.replace(/\s{2,}/g, ' ').replace(/\s+([，。；、])/g, '$1').trim()
}

// 去掉 `type(scope):` 前缀，只保留中文描述
export function toReadableTitle(title) {
  return String(title ?? '').replace(/^\s*[a-z]+(\([^)]*\))?!?:\s*/i, '').replace(/\s*\(#\d+\)\s*$/, '').trim()
}

export function truncate(text, max) {
  const s = String(text ?? '')
  return s.length <= max ? s : `${s.slice(0, max)}\n…（已截断，原长 ${s.length} 字符）`
}

export function tail(text, max) {
  const s = String(text ?? '')
  return s.length <= max ? s : `…（前面省略）\n${s.slice(s.length - max)}`
}
