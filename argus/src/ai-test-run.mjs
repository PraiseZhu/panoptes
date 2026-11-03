#!/usr/bin/env node
// AI 补测执行器：写入补测文件 → 在当前代码上跑 → 植入错误再跑一次 → 还原源码、删掉补测文件。零依赖，由 runner 复制进测试目录执行。
// 用法：node ai-test-run.mjs <spec.json>（工作目录是被测源码）
// 退出码：0 通过且植入错误后能发现问题 / 1 在当前代码上失败 / 2 没法跑（文件冲突等） / 4 测试无效（植入错误对不上或植入后仍通过）
import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync, readdirSync, rmdirSync } from 'node:fs'
import { dirname, join } from 'node:path'

const OUTPUT_TAIL = 60

// runFileStrip：测试命令在子目录里跑时（例如 Cindy 的 apps/desktop），传给它的路径要去掉这段前缀
function runTest(cmd, file, strip = '') {
  const arg = strip && file.startsWith(strip) ? file.slice(strip.length) : file
  const argv = cmd.map((a) => a.replaceAll('{file}', arg))
  const r = spawnSync(argv[0], argv.slice(1), { cwd: process.cwd(), env: process.env, encoding: 'utf8', timeout: 8 * 60_000 })
  return { code: r.status ?? 1, out: `${r.stdout ?? ''}${r.stderr ?? ''}` }
}

const countOf = (text, needle) => text.split(needle).length - 1

function removeCreated(file, createdDirs) {
  rmSync(file, { force: true })
  for (const d of createdDirs.reverse()) {
    try { if (readdirSync(d).length === 0) rmdirSync(d) } catch { /* 不是本次建的或已删 */ }
  }
}

function main() {
  const spec = JSON.parse(readFileSync(process.argv[2], 'utf8'))
  const file = join(process.cwd(), spec.filePath)
  if (existsSync(file)) { console.log(`补测文件和已有文件重名：${spec.filePath}`); process.exit(2) }
  const createdDirs = []
  for (let d = dirname(file); !existsSync(d); d = dirname(d)) createdDirs.unshift(d)
  mkdirSync(dirname(file), { recursive: true })
  writeFileSync(file, spec.content)
  try {
    // 先核对植入错误对得上源码，再跑测试：对不上的补测（例如把已删组件的属性套到活组件上）不能报「当前代码上失败」（#1433）。
    // spec.legacyOrder（回滚开关 qa-misattrib-off，runner 写进 spec）= 旧顺序：先跑测试，失败就 exit 1
    const target = join(process.cwd(), spec.mutation.file)
    const checkMutation = () => {
      let text
      try { text = readFileSync(target, 'utf8') } catch { console.log(`植入错误的源文件不存在：${spec.mutation.file}`); return null }
      if (countOf(text, spec.mutation.find) !== 1) { console.log('植入错误对不上源码（要替换的片段不是恰好出现一次），这个补测无法证明有效，已丢弃'); return null }
      return text
    }
    let original = spec.legacyOrder ? undefined : checkMutation()
    if (original === null) { process.exitCode = 4; return }
    const first = runTest(spec.runCmd, spec.filePath, spec.runFileStrip)
    if (first.code !== 0) {
      console.log(first.out.split('\n').slice(-OUTPUT_TAIL).join('\n'))
      console.log(`✖ ${spec.purposeZh}`)
      process.exitCode = 1
      return
    }
    original ??= checkMutation()
    if (original === null) { process.exitCode = 4; return }
    let mutated
    try {
      writeFileSync(target, original.replace(spec.mutation.find, spec.mutation.replace))
      mutated = runTest(spec.runCmd, spec.filePath, spec.runFileStrip)
    } finally {
      writeFileSync(target, original)
    }
    if (mutated.code === 0) { console.log('植入错误后测试仍然通过，说明它没测到东西，已丢弃'); process.exitCode = 4; return }
    console.log(`✔ ${spec.purposeZh}（植入错误后能发现问题）`)
  } finally {
    removeCreated(file, createdDirs)
  }
}

main()
