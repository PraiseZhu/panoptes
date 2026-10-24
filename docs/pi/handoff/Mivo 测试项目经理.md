# Pi 线迁移验收 · Mivo 测试项目经理（2026-10-23 13:15 北京时间）

只写文件。没开闸、没动 cron/systemd/Grok routine、没发消息、没改别的 bot 文件、没改仓库设置、没 push。
cron 行没改（`/etc/cron.d/pi-fallback` 原有：pm @10 :35、pmnudge @10,12,14,16,18,20 :05、p2 @12,18 :20）。

## 1. 职责 ↔ Pi 线脚本
| 职责（Mivo 测试项目经理.md） | 脚本 | 时间（北京） | 产出 | 状态 |
|---|---|---|---|---|
| 10:30 核对前一天 G1–G5、每 bot 一行、遗漏清单 | bin/pm.py（重写，零模型） | 10:35 | out/pm/<数据日>.md + .json；外发草稿 out/pm/<数据日>.actions.json | ✅ dry-run 过 |
| 群复盘 + 给 Chief 一句进展 | pm.py 写 actions（`pm-retro:<日>`、`pm-cos:<日>`） | 同上 | actions.json，闸门关 | ⛔ 渠道待定（§4） |
| 白天催办（QA 修复 PR） | bin/pmnudge.py（重写） | 10/12/14/16/18/20 :05 | 追加 out/pm/<当天>.md「## 白天催办 HH:MM」；out/pm/<当天>.nudges.actions.json；20 点 out/pm/<当天>.chief.md | ✅ 回放过 |
| P2 裁决 | bin/p2.py（模型提议不变 + 新增 `record` 子命令） | 12:20、18:20 | out/p2/<日>.md（提议）；会签后 `p2.py record` 写 fleet-standards/p2-rulings.md | ✅ record 只 dry 测过，没写真文件 |
| 交接仓验收、修复 PR 验收、前一天催办复查 | — | — | — | ❌ 没做（§5） |

## 2. 改动的文件（改前都留了 .bak）
- `/workspace/pi-fallback/bin/pm.py`（备份 pm.py.bak-20261023-1304-pm）
- `/workspace/pi-fallback/bin/pmnudge.py`（备份 pmnudge.py.bak-20261023-1304-pm）
- `/workspace/pi-fallback/bin/p2.py`（备份 p2.py.bak-20261023-1304-pm）
- `/workspace/pi-fallback/out/pm/2026-10-2{1,2}.{md,json}`：新 dry-run 结果（旧草稿留在 .md.bak-pre-pm-v2）
- 没动：lib.py、run.sh、cron、README.md、p2-rulings.md、fleet-standards/pm-daily/。

口径要点：
- **pm.py**：G1 按首份报告算（包括 reports/merged/history 旧版）。G2a 的级别取报告各版本加上日报原文 `report/sent/<日>/group-content.txt` 的并集，也就是按发出时的级别。认领指 24h 内有正文含「QA: #N」的修复 PR，或者 praise-pr/notes 里有不可复现说明。G2b 只算 P0/P1 加上 p2-rulings 里判「修」的 P2，并标了「Praise 本人未确认」。G3 取 Grok 实发记录 `report/sent/<日>/slack-sent.json` 和 `slack/sent.json` 里最早的一条，再核对合并 PR 数和 PR 号是否都写到、有没有 SHADOW 标记，同一天有两条发送记录就报疑似重复。G4 看 audit.log 数据日的 bot 合并次数、巡检有没有读到每个合并 PR，以及 PAUSED。G5 看 headRefOid 上最新一次 review-pr 的结论。数据拿不到的写进「未核实」。重跑时会保留文件里已有的「白天催办」节。
- **pmnudge.py**：只看 PraiseZhu 名下、正文含「QA: #N」的 open PR。跳过 keel/lifeline（看分支名或标题）。所关联的问题全是判「不修」的 P2（且不是 P0/P1）就跳过。ready 的不催。review-pr 不参与判断。draft 时长从最后一次 convert_to_draft 算起，查不到就从创建算起。幂等 key 是 `nudge:<日>:<PR>:red|green`，所以同一 PR 同一类当天只催一次。`--dry-run` 只打印。
- **p2.py**：cron 照旧只出提议，不写 p2-rulings。新增 `p2.py record <PR> <问题> <修|不修> <理由> <证据路径...> [--by 会签人]`：会检查证据路径是否存在，写入前留 .bak，追加的行带日期、报告 PR、问题、裁决、理由、证据、会签和「待 Praise 确认」，首次写入时会在表头加一行规则未确认的提示。`P2_DRY=1` 时只打印。

## 3. dry-run 和 Grok 线对比
命令：`PM_OUT_DIR=/tmp/pmdry python3 bin/pm.py 2026-10-21|2026-10-22`（只读 gh 和本地文件）。pmnudge 用 `/tmp/nudge_replay.py` 回放 10-22 18:05 和 20:09 的情形（gh 是假数据），另外对当前真实状态跑了一次 `pmnudge.py --dry-run`（13:08：QA 修复 PR 1 个，#1166 已 ready，不催）。

### 数据日 10-21（Grok：fleet-standards/pm-daily/2026-10-21.md）
| 目标 | Grok | Pi | 结论 |
|---|---|---|---|
| G1 | 22/27 | **23/27** | 差在 #1161：17:55 合并，history/PR-1161.v1.json 里 18:22 已有 event 报告（27 分钟）。当前版本是 20:01 的 manual 重跑，Grok 拿这一版算成 2h06m。runbook 规定手动重跑的旧版放进 history，所以按首份报告算 Pi 是对的。另外 #1147、#1146 两边都判超时，Pi 记 trigger=event，Grok 写的是排锁。**我认为 Pi 对，没改。** |
| G2a | 3/3 | 3/3 | 一致。#1144、#1145 在当前报告和台账里已改成 P3，所以改为从日报原文取 P1。#1144 的说明文件 mtime 被盒子恢复重置成 10-23 04:34，已记入「未核实」 |
| G2b | 8/12（含全部 P2） | 3/3（P0/P1 3/3，P2「修」0/0） | 规则不同：Grok 当天还没有 10-22 的 P2 新规。p2-rulings.md 是空的，所以 P2 分母是 0，8 个 P2 报告 PR 列为「未裁决」。**差异有意保留。** |
| G3 | 1/1，08:31 | 1/1，08:31 | 一致。PR 数 27=27 |
| G4 | 0 误合 | 0 误合 / 0 次 bot 合并，巡检读到 27/27 | 一致 |
| G5 | 25/27，缺 #1150、#1161 | 25/27，缺 #1150、#1161 | 一致 |

### 数据日 10-22（Grok 的 pm-daily/2026-10-22.md 只有白天催办和 #923/#1144/#1145 复核；10-22 的 G 表要等 10-23 的核对，盒子上还没有 2026-10-23 的 Grok 文件，没法逐项比）
Pi 结果：G1 25/25，G2a 5/8（#1124、#1156 超过 24h 没有修复 PR 或说明，#1196 还没到期），G2b 5/8，**G3 0/1**，G4 0/0，G5 25/25。
- G3 0/1 有两个原因。一是日报写「合了 24 个」，gh 是 25 个，漏了 #434（zhongxingtian-ai 23:38 北京合并）。二是 `slack/sent.json` 的 `mivo-daily-report:2026-10-22` 被 10-23 11:50 那次 Pi 重复发送覆盖了，真实首发是 08:37（report/sent/2026-10-22/slack-sent.json），Pi 取最早的一条，所以按时。旧版 pm.py 只读 sent.json，会误判为超时，现在已修。
- #1124、#1156、#1196 的作者不是 Praise，走交接仓。Pi 把「认领」只认作修复 PR 或说明，交接 issue 不算，需要 Praise 定（§6）。
- 和 Grok 当天的白天催办对比：10-22 18:05，Grok 催 #1207（全绿仍是 draft），#1165 不到 2h 不催。Pi 回放结果相同。14:05 那轮 Grok 催了 lifeline #1177–#1188，这是 16:08 范围规则出来之前的做法，按新规则 Pi 不催，属于有意的差异。

### 旧版 Pi 草稿和新版的区别（out/pm/*.bak-pre-pm-v2）
旧版 G2a 是 1/1，因为只读当前报告，漏了 #1144、#1145。旧版 G2b 用的是全量累计台账（22/25）。旧版 G3 只读 sent.json。旧版没有 bot 行、遗漏清单和 G4 巡检核对。这些都已修掉。

## 4. 催办渠道（Pi 线没有 Grok 群）· **待 Praise 定**
| 方案 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| A. 写进 Praise PR 的收件队列 | pmnudge 把催办写到 `/workspace/pi-fallback/out/praise-pr/inbox/<日>.jsonl`，Praise PR 的 Pi 作业（prretro/prfix）开跑时读取，并在自己的 .md 里回执 | 不外发，闸门关着也能用，零凭证；有幂等 key，好对账 | Praise PR 的 Pi 作业是定时跑的（09:45、14:20），催办最长会晚几小时；要 Praise PR 改脚本去读（不是我的文件） |
| B. 走任务书现有的 actions.json + outbound.py，发到 Slack #mivo-canvas-qa（C0CAH3W61N2）的线程 | 已经写好 actions（type=nudge）；开闸后由 outbound.py 发出 | 人能看到，Praise 也能看到；有现成的 bot token 和幂等台账 | 要 Praise 批开闸并接执行器；会把 bot 之间的催办发进人看的群，可能刷屏 |
| C. 只写文件，由 Jasper 每天 09:05 的 jasper.py 简报汇总 | 20 点 chief.md 和催办节被 Jasper 读进简报 | 最安全，不加新通道 | 不是实时，Praise PR 不会马上看到，等于降级成日报 |

**推荐 A**（白天催办）+ **C**（20 点给 Chief 的汇总）。B 留到开闸以后再说。三个都没接。

## 5. 没做完的
- 交接仓验收（两仓 issue、receipts.md、逾期、目录一致）、修复 PR 复测验收、前一天催办复查：pm.py 里还没有，dry-run 的数字只覆盖 G1–G5、bot 行和遗漏清单。
- G3 只核对了 PR 数、PR 号和 SHADOW 标记，没有逐个对 PR 的级别（日报发出后报告级别还会被改）。
- G4 只在影子期有效。真开自动合并后，「误合」要按 audit.log 里的合并行，结合报告 P0 和必需检查来判，还没写。
- pmnudge 对 10-21、10-22 只能用假数据回放，gh 拿不到历史上的 draft/CI 状态。
- README 覆盖表里 pmnudge 的产出还写着 `<日>.nudges.md`，现在改成追加到 `out/pm/<当天>.md`。README 是共享文件，我没改，请 Jasper 更新。旧的 2026-10-23.nudges.md 留着。

## 6. 开闸前要 Praise 拍板
1. P2 新规（只修判「修」的；PM 和 Praise PR 会签）还没确认。不确认的话 G2b 分母要改回全部 P2。
2. 催办渠道选 A、B、C 中哪个（推荐 A+C）。群复盘和给 Chief 的进展走哪个渠道。
3. Pi 线的 pm-daily 写在 out/pm/，还是切到 pi 模式后直接写 fleet-standards/pm-daily/（Grok 线的位置）。两条线不能同时写。
4. G1 按首份报告算（Pi）还是按当前版本算（Grok）：10-21 会差 1 个（#1161）。
5. 别人名下的 P0/P1 走交接仓 issue，算不算「认领」（影响 10-22 G2a 的 #1124、#1156）。
6. slack/sent.json 的 10-22 键被 11:50 那次重复发送覆盖了，要不要让日报 bot 恢复成 08:37 的记录（这不是我的文件，我没动）。
