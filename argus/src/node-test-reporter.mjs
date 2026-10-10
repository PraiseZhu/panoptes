import { relative } from 'node:path'

// 只输出执行身份与状态，不序列化测试异常、stack、stdout 或跳过理由。
export default async function* reporter(source) {
  for await (const { type, data } of source) {
    if (type === 'test:pass' || type === 'test:fail') {
      yield JSON.stringify({ kind: 'case', name: data.name,
        file: data.file ? relative(process.cwd(), data.file) : null,
        type: data.details?.type,
        status: data.todo ? 'todo' : data.skip ? 'skipped' : type === 'test:pass' ? 'passed' : 'failed',
      }) + '\n'
    } else if (type === 'test:summary') {
      const c = data.counts
      yield JSON.stringify({ kind: 'summary', file: data.file ? relative(process.cwd(), data.file) : null,
        counts: { tests: c.tests, passed: c.passed, failed: c.failed, skipped: c.skipped, todo: c.todo, cancelled: c.cancelled },
      }) + '\n'
    }
  }
}
