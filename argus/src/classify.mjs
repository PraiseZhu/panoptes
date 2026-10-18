// 改动分级：A 只改说明文档 / B 常规 / C 高风险。未知路径永远不进 A。

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

const DEFAULT_A = ['**/*.md', 'docs/**', 'LICENSE', '**/*.txt']
// 测试、依赖、CI、脚本、配置即使长得像文档也不能进 A
const NEVER_A = ['**/package.json', '**/package-lock.json', '**/pyproject.toml', '**/uv.lock', '.github/**', 'scripts/**', '**/*.test.*', 'tests/**']
// 给编码智能体看的说明文档：单独一类（change_kind agent-docs），和普通文档一样只做代码审查、不跑测试
const AGENT_DOCS = ['**/AGENTS.md', '**/CLAUDE.md']
// 测试文件、纯版本号数据、智能体说明文档即使落在高风险目录里，也不把 PR 推到 C
const NEVER_C = ['**/*.test.*', '**/__tests__/**', 'tests/**', '**/ghost.json', ...AGENT_DOCS]

// 命中 profile.cPaths 的改动文件（去掉测试文件和 ghost.json）
export const highRiskFiles = (paths, profile = {}) => paths.filter((p) => matchAny(p, profile.cPaths ?? []) && !matchAny(p, NEVER_C))

export function classifyChange(files, profile = {}) {
  const paths = files.map((f) => f.filename)
  if (paths.length === 0) return { riskClass: 'B', reasonZh: '拿不到改动文件列表，按常规处理' }
  const aGlobs = profile.aPaths ?? DEFAULT_A
  const hitsC = highRiskFiles(paths, profile)
  if (hitsC.length > 0) {
    return { riskClass: 'C', reasonZh: `改到了高风险区域（共 ${hitsC.length} 个文件，例如 ${hitsC[0]}）` }
  }
  const allA = paths.every((p) => matchAny(p, aGlobs) && !matchAny(p, NEVER_A))
  if (allA && paths.some((p) => matchAny(p, AGENT_DOCS))) return { riskClass: 'A', changeKind: 'agent-docs', reasonZh: `只改了智能体说明文档（AGENTS.md / CLAUDE.md 等，${paths.length} 个文件）` }
  if (allA) return { riskClass: 'A', reasonZh: `只改了说明文档（${paths.length} 个文件）` }
  return { riskClass: 'B', reasonZh: `常规代码改动（${paths.length} 个文件）` }
}
