// Shared preflight and outcome contract; never imports or evaluates generated/product modules.
import { lstatSync, readFileSync, realpathSync } from 'node:fs'
import { dirname, extname, isAbsolute, relative, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import { stripTypeScriptTypes } from 'node:module'

// 「测试无效」：补测在当前代码上失败，但失败原因是测试自己没备好前提（缺测试数据 / 文件、导入了不存在的模块或导出），
// 还没走到断言。这类失败说明不了被测代码有问题 → 按 discarded 处理（invalid: true，显示「测试无效」），不产生 finding。
// 只要输出里有断言失败（AssertionError / expected … to …）就不算无效：例如 expect(...).toThrow('X') 却收到 ENOENT，仍是 fails-on-current。
export const AI_TEST_INVALID_ZH = '测试无效'
// missing-module 也包括测试自己的 vi.mock 工厂少导出了被测代码要用的函数（vitest: No "x" export is defined on the "…" mock，#915 10-15）
const INVALID_SIGNATURES = [
  { kind: 'missing-file', re: /\bENOENT\b|no such file or directory/i },
  { kind: 'missing-module', re: /Cannot find module ['"]|Cannot find package ['"]|Failed to resolve import|Failed to load url|Does the file exist\?|does not provide an export named|No "[^"]+" export is defined on the "[^"]+" mock/ },
]
const ASSERTION_FAILURE = /AssertionError|\bexpected\b[^\n]*\bto (be|equal|throw|have|contain|match|deep|strictly|include|satisfy)/i
// missing-module 还包括：测试从某个模块按名导入了该模块没有的导出（ESM 里拿到 undefined），并在测试文件里直接调用它
// → TypeError: x is not a function，且报错位置（第一帧）就在这个补测文件里（#1008 10-16：renderAutomationBody 从错的模块导入）。
// 只看测试代码里 import { … } 的名字；被测代码里抛出的 "is not a function" 不算（可能是真问题）。
const NOT_A_FUNCTION = /TypeError: ([A-Za-z_$][\w$]*) is not a function/
export function importedNames(content) {
  const names = new Set()
  for (const m of String(content ?? '').matchAll(/import\s+(?!type\b)(?:[A-Za-z_$][\w$]*\s*,\s*)?\{([^}]*)\}\s*from\s*['"][^'"]+['"]/g)) {
    for (const part of m[1].split(',')) {
      const p = part.trim().replace(/^type\s+/, '')
      if (!p || part.trim().startsWith('type ')) continue
      names.add(p.split(/\s+as\s+/).pop().trim())
    }
  }
  return names
}
function missingImportCall(text, { content, filePath } = {}) {
  const m = text.match(NOT_A_FUNCTION)
  if (!m || !content || !importedNames(content).has(m[1])) return null
  const frame = text.slice(m.index).split('\n').slice(1).find((l) => /^\s*❯\s/.test(l))
  const base = filePath ? String(filePath).split('/').pop() : null
  if (!frame || !(base ? frame.includes(`${base}:`) : /\.test\.[cm]?[jt]sx?:\d+/.test(frame))) return null
  return { kind: 'missing-module', line: `${m[0]} (imported by the test; not exported by that module) ${frame.trim()}`.slice(0, 240) }
}
// fixture-gap：测试把对象传给它导入的函数 NAME(X, …)，被测代码读 X.K.P 时报 Cannot read properties of undefined (reading 'P')，
// 而测试代码里根本没出现 K、被测代码也从不给 K 赋值 → undefined 只能来自测试传入的对象缺字段（#1013 10-18：transport(c) 缺 c.budgets）。
// 需要被测源码（test.source）对照；对不上任何一条都仍算 fails-on-current（产品自己算出 undefined 是真问题）。
function fixtureGap(text, { content, source } = {}) {
  const p = text.match(/Cannot read properties of undefined \(reading '(\w+)'\)/)?.[1]
  if (!p || !content || !source) return null
  const names = importedNames(content)
  for (const [, x, k] of source.matchAll(new RegExp(`\\b(\\w+)\\.(\\w+)\\.${p}\\b`, 'g'))) {
    if (new RegExp(`\\b${k}\\b`).test(content) || new RegExp(`\\b${k}\\s*(=[^=]|:)`).test(source)) continue
    const fn = [...source.matchAll(new RegExp(`function\\s+(\\w+)\\s*\\(\\s*${x}\\b`, 'g'))].find((m) => names.has(m[1]))
    if (fn) return { kind: 'fixture-gap', line: `Cannot read properties of undefined (reading '${p}'): ${fn[1]}(${x}) reads ${x}.${k}.${p}, the test never passes ${k}` }
  }
  return null
}
// fixture-gap（缺方法）：测试把自己拼的对象 X 传给它导入的函数 NAME(X, …)，被测代码调 X.M() 报 TypeError: X.M is not a function，
// 而测试代码里根本没出现 M、被测代码也从不给 X.M 赋值 → 只能是测试传入的对象少了这个方法（#1121 10-20：collectGlobal(client) 调 client.now()，测试的 client 没给 now）。
// 需要被测源码（test.source）对照；对不上任何一条都仍算 fails-on-current。
const NOT_A_METHOD = /TypeError: ([A-Za-z_][\w]*)\.([A-Za-z_][\w]*) is not a function/
function fixtureMissingMethod(text, { content, source } = {}) {
  const m = text.match(NOT_A_METHOD)
  if (!m || !content || !source) return null
  const [, x, k] = m
  if (new RegExp(`\\b${k}\\b`).test(content)) return null
  if (new RegExp(`\\b${x}\\.${k}\\s*=[^=]`).test(source)) return null
  const names = importedNames(content)
  const fn = [...source.matchAll(new RegExp(`function\\s+(\\w+)\\s*\\(\\s*${x}\\b`, 'g'))].find((f) => names.has(f[1]))
  if (!fn) return null
  return { kind: 'fixture-gap', line: `${m[0]}: ${fn[1]}(${x}) calls ${x}.${k}(), the test's ${x} never defines ${k}`.slice(0, 240) }
}
// missing-module 还包括：测试文件里直接调用了一个既没导入、也没在测试里声明的名字 → ReferenceError: x is not defined，
// 且报错位置（第一帧）就在这个补测文件里（#978 10-19：用了 isMarkupTextCoveredByHigherSection 却只导入了另一个函数）。
// 报错在被测代码里、或测试里有这个名字的导入 / 声明，都不算（可能是真问题）。
const NOT_DEFINED = /ReferenceError: ([A-Za-z_$][\w$]*) is not defined/
function declaredInTest(content, name) {
  const n = name.replace(/\$/g, '\\$')
  if (importedNames(content).has(name)) return true
  if (new RegExp(`import\\s+(?:\\*\\s+as\\s+)?${n}\\b`).test(content)) return true
  if (new RegExp(`\\b(?:const|let|var|function\\*?|class)\\s+${n}\\b`).test(content)) return true
  return new RegExp(`\\b(?:const|let|var)\\s*[{\\[][^=;]*\\b${n}\\b[^=;]*[}\\]]\\s*=`).test(content)
}
function undeclaredInTest(text, { content, filePath } = {}) {
  const m = text.match(NOT_DEFINED)
  if (!m || !content || declaredInTest(content, m[1])) return null
  const frame = text.slice(m.index).split('\n').slice(1).find((l) => /^\s*❯\s/.test(l))
  const base = filePath ? String(filePath).split('/').pop() : null
  if (!frame || !(base ? frame.includes(`${base}:`) : /\.test\.[cm]?[jt]sx?:\d+/.test(frame))) return null
  return { kind: 'missing-module', line: `${m[0]} (used by the test; never imported or declared in it) ${frame.trim()}`.slice(0, 240) }
}
export function classifyAiTestFailure(tail, test = {}) {
  const text = String(tail ?? '').replace(/\x1b\[[0-9;]*m/g, '').replace(/\b(ReferenceError|TypeError|SyntaxError) \[Error\]:/g, '$1:')
  if (!text) return null
  const gap = fixtureGap(text, test)
  if (gap) return gap
  if (ASSERTION_FAILURE.test(text)) return null
  for (const { kind, re } of INVALID_SIGNATURES) {
    const line = text.split('\n').find((l) => re.test(l))
    if (line) return { kind, line: line.trim().slice(0, 240) }
  }
  const known = missingImportCall(text, test) ?? undeclaredInTest(text, test) ?? fixtureMissingMethod(text, test)
  if (known) return known
  if (/\b(?:ERR_INVALID_ARG_TYPE|ERR_INVALID_ARG_VALUE|ERR_MISSING_ARGS)\b/.test(text)) return { kind: 'wrong-arguments', line: 'generated test supplied invalid arguments' }
  // Only the first frame identifies the origin; a product exception also has a test caller.
  const error = text.match(/(?:ReferenceError|TypeError|SyntaxError):[^\n]*/)
  const missing = error?.[0].match(NOT_DEFINED)
  if (missing && declaredInTest(test.content, missing[1])) return null
  const frame = error && text.slice(error.index + error[0].length).split('\n').find((l) => /^\s*(?:❯|at)\s/.test(l))
  const path = test.filePath?.replaceAll('\\', '/')
  const name = error?.[0].match(/(?:ReferenceError|TypeError):\s*([\w$]+)/)?.[1]
  if (frame && path && name && test.content?.includes(name) && (frame.includes(`${path}:`) || frame.includes(`/${path.split('/').pop()}:`))) {
    return { kind: 'initialization', line: error[0].slice(0, 240) }
  }
  return null
}

export const VALIDATION_PREFIX = 'ARGUS_AI_VALIDATION '
export function readValidationResult(tail) {
  const line = String(tail ?? '').split('\n').findLast((l) => l.startsWith(VALIDATION_PREFIX))
  if (!line) return null
  try {
    const value = JSON.parse(line.slice(VALIDATION_PREFIX.length))
    return ['invalid', 'valid', 'blocked'].includes(value?.status) ? value : null
  } catch { return null }
}

const invalid = (kind, reason) => ({ status: 'invalid', kind, reason })
export const isAssertionFailure = (text) => ASSERTION_FAILURE.test(text) || /\bERR_ASSERTION\b/.test(text)

// Check lexical containment before touching disk, then reject every symlink component.
// This also protects paths that do not exist yet (the generated test).
export function isolatedPath(root, path) {
  if (typeof path !== 'string' || !path || path.includes('\0') || path.includes('\\') || isAbsolute(path)) return null
  const parts = path.split('/')
  if (parts.some((p) => !p || p === '..' || p === '.' || /^(?:\.git|\.env.*|\.codex|\.claude|secrets?|credentials?|auth\.json|.*\.(?:pem|key|token\.json))$/i.test(p))) return null
  const base = resolve(root)
  const dest = resolve(base, path)
  if (relative(base, dest).startsWith('..')) return null
  try {
    if (realpathSync(base) !== base) return null
    let current = base
    for (const part of parts) {
      current = resolve(current, part)
      try { if (lstatSync(current).isSymbolicLink()) return null }
      catch (err) { if (err.code !== 'ENOENT') return null }
    }
  } catch { return null }
  return dest
}

// Parse only, without executing code or loading a target project's configuration.
export function validateSyntax(content, filePath) {
  let code = content
  const ext = extname(filePath)
  if (/^\.[cm]?ts$/.test(ext)) {
    try { code = stripTypeScriptTypes(content, { mode: 'transform' }) }
    catch { return invalid('parse', 'generated TypeScript does not parse') }
  }
  if (/^\.[jt]sx$/.test(ext)) return { status: 'blocked', kind: 'parse', reason: 'JSX syntax requires the isolated project parser' }
  const parsed = spawnSync(process.execPath, ['--check', `--input-type=${/\.[cm]?(?:js|ts)$/.test(ext) && ext !== '.cjs' && ext !== '.cts' ? 'module' : 'commonjs'}`], {
    input: code, encoding: 'utf8', timeout: 5000, env: {},
  })
  if (parsed.error || parsed.signal) return { status: 'blocked', kind: 'parse', reason: 'syntax checker unavailable or timed out' }
  return parsed.status === 0 ? { status: 'valid' } : invalid('parse', 'generated test does not parse')
}

export function validateSideEffects(content) {
  // Static preflight is an accident detector. Execution still requires the runner sandbox.
  if (/\b(?:fetch|WebSocket|XMLHttpRequest|eval|Function)\s*\(|\bimport\s*\(\s*[^'"\s]|\bprocess\s*(?:\?\.)?\s*(?:\.\s*env|\[\s*['"]env['"])/.test(content)
    || /(?:from\s*|import\s*\(|require\s*\(|import\s*)['"](?:node:)?(?:https?|https?2|net|tls|dns|dgram|child_process|cluster|worker_threads|vm)['"]/.test(content)
    || /['"](?:https?:\/\/|file:\/\/|\/(?:Users|home|workspace|tmp|var|etc)\/|~\/)/.test(content)) {
    return invalid('side-effect', 'generated code requests network, credentials, external paths or process execution')
  }
  return { status: 'valid' }
}

function imports(content) {
  return [...content.matchAll(/(?:import\s+(?:([^;]*?)\s+from\s*)?|export\s+[^;]*?\s+from\s*|(?:import|require)\s*\()\s*['"]([^'"]+)['"]/g)]
    .map((m) => ({ clause: m[1] ?? '', path: m[2] }))
}

function localImport(root, fromFile, path) {
  const rel = relative(resolve(root), resolve(root, dirname(fromFile), path))
  const candidates = [rel, ...['.ts', '.tsx', '.mjs', '.js', '/index.ts', '/index.js'].map((ext) => rel + ext)]
  for (const candidate of candidates) {
    const safe = isolatedPath(root, candidate)
    if (!safe) continue
    try { if (lstatSync(safe).isFile()) return { path: candidate, content: readFileSync(safe, 'utf8') } } catch { /* next candidate */ }
  }
  return null
}

export function validateAiTest(spec, { root, isolated = false, allowedTargets = null, deferMutation = false } = {}) {
  if (!isolated || !root) return { status: 'blocked', kind: 'isolation', reason: 'an isolated checkout is required before preflight' }
  const file = isolatedPath(root, spec?.filePath)
  const target = isolatedPath(root, spec?.mutation?.file)
  if (!file || !target) return invalid('target', 'test or mutation path leaves the allowed isolated tree')
  if (allowedTargets && !allowedTargets.includes(spec.mutation.file)) return invalid('target', 'mutation is outside the supplied target set')
  if (typeof spec.content !== 'string' || !spec.content.trim()) return invalid('parse', 'generated test is empty')
  if (spec.content.length > 20_000) return invalid('parse', 'generated test exceeds the content limit')
  if (!Array.isArray(spec.runCmd) || !spec.runCmd.length || spec.runCmd.some((a) => typeof a !== 'string') || !spec.runCmd[0]) {
    return { status: 'blocked', kind: 'execution', reason: 'isolated test command must be a nonempty argument array' }
  }
  const sideEffects = validateSideEffects(spec.content)
  if (sideEffects.status !== 'valid') return sideEffects
  const syntax = validateSyntax(spec.content, spec.filePath)
  if (syntax.status !== 'valid') return syntax
  let source
  try { source = readFileSync(target, 'utf8') } catch { return invalid('target', 'mutation target does not exist') }
  const references = imports(spec.content)
  const visited = new Set()
  const visit = (content, path, depth = 0) => {
    if (depth > 12 || visited.size > 80) return
    for (const ref of imports(content)) {
      if (!ref.path.startsWith('.')) continue
      const local = localImport(root, path, ref.path)
      if (local && !visited.has(local.path)) { visited.add(local.path); visit(local.content, local.path, depth + 1) }
    }
  }
  for (const ref of references) {
    if (/^type\b/.test(ref.clause.trim())) continue
    if (!ref.path.startsWith('.')) continue // package exports are validated by the isolated test framework
    const local = localImport(root, spec.filePath, ref.path)
    if (!local) return invalid('missing-module', 'a generated test import cannot be resolved inside the checkout')
    const names = ref.clause.match(/\{([^}]*)\}/)?.[1]?.split(',').map((n) => n.trim().split(/\s+as\s+/)[0]).filter((n) => n && !n.startsWith('type ')) ?? []
    // Re-exports/CJS need the framework resolver; never execute them during preflight.
    if (!/\bexport\s+(?:\*|\{)|\bmodule\.exports\b|\bexports\./.test(local.content)) {
      for (const name of names) {
        if (!new RegExp(`\\bexport\\s+(?:async\\s+)?(?:function\\*?|class|const|let|var)\\s+${name.replace(/[$]/g, '\\$')}\\b`).test(local.content)) return invalid('missing-module', 'a named import is not exported by its target module')
      }
    }
  }
  visit(spec.content, spec.filePath)
  if (!visited.has(spec.mutation.file)) return invalid('target', 'test does not reference the mutation target through local imports')
  const mutation = spec.mutation
  if (mutation.status === 'not_applicable') {
    if (typeof mutation.reason !== 'string' || !mutation.reason.trim()) return invalid('mutation', 'not_applicable mutation requires a reason')
    return { status: 'valid', mutation: { status: 'not_applicable', reason: mutation.reason.slice(0, 240) }, source }
  }
  if (typeof mutation.find !== 'string' || !mutation.find || typeof mutation.replace !== 'string' || mutation.find === mutation.replace) {
    return invalid('mutation', 'mutation must replace exactly one source fragment with a different value')
  }
  const replacement = validateSideEffects(mutation.replace)
  if (replacement.status !== 'valid') return invalid('mutation', 'mutation introduces an unsafe side effect')
  if (source.split(mutation.find).length !== 2) {
    return deferMutation ? { status: 'valid', mutation: { status: 'pending' }, source } : invalid('mutation', 'mutation must replace exactly one source fragment with a different value')
  }
  const mutatedSyntax = validateSyntax(source.replace(mutation.find, mutation.replace), mutation.file)
  if (mutatedSyntax.status === 'blocked') return { status: 'valid', mutation: { status: 'not_applicable', reason: mutatedSyntax.reason }, source }
  if (mutatedSyntax.status !== 'valid') return { ...mutatedSyntax, kind: 'mutation', reason: 'mutation cannot be parsed safely' }
  return { status: 'valid', mutation: { status: 'pending' }, source }
}

