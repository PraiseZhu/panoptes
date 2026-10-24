# Pi line · 2026-10-23 建，北京时间

> **2026-10-24：Pi 线并入 panoptes。** 代码在 `pi/`（部署在 `/workspace/panoptes/pi`），要留的数据在 `/workspace/mivo-qa/pi/`，用完就扔的工作目录在 `/workspace/pi-work/`。下文写到 `/workspace/pi-fallback/bin`、`conf`、`test` 的地方，现在对应 `pi/` 下的同名目录；写到 `/workspace/pi-fallback/out`、`takeover`、`outbound-*` 的地方，现在对应 `/workspace/mivo-qa/pi/` 下的同名文件。改动只走 PR。

10-23 16:10 起正式接管（fleet-mode=pi + outbound-enabled，真实外发；Grok 对应 routine 已暂停）。10-23 上午的影子期只写文件，下文「影子产出」「对比」列是那时的记录。代码：`/workspace/pi-fallback/bin/`，产出：`/workspace/pi-fallback/out/`，cron：`/etc/cron.d/pi-fallback`（副本 `/workspace/pi-fallback/pi-fallback.cron`）。不改 Grok 线任何东西。

## 开关与外发闸
- `/workspace/fleet-mode`：`grok`（现在）或 `pi`。每个作业启动时读取并记到日志；两种模式下**都只写文件**。
- 外发（Slack/飞书/gh 写/SendToAgent）只写进 `out/<job>/<date>.actions.json`，每条带幂等 key（如 `mivo-daily-report:<日期>`、`dm:<日期>:<PR>`、`handoff:<PR>:<fingerprint>`、`merge:<PR>:<headSHA>`、`fixpr:<PR>:<fingerprint>`、`ready:<PR>`），并注明去重依据文件（sent.json、feishu-sent.json、issues.json）。
- 只有 `fleet-mode=pi` **且** `/workspace/pi-fallback/outbound-enabled` 存在时闸门才打开，`bin/outbound.py` 真实外发；`outbound-disabled-types` 里的类型永不外发（10-23 起锁的是合并官的合并 / 评论类，Praise 定）。status=done 的 key 永不重做。
- 公共：`run.sh` 内存 MemAvailable < 3G 跳过、每作业 flock、按北京小时门控（盒子是洛杉矶时间）、日志 `out/logs/<job>-<日期>.log`；密钥从 `~/.pi-fallback-secret/env`（600，不在任何 git 仓/备份同步路径）读入，日志只写 set/unset。
- 模型只经 `~/tools/pi-call/pi-auto <tier>`；任务记录 `out/tasks/`，owner=`pi-line-cron`。

## 覆盖表
| Bot | 职责 | Pi line 脚本 | 时间（北京） | tier | 影子产出 | 对比方法 | 状态 |
|---|---|---|---|---|---|---|---|
| Mivo QA 日报 | 日报草稿 + 私信/交接 issue 动作 | daily.py | 08:35 | 无模型 | out/daily/<数据日>.md/.json/.actions.json | compare：PR 集合、各级计数、逐 PR 级别 vs report/sent/<日>/*.txt | ✅ 已跑 |
| | 交接仓回执扫描 / 15:20 逾期提醒 | handoff.py | 00:05、06:20、15:20、18:35 | 无 | out/handoff/<日>.md、.actions.json | vs 交接仓 daily/…/receipts.md | ✅ |
| | 23:30 重建台账 | ledger.py（build-ledger.mjs --dry-run） | 23:40 | 无 | out/ledger/<日>.json/.md | summary 对比 ledger/daily | ✅ |
| | 09:56 日报复盘 | dailyretro.py | 09:56 | 有（work 档） | out/dailyretro/<日>.md（只写文件，不发群、不私信；发不发、发到哪等 Praise 定） | takeover/dailyretro | ✅ 只写文件 |
| Mivo 测试项目经理 | 10:30 G1–G5 核对 | pm.py | 10:35 | 无 | out/pm/<数据日>.md | vs fleet-standards/pm-daily/<日>.md 目标表 | ✅ |
| | 白天催办（只看 QA: #N 修复 PR） | pmnudge.py | 10/12/14/16/18/20 :05 | 无 | 追加到 out/pm/<当天>.md「## 白天催办 HH:MM」节；动作 out/pm/<当天>.nudges.actions.json；20 点 out/pm/<当天>.chief.md | pm-daily「白天催办」节 | ✅ |
| | P2 裁决提议 | p2.py | 12:20、18:20（每次≤6 调用） | judge | out/p2/<日>.md、state.json | vs p2-rulings.md 同题裁决 | ✅（不写 p2-rulings） |
| Mivo QA 运维 | 合并即测 / catch-up | **mergewatch.py（每分钟 git ls-remote，main 一动就以 event 模式调起 panoptes catch-up）**；panoptes-catch-up cron（每 2 小时）兜底 | 每分钟 | 无 | reports/merged（trigger=event） | watchdog：合并 >90 分钟无报告即告警 | ✅ 10-23 21:13 起；替代 Grok on-merge（原 catch-up 兜底实测 52–173 分钟） |
| | 每小时环境自检（只查） | opsenv.py | 每小时 :25 | 无 | out/ops/env-<日>.md | vs Grok env-guard（无盘上记录→人工） | ✅ |
| | 10:57 复盘 + P1 校准 | opsreview.py（review-ops collector --out + review tier） | 11:15（排在 10:07 catch-up 之后） | review | out/ops/review-<数据日>.md | vs review-ops/daily/<日>.md 校准条目 | ✅ |
| Praise PR | 09:41 复盘（含“将转 ready”） | prretro.py | 09:45 | 无 | out/praise-pr/<日>.retro.md、.actions.json | 人工 | ✅ |
| | 修自己 PR 上测出的问题（规则见文末 10-23 晚 / 10-24） | prfix.py（prwatch 按内存并发派活，一单一个进程；Pi agent + deepseek-v4.1-flash，thinking max） | prwatch 每分钟；14:20 兜底 | deep | out/praise-pr/tickets.json、<日>-<tag>.pr.patch/.md | — | ✅ |
| | 22:23 清理 | prretro.py 末节（只列） | 09:45 | 无 | 同上 | — | ✅ 只列不删 |
| Mivo 合并官 | 判定哪些 PR 可合 + 巡检 P0 | merger.py（零模型，8 项必需检查等） | 每小时 :13/:43（不做 5 分钟轮询：Praise 10-22 叫停轮询） | 无 | out/merger/<日>.md、.actions.json | vs audit.log | ✅ |
| 备份管家 | grokbot-box 每小时推送 | 已是 cron；boxcheck.py 核日志新鲜度、本地=远端 | 每小时 :45 | 无 | out/box/<日>.md | — | ✅ 不需 Grok |
| 电脑运维 | 保活/内存日志/轮转 + box-health | 已是 cron（box-ops）；boxhealth.py（takeover/boxhealth）接管 box-health；boxcheck.py 只查 | boxhealth 每小时 :17；boxcheck :45 | 无 | out/box/<日>.health.md、<日>.md、<日>.actions.json | — | ✅ boxhealth.py 已接管（清理默认只列） |
| Cindy 管家 | 每 5 分钟 ensure-cindy.sh | cindy.py（takeover/cindy）每次调 ensure-cindy.sh；boxcheck.py 只查 | 每 5 分钟 | 无 | out/cindy/<日>.md | — | ✅ 已接管 by cindy.py（ensure 有接管守卫，Grok routine 已暂停） |
| 知识沉淀 | 周五 08:04 10 个选题 | knowledge.py | 每天 08:05（仅周五出题） | work | out/knowledge/<日>.md | 人工 | ✅ |
| | 工作日 10:04 X 风格收集 | xstyle.py | 10:04 | 有 | — | takeover/xstyle（没建：缺 X_BEARER_TOKEN） | ⛔ 缺密钥 |
| Jasper | 9:00 简报（舰队部分）+ 舰队核查 | jasper.py | 09:05 | work | out/jasper/<日>.md、.facts.json | 人工 | ✅（Slack/邮件/日历部分 ⛔） |
| — | 对比 | compare.py | 12:50 | 无 | out/compare/<数据日>.md | — | ✅ |

## 不能迁（需要 Grok 连接器或人）
| 职责 | 原因 | 解锁需要 |
|---|---|---|
| 日报发 Slack、作者飞书私信、3 天逾期群提醒 | 外发；本阶段禁止 | Slack bot token（Mivo app，C0CAH3W61N2）已在 report/slack 脚本里用；飞书在 /home/box/.config/mivo-qa/secrets.env。技术上可接，等 Praise 批准开闸 |
| 交接 issue 开/关、PR ready、开修复 PR、合并、PR 留言 | gh 写；本阶段禁止 | 盒上 gh（PraiseZhu）已可写，只差批准 |
| 日报 09:56 复盘、PM/运维发「MivoCanvas QA」Grok 群、给 Chief of Staff 进展、SendToAgent 催办 | Grok Bot 内部群/agent 消息，没有外部 API | 无（Grok 宕机时改发 Slack/飞书需 Praise 定渠道） |
| Jasper 扫 Slack/邮件/日历/会议纪要 | Grok 连接器 OAuth | Slack user token、Gmail/Calendar OAuth refresh token（Praise 授权给脚本） |
| 知识沉淀 X 热门长文风格收集、公众号/X 草稿 | X 连接器 | X API bearer/OAuth（及公众号 AppSecret） |
| 和 Praise 对话、拍板事项 | 人 | — |
| ~~Cindy 保活、box-health~~ | 10-23 已由 cindy.py / boxhealth.py 接管（见覆盖表） | box-health 真删清理需 Praise 批（PI_BOXHEALTH_CLEAN=1） |

## 内存与时间编排
panoptes catch-up 每 2 小时 :07 + 每次合并（mergewatch）起跑；修 PR 不再让路，和合并测试同时跑，内存是唯一门槛（10-23 Praise），并发规则见文末 10-24。所有重作业 MemAvailable<3G 等待或跳过。

## 10-23 增补（100% 版）
- 新作业：outbound.py（外发执行器，*/10，闸门 = fleet-mode=pi 且 outbound-enabled；台账 out/outbound/ledger.json）、cindy.py（*/5，takeover/cindy）、boxhealth.py（:17，takeover/boxhealth，清理默认只列）、dailyretro.py（09:56，takeover/dailyretro）、roster.py（1/15 08:23，takeover/roster）、xstyle.py（工作日 10:04，takeover/xstyle，需 X bearer）、credcheck.py（07:01）。
- 切换：`bin/switch.sh pi|grok [--with-outbound] [--dry-run]`。测试覆盖：`PI_FLEET_MODE` / `PI_TAKEOVER_DIR` 环境变量（只用于测试）。
- 重置恢复：grokbot-box `restore.d/60-pi-line.sh` + `/workspace/pi-fallback/restore/`。E2E：e2e-2026-10-23.md。


## 10-23 晚 全链路修复（Praise 批准「全部修，今晚上线」）
排查报告：Cindy 对话目录 documents/2026-10-23-Pi线全链路排查.md。改动全部进本地 git：
`git --git-dir=/home/box/.pi-fallback.git log --oneline`（工作树 /workspace/pi-fallback，out/ 不入库；提交前自动编译检查）。
回滚某一步：`git --git-dir=/home/box/.pi-fallback.git revert <sha>` 或 `checkout <sha> -- bin/<file>`。不要再新建 .bak。

| 部件 | 做什么 | 频率 | 产出 / 状态 |
|---|---|---|---|
| bin/mergewatch.py | main 有新合并 → 1 分钟内调起 panoptes catch-up（--trigger event --create-issues）；catch-up 在跑就记"待测"，结束后立刻补 | cron 每分钟（不走 run.sh） | out/mergewatch/state.json（心跳）、out/logs/mergewatch-<日>.log（只记事件） |
| bin/prwatch.py | 修 PR bot 接单：新 P0/P1 → 私信 Praise；修复草稿（QA: #N）除 review-pr 全绿 → 立即 gh.pr.ready；CI 红 → 私信；有 takeover/prfix-event 时按单调起 `run.sh prfix --next`，写出动作后立即外发 | cron 每分钟 | out/prwatch/state.json、out/logs/prwatch-<日>.log |
| bin/watchdog.py | 自动化本身停摆时告诉 Praise（作业过期/失败/漏跑、编译失败、合并无报告、备份、mergewatch/prwatch 心跳、panoptes 未部署）；只在状态变化时私信，恢复报一次，24h 提醒；outbound 坏了就绕过它直发 | cron */5 | out/watchdog/state.json |
| outbound.py | rc=1 只代表真失败；外发前先落盘 sending、每条立即落盘（防虚拟机快照分叉重复发；Slack/飞书中断的不盲目重发，标 uncertain 并提醒）；所有作业失败退避，5 次放弃并提醒；原 Grok-only 消息改为私信 Praise（紧急单发，其余每轮一条摘要，>6h 不补发）；#923 等闸名单 conf/gates.json；转 ready 执行前复核 head 与检查 | */10 | out/outbound/ledger.json、relay-<日>-<HHMM>.md |
| boxhealth.py | 只告警真问题：平台僵尸（Xvfb 等）不算；内存看连续 ≥5 分钟 <3G 或 swap>4G；按状态变化发、恢复发一次 | :17 | out/box/alert-state.json |
| opsenv.py | panoptes 自动部署：main、干净、落后、目标提交 CI 全成功、两把锁都空闲才 ff-only；状态变化私信 | :25 | out/ops/env-state.json |
| run.sh | 先编译检查（失败 rc=3，watchdog 告警）；零模型作业不受 3G 门；重作业最多等 10 分钟内存 | — | — |
| grokbot-box/sync.sh | push 被拒（快照分叉）时以 origin 为底重提交再推（不 force） | :17 | /tmp/grokbot-box-sync.log |
| ~/.config/cindy-launch/nudge-loop.sh | 不依赖 cron 的 cron 看门狗：每 60 秒 cron 不在就 sudo -n 拉起 | 常驻 | /workspace/box-ops/cron-restarts.log |
| takeover/dailyretro、roster | 日报复盘、花名册恢复（复盘经 outbound 私信 Praise）；switch.sh / restore 60 段同步管理这两个标记 | — | — |

时区：平台会随时改 /etc/localtime（10-23 见过 Asia/Shanghai、Asia/Taipei、America/Los_Angeles）。Pi 线所有作业按北京小时门控，不受影响；panoptes 自己的 `7 */2` 会落在北京奇数或偶数小时，重作业靠 nightly.lock 协调，不靠钟点错开。
| ledger.py / build-ledger.mjs | pi 模式**真实**重建 ledger/daily/<日>.json（写前备份、写后核对，坏了隔离为 .bad-*）；认 `dm:` 私信 key；直接收 PraiseZhu 本人的 P0–P2；复测「fail 优先」 | 23:40 | ledger/daily/ |
| opsfix.py retest | 范围 = 上次成功复测后合并的 `QA: #N` 修复 PR（out/ops/retest-state.json 水位线）；锁/内存不够 rc 75 下个时段补（09/12/16/20 :30）；回写后生成交接 issue close/reopen 动作 | 09/12/16/20 :30 | 台账、out/opsfix/ |
| followup.py | 跟进作者回应（感谢/提醒/7 次升级），真实推进 followup/state.json；私信经 outbound（拒发无正文无 PR 号的私信） | 06/15 :27 | followup/state.json |
| prfix.py（重写） | 一个进程一单（`--next`；10-24 起可并发，见文末）；指纹级认领（含作者自己的修复 PR、交接回执）；真实 vitest/npm 返回码；失败 finally 清理 worktree+分支；NOREPRO 写 notes + 私信；退避 15m/1h/4h→转人工；推送前跑仓库 .githooks/pre-push；PR 正文按模板；docs/pr 真实号（outbound 改名）；有合并测试排队就让出 nightly.lock；闸名单 conf/gates.json | prwatch 触发 + 14:20 兜底 | out/praise-pr/tickets.json、last-result.json |

## 10-23 晚 22:30–23:18 的规则（原写于 23:18，随主机回滚丢失，10-24 补回）
- **只修自己的 PR**：prfix 只接 PraiseZhu 的 PR（`PRFIX_OWNERS`）。别人的问题照旧由日报开到他们的交接仓，由他们自己的 bot 修；prwatch 也只为你自己的 PR 私信新问题。
- **自己 PR 上测出的问题都算单**：P0/P1 一律修，含待核实（第一步写复现测试就是核实；复现不了写 notes 并私信）；审查确认成立的 P2 修，除非 p2-rulings.md 判了不修 / 产品代码不修 / 误报 / P3 / 已修；待核实的 P2 不派活，台账保留并标「待核实」；只差覆盖率的只当提示。台账（build-ledger）和 prfix 用同一套判断（finding_policy.own_pr_ticket）。
- **和合并测试同时跑**：内存够就开工，不等 panoptes。
- **修复草稿 CI 假红**：只有 race-prone 的整 PR 门（pr-format-gate、conflict sentinel）红时，每个 head 重跑一次；还红就 update-branch（合入最新 main，不改代码，每个 PR 最多 2 次）；还红才私信。
- **修测试系统本身（infrafix，23:10 上线）**：盯三类信号（报告 system_health 的系统故障、`PraiseZhu/pr-qa-agent-deploy` 的系统故障 issue、补测反复失败后放弃的 PR），先写测试复现，再修 panoptes、跑全量测试、开 PR；满足条件才自动合并（先红后绿的复现测试、本地全量通过、CI 全绿、不碰锁 / 定时任务 / 密钥类文件），合并后 opsenv 部署、部署后复查。闸名单 `conf/gates.json` 的 `infra_hold` / `infra_automerge`。

## 10-24 主机回滚与恢复
- 10-24 00:05 晚上那台主机消失，现在这台从 10-23 10:57 快照恢复；01:11 按 23:17 的备份（grokbot-box `adff3a11`、pi-fallback git `34c443b`）恢复了脚本与状态，02:07 切回 pi + 外发。恢复材料：`/workspace/_rollback-recovery-20261024/`。23:17 之后只在本机做的改动丢了（其中 #1179 第 1 条 P2 的修复在推送前丢失，已自动重修）。
- **密钥不在备份里**：`~/.config/mivo-qa/secrets.env`（600）缺 `MIVO_SLACK_BOT_TOKEN`、`MIVO_FEISHU_APP_SECRET` 时 Slack / 飞书全部发不出去。10-24 02:28 从 Cindy 会话环境补回。以后再回滚，先查这一项。
- **回滚保护**：grokbot-box `sync.sh` 推送被拒时，若备份里有本机没有、且比共同祖先新 2 小时以上的提交，判为「疑似回滚」：不覆盖备份，写 `/workspace/box-ops/ROLLBACK-SUSPECTED`，日志打 `PUSH FAILED — 疑似盒子被回滚`（watchdog 私信你）。虚拟机分叉（相差约 1 小时）仍按原来的自愈处理。
- **疑似回滚时怎么恢复**（10-24 核查后更正：不是 `restore.sh`）。`restore.sh` 是给「空机器重置」用的：`00-repo` 只做 `pull --ff-only`，本机有回滚态的新提交时会失败，而且失败被吞掉；`30-files` 用 `rsync --ignore-existing`，不会覆盖本机已有的旧文件。所以回滚场景下它几乎什么都不恢复。10-24 实际做法见 `/workspace/_rollback-recovery-20261024/`（`plan.txt`、`applied.json`、`git-restore.log`）：
  1. **不要强推**，也不要让 sync.sh 自愈。备份里较新的版本在 grokbot-box 的 `origin/main`。
  2. 先切 grok 停外发：`bin/switch.sh grok`。
  3. 对比 `origin/main:files/…` 和本机文件，逐项把较新的拷回。日志、缓存类按本机为准。
  4. git 仓用 `bundles/` 恢复到备份里的提交，比如 `~/.pi-fallback.git`。
  5. 补密钥：`secrets.env` 里的三个 `MIVO_*` 和 `DEEPSEEK_API_KEY`。`restore.d/60-pi-line.sh` 会列出缺哪个。
  6. grokbot-box 本地 main 执行 `reset --hard origin/main`，旧的本地提交留在分支里；然后删掉 `ROLLBACK-SUSPECTED`。
  7. 跑 `bin/selftest.sh` 和 `bin/run.sh credcheck`，最后 `bin/switch.sh pi --with-outbound`。
- 恢复后 prwatch 把 16 条旧问题误当新问题（报告文件时间被改），因 Slack 密钥缺失没发出，已在 outbound 台账标 skipped；根因已修（见下）。

## 10-24 修 PR bot 升级（Praise：「bot 只是个调度器，让 pi agent 加载 deepseekv4.1flash max 来修，并发数量看内存，不设上限」）
- **模型**：prfix 每一步（复现、修复、交接文件）走 `pi-auto deep` = `tap/deepseek/deepseek-v4.1-flash`，thinking max（`PRFIX_TIER` 可覆盖）。
- **并发**：没有固定上限，内存决定。prwatch 每分钟：没有正在挑单的进程、且 余量 ≥ 1.5 GB + 一单的峰值 时，再起一个 `run.sh prfix --next`（每分钟最多起一个）。余量 = MemAvailable − 正在修的每一单还会涨的内存 − 合并测试还会涨的内存（正在跑的算剩余增长；已有修复在跑时，还没开始的合并测试也按整份峰值预留，因为它随时可能开跑）。峰值是学出来的：每分钟记录每单、每次 panoptes 的进程树 RSS，攒够 3 次后取最近 20 次最大值 × 1.3；之前默认一单 2.5 GB、panoptes 6 GB。这台 16 GB 的机器通常是「1 单修复 + 1 个合并测试」，内存大了自然多开。每一单开始前、每个重步骤前仍要 MemAvailable ≥ 3 GB（不够就等）。
- **并发安全**：没有全局锁；认领在 tickets.json 的锁里原子完成（重读、重判、拿单子锁、标 running）；共享 clone 的 fetch / worktree 增删 / 删分支走 `out/praise-pr/git.lock`（opsfix 复测也走这把锁）；日志每行带单号；PR 列表 60 秒共享缓存（增量刷新，每小时全量，省 GraphQL 配额）。
- **prwatch 新问题私信**：按报告内容（sha1）判断是否变化，文件被恢复 / 复制不会再误报；第一次运行或状态格式变化只建基线不发。
- 排查命令：`python3 bin/prfix.py --list`（只读）；日志 `out/logs/prfix-<日>.log`、`out/logs/prwatch-<日>.log`。

## 10-24 其它
- **infrafix**：报告里有 `code_review.ran` 时（panoptes #20 起），`status=failed` 是审查结论不是故障；`infra_automerge=true`（Praise 授权全部收尾），条件不变；`inf1-4461a208` 的 hold 保留到 #20 部署后 7 天窗口里的旧报告过期（约 10-31）再删。
- **3 天逾期群通报**（`track-followups --group-overdue`）：通道已接好（`bin/followup_post_sender.mjs` 排队 → `feishu.group` post 带 @ / `slack.post` → outbound），开关是 `takeover/group-overdue`（15:27 remind 那一轮）。旧版工具会把你自己 4 个修复已合并的 PR 当逾期点名；panoptes #19（10-24 02:49 合并，03:08 手工部署）修好后在部署版上预演：0 条通报、0 错误（你自己的 PR 不进群通报），10-24 03:1x 打开。
- 10-23 23:40 漏跑的台账已于 10-24 02:24 补跑（104 行）。

## 10-24 复查（Praise 要求派子代理核查「是否完整修复、bot 是否都正常、还有没有冲突阻塞遗漏」）
6 个只读子代理分别查了修 PR 链、测试系统、外发、定时任务 / 主机 / 备份、跨会话冲突、Mac mini 盯梢。查出的问题，能直接修的已经修掉：
- **看门狗新增的检查**：
  - 外发密钥缺失、登录失败、同一条失败两次以上：失败的动作会进退避，cron 每轮照样 rc=0，02:07–02:28 外发全部失败时没人知道。
  - 修 PR bot：单子卡住超过 90 分钟；开 PR 动作 2 小时还没执行；需要你拍板的单子 12–36 小时没结论，只提醒一次；prwatch 出错；排队等内存超过 3 小时。
  - Mac mini 盯梢：已转正式的 PR 测试没过，而且 4 小时 GitHub 上没动静。10-23 21:54–03:20 它停摆，没人发现。
  - 回滚时备份推送被拒，看门狗报「机器疑似被回滚」，不再提示「再推一次」。
  - 备份合规检查（grokbot-box `check.sh`，每小时 40 分，回滚时丢了，已补回 cron）的 FAIL。
  - 看门狗绕过 outbound 直发时，以前拿不到 Slack 密钥，现在 `lib.secret_env_dict` 统一读两个密钥文件。
- **修 PR bot**：
  - 预测 PR 号时跳过别的单子已经预留的号。
  - 「QA 回访 #904/#910…」这种多号写法也能认出来。
  - 10-23 上午写的「P2 暂停 / 等 Praise 确认规则」那类 note 不再算已认领。因此 #1148、#1177、#1198 三条已确认的 P2 放出来修了。
  - npm ci、vitest、pre-push 以 nice 10 运行，CPU 让给合并测试。
- **测试系统**：
  - infrafix 能认出检查项级故障：同一检查在基线版本上也失败，7 天 ≥3 次就成单，而且从不自动合并。api-probe 那个已由 #18 修好的签名 `6a6e6cde` 先 hold 到约 10-31。
  - infrafix 也用 deep 档。
  - 新增 `opsdeploy`，每 10 分钟做一次和 opsenv 一样有保护的部署，以前每小时只试一次，碰上测试在跑就要再等一小时。
- **主机**：
  - `ensure-cindy.sh` 起 nudge-loop 时关掉锁的文件描述符（`8>&-`），否则锁会被一直占着，之后每次保活都静默跳过。`cindy.py` 连续 6 次因锁 SKIP 就算 FAIL。
  - 新版 nudge-loop（带 cron 守护）已经在跑。
  - logrotate 晚上版补回；grokbot-box-sync 的 cron 加了 PATH，npm 全局清单不再是空的。
  - `~/.pi-fallback-secret` 权限改成 700。
  - pre-commit 钩子（selftest）补回；`restore.d/60-pi-line.sh` 以后会自动装上它，并检查三个外发密钥。
- **03:08 的意外外发**：一个会话用 import 做冒烟检查，等于真跑了一次跟进，给 3 位作者发了 4 条飞书私信。记录在 `out/followup/2026-10-24-0308-out-of-band-run.md`。panoptes #21 已经给 track-followups 加了入口保护；#926 的提醒计数已按 Praise 的要求改回 6 次。

## 10-24 晚 收尾（Praise 问「你全部完成了么」时又查了一遍）
- **密钥泄露（已处理，要轮换）**：
  - 经过：10-24 05:19，另一个会话从自己的 Claude 环境里手动跑了 `prfix --qa 1188`。那个环境里注入了会话密钥，还有一个变量 `__CURSOR_SANDBOX_ENV_RESTORE`，它的值就是一串 `export 各种 TOKEN=…`。模型执行 `env | grep -i "pi_|repro|qa"` 时，"API_KEY" 里含有 "pi_"，这个变量被 grep 了出来。结果 4 个真实密钥进了发给模型的工具输出：Slack bot token、飞书 app secret、`MIVO_AUTOMERGE_TOKEN`（GitHub token）、DeepSeek key。
  - 同类问题：做测试时，一条比对 token 的测试失败，把完整的 Slack token 打印进了两个 Claude 会话的记录。
  - 已修：
    - `lib.pi` 只给模型传白名单环境（`lib.model_env`）；
    - prwatch 不再改写自己的环境；
    - infrafix、opsfix 的测试也从白名单起步；
    - selftest 用 `env -i` 跑，测试拿不到真 token。
  - 本机两份 pi 会话日志里的真值已替换成 `[REDACTED:名字]`。备份（grokbot-box 自 10-22 以来 49 个提交）里没有这些真值；07:52 起 `~/.pi/agent/sessions/` 不再进备份。
  - **要 Praise 做**：轮换上面 4 个密钥。两个 Claude 会话记录里的 Slack token 也要靠轮换失效。
  - **以后**：别从会话里直接跑 prfix / infrafix 的修复模式；要跑，先 `env -i` 或等 prfix 自己清环境（已交给负责修 PR bot 的会话）。
- **看门狗又加了两项**：测试系统某次运行超过 5 小时（2 小时兜底补测没有单 PR 超时，卡住会挡住所有合并测试）；Cindy 挂了且内存不够、暂时不拉起时私信一次。
- **盯梢那边 PR 卡住怎么办**：看门狗报「Mac mini 盯梢那边有 PR 卡了很久」时，先在 Mac mini 上看盯梢还在不在跑。上次停摆是 Keel 插件换了命名空间，台账目录从 `ghost-fs/keel` 变成 `ghost-fs/_ns__xd__keel`；解法见 vigil 仓 `docs/operations.md`：改 profile 的 `keelLedgerRoot`、`keelNamespace` 后重新部署。盯梢正常但 PR 一直没人处理的，在 Cindy 里让 Mac mini 的会话直接处理。
