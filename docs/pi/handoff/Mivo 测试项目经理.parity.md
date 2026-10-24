> 历史记录（写于 10-24 Pi 线并入 panoptes 之前）：文中的 `/workspace/pi-fallback/...` 是旧位置——代码现在在 `/workspace/panoptes/pi/`，状态在 `/workspace/mivo-qa/pi/`，对照见 `pi/README.md`「三类存放位置」。

# 机制对齐表 · Mivo 测试项目经理（第二阶段，2026-10-23 14:05 北京时间）

范围：Grok 线机制以 fleet-standards/Mivo 测试项目经理.md、daily-runbook.md 和 pm-daily/2026-10-2{1,2}.md 的真实产出为准。Pi 线代码在 `/workspace/pi-fallback/bin/{pm,pmnudge,p2}.py`，本阶段改动前的备份是 `*.bak-20261023-13xx-parity`。
状态标记：**一致**；**已修**（本阶段或第一阶段改过，已对齐）；**无法对齐**（写明原因和需要什么）。
证据：dry-run 输出在 `/tmp/pmdry/`（第一阶段）、`/tmp/pmdry2/2026-10-22.md`（本阶段，读入了补回的 p2-rulings），回放脚本是 `/tmp/nudge_replay.py`。

## A1. 10:30 日检（Grok：PM routine 10:30 → Pi：pm.py，cron `35 * * * * run.sh pm @10`）
| 机制 | Grok 线 | Pi 线（证据） | 状态 |
|---|---|---|---|
| 触发 | routine 每天 10:30 | cron 10:35，用 run.sh 按北京小时门控 | 一致。晚 5 分钟是为了错开；pm-daily 的截止时间是 11:30，不受影响 |
| 门控 | 你加的开头检查：fleet-mode=pi 就结束 | 产出写文件不受门控；只有 fleet-mode=pi 时才写正式位置 pm-daily（pm.py:224-231）；外发要 pi 模式且 outbound-enabled 存在（lib.py:92） | 已修（本阶段） |
| 数据日、区间 | 北京自然日，gh 按 UTC 区间查 | lib.utc_window，并按 mergedAt 精确再筛一遍 | 一致 |
| G1 | 首份报告在合并后 2h 内 / 合并数 | pm.py:77-99，首份报告包括 history 旧版 | **口径差 1 个**：10-21 Grok 22/27，Pi 23/27（#1161）。Pi 的算法符合 runbook，Grok 那天用的是当前版本。要 Praise 定按哪种算（拍板 1） |
| G2a | 新报 P0/P1 在 24h 内有修复 PR 或不可复现说明 | pm.py:105-125，级别取报告各版本加日报原文；认领 = 正文含「QA: #N」的修复 PR，或 praise-pr/notes 里的说明 | 已修。10-21 两边都是 3/3 |
| G2b P2 分母 | 只算 p2-rulings 判「修」的 | pm.py:32-45 解析裁决：「产品代码不修」算不修，「已修」算修。10-22 数据日读出：判「修」1 个（#1124），判「不修」1 个（#434） | 已修（本阶段，适配你补回的格式）。#1124 同时是 P1，按 P1 计，不重复算 |
| G3 | 9:00 前发出，并且和报告对得上 | pm.py:162-188，取 Grok 实发记录（report/sent/<日>/slack-sent.json）和 slack/sent.json 中最早一条；核对 PR 数、PR 号、SHADOW 标记；有两条发送记录就报疑似重复 | 一致（10-21 两边都是 1/1，08:31）。Grok 另外人工核对了级别，Pi 没有逐个核对级别：无法对齐，原因见 §B |
| G4 | 0 误合，看 audit.log 和 PAUSED | pm.py:189-196，数 bot 合并次数，核对巡检是否覆盖每个合并 PR | 一致（10-21 两边都是 0） |
| G5 | 最终提交的 review-pr 是 success / 合并数 | pm.py:91-98，用 check-runs 取最新一次 review-pr | 一致（10-21 两边都是 25/27，缺 #1150、#1161） |
| 每 bot 一行、遗漏清单 | pm-daily 第 6 节、催办表 | pm.py 第 6 节、第 8 节 | 一致（格式比 Grok 精简） |
| 数据拿不到 | 记「未核实」并写原因 | 「未核实」一节 | 一致 |
| 产出位置 | fleet-standards/pm-daily/<数据日>.md | pi 模式下写同一位置（如果已有 Grok 写的核对就不覆盖，只写 out/pm）；任何模式都写 out/pm/<数据日>.md 和 .json | 已修（本阶段） |
| 幂等 | 每天一个文件，重跑会覆盖 | 重跑覆盖，但保留文件里已有的「白天催办」节 | 一致 |
| 交接仓验收（5 项）、修复 PR 复测验收、前一天催办复查 | 有（pm-daily 第 8、9、12 节） | bin/pm_extra.py 的 handoff()、retest()、prev_review()，由 pm.py 调用，输出 §11–13（10-23 14:50 补） | 已修，见 B 节 |
| 群复盘（MivoCanvas QA） | Grok 群 | actions.json `pm-retro:<日>`，闸门关 | **无法对齐**：Pi 线没有 Grok 群，要 Praise 定渠道（拍板 2） |
| 给 Chief 一句进展 | SendToAgent | actions.json `pm-cos:<日>` | **无法对齐**：同上，没有 SendToAgent |
| 判断性内容（独立抽查代码、卡在谁、选项写法） | 模型加人判断 | 零模型，只给数字和清单 | **无法对齐**：是有意的设计（零模型、可复现）。如果要 100% 一致，就得给 pm.py 加一次 pi-auto review 调用，要 Praise 定是否花这个成本（拍板 4） |
| 重试 | routine 失败就手动重跑 | run.sh：flock，内存不足 3G 就跳过；gh 失败记入「未核实」；不重试 | 一致（两边都不自动重试） |

## A2. 白天催办（Grok：10/12/14/16/18/20 点的 routine → Pi：pmnudge.py，cron `5 * * * * run.sh pmnudge @10,12,14,16,18,20`）
| 机制 | Grok 线 | Pi 线 | 状态 |
|---|---|---|---|
| 时间 | 整点，实际有 :05–:09 的漂移 | :05 | 一致 |
| 范围 | PraiseZhu 名下、正文含「QA: #N」的 open PR；不含 keel/lifeline；不含判「不修」的 P2 | pmnudge.py:17-30、45-53（「不修」按「包含」匹配，本阶段修了，能读出 #434、#1216） | 已修 |
| 规则 a | draft 超过 2h，且除 review-pr 外有红 | :66，draft 时长从最后一次转 draft 起算（timeline），查不到就从创建起算 | 一致。回放 10-22 18:05：#1165 17:51 才转回 draft，不催，和 Grok 相同 |
| 规则 b | 除 review-pr 外全绿（无 pending）仍是 draft | :67 | 一致。回放 18:05 催 #1207，和 Grok 相同 |
| ready、review-pr 红 | 不催 | 不催 | 一致 |
| 产出 | 追加到 pm-daily/<当天>.md「白天催办」 | pi 模式下追加到同一个文件（:94-96）；同时写 out/pm/<当天>.md | 已修（本阶段） |
| 外发 | SendToAgent(priority:true) 发给 Praise PR | actions.json，key 是 `nudge:<日>:<PR>:red|green`，同一 PR 同一类当天只记一次 | **无法对齐**：没有 SendToAgent，渠道待定（拍板 2）。另外 Grok 每轮都会重复催，Pi 当天去重，是有意的差异 |
| 20 点汇总给 Chief | SendToAgent(priority:false) | out/pm/<当天>.chief.md 加 action `pm-chief:<日>` | **无法对齐**：同上 |
| 无问题时 | 不打扰任何人 | 只写一行「本轮无催办」 | 一致 |

## A3. P2 裁决（Grok：PM 和 Praise PR 在会话里逐条核对 → Pi：p2.py）
| 机制 | Grok 线 | Pi 线 | 状态 |
|---|---|---|---|
| 触发 | 有新 P2 就人工或会话来做 | cron 12:20、18:20，每次最多调 6 次模型（p2.py:8-9） | **无法对齐**：一边事件驱动，一边定时，Pi 只能定时 |
| 判定 | PM 和 Praise PR 对照代码和报告证据 | 模型（judge 档）在只读代码快照上判，只出提议 | **无法对齐**：缺 Praise PR 会签。要 Praise 定 Pi 线是否允许模型提议直接入表（拍板 3）。目前提议需要人用 `p2.py record` 写入 |
| 记录格式 | 日期、报告 PR、问题、裁决、理由（证据写在理由里） | `record` 追加的行多三列（证据、会签、规则状态）；读表时按 PR 号匹配，兼容 Grok 的写法 | 已修（本阶段：p2.py:31-36 改成按 PR 号判断是否已裁决，否则「AI 测试 P2 两条」这种行会被重复送去问模型） |
| 规则未确认提示 | 写在节标题里 | 提议和 record 都带 PENDING 提示 | 一致 |

## B. 还没对齐的汇总（10-23 14:55 更新）
**第 1 条已补上**：新文件 `bin/pm_extra.py`，pm.py 调用它，输出写在 §11–13。改前备份 `pm.py.bak-*-extra`。
- **§11 交接仓验收**（路由读 handoff-routes.json）：
  1. P0/P1 的 issue 是否开在对的仓、有没有重复；P2 列出来供人工核对是否该开。
  2. 次日 feedback.md 和当天台账在不在。
  3. 数据日和次日的 receipts.md 在不在。
  4. 开着超过 72h 的 issue 有没有逾期提醒：在当天 15:17 扫描之前只标「等待」，过了还没有提醒才报缺。
  5. 两仓 7 个规则文件逐行对比：账号和仓名先替换掉，剩下只差人名这类短片段的算一致。
- **§12 修复 PR 验收**：数据日合并的、正文含「QA: #N」的 PR，看报告里有没有原用例复测和定向回归；看台账 retest 有没有回写；看轮次字段在不在；第三轮的有没有进早报「需要你做」。
- **§13 前一天催办复查**：前一天 Pi 的 <日>.json 遗漏清单逐条复查。G1、G3、G5 这类已经发生的事实标「已发生」。P2 未裁决按 p2-rulings 重新判断。如果前一天只有 Grok 格式的文件，就列出催办表涉及的 PR，交人工复核。
- **同时修了**：日报发出时改过级的问题，按日报的级别计，和 Grok 线一致。比如 #1124 报告里是 P1、日报里是 P2，就按 P2 算，并记入「未核实」。

**10-22 dry-run 结果**（`PM_OUT_DIR=/tmp/pmdry3 python3 bin/pm.py 2026-10-22`，只读）：
- G1 25/25，G2a 5/7，G2b 5/8，G3 0/1（漏了 #434；sent.json 被 11:50 那次覆盖），G4 零误合，G5 25/25。
- 交接仓：P0/P1 issue 2/2 都开对了（#1156→aj0928-handoff#6，#1196→handover-mivo#3）。两仓的 feedback 和 receipts 都在。aj0928-handoff#2 10-23 09:13 到 72h，等今天 15:17 扫描提醒。两仓规则文件一致。
- 修复 PR：13 个，原用例复测 0/13，回写 0/13（retest 全是 pending），第三轮 #966 没进「需要你做」。
- 前一天复查：10-21 的 8 个 P2 报告 PR 仍未裁决（#1089 #1128 #1132 #1136 #1140 #1143 #1145 #1146）。
- 和 Grok 在 10-21 的结论方向一致：复测规则没落地、回写 pending。

**还没对齐的**：
2. G3 逐个核对 PR 级别，以及日检里的判断性内容：Pi 线是零模型，要完全一致就得加模型调用，等 Praise 定。
3. 外发三项（群复盘、给 Chief、催 Praise PR）：渠道等 Praise 定。
4. P2 裁决：Pi 线没有 Praise PR 会签。
5. G1 口径：#1161 差 1 个，等 Praise 定。
6. 新加的限制：P2「有修改建议才开交接 issue」只能列出来人工判断，因为报告里没有「建议」字段。§13 要等 Pi 自己连续跑两天才有前一天的 json，在那之前读 Grok 格式的文件，只列不判。

## C. 一键切换
- **Pi 线**：cron 每轮都跑，产出都写在 out/。正式位置 pm-daily 只在 `/workspace/fleet-mode`=pi 时才写（本阶段新加）。所以切线只需要改这一个文件。
- **Grok 线**：PM 日检和白天催办两个 routine 的开头要先读 `/workspace/fleet-mode`，值是 pi 就立刻结束（不调模型、不发消息、不写 pm-daily），值是 grok 才干活。你说已经给 mivo、praise-pr-2 两个 routine 加了这个检查。如果 PM 的 10:30 日检和白天催办是另外的 routine，请在它们开头加上同样这段（我没改任何 routine）：
  > 第一步：读 /workspace/fleet-mode。内容（去掉空白）是 `pi` 就立刻结束，不调模型、不发消息、不写文件；只有内容是 `grok` 才继续往下做。
- **切到 Pi**：`echo pi > /workspace/fleet-mode`（或者跑 `switch.sh pi`）。下一个 :05/:35 起 Pi 写 pm-daily；Grok routine 读到 pi 自己结束。
- **切回 Grok**：`echo grok > /workspace/fleet-mode`。Pi 只写 out/。同一天如果 Grok 已经写过日检，Pi 不覆盖。
- **注意**：现在 fleet-mode 已经是 pi，cron 没停，所以从今天 14:05 那轮起，pmnudge 会往 `fleet-standards/pm-daily/2026-10-23.md` 追加「白天催办」节；明早 10:35 pm.py 会写 `pm-daily/2026-10-23.md`。只写文件，不外发。如果不想现在就生效，把 fleet-mode 改回 grok 就行。

## D. 需要你或 Praise 做的事
1. （你）确认 PM 日检和白天催办两个 Grok routine 的名字。如果不是 mivo、praise-pr-2，请按上面那段加门控。routine 保持暂停，等 Praise 说恢复。
2. （Praise）G1 用首份报告还是当前版本算。
3. （Praise）催办、群复盘、给 Chief 的渠道选哪个（推荐 A+C）。
4. （Praise）Pi 线的 P2 模型提议能不能不经 Praise PR 会签直接入表；P2 新规本身也要确认。
5. （Praise）日检要不要在 Pi 线加一次模型调用，补上判断性内容（成本大约每天 1 次 review 档）。
6. （Jasper）README 覆盖表里 pmnudge 的产出位置要更新。
