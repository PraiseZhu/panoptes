// 沙箱保真度：产品用例用「每个祖先目录都必须由 root 或本人拥有、且不带组/其他写位」证明控制器不会被改写
// （mivo scripts/ops/review-capacity-check/launcher.native.test.mjs > 版本探测成功但版本不合格时必须明确拒绝…）。
// bwrap 建 user namespace 后宿主 root 的 inode 没有映射，在沙箱里显示成 65534；沙箱根如果是 ro-bind 宿主 /，
// 这条链在 QA 沙箱里就永远不可信 —— 产品用例于是「在基线版本上也失败」，被记成 pre-existing P3 噪声，
// 谁来修都看不到（inf1-2a243a907acdcdb626a4，2026-10-30 的 5 次运行）。
// 这里按产品用例的走法把链走一遍并断言它可信：沙箱要让被测代码看到和 CI 同形的祖先链，而不是 65534。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { chmodSync, mkdirSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, join } from 'node:path'
import { spawnSync } from 'node:child_process'

// 与 linux-nightly.test.mjs 一样先给 ARGUS_ROOT：否则真实 ~/.local/state/argus 会被挂进沙箱
process.env.ARGUS_ROOT ??= mkdtempSync(join(tmpdir(), 'argus-sbxtrust-root-'))
const { bwrapArgs, sandboxMode } = await import('../src/runner.mjs')

// 逐级 stat 从 / 到目标目录，一行一个祖先（path \t uid \t mode）；产品用例的 trusted_path 就是这么走的
const WALK = `
target="$1"; cur=/
printf 'euid\\t%s\\t-\\n' "$(id -u)"
owner=$(stat -c '%u' -- "$cur") || exit 3
mode=$(stat -c '%a' -- "$cur") || exit 3
printf '%s\\t%s\\t%s\\n' "$cur" "$owner" "$mode"
for part in $(printf '%s' "$target" | tr '/' ' '); do
  cur="\${cur%/}/\$part"
  owner=$(stat -c '%u' -- "$cur") || { printf '%s\\tstat-failed\\t-\\n' "$cur"; exit 4; }
  mode=$(stat -c '%a' -- "$cur") || { printf '%s\\tstat-failed\\t-\\n' "$cur"; exit 4; }
  printf '%s\\t%s\\t%s\\n' "$cur" "$owner" "$mode"
done
`

function walkAncestors(runDir, target) {
  const res = spawnSync('/usr/bin/bwrap', [...bwrapArgs(runDir), '--', 'sh', '-c', WALK, 'sh', target], { encoding: 'utf8' })
  assert.equal(res.status, 0, `sandbox walk failed: ${res.stderr || res.stdout}`)
  return res.stdout.trim().split('\n').map((line) => {
    const [path, uid, mode] = line.split('\t')
    return { path, uid: Number(uid), mode }
  })
}

const untrusted = (uid, mode, euid) => !(uid === 0 || uid === euid) || (parseInt(mode, 8) & 0o022) !== 0

test('bwrap 沙箱里被测代码看到的祖先链与 CI 同形：属主是 root 或当前用户，且不带组/其他写位',
  { skip: process.platform !== 'linux' || sandboxMode() !== 'bwrap' }, () => {
    const runDir = mkdtempSync(join(tmpdir(), 'argus-sbxtrust-run-'))
    const home = join(runDir, 'home')
    // HOME 与夹具照 runner 的做法：HOME 在 run 目录里 0755，夹具是 mkdtemp 式的 0700（产品用例就在 HOME 下建夹具）
    const fixture = join(home, '.mivo-capacity-launcher-fixture')
    try {
      mkdirSync(home, { recursive: true })
      chmodSync(home, 0o755)
      mkdirSync(fixture)
      chmodSync(fixture, 0o700)
      const rows = walkAncestors(runDir, fixture)
      const euid = rows[0].uid
      const chain = rows.slice(1)
      assert.equal(chain[0].path, '/')
      assert.equal(chain.at(-1).path, fixture)
      const bad = chain.filter((r) => untrusted(r.uid, r.mode, euid))
      assert.deepEqual(bad, [],
        `沙箱里这些祖先不可信（属主既不是 root 也不是沙箱用户 ${euid}，或带组/其他写位）：` +
        `${bad.map((r) => `${r.path} uid=${r.uid} mode=${r.mode}`).join('; ')}。` +
        '产品用例的 trusted_path 用同一条规则，于是它先拒绝启动，在 PR 版本和基线版本上一起失败；' +
        '沙箱根不能把宿主 root 的 inode 留成未映射的 65534')
    } finally {
      rmSync(runDir, { recursive: true, force: true })
      // bwrapArgs 会顺手建 run 的短临时目录（<TMPDIR>/argus-<run 名>），一起清掉，别在 /tmp 留东西
      rmSync(join(tmpdir(), `argus-${basename(runDir)}`), { recursive: true, force: true })
    }
  })
