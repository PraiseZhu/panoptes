// 生成 10-10 日报样例：post 内容 + 纯文本版。
// 级别 / 作者 / 测试佐证从 reports/merged/PR-N.json + author-map.json 读取；问题与"下一步"的一句话由人工按
// findings[].title / evidence.recommendation 改写（NOTES），不编造。已私信名单只取 feishu-sent.json 当日成功记录。
import { writeFileSync, readFileSync } from 'node:fs'
const ROOT = process.env.QA_HOME || '/workspace/mivo-qa'
const R = 'https://github.com/xindong/mivo-canvas-plugin/pull/'
const DAY = { label: '10 月 10 日', title: 'Mivo QA 日报 · 10月10日' }
const MERGED = [902, 893, 898, 884, 892, 894, 897]
const OPEN = [888, 878, 731]

const amap = JSON.parse(readFileSync(`${ROOT}/author-map.json`, 'utf8'))
const authorOf = login => amap.mivo_authors.find(a => a.github_login === login)
// MODE=group：@ 真实作者 open_id（发群用）；MODE=test（默认）：所有 @ 都指向 Praise，用于私聊演示，绝不 @ 他人
const MODE = process.env.MODE === 'group' ? 'group' : 'test'
const PRAISE_OPEN_ID = authorOf('PraiseZhu').feishu_open_id
const isBot = login => /\[bot\]$/.test(login) || authorOf(login)?.account_type === 'Bot'
const nameOf = login => (amap.mivo_authors.find(a => a.github_login === login)?.name) || login.replace(/\[bot\]$/, '')
const prs = Object.fromEntries(MERGED.map(n => [n, JSON.parse(readFileSync(`${ROOT}/reports/merged/PR-${n}.json`, 'utf8')).prs[0]]))
const health = MERGED.map(n => JSON.parse(readFileSync(`${ROOT}/reports/merged/PR-${n}.json`, 'utf8')).system_health)

// 人工改写：short = 群里显示的短标题；P0/P1 用 problem + next；P2/P3 用 line
const NOTES = {
  902: { short: '为 seat1 硬超时保留备用审查窗口',
    problem: '超时清理只检查进程命令行，可能漏掉工作树里还在跑的旧审查进程，备用审查会和它并行。',
    next: '检测旧进程时补上按工作目录（cwd）的检查并把子进程算进去（更稳的是查进程组或 cgroup），确认不了旧进程已退出就不启动备用，然后提修复 PR；如果觉得不用改，在 PR 下留言说明就行。' },
  893: { short: '火焰展示每日开发日志并回落随包说明',
    problem: '日志加载过一次后，再点火焰不会刷新远端日志，插件运行期间新合入的日志看不到。',
    next: '给火焰入口加一条强制刷新路径（如 force 参数或单独的 refresh action，保留并发去重，失败时回落随包数据），然后提修复 PR；如果觉得不用改，在 PR 下留言说明就行。' },
  898: { short: '测试包改用先行版 id，新增旧测试库搬家脚本', mention: true,
    line: '1 条小建议：搬家脚本的绑定检查记录分不出源目录还是目标目录，可顺手给每条加上来源标签。' },
  884: { short: '用现有 Task API 接入受控派工试验', mention: true,
    line: '1 条小建议：验证任务报“已通过”但证据核对不上时，上级任务没有被拦下，可顺手补上拦截，或把文档里的说法改窄。' },
  892: { short: '更新日志栏目滚动、轮播最多 7 页、注意图标', mention: true,
    line: '1 条小建议：窗口比较矮时，栏目内容可能被切掉看不到，可顺手加个降级布局或允许滚动。' },
  894: { short: 'sharp 0.35.4 → 0.35.5',
    line: '只是仓库整体覆盖率差一点（见下），本 PR 不用处理。' },
  897: { short: '更新日志补扫 10-09',
    line: '纯文档，按规则不跑测试，审查无问题，无需处理。' },
}
const SECTION = {
  P0: 'P0 阻断｜请优先修复',
  P1: 'P1 严重｜请作者确认并修复',
  P2: 'P2 一般｜有空顺手改，不阻断',
  P3: 'P3 提示｜无需处理',
}

const pr = n => ({ pr: n })
// 需要作者处理的条目才 @：P0/P1 全部；P2 仅 NOTES[n].mention（有具体建议要改）；bot 作者、查不到 open_id 的退回写名字
const who = n => {
  const p = prs[n], sev = p.severity
  const wants = sev === 'P0' || sev === 'P1' || (sev === 'P2' && NOTES[n].mention)
  const oid = authorOf(p.author)?.feishu_open_id
  return wants && !isBot(p.author) && oid ? { at: p.author, oid } : nameOf(p.author)
}
const mentions = []
const b = t => ({ b: t })
const by = sev => MERGED.filter(n => prs[n].severity === sev)
const cnt = Object.fromEntries(['P0', 'P1', 'P2', 'P3'].map(s => [s, by(s).length]))

// 已私信通知
const sentLog = JSON.parse(readFileSync(`${ROOT}/feishu-sent.json`, 'utf8'))
const dmPrs = [...new Set(Object.entries(sentLog).filter(([, v]) => v?.message_id)
  .map(([k]) => Number(k.match(/^xindong\/mivo-canvas-plugin#(\d+):/)?.[1])).filter(n => MERGED.includes(n)))]
  .sort((a, b) => MERGED.indexOf(a) - MERGED.indexOf(b))

const parts = []
if (cnt.P0 === 0) parts.push('没有 P0')
for (const s of ['P0', 'P1', 'P2', 'P3']) if (cnt[s]) parts.push(`${s} ${cnt[s]} 个${{ P0: '需要立即修', P1: '请作者确认', P2: '可顺手改', P3: '无需处理' }[s]}`)
const healthy = health.every(h => h.status === 'ok' && h.complete)
const lines = [[`Mivo 播报 ${DAY.label}（北京时间）：合了 ${MERGED.length} 个 PR，${parts.join('，')}。测试系统${healthy ? '运行正常' : '有异常，见系统状态'} 😌`]]

for (const s of ['P0', 'P1']) {
  if (!cnt[s]) continue
  lines.push([], [b(`${SECTION[s]}（${cnt[s]} 个）`)])
  for (const n of by(s)) {
    const p = prs[n], note = NOTES[n]
    lines.push([pr(n), ` ${note.short} — `, who(n)])
    lines.push([`· 问题：${note.problem}`])
    lines.push([p.test_backed ? '· 自动测试已复现这个问题。' : '· 审查结论，测试未复现（自动测试全部通过，这是看代码发现的）。'])
    lines.push([`· 下一步：${note.next}`])
  }
}
lines.push([], [b('已私信通知')])
if (dmPrs.length) for (const n of dmPrs) lines.push([pr(n), ` ${NOTES[n].short} → ${nameOf(prs[n].author)}，已飞书私信`])
else lines.push(['今天没有需要私信的。'])

for (const s of ['P2', 'P3']) {
  if (!cnt[s]) continue
  lines.push([], [b(`${SECTION[s]}（${cnt[s]} 个）`)])
  for (const n of by(s)) {
    lines.push([pr(n), ` ${NOTES[n].short} — `, who(n)])
    lines.push([`· ${s} ${NOTES[n].line}`])
  }
}
lines.push([], [b('测试覆盖情况')],
  ['· 整个仓库大约 80% 的函数有单元测试覆盖，比 80% 的门槛差一点。这是老代码的整体情况，不是昨天哪个 PR 拉低的，不用处理。'],
  ['· 有 3 个 PR 新写的代码测试补得不够（门槛是新代码 90% 以上被测试跑到）：', pr(884), ' 79%、', pr(898), ' 83%、', pr(892), ' 88%，有空可以补几个用例。'],
  ['· 有个性能测试（shadowRoundTrip）在 ', pr(892), ' 和 ', pr(898), ' 上第一次跑超时，重跑就过了，看起来是测试本身不稳定（未与合并前版本对比）😏'],
  [], [b('系统状态')],
  [healthy ? `${MERGED.length} 个都测完了，测试系统本身运行正常。` : '测试系统本身出了点状况，详情已私下同步。'],
  [], [b('未合并（顺带）')],
  [pr(888), ' 全部通过；', pr(878), ' 有 3 条小建议，另有一个性能测试没过，还没确认是不是它导致的；', pr(731), ' 通过（有一个性能测试没过，但它改之前就不过，不是它导致的）。'],
  [], ['分级说明：P0 合进去后现在的代码还在出错，要先修；P1 这个 PR 带来的严重问题，请作者看看；P2 测试补得不够、测试不稳定或一般建议，有空再改；P3 只是提醒或没问题。'],
)

const seg = s => {
  if (typeof s === 'string') return [{ tag: 'text', text: s }]
  if (s.pr) return [{ tag: 'a', text: `#${s.pr}`, href: R + s.pr }]
  if (s.b) return [{ tag: 'text', text: s.b, style: ['bold'] }]
  const name = nameOf(s.at)
  mentions.push({ login: s.at, name, group_target: s.oid })
  if (MODE === 'group') return [{ tag: 'at', user_id: s.oid, user_name: name }]
  return [{ tag: 'at', user_id: PRAISE_OPEN_ID, user_name: name }, { tag: 'text', text: `（演示：群里这里会 @${name}）` }]
}
const post = { zh_cn: { title: DAY.title, content: lines.map(segs => segs.length === 0 ? [{ tag: 'text', text: ' ' }] : segs.flatMap(seg)) } }
// 护栏：test 模式下所有 at 必须指向 Praise
const atIds = post.zh_cn.content.flat().filter(x => x.tag === 'at').map(x => x.user_id)
if (MODE === 'test' && atIds.some(id => id !== PRAISE_OPEN_ID)) throw new Error('test 模式出现指向他人的 @，拒绝生成')
const plain = [DAY.title, '', ...lines.map(segs => segs.map(s => typeof s === 'string' ? s : s.pr ? `#${s.pr}` : s.b ? s.b : `@${nameOf(s.at)}`).join(''))].join('\n')
const linked = [...new Set(lines.flat().filter(s => s?.pr).map(s => s.pr))]
const OUT = MODE === 'group' ? 'content-2026-10-10.post.json' : 'content-2026-10-10.test-dm.post.json'
writeFileSync(OUT, JSON.stringify(post, null, 2) + '\n')
writeFileSync('mentions-2026-10-10.json', JSON.stringify(mentions.map(m => ({ login: m.login, name: m.name })), null, 2) + '\n')
writeFileSync('sample-2026-10-10.txt', plain + '\n\n链接：\n' + linked.map(n => `#${n} ${R}${n}`).join('\n') + '\n')
console.log(plain)
