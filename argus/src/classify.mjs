// 改动分级：A 只改说明文档 / B 常规 / C 高风险。未知路径永远不进 A。
import { createRequire } from 'node:module'

export function globToRegExp(glob) {
  let re = ''
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i]
    if (c === '*') {
      if (glob[i + 1] === '*') {
        re += '.*'
        i++
        if (glob[i + 1] === '/') i++
      } else {
        re += '[^/]*'
      }
    } else if (c === '?') {
      re += '[^/]'
    } else {
      re += c.replace(/[.+^${}()|[\]\\]/g, '\\$&')
    }
  }
  return new RegExp(`^${re}$`)
}

const matchAny = (path, globs) => globs.some((g) => globToRegExp(g).test(path))

// 测试文件（*.test.* / *.spec.* / *.node-test.* / __tests__/ / test_*.py / *_test.py）
// 以及测试辅助文件：文件名以 -fixture / _fixture / .fixture / Fixture 或 -harness / Harness 结尾（company-cli-test-fixture.mjs、
// design-ratchet-fixture.mjs、canvasHarness.ts）；fixtureLoader.ts 这类只是开头像的产品文件不算
export const TEST_HELPER_RE = /(?:[-_.]fixtures?|Fixtures?|[-_.]harness|Harness)\.[cm]?[jt]sx?$/
const TEST_PATH_RE = /\.(test|spec|node-test)\.[cm]?[jt]sx?$|(^|\/)__tests__\/|(^|\/)test_[^/]+\.py$|_test\.py$/
export const isTestPath = (path) => TEST_PATH_RE.test(String(path ?? '')) || TEST_HELPER_RE.test(String(path ?? ''))
// 只改了测试文件（至少一个，且全是测试文件；删掉的测试文件也算测试改动）
export const isTestsOnly = (files) => (files ?? []).length > 0 && files.every((f) => isTestPath(f.filename))

const DEFAULT_A = ['**/*.md', 'docs/**', 'LICENSE', '**/*.txt']
// 测试、依赖、CI、脚本、配置即使长得像文档也不能进 A
const NEVER_A = ['**/package.json', '**/package-lock.json', '**/pyproject.toml', '**/uv.lock', '.github/**', 'scripts/**', '**/*.test.*', 'tests/**']
// 给编码智能体看的说明文档：单独一类（change_kind agent-docs），和普通文档一样只做代码审查、不跑测试
const AGENT_DOCS = ['**/AGENTS.md', '**/CLAUDE.md']
// 测试文件、纯版本号数据、智能体说明文档即使落在高风险目录里，也不把 PR 推到 C
const NEVER_C = ['**/*.test.*', '**/__tests__/**', 'tests/**', '**/ghost.json', ...AGENT_DOCS]

// 只改注释：改动前后的完整文件（f.before / f.after，新增文件 before 为 ''、删除文件 after 为 ''）用 typescript 解析，
// 去掉注释后的 token 序列相同才算。拿不到全文、解析失败、不认识的语言、没装 typescript，一律按真实改动算
let ts = null
try { ts = createRequire(import.meta.url)('typescript') } catch {}
const TS_KIND = { js: 'JS', mjs: 'JS', cjs: 'JS', jsx: 'JSX', ts: 'TS', mts: 'TS', cts: 'TS', tsx: 'TSX' }
function codeTokens(text, kind) {
  const sf = ts.createSourceFile(`x.${kind.toLowerCase()}`, text, ts.ScriptTarget.Latest, true, ts.ScriptKind[kind])
  if (sf.parseDiagnostics?.length) return null
  const out = []
  // getChildren 会把 /** */ 也当成子节点，跳过；父节点也记进去，ASI 改变语法结构（如 `return` 后换行）时 token 相同也能区分
  const walk = (n) => { if (n.kind >= ts.SyntaxKind.FirstJSDocNode && n.kind <= ts.SyntaxKind.LastJSDocNode) return; const kids = n.getChildren(sf); if (!kids.length) out.push(`${n.kind}:${n.getText(sf)}`); else { out.push(`(${n.kind}`); kids.forEach(walk); out.push(')') } }
  walk(sf)
  return out.join('\n')
}
export function isCommentOnly(f) {
  const kind = TS_KIND[/\.([a-z]+)$/i.exec(f.filename ?? '')?.[1]?.toLowerCase()]
  if (!ts || !kind || typeof f.before !== 'string' || typeof f.after !== 'string') return false
  try {
    const a = codeTokens(f.before, kind), b = codeTokens(f.after, kind)
    return a !== null && a === b
  } catch { return false }
}

// 命中 profile.cPaths 的改动文件（去掉测试文件、ghost.json 和只改注释的文件）；files 是 [{ filename, patch }]
export const highRiskFiles = (files, profile = {}) => files.filter((f) => matchAny(f.filename, profile.cPaths ?? []) && !matchAny(f.filename, NEVER_C) && !isCommentOnly(f)).map((f) => f.filename)

// PR 交接文件（docs/pr/**）不参与分级：去掉后为空（只加了交接）按只改文档处理
export const isHandoffFile = (path) => /^docs\/pr\//.test(path ?? '')
export const withoutHandoff = (files) => files.filter((f) => !isHandoffFile(f.filename))

export function classifyChange(allFiles, profile = {}) {
  const files = withoutHandoff(allFiles)
  if (allFiles.length && !files.length) return { riskClass: 'A', reasonZh: `只改了 PR 交接文件（docs/pr/**，${allFiles.length} 个文件）` }
  const paths = files.map((f) => f.filename)
  if (paths.length === 0) return { riskClass: 'B', reasonZh: '拿不到改动文件列表，按常规处理' }
  const aGlobs = profile.aPaths ?? DEFAULT_A
  const hitsC = highRiskFiles(files, profile)
  if (hitsC.length > 0) {
    return { riskClass: 'C', reasonZh: `改到了高风险区域（共 ${hitsC.length} 个文件，例如 ${hitsC[0]}）` }
  }
  const allA = paths.every((p) => matchAny(p, aGlobs) && !matchAny(p, NEVER_A))
  if (allA && paths.some((p) => matchAny(p, AGENT_DOCS))) return { riskClass: 'A', changeKind: 'agent-docs', reasonZh: `只改了智能体说明文档（AGENTS.md / CLAUDE.md 等，${paths.length} 个文件）` }
  if (allA) return { riskClass: 'A', reasonZh: `只改了说明文档（${paths.length} 个文件）` }
  if (files.every((f) => isCommentOnly(f) || matchAny(f.filename, aGlobs))) return { riskClass: 'B', changeKind: 'comment-only', reasonZh: `只改了注释（${paths.length} 个文件）` }
  return { riskClass: 'B', reasonZh: `常规代码改动（${paths.length} 个文件）` }
}
