# Pi 线迁移 · 独立验收（2026-10-23 13:55 北京时间，Jasper 验收）

只读检查 + dry-run/只写文件。没有外发、没开闸（outbound-enabled 不存在，仍是 outbound-enabled.off-1151）、没改 fleet-mode（仍是 pi）、没动 Grok routine、没 force-push、没删数据。唯一改动：README.md 文档修正（备份 README.md.bak-20261023-1350）。

## 总结论
- **能否切过去（只写文件的 Pi 模式）：能，有条件。** 17 个作业 dry-run 全部 rc=0；switch.sh 两个方向都能跑；重置恢复演练能装回 cron 和两个接管标记。条件：先修好下面的 F1（切回 grok 不会删接管标记）。
- **能否开闸（真实外发）：不能。** 开闸后 outbound.py 会马上给 QA #923 开修复 PR（F2，违反 #923 闸门）；另外 merger 身份门一直 STOP、催办/复盘渠道没定、若干规则等 Praise 拍板。
- 去重（11:50 事故复查）：dry-run 里 10-22 的日报、9 封私信、交接 issue 全部判为 done（ledger），**和 Grok 已发记录不会重复**；所有 actions.json 里没有 SHADOW / NOT SENT / `<!--` 草稿标记。

## 失败 / 风险项
| # | 项 | 证据 | 建议 |
|---|---|---|---|
| F1 | `switch.sh grok` **不删** takeover/cindy、takeover/boxhealth | dry-run 只打印改 fleet-mode，没有 rm 接管标记。切回 grok 后 Grok「Cindy 保活」会被 ensure 守卫 SKIP，Pi 的 cindy.py/boxhealth.py 也还会继续跑 → 双跑或没人管 | switch.sh grok 时 rm takeover/{cindy,boxhealth}；pi 时 touch（和 60-pi-line.sh 一致） |
| F2 | 开闸会给 #923 开 PR | out/praise-pr/2026-10-23.actions.json 仍有 `fixpr:923`（旧 prfix 08:59 产出），ledger 里 status=failed（11:50 git 身份错误），failed 会重试；outbound dry-run 显示 **would-do** gh.pr.create(draft) QA:#923 | 开闸前把 ledger 里 fixpr:923 标 skipped（理由：Praise 闸），或 outbound 加 #923 硬跳过 |
| F3 | 开闸后会发 1 条新 Slack 私信 | `boxhealth:2026-10-23:13` slack.dm 给朱赞（/node_modules 已在 13:17 改名为 /node_modules.bak-202610231317）。不是重复，但要 Praise 知道 | 开闸时确认要不要发 |
| F4 | Cindy 保活受内存守卫影响 | run.sh MemAvailable<3G 就跳过；13:45 那轮 cindy 因 2483MB 被跳过。内存紧时正是最需要保活的时候 | 给 cindy/boxhealth 豁免内存守卫（它们零模型） |
| F5 | 重作业撞 catch-up | 盒子已改 Asia/Shanghai，catch-up 在北京**偶数**小时 :07 起跑；p2 12:20、prfix 14:20 正好在 catch-up 开跑后 13 分钟（prfix 会自跳过，p2 不会）。cron 文件头注释仍写"奇数"小时 | 把 p2/prfix 挪到奇数小时；改 pi-fallback.cron 注释（未改，非文档范围） |
| F6 | catch-up 绕过外发闸 | /etc/cron.d/panoptes-catch-up 带 `ARGUS_ALLOW_ISSUES=1 ... --create-issues` → 有证据的 P0/P1 会直接开 GitHub issue，不经 Pi 闸门 | 只报告，Praise 定 |
| F7 | 重置后要有人跑 restore.sh | 重置清 /etc，cron 没了 Pi 线自己起不来；标记只在 fleet-mode=pi 时补建 | Praise 定由谁触发 |

## dry-run 运行记录（13:45–13:48，全部先查 MemAvailable≥3G）
| 作业 | 命令 | rc | 产出 |
|---|---|---|---|
| daily | daily.py 2026-10-22 --render-only --diff-sent | 0 | out/daily/2026-10-22.render.md/.diff（P1 7/P2 3/P3 14，hold #434，diff 112 行措辞） |
| handoff | handoff.py scan --render-only；plan 2026-10-22 --render-only --diff-sent | 0/0 | out/handoff/2026-10-23.render.md、plan-2026-10-22.render.md |
| ledger | ledger.py 2026-10-22（build-ledger --dry-run） | 0 | out/ledger/2026-10-22.diff.md |
| pm | PM_OUT_DIR=/tmp/acc/pm pm.py 2026-10-22 | 0 | /tmp/acc/pm/2026-10-22.md/.json |
| pmnudge | --dry-run | 0 | 只打印：本轮无催办（#1166 已 ready） |
| prretro | --dry-run | 0 | 只打印，ACTIONS [] |
| prfix | --list | 0 | 只列；#923 显示「Praise 闸：不得创建」 |
| merger | merger.py（只读 gh） | 0 | out/merger/2026-10-23.md/.actions.json/state.json |
| opsenv | opsenv.py（无 ops-repair-enabled，只查） | 0 | out/ops/env-2026-10-23.md/.alerts.json |
| opsreview | OPS_CAP=0 opsreview.py 2026-10-22（无模型） | 0 | 跑完已把 review-2026-10-22.md/.json、2026-10-22.actions.json 还原成原版 |
| boxcheck | boxcheck.py | 0 | out/box/2026-10-23.md |
| boxhealth | PI_BOXHEALTH_DRYRUN=1 | 0 | 只打印：清理候选 2 项/298M，僵尸 3 |
| cindy | cindy.py --dry-run | 0 | out/cindy/2026-10-23.dryrun.md（ensure 将做：无） |
| compare | compare.py | 0 | out/compare/2026-10-22.md |
| roster / xstyle | 直接跑 | 0/0 | 无接管标记 → skip |
| outbound | outbound.py --dry-run（闸关） | 0 | out/outbound/2026-10-23.dry-run.md；ledger.json md5 前后一致 |
| 未跑（要调模型，10:33 Praise 要求省用量） | p2、dailyretro、jasper、knowledge、credcheck | — | 用今天已有 cron 日志：均有 10-23 产出，p2 08:35 有 1 次 rc=130 |

switch.sh：`grok --dry-run` rc=0（只会改 fleet-mode，**不删接管标记**，见 F1）；`pi --dry-run` rc=0，列出标记 boxhealth cindy、闸仍关。fleet-mode 跑完仍是 pi。

outbound 去重核对：ledger 21 条。Grok 10-22 已发：slack-sent.json（08:37）→ `mivo-daily-report:2026-10-22` done；sent/2026-10-22/dm-*.txt 9 封 → 1124/1156/1196（11:50 Pi 已重发过，历史事故）+ 1174/1176/1191/1192/1198/1215（backfill-grok）全部 done；created.tsv 的 issue → done。would-do 只有 F2、F3 两条。草稿标记 grep：0 命中。

重置恢复：`restore.sh --dry-run --only 60` exit 0，全 ok（cron 与仓内副本一致、两标记在）。另用临时路径模拟"cron 和标记都没了"：输出会 install /etc/cron.d/pi-fallback、mkdir takeover、touch cindy、boxhealth。fleet-mode 在 grokbot-box/files/workspace/ 有备份。grokbot-box HEAD=origin/main=adfc6f7（13:17）。

## 各 bot 核对
| Bot | 结论 | 已核实 | 不符 / 未核实 |
|---|---|---|---|
| Cindy 管家 | ✅ 通过 | cindy.py+.bak-1224；ensure-cindy.sh 顶部守卫+flock+.bak-1224；boxcheck .bak-1224；cron */5；out/cindy/2026-10-23.md 13:40 新鲜；dry-run 无动作 | 未核实：PID 不变（没真跑）。F1/F4 影响此 bot |
| Mivo QA 日报 | ✅ 通过 | daily.py .bak-1315、conf/daily-calibrations.json、render/diff 复现（25 窗口、hold #434、P1 7/P2 3/P3 14、diff 112 行）；handoff/ledger/dailyretro .bak-1325；retro.md + .prev；plan 4 issue | 未核实：私信 9 行逐行对齐、ledger 78 条（没逐条比） |
| Mivo QA 运维 | ✅ 通过 | opsenv/opsreview .bak-1224；cron .bak 两份（/etc 带点）；opsreview 改 @11 :15；opsfix.py 存在；catch-up 偶数小时属实（13:22 结果文件，#1221/1222/1223/1227 已补测）；--create-issues 属实 | 不符：manual-runs.jsonl 不存在（没 execute，符合说法） |
| Mivo 合并官 | ✅ 通过（判定），开闸不行 | merger.py 3 版 .bak、gate_port/gate_diff/merger_replay/test/gate_fuzz.mjs；outbound .bak-1229-merger、ledger .bak；merge:1222 = skipped；实跑 0 动作 | 不符（小）：备份名写 1236-v3，实际是 merger.py.bak-20261023-1237-v3。未核实：10 万例 fuzz、165/165 回放（没重跑） |
| Mivo 测试项目经理 | ✅ 通过 | pm/pmnudge/p2 .bak-1304-pm；pm 10-22 dry-run 复现；pmnudge 写到 <当天>.md 属实；cron 未改 | 未核实：10-21 G 表逐项。交接仓验收等 3 项本人自报未做 |
| Praise PR | ⚠ 有条件通过 | prretro/prfix .bak-1235；prretro dry-run 0 动作；prfix 跳过 #923 | **不符**：说"新版硬编码跳过 #923"，但旧 actions 里的 fixpr:923 仍会被 outbound 执行（F2）。git user.name=Praise 待核 |
| 备份管家 | ✅ 通过 | grokbot-box-sync cron、cron pid 49249；de24ace/76884b2 在；HEAD=origin；60-pi-line.sh 补标记逻辑在；bundles/ 已无 xindong/praise-pr | bundles/ 仍有 39 个文件（panoptes 等 Praise 自己的仓），未逐个核实是否含公司代码 |
| 电脑运维 | ✅ 通过 | boxhealth .bak.1225、boxcheck .bak.1225；logrotate 加 health.log+maxsize 50M，有 .bak；dry-run 正常；/node_modules 已在 13:17 改名 | 告警只进 actions.json（F3） |
| 知识沉淀 | ⚠ 有条件通过（能跑，没对齐） | out/knowledge/2026-10-23.md、.xstyle.md（blocked: X_BEARER_TOKEN）；cron knowledge @08、xstyle @10；无 takeover/xstyle | 自报未端到端测通；周五 top5 无脚本 |

README 修正（已做，留 .bak）：catch-up 改成北京偶数小时 :07；opsreview 改 11:15；Cindy 行 = cindy.py 已接管；电脑运维行 = boxhealth.py 已接管；「不能迁」表的 Cindy/box-health 行划掉；pmnudge 产出改成追加到 out/pm/<当天>.md。

## 需要 Praise 拍板（各 handoff 合并去重）
**开闸 / 外发**
1. 是否开闸（只能 Praise 批、Jasper 操作）；开之前先修 F1、F2。
2. catch-up 的 `--create-issues` 要不要改成 `--no-issues`（现在绕开闸门）。
3. 私信 Praise 本人：Grok 10-22 用 --allow-owner 发了 6 封，Pi 规则是永不私信 Praise，按哪个来。
4. 催办渠道（PM 推荐 A 写 Praise PR 收件队列 + C 进 Jasper 简报）；日报 09:56 复盘、运维复盘、PM 群复盘、给 Chief 的进展走什么渠道（Grok 群没有 API）。
5. box-health 告警（/node_modules 改名）这条 Slack 私信开闸后要不要发。
6. slack/sent.json 的 10-22 键被 11:50 重复发送覆盖了，要不要恢复成 08:37 的记录。

**合并官**
7. 给 Pi 线一个只有 `repo` 权限的 PraiseZhu 令牌（否则身份门永远 STOP，永不合并）。
8. 自动合并模式 shadow → canary/on；每日上限 2（canary）还是 12（on）。
9. STOP 时要不要补 gh 写熔断标签；BLOCK/HUMAN 阻塞评论开闸前发不发。

**规则口径**
10. P2 新规（只修判「修」的 P2，PM+Praise PR 会签）是否确认 —— 和 10-21「所有 P2 都修」冲突；p2-rulings.md 现在是空表。
11. G1 按首份报告（Pi）还是当前版本（Grok）算。
12. 别人名下的 P0/P1 开了交接 issue 算不算「认领」。
13. 3 天逾期通报是否只含 Praise 自己的 PR；覆盖率-only 的 P2 要不要开交接 issue。
14. #434（api-probe 疑似旧探针）和 #1196 P1 撤回私信，等运维校准后怎么处理。

**归属 / 写入位置**
15. Cindy 保活、box-health 是否永久由 Pi 线接管（Grok routine 删除还是保持暂停）。
16. Pi 线 pm-daily 写 out/pm/ 还是直接写 fleet-standards/pm-daily/；10-22 运维复盘正式版谁写。
17. 知识沉淀输出切到 atelier outlines/style-library 并 push 的时间点。

**运维开关**
18. 是否打开 ops-repair-enabled（自检会 sudo 跑 bootstrap）；box-health 真删清理（PI_BOXHEALTH_CLEAN=1）；logrotate 是否改按小时检查。
19. 重置后由谁/在哪触发 restore.sh（例如保留一个 Grok 侧一次性 restore routine）。
20. mivo-qa 的 nightly/catch-up 锁可能是旧锁（cache/runs 一直清不掉），8→3 个僵尸进程是否处理。
21. grokbot-box 历史里的公司代码 bundle 要不要改写历史清理（需要 force-push，必须 Praise 批）。

**凭证（只放 ~/.pi-fallback-secret/env，不在聊天给）**
22. X_BEARER_TOKEN；X OAuth2 user token（@PraiseBuild，建议先重置已泄露的 Client Secret）；WECHAT_APPID/APPSECRET + 出口 IP 白名单。
23. 盒子重置后若丢失：~/.pi-fallback-secret/env（DEEPSEEK key）、~/.config/cindy-launch/keyring-pass；Cindy 掉登录要 Praise 在 :2 桌面重登。

## 第二阶段进展（13:57 记）
- 电脑运维：F4 已修（run.sh，备份 run.sh.bak-20261023-1356），三种低内存情况已模拟测过。剩余缺口：ensure-cindy.sh 里 Cindy 在时的 3 种重启不受内存拦截，已交给 Cindy 管家补。
- 合并官：parity.md 完成（31 项），merger.py 对齐（备份 v4，回放 165/165、2467 项 0 差异），routine 已加 fleet-mode 闸门，仍暂停。
  - outbound.py 有 12 处缺口要我来补（合并前没有“正在合并”记录、合并后没回读核对、没查 rule-suite、没发“已自动合并”评论、第30行每日计数只算 Pi 自己、熔断和 #1027 评论没有执行代码、33–41 行把 <!-- --> 标记都删了、#1027 心跳缺失）。
  - 待 Praise 决定：1 给不给只有 repo 权限的令牌；2 <!-- mivo-merger --> 标记留还是删；3 每日上限 12 还是 2；4 轮询 5 分钟还是 30 分钟；5 两条线的记录要不要互通（要改 run.mjs，走人工合并 PR）。
- 知识沉淀（13:58）：parity.md 30 行（一致10/已修17/无法对齐3，都卡 X 凭证）；两个 routine 已加闸门仍暂停。我待办：outbound.py 加 git.push.atelier（先查 PRIVATE）；配好 X_BEARER_TOKEN 后手工建 takeover/xstyle。待 Praise：给他的摘要走什么渠道；周五 top5 要不要每周 1 次模型调用（现在规则挑选，W42 只 1/5 重合）。
- 日报（13:58）：parity.md 交付（8 routine 切换表 + 20 行对照）；issues.json / feishu-sent.json 已补齐，新脚本加锁去重已测。我待办：加 followup.py 的 cron（06:17/15:17）；outbound.py 发送加锁。它待办：用量恢复后把 Grok routine 改成调新脚本。注意：误改 panoptes send-feishu-dm.mjs 已恢复，git 干净。待 Praise：他本人还收不收私信；复盘和 3 天逾期通报发哪；要不要模型改写对齐措辞。
- QA 运维（14:14）：parity.md 交付。code_review.status=failed 是一个字段被当两种意思用（mingqa.mjs:154 / nightly.mjs:752），审查本身跑完了；修复分支 7a960e8 或 c8b7bc4 二选一，都没推送。#434 探针确实过期，那条 P2 是误报。合并即测已加闸门，其余还在补，全部暂停。待 Praise：(a) catch-up 的 --create-issues 选 A/B/C（它建议 B+C）；(b) 选哪个修法；(c) 要不要改 7 份报告的字段，#1137 要不要校准为 P2；(d) 要不要建 ops-repair-enabled；(e) catch-up 要不要改成每小时跑。
- Praise PR（14:14）：parity.md 交付，两个 routine 已加闸门，仍暂停。阻断：prfix 写出的动作类型 outbound 不支持，已让它修并用 dry-run 证明。待 Praise：4 件事见它的文件。
