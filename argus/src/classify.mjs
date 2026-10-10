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
const NEVER_A = ['**/package.json', '**/package-lock.json', '**/pyproject.toml', '**/uv.lock', '.github/**', 'scripts/**', '**/*.test.*', 'tests/**', '**/AGENTS.md', '**/CLAUDE.md']

export function classifyChange(files, profile = {}) {
  const paths = files.map((f) => f.filename)
  if (paths.length === 0) return { riskClass: 'B', reasonZh: '拿不到改动文件列表，按常规处理' }
  const aGlobs = profile.aPaths ?? DEFAULT_A
  const cGlobs = profile.cPaths ?? []
  const hitsC = paths.filter((p) => matchAny(p, cGlobs))
  if (hitsC.length > 0) {
    return { riskClass: 'C', reasonZh: `改到了高风险区域（共 ${hitsC.length} 个文件，例如 ${hitsC[0]}）` }
  }
  const allA = paths.every((p) => matchAny(p, aGlobs) && !matchAny(p, NEVER_A))
  if (allA) return { riskClass: 'A', reasonZh: `只改了说明文档（${paths.length} 个文件）` }
  return { riskClass: 'B', reasonZh: `常规代码改动（${paths.length} 个文件）` }
}
